#!/usr/bin/env python3
"""青岚 v1.1 独立策略参考模型（标准库、只读，不是正式游戏执行器）。

--self-test 运行确定性参考用例；不带参数输出反例与能力边界。
不导入后端、不启动 Unity、不调用模型、不改地图。简化一维轨迹只验证
保护穿行的必要边界，不能证明二维联合寻路、真实碰撞或整个游戏无漏洞。
"""
from dataclasses import dataclass
import json
import math
import sys
import unittest

VERSION = 'qinglan-rules-v1.1'
BASE_AMOUNTS = (50, 50, 50, 50, 60, 50)
KILL_POINTS = {'drone': 5, 'ground': 10, 'support': 8}


@dataclass
class CommonRound:
    authority: int
    adopted: bool = False

    def finish(self, delays, valid=(True, True)):
        """整轮耗时含修复；120秒恰好提交有效，失败没有单方采用。"""
        if self.adopted:
            raise ValueError('round already adopted')
        if len(delays) != 2 or any(d < 0 for d in delays):
            raise ValueError('two nonnegative whole-round durations required')
        if max(delays) > 120 or not all(valid):
            return None
        self.adopted = True
        return {'authority_boundary': self.authority,
                'released_after_real_seconds': max(delays),
                'adopted_sides': ('red', 'blue')}

    def advance(self, seconds=60):
        if not self.adopted or seconds <= 0 or seconds > 60 or seconds % 5:
            raise ValueError('only adopted 5-second results, at most one segment')
        self.authority += seconds
        self.adopted = False


@dataclass
class Buffer:
    authority: int = 0
    playback: int = 0
    started: bool = False
    rebuffering: bool = False
    terminal: bool = False
    planned_at: int | None = None

    @property
    def backlog(self):
        return self.authority - self.playback

    def begin_plan(self):
        if self.terminal or self.planned_at is not None or self.backlog > 120:
            raise ValueError('terminal, busy or backpressure')
        if self.authority % 60 or self.authority >= 3600:
            raise ValueError('not a normal planning boundary')
        self.planned_at = self.authority

    def commit_segment(self, seconds=60, terminal=False):
        """测试用按整段提交；真实实现可按5秒逐步提交，不在此假装验证。"""
        if self.planned_at != self.authority:
            raise ValueError('must plan from current authority')
        if seconds <= 0 or seconds > 60 or seconds % 5:
            raise ValueError('invalid segment')
        if seconds < 60 and not terminal:
            raise ValueError('partial segment requires terminal event')
        if self.authority + seconds > 3600:
            raise ValueError('beyond match limit')
        self.authority += seconds
        self.terminal = terminal or self.authority == 3600
        self.planned_at = None

    def speed(self):
        b = self.backlog
        if not self.started:
            if b < 180 and not self.terminal:
                return 0
            self.started = True
        if self.rebuffering:
            if b < 120 and not self.terminal:
                return 0
            self.rebuffering = False
        if b == 0:
            self.rebuffering = not self.terminal
            return 0
        return 2 if b >= 120 else 1 if b >= 60 else 0.5

    def consume_step(self):
        speed = self.speed()
        if speed == 0:
            return 0
        # B、权威事件都在5秒网格，本步速度不在中途变化。
        self.playback += 5
        if self.backlog == 0 and not self.terminal:
            self.rebuffering = True
        return 5 / speed


def public_projection(events, playback, audience):
    """参考投影，不是现有HUD/导出接口的信息隔离测试。"""
    return [e for e in events if e['time'] <= playback and
            e['audience'] in ('public', audience)]


@dataclass(frozen=True)
class Receiver:
    alive: bool = True
    stopped: bool = True
    requested: bool = True
    fired: bool = False
    last_fired: int | None = None
    last_damaged: int | None = None
    current_damage: float = 0


def service_allowed(unit, boundary, base=True):
    if not unit.alive or not unit.stopped or not unit.requested or unit.fired:
        return False
    if not base:
        return True
    damaged = boundary if unit.current_damage > 0 else unit.last_damaged
    return all(t is None or boundary - t >= 15
               for t in (unit.last_fired, damaged))


def replenish(current, capacity, amounts=BASE_AMOUNTS):
    if len(current) != 6 or len(capacity) != 6 or any(
            c < 0 or c > cap for c, cap in zip(current, capacity)):
        raise ValueError('six legal resource values required')
    return tuple(min(cap, c + amount) for c, cap, amount in zip(current, capacity, amounts))


