"""Read saved actual MapMotion evidence, then stage one real Executor capture round.

This is an in-memory component continuation, NOT a complete HTTP or all-Executor
route replay. No database, live game, deployment or authority file is modified.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from backend import battle_setup, battle_kernel as k
from backend.battle_execution import Executor
from backend.battle_map_surfaces import seal_catalog
from backend.manual_server import load_bundle


def verify(bundle_path, motion_path):
    motion = json.loads(Path(motion_path).read_bytes())
    assert seal_catalog(motion)['payload_sha256'] == motion['payload_sha256']
    result = next(r for r in motion['results'] if r['name'] == 'P-03' and r['side'] == 'blue')
    assert result['success'] and result['final_full_footprint_inside_capture_mask']
    assert all(a['server_finished'] for a in result['arrivals'])
    geometry, request = load_bundle(bundle_path)
    state = battle_setup.build(request, geometry.rules, geometry.scenario, geometry.capacity, geometry)
    executor = Executor(geometry)
    executor.prepare_new_game(state)
    unit = next(u for u in state['units'] if u['unit_id'] == 'B-G01')
    # Explicit staging at the SAVED ACTUAL endpoint/energy; not requested target.
    unit['position_mm'] = deepcopy(result['final_position_mm'])
    unit['position'] = geometry.display_position(unit['position_mm'])
    # Preserve the frozen initial resource denominator and record the exact
    # already-spent route energy, rather than bypassing conservation checks.
    route_energy_spent = k.number(unit['energy']) - k.number(result['final_energy'])
    k.consume_energy(state, unit, route_energy_spent)
    unit['movement'] = None
    unit['task'] = None
    cleared = geometry.clear_movement(deepcopy(unit))
    unit.clear()
    unit.update(cleared)
    initial_unit = deepcopy(unit)
    assert state['battle_ledger']['elapsed_seconds'] == state['manual_battle']['elapsed_seconds'] == 0
    before = deepcopy(next(p for p in state['battle_points'] if p['point_id'] == 'P-03'))
    after, record = executor(state, {'blue': [], 'red': []})
    final = next(u for u in after['units'] if u['unit_id'] == 'B-G01')
    point = next(p for p in after['battle_points'] if p['point_id'] == 'P-03')
    events = [e for frame in record['frames'] for e in frame['events']
              if e.get('kind') == 'capture']
    assert len(record['frames']) == 12 and after['battle_ledger']['elapsed_seconds'] == 60
    assert point['owner'] == 'blue'
    assert final['position_mm'] == initial_unit['position_mm'] and final['energy'] == initial_unit['energy']
    assert final['movement'] is None
    assert any(e.get('source_id') == 'P-03' for e in events), record['frames']
    return seal_catalog(dict(schema_version='qinglan-local-staged-capture-evidence-v1',
        release_scope='local_manual_test', production_ready=False,
        source_motion_sha256=motion['payload_sha256'], source_api_route_sha256=motion['api_route_sha256'],
        binding=deepcopy(geometry.binding), staged_manifest=deepcopy(state['manifest']),
        prior_route_engine='MapMotion', prior_route_steps=len(result['steps']),
        prior_route_simulated_seconds=len(result['steps'])*5,
        continuation_engine='Executor', continuation_seconds=60,
        staging='Fresh validated accepted setup, actual saved endpoint and energy; clocks start at zero. Not a persisted full-state resume.',
        full_http=False, full_route_executor=False, database_access=False,
        submitted_intents={'blue': [], 'red': []}, initial_unit=initial_unit, final_unit=final,
        staged_prior_energy_consumed=str(route_energy_spent), initial_point=before, final_point=point, capture_events=events, replay_record=record, success=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--motion', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output must be a new evidence file')
    evidence = verify(args.bundle, args.motion)
    with args.output.open('x') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2)
    print(json.dumps({key: evidence[key] for key in ('success', 'payload_sha256', 'capture_events', 'final_point')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
