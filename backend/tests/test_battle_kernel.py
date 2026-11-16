"""总册数值核心测试；无用户库、无网络、不假设真实地图已验收。"""
from copy import deepcopy
from fractions import Fraction
from unittest import TestCase

from backend import battle_kernel as k
from backend.runtime import ServiceError


def fixture(count=1):
    units = []
    for side in k.SIDES:
        for kind, p in k.profiles().items():
            for i in range(count):
                identity = f'{side}-{kind}-{i}'
                units.append(dict(unit_id=identity, life_id=identity + '-life', side=side, unit_type=kind,
                    durability=p['durability'], energy=p['energy'], ammo=p['ammo'],
                    position_mm=dict(x_mm=1000000 if side == 'blue' else 1100000, z_mm=1000000, height_mm=0)))
    return k.initialize(dict(units=units, lifecycle=dict(phase='preparing', simulation_step=0)))


def unit(state, kind='ground', side='blue', i=0):
    return next(u for u in state['units'] if u['unit_id'] == f'{side}-{kind}-{i}')


def spend(state, u, field, amount):
    u[field] = k.exact(k.number(u[field]) - amount)
    account = state['battle_ledger'][u['side']]['consumed']
    account[field] = k.exact(k.number(account[field]) + amount)


def fire(state, **kwargs):
    defaults = dict(seconds=5, start_positive={u['unit_id'] for u in state['units'] if k.alive(u) and k.number(u['energy']) > 0},
        moved_energy={}, observed={s: {u['unit_id'] for u in state['units'] if u['side'] != s} for s in k.SIDES},
        receivers=set(), fire_clear=lambda *_: True)
    defaults.update(kwargs)
    return k.resolve_fire(state, **defaults)


def aim(state, side='blue'):
    source, target = unit(state, side=side), unit(state, side='red' if side == 'blue' else 'blue')
    source['task'] = dict(action='attack', target_id=target['unit_id'], target_life_id=target['life_id'])


def points(owner='neutral'):
    return [dict(point_id=f'P-{i:02d}', owner=owner, advancing_side='none', progress_steps=0, contested=False) for i in range(1, 6)]


def request(state, resource='energy', target_kind='ground', **value):
    source, target = unit(state, 'support'), unit(state, target_kind)
    return dict(source_id=source['unit_id'], source_life_id=source['life_id'], target_id=target['unit_id'],
                target_life_id=target['life_id'], resource=resource, **(value or {'quantity': '10'}))


def service(state, requests, **kwargs):
    defaults = dict(seconds=5, stationary={u['unit_id'] for u in state['units']},
                    start_positive={u['unit_id'] for u in state['units'] if k.number(u['energy']) > 0}, service_clear=lambda *_: True)
    defaults.update(kwargs)
    return k.service_step(state, requests, **defaults)


class ExactLedgerTests(TestCase):
    def test_new_full_ledger_and_exact_thirty_resource_points(self):
        s = fixture()
        for side in k.SIDES:
            self.assertEqual(s['battle_ledger'][side]['initial'], dict(energy='1700', ammo='780', repair='300'))
            self.assertEqual(k.score(s, side)['resources'], '30')
            self.assertEqual(k.score(s, side)['total'], '30')

    def test_initialization_does_not_mutate_and_cannot_reset(self):
        s = fixture(); before = deepcopy(s)
        with self.assertRaises(ServiceError):
            k.initialize(s)
        self.assertEqual(s, before)

    def test_exact_numbers_reject_float_bool_nan_exponents_and_zero_division(self):
        for value in (True, 1.0, 'NaN', '1e2', '-1', '1/0', '1/' + '2' * 80):
            with self.subTest(value=value), self.assertRaises(ServiceError):
                k.number(value)
        self.assertEqual(k.number('0.1') * 10, 1)

    def test_conservation_rejects_fabricated_energy(self):
        s = fixture(); unit(s)['energy'] = '99'
        with self.assertRaises(ServiceError):
            k.validate(s)

    def test_fraction_energy_does_not_round_on_accumulation(self):
        s = fixture(); u = unit(s)
        for _ in range(600):
            k.consume_energy(s, u, Fraction(1, 6))
        self.assertEqual(u['energy'], '0'); k.validate(s)
        with self.assertRaises(ServiceError):
            k.consume_energy(s, u, 1)

    def test_fractional_ammo_is_rejected(self):
        s = fixture(); unit(s)['ammo'] = '59/2'
        with self.assertRaises(ServiceError):
            k.validate(s)


