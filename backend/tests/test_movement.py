"""以独立几何及手算预期检查兵种规则，不修改本机真实对局。"""

from copy import deepcopy
from unittest import TestCase, main
from unittest.mock import patch

from backend import movement, spatial
from backend.demo_domain import initial_state
from backend.runtime import ServiceError


class MovementTests(TestCase):
    """覆盖移动模式、完整路径、兵种差异及部署资源隔离。"""

    def setUp(self):
        """构造平直测试道路；预期数值独立取自规则文档。"""
        self.state=initial_state('test','test','patrol')
        self.state['manifest'].update(spatial_profile=spatial.profile(),map_source_sha256='fixture')
        self.state['camps']=[]; self.state['routes']=[]
        self.unit=next(unit for unit in self.state['units'] if unit['unit_id']=='R-G01')
        self.unit.update(position=spatial.business((200,500)),energy=100,durability=100,heading_degrees=90)
        self.state['units']=[self.unit]
        self.data=dict(width=1500,length=1500,heightColumns=2,heightRows=2,heights=[0,0,0,0],
            nodes=[dict(id='a',x=100,z=500),dict(id='b',x=1400,z=500)],
            roads=[dict(id='road',startId='a',endId='b',width=24,isBridge=False)])
        self.grid=bytearray([8])*90000
        self.meta=dict(source_sha256='fixture',grid_sha256='test',cell_m=5,columns=300,rows=300,
            terrain_names=['unknown','open','field','sparse_forest','dense_forest','water','obstacle','mud','road','bridge','gravel'],no_fly_polygons=[])
        self.addCleanup(patch.stopall)
        patch('backend.movement.navigation',return_value=(self.meta,self.grid)).start()
        patch('backend.spatial.map_data',return_value=self.data).start()

    def route(self,mode='move',target=(1200,500)):
        """创建默认一公里直线路线。"""
        return movement.plan(self.state,self.unit,spatial.business(target),mode)

    def test_ground_normal_distance_and_energy(self):
        """平路一公里普通移动需要三步，累计消耗八点能源。"""
        route=self.route()
        for expected in (600,1000,1200):
            movement.advance_unit(self.state,self.unit,route)
            self.assertAlmostEqual(spatial.meters(self.unit['position'])[0],expected,5)
        self.assertEqual(route['status'],'arrived')
        self.assertAlmostEqual(self.unit['energy'],92,5)

    def test_fast_move_distance_and_energy(self):
        """快速移动不靠动画提速，两步一公里，消耗九点六能源。"""
        route=self.route('fast_move')
        for _ in range(2): movement.advance_unit(self.state,self.unit,route)
        self.assertEqual(route['status'],'arrived')
        self.assertAlmostEqual(self.unit['energy'],90.4,5)

    def test_deployment_clock_and_real_resources_unchanged(self):
        """试走只改变位置、试走步和试走能源。"""
        clock=deepcopy(self.state['lifecycle'])
        route=self.route()
        movement.advance_unit(self.state,self.unit,route,deployment=True)
        self.assertEqual(self.state['lifecycle'],clock)
        self.assertEqual(self.unit['energy'],100)
        self.assertAlmostEqual(self.unit['movement_test_energy'],96.8,5)
        self.assertEqual(self.unit['movement_test_steps'],1)

    def test_ground_and_support_forest_differ(self):
        """同一疏林区域，履带可通过，轮式保障拒绝。"""
        self.grid[:]=bytearray([3])*len(self.grid)
        nav=movement.Navigator(self.state,self.unit,'move')
        self.assertIsNotNone(nav.segment((200,500),(230,500)))
        self.unit['unit_type']='support'
        self.assertFalse(movement.Navigator(self.state,self.unit,'move').clear((230,500)))

    def test_ground_water_building_and_dense_forest_block(self):
        """水域、建筑、密林和泥地不能用大代价伪装可通过。"""
        for value in (4,5,6,7):
            self.grid[:]=bytearray([value])*len(self.grid)
            self.assertFalse(movement.Navigator(self.state,self.unit,'move').clear((200,500)))

    def test_circular_clearance_respects_unit_radius(self):
        """同一斜角禁行格按真实圆形占地判定，不扩大成方形阻挡。"""
        self.grid[101*300+41]=5
        point=(202.5,502.5)
        self.assertTrue(movement.Navigator(self.state,self.unit,'move').clear(point))
        self.unit['unit_type']='support'
        self.assertFalse(movement.Navigator(self.state,self.unit,'move').clear(point))

    def test_mid_segment_checks_water(self):
        """起终点合法也不能直接穿过中间河道。"""
        for row in range(300): self.grid[row*300+100:row*300+104]=bytes([5])*4
        self.assertIsNone(movement.Navigator(self.state,self.unit,'move').segment((200,500),(800,500)))

    def test_search_cost_matches_confirmed_route_cost(self):
        """搜索边权与最终逐段证据的预计耗时保持一致。"""
        points=[(200,500),(700,500),(1200,500)]
        for mode in ('move','fast_move','reverse_move'):
            navigator=movement.Navigator(self.state,self.unit,mode)
            pieces=navigator.segments(points)
            self.assertIsNotNone(pieces)
            expected=sum(piece['length_m']/piece['speed_m_per_step'] for piece in pieces)
            self.assertAlmostEqual(navigator.path_cost(points),expected,8)
        self.grid[100*300+100]=5
        navigator=movement.Navigator(self.state,self.unit,'move')
        self.assertIsNone(navigator.path_cost(points))

    def test_slope_limits(self):
        """坡度比例不是角度，分别验证轮式与履带阈值。"""
        for kind,grade,allowed in [('ground',.35,True),('ground',.36,False),('support',.20,True),('support',.21,False)]:
            self.unit['unit_type']=kind
            with patch('backend.spatial.height',side_effect=lambda data,point:point[0]*grade):
                result=movement.Navigator(self.state,self.unit,'move').segment((200,500),(210,500))
            self.assertEqual(result is not None,allowed)

    def test_air_crosses_water_but_not_no_fly_zone(self):
        """空中不继承地面水域限制，禁飞区域独立生效。"""
        self.unit['unit_type']='recon'; self.grid[:]=bytes([5])*len(self.grid)
        self.assertIsNotNone(movement.Navigator(self.state,self.unit,'move').segment((200,500),(800,500)))
        self.meta['no_fly_polygons']=[[[400,400],[600,400],[600,600],[400,600]]]
        self.assertIsNone(movement.Navigator(self.state,self.unit,'move').segment((200,500),(800,500)))

    def test_reverse_limits_and_preserves_heading(self):
        """倒车限长、限方向，并在推进后保留车头。"""
        route=self.route('reverse_move',(100,500))
        movement.advance_unit(self.state,self.unit,route)
        self.assertEqual(self.unit['heading_degrees'],90)
        for point in ((300,500),(0,500)):
            with self.assertRaises(ServiceError): self.route('reverse_move',point)
        self.unit['unit_type']='recon'
        with self.assertRaises(ServiceError): self.route('reverse_move',(90,500))

    def test_stop_and_invalid_reorder_preserve_position(self):
        """无效改令保留原路线，停止不回滚位置或消耗。"""
        self.state['routes']=[self.route()]
        before=deepcopy(self.unit['position'])
        with self.assertRaises(ServiceError): movement.order(self.state,'red',dict(unit_id='R-G01',mode='move',position=spatial.business((-10,500))))
        self.assertEqual(len(self.state['routes']),1)
        movement.order(self.state,'red',dict(unit_id='R-G01',mode='stop_move'))
        self.assertEqual(self.state['routes'],[])
        self.assertEqual(self.unit['position'],before)

    def test_test_energy_reset_does_not_create_real_energy(self):
        """重置仅在部署阶段恢复独立试走资源。"""
        self.unit.update(energy=42,movement_test_energy=3,movement_test_steps=5)
        movement.order(self.state,'red',dict(unit_id='R-G01',mode='reset_test_energy'))
        self.assertEqual(self.unit['energy'],42)
        self.assertEqual(self.unit['movement_test_energy'],42)
        self.state['lifecycle']['phase']='paused'
        with self.assertRaises(ServiceError): movement.order(self.state,'red',dict(unit_id='R-G01',mode='reset_test_energy'))

    def test_ownership_and_confirmation(self):
        """其他阵营及待确认阶段不能绕过统一移动入口。"""
        with self.assertRaises(ServiceError): movement.order(self.state,'blue',dict(unit_id='R-G01',mode='stop_move'))
        self.state['lifecycle']['phase']='awaiting_confirmation'
        with self.assertRaises(ServiceError): movement.order(self.state,'red',dict(unit_id='R-G01',mode='stop_move'))

    def test_zero_durability_and_insufficient_energy(self):
        """失效或能源不足时不产生错误位移。"""
        route=self.route(); before=deepcopy(self.unit['position']); self.unit['durability']=0
        movement.advance_unit(self.state,self.unit,route)
        self.assertEqual(self.unit['position'],before)
        self.unit['durability']=100; self.unit['energy']=.01
        with self.assertRaises(ServiceError): self.route()

    def test_restore_continues_same_result(self):
        """保存路段游标后重放得到同一位置及能源。"""
        route=self.route(); movement.advance_unit(self.state,self.unit,route)
        state_copy,unit_copy,route_copy=deepcopy(self.state),deepcopy(self.unit),deepcopy(route)
        movement.advance_unit(self.state,self.unit,route)
        movement.advance_unit(state_copy,unit_copy,route_copy)
        self.assertEqual(unit_copy,self.unit)
        self.assertEqual(route_copy,route)

    def test_first_order_leaves_camp_and_reorder_uses_current_position(self):
        """首令从兵营经出口驶出并立即移动，改令不重返兵营。"""
        self.unit['camp_id']='red-ground'
        self.state['camps']=[dict(camp_id='red-ground',position=spatial.business((200,500)),half_extent_m=30)]
        self.unit['position']=spatial.business((900,800))
        self.assertTrue(movement.is_garrisoned(self.state,self.unit))
        movement.order(self.state,'red',dict(unit_id=self.unit['unit_id'],mode='move',position=spatial.business((1200,500))))
        active=self.state['routes'][0]
        self.assertAlmostEqual(active['points'][0][0],200)
        self.assertAlmostEqual(active['points'][1][1],540,5)
        self.assertEqual(self.unit['movement_test_steps'],1)
        self.assertFalse(movement.is_garrisoned(self.state,self.unit))
        current=spatial.meters(self.unit['position'])
        movement.order(self.state,'red',dict(unit_id=self.unit['unit_id'],mode='move',position=spatial.business((1300,500))))
        self.assertAlmostEqual(self.state['routes'][0]['points'][0][0],current[0],5)

    def test_invalid_departure_preserves_garrison(self):
        """非法目标拒绝后，待命单位仍留在兵营且不扣任何能源。"""
        self.unit['camp_id']='red-ground'
        self.state['camps']=[dict(camp_id='red-ground',position=spatial.business((200,500)),half_extent_m=30)]
        before=deepcopy(self.unit)
        with self.assertRaises(ServiceError):
            movement.order(self.state,'red',dict(unit_id=self.unit['unit_id'],mode='move',position=spatial.business((-10,500))))
        self.assertEqual(self.unit,before)
        self.assertTrue(movement.is_garrisoned(self.state,self.unit))


if __name__=='__main__': main()
