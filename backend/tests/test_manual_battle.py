"""独立新模式提交屏障：纯内存，不改用户游戏，不调用旧引擎。"""
from copy import deepcopy
from unittest import TestCase

from pydantic import ValidationError

from backend import manual_battle
from backend.battle_contracts import BattleDraftRequest, BattleUnitIntent, BattleTaskIntent
from backend.battle_replay import seal_record
from backend.demo_domain import initial_state
from backend.runtime import ServiceError


def state_fixture():
    state = initial_state('manual-scenario-fixture', 'manual-rule-fixture', 'patrol')
    state['manifest'].update(battle_mode='manual', battle_scenario_sha256='a' * 64, battle_rule_sha256='b' * 64)
    state['units'] = [dict(unit_id=side + '-' + kind, life_id=side + '-' + kind + '-life',
        side=side, unit_type=kind, durability=100, energy=100, position=dict(x='10', y='10'))
        for side in ('red', 'blue') for kind in ('ground', 'recon', 'support')]
    for side in ('red', 'blue'):
        state['observations'][side]['contacts'] = [deepcopy(unit) for unit in state['units'] if unit['side'] != side]
    manual_battle.initialize(state)
    return state


def intent(side='blue', kind='ground', task='stop'):
    return dict(unit_id=side + '-' + kind, life_id=side + '-' + kind + '-life', task=dict(action=task))


def world(seconds):
    return dict(elapsed_seconds=seconds, units=[], capture_points=[dict(point_id='P-%02d' % i,
        owner='neutral', advancing_side='none', progress_steps=0, contested=False) for i in range(1, 6)],
        blue_score='0', red_score='0')


class FixtureExecutor:
    """仅测试协议的假执行器；不提供任何战斗裁决，绝不接入服务。"""
    def __init__(self, *, duration=60, finished=False, wrong_world=False):
        self.duration, self.finished, self.wrong_world = duration, finished, wrong_world
        self.calls = 0

    def project_world(self, state):
        return world(state.get('fixture_seconds', 0))

    def __call__(self, state, intents):
        self.calls += 1
        info = state['manual_battle']
        start = info['elapsed_seconds']
        before = self.project_world(state)
        frames = [dict(at_seconds=t, world=world(t), events=[]) for t in range(start + 5, start + self.duration + 1, 5)]
        record = seal_record(round_number=info['round_number'], rule_sha256=state['manifest']['battle_rule_sha256'],
            scenario_sha256=state['manifest']['battle_scenario_sha256'], previous_round_sha256=info['previous_round_sha256'],
            initial_world=before, frames=frames, finished=self.finished)
        state['fixture_seconds'] = start + self.duration + (5 if self.wrong_world else 0)
        return state, record