def assign_base_receivers(requests, boundary, already_served=()):
    """requests已按合法队列排序；不在此虚构队列公平或防饥饿证明。"""
    assigned = []
    used = set(already_served)
    for slot, unit in requests:
        if slot not in used and service_allowed(unit, boundary):
            assigned.append(slot)
            used.add(slot)
        if len(assigned) == 4:
            break
    return assigned


@dataclass(frozen=True)
class PointUnit:
    side: str
    inside_after_move: bool
    full_stop: bool = False
    survives_damage: bool = True
    energy_at_start: float = 1
    energy_after_move: float = 1
    protected: bool = False
    kind: str = 'ground'

    def eligible(self):
        return (self.energy_at_start > 0 and self.energy_after_move > 0 and
                not self.protected and self.kind in ('ground', 'support'))


def point_snapshot(units):
    return frozenset(u.side for u in units if u.inside_after_move and u.eligible())


def point_decision(owner, stage_side, units):
    snapshot = point_snapshot(units)
    enemy = 'blue' if owner == 'red' else 'red'
    suppressed = owner is not None and enemy in snapshot
    contest = len(snapshot) == 2 or any(s != stage_side for s in snapshot)
    progress = (not contest and any(u.side == stage_side and u.eligible() and
                u.inside_after_move and u.full_stop and u.survives_damage for u in units))
    return {'snapshot': snapshot, 'income': bool(owner) and not suppressed,
            'core_point': bool(owner) and not suppressed,
            'reset': contest or not progress, 'progress_seconds': 5 if progress else 0}


def legal_1d_pair(a, b, clearance, protected=False,
                  a_budget=math.inf, b_budget=math.inf, static_ok=True):
    """a,b为两单位起终点；直线同速插值，clearance=半径和+安全余量。

    仅在这个一维示例中检查：能源、静态许可、边界和普通敌军扫掠。
    不求解停车分配，不检查真实地图/二维转弯/多单位/提前解除保护。
    """
    if not static_ok or abs(a[1] - a[0]) > a_budget or abs(b[1] - b[0]) > b_budget:
        return False
    start, end = a[0] - b[0], a[1] - b[1]
    if min(abs(start), abs(end)) < clearance:
        return False
    if not protected and start * end < 0:
        return False
    return True


def terminal_result(core_red, core_blue, score_red, score_blue, seconds):
    if core_red <= 0 and core_blue <= 0:
        return 'draw'
    if core_red <= 0:
        return 'blue'
    if core_blue <= 0:
        return 'red'
    if max(score_red, score_blue) >= 1500 or seconds >= 3600:
        return 'draw' if score_red == score_blue else ('red' if score_red > score_blue else 'blue')
    return None


def old_async_count(delay, horizon=600, playback_speed=2):
    # 旧规则反例，不含t=0首次方案；在10个60秒边界发起，忙则跳过。
    ready, completions = 0, []
    for boundary in range(60, horizon, 60):
        if boundary >= ready:
            ready = boundary + delay * playback_speed
            if ready <= horizon:
                completions.append(ready)
    return len(completions)



def stationary_base_counterexample(attackers, new_policy=False, horizon=600):
    """固定来袭火力的算术反例；不模拟攻击者损耗、位置或完整交火。

    来袭者与守军在5,15,…秒开火，守军仅在10,20,…秒请求基地服务。
    原规则可用冷却空档维修；新版每次申请离上次开火仅5秒。
    """
    hp, shots, last_shot = 100, 0, None
    for boundary in range(5, horizon + 1, 5):
        if boundary % 10 == 5:
            hp -= 16 * attackers
            shots += 1  # 本步同步开火，即使本步死亡仍完成已冻结攻击
            last_shot = boundary
            if hp <= 0:
                return {'death_at': boundary, 'shots': shots}
        elif not new_policy or service_allowed(Receiver(last_fired=last_shot), boundary):
            hp = min(100, hp + 50)
    return {'death_at': None, 'shots': shots}