class MovementBudgetTests(TestCase):
    def budget(self, kind='ground', surface='road', grade='0', **kwargs):
        return k.segment_budget(kind, [dict(surface=surface, grade=grade, length_mm='100000')], '100', **kwargs)

    def test_road_and_field_tank_speed_and_energy(self):
        self.assertEqual(self.budget()['lengths_mm'], ['100000/3'])
        self.assertEqual(self.budget()['energy'], '4/15')
        self.assertEqual(self.budget(surface='field')['lengths_mm'], ['50000/3'])
        self.assertEqual(self.budget(surface='field')['energy'], '4/15')

    def test_support_field(self):
        r = self.budget('support', 'field'); self.assertEqual(r['lengths_mm'], ['7500'])
        self.assertEqual(r['energy'], '1/4')

    def test_mixed_surface_uses_remaining_time_not_fresh_step(self):
        r = k.segment_budget('ground', [dict(surface='road', grade='0', length_mm='10000'),
            dict(surface='field', grade='0', length_mm='100000')], '100')
        self.assertEqual(sum(k.number(n) for n in r['lengths_mm']), Fraction(65000, 3))
        self.assertEqual(r['energy'], '4/15')

    def test_grade_boundaries_and_illegal_unknown_surfaces(self):
        self.budget('support', grade='0.2'); self.budget(grade='0.35')
        for kind, surface, grade in [('support', 'road', '0.2001'), ('ground', 'road', '0.3501'),
                ('ground', 'water', '0'), ('support', 'sparse_forest', '0'), ('ground', 'unknown', '0')]:
            with self.subTest(kind=kind, surface=surface, grade=grade), self.assertRaises(ServiceError):
                self.budget(kind, surface, grade)

    def test_bridge_actual_grade_is_not_forced_to_flat(self):
        self.assertLess(k.number(self.budget(surface='bridge', grade='0.15')['lengths_mm'][0]),
                        k.number(self.budget(surface='bridge', grade='0')['lengths_mm'][0]))

    def test_reservation_limits_distance_and_preserves_unspent_energy(self):
        r = k.segment_budget('ground', [dict(surface='road', grade='0', length_mm='100000')], '1/10', moving_attack=True)
        self.assertEqual(r['lengths_mm'], ['10000']); self.assertEqual(r['energy'], '2/25')
        self.assertEqual(r['reserved_energy'], '1/10')

    def test_drone_ignores_ground_factors_but_requires_air(self):
        self.assertEqual(self.budget('recon', 'air', '0.9')['lengths_mm'], ['200000/3'])
        with self.assertRaises(ServiceError):
            self.budget('recon', 'water')

    def test_split_same_surface_does_not_create_time_or_energy(self):
        one = self.budget()
        split = k.segment_budget('ground', [dict(surface='road', grade='0', length_mm='1000') for _ in range(100)], '100')
        self.assertEqual(sum(k.number(n) for n in split['lengths_mm']), k.number(one['lengths_mm'][0]))
        self.assertEqual(split['energy'], one['energy'])


