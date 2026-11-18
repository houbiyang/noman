"""Versioned field energy exception. Synthetic geometry only; no DB or real-map acceptance."""
from copy import deepcopy
from fractions import Fraction as F
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from pydantic import ValidationError

from backend import battle_kernel as k, battle_replay, battle_scenario, battle_setup
from backend.battle_execution import Executor
from backend.battle_map_geometry import MapGeometry
from backend.battle_map_interactions import Interactions
from backend.battle_preflight import scenario_check
from backend.runtime import ServiceError, sha256
from backend.tests.test_battle_execution import FixtureGeometry, order, state_fixture
from backend.tests.test_battle_kernel import request, service, spend, unit
from backend.tests.test_battle_map_geometry import terrain
from backend.tests.test_battle_setup import SetupGeometry, setup_request

POLICY_KEY = 'field_energy_service_policy'
POLICY = k.FIELD_ENERGY_SERVICE_POLICY


def scenario_values():
    return battle_setup.sources(hover_energy=True)


def flying_state(*, enabled=True, distance_mm=100000):
    state = state_fixture()
    if enabled:
        state['manifest'][POLICY_KEY] = POLICY
    source, target = unit(state, 'support'), unit(state, 'recon')
    spend(state, target, 'energy', F(20))
    target['surface_id'] = 'bridge'
    source['surface_id'] = 'ground'
    target['air_state'] = 'flying'
    target['position_mm'].update(x_mm=source['position_mm']['x_mm']+distance_mm, height_mm=30000)
    return state, source, target