class ManualBattleTests(TestCase):
    def assert_code(self, code, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as error:
            fn(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_independent_new_game_only_and_frozen_mode(self):
        state = initial_state('legacy', 'legacy', 'patrol')
        self.assert_code('BATTLE_VERSION_REQUIRED', manual_battle.initialize, state)
        new = state_fixture()
        self.assert_code('MODE_FROZEN', manual_battle.initialize, new)
        manual_battle.validate(new)

    def test_draft_is_intent_only_and_does_not_change_original(self):
        state = state_fixture(); before = deepcopy(state)
        result = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[intent()]))
        self.assertEqual(state, before)
        self.assertEqual(result['units'], before['units'])
        self.assertEqual(result['lifecycle'], before['lifecycle'])
        self.assertEqual(set(result['manual_battle']['drafts']['blue']), {'blue-ground'})

    def test_both_channels_allow_moving_fire_and_independent_edits(self):
        state = state_fixture()
        attack = intent(task='attack'); attack['task'].update(target_id='red-ground', target_life_id='red-ground-life')
        state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[attack]))
        move = dict(unit_id='blue-ground', life_id='blue-ground-life', movement=dict(action='move', position=dict(x='20', y='20')))
        state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[move]))
        entry = state['manual_battle']['drafts']['blue']['blue-ground']
        self.assertEqual(entry['task']['action'], 'attack')
        self.assertEqual(entry['movement']['action'], 'move')

    def test_cross_role_host_and_life_rejected_before_mutation(self):
        state = state_fixture()
        self.assert_code('ROLE_FORBIDDEN', manual_battle.draft, state, 'host', dict(round_number=1, intents=[intent()]))
        self.assert_code('UNIT_FORBIDDEN', manual_battle.draft, state, 'red', dict(round_number=1, intents=[intent()]))
        item = intent(); item['life_id'] = 'stale'
        self.assert_code('LIFE_CHANGED', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[item]))

    def test_atomic_batch_does_not_write_first_valid_item_when_second_invalid(self):
        state = state_fixture(); before = deepcopy(state)
        self.assert_code('UNIT_FORBIDDEN', manual_battle.draft, state, 'blue',
            dict(round_number=1, intents=[intent(), intent('red')]))
        self.assertEqual(state, before)

    def test_visible_enemy_only_and_error_does_not_reveal_hidden_life(self):
        state = state_fixture(); state['observations']['blue']['contacts'] = []
        item = intent(task='attack'); item['task'].update(target_id='red-ground', target_life_id='wrong-life')
        self.assert_code('TARGET_NOT_VISIBLE', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[item]))
        state['units'] = [u for u in state['units'] if u['unit_id'] != 'red-ground']
        self.assert_code('TARGET_NOT_VISIBLE', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[item]))

    def test_unit_type_and_friendly_service_validation(self):
        state = state_fixture()
        item = intent(kind='recon', task='attack'); item['task'].update(target_id='red-ground', target_life_id='red-ground-life')
        self.assert_code('UNIT_ACTION_FORBIDDEN', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[item]))
        item = intent(kind='support', task='resupply'); item['task'].update(target_id='blue-recon', target_life_id='blue-recon-life', resource='ammo', quantity='2')
        self.assert_code('NO_WEAPON', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[item]))

    def test_strict_shapes_no_fast_reverse_or_implicit_transition(self):
        for movement in ({'action': 'fast_move'}, {'action': 'stop', 'position': {'x': '10', 'y': '10'}},
                         {'action': 'move', 'position': {'x': 'NaN', 'y': '10'}}):
            with self.subTest(movement=movement), self.assertRaises((ValidationError, ServiceError)):
                BattleUnitIntent.model_validate(dict(unit_id='u', life_id='l', movement=movement))
        for task in ({'action': 'attack'}, {'action': 'resupply', 'target_id': 'u', 'target_life_id': 'l'},
                     {'action': 'takeoff', 'position': {'x': '10', 'y': '10'}},
                     {'action': 'stop', 'resource': 'ammo'}, {'action': 'recon'},
                     {'action': 'attack', 'target_id': 'u', 'target_life_id': 'l', 'position': {'x': '1', 'y': '1'}}):
            with self.subTest(task=task), self.assertRaises(ValidationError):
                BattleTaskIntent.model_validate(task)

    def test_duplicate_units_capacity_and_round_are_strict(self):
        for payload in (dict(round_number=True, intents=[intent()]), dict(round_number=61, intents=[intent()]),
                        dict(round_number=1, intents=[intent(), intent()])):
            with self.assertRaises(ValidationError): BattleDraftRequest.model_validate(payload)

    def test_takeoff_draft_merge_cannot_hide_move_channel(self):
        state = state_fixture()
        item = intent(kind='recon', task='takeoff')
        state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[item]))
        move = dict(unit_id='blue-recon', life_id='blue-recon-life', movement=dict(action='move', position=dict(x='20', y='20')))
        before = deepcopy(state)
        self.assert_code('INTENT_CONFLICT', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[move]))
        self.assertEqual(before, state)

    def test_docked_recon_movement_rejected_before_lock_and_stop_recovers(self):
        for air in ('docked', 'taking_off', 'landing'):
            with self.subTest(air=air):
                state = state_fixture()
                next(u for u in state['units'] if u['unit_id'] == 'blue-recon')['air_state'] = air
                move = dict(unit_id='blue-recon', life_id='blue-recon-life',
                            movement=dict(action='move', position=dict(x='20', y='20')))
                state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[move]))
                before = deepcopy(state)
                self.assert_code('BATTLE_ROUTE_FORBIDDEN', manual_battle.submit, state, 'blue', {'round_number': 1})
                self.assertEqual(state, before)
                move['movement'] = dict(action='stop')
                state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[move]))
                self.assertTrue(manual_battle.submit(state, 'blue', {'round_number': 1})['manual_battle']['submitted']['blue'])

    def test_submit_locks_own_side_only_and_empty_submission_is_valid(self):
        state = state_fixture()
        state = manual_battle.submit(state, 'blue', {'round_number': 1})
        self.assertTrue(state['manual_battle']['submitted']['blue'])
        self.assertFalse(state['manual_battle']['submitted']['red'])
        self.assert_code('SIDE_ALREADY_SUBMITTED', manual_battle.draft, state, 'blue', dict(round_number=1, intents=[intent()]))
        self.assertEqual(manual_battle.submit(state, 'blue', {'round_number': 1}), state)

    def test_projection_filters_private_drafts_including_host(self):
        state = state_fixture()
        state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[intent()]))
        self.assertEqual(len(manual_battle.projection(state, 'blue')['own_drafts']), 1)
        for actor in ('host', 'red'):
            result = manual_battle.projection(state, actor)
            self.assertEqual(result['own_drafts'], [])
            self.assertNotIn('blue-ground-life', repr(result))
            self.assertNotIn('drafts', result)

    def locked(self):
        state = state_fixture()
        for side in ('blue', 'red'): state = manual_battle.submit(state, side, {'round_number': 1})
        return state

    def test_resolve_requires_host_both_submissions_and_real_executor(self):
        state = state_fixture(); engine = FixtureExecutor()
        self.assert_code('ROLE_FORBIDDEN', manual_battle.settle, state, 'blue', {'round_number': 1}, engine)
        self.assert_code('BOTH_SIDES_REQUIRED', manual_battle.settle, state, 'host', {'round_number': 1}, engine)
        self.assertEqual(engine.calls, 0)
        self.assert_code('BATTLE_ENGINE_NOT_READY', manual_battle.settle, self.locked(), 'host', {'round_number': 1}, None)

    def test_one_settlement_is_60_seconds_then_reopens_both_sides(self):
        state = self.locked(); before = deepcopy(state)
        after, record = manual_battle.settle(state, 'host', {'round_number': 1}, FixtureExecutor())
        self.assertEqual(state, before)
        self.assertEqual(after['manual_battle']['completed_rounds'], 1)
        self.assertEqual(after['manual_battle']['round_number'], 2)
        self.assertEqual(after['manual_battle']['elapsed_seconds'], 60)
        self.assertEqual(after['manual_battle']['submitted'], dict(blue=False, red=False))
        self.assertEqual(record['end_seconds'], 60)
        manual_battle.validate(after)
        self.assert_code('ROUND_CONFLICT', manual_battle.settle, after, 'host', {'round_number': 1}, FixtureExecutor())

    def test_early_finish_stops_decision_before_full_minute(self):
        after, record = manual_battle.settle(self.locked(), 'host', {'round_number': 1}, FixtureExecutor(duration=5, finished=True))
        self.assertEqual((record['end_seconds'], after['lifecycle']['phase']), (5, 'finished'))
        manual_battle.validate(after)
        self.assert_code('BATTLE_FINISHED', manual_battle.submit, after, 'blue', {'round_number': 2})

    def test_fake_animation_cannot_disagree_with_authoritative_world(self):
        state = self.locked(); before = deepcopy(state)
        self.assert_code('ROUND_RECORD_INVALID', manual_battle.settle, state, 'host', {'round_number': 1}, FixtureExecutor(wrong_world=True))
        self.assertEqual(state, before)

    def test_60_round_boundary_and_unchanged_legacy_commands(self):
        state = state_fixture()
        for count in range(1, 61):
            for side in ('blue', 'red'): state = manual_battle.submit(state, side, {'round_number': count})
            if count == 60:
                self.assert_code('REPLAY_TIME_INVALID', manual_battle.settle, state, 'host', {'round_number': count}, FixtureExecutor())
            state, _ = manual_battle.settle(state, 'host', {'round_number': count}, FixtureExecutor(finished=count == 60))
        self.assertEqual((state['manual_battle']['elapsed_seconds'], state['manual_battle']['completed_rounds']), (3600, 60))
        self.assertEqual(state['manual_battle']['stage'], 'finished')
        manual_battle.validate(state)
        for kind in ('advance_step', 'continue', 'start_game', 'unit_order', 'restore_save', 'configure_army'):
            self.assert_code('MANUAL_BARRIER_REQUIRED', manual_battle.guard_command, state, kind)
            manual_battle.guard_command({}, kind)
        self.assert_code('MANUAL_MODE_REQUIRED', manual_battle.guard_command, {}, 'battle_submit')

    def test_corrupt_manual_version_cannot_fall_back_to_old_engine(self):
        self.assert_code('MANUAL_STATE_INVALID', manual_battle.guard_command, {'manual_battle': {}}, 'advance_step')

