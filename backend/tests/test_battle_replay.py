"""只读动画记录：精确账本、时钟、哈希、身份和播放隔离。无需数据库。"""
from copy import deepcopy
from unittest import TestCase

from pydantic import ValidationError

from backend.battle_replay import ReplayCursor, seal_record, validate_record
from backend.runtime import ServiceError, sha256
from backend.tests.test_manual_battle import world


def visual_unit(unit_id='B-G01', side='blue'):
    return dict(unit_id=unit_id, life_id=unit_id + '-life', side=side, unit_type='ground',
        position=dict(x_mm=1000, z_mm=1000, height_mm=0), durability='100', energy='100',
        ammo='60', max_durability='100', max_energy='100', max_ammo='60',
        base_speed_m_per_minute='400', speed_m_per_minute='0', retired=False, docked=False)


def record_fixture(number=1, previous=None, duration=60, finished=False):
    start = (number - 1) * 60
    initial = deepcopy(previous['frames'][-1]['world']) if previous else world(start)
    if previous is None:
        initial['units'] = [visual_unit(), visual_unit('R-G01', 'red')]
    frames = []
    for second in range(start + 5, start + duration + 1, 5):
        current = deepcopy(initial)
        current['elapsed_seconds'] = second
        events = []
        if second >= start + 10:
            current['units'][1]['durability'] = '84'
            current['units'][0]['ammo'] = '59'
        if second == start + 10:
            events = [dict(event_id=f'{number}-hit', kind='hit', at_seconds=second,
                source_id='B-G01', source_life_id='B-G01-life', target_id='R-G01', target_life_id='R-G01-life',
                source_position=deepcopy(current['units'][0]['position']),
                target_position=deepcopy(current['units'][1]['position']))]
        frames.append(dict(at_seconds=second, world=current, events=events))
    return seal_record(round_number=number, rule_sha256='a' * 64, scenario_sha256='b' * 64,
        previous_round_sha256=previous['payload_sha256'] if previous else ('c' * 64 if number > 1 else None),
        initial_world=initial, frames=frames, finished=finished)


def reseal(value):
    value['before_world_sha256'] = sha256(value['initial_world'])
    value['after_world_sha256'] = sha256(value['frames'][-1]['world'])
    value['payload_sha256'] = sha256({k: v for k, v in value.items() if k != 'payload_sha256'})
    return value


