"""统一执行链测试。地图为显式合成夹具，不能用于生产地图验收/用户库。"""
from copy import deepcopy
from fractions import Fraction
from unittest import TestCase

from pydantic import ValidationError

from backend import battle_kernel as k, manual_battle, battle_replay
from backend.battle_contracts import BattleTaskIntent
from backend.battle_execution import Executor, Geometry, Motion
from backend.demo_domain import require
from backend.runtime import ServiceError, sha256
from backend.tests.test_battle_kernel import fixture, unit, points, spend


def state_fixture():
    state = fixture()
    state['manifest'] = dict(battle_mode='manual', battle_rule_sha256='1' * 64, battle_scenario_sha256='2' * 64)
    state['observations'] = {s: dict(contacts=[deepcopy(u) for u in state['units'] if u['side'] != s]) for s in k.SIDES}
    state['battle_points'] = points()
    manual_battle.initialize(state)
    return state


class FixtureGeometry(Geometry):
    """仅供此文件测试的全平合成面，不加载或声称验收任何青岚地图资产。"""
    def __init__(self):
        self.failed = False
        self.observed = True
        self.clear = True
        self.step_calls = 0
        self.fail_at = None
        self.presence = {}
        self.residents = {}
        self.mover = None

    def verify(self, state):
        require(not self.failed and state['manifest']['battle_scenario_sha256'] == '2' * 64,
                'BATTLE_MAP_NOT_ACCEPTED', '测试地图版本不符或验收未通过', 503)

    def position(self, point):
        return dict(x_mm=int(Fraction(point['x']) * 100000), z_mm=int(Fraction(point['y']) * 150000), height_mm=0)

    def display_position(self, point):
        return dict(x=format(point['x_mm'] / 100000, '.9f'), y=format(point['z_mm'] / 150000, '.9f'))

    def motions(self, state, **kwargs):
        self.step_calls += 1
        if self.step_calls == self.fail_at:
            require(False, 'FIXTURE_FAILURE', '模拟第若干步地图失败')
        result = {u['unit_id']: Motion(trace=(deepcopy(u['position_mm']),)) for u in state['units'] if k.alive(u)}
        if self.mover:
            return self.mover(state, result, kwargs)
        return result

    def observe(self, state, **kwargs):
        return {s: {u['unit_id'] for u in state['units'] if k.alive(u) and u['side'] != s}
                if self.observed else set() for s in k.SIDES}

    def capture(self, state, before, motions):
        return deepcopy(self.presence), deepcopy(self.residents)

    def camps(self, state):
        return [dict(camp_id='blue-camp', side='blue', position_mm=deepcopy(unit(state)['position_mm']))]

    def fire_clear(self, source, target):
        return self.clear

    def service_clear(self, source, target):
        return self.clear

    def flight_legal(self, unit, action):
        return self.clear, self.clear

    def landing_clear(self, unit, state):
        return self.clear

    def flight_position(self, unit):
        return dict(unit['position_mm'], height_mm=30000 if unit['air_state'] == 'flying' else 0)


def order(state, kind='ground', side='blue', **channels):
    u = unit(state, kind, side)
    return dict(unit_id=u['unit_id'], life_id=u['life_id'], **channels)


def lock(state, blue=(), red=()):
    for side, items in zip(k.SIDES, (blue, red)):
        if items:
            state = manual_battle.draft(state, side, dict(round_number=state['manual_battle']['round_number'], intents=list(items)))
        state = manual_battle.submit(state, side, dict(round_number=state['manual_battle']['round_number']))
    return state


def attack(state, side='blue'):
    target = unit(state, side='red' if side == 'blue' else 'blue')
    return order(state, side=side, task=dict(action='attack', target_id=target['unit_id'], target_life_id=target['life_id']))


def resupply(state, *, kind='ground', resource='energy', **amount):
    target = unit(state, kind)
    return order(state, 'support', task=dict(action='resupply', target_id=target['unit_id'],
        target_life_id=target['life_id'], resource=resource, **(amount or dict(quantity='10'))))