class HoverFieldKernelTests(TestCase):
    def deliver(self, state, *, resource='energy', hover_energy_clear=lambda *_: True, **kwargs):
        return service(state, [request(state, resource, target_kind='recon', quantity='3')],
                       hover_energy_clear=hover_energy_clear, **kwargs)

    def test_boundary_exactly_100m_and_100m_plus_one_mm(self):
        for mm, expected in ((100000, 2), (100001, 0)):
            with self.subTest(mm=mm):
                state, source, target = flying_state(distance_mm=mm)
                before = deepcopy(state)
                after, events = self.deliver(state, service_clear=lambda *_: self.fail('no ground link required'))
                self.assertEqual(state, before)
                self.assertEqual(F(unit(after, 'recon')['energy']), F(target['energy'])+expected)
                self.assertEqual(F(unit(after, 'support')['cargo']['energy']),F(source['cargo']['energy'])-expected)
                self.assertEqual([e['amount'] for e in events], ['2'] if expected else [])
                self.assertEqual(after['battle_ledger'], before['battle_ledger'])
                k.validate(after)

    def test_old_rule_and_old_adapter_still_reject_airborne_target(self):
        for enabled, callback in ((False, lambda *_: self.fail('old policy invoked hover callback')),
                                  (True, None), (True, lambda *_: False)):
            state, _, _ = flying_state(enabled=enabled)
            after, events = self.deliver(state, hover_energy_clear=callback)
            self.assertEqual((after,events),(state,[]))

    def test_old_docked_service_still_uses_ground_geometry(self):
        state, _, target = flying_state(enabled=False)
        target['air_state'] = 'docked'
        self.assertEqual(self.deliver(state, service_clear=lambda *_: False)[1], [])
        self.assertEqual(self.deliver(state)[1][0]['amount'], '2')

    def test_inventory_limit_and_empty_inventory(self):
        state, source, _ = flying_state()
        cargo = F(3,2)
        # Record previously spent stock in the existing conserved ledger.
        k._add(state['battle_ledger']['blue']['consumed'], 'energy', F(source['cargo']['energy'])-cargo)
        source['cargo']['energy'] = k.exact(cargo)
        after, events = self.deliver(state)
        self.assertEqual(events[0]['amount'], '3/2')
        self.assertEqual(unit(after, 'support')['cargo']['energy'], '0')
        self.assertEqual(self.deliver(after)[1], [])
        k.validate(after)

    def test_start_power_stationarity_and_takeoff_landing_retained(self):
        for scenario in ('moving_target','moving_source','no_start_energy','taking_off','landing'):
            with self.subTest(scenario=scenario):
                state, source, target = flying_state()
                stationary = {source['unit_id'],target['unit_id']}
                positive = set(stationary)
                if scenario=='moving_target': stationary.remove(target['unit_id'])
                if scenario=='moving_source': stationary.remove(source['unit_id'])
                if scenario=='no_start_energy': positive.remove(source['unit_id'])
                if scenario in ('taking_off','landing'): target['air_state']=scenario
                after, events = self.deliver(state, stationary=stationary, start_positive=positive,
                    hover_energy_clear=lambda *_: self.fail('not eligible to check geometry'))
                self.assertEqual((after,events),(state,[]))

    def test_retired_units_and_supplier_out_of_operating_energy_do_not_deliver(self):
        for invalid in ('retired_source', 'retired_target', 'empty_source_energy'):
            with self.subTest(invalid=invalid):
                state, source, target = flying_state()
                if invalid == 'empty_source_energy':
                    spend(state, source, 'energy', F(source['energy']))
                else:
                    k._retire(state, source if invalid == 'retired_source' else target, 'attack', 'red')
                self.assertEqual(self.deliver(state, start_positive={source['unit_id']},
                    hover_energy_clear=lambda *_: self.fail('not eligible to check geometry')), (state, []))

    def test_stale_life_requests_rejected(self):
        state, _, _ = flying_state()
        for field in ('source_life_id', 'target_life_id'):
            req = request(state, target_kind='recon')
            req[field] = 'stale-life'
            with self.assertRaises(ServiceError) as error:
                service(state, [req], hover_energy_clear=lambda *_: self.fail('stale request'))
            self.assertEqual(error.exception.code, 'BATTLE_SERVICE_INVALID')

    def test_target_balance_limits_delivery_and_full_recipient_does_not_draw_cargo(self):
        state, _, target = flying_state()
        req = request(state, target_kind='recon', target_balance=k.exact(F(target['energy'])+F(1, 3)))
        after, events = service(state, [req], hover_energy_clear=lambda *_: True)
        self.assertEqual(events[0]['amount'], '1/3')
        self.assertEqual(events[0]['remaining_request'], req)
        self.assertEqual(service(after, [req], hover_energy_clear=lambda *_: True), (after, []))
        full, _, target = flying_state()
        spend(full, target, 'energy', -F(20))
        self.assertEqual(self.deliver(full), (full, []))

    def test_enemy_and_non_support_request_rejected(self):
        for invalid in ('enemy','non_support'):
            state, _, _ = flying_state()
            req = request(state,target_kind='recon')
            item = unit(state,'recon','red') if invalid=='enemy' else unit(state,'ground')
            role = 'target' if invalid=='enemy' else 'source'
            req.update({role+'_id':item['unit_id'],role+'_life_id':item['life_id']})
            with self.assertRaises(ServiceError) as error:
                service(state,[req],hover_energy_clear=lambda *_: True)
            self.assertEqual(error.exception.code,'BATTLE_SERVICE_INVALID')

    def test_repair_barracks_and_ground_targets_not_relaxed(self):
        state, source, target = flying_state()
        target['durability'] = '10'
        forbidden = lambda *_: self.fail('unapproved category called hover geometry')
        self.assertEqual(self.deliver(state,resource='repair',hover_energy_clear=forbidden)[1],[])
        req = dict(unit_id=target['unit_id'],life_id=target['life_id'],camp_id='blue-camp',targets={'energy':'100'})
        camp = dict(camp_id='blue-camp',side='blue',position_mm=source['position_mm'],deployment_validated=True)
        _, deliveries = k.barracks_step(state,[req],[camp],seconds=5,stationary={target['unit_id']},service_clear=lambda *_: True)
        self.assertEqual(deliveries,[])
        req = request(state,target_kind='ground')
        spend(state,unit(state),'energy',F(10))
        self.assertEqual(service(state,[req],service_clear=lambda *_: False,hover_energy_clear=forbidden)[1],[])