class BattleReplayTests(TestCase):
    def assert_code(self, code, function, *args):
        with self.assertRaises(ServiceError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def test_local_placement_preserves_prefix_and_can_precede_actual_movement(self):
        prev = record_fixture()
        r = record_fixture(2, prev)
        unit = r['initial_world']['units'][0]
        origin = unit['position']; placed = dict(origin, x_mm=5000); end = dict(placed, x_mm=5100)
        for frame in r['frames']:
            frame['world']['units'][0]['position'] = end
        frame = r['frames'][0]
        identity = dict(source_id=unit['unit_id'], source_life_id=unit['life_id'], at_seconds=65)
        frame['events'] = [dict(identity, event_id='place', kind='host_place', source_position=origin,
                                target_position=placed),
                           dict(identity, event_id='real-move', kind='move', trace=[placed, end])]
        ReplayCursor([prev, reseal(r)])
        frame['events'][1]['trace'] = [origin, end]
        self.assert_code('REPLAY_TRACE_INVALID', validate_record, reseal(r))
        frame['events'] = []
        self.assert_code('REPLAY_TRACE_INVALID', validate_record, reseal(r))

    def test_exact_fractional_resources_and_five_frozen_points(self):
        value = record_fixture()
        value['frames'][0]['world']['units'][0].update(durability='492/5', energy='97.125')
        reseal(value)
        parsed = validate_record(value)
        self.assertEqual(parsed.frames[0].world.units[0].durability, '492/5')
        self.assertEqual(len(parsed.frames), 12)

    def test_payload_and_world_hash_tampering_rejected(self):
        value = record_fixture(); value['frames'][0]['world']['red_score'] = '120'
        self.assert_code('REPLAY_HASH_MISMATCH', validate_record, value)
        value = record_fixture(); value['after_world_sha256'] = 'f' * 64
        value['payload_sha256'] = sha256({k: v for k, v in value.items() if k != 'payload_sha256'})
        self.assert_code('REPLAY_HASH_MISMATCH', validate_record, value)

    def test_missing_duplicate_reversed_frames_rejected_even_if_rehashed(self):
        for mutation in ('missing', 'duplicate', 'reverse'):
            value = record_fixture()
            if mutation == 'missing': del value['frames'][2]
            elif mutation == 'duplicate': value['frames'][2] = deepcopy(value['frames'][1])
            else: value['frames'][1:3] = reversed(value['frames'][1:3])
            self.assert_code('REPLAY_TIME_INVALID', validate_record, reseal(value))

    def test_life_side_type_and_reinforcement_cannot_change(self):
        for field, new_value in (('life_id', 'new-life'), ('side', 'red'), ('unit_type', 'recon')):
            value = record_fixture(); value['frames'][0]['world']['units'][0][field] = new_value
            self.assert_code('REPLAY_IDENTITY_CHANGED', validate_record, reseal(value))
        value = record_fixture(); value['frames'][0]['world']['units'].append(visual_unit('B-G02'))
        self.assert_code('REPLAY_IDENTITY_CHANGED', validate_record, reseal(value))

    def test_events_require_actual_life_endpoints_unique_id_and_frame_time(self):
        for change in ('wrong_life', 'wrong_time', 'duplicate'):
            value = record_fixture(); event = value['frames'][1]['events'][0]
            if change == 'wrong_life': event['target_life_id'] = 'old-life'
            elif change == 'wrong_time': event['at_seconds'] = 5
            else: value['frames'][1]['events'].append(deepcopy(event))
            self.assert_code('REPLAY_EVENT_INVALID', validate_record, reseal(value))
        value = record_fixture(); del value['frames'][1]['events'][0]['source_position']
        with self.assertRaises(ValidationError): validate_record(reseal(value))

    def test_service_visual_requires_real_resource_and_amount(self):
        value = record_fixture(); event = value['frames'][1]['events'][0]; event['kind'] = 'service'
        with self.assertRaises(ValidationError): validate_record(reseal(value))
        event.update(resource='energy', amount='2')
        validate_record(reseal(value))

    def test_invalid_numbers_float_coordinates_duplicate_points_rejected(self):
        for invalid in ('NaN', 'Infinity', '-1', '1/0', '10001', '1e2'):
            value = record_fixture(); value['initial_world']['units'][0]['energy'] = invalid
            with self.assertRaises(ValidationError): validate_record(reseal(value))
        value = record_fixture(); value['frames'][0]['world']['units'][0]['position']['x_mm'] = 1.5
        with self.assertRaises(ValidationError): validate_record(reseal(value))
        value = record_fixture(); value['initial_world']['capture_points'][0]['point_id'] = 'P-02'
        with self.assertRaises(ValidationError): validate_record(reseal(value))

    def test_capacity_full_sixty_units_and_overflow(self):
        value = record_fixture()
        for current in [value['initial_world']] + [f['world'] for f in value['frames']]:
            current['units'] = [visual_unit('U%02d' % i, 'blue' if i < 30 else 'red') for i in range(60)]
        value['frames'][1]['events'] = []
        validate_record(reseal(value))
        value['initial_world']['units'].append(visual_unit('overflow'))
        with self.assertRaises(ValidationError): validate_record(reseal(value))

    def test_early_finish_and_deadline(self):
        validate_record(record_fixture(duration=5, finished=True))
        with self.assertRaises(ServiceError): record_fixture(duration=5)
        with self.assertRaises(ServiceError): record_fixture(number=60)
        last = record_fixture(number=60, finished=True)
        last['previous_round_sha256'] = 'c' * 64
        validate_record(reseal(last))

    def test_chain_rejects_wrong_version_hash_world_gap_and_after_finish(self):
        first = record_fixture(); second = record_fixture(2, first)
        ReplayCursor([first, second])
        for change in ('version', 'hash', 'world', 'gap'):
            modified = deepcopy(second)
            if change == 'version': modified['scenario_sha256'] = 'c' * 64
            elif change == 'hash': modified['previous_round_sha256'] = 'c' * 64
            elif change == 'world': modified['initial_world']['blue_score'] = '1'
            else: modified = record_fixture(3, second)
            self.assert_code('REPLAY_CHAIN_INVALID', ReplayCursor, [first, reseal(modified)])
        ended = record_fixture(finished=True)
        self.assert_code('REPLAY_CHAIN_INVALID', ReplayCursor, [ended, record_fixture(2, ended)])

    def test_no_future_hp_is_applied_and_seek_does_not_reemit_events(self):
        cursor = ReplayCursor([record_fixture()])
        self.assertEqual(cursor.seek(9.99)['units'][1]['durability'], '100')
        self.assertEqual(cursor.seek(10)['units'][1]['durability'], '84')
        cursor.seek(5); cursor.play()
        self.assertEqual(len(cursor.advance(5)['events']), 1)
        self.assertEqual(cursor.advance(0)['events'], [])
        self.assertEqual(cursor.advance(1)['events'], [])
        self.assertEqual(cursor.seek(0)['units'][1]['durability'], '100')

    def test_pause_continuous_play_end_and_defensive_copy(self):
        first = record_fixture(); second = record_fixture(2, first); before = deepcopy([first, second])
        cursor = ReplayCursor([first, second]); cursor.play()
        cursor.advance(59); cursor.pause()
        self.assertEqual(cursor.advance(500)['seconds'], 59)
        cursor.play(); progress = cursor.advance(11)
        self.assertEqual(progress['seconds'], 70)
        self.assertEqual(len(progress['events']), 1)
        result = cursor.advance(500)
        self.assertFalse(result['playing']); self.assertEqual(result['seconds'], 120)
        self.assertEqual(cursor.advance(500)['events'], [])
        result['world']['units'].clear()
        self.assertEqual(len(cursor.seek(120)['units']), 2)
        self.assertEqual([first, second], before)

    def test_invalid_playback_inputs_rejected(self):
        self.assert_code('REPLAY_EMPTY', ReplayCursor, [])
        cursor = ReplayCursor([record_fixture()])
        for value in (float('nan'), float('inf'), -1, 61, True):
            self.assert_code('REPLAY_SEEK_INVALID', cursor.seek, value)
        for value in (float('nan'), float('inf'), -1, True):
            self.assert_code('REPLAY_DELTA_INVALID', cursor.advance, value)

    def test_initial_resources_cannot_exceed_frozen_capacity(self):
        value = record_fixture(); value['initial_world']['units'][0]['energy'] = '101'
        self.assert_code('REPLAY_RESOURCE_INVALID', validate_record, reseal(value))

    def test_move_trace_must_join_authoritative_worlds_once(self):
        value = record_fixture(); unit = value['initial_world']['units'][0]
        event = dict(event_id='move-5', kind='move', at_seconds=5, source_id=unit['unit_id'],
                     source_life_id=unit['life_id'], trace=[deepcopy(unit['position']), deepcopy(unit['position'])])
        value['frames'][0]['events'] = [event]
        validate_record(reseal(value))
        bad = deepcopy(value); bad['frames'][0]['events'][0]['trace'][0]['x_mm'] += 1
        self.assert_code('REPLAY_TRACE_INVALID', validate_record, reseal(bad))
        duplicate = deepcopy(event); duplicate['event_id'] = 'move-5-again'
        value['frames'][0]['events'].append(duplicate)
        self.assert_code('REPLAY_TRACE_INVALID', validate_record, reseal(value))

    def test_cross_round_event_id_cannot_repeat(self):
        first = record_fixture(); second = record_fixture(2, first)
        second['frames'][1]['events'][0]['event_id'] = first['frames'][1]['events'][0]['event_id']
        self.assert_code('REPLAY_EVENT_INVALID', ReplayCursor, [first, reseal(second)])

    def test_retired_life_never_reappears(self):
        value = record_fixture(); value['frames'][0]['world']['units'][0]['retired'] = True
        self.assert_code('REPLAY_IDENTITY_CHANGED', validate_record, reseal(value))

    def test_flight_transition_requires_authoritative_endpoints(self):
        value = record_fixture(); unit = value['initial_world']['units'][0]
        event = dict(event_id='takeoff-5', kind='takeoff', at_seconds=5, source_id=unit['unit_id'], source_life_id=unit['life_id'])
        value['frames'][0]['events'] = [event]
        with self.assertRaises(ValidationError): validate_record(reseal(value))
        event.update(source_position=deepcopy(unit['position']), target_position=deepcopy(unit['position']))
        validate_record(reseal(value))
        event['target_position']['height_mm'] = 1000
        self.assert_code('REPLAY_TRACE_INVALID', validate_record, reseal(value))

    def test_barracks_visual_is_not_unit_service_and_requires_real_category(self):
        value = record_fixture()
        event = value['frames'][1]['events'][0]
        event.update(kind='barracks_service', camp_id='B-C01', delivery_category='stock_ammo', resource='ammo', amount='2')
        event.pop('source_id'); event.pop('source_life_id')
        validate_record(reseal(value))
        for fields in (dict(camp_id=None), dict(delivery_category='stock_energy'), dict(amount='0'),
                       dict(source_id='B-G01'), dict(source_life_id='fake-life')):
            bad = deepcopy(value); bad['frames'][1]['events'][0].update(fields)
            with self.assertRaises(ValidationError): validate_record(reseal(bad))
        bad = deepcopy(value); bad['frames'][1]['events'][0]['target_position']['x_mm'] += 1
        self.assert_code('REPLAY_EVENT_INVALID', validate_record, reseal(bad))

    def test_non_camp_visual_cannot_smuggle_camp_id(self):
        value = record_fixture(); value['frames'][1]['events'][0]['camp_id'] = 'B-C01'
        with self.assertRaises(ValidationError): validate_record(reseal(value))