class ExecutionTests(TestCase):
    def setUp(self):
        self.geometry = FixtureGeometry()
        self.engine = Executor(self.geometry)
        self.state = state_fixture()

    def settle(self, state):
        return manual_battle.settle(state, 'host', dict(round_number=state['manual_battle']['round_number']), self.engine)

    def test_requires_complete_geometry_no_implicit_legacy_fallback(self):
        with self.assertRaises(ServiceError):
            Executor(None)
        self.geometry.failed = True
        before = deepcopy(self.state)
        self.assertFalse(self.engine.supports(self.state))
        with self.assertRaises(ServiceError):
            self.settle(lock(self.state))
        self.assertEqual(self.state, before)

    def test_full_sixty_seconds_twelve_frames_and_input_unchanged(self):
        state = lock(self.state); before = deepcopy(state)
        after, record = self.settle(state)
        self.assertEqual(state, before)
        self.assertEqual([f['at_seconds'] for f in record['frames']], list(range(5, 61, 5)))
        self.assertEqual(after['manual_battle']['completed_rounds'], 1)
        self.assertEqual(after['battle_ledger']['elapsed_seconds'], 60)
        self.assertEqual(len(after['battle_round_facts']), 12)
        self.assertEqual(record['initial_world'], self.engine.project_world(state))
        self.assertEqual(record['frames'][-1]['world'], self.engine.project_world(after))
        battle_replay.validate_record(record)

    def test_two_real_rounds_form_world_and_hash_chain(self):
        after, first = self.settle(lock(self.state))
        final, second = self.settle(lock(after))
        self.assertEqual(second['previous_round_sha256'], first['payload_sha256'])
        self.assertEqual(second['initial_world'], first['frames'][-1]['world'])
        self.assertEqual(final['manual_battle']['elapsed_seconds'], 120)
        self.assertEqual(len(final['battle_round_facts']), 12)

    def test_replay_read_and_repeat_do_not_execute_or_change_world(self):
        after, first = self.settle(lock(self.state))
        calls = self.geometry.step_calls; before = deepcopy(after)
        for _ in range(3):
            cursor = battle_replay.ReplayCursor([first])
            cursor.seek(60)
            battle_replay.validate_record(first)
        self.assertEqual(self.geometry.step_calls, calls)
        self.assertEqual(before, after)

    def test_late_failure_discards_earlier_eleven_steps(self):
        state = lock(self.state, [attack(self.state)]); before = deepcopy(state)
        self.geometry.fail_at = 12
        with self.assertRaises(ServiceError):
            self.settle(state)
        self.assertEqual(state, before)

    def test_sixty_round_deadline_is_terminal_without_seventy_first(self):
        state = self.state
        for _ in range(60):
            state, record = self.settle(lock(state))
        self.assertEqual(state['manual_battle']['completed_rounds'], 60)
        self.assertEqual(state['battle_ledger']['elapsed_seconds'], 3600)
        self.assertEqual(state['battle_result'], dict(winner='draw', reason='time_limit'))
        self.assertTrue(record['finished'])
        self.assertFalse(self.engine.supports(state))
        with self.assertRaises(ServiceError):
            self.settle(state)

    def test_sync_mutual_kill_and_early_terminal_real_five_second_record(self):
        # 只保留双方坦克，重新初始化满值名册后设置合法受损耐久。
        source = fixture()
        source['units'] = [u for u in source['units'] if u['unit_type'] == 'ground']
        source.pop('battle_ledger')
        source = k.initialize(source)
        source.update(manifest=deepcopy(self.state['manifest']), observations=deepcopy(self.state['observations']), battle_points=points())
        manual_battle.initialize(source)
        for u in source['units']:
            u['durability'] = '16'
        after, record = self.settle(lock(source, [attack(source)], [attack(source, 'red')]))
        self.assertTrue(record['finished'])
        self.assertEqual(record['end_seconds'], 5)
        self.assertEqual(len([e for e in record['frames'][0]['events'] if e['kind'] == 'shot']), 2)
        self.assertEqual(after['battle_result']['reason'], 'mutual_elimination')
        self.assertTrue(all(u['retired'] for u in after['units']))

    def test_first_fire_five_then_ten_second_cooldown_and_no_replay_deductions(self):
        after, record = self.settle(lock(self.state, [attack(self.state)]))
        shots = [e['at_seconds'] for f in record['frames'] for e in f['events'] if e['kind'] == 'shot']
        self.assertEqual(shots, [5, 15, 25, 35, 45, 55])
        self.assertEqual(unit(after)['ammo'], '54')
        self.assertEqual(unit(after, side='red')['durability'], '4')
        k.validate(after)

    def test_field_service_completed_quantity_persists_across_steps(self):
        spend(self.state, unit(self.state), 'energy', Fraction(20))
        after, record = self.settle(lock(self.state, [resupply(self.state, quantity='5')]))
        amounts = [e['amount'] for f in record['frames'] for e in f['events'] if e['kind'] == 'service']
        self.assertEqual(amounts, ['2', '2', '1'])
        self.assertEqual(unit(after)['energy'], '85')
        self.assertIsNone(unit(after, 'support')['task'])
        self.assertEqual(unit(after, 'support')['cargo']['energy'], '195')
        k.validate(after)

    def test_target_balance_completes_and_does_not_repeat_full_quantity_next_round(self):
        spend(self.state, unit(self.state), 'energy', Fraction(20))
        after, _ = self.settle(lock(self.state, [resupply(self.state, target_balance='83')]))
        self.assertEqual(unit(after)['energy'], '83')
        after, _ = self.settle(lock(after))
        self.assertEqual(unit(after)['energy'], '83')
        self.assertEqual(unit(after, 'support')['cargo']['energy'], '197')

    def test_receiving_intent_prevents_fire_even_when_service_surface_fails(self):
        spend(self.state, unit(self.state), 'energy', Fraction(20))
        self.geometry.clear = False
        after, _ = self.settle(lock(self.state, [attack(self.state), resupply(self.state)]))
        self.assertEqual(unit(after)['ammo'], '60')
        self.assertEqual(unit(after)['energy'], '80')

    def test_unified_field_and_barracks_roles_conflict_atomically(self):
        camp = order(self.state, 'support', task=dict(action='barracks', camp_id='blue-camp'))
        # 同一来源旧任务与本轮兵营任务不能同时采用；构造另一辆车向它提供服务。
        other = deepcopy(unit(self.state, 'support')); other.update(unit_id='blue-support-extra', life_id='extra-life')
        fresh = deepcopy(self.state)
        fresh['units'].append(other); fresh.pop('battle_ledger'); fresh.pop('manual_battle')
        fresh = k.initialize(fresh); manual_battle.initialize(fresh)
        request = resupply(fresh, kind='support')
        request.update(unit_id=other['unit_id'], life_id=other['life_id'])
        state = manual_battle.draft(fresh, 'blue', dict(round_number=1, intents=[camp, request]))
        before = deepcopy(state)
        with self.assertRaises(ServiceError) as error:
            manual_battle.submit(state, 'blue', dict(round_number=1))
        self.assertEqual(error.exception.code, 'BATTLE_SERVICE_CONFLICT')
        self.assertEqual(before, state)
        self.assertFalse(state['manual_battle']['submitted']['blue'])

    def test_source_barracks_and_outbound_service_old_task_conflict(self):
        target = unit(self.state)
        unit(self.state, 'support')['task'] = resupply(self.state)['task']
        target['task'] = dict(action='barracks', camp_id='blue-camp')
        with self.assertRaises(ServiceError) as error:
            self.settle(lock(self.state))
        self.assertEqual(error.exception.code, 'BATTLE_SERVICE_CONFLICT')

    def test_services_must_explicitly_stop_existing_movement(self):
        unit(self.state)['movement'] = dict(action='move', position=dict(x='20', y='20'))
        with self.assertRaises(ServiceError) as error:
            self.settle(lock(self.state, [resupply(self.state)]))
        self.assertEqual(error.exception.code, 'INTENT_CONFLICT')

    def test_new_energy_next_step_not_same_step_capture(self):
        spend(self.state, unit(self.state), 'energy', Fraction(100))
        i = unit(self.state)['unit_id']
        self.geometry.presence = {'P-01': {'blue': {i}}}
        self.geometry.residents = deepcopy(self.geometry.presence)
        state = lock(self.state, [resupply(self.state, quantity='2')])
        self.engine._adopt(state, {s: list(state['manual_battle']['drafts'][s].values()) for s in k.SIDES})
        first, _ = self.engine.step(state)
        self.assertEqual(first['battle_points'][0]['progress_steps'], 0)
        self.assertEqual(unit(first)['energy'], '2')
        second, _ = self.engine.step(first)
        self.assertEqual(second['battle_points'][0]['progress_steps'], 1)

    def test_suppression_frozen_before_lethal_damage_and_new_owner_income_next_step(self):
        self.state['battle_points'][0]['owner'] = 'blue'
        enemy = unit(self.state, side='red'); enemy['durability'] = '16'
        self.geometry.presence = {'P-01': {'red': {enemy['unit_id']}}}
        state = lock(self.state, [attack(self.state)])
        after, _ = self.settle(state)
        self.assertEqual(after['battle_ledger']['blue']['control_steps'], 11)
        self.assertTrue(unit(after, side='red')['retired'])

    def test_takeoff_full_step_height_and_landing_no_service_until_next_step(self):
        after, record = self.settle(lock(self.state, [order(self.state, 'recon', task=dict(action='takeoff'))]))
        self.assertEqual(unit(after, 'recon')['air_state'], 'flying')
        self.assertEqual(unit(after, 'recon')['position_mm']['height_mm'], 30000)
        self.assertEqual(unit(after, 'recon')['energy'], '97')
        self.assertEqual([e['kind'] for e in record['frames'][0]['events']], ['takeoff'])
        after, record = self.settle(lock(after, [order(after, 'recon', task=dict(action='land'))]))
        self.assertEqual(unit(after, 'recon')['air_state'], 'docked')
        self.assertEqual(unit(after, 'recon')['position_mm']['height_mm'], 0)
        self.assertEqual(unit(after, 'recon')['energy'], '575/6')

    def test_drone_power_failure_cancels_fire_candidate_without_enemy_kill_credit(self):
        drone = unit(self.state, 'recon', 'red'); drone['air_state'] = 'flying'
        spend(self.state, drone, 'energy', Fraction(599, 6))
        target = order(self.state, task=dict(action='attack', target_id=drone['unit_id'], target_life_id=drone['life_id']))
        after, record = self.settle(lock(self.state, [target]))
        self.assertTrue(unit(after, 'recon', 'red')['retired'])
        self.assertEqual(unit(after)['ammo'], '60')
        self.assertEqual(after['battle_ledger']['blue']['kills'], '0')
        self.assertEqual(record['frames'][0]['events'][0]['kind'], 'retired')

    def test_finite_barracks_saved_as_all_twelve_step_facts_and_locks_fire(self):
        spend(self.state, unit(self.state), 'energy', Fraction(60))
        u = unit(self.state); u['task'] = attack(self.state)['task']
        after, _ = self.settle(lock(self.state, [order(self.state, task=dict(action='barracks', camp_id='blue-camp', targets={'energy': '100'}))]))
        self.assertEqual(unit(after)['energy'], '100')
        self.assertEqual(after['battle_ledger']['blue']['reserve']['energy'], '1140')
        self.assertEqual([d['amount'] for f in after['battle_round_facts'] for d in f['barracks_deliveries']], ['50', '10'])
        self.assertEqual(unit(after)['ammo'], '60')

    def test_no_observation_no_fire_and_no_hidden_contacts_published(self):
        self.geometry.observed = False
        after, _ = self.settle(lock(self.state, [attack(self.state)]))
        self.assertEqual(unit(after)['ammo'], '60')
        self.assertEqual(after['observations']['blue']['contacts'], [])

    def test_fake_stationary_teleport_and_overbudget_motion_fail_closed(self):
        state = lock(self.state, [order(self.state, movement=dict(action='move', position=dict(x='20', y='20')))])
        def teleport(s, motions, kwargs):
            u = unit(s); motions[u['unit_id']] = Motion(trace=(deepcopy(u['position_mm']), dict(u['position_mm'], x_mm=2000000)))
            return motions
        self.geometry.mover = teleport
        with self.assertRaises(ServiceError):
            self.settle(state)
        def overbudget(s, motions, kwargs):
            u = unit(s); motions[u['unit_id']] = Motion(trace=(deepcopy(u['position_mm']), dict(u['position_mm'], x_mm=2000000)),
                segments=(dict(length_mm='1000000', grade='0', surface='road'),))
            return motions
        self.geometry.mover = overbudget
        with self.assertRaises(ServiceError):
            self.settle(state)

    def test_exact_movement_and_moving_fire_share_one_energy_budget(self):
        def move(s, motions, kwargs):
            u = unit(s)
            end = dict(u['position_mm'], x_mm=u['position_mm']['x_mm'] + 1000)
            motions[u['unit_id']] = Motion(trace=(deepcopy(u['position_mm']), end), segments=(dict(length_mm='1000', grade='0', surface='road'),))
            return motions
        self.geometry.mover = move
        item = attack(self.state); item['movement'] = dict(action='move', position=dict(x='20', y='20'))
        after, record = self.settle(lock(self.state, [item]))
        self.assertEqual(unit(after)['energy'], '24973/250')
        self.assertEqual(unit(after, side='red')['durability'], '212/5')
        self.assertEqual(unit(after)['position_mm']['x_mm'], 1012000)
        self.assertEqual(unit(after)['speed_m_per_minute'], '12')
        self.assertEqual(len([e for f in record['frames'] for e in f['events'] if e['kind'] == 'move']), 12)

    def test_time_mismatch_rejected_without_running_geometry(self):
        self.state['battle_ledger']['elapsed_seconds'] = 5
        self.assertFalse(self.engine.supports(self.state))
        self.assertEqual(self.geometry.step_calls, 0)


    def test_barracks_visual_events_match_only_actual_delivery_facts(self):
        spend(self.state, unit(self.state), 'energy', Fraction(60))
        after, record = self.settle(lock(self.state, [order(self.state,
            task=dict(action='barracks', camp_id='blue-camp', targets={'energy': '100'}))]))
        events = [event for frame in record['frames'] for event in frame['events'] if event['kind'] == 'barracks_service']
        facts = [delivery for frame in after['battle_round_facts'] for delivery in frame['barracks_deliveries']]
        self.assertEqual([e['amount'] for e in events], [f['amount'] for f in facts])
        self.assertEqual([e['amount'] for e in events], ['50', '10'])
        for event in events:
            self.assertEqual(event['camp_id'], 'blue-camp')
            self.assertEqual(event['delivery_category'], 'energy')
            self.assertEqual(event['target_life_id'], unit(self.state)['life_id'])
            self.assertNotIn('source_id', event)
            self.assertEqual(event['source_position'], self.geometry.camps(self.state)[0]['position_mm'])
        battle_replay.validate_record(record)
        before = deepcopy(after)
        battle_replay.ReplayCursor([record]).seek(60)
        self.assertEqual(after, before)

    def test_replay_attack_target_is_cleared_after_cease_fire_not_last_shot_forever(self):
        after, first = self.settle(lock(self.state, [attack(self.state)]))
        self.assertEqual(first['frames'][0]['world']['units'][0]['attack_target_id'], unit(self.state, side='red')['unit_id'])
        after, second = self.settle(lock(after, [order(after, task={'action': 'stop'})]))
        for frame in second['frames']:
            own = next(u for u in frame['world']['units'] if u['unit_id'] == unit(after)['unit_id'])
            self.assertIsNone(own['attack_target_id'])



