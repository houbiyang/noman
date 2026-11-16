"""五据点共享配置、桥面有效域、历史版本及目录版本回归。"""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from backend.capture_map import CAPTURE_PATH, current_layout, on_deck, validate
from backend.demo_domain import MAP_PATH, initial_state
from backend.map_catalog import current_map
from backend import battle_rules, qinglan_rules, spatial
from backend.runtime import ServiceError


class CaptureMapTests(unittest.TestCase):
    def state(self):
        state = initial_state('s', 'r', 'patrol')
        state['manifest']['spatial_profile'] = spatial.profile()
        qinglan_rules.initialize(state)
        state['units'] = []
        return state

    def test_shared_coordinates_and_unmodified_base_geometry(self):
        layout, digest = current_layout()
        self.assertEqual(digest, hashlib.sha256(CAPTURE_PATH.read_bytes()).hexdigest())
        expected = [('P-01', 4500, 8500), ('P-02', 4500, 4400), ('P-03', 7600, 8500),
                    ('P-04', 7600, 4400), ('P-05', 6162.963, 5800)]
        self.assertEqual([(p['id'], p['x'], p['z']) for p in layout['points']], expected)
        source = json.loads(MAP_PATH.read_bytes())
        self.assertEqual(current_map()['source_payload'], source)
        self.assertEqual(current_map()['import_audit']['source_sha256'], hashlib.sha256(MAP_PATH.read_bytes()).hexdigest())
        self.assertEqual(current_map()['import_audit']['capture_layout_sha256'], digest)
        self.assertTrue(all(p['radius_m'] == 250 and p['initial_owner'] == 'neutral' for p in current_map()['capture_points']))
        self.assertEqual({p['point_id'] for p in current_map()['points'] if p['kind'] == 'task'}, {'TASK_A','TASK_B','TASK_C'})
        self.assertEqual([p['owner'] for p in qinglan_rules.observe(self.state())], ['neutral'] * 5)

    def test_bridge_circle_deck_and_layers(self):
        point = qinglan_rules.DEFINITION['objectives'][-1]
        state = self.state()
        unit = dict(unit_id='u', side='red', unit_type='ground', durability=100)
        state['units'] = [unit]
        x, z = float(point['x']), float(point['z'])
        for dx, dz, expected in ((0,0,'red'), (250,0,'red'), (250.001,0,'neutral'),
                                  (0,16,'red'), (0,16.001,'neutral'), (0,100,'neutral'), (249.9,16,'neutral')):
            unit['position'] = spatial.business((x+dx,z+dz))
            with self.subTest(dx=dx,dz=dz): self.assertEqual(qinglan_rules.observe(state)[-1]['owner'], expected)
        unit['position'] = spatial.business((x,z))
        unit['navigation_layer_id'] = 'ground'
        self.assertEqual(qinglan_rules.observe(state)[-1]['owner'], 'neutral')
        unit['navigation_layer_id'] = point['navigation_layer_id']
        unit['surface_elevation_m'] = -10
        self.assertEqual(qinglan_rules.observe(state)[-1]['owner'], 'neutral')
        unit['surface_elevation_m'] = 22
        self.assertEqual(qinglan_rules.observe(state)[-1]['owner'], 'red')
        self.assertFalse(on_deck(point['deck']['start']['x']-1,z,point['deck']))
        self.assertFalse(on_deck(point['deck']['end']['x']+1,z,point['deck']))

    def test_legacy_rule_and_saved_evidence_still_score(self):
        state = self.state()
        state['manifest']['victory_rules'] = deepcopy(qinglan_rules.LEGACY_DEFINITION)
        state['victory']['ruleVersion'] = qinglan_rules.LEGACY_VERSION
        self.assertTrue(qinglan_rules.enabled(state))
        p = qinglan_rules.LEGACY_DEFINITION['objectives'][0]
        state['units'] = [dict(unit_id='u',side='red',unit_type='ground',durability=100,
                               position=spatial.business((float(p['x'])+400,float(p['z']))))]
        for minute in (20,40,60):
            state['lifecycle']['simulation_step'] = minute
            battle_rules.after_advance(state)
        result = battle_rules.evaluate(battle_rules.evidence(state))
        self.assertTrue(result['final'])
        self.assertEqual((result['redPoints'], result['scoreMaximum']), (6,18))
        self.assertEqual(len(state['victory']['stages'][0]['objectives']),3)

    def test_validation_rejects_bad_base_bridge_radius_and_duplicate(self):
        layout,_ = current_layout(); source=json.loads(MAP_PATH.read_bytes())
        for mutation in ('base','road','radius','duplicate','support'):
            value=deepcopy(layout)
            if mutation=='base': value['baseMapContentHash']='0'*64
            elif mutation=='road': value['points'][-1]['bridgeRoadId']='ROAD_001'
            elif mutation=='radius': value['points'][0]['radius']=200
            elif mutation=='duplicate': value['points'][1]['id']='P-01'
            else: value['points'][-1]['areaPolicy']='circle'
            with self.subTest(mutation=mutation), self.assertRaises(ServiceError): validate(value,source)

    def test_five_point_maximum_and_forged_legacy_ids_rejected(self):
        state=self.state()
        for i,p in enumerate(qinglan_rules.DEFINITION['objectives']):
            state['units'].append(dict(unit_id=str(i), side='red', unit_type='ground', durability=100,
                                       position=spatial.business((float(p['x']),float(p['z'])))))
        for minute in (20,40,60):
            state['lifecycle']['simulation_step']=minute; battle_rules.after_advance(state)
        result=battle_rules.evaluate(battle_rules.evidence(state))
        self.assertEqual((result['redPoints'],result['scoreMaximum'],result['redScore100']),(30,30,100))
        self.assertTrue(result['final'])
        state['victory']['stages'][0]['objectives'][0]['point_id']='TASK_A'
        self.assertEqual(battle_rules.evaluate(state['victory'])['scoreMaximum'],0)

    def test_coordination_uses_frozen_five_point_layer_only_in_new_mode(self):
        from backend import coordination
        from backend.agent_contracts import CoordinationRules
        from backend.demo_domain import map_manifest
        state=self.state()
        state['manifest']['map_source_sha256']=map_manifest()['source_file_sha256']
        with patch('backend.coordination.movement.Navigator') as nav:
            nav.return_value.clear.return_value=True
            coordination.enable(state, CoordinationRules().model_dump())
            self.assertEqual([p['point_id'] for p in state['coordination']['points']], list(qinglan_rules.POINT_IDS))
            point=state['coordination']['points'][-1]
            unit=dict(unit_id='u', side='red', unit_type='ground', availability='available', durability=100,
                      movement_departed=True, position=spatial.business((6162.963,5800)))
            self.assertTrue(coordination.in_point(state,unit,point))
            unit['position']=spatial.business((6162.963,5817))
            self.assertFalse(coordination.in_point(state,unit,point))
        state=self.state(); state['manifest'].pop('victory_rules')
        state['manifest']['map_source_sha256']=map_manifest()['source_file_sha256']
        with patch('backend.coordination.movement.Navigator') as nav:
            nav.return_value.clear.return_value=True
            coordination.enable(state,CoordinationRules().model_dump())
        self.assertEqual([p['point_id'] for p in state['coordination']['points']], list(qinglan_rules.LEGACY_POINT_IDS))
