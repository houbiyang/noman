"""桥面分层导出及真实三桥通行回归；不读取或修改用户对局。"""

import base64
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from unittest import TestCase, main
import zlib

from backend import movement, spatial
from backend.demo_domain import MAP_PATH, initial_state

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('export_movement_map', ROOT / 'unity-client/Tools/export_movement_map.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def rectangle(layer, material, left, right, bottom, top, height):
    """以两片三角形构造水平测试面，不依赖真实地图生成器。"""
    return dict(layer=layer, material=material,
                vertices=[left,height,bottom, right,height,bottom, right,height,top, left,height,top],
                triangles=[0,1,2,0,2,3])


class BridgeExportTests(TestCase):
    """桥下实体不能切断桥面；真实桥面障碍不能被无条件放行。"""

    def setUp(self):
        self.definition = dict(mapId='bridge-fixture', width=200, length=100,
            heightColumns=2, heightRows=2, heights=[-6]*4,
            nodes=[dict(id='a',x=20,z=50),dict(id='b',x=180,z=50)],
            roads=[dict(id='bridge',startId='a',endId='b',isBridge=True,width=30,surfaceHeight=22)])
        self.water = rectangle('water','Water',0,200,0,100,1.3)

    def terrain(self, parts, point=(102.5,52.5)):
        result = exporter.build_navigation(json.dumps(self.definition).encode(),json.dumps(dict(parts=parts)).encode())
        grid = zlib.decompress(base64.b64decode(result['grid_zlib_base64']))
        self.assertEqual(hashlib.sha256(grid).hexdigest(),result['grid_sha256'])
        index = int(point[1]/5)*result['columns']+int(point[0]/5)
        return result['terrain_names'][grid[index]]

    def test_pier_top_flush_with_deck_does_not_block(self):
        pier = rectangle('facilities','Battle_Concrete',98,102,38,62,22)
        self.assertEqual(self.terrain([self.water,pier]),'bridge')

    def test_structure_below_deck_does_not_block(self):
        pier = rectangle('facilities','Battle_Concrete',98,102,38,62,10)
        self.assertEqual(self.terrain([self.water,pier]),'bridge')

    def test_structure_above_deck_still_blocks(self):
        obstacle = rectangle('facilities','Battle_Concrete',98,102,38,62,23.3)
        self.assertEqual(self.terrain([self.water,obstacle]),'obstacle')

    def test_later_pier_does_not_erase_real_deck_obstacle(self):
        obstacle = rectangle('facilities','Battle_Concrete',98,102,38,62,23.3)
        pier = rectangle('facilities','Battle_Concrete',98,102,38,62,22)
        for parts in ([obstacle,pier],[pier,obstacle]):
            with self.subTest(order=[part['vertices'][1] for part in parts]):
                self.assertEqual(self.terrain([self.water,*parts]),'obstacle')

    def test_same_height_structure_outside_bridge_still_blocks(self):
        obstacle = rectangle('facilities','Battle_Concrete',98,102,8,12,22)
        self.assertEqual(self.terrain([self.water,obstacle],(102.5,12.5)),'obstacle')

    def test_water_outside_bridge_remains_water(self):
        self.assertEqual(self.terrain([self.water],(102.5,22.5)),'water')

    def test_rail_above_deck_remains_obstacle(self):
        rail = rectangle('facilities','Battle_Concrete',20,180,64,66,23.3)
        self.assertEqual(self.terrain([self.water,rail],(102.5,62.5)),'obstacle')

    def test_diagonal_bridge_also_uses_its_deck_height(self):
        self.definition['nodes'] = [dict(id='a',x=20,z=10),dict(id='b',x=180,z=90)]
        pier = rectangle('facilities','Battle_Concrete',98,102,48,52,22)
        self.assertEqual(self.terrain([self.water,pier]),'bridge')


class PublishedBridgeTests(TestCase):
    """用实际发布网格验证三座桥、两种地面兵种和完整推进，不只检查节点连通。"""

    def setUp(self):
        self.state = initial_state('bridge-audit','bridge-audit','patrol')
        self.state['manifest'].update(map_source_sha256=hashlib.sha256(MAP_PATH.read_bytes()).hexdigest(),
                                      spatial_profile=spatial.profile())
        self.state['camps'] = []
        self.unit = next(unit for unit in self.state['units'] if unit['unit_id']=='R-G01')
        self.unit.update(energy=100,durability=100)
        self.data = spatial.map_data(self.state)
        self.nodes = {node['id']:(node['x'],node['z']) for node in self.data['nodes']}
        self.bridges = [road for road in self.data['roads'] if road['isBridge']]
        self.assertEqual(len(self.bridges),3)

    def test_full_crossings_both_directions_and_modes(self):
        clock = deepcopy(self.state['lifecycle'])
        for bridge in self.bridges:
            a,b = self.nodes[bridge['startId']],self.nodes[bridge['endId']]
            banks = ((a[0]-100,a[1]),(b[0]+100,b[1]))
            for kind in ('ground','support'):
                for mode in ('move','fast_move'):
                    for start,end in (banks,banks[::-1]):
                        with self.subTest(bridge=bridge['id'],kind=kind,mode=mode,start=start):
                            self.unit.update(unit_type=kind,position=spatial.business(start),movement_test_energy=100)
                            route = movement.plan(self.state,self.unit,spatial.business(end),mode)
                            self.assertAlmostEqual(route['distance_m'],900,places=5)
                            self.assertTrue(any(piece['terrain']=='bridge' for piece in route['segments']))
                            self.assertFalse(any(piece['terrain'] in ('water','obstacle') for piece in route['segments']))
                            expected_energy = (7.2 if kind=='ground' else 9)*(1.2 if mode=='fast_move' else 1)
                            self.assertAlmostEqual(route['estimated_energy'],expected_energy,places=5)
                            for _ in range(10):
                                if route['status']=='arrived': break
                                movement.advance_unit(self.state,self.unit,route,deployment=True)
                            self.assertEqual(route['status'],'arrived')
                            self.assertAlmostEqual(spatial.meters(self.unit['position'])[0],end[0],places=5)
                            self.assertAlmostEqual(spatial.meters(self.unit['position'])[1],end[1],places=5)
                            self.assertAlmostEqual(self.unit['movement_test_energy'],100-expected_energy,places=5)
                            self.assertEqual(self.unit['energy'],100)
                            self.assertEqual(self.state['lifecycle'],clock)

    def test_central_main_road_crossing(self):
        """中央主干道 3.7 公里完整接入桥梁，不只桥头局部线段可用。"""
        for kind in ('ground','support'):
            for mode in ('move','fast_move'):
                with self.subTest(kind=kind,mode=mode):
                    self.unit.update(unit_type=kind,position=spatial.business((4500,5800)))
                    route = movement.plan(self.state,self.unit,spatial.business((8200,5800)),mode)
                    self.assertAlmostEqual(route['distance_m'],3700,places=5)
                    self.assertTrue(any(piece['terrain']=='bridge' for piece in route['segments']))
                    expected = (29.6 if kind=='ground' else 37)*(1.2 if mode=='fast_move' else 1)
                    self.assertAlmostEqual(route['estimated_energy'],expected,places=5)

    def test_piers_clear_but_river_outside_deck_blocks(self):
        for bridge in self.bridges:
            a,b = self.nodes[bridge['startId']],self.nodes[bridge['endId']]
            for kind in ('ground','support'):
                self.unit['unit_type'] = kind
                nav = movement.Navigator(self.state,self.unit,'move')
                with self.subTest(bridge=bridge['id'],kind=kind):
                    for fraction in (.2,.5,.8):
                        self.assertTrue(nav.clear((a[0]+(b[0]-a[0])*fraction,a[1])))
                    river = ((a[0]+b[0])/2,a[1]+100)
                    self.assertEqual(nav.terrain(river),'water')
                    self.assertFalse(nav.clear(river))
                    self.assertIsNone(nav.segment((a[0]-100,a[1]+100),(b[0]+100,b[1]+100)))

    def test_published_grid_matches_current_geometry(self):
        meta,_ = movement.navigation()
        geometry = MAP_PATH.with_name('map-geometry.json').read_bytes()
        self.assertEqual(meta['geometry_sha256'],hashlib.sha256(geometry).hexdigest())
        self.assertEqual(meta['conversion_policy'],'published-geometry-layered-bridges-v2')


if __name__=='__main__':
    main()