class PlanBeforeLockTests(TestCase):
    def setUp(self):
        self.state = state_fixture()
        self.target = next(u for u in self.state['units'] if u['unit_id'] == 'blue-ground')
        self.source = next(u for u in self.state['units'] if u['unit_id'] == 'blue-support')
        self.task = dict(action='resupply', target_id=self.target['unit_id'],
            target_life_id=self.target['life_id'], resource='energy', quantity='10')

    def staged(self, task=None):
        return manual_battle.draft(self.state, 'blue', dict(round_number=1, intents=[dict(
            unit_id=self.source['unit_id'], life_id=self.source['life_id'], task=task or self.task)]))

    def test_target_continuing_movement_rejected_before_lock_and_can_be_fixed(self):
        self.target['movement'] = dict(action='move', position=dict(x='20', y='20'))
        state = self.staged(); before = deepcopy(state)
        with self.assertRaises(ServiceError) as error:
            manual_battle.submit(state, 'blue', dict(round_number=1))
        self.assertEqual(error.exception.code, 'INTENT_CONFLICT')
        self.assertEqual(before, state)
        state = manual_battle.draft(state, 'blue', dict(round_number=1, intents=[dict(
            unit_id=self.target['unit_id'], life_id=self.target['life_id'], movement=dict(action='stop'))]))
        locked = manual_battle.submit(state, 'blue', dict(round_number=1))
        self.assertTrue(locked['manual_battle']['submitted']['blue'])
        self.assertFalse(locked['manual_battle']['submitted']['red'])

    def test_excess_target_balance_rejected_without_lock(self):
        self.task.pop('quantity'); self.task['target_balance'] = '101'
        state = self.staged()
        with self.assertRaises(ServiceError) as error:
            manual_battle.submit(state, 'blue', dict(round_number=1))
        self.assertEqual(error.exception.code, 'BATTLE_SERVICE_INVALID')
        self.assertFalse(state['manual_battle']['submitted']['blue'])

    def test_takeoff_cannot_keep_previous_movement(self):
        recon = next(u for u in self.state['units'] if u['unit_id'] == 'blue-recon')
        recon['movement'] = dict(action='move', position=dict(x='20', y='20'))
        state = manual_battle.draft(self.state, 'blue', dict(round_number=1, intents=[intent(kind='recon', task='takeoff')]))
        with self.assertRaises(ServiceError) as error:
            manual_battle.submit(state, 'blue', dict(round_number=1))
        self.assertEqual(error.exception.code, 'INTENT_CONFLICT')

    def test_completed_target_balance_does_not_lock_moving_target(self):
        self.target['movement'] = dict(action='move', position=dict(x='20', y='20'))
        self.task.pop('quantity'); self.task['target_balance'] = '100'
        state = manual_battle.submit(self.staged(), 'blue', dict(round_number=1))
        self.assertTrue(state['manual_battle']['submitted']['blue'])

    def test_dead_receiver_old_task_is_cleared_at_execution_not_a_lock_error(self):
        self.source['task'] = deepcopy(self.task); self.target['durability'] = '0'
        state = manual_battle.submit(self.state, 'blue', dict(round_number=1))
        self.assertTrue(state['manual_battle']['submitted']['blue'])

    def test_invalid_exact_service_amount_is_validation_error_not_server_error(self):
        for amount in ('1/0', '-1', 'nan', '1e3'):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                BattleTaskIntent.model_validate(dict(self.task, quantity=amount))

    def test_barracks_over_capacity_fails_before_lock(self):
        state = self.staged(dict(action='barracks', camp_id='named-blue-camp', targets={'energy': '101'}))
        with self.assertRaises(ServiceError) as error:
            manual_battle.submit(state, 'blue', dict(round_number=1))
        self.assertEqual(error.exception.code, 'BATTLE_SERVICE_INVALID')

    def test_host_still_cannot_see_drafts_or_submit(self):
        state = self.staged()
        self.assertEqual(manual_battle.projection(state, 'host')['own_drafts'], [])
        with self.assertRaises(ServiceError) as error:
            manual_battle.submit(state, 'host', dict(round_number=1))
        self.assertEqual(error.exception.status, 403)