class FireTests(TestCase):
    def test_synchronized_mutual_kill_both_shots_and_one_value_each(self):
        s = fixture(); aim(s); aim(s, 'red')
        unit(s)['durability'] = '16'; unit(s, side='red')['durability'] = '16'
        before = deepcopy(s); after, shots = fire(s)
        self.assertEqual(s, before); self.assertEqual(len(shots), 2)
        self.assertTrue(unit(after)['retired']); self.assertTrue(unit(after, side='red')['retired'])
        self.assertEqual(after['battle_ledger']['blue']['kills'], '10')
        self.assertEqual(after['battle_ledger']['red']['kills'], '10')
        self.assertEqual(after['battle_ledger']['blue']['consumed']['ammo'], '1')
        self.assertEqual(after['battle_ledger']['blue']['lost']['ammo'], '59')
        again, shots = fire(after); self.assertEqual(shots, []); self.assertEqual(again, after)

    def test_first_shot_five_then_fifteen_and_target_change_does_not_reset(self):
        s = fixture(); aim(s)
        s, shots = fire(s); self.assertEqual(len(shots), 1)
        s['battle_ledger']['elapsed_seconds'] = 5
        s, shots = fire(s, seconds=10); self.assertEqual(shots, [])
        s['battle_ledger']['elapsed_seconds'] = 10
        _, shots = fire(s, seconds=15); self.assertEqual(len(shots), 1)

    def test_moving_damage_and_exact_extra_energy(self):
        s = fixture(); aim(s); k.consume_energy(s, unit(s), Fraction(4, 15))
        after, shots = fire(s, moved_energy={unit(s)['unit_id']: '4/15'})
        self.assertEqual(shots[0]['damage'], '48/5')
        self.assertEqual(unit(after, side='red')['durability'], '452/5')
        self.assertEqual(unit(after)['energy'], '299/3')

    def test_extra_energy_exactly_to_zero_refuses_without_ammo_or_cooldown(self):
        s = fixture(); aim(s); spend(s, unit(s), 'energy', Fraction(999, 10))
        before = deepcopy(s)
        after, shots = fire(s, moved_energy={unit(s)['unit_id']: '2/5'})
        self.assertEqual(shots, []); self.assertEqual(after, before)

    def test_blocked_lost_sight_service_or_zero_start_energy_do_not_shoot(self):
        s = fixture(); aim(s)
        for kwargs in (dict(fire_clear=lambda *_: False), dict(observed={}),
                       dict(receivers={unit(s)['unit_id']}), dict(start_positive=set())):
            with self.subTest(kwargs=kwargs):
                after, shots = fire(s, **kwargs); self.assertEqual(shots, []); self.assertEqual(after, s)

    def test_target_life_mismatch_or_out_of_range(self):
        s = fixture(); aim(s); unit(s)['task']['target_life_id'] = 'old-life'
        self.assertEqual(fire(s)[1], [])
        aim(s); unit(s, side='red')['position_mm']['x_mm'] = 1600001
        self.assertEqual(fire(s)[1], [])
        unit(s, side='red')['position_mm']['x_mm'] = 1600000
        self.assertEqual(len(fire(s)[1]), 1)

    def test_air_transition_uses_air_range_but_docked_is_ground_range(self):
        s = fixture(); source, target = unit(s), unit(s, 'recon', 'red')
        source['task'] = dict(action='attack', target_id=target['unit_id'], target_life_id=target['life_id'])
        target['position_mm']['x_mm'] = 1700000
        self.assertEqual(fire(s)[1], [])
        target['air_state'] = 'taking_off'
        self.assertEqual(len(fire(s)[1]), 1)

    def test_range_tie_cursor_persists_and_rejected_shot_does_not_advance(self):
        s = fixture(2); source = unit(s)
        source['task'] = dict(action='attack_area', center_mm=deepcopy(source['position_mm']), radius_mm='1000000')
        # 坦克耐久100，另外两类先移到范围外，构造等距等耐久候选。
        for u in s['units']:
            if u['side'] == 'red' and u['unit_type'] != 'ground':
                u['position_mm']['x_mm'] = 5000000
        s, shots = fire(s); self.assertEqual(shots[0]['target_id'], 'red-ground-0')
        unit(s, side='red')['durability'] = '100'
        s['battle_ledger']['elapsed_seconds'] = 10
        s, shots = fire(s, seconds=15); self.assertEqual(shots[0]['target_id'], 'red-ground-1')

    def test_order_of_roster_does_not_change_simultaneous_result(self):
        s = fixture(); aim(s); aim(s, 'red'); reverse = deepcopy(s); reverse['units'].reverse()
        a, events_a = fire(s); b, events_b = fire(reverse)
        self.assertEqual(events_a, events_b)
        self.assertEqual(k._units(a), k._units(b))