def evidence():
    return {
        'rule_version': VERSION,
        'scope': 'independent reference policies, NOT runtime or real-match verification',
        'old_async_adoptions_excluding_t0_in_first_600_sim_seconds':
            {str(d): old_async_count(d) for d in (10, 60, 120)},
        'base_refill_per_5_seconds': list(BASE_AMOUNTS),
        'old_fixed_incoming_base_arithmetic': {str(n): stationary_base_counterexample(n) for n in (3, 4, 12)},
        'shot_at_5_earliest_base_service': 20,
        'edge_crossing_12_steps': {'old_income_suppressed': 12,
                                  'old_core_blocked': 6,
                                  'new_both_suppressed': 6},
        'full_roster_kill_points': 4 * 5 + 12 * 10 + 4 * 8,
        'nine_rosters_kill_points': 9 * (4 * 5 + 12 * 10 + 4 * 8),
        'sync_spawn_125_expiry_140_next_boundary_180_exposure_seconds': 40,
        'map_edited': False,
        'limitations': ['no actual engine, AI provider, UI or real navigation executed',
                        'one-dimensional pair check is not a collision solver',
                        'point and resource helpers omit full match orchestration',
                        'release gates remain open; no win rate or bug-free claim'],
    }


class PlanningTests(unittest.TestCase):
    def test_delay_exchange_common_adoption(self):
        for a in (10, 60, 120):
            for b in (10, 60, 120):
                x, y = CommonRound(60), CommonRound(60)
                self.assertEqual(x.finish((a, b)), y.finish((b, a)))
                self.assertEqual(x.authority, 60)
                x.advance()
                self.assertEqual(x.authority, 120)

    def test_equal_command_counts_over_ten_rounds(self):
        for delays in ((10, 120), (120, 10), (60, 60)):
            clock, counts = CommonRound(0), [0, 0]
            for _ in range(10):
                self.assertIsNotNone(clock.finish(delays))
                counts = [n + 1 for n in counts]
                clock.advance()
            self.assertEqual((clock.authority, counts), (600, [10, 10]))

    def test_timeout_exact_boundary_and_over(self):
        self.assertIsNotNone(CommonRound(0).finish((120, 120)))
        clock = CommonRound(60)
        self.assertIsNone(clock.finish((10, 120.001)))
        self.assertEqual(clock.authority, 60)
        self.assertFalse(clock.adopted)

    def test_repair_is_part_of_total_budget(self):
        self.assertIsNone(CommonRound(0).finish((100 + 21, 10)))
        self.assertIsNotNone(CommonRound(0).finish((100 + 20, 10)))

    def test_invalid_side_cancels_common_adoption(self):
        self.assertIsNone(CommonRound(0).finish((10, 20), (True, False)))

    def test_no_advance_before_adoption_or_duplicate_apply(self):
        clock = CommonRound(0)
        with self.assertRaises(ValueError):
            clock.advance()
        clock.finish((10, 20))
        with self.assertRaises(ValueError):
            clock.finish((10, 20))

    def test_old_async_counterexample(self):
        self.assertEqual([old_async_count(d) for d in (10, 60, 120)], [9, 4, 2])


class PlaybackTests(unittest.TestCase):
    def test_prefill_requires_three_distinct_snapshots(self):
        buffer = Buffer()
        for t in (0, 60, 120):
            self.assertEqual(buffer.authority, t)
            self.assertEqual(buffer.speed(), 0)
            buffer.begin_plan()
            self.assertEqual(buffer.planned_at, t)
            buffer.commit_segment()
        self.assertEqual(buffer.speed(), 2)
        self.assertEqual(buffer.backlog, 180)

    def test_backpressure_and_cap(self):
        buffer = Buffer(authority=180, started=True)
        with self.assertRaises(ValueError):
            buffer.begin_plan()
        buffer.playback = 60
        buffer.begin_plan()
        buffer.commit_segment()
        self.assertEqual(buffer.backlog, 180)

    def test_speed_boundaries(self):
        for b, speed in ((180, 2), (120, 2), (115, 1), (60, 1), (55, .5), (5, .5), (0, 0)):
            self.assertEqual(Buffer(authority=b, started=True).speed(), speed)

    def test_step_speed_held_until_next_step(self):
        buffer = Buffer(authority=120, started=True)
        self.assertEqual(buffer.consume_step(), 2.5)
        self.assertEqual(buffer.backlog, 115)
        self.assertEqual(buffer.consume_step(), 5)

    def test_underflow_requires_120_to_resume(self):
        buffer = Buffer(authority=60, playback=55, started=True)
        buffer.consume_step()
        self.assertTrue(buffer.rebuffering)
        buffer.begin_plan()
        buffer.commit_segment()
        self.assertEqual(buffer.speed(), 0)
        buffer.begin_plan()
        buffer.commit_segment()
        self.assertEqual(buffer.speed(), 2)
        self.assertFalse(buffer.rebuffering)

    def test_terminal_before_initial_prefill(self):
        buffer = Buffer()
        buffer.begin_plan()
        buffer.commit_segment(5, terminal=True)
        self.assertEqual(buffer.speed(), .5)
        buffer.consume_step()
        self.assertEqual((buffer.backlog, buffer.speed()), (0, 0))
        with self.assertRaises(ValueError):
            buffer.begin_plan()

    def test_terminal_during_rebuffer_drains_remainder(self):
        buffer = Buffer(authority=60, playback=60, started=True, rebuffering=True)
        buffer.begin_plan()
        buffer.commit_segment(10, terminal=True)
        self.assertEqual(buffer.speed(), .5)
        buffer.consume_step()
        buffer.consume_step()
        self.assertEqual(buffer.playback, buffer.authority)

    def test_no_placeholder_or_more_than_one_producer(self):
        buffer = Buffer(started=True)
        self.assertEqual(buffer.consume_step(), 0)
        self.assertEqual(buffer.playback, 0)
        with self.assertRaises(ValueError):
            buffer.commit_segment()
        buffer.begin_plan()
        with self.assertRaises(ValueError):
            buffer.begin_plan()

    def test_future_terminal_and_private_info_filtered(self):
        events = [{'time': 60, 'audience': 'public', 'kind': 'score'},
                  {'time': 60, 'audience': 'blue', 'kind': 'ammo'},
                  {'time': 120, 'audience': 'public', 'kind': 'terminal'}]
        self.assertEqual([e['kind'] for e in public_projection(events, 60, 'red')], ['score'])
        self.assertEqual(len(public_projection(events, 120, 'blue')), 3)