class HoverGeometryAndRoundTests(TestCase):
    def geometry(self):
        real_adapter = object.__new__(MapGeometry)
        real_adapter.scenario = scenario_values()[1]
        adapter = terrain()
        adapter.definition['dock_surfaces'] = ['ground']  # target is on synthetic bridge layer, never a dock
        real_adapter.interactions = Interactions(adapter,None,adapter.forests)
        g = FixtureGeometry()
        g.hover_energy_clear = real_adapter.hover_energy_clear
        g.service_clear = real_adapter.service_clear
        return g, real_adapter

    @staticmethod
    def command(state, **value):
        target=unit(state,'recon')
        return order(state,'support',task=dict(action='resupply',resource='energy',
                    target_id=target['unit_id'],target_life_id=target['life_id'],**value))

    def test_adapter_only_checks_supplier_footprint_not_dock_or_route(self):
        _, source, target = flying_state()
        _, geom = self.geometry(); terrain_adapter = geom.interactions.terrain
        self.assertFalse(terrain_adapter.dock_footprint_legal(target))
        self.assertFalse(geom.service_clear(source,target))
        with patch.object(terrain_adapter,'service_segment_clear',side_effect=AssertionError('no ground link')):
            self.assertTrue(geom.hover_energy_clear(source,target))
        with patch.object(terrain_adapter, 'service_footprint_legal', return_value=False) as footprint:
            self.assertFalse(geom.hover_energy_clear(source, target))
            footprint.assert_called_once_with(source)
        source['position_mm']['height_mm'] = 1
        with self.assertRaises(ServiceError):
            geom.hover_energy_clear(source,target)

    def test_command_to_sealed_replay_records_only_actual_delivery_and_endpoints(self):
        state, source, target = flying_state()
        g, _ = self.geometry(); executor = Executor(g)
        after, record = executor(state,dict(blue=[self.command(state,quantity='5/2')],red=[]))
        record = json.loads(json.dumps(record))
        events=[e for f in record['frames'] for e in f['events'] if e['kind']=='service']
        self.assertEqual([e['amount'] for e in events],['2','1/2'])
        self.assertEqual([e['at_seconds'] for e in events],[5,10])
        for event in events:
            battle_replay.VisualEvent.model_validate(event)
            self.assertEqual(event['source_position'],source['position_mm'])
            self.assertEqual(event['target_position'],target['position_mm'])
            self.assertEqual(event['target_life_id'],target['life_id'])
            self.assertEqual(event['resource'],'energy')
        self.assertEqual(F(unit(after,'recon')['energy']), F(80)+F(5,2)-12*F(1,6))
        self.assertEqual(F(unit(after,'support')['cargo']['energy']),F(200)-F(5,2))
        self.assertEqual(F(after['battle_ledger']['blue']['consumed']['energy']),F(22))
        self.assertIsNone(unit(after,'support')['task'])
        k.validate(after)

    def test_executor_rejects_new_supply_while_receiver_has_movement(self):
        state, _, target = flying_state()
        target['movement'] = dict(action='move',position=dict(x='11',y='10'))
        g, _ = self.geometry()
        with self.assertRaises(ServiceError) as failure:
            Executor(g)(state,dict(blue=[self.command(state,quantity='2')],red=[]))
        self.assertEqual(failure.exception.code,'INTENT_CONFLICT')

    def test_executor_actual_takeoff_and_landing_do_not_deliver_in_that_step(self):
        for action, initial in (('takeoff','docked'), ('land','flying')):
            with self.subTest(action=action):
                state, _, target=flying_state()
                target['air_state']=initial
                target['position_mm']['height_mm']=30000 if initial=='flying' else 0
                g,_=self.geometry(); executor=Executor(g)
                executor._adopt(state,dict(blue=[self.command(state,quantity='2'),
                    order(state,'recon',task=dict(action=action))],red=[]))
                after, frame=executor.step(state)
                self.assertEqual([e['kind'] for e in frame['events']],[action])
                self.assertEqual(unit(after,'support')['cargo']['energy'],'200')