class AirTests(TestCase):
    def test_takeoff_strict_threshold_and_full_cost(self):
        s = fixture(); u = unit(s, 'recon'); spend(s, u, 'energy', 100 - Fraction(7, 6))
        after, transition = k.air_begin(s, u['unit_id'], 'takeoff', dock_legal=True, air_legal=True)
        self.assertIsNone(transition); self.assertEqual(after, s)
        s = fixture(); u = unit(s, 'recon')
        after, transition = k.air_begin(s, u['unit_id'], 'takeoff', dock_legal=True, air_legal=True)
        self.assertEqual(transition, 'takeoff'); self.assertEqual(unit(after, 'recon')['energy'], '593/6')
        self.assertEqual(unit(after, 'recon')['air_state'], 'taking_off')
        after = k.air_finish(after, u['unit_id'], landing_clear=True)
        self.assertEqual(unit(after, 'recon')['air_state'], 'flying')

    def test_invalid_land_costs_only_maintenance(self):
        s = fixture(); u = unit(s, 'recon'); u['air_state'] = 'flying'
        after, transition = k.air_begin(s, u['unit_id'], 'land', dock_legal=False, air_legal=True)
        self.assertIsNone(transition); self.assertEqual(unit(after, 'recon')['energy'], '599/6')

    def test_failed_dynamic_landing_keeps_flying_no_refund(self):
        s = fixture(); u = unit(s, 'recon'); u['air_state'] = 'flying'
        after, _ = k.air_begin(s, u['unit_id'], 'land', dock_legal=True, air_legal=True)
        after = k.air_finish(after, u['unit_id'], landing_clear=False)
        self.assertEqual(unit(after, 'recon')['air_state'], 'flying')
        self.assertEqual(unit(after, 'recon')['energy'], '593/6')

    def test_air_power_failure_is_permanent_without_kill_credit(self):
        s = fixture(); u = unit(s, 'recon'); u['air_state'] = 'flying'; spend(s, u, 'energy', Fraction(599, 6))
        after, _ = k.air_begin(s, u['unit_id'], None, dock_legal=False, air_legal=True)
        self.assertTrue(unit(after, 'recon')['retired']); self.assertEqual(after['battle_ledger']['red']['kills'], '0')
        after2, _ = k.air_begin(after, u['unit_id'], 'takeoff', dock_legal=True, air_legal=True)
        self.assertEqual(after2, after)

    def test_docked_zero_energy_is_disabled_not_dead(self):
        s = fixture(); u = unit(s, 'recon'); spend(s, u, 'energy', 100)
        after, _ = k.air_begin(s, u['unit_id'], None, dock_legal=True, air_legal=True)
        self.assertFalse(unit(after, 'recon')['retired']); self.assertEqual(after, s)


