"""工作台3随机PG隔离集成；合成明确层，不等同真实桥头/生产地图验收。

使用已有随机库生命周期辅助方法，不继承/重复收集其整套测试。
"""
from unittest import TestCase

from fastapi.testclient import TestClient
from unittest.mock import patch

from backend.main import create_app
from backend.tests import test_manual_battle_db as db_fixture
from backend.tests.test_battle_execution import order
from backend.tests.test_battle_kernel import unit
from backend.tests.test_manual_geometry_persistence import PlannedFixtureGeometry, two_layers


class ManualSurfacePersistenceDatabaseTests(TestCase):
    setUpClass = classmethod(db_fixture.ManualBattleDatabaseTests.setUpClass.__func__)
    cleanup_database = classmethod(db_fixture.ManualBattleDatabaseTests.cleanup_database.__func__)
    setUp = db_fixture.ManualBattleDatabaseTests.setUp
    make_game = db_fixture.ManualBattleDatabaseTests.make_game
    view = db_fixture.ManualBattleDatabaseTests.view
    command = db_fixture.ManualBattleDatabaseTests.command
    state = db_fixture.ManualBattleDatabaseTests.state
    execution_game = db_fixture.ManualBattleDatabaseTests.execution_game

    def restart_http_service(self, geometry):
        """新建真实HTTP应用；仅替换app.state不会改变routes捕获的service。"""
        from backend.battle_execution import Executor
        previous_client = self.client
        self.client = TestClient(create_app(self.settings))
        self.addCleanup(setattr, self, 'client', previous_client)
        self.addCleanup(self.client.close)
        service = self.client.app.state.service
        self.assertIsNot(service, previous_client.app.state.service)
        service.battle_executor = Executor(geometry)
        # 验证请求确实经由新service，而非旧routes闭包继续执行。
        with patch.object(service, 'view', wraps=service.view) as routed_view:
            self.assertTrue(self.view()['manual_battle']['engine_ready'])
            routed_view.assert_called_once()
        return service

    def test_completed_surface_persists_to_snapshot_new_service_and_next_round(self):
        def configure(state):
            for u in state['units']:
                u['surface_id'] = 'ground'
        self.execution_game(lock=False, configure=configure)
        geometry = PlannedFixtureGeometry(two_layers(), [
            dict(surface_id='ground', end_m=[1001, 1000]),
            dict(surface_id='fixture-other', end_m=[1002, 1000])])
        self.client.app.state.service.battle_executor.geometry = geometry
        self.submit_move_and_resolve(1)
        saved = self.state()
        u = unit(saved)
        self.assertEqual(u['surface_id'], 'fixture-other')
        self.assertNotIn('movement_progress', u)
        self.assertEqual(u['position_mm']['x_mm'], 1002000)
        restored_service = self.restart_http_service(geometry)
        with self.settings.connect() as connection:
            restored = restored_service._game(connection, self.game_id)['state_payload']
        self.assertEqual(unit(restored), u)
        geometry.route = [dict(surface_id='fixture-other', end_m=[1003, 1000])]
        seen_before = len(geometry.seen)
        self.submit_move_and_resolve(2)
        self.assertEqual(geometry.seen[seen_before]['surface_id'], 'fixture-other')
        self.assertEqual(unit(self.state())['position_mm']['x_mm'], 1003000)

    def submit_move_and_resolve(self, number):
        item = order(self.state(), movement=dict(action='move', position=dict(x='12', y='10')))
        response = self.command('battle_draft', 'blue', dict(round_number=number, intents=[item]))
        self.assertEqual(response.status_code, 200, response.text)
        for role in ('blue', 'red'):
            response = self.command('battle_submit', role, dict(round_number=number))
            self.assertEqual(response.status_code, 200, response.text)
        response = self.command('battle_resolve', payload=dict(round_number=number))
        self.assertEqual(response.status_code, 200, response.text)

    def test_completed_route_clears_remainder_before_pg_restore_and_new_round(self):
        from backend.tests.test_manual_geometry_persistence import assert_grid_origin
        def configure(state):
            for u in state['units']:
                u['surface_id'] = 'ground'
        self.execution_game(lock=False, configure=configure)
        geometry = PlannedFixtureGeometry(two_layers(), [dict(surface_id='ground', end_m=[1100, 1000])])
        self.client.app.state.service.battle_executor.geometry = geometry
        self.submit_move_and_resolve(1)
        saved = self.state()
        self.assertNotIn('movement_progress', unit(saved))
        self.assertIsNone(unit(saved)['movement'])
        assert_grid_origin(self, unit(saved))
        restored_service = self.restart_http_service(geometry)
        with self.settings.connect() as connection:
            restored = restored_service._game(connection, self.game_id)['state_payload']
        self.assertEqual(unit(restored), unit(saved))
        assert_grid_origin(self, unit(restored))
        geometry.route = [dict(surface_id='ground', end_m=[1200, 1000])]
        seen_before = len(geometry.seen)
        self.submit_move_and_resolve(2)
        self.assertEqual(geometry.seen[seen_before]['position_mm']['x_mm'], 1100000)
        assert_grid_origin(self, geometry.seen[seen_before])
        self.assertEqual(unit(self.state())['position_mm']['x_mm'], 1200000)

    def test_uninterrupted_route_pg_restore_and_next_round_match_reference_budget(self):
        from copy import deepcopy
        from backend.battle_execution import Executor
        def configure(state):
            for u in state['units']:
                u['surface_id'] = 'ground'
        self.execution_game(lock=False, configure=configure)
        route = [dict(surface_id='ground', end_m=[5000, 3000])]
        geometry = PlannedFixtureGeometry(two_layers(), route)
        self.client.app.state.service.battle_executor.geometry = geometry
        reference = deepcopy(self.state())
        reference_unit = unit(reference)
        reference_unit['movement'] = dict(action='move', position=dict(x='12', y='10'))
        reference_executor = Executor(PlannedFixtureGeometry(two_layers(), route))
        self.submit_move_and_resolve(1)
        saved = self.state()
        self.assertIsNotNone(unit(saved)['movement'])
        self.assertTrue(unit(saved).get('movement_progress'))
        restored_service = self.restart_http_service(geometry)
        with self.settings.connect() as connection:
            restored = restored_service._game(connection, self.game_id)['state_payload']
        self.assertEqual(unit(restored), unit(saved))
        # 服务重建后继续已提交路线：第二轮不重发movement，避免把取消伪装连续。
        for role in ('blue', 'red'):
            response = self.command('battle_submit', role, dict(round_number=2))
            self.assertEqual(response.status_code, 200, response.text)
        response = self.command('battle_resolve', payload=dict(round_number=2))
        self.assertEqual(response.status_code, 200, response.text)
        for _ in range(24):
            reference, _ = reference_executor.step(reference)
        actual = self.state()
        self.assertEqual(unit(actual)['position_mm'], unit(reference)['position_mm'])
        self.assertEqual(unit(actual)['energy'], unit(reference)['energy'])
        self.assertEqual(actual['battle_ledger'], reference['battle_ledger'])
        self.assertEqual(actual['battle_ledger']['elapsed_seconds'], 120)

    def test_diagonal_air_pg_restore_matches_maintenance_and_recorded_cost(self):
        from copy import deepcopy
        from fractions import Fraction as F
        from backend.battle_execution import Executor
        from backend.tests.test_manual_geometry_persistence import CoordinatedFixtureGeometry
        def configure(state):
            for u in state['units']:
                u['surface_id'] = 'ground'
            unit(state, 'recon')['air_state'] = 'flying'
            unit(state, 'recon')['position_mm']['height_mm'] = 30000
        self.execution_game(lock=False, configure=configure)
        route = [dict(surface_id='ground', end_m=[5000, 3000])]
        self.client.app.state.service.battle_executor.geometry = CoordinatedFixtureGeometry('recon', route)
        reference = deepcopy(self.state())
        unit(reference, 'recon')['movement'] = dict(action='move', position=dict(x='50', y='20'))
        initial_energy = F(unit(reference, 'recon')['energy'])
        reference_executor = Executor(CoordinatedFixtureGeometry('recon', route))
        item = order(self.state(), kind='recon', movement=dict(action='move', position=dict(x='50', y='20')))
        response = self.command('battle_draft', 'blue', dict(round_number=1, intents=[item]))
        self.assertEqual(response.status_code, 200, response.text)
        for number in (1, 2):
            for role in ('blue', 'red'):
                response = self.command('battle_submit', role, dict(round_number=number))
                self.assertEqual(response.status_code, 200, response.text)
            response = self.command('battle_resolve', payload=dict(round_number=number))
            self.assertEqual(response.status_code, 200, response.text)
            if number == 1:
                saved = unit(self.state(), 'recon')
                self.assertEqual(saved['movement_progress']['recorded_distance_mm'], 800000)
                self.assertEqual(saved['movement_progress']['path_pending_segments'], [])
                self.assertTrue(any((F(v)*1000).denominator != 1
                                    for v in saved['movement_progress']['exact_position_m']))
                restored_service = self.restart_http_service(CoordinatedFixtureGeometry('recon', route))
                with self.settings.connect() as connection:
                    restored = restored_service._game(connection, self.game_id)['state_payload']
                self.assertEqual(unit(restored, 'recon'), saved)
        for _ in range(24):
            reference, _ = reference_executor.step(reference)
        actual = self.state()
        self.assertEqual(unit(actual, 'recon')['position_mm'], unit(reference, 'recon')['position_mm'])
        self.assertEqual(unit(actual, 'recon')['energy'], unit(reference, 'recon')['energy'])
        self.assertEqual(actual['battle_ledger'], reference['battle_ledger'])
        self.assertEqual(unit(actual, 'recon')['movement_progress']['recorded_distance_mm'], 1600000)
        # 总册独立数值：1.6km×5 + 2分钟×2，兼含移动账本和飞行维持。
        self.assertEqual(initial_energy-F(unit(actual, 'recon')['energy']), F(12))

    def test_hover_energy_persists_as_read_only_two_round_replay(self):
        """存取真实PG及HTTP；地图仍为合成夹具，绝不当成真实部署结果。"""
        import json
        import os
        from fractions import Fraction as F
        from pathlib import Path
        from backend import battle_kernel as k, battle_replay
        from backend.tests.test_battle_hover_energy import HoverGeometryAndRoundTests, flying_state

        policy_key = 'field_energy_service_policy'
        self.assertNotIn(policy_key, self.view('blue')['manual_battle'])
        fixture, _, _ = flying_state(distance_mm=100000)
        def configure(state):
            for key in ('units', 'battle_ledger'):
                state[key] = fixture[key]
            state['manifest'][policy_key] = k.FIELD_ENERGY_SERVICE_POLICY

        self.execution_game(lock=False, configure=configure)
        geometry, _ = HoverGeometryAndRoundTests().geometry()
        self.client.app.state.service.battle_executor.geometry = geometry
        view = self.view('blue')
        self.assertEqual(view['manual_battle'][policy_key], k.FIELD_ENERGY_SERVICE_POLICY)
        source, target = unit(self.state(), 'support'), unit(self.state(), 'recon')
        self.assertFalse(next(u for u in view['units'] if u['unit_id'] == target['unit_id'])['docked'])

        for number in (1, 2):
            if number == 2:
                item = HoverGeometryAndRoundTests.command(self.state(), quantity='5/2')
                response = self.command('battle_draft', 'blue', dict(round_number=number, intents=[item]))
                self.assertEqual(response.status_code, 200, response.text)
            for role in ('blue', 'red'):
                response = self.command('battle_submit', role, dict(round_number=number))
                self.assertEqual(response.status_code, 200, response.text)
            response = self.command('battle_resolve', payload=dict(round_number=number))
            self.assertEqual(response.status_code, 200, response.text)

        saved = self.state()
        counts = db_fixture.ManualBattleDatabaseTests.execution_counts(self)
        reloaded_geometry, _ = HoverGeometryAndRoundTests().geometry()
        self.restart_http_service(reloaded_geometry)
        path = self.path + '/battle/rounds'
        index = self.client.get(path, headers=self.headers['host'])
        self.assertEqual(index.status_code, 200, index.text)
        self.assertEqual([item['round_number'] for item in index.json()['items']], [1, 2])
        records = []
        for number in (1, 2):
            response = self.client.get(f'{path}/{number}', headers=self.headers['host'])
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertIs(body['read_only'], True)
            self.assertIs(body['writes_game_state'], False)
            records.append(body['record'])
        self.assertEqual(records[0]['frames'][-1]['world'], records[1]['initial_world'])
        self.assertEqual(records[0]['payload_sha256'], records[1]['previous_round_sha256'])
        for record in records:
            battle_replay.validate_record(record)
        facts = [e for r in records for f in r['frames'] for e in f['events'] if e['kind'] == 'service']
        self.assertEqual([(e['at_seconds'], e['resource'], e['amount']) for e in facts],
                         [(65, 'energy', '2'), (70, 'energy', '1/2')])
        for fact in facts:
            self.assertEqual(fact['source_position'], source['position_mm'])
            self.assertEqual(fact['target_position'], target['position_mm'])
            self.assertEqual(fact['source_life_id'], source['life_id'])
            self.assertEqual(fact['target_life_id'], target['life_id'])
        self.assertEqual(F(unit(saved, 'support')['cargo']['energy']), F(source['cargo']['energy']) - F(5, 2))
        self.assertEqual(F(unit(saved, 'recon')['energy']), F(target['energy']) + F(5, 2) - 4)
        self.assertEqual(unit(saved, 'recon')['air_state'], 'flying')
        k.validate(saved)
        for role in ('blue', 'red'):
            self.assertEqual(self.client.get(f'{path}/2', headers=self.headers[role]).status_code, 403)
        self.assertEqual(saved, self.state())
        self.assertEqual(counts, db_fixture.ManualBattleDatabaseTests.execution_counts(self))
        self.assertEqual(reloaded_geometry.step_calls, 0)
        if os.environ.get('QIONGTU_EXPORT_ISOLATED_HOVER_REPLAY') == '1':
            directory = Path(__file__).resolve().parents[2] / '.local/workbench3-20261117/airborne-replay'
            directory.mkdir(parents=True, exist_ok=True)
            for name, items in (('pg-hover-two-rounds.json', records), ('pg-hover-single-round.json', records[1:])):
                (directory / name).write_text(json.dumps(dict(records=items), ensure_ascii=False, indent=2) + '\n')
