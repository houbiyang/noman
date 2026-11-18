"""配置工厂纯测试：合成地图不能用于发布青岚真实地图。"""
from copy import deepcopy
from unittest import TestCase

from pydantic import ValidationError

from backend import battle_setup, battle_kernel as k, battle_scenario
from backend.battle_execution import Executor
from backend.runtime import ServiceError
from backend.tests.test_battle_execution import FixtureGeometry


def setup_request(values=None):
    values = values or battle_setup.sources()
    binding = battle_setup.versions(*values)
    config = {}
    for side, x in (('blue', 10000000), ('red', 1000000)):
        config[side] = dict(quantities=dict(recon=1, ground=1, support=1),
            units=[dict(unit_type=kind, ordinal=1, position=dict(x_mm=x, z_mm=2000000 + i*100000, height_mm=0),
                        surface_id='synthetic-plane') for i, kind in enumerate(('recon', 'ground', 'support'))],
            camps=[dict(unit_type=kind, position=dict(x_mm=x, z_mm=3000000 + i*200000, height_mm=0),
                surface_id='synthetic-plane', service_anchor=dict(x_mm=x+50000, z_mm=3000000+i*200000, height_mm=0),
                service_surface_id='synthetic-plane') for i, kind in enumerate(('recon', 'ground', 'support'))])
    return dict(title='仅隔离合成新局', mode='manual', **binding, **config)


class SetupGeometry(FixtureGeometry):
    """仅随机测试库/纯测试使用；不作为生产适配器。"""
    def verify(self, state):
        if state['manifest']['battle_scenario_sha256'] != self.scenario_sha256:
            raise ServiceError(503, 'SYNTHETIC_BINDING', '合成场景不符')

    def validate_deployment(self, state):
        return True

    def camps(self, state):
        return deepcopy(state['camps'])


class BattleSetupTests(TestCase):
    def build(self, body=None):
        values = battle_setup.sources()
        body = body or setup_request(values)
        request = battle_setup.CreateManualBattle.model_validate(body)
        geometry = SetupGeometry(); geometry.scenario_sha256 = body['scenario_sha256']
        return battle_setup.build(request, *values, geometry), geometry

    def test_initial_resources_roster_six_camps_and_neutral_points(self):
        state, geometry = self.build()
        Executor(geometry).prepare_new_game(state)
        self.assertEqual(len(state['units']), 6)
        self.assertEqual(len(state['camps']), 6)
        self.assertEqual(len({u['life_id'] for u in state['units']}), 6)
        for u in state['units']:
            p = k.profiles()[u['unit_type']]
            self.assertEqual(u['durability'], str(p['durability']))
            self.assertEqual(u['energy'], str(p['energy']))
            self.assertEqual(u['ammo'], str(p['ammo']))
            self.assertIsNone(u['task']); self.assertIsNone(u['movement'])
            self.assertEqual(u['speed_m_per_minute'], '0')
            self.assertEqual(u['next_fire_seconds'], 0)
            if u['unit_type'] == 'recon': self.assertEqual(u['air_state'], 'docked')
            if u['unit_type'] == 'support': self.assertTrue(all(k.number(v) > 0 for v in u['cargo'].values()))
        for point in state['battle_points']:
            self.assertEqual((point['owner'], point['progress_steps']), ('neutral', 0))
        for side in ('blue', 'red'):
            self.assertEqual(k.score(state, side)['total'], '30')
            self.assertEqual(state['battle_ledger'][side]['kills'], '0')
        self.assertNotIn('victory', state); self.assertNotIn('unit_interactions', state)

    def test_zero_type_asymmetric_and_no_capture_warning(self):
        body = setup_request(); body['blue']['quantities'] = dict(recon=1, ground=0, support=0)
        body['blue']['units'] = body['blue']['units'][:1]
        request = battle_setup.CreateManualBattle.model_validate(body)
        self.assertEqual(battle_setup.warnings(request)[0]['code'], 'NO_CAPTURE_CAPABILITY')
        state, _ = self.build(body)
        self.assertEqual(len(state['units']), 4)
        self.assertEqual(len(state['camps']), 6)
        self.assertIsNone(k.terminal(state, 0))

    def test_roster_missing_duplicate_wrong_ordinal_and_quantity_rejected(self):
        for mutate in (lambda b: b['blue']['units'].pop(),
                       lambda b: b['blue']['units'].append(deepcopy(b['blue']['units'][0])),
                       lambda b: b['blue']['units'][0].update(ordinal=2),
                       lambda b: b['blue']['quantities'].update(recon=True),
                       lambda b: b['blue']['quantities'].update(recon=0, ground=0, support=0),
                       lambda b: b['blue']['quantities'].update(ground=11)):
            body = setup_request(); mutate(body)
            with self.assertRaises(ValidationError): battle_setup.CreateManualBattle.model_validate(body)

    def test_three_explicit_camps_positions_surfaces_and_no_extra_fields(self):
        for mutate in (lambda b: b['red']['camps'].pop(),
                       lambda b: b['red']['camps'][0].update(unit_type='ground'),
                       lambda b: b['blue']['units'][0].update(ammo=999),
                       lambda b: b['blue']['units'][0].update(surface_id=' '),
                       lambda b: b['blue']['units'][0]['position'].update(x_mm=12000001),
                       lambda b: b.update(accepted=True),
                       lambda b: b.update(mode='agent')):
            body = setup_request(); mutate(body)
            with self.assertRaises(ValidationError): battle_setup.CreateManualBattle.model_validate(body)

    def test_versions_and_hashes_cannot_be_silently_replaced(self):
        for key in ('rule_sha256', 'scenario_sha256', 'capacity_sha256'):
            body = setup_request(); body[key] = '0'*64
            with self.assertRaises(ServiceError) as caught: self.build(body)
            self.assertEqual(caught.exception.code, 'BATTLE_VERSION_CHANGED')

    def test_new_lives_and_input_immutable(self):
        body = setup_request(); before = deepcopy(body)
        a, _ = self.build(body); b, _ = self.build(body)
        self.assertEqual(body, before)
        self.assertEqual([u['unit_id'] for u in a['units']], [u['unit_id'] for u in b['units']])
        self.assertTrue(set(u['life_id'] for u in a['units']).isdisjoint(u['life_id'] for u in b['units']))

    def test_default_geometry_cannot_skip_deployment_acceptance(self):
        state, _ = self.build()
        with self.assertRaises(ServiceError) as caught: Executor(FixtureGeometry()).prepare_new_game(state)
        self.assertEqual(caught.exception.code, 'BATTLE_DEPLOYMENT_NOT_READY')

    def test_acceptance_must_be_strict_and_complete(self):
        value = battle_scenario.definition()
        self.assertFalse(battle_scenario.accepted(value))
        value['acceptance'] = {key: True for key in value['acceptance'] if key not in ('status', 'blocks')}
        value['acceptance'].update(status='accepted', blocks=[])
        self.assertTrue(battle_scenario.accepted(value))
        for key in list(value['acceptance']):
            bad = deepcopy(value); bad['acceptance'].pop(key)
            self.assertFalse(battle_scenario.accepted(bad))

    def test_deployment_callback_cannot_mutate_state_and_requires_true(self):
        state, geo = self.build(); before = deepcopy(state)
        def callback(copy):
            copy['units'][0]['ammo'] = '999'
            return True
        geo.validate_deployment = callback
        Executor(geo).prepare_new_game(state)
        self.assertEqual(state, before)
        geo.validate_deployment = lambda _: 1
        with self.assertRaises(ServiceError): Executor(geo).prepare_new_game(state)