class ServiceTests(unittest.TestCase):
    def test_shot_then_10_15_20_boundaries(self):
        unit = Receiver(last_fired=5)
        self.assertEqual([service_allowed(unit, t) for t in (10, 15, 20)], [False, False, True])

    def test_both_histories_apply(self):
        unit = Receiver(last_fired=5, last_damaged=10)
        self.assertFalse(service_allowed(unit, 20))
        self.assertTrue(service_allowed(unit, 25))

    def test_current_damage_cancels_base_not_field(self):
        unit = Receiver(current_damage=1)
        self.assertFalse(service_allowed(unit, 100))
        self.assertTrue(service_allowed(unit, 100, base=False))

    def test_zero_damage_does_not_reset(self):
        self.assertTrue(service_allowed(Receiver(last_damaged=5, current_damage=0), 20))

    def test_lethal_never_healed(self):
        for base in (False, True):
            self.assertFalse(service_allowed(Receiver(alive=False), 100, base=base))

    def test_stop_request_no_fire_are_mandatory(self):
        for unit in (Receiver(stopped=False), Receiver(requested=False), Receiver(fired=True)):
            self.assertFalse(service_allowed(unit, 100))
            self.assertFalse(service_allowed(unit, 100, base=False))

    def test_new_life_history_empty_not_reset_by_real_wait(self):
        previous = Receiver(last_fired=5)
        self.assertFalse(service_allowed(previous, 10))
        self.assertFalse(service_allowed(previous, 10))  # real wait has no sim-time input
        self.assertTrue(service_allowed(Receiver(), 10))  # new life, no fabricated damage

    def test_exact_amounts_caps_and_support_energy_50(self):
        self.assertEqual(replenish((0,) * 6, (100,) * 6), BASE_AMOUNTS)
        self.assertEqual(replenish((99,) * 6, (100,) * 6), (100,) * 6)
        self.assertEqual(BASE_AMOUNTS[3], 50)

    def test_four_receivers_unique_and_unqualified_do_not_take_slot(self):
        requests = [('recent', Receiver(last_fired=19))] + [(str(i), Receiver()) for i in range(6)]
        requests.insert(3, ('0', Receiver()))
        self.assertEqual(assign_base_receivers(requests, 20, already_served=('1',)), ['0', '2', '3', '4'])

    def test_old_base_fixed_incoming_counterexample(self):
        self.assertEqual(stationary_base_counterexample(3), {'death_at': None, 'shots': 60})
        self.assertEqual(stationary_base_counterexample(4)['death_at'], 35)
        self.assertEqual(stationary_base_counterexample(12)['death_at'], 5)
        self.assertEqual(stationary_base_counterexample(3, new_policy=True)['death_at'], 25)

    def test_old_alternating_shots_cannot_refill_new_base(self):
        for shot in range(5, 600, 10):
            self.assertFalse(service_allowed(Receiver(last_fired=shot), shot + 5))