class ServiceContractTests(TestCase):
    def test_explicit_positive_quantity_or_target_balance_and_integer_ammo(self):
        base = dict(action='resupply', target_id='u', target_life_id='l', resource='ammo')
        for fields in ({}, dict(quantity='1', target_balance='2'), dict(quantity='0'),
                       dict(quantity='1/2'), dict(quantity='NaN'), dict(quantity=True)):
            with self.subTest(fields=fields), self.assertRaises((ValidationError, ServiceError)):
                BattleTaskIntent.model_validate(base | fields)
        self.assertEqual(BattleTaskIntent.model_validate(base | dict(quantity='2')).quantity, '2')

    def test_camp_must_be_explicit_and_six_accounts_only(self):
        for fields in ({}, dict(camp_id='b', targets={}), dict(camp_id='b', targets={'money': '2'}),
                       dict(camp_id='b', targets={'ammo': '1/2'})):
            with self.subTest(fields=fields), self.assertRaises((ValidationError, ServiceError)):
                BattleTaskIntent.model_validate(dict(action='barracks') | fields)
        BattleTaskIntent.model_validate(dict(action='barracks', camp_id='b', targets={'energy': '50'}))

    def test_non_service_cannot_smuggle_amount_or_camp(self):
        for fields in (dict(quantity='1'), dict(target_balance='1'), dict(camp_id='b'), dict(targets={'energy': '50'})):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                BattleTaskIntent.model_validate(dict(action='stop') | fields)


class LedgerBoundaryTests(TestCase):
    def test_forged_denominator_is_not_accepted_even_if_conservation_is_forged_too(self):
        s = fixture(); s['battle_ledger']['blue']['initial']['energy'] = '1701'
        s['battle_ledger']['blue']['consumed']['energy'] = '1'
        with self.assertRaises(ServiceError):
            k.validate(s)

    def test_roster_life_and_value_denominator_cannot_change(self):
        for mutate in (lambda s: unit(s).update(life_id='forged-life'),
                       lambda s: s['battle_ledger']['blue'].update(initial_value='24'),
                       lambda s: unit(s).update(air_state='flying'),
                       lambda s: unit(s).update(last_damage_seconds=True)):
            s = fixture(); mutate(s)
            with self.assertRaises(ServiceError):
                k.validate(s)

