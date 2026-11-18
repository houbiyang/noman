"""合成门禁测试，不构成任何真实地图9case验收或发布证据。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from uuid import uuid4
import json

from backend import battle_scenario as scenario, battle_setup
from backend.battle_map_geometry import CatalogInteractions, OfflineMapGeometry, LocalManualMapGeometry, seal_interactions
from backend.battle_preflight import scenario_check, summarize, check, BattlePreflight
from backend.runtime import ServiceError, sha256
from backend.service import SandboxService
from backend.tests.test_battle_map_geometry import sources, encoded
from backend.tests.test_battle_map_components import unit
from backend.tests.test_battle_setup import setup_request, SetupGeometry
from backend.battle_map_surfaces import rectangle


def candidate():
    c, d, _ = sources()
    return scenario.local_manual_definition(c.payload, d, version='synthetic-local-qualification-v1',
                                            limitations=['合成门禁测试，不授予真实资格'])


def report(value):
    return dict(schema_version='qinglan-local-manual-acceptance-v1', environment='isolated_real_map',
        release_scope='local_manual_test', competitive_ready=False, passed=True,
        geometry_binding=value['geometry_binding'], field_energy_service_policy=value['field_energy_service_policy'],
        cases=[dict(case_id=key, passed=True, source='real_map', evidence_sha256='a'*64)
               for key in sorted(scenario.LOCAL_MANUAL_CASES | {scenario.HOVER_ENERGY_CASE})])


class RayDomainTests(TestCase):
    def terrain(self, domains):
        c, d, s = sources()
        d['verified_interaction_domains'] = domains
        return CatalogInteractions(c, seal_interactions(encoded(d)), s)

    def test_joined_closed_domains_cover_whole_ray_in_both_directions(self):
        t = self.terrain([rectangle(1000, 1000, 10), rectangle(1020, 1000, 10)])
        self.assertTrue(t.ray_domain_clear(unit(x=995), unit(x=1025)))
        self.assertTrue(t.ray_domain_clear(unit(x=1025), unit(x=995)))

    def test_endpoint_membership_cannot_bridge_unverified_gap(self):
        t = self.terrain([rectangle(1000, 1000, 10), rectangle(1030, 1000, 10)])
        self.assertFalse(t.ray_domain_clear(unit(x=995), unit(x=1035)))

    def test_same_point_must_be_inside_verified_domain(self):
        t = self.terrain([rectangle(1000, 1000, 10)])
        self.assertTrue(t.ray_domain_clear(unit(x=1000), unit(x=1000)))
        self.assertFalse(t.ray_domain_clear(unit(x=1100), unit(x=1100)))
        self.assertFalse(t.ray_domain_clear(unit(x=1000), unit(x=1100)))

    def test_empty_domain_cannot_mean_unlimited(self):
        with self.assertRaises(ServiceError): self.terrain([])
        c, d, s = sources()
        self.assertTrue(CatalogInteractions(c, d, s).ray_domain_clear(unit(x=1), unit(x=11999)))


class LocalScopeTests(TestCase):
    def test_candidate_and_local_are_never_formal_accepted(self):
        v = candidate()
        self.assertFalse(scenario.accepted(v)); self.assertFalse(scenario.manual_accepted(v))
        final = scenario.freeze_local_manual(v, report(v))
        self.assertTrue(scenario.local_manual_accepted(final)); self.assertTrue(scenario.manual_accepted(final))
        self.assertFalse(scenario.accepted(final))
        forged = deepcopy(final); forged['acceptance'].update(status='accepted', blocks=[])
        self.assertFalse(scenario.accepted(forged)); self.assertFalse(scenario.manual_accepted(forged))
        self.assertEqual(final['acceptance']['terrain_masks_validated'], v['acceptance']['terrain_masks_validated'])

    def test_version_and_explicit_limitations_are_required(self):
        c, d, _ = sources()
        for version, limits in ((scenario.VERSION, ['x']), ('new', []), ('new', ['']), ('', ['x'])):
            with self.subTest(version=version, limits=limits), self.assertRaises(ServiceError):
                scenario.local_manual_definition(c.payload, d, version=version, limitations=limits)

    def test_local_report_requires_exact_9_real_cases_and_policy(self):
        v = candidate(); r = report(v)
        self.assertEqual(len(r['cases']), 9)
        for kind in ('missing', 'duplicate', 'synthetic', 'stale', 'formal', 'policy'):
            bad = deepcopy(r)
            if kind == 'missing': bad['cases'].pop()
            if kind == 'duplicate': bad['cases'][-1] = bad['cases'][0]
            if kind == 'synthetic': bad['cases'][0]['source'] = 'fixture'
            if kind == 'stale': bad['geometry_binding'] = {}
            if kind == 'formal': bad['schema_version'] = 'qinglan-real-map-acceptance-v1'
            if kind == 'policy': bad.pop('field_energy_service_policy')
            with self.subTest(kind=kind):
                self.assertFalse(scenario.local_manual_evidence_valid(bad, v))
                with self.assertRaises(ServiceError): scenario.freeze_local_manual(v, bad)

    def test_raw_evidence_needs_actual_assertions_and_observations(self):
        v = candidate()
        record = dict(case_id='source_integrity', passed=True, source='real_map',
            geometry_binding=v['geometry_binding'], map_source_sha256=v['map']['source_sha256'],
            database_map_content_hash=v['map']['content_hash'])
        match = lambda r: scenario.local_manual_record_matches(r, 'source_integrity', geometry_binding=v['geometry_binding'], scenario=v)
        self.assertFalse(match(record))
        record.update(assertions={'map_same': True}, observations={'map_hash': v['map']['content_hash']})
        self.assertTrue(match(record))
        record['database_map_content_hash'] = 'wrong'; self.assertFalse(match(record))
        record['database_map_content_hash'] = v['map']['content_hash']
        record['assertions'] = [False]; self.assertFalse(match(record))

    def test_report_digest_and_scope_manifest_cannot_change(self):
        v = candidate(); final = scenario.freeze_local_manual(v, report(v))
        bad = deepcopy(final); bad['local_manual_acceptance']['passed'] = False
        self.assertFalse(scenario.local_manual_accepted(bad))
        manifest = {k: deepcopy(final[k]) for k in ('release_scope', 'competitive_ready', 'limitations')}
        self.assertTrue(scenario.release_scope_matches(manifest, final))
        manifest['competitive_ready'] = True; self.assertFalse(scenario.release_scope_matches(manifest, final))
        self.assertTrue(scenario.release_scope_matches({}, scenario.definition()))

    def test_factory_and_all_role_views_freeze_scope_without_upgrading_old(self):
        values = list(battle_setup.sources()); values[1] = candidate()
        body = setup_request(values); geometry = SetupGeometry(); geometry.scenario_sha256 = body['scenario_sha256']
        state = battle_setup.build(battle_setup.CreateManualBattle.model_validate(body), *values, geometry)
        provider = SandboxService(None); provider._context = lambda *_: {}
        game = dict(id=uuid4(),game_no='fixture',title='合成测试',is_demo=True,
            scenario_version_id=uuid4(),rule_version_id=uuid4(),head_snapshot_id=uuid4())
        for role in ('host', 'blue', 'red'):
            manual = provider._project(game, dict(role=role), state)['manual_battle']
            self.assertEqual(manual['release_scope'], 'local_manual_test')
            self.assertIs(manual['competitive_ready'], False)
            self.assertEqual(manual['limitations'], values[1]['limitations'])
            manual['limitations'].append('must not mutate')
        self.assertEqual(state['manifest']['limitations'], values[1]['limitations'])

    def test_local_scenario_preflight_matches_frozen_scope_and_never_agent(self):
        values = list(battle_setup.sources()); v = candidate(); values[1] = scenario.freeze_local_manual(v, report(v))
        body = setup_request(values); geometry = SetupGeometry(); geometry.scenario_sha256 = body['scenario_sha256']
        state = battle_setup.build(battle_setup.CreateManualBattle.model_validate(body), *values, geometry)
        game = dict(state_payload=state, scenario_version_id=body['scenario_version_id'],rule_version_id=body['rule_version_id'])
        row = dict(definition=values[1], payload_sha256=sha256(values[1]), availability='active',
            rule_definition=values[0], rule_payload_sha256=sha256(values[0]),rule_availability='active')
        self.assertEqual(scenario_check(game,row,scenario=values[1])['status'], 'passed')
        checks = [check('core','core','x',True,'ok'), check('local_manual_scope','ai','x',False,'manual only')]
        self.assertTrue(summarize(checks,'manual')['ready']); self.assertFalse(summarize(checks,'agent')['ready'])
        state['manifest']['limitations'] = ['forged']
        self.assertEqual(scenario_check(game,row,scenario=values[1])['status'], 'failed')


class IsolationGateTests(TestCase):
    def test_production_install_rejects_offline_even_if_subclass_or_mock(self):
        service = SandboxService(None)
        with self.assertRaises(ServiceError): service.install_manual_geometry(object.__new__(OfflineMapGeometry))
        with self.assertRaises(ServiceError): service.install_manual_geometry(Mock(spec=LocalManualMapGeometry))
        self.assertIsNone(service.battle_executor)

    def test_actual_db_and_config_both_must_match_random_pattern(self):
        c = Mock(); valid = 'qiongtu_manual_test_' + 'a'*32
        for actual, expected in (('battle', None), (valid, 'battle'), ('qiongtu_manual_test_old', None)):
            c.execute.return_value.fetchone.return_value = dict(database_name=actual)
            with self.subTest(actual=actual,expected=expected), self.assertRaises(ServiceError):
                scenario.require_acceptance_database(c, expected)
        c.execute.return_value.fetchone.return_value = dict(database_name=valid)
        self.assertEqual(scenario.require_acceptance_database(c, valid), valid)

    def test_candidate_registration_rejects_wrong_type_before_any_db_write(self):
        c = Mock()
        with self.assertRaises(ServiceError): scenario.register_acceptance_candidate(c, object())
        c.execute.assert_not_called()

    def test_candidate_install_rejects_actual_user_db_before_verifying_source(self):
        service = SandboxService(Mock(connection={'database':'qiongtu_manual_test_'+'a'*32}))
        service.settings.connect.return_value.__enter__ = Mock(return_value=Mock())
        service.settings.connect.return_value.__exit__ = Mock(return_value=False)
        c = service.settings.connect.return_value.__enter__.return_value
        c.execute.return_value.fetchone.return_value = dict(database_name='user_database')
        g = object.__new__(OfflineMapGeometry); g._verify_sources = Mock()
        with self.assertRaises(ServiceError): service.install_acceptance_geometry(g)
        g._verify_sources.assert_not_called()
        self.assertIsNone(service.battle_executor)

    def test_bundle_refuses_candidate_scope_without_loading_or_db(self):
        from backend.manual_server import load_bundle
        with TemporaryDirectory() as root:
            path = Path(root)/'bundle.json'
            path.write_text(json.dumps(dict(schema_version='qinglan-manual-server-bundle-v1',release_scope='candidate')))
            with self.assertRaises(ServiceError): load_bundle(path)

    def test_freezer_rejects_missing_real_cases_and_overwrites(self):
        from backend.manual_map_release import evidence_report, write_new
        with TemporaryDirectory() as root:
            with self.assertRaises(ServiceError): evidence_report(candidate(), root)
            path = Path(root)/'new.json'; write_new(path, {'candidate':True})
            with self.assertRaises(FileExistsError): write_new(path, {'passed':True})
            self.assertEqual(json.loads(path.read_text()), {'candidate':True})


class FrozenSourceCacheTests(TestCase):
    def test_first_read_hashes_then_same_stat_skips_bytes(self):
        from backend.battle_map_geometry import _verified_file
        from hashlib import sha256 as digest
        with TemporaryDirectory() as root:
            path = Path(root)/'frozen'; path.write_bytes(b'original')
            raw, signature = _verified_file(path, digest(b'original').hexdigest())
            self.assertEqual(raw, b'original')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('no repeated 98MB read')):
                self.assertEqual(_verified_file(path, digest(raw).hexdigest(), signature), (None, signature))

    def test_same_length_overwrite_restored_mtime_and_atomic_replace_fail(self):
        from backend.battle_map_geometry import _verified_file
        from hashlib import sha256 as digest
        import os
        with TemporaryDirectory() as root:
            path = Path(root)/'frozen'; path.write_bytes(b'original')
            stat = path.stat(); expected = digest(b'original').hexdigest()
            _, signature = _verified_file(path, expected)
            path.write_bytes(b'tampered'); os.utime(path, ns=(stat.st_atime_ns,stat.st_mtime_ns))
            with self.assertRaises(ServiceError): _verified_file(path, expected, signature)
            path.unlink(); path.write_bytes(b'tampered'); os.utime(path, ns=(stat.st_atime_ns,stat.st_mtime_ns))
            with self.assertRaises(ServiceError): _verified_file(path, expected, signature)
            path.unlink()
            with self.assertRaises(ServiceError): _verified_file(path, expected, signature)


class AgentScopeWriteGateTests(TestCase):
    def test_agent_request_explicitly_refuses_local_scope_before_any_run_insert(self):
        from backend.agent_service import AgentCoordinator
        from unittest.mock import MagicMock
        coordinator = object.__new__(AgentCoordinator)
        coordinator.sandbox = Mock()
        coordinator.settings = Mock()
        connection = MagicMock()
        coordinator.settings.connect.return_value = connection
        c = connection.__enter__.return_value
        c.execute.return_value.fetchone.return_value = None
        coordinator.principal = Mock(return_value={'role':'blue'})
        coordinator.load_configuration = Mock(return_value=Mock())
        coordinator.sandbox._game.return_value = {'state_payload': {'manifest': {'release_scope':'local_manual_test'}}}
        coordinator.sandbox._principal.return_value = {'role':'blue'}
        request = Mock()
        request.context.model_dump.return_value = {'game_id':'synthetic'}
        request.model_dump.return_value = {'context':{'game_id':'synthetic'},'reason':'test only'}
        with self.assertRaises(ServiceError) as raised:
            coordinator.request('synthetic', 'test-only-token', uuid4(), request)
        self.assertEqual(raised.exception.code, 'AGENT_MODE_FORBIDDEN')
        self.assertEqual(raised.exception.status, 403)
        self.assertFalse(any('INSERT' in str(call) for call in c.execute.call_args_list))


class BundleStartupTests(TestCase):
    def test_loader_runs_real_prepare_for_bound_recommendation(self):
        from backend.manual_server import load_bundle
        from backend.runtime import canonical
        from backend import battle_map_geometry
        from backend.battle_execution import Executor
        values = list(battle_setup.sources()); values[1] = candidate()
        body = setup_request(values)
        g = SetupGeometry(); g.rules,g.scenario,g.capacity = values
        g.scenario_sha256 = body['scenario_sha256']
        with TemporaryDirectory() as root:
            root = Path(root)
            (root/'setup.json').write_text(canonical(body))
            (root/'bundle.json').write_text(canonical(dict(schema_version='qinglan-manual-server-bundle-v1',
                release_scope='local_manual_test',surface_path='surface.json',interaction_path='interaction.json',
                scenario_path='scenario.json', evidence_directory='evidence',default_setup_path='setup.json')))
            with patch('backend.manual_server.load_local_manual_geometry', return_value=g) as loader:
                loaded, request = load_bundle(root/'bundle.json')
                self.assertIs(loaded, g); self.assertEqual(request.model_dump(mode='json'), body)
                self.assertEqual(loader.call_args.args, tuple((root/p).resolve() for p in ('surface.json','interaction.json','scenario.json','evidence')))
                bad = deepcopy(body); bad['scenario_sha256'] = '0'*64
                (root/'setup.json').write_text(canonical(bad))
                with self.assertRaises(ServiceError): load_bundle(root/'bundle.json')

    def test_app_installs_original_route_service_no_implicit_registration(self):
        from backend.manual_server import create_bundle_app
        from backend.runtime import Settings
        from starlette.middleware.cors import CORSMiddleware
        settings = Settings({'database':'test_no_connection'}, 'test-signing', 'test-bootstrap', scheduler_enabled=False)
        g = object()
        with patch('backend.manual_server.load_bundle', return_value=(g,None)), \
                patch.object(SandboxService,'install_manual_geometry') as install, \
                patch.object(Settings,'connect', side_effect=AssertionError('no implicit database access')):
            app = create_bundle_app(settings, '/test-only/bundle.json')
            install.assert_called_once_with(g)
            cors = next(m.kwargs for m in app.user_middleware if m.cls is CORSMiddleware)
            self.assertIn('http://127.0.0.1:18768', cors['allow_origins'])
            self.assertIn('http://127.0.0.1:18766', cors['allow_origins'])
            self.assertIn('http://127.0.0.1:18769', cors['allow_origins'])
            self.assertIn('http://localhost:18769', cors['allow_origins'])
            self.assertIs(app.state.service.settings, settings)


class InsertOnlyLocalRegistrationTests(TestCase):
    def test_existing_active_rules_are_not_updated_and_inactive_existing_refused(self):
        from backend import rulebook, battle_capacity
        v = candidate()
        g = Mock(scenario=v, rules=rulebook.definition(), capacity=battle_capacity.definition())
        for availability in ('active', 'draft'):
            with self.subTest(availability=availability):
                c = Mock()
                c.execute.return_value.fetchone.side_effect = [
                    dict(definition=g.rules, payload_sha256=sha256(g.rules), availability=availability),
                    dict(definition=g.capacity, payload_sha256=sha256(g.capacity), availability='active'), None]
                with patch.object(rulebook, 'register', return_value=dict(rule_version_id=v['rule_version_id'], inserted=False)), \
                     patch.object(battle_capacity, 'register', return_value=dict(inserted=False)):
                    if availability == 'active':
                        self.assertTrue(scenario._register_local_geometry(c, g)['inserted'])
                    else:
                        with self.assertRaises(ServiceError): scenario._register_local_geometry(c, g)
                self.assertFalse(any('UPDATE rule_versions SET' in str(call) for call in c.execute.call_args_list))

class IndependentDatabaseSettingsTests(TestCase):
    def test_explicit_new_database_only_and_original_config_unchanged(self):
        from backend.manual_server import local_settings
        from backend.runtime import Settings
        base = Settings(dict(host='127.0.0.1',port=55433,user='app',password='test-only',database='old_user_db'), 'signing','bootstrap')
        with TemporaryDirectory() as root, patch.object(Settings,'from_local',return_value=base):
            path=Path(root)/'private.json'; config=deepcopy(base.connection)
            config['database']='qiongtu_local_manual_'+'a'*32
            path.write_text(json.dumps(config))
            new=local_settings(path)
            self.assertFalse(new.scheduler_enabled);self.assertEqual(new.connection,config)
            self.assertEqual(base.connection['database'],'old_user_db')
            self.assertEqual(new.bootstrap_key,base.bootstrap_key)
            for key,value in [('database','old_user_db'),('host','remote.example'),('password','replacement')]:
                bad=deepcopy(config);bad[key]=value;path.write_text(json.dumps(bad))
                with self.assertRaises(ServiceError): local_settings(path)