class HoverPolicyFreezeTests(TestCase):
    def test_policy_is_server_scenario_only_and_new_identity(self):
        old = battle_setup.sources(); values = scenario_values()
        old_request=setup_request(old); body=setup_request(values)
        self.assertNotEqual(old_request['scenario_version_id'],body['scenario_version_id'])
        self.assertNotEqual(old_request['scenario_sha256'],body['scenario_sha256'])
        self.assertEqual(old_request['rule_sha256'],body['rule_sha256'])
        with self.assertRaises(ValidationError):
            battle_setup.CreateManualBattle.model_validate(dict(body,**{POLICY_KEY:POLICY}))
        geometry=SetupGeometry()
        for payload, sources, expected in ((old_request,old,None),(body,values,POLICY)):
            state=battle_setup.build(battle_setup.CreateManualBattle.model_validate(payload),*sources,geometry)
            manifest=state['manifest']
            self.assertEqual(manifest.get(POLICY_KEY),expected)
            self.assertEqual(POLICY_KEY in manifest,expected is not None)
            self.assertTrue(battle_scenario.service_policy_matches(manifest,sources[1]))
            if expected is None:
                manifest[POLICY_KEY]=POLICY
            else:
                manifest.pop(POLICY_KEY)
            self.assertFalse(battle_scenario.service_policy_matches(manifest,sources[1]))
        for invalid in (dict(old[1],**{POLICY_KEY:POLICY}),dict(values[1],**{POLICY_KEY:'unknown-v2'})):
            sources=(values[0],invalid,values[2]); request=setup_request(sources)
            with self.assertRaises(ServiceError) as failure:
                battle_setup.build(battle_setup.CreateManualBattle.model_validate(request),*sources,geometry)
            self.assertEqual(failure.exception.code,'BATTLE_SERVICE_POLICY_INVALID')

    def test_preflight_declines_frozen_mismatch_even_with_valid_database_version(self):
        rules, scenario, capacity = scenario_values()
        scenario['acceptance'].update(status='accepted', blocks=[])
        for field in ('structural_checks_passed','terrain_masks_validated','ground_and_support_routes_validated',
                      'bridge_actual_grade_validated','service_surface_isolation_validated'):
            scenario['acceptance'][field]=True
        body=setup_request((rules,scenario,capacity))
        state=battle_setup.build(battle_setup.CreateManualBattle.model_validate(body),rules,scenario,capacity,SetupGeometry())
        game=dict(state_payload=state,scenario_version_id=body['scenario_version_id'],rule_version_id=body['rule_version_id'])
        row=dict(definition=scenario, payload_sha256=sha256(scenario), availability='active',
                 rule_definition=rules,rule_payload_sha256=sha256(rules),rule_availability='active')
        self.assertEqual(scenario_check(game,row,scenario=scenario)['status'],'passed')
        state['manifest'].pop(POLICY_KEY)
        self.assertEqual(scenario_check(game,row,scenario=scenario)['status'],'failed')

    def test_opt_in_requires_distinct_real_map_evidence_case_and_policy_binding(self):
        def case(case_id):
            return dict(case_id=case_id,passed=True,source='real_map',evidence_sha256='a'*64)
        report=dict(cases=[case(c) for c in battle_scenario.REQUIRED_ACCEPTANCE_CASES])
        self.assertTrue(battle_scenario.acceptance_evidence_valid(report))
        self.assertFalse(battle_scenario.acceptance_evidence_valid(report,field_energy_service_policy=POLICY))
        report['cases'].append(case(battle_scenario.HOVER_ENERGY_CASE))
        self.assertFalse(battle_scenario.acceptance_evidence_valid(report,field_energy_service_policy=POLICY))
        report[POLICY_KEY]=POLICY
        self.assertTrue(battle_scenario.acceptance_evidence_valid(report,field_energy_service_policy=POLICY))
        self.assertFalse(battle_scenario.acceptance_evidence_valid(report))

    def test_hover_acceptance_record_binds_policy_not_just_map_and_geometry(self):
        scenario = scenario_values()[1]
        binding = {'surface_catalog_sha256': 'a'*64}
        case = battle_scenario.HOVER_ENERGY_CASE
        record = dict(case_id=case, passed=True, source='real_map', geometry_binding=binding,
                      map_source_sha256=scenario['map']['source_sha256'])
        matches = battle_scenario.acceptance_record_matches
        self.assertFalse(matches(record, case, geometry_binding=binding, scenario=scenario))
        for value, expected in (('unknown-v2', False), (POLICY, True)):
            record[POLICY_KEY] = value
            self.assertEqual(matches(record, case, geometry_binding=binding, scenario=scenario), expected)
        self.assertFalse(matches(record, case, geometry_binding={}, scenario=scenario))
        self.assertFalse(matches(record, case, geometry_binding=binding, scenario=battle_scenario.definition()))

    def test_opt_in_registration_inserts_separate_draft_without_changing_default(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                value = battle_setup.sources(hover_energy=enabled)[1]
                conn = Mock()
                conn.execute.return_value.fetchone.return_value = None
                rule_row = {'rule_version_id': value['rule_version_id']}
                with patch.object(battle_scenario.rulebook, 'register', return_value=rule_row):
                    result = battle_scenario.register(conn, hover_energy=enabled)
                advisory, lookup, insert = conn.execute.call_args_list
                self.assertIn('pg_advisory_xact_lock', advisory.args[0])
                self.assertEqual(lookup.args[1], (battle_scenario.SCENARIO_CODE, value['version']))
                self.assertIn('INSERT INTO scenario_versions', insert.args[0])
                self.assertIn("'draft',false", insert.args[0])
                self.assertEqual(insert.args[1][5].obj, value)
                self.assertEqual(result, dict(scenario_version_id=battle_scenario.identity(value),
                    payload_sha256=sha256(value), inserted=True, availability='draft'))

    def test_new_provider_uses_explicit_draft_not_implicit_old_rule_migration(self):
        from backend.service import SandboxService
        provider = object.__new__(SandboxService); provider.battle_executor = None
        sources = provider._manual_sources()
        self.assertEqual(sources[1],battle_scenario.hover_energy_definition())
        self.assertFalse(battle_scenario.accepted(sources[1]))
        self.assertIn('hover_energy_service',sources[1]['acceptance']['blocks'])
        self.assertNotIn(POLICY_KEY,battle_scenario.definition())
        self.assertEqual(provider.manual_setup()[POLICY_KEY],POLICY)
        self.assertFalse(provider.manual_setup()['can_create'])

    def test_role_views_publish_only_frozen_manual_policy_not_current_default(self):
        from backend.service import SandboxService
        from uuid import uuid4
        provider=object.__new__(SandboxService); provider.battle_executor=None
        provider._context=lambda *_: {}
        game=dict(id=uuid4(),game_no='test',title='synthetic',is_demo=False,
                  scenario_version_id=uuid4(),rule_version_id=uuid4(),head_snapshot_id=uuid4())
        for enabled in (False,True):
            sources=battle_setup.sources(hover_energy=enabled)
            body=setup_request(sources)
            state=battle_setup.build(battle_setup.CreateManualBattle.model_validate(body),*sources,SetupGeometry())
            for role in ('host','blue','red'):
                view=provider._project(game,dict(role=role),state)
                self.assertEqual(POLICY_KEY in view['manual_battle'],enabled)
                self.assertEqual(view['manual_battle'].get(POLICY_KEY),POLICY if enabled else None)
                self.assertFalse(view['manual_battle']['engine_ready'])
