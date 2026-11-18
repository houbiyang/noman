"""Verify accepted real-map deployment -> HTTP rounds -> capture -> persisted replay.

Creates and drops only a random isolated test database. Never edits deployment,
positions, resources, live games, or geometry. The next route batch is submitted
only after the authoritative movement channel clears, not by coordinate epsilon.
"""
from pathlib import Path
import argparse
from copy import deepcopy
import json
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'unity-client/Tools')]
from verify_real_manual_flow import (isolated_database, register_verified_sources,
    db_digest, bind_intents, need, write_json)
from verify_manual_replay import verify
from backend.manual_server import load_bundle
from backend.battle_map_surfaces import seal_catalog
from backend.main import create_app
from fastapi.testclient import TestClient


def run(bundle, route_path, output, *, replace_second_round=False):
    output.mkdir(parents=True, exist_ok=False)
    route = json.loads(route_path.read_bytes())
    need(seal_catalog(route)['payload_sha256'] == route['payload_sha256'], 'ROUTE_DIGEST')
    batches = route['api_route']['batches']
    geometry, request = load_bundle(bundle)
    need(request is not None, 'ACCEPTED_SETUP_REQUIRED')
    rounds, records = [], []
    with isolated_database() as (settings, admin):
        register_verified_sources(settings, admin, geometry, 'local_manual_test')
        def new_client():
            app = create_app(settings)
            app.state.service.install_manual_geometry(geometry)
            return TestClient(app)
        client = new_client()
        def http(method, path, status=200, **kwargs):
            response = client.request(method, '/api/v1' + path, **kwargs)
            if response.status_code != status:
                raise RuntimeError(f'{method} {path}: {response.status_code} {response.text[:1000]}')
            return response.json()
        try:
            bootstrap = {'X-Bootstrap-Key': settings.bootstrap_key}
            game = http('POST', '/battle/games', 201, headers={**bootstrap, 'Idempotency-Key': str(uuid4())}, json=request.model_dump(mode='json'))
            base = '/games/' + game['game_id']
            seats = {role: {'Authorization': 'Bearer ' + http('POST', '/bootstrap' + base + '/sessions', headers=bootstrap, json={'role': role})['access_token']} for role in ('blue', 'red', 'host')}
            def view(role='host'):
                return http('GET', base + '/view', headers=seats[role])
            def moving():
                # Test-only read of persisted authority, never a mutation or staging.
                with settings.connect() as connection:
                    state = connection.execute('SELECT s.state_payload FROM games g JOIN game_snapshots s ON s.id=g.head_snapshot_id WHERE g.id=%s', (game['game_id'],)).fetchone()['state_payload']
                return bool(next(u for u in state['units'] if u['unit_id'] == 'B-G01').get('movement'))
            def command(role, kind, payload):
                envelope = dict(context=view(role)['context'], command_type=kind, payload=payload)
                headers = {**seats[role], 'Idempotency-Key': str(uuid4())}
                result = http('POST', base + '/commands', headers=headers, json=envelope)
                return envelope, headers, result
            batch_index = 0
            awaiting = False
            for number in range(1, 31):
                own = view('blue')
                unit = next(u for u in own['units'] if u['unit_id'] == 'B-G01')
                if number == 2 and replace_second_round:
                    need(awaiting and moving(), 'REPLAN_REQUIRES_ACTIVE_MOVEMENT')
                    batch_index += 1
                    awaiting = False
                if awaiting and not moving():
                    batch_index += 1
                    awaiting = False
                if batch_index < len(batches) and not awaiting:
                    item = deepcopy(batches[batch_index]['intent'])
                    command('blue', 'battle_draft', dict(round_number=number, intents=bind_intents([item], own)))
                    awaiting = True
                for side in ('blue', 'red'):
                    command(side, 'battle_submit', dict(round_number=number))
                started = time.monotonic()
                envelope, headers, _ = command('host', 'battle_resolve', dict(round_number=number))
                duration = time.monotonic() - started
                state = view()
                unit = next(u for u in state['units'] if u['unit_id'] == 'B-G01')
                point = next(p for p in state['battle_capture_points'] if p['point_id'] == 'P-03')
                row = dict(round_number=number, batch_number=min(batch_index+1,len(batches)), elapsed_seconds=state['manual_battle']['elapsed_seconds'], wall_seconds=round(duration,3), unit=unit, point=point)
                need(row['elapsed_seconds'] == 60*number, 'ROUND_CLOCK')
                rounds.append(row)
                write_json(output / 'progress.json', dict(rounds=rounds))
                print(json.dumps({k: row[k] for k in ('round_number','batch_number','elapsed_seconds','wall_seconds','point')}, ensure_ascii=False), flush=True)
                # A fresh service reads the persisted game; no in-memory state injection.
                if number == 2:
                    before = db_digest(settings)
                    client.close(); client = new_client()
                    need(view()['manual_battle']['elapsed_seconds'] == 120, 'RESTART_CLOCK')
                    http('POST', base+'/commands', headers=headers, json=envelope)
                    need(db_digest(settings) == before, 'RESOLVE_RETRY_WRITES')
                if point['owner'] == 'blue' and batch_index == len(batches)-1 and not moving():
                    break
            else:
                raise RuntimeError('ROUTE_CAPTURE_NOT_COMPLETED_IN_30_ROUNDS')
            before = db_digest(settings)
            client.close(); client = new_client()
            index = http('GET', base + '/battle/rounds', headers=seats['host'])
            need(len(index['items']) == len(rounds), 'ROUND_INDEX')
            for n in range(1, len(rounds)+1):
                records.append(http('GET', base + f'/battle/rounds/{n}', headers=seats['host'])['record'])
            for side in ('red', 'blue'):
                http('GET', base + '/battle/rounds', 403, headers=seats[side])
            proof = verify(records, required_events=['move', 'capture'])
            need(db_digest(settings) == before, 'REPLAY_WRITES')
            need(records[-1]['end_seconds'] == 60*len(rounds), 'REPLAY_CLOCK')
            write_json(output / 'replay-cumulative.json', dict(records=records))
            report = dict(passed=True, scope='local_manual_test', competitive_ready=False,
                actual_deployment=True, deployment_to_capture_http_passed=True,
                replaced_movement_in_round_two=replace_second_round,
                position_or_resource_staging=False, http_rounds=len(rounds),
                simulated_seconds=60*len(rounds), source_route_sha256=route['payload_sha256'],
                final_unit=rounds[-1]['unit'], final_point=rounds[-1]['point'],
                restart_and_idempotent_retry=True, replay_read_only=True, side_replay_forbidden=True,
                replay_verification=proof, round_wall_seconds=[r['wall_seconds'] for r in rounds])
        finally:
            client.close()
    report['isolated_database_removed'] = True
    write_json(output / 'report.json', report)
    print(json.dumps({k: report[k] for k in ('passed','http_rounds','simulated_seconds','isolated_database_removed')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--route', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--replace-second-round', action='store_true', help='Replace the active first route with batch 2 in round 2, reproducing the long-leg regression without staging')
    args = parser.parse_args()
    run(args.bundle, args.route, args.output, replace_second_round=args.replace_second_round)