class ServiceTests(TestCase):
    def test_energy_transfer_conserved_and_quantity_remaining(self):
        s = fixture(); spend(s, unit(s), 'energy', 10); r = request(s); before = deepcopy(s)
        after, events = service(s, [r])
        self.assertEqual(s, before); self.assertEqual(r['quantity'], '10')
        self.assertEqual(unit(after)['energy'], '92'); self.assertEqual(unit(after, 'support')['cargo']['energy'], '198')
        self.assertEqual(events[0]['remaining_request']['quantity'], '8')
        self.assertEqual(k.resource_totals(s, 'blue'), k.resource_totals(after, 'blue'))

    def test_repair_consumes_material_not_energy_and_never_overheals(self):
        s = fixture(); unit(s)['durability'] = '99'
        after, events = service(s, [request(s, 'repair', target_balance='100')])
        self.assertEqual(events[0]['amount'], '1'); self.assertEqual(unit(after)['durability'], '100')
        self.assertEqual(after['battle_ledger']['blue']['consumed']['repair'], '1')
        self.assertEqual(unit(after, 'support')['energy'], '100')

    def test_illegal_surface_dead_source_or_moving_returns_zero_without_stock_change(self):
        s = fixture(); spend(s, unit(s), 'energy', 10); r = request(s)
        for kwargs in (dict(service_clear=lambda *_: False), dict(stationary=set()), dict(start_positive=set())):
            after, events = service(s, [r], **kwargs); self.assertEqual(after, s); self.assertEqual(events, [])
        k._retire(s, unit(s, 'support'), 'attack', 'red')
        after, events = service(s, [r]); self.assertEqual(after, s); self.assertEqual(events, [])

    def test_single_role_single_source_and_single_resource_conflicts_reject_atomically(self):
        s = fixture(2); a = request(s); b = deepcopy(a); b['resource'] = 'repair'
        before = deepcopy(s)
        with self.assertRaises(ServiceError):
            service(s, [a, b])
        b = dict(source_id='blue-support-1', source_life_id='blue-support-1-life', target_id='blue-support-0',
                 target_life_id='blue-support-0-life', resource='energy', quantity='2')
        with self.assertRaises(ServiceError):
            service(s, [a, b])
        self.assertEqual(s, before)

    def test_receiving_intent_locks_fire_even_if_no_delivery(self):
        s = fixture(); aim(s); r = request(s)
        receivers = k.service_receivers(s, [r])
        self.assertEqual(fire(s, receivers=receivers)[1], [])
        self.assertEqual(service(s, [r])[1], [])

    def test_self_enemy_ammo_non_tank_and_wrong_life_rejected(self):
        s = fixture()
        for patch in (dict(target_id='blue-support-0', target_life_id='blue-support-0-life'),
                dict(target_id='red-ground-0', target_life_id='red-ground-0-life'),
                dict(resource='ammo', target_id='blue-recon-0', target_life_id='blue-recon-0-life'),
                dict(target_life_id='old-life'), dict(resource='ammo', quantity='1/2')):
            r = request(s); r.update(patch)
            with self.subTest(patch=patch), self.assertRaises(ServiceError):
                service(s, [r])

    def test_docked_drone_can_receive_but_transition_cannot(self):
        s = fixture(); spend(s, unit(s, 'recon'), 'energy', 10); r = request(s, target_kind='recon')
        self.assertEqual(len(service(s, [r])[1]), 1)
        for air in ('flying', 'taking_off', 'landing'):
            unit(s, 'recon')['air_state'] = air
            self.assertEqual(service(s, [r])[1], [])

    def test_exact_radius_and_capacity_deficit(self):
        s = fixture(); spend(s, unit(s), 'energy', 1)
        unit(s, 'support')['position_mm']['x_mm'] = 1100000
        after, events = service(s, [request(s)]); self.assertEqual(events[0]['amount'], '1')
        unit(s, 'support')['position_mm']['x_mm'] += 1
        self.assertEqual(service(s, [request(s)])[1], [])