class PointTests(unittest.TestCase):
    def test_pass_through_absent_no_suppression_or_reset(self):
        units = [PointUnit('red', True, full_stop=True), PointUnit('blue', False)]
        result = point_decision('red', 'red', units)
        self.assertTrue(result['income'])
        self.assertTrue(result['core_point'])
        self.assertFalse(result['reset'])
        self.assertEqual(result['progress_seconds'], 5)

    def test_dead_after_snapshot_still_suppresses(self):
        result = point_decision('red', 'red', [PointUnit('blue', True, survives_damage=False)])
        self.assertFalse(result['income'])
        self.assertFalse(result['core_point'])
        self.assertTrue(result['reset'])

    def test_moving_entry_no_capture_progress(self):
        result = point_decision(None, 'blue', [PointUnit('blue', True, full_stop=False)])
        self.assertEqual(result['progress_seconds'], 0)

    def test_dead_capturer_no_progress(self):
        result = point_decision(None, 'red', [PointUnit('red', True, full_stop=True, survives_damage=False)])
        self.assertEqual(result['progress_seconds'], 0)

    def test_ineligible_energy_protection_drone(self):
        for unit in (PointUnit('blue', True, energy_at_start=0),
                     PointUnit('blue', True, energy_after_move=0),
                     PointUnit('blue', True, protected=True),
                     PointUnit('blue', True, kind='drone')):
            self.assertEqual(point_snapshot([unit]), frozenset())

    def test_edge_oscillation_income_matches_core(self):
        suppressed_income, suppressed_core = [], []
        for step in range(12):
            result = point_decision('red', 'red', [PointUnit('blue', step % 2 == 1)])
            suppressed_income.append(not result['income'])
            suppressed_core.append(not result['core_point'])
        self.assertEqual(suppressed_income, suppressed_core)
        self.assertEqual(sum(suppressed_income), 6)


class ProtectionAndRetainedRiskTests(unittest.TestCase):
    def test_protected_1d_crossing_allowed_ordinary_blocked(self):
        self.assertTrue(legal_1d_pair((0, 0), (-10, 15), 7, protected=True))
        self.assertFalse(legal_1d_pair((0, 0), (-10, 15), 7))

    def test_end_overlap_or_no_safe_energy_rejected(self):
        self.assertFalse(legal_1d_pair((0, 0), (-10, 0), 7, protected=True))
        self.assertFalse(legal_1d_pair((0, 0), (-10, 15), 7, protected=True, b_budget=10))
        self.assertFalse(legal_1d_pair((0, 0), (-10, 15), 7, protected=True, static_ok=False))

    def test_stationary_legal_position_remains_on_rejected_route(self):
        start = (0, -10)
        allowed = legal_1d_pair((0, 0), (-10, 0), 7, protected=True)
        committed = (0, 0) if allowed else start
        self.assertEqual(committed, start)
        self.assertGreaterEqual(abs(committed[0] - committed[1]), 7)

    def test_full_repeated_kill_scores_retained(self):
        roster = 4 * KILL_POINTS['drone'] + 12 * KILL_POINTS['ground'] + 4 * KILL_POINTS['support']
        self.assertEqual(roster, 172)
        self.assertEqual(roster * 9, 1548)

    def test_respawn_idle_exposure_retained(self):
        spawn, protection, next_plan = 125, 15, 180
        self.assertEqual(max(0, next_plan - (spawn + protection)), 40)
        # 下一轮推理期间权威冻结，不把现实120秒再计入暴露。


class TerminalReferenceTests(unittest.TestCase):
    def test_simultaneous_core_death_draw(self):
        self.assertEqual(terminal_result(0, 0, 1600, 0, 100), 'draw')

    def test_core_beats_score(self):
        self.assertEqual(terminal_result(100, 0, 0, 1500, 100), 'red')

    def test_score_tie_and_time_limit(self):
        self.assertEqual(terminal_result(100, 100, 1500, 1500, 100), 'draw')
        self.assertEqual(terminal_result(100, 100, 1499, 1498, 3600), 'red')
        self.assertIsNone(terminal_result(100, 100, 1499, 1498, 3595))

    def test_final_step_count(self):
        self.assertEqual(len(range(5, 3601, 5)), 720)
        self.assertEqual(list(range(0, 3600, 60))[-1], 3540)


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        print(json.dumps(evidence(), ensure_ascii=False, indent=2, allow_nan=False))