class CaptureTests(TestCase):
    def step(self, s, p, *, present=True, resident=True, side='blue'):
        ids = {unit(s, side=side)['unit_id']}
        return k.capture_step(s, p, presence={'P-01': {side: ids}} if present else {},
                              residents={'P-01': {side: ids}} if resident else {})

    def test_neutral_six_steps_income_starts_next_step(self):
        s, p = fixture(), points()
        for i in range(6):
            s, p = self.step(s, p)
        self.assertEqual(p[0]['owner'], 'blue'); self.assertEqual(s['battle_ledger']['blue']['control_steps'], 0)
        s, p = self.step(s, p, present=False, resident=False)
        self.assertEqual(s['battle_ledger']['blue']['control_steps'], 1)

    def test_enemy_requires_two_distinct_six_step_stages(self):
        s, p = fixture(), points('red')
        for _ in range(6):
            s, p = self.step(s, p)
        self.assertEqual(p[0]['owner'], 'neutral'); self.assertEqual(p[0]['progress_steps'], 0)
        for _ in range(6):
            s, p = self.step(s, p)
        self.assertEqual(p[0]['owner'], 'blue')

    def test_moving_presence_suppresses_but_does_not_capture(self):
        s, p = self.step(fixture(), points('red'), resident=False)
        self.assertEqual(p[0]['owner'], 'red'); self.assertEqual(p[0]['progress_steps'], 0)
        self.assertEqual(s['battle_ledger']['red']['control_steps'], 4)

    def test_contest_or_missing_full_resident_resets_progress(self):
        s, p = self.step(fixture(), points()); self.assertEqual(p[0]['progress_steps'], 1)
        s, p = self.step(s, p, resident=False); self.assertEqual(p[0]['progress_steps'], 0)
        s, p = self.step(s, p)
        s, p = k.capture_step(s, p, presence={'P-01': {'blue': {'blue-ground-0'}, 'red': {'red-ground-0'}}},
                              residents={'P-01': {'blue': {'blue-ground-0'}}})
        self.assertTrue(p[0]['contested']); self.assertEqual(p[0]['progress_steps'], 0)

    def test_killed_enemy_presence_still_suppresses_and_dead_resident_cannot_advance(self):
        s = fixture(); k._retire(s, unit(s), 'attack', 'red')
        after, p = self.step(s, points('red'))
        self.assertEqual(p[0]['owner'], 'red'); self.assertEqual(p[0]['progress_steps'], 0)
        self.assertEqual(after['battle_ledger']['red']['control_steps'], 4)

    def test_empty_owned_points_keep_income_and_final_score_uses_full_denominator(self):
        s = fixture(); p = points('blue')
        for _ in range(720):
            s, p = k.capture_step(s, p, presence={}, residents={})
        self.assertEqual(s['battle_ledger']['blue']['control_steps'], 3600)
        self.assertEqual(k.score(s, 'blue')['control'], '70')
        self.assertEqual(k.score(s, 'blue')['total'], '100')


class BarracksTests(TestCase):
    def setup_case(self, count=1):
        s = fixture(count)
        camps = [dict(camp_id=f'blue-camp-{i}', side='blue', position_mm=dict(x_mm=1000000, z_mm=1000000)) for i in range(3)]
        return s, camps

    def requests(self, s):
        return [dict(unit_id=u['unit_id'], life_id=u['life_id'], camp_id='blue-camp-0') for u in s['units'] if u['side'] == 'blue']

    def step(self, s, camps, requests=None, **kwargs):
        defaults = dict(seconds=5, stationary={u['unit_id'] for u in s['units']}, service_clear=lambda *_: True)
        defaults.update(kwargs)
        return k.barracks_step(s, self.requests(s) if requests is None else requests, camps, **defaults)

    def test_six_categories_debit_shared_reserve_and_repair_consumes_material(self):
        s, camps = self.setup_case(); u = unit(s, 'support'); spend(s, u, 'energy', 80); u['durability'] = '20'
        # 货物支出计为历史已消耗，不伪造资源缺口。
        for key, amount in dict(energy=80, ammo=80, repair=80).items():
            u['cargo'][key] = k.exact(k.number(u['cargo'][key]) - amount)
            k._add(s['battle_ledger']['blue']['consumed'], key, amount)
        after, events = self.step(s, camps)
        self.assertEqual(unit(after, 'support')['energy'], '70')
        self.assertEqual(unit(after, 'support')['durability'], '70')
        self.assertEqual(unit(after, 'support')['cargo'], dict(energy='170', ammo='100', repair='70'))
        self.assertEqual(after['battle_ledger']['blue']['reserve'], dict(energy='1100', ammo='540', repair='100'))
        self.assertEqual(len(events), 5)

    def test_quiet_fifteen_seconds_exact_and_refresh_preserves_registration(self):
        s, camps = self.setup_case(); spend(s, unit(s), 'energy', 80); unit(s)['last_damage_seconds'] = 5
        s, events = self.step(s, camps, seconds=15); self.assertEqual(events, [])
        since = unit(s)['barracks_registration']['since_seconds']
        s, events = self.step(s, camps, seconds=20); self.assertTrue(events)
        self.assertEqual(unit(s)['barracks_registration']['since_seconds'], since)

    def test_four_slots_count_units_not_categories_and_round_robin(self):
        s, camps = self.setup_case(3)
        for u in s['units']:
            if u['side'] == 'blue':
                spend(s, u, 'energy', 100)
        s, events = self.step(s, camps)
        self.assertEqual(len({e['target_id'] for e in events}), 4)
        first = {e['target_id'] for e in events}
        s, events = self.step(s, camps, seconds=10)
        self.assertTrue({e['target_id'] for e in events} - first)

    def test_no_delivery_does_not_take_slot_or_use_inventory_and_movement_clears_queue(self):
        s, camps = self.setup_case(); before = deepcopy(s)
        after, events = self.step(s, camps); self.assertEqual(events, []); self.assertEqual(after, before)
        spend(s, unit(s), 'energy', 10); unit(s)['last_damage_seconds'] = 5
        s, events = self.step(s, camps); self.assertIn('barracks_registration', unit(s))
        s, events = self.step(s, camps, stationary=set()); self.assertNotIn('barracks_registration', unit(s))

    def test_target_subset_ignores_unselected_categories(self):
        s, camps = self.setup_case(); u = unit(s); spend(s, u, 'energy', 10); u['durability'] = '50'
        requests = [dict(unit_id=u['unit_id'], life_id=u['life_id'], camp_id='blue-camp-0', targets=dict(energy='100'))]
        after, _ = self.step(s, camps, requests)
        self.assertEqual(unit(after)['energy'], '100'); self.assertEqual(unit(after)['durability'], '50')

    def test_finite_reserve_order_and_no_free_regeneration(self):
        s, camps = self.setup_case(); spend(s, unit(s), 'energy', 100)
        reserve = s['battle_ledger']['blue']; reserve['consumed']['energy'] = '1299'; reserve['reserve']['energy'] = '1'
        after, events = self.step(s, camps)
        self.assertEqual(events[0]['amount'], '1'); self.assertEqual(unit(after)['energy'], '1')
        self.assertEqual(after['battle_ledger']['blue']['reserve']['energy'], '0')
        after, events = self.step(after, camps, seconds=10); self.assertEqual(events, [])

    def test_wrong_life_enemy_camp_and_over_capacity_rejected(self):
        s, camps = self.setup_case(); before = deepcopy(s)
        for patch in (dict(life_id='stale'), dict(camp_id='missing'), dict(targets={'energy': '101'}), dict(targets={'ammo': '1/2'})):
            r = self.requests(s)[:1]; r[0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ServiceError):
                self.step(s, camps, r)
        self.assertEqual(s, before)


class TerminalTests(TestCase):
    def test_no_score_threshold_and_time_limit(self):
        s = fixture(); s['battle_ledger']['blue']['control_steps'] = 3600
        self.assertIsNone(k.terminal(s, 60)); self.assertEqual(k.terminal(s, 3600)['winner'], 'blue')

    def test_zero_energy_ground_still_counts_as_alive(self):
        s = fixture()
        for u in s['units']:
            spend(s, u, 'energy', 100)
        self.assertIsNone(k.terminal(s, 60))

    def test_one_eliminated_loses_regardless_of_high_score(self):
        s = fixture(); s['battle_ledger']['blue']['control_steps'] = 3600
        for u in s['units']:
            if u['side'] == 'blue':
                k._retire(s, u, 'attack', 'red')
        self.assertEqual(k.terminal(s, 5)['winner'], 'red')

    def test_mutual_elimination_compares_exact_score_then_control(self):
        s = fixture()
        for u in s['units']:
            k._retire(s, u, 'attack', 'red' if u['side'] == 'blue' else 'blue')
        self.assertEqual(k.terminal(s, 5), dict(winner='draw', reason='mutual_elimination'))
        s['battle_ledger']['blue']['control_steps'] = 1
        self.assertEqual(k.terminal(s, 5)['winner'], 'blue')
