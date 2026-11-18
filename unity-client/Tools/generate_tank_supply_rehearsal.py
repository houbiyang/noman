#!/usr/bin/env python3
"""Validate/regenerate an isolated backend fixture for the existing Unity map replay.

Not a real-map clearance test or an official match. No DB or network calls.
Run with the project's backend Python environment; --write updates only the replay asset.
"""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import battle_kernel as kernel, battle_replay
from backend.battle_execution import Executor
from backend.tests.test_battle_execution import FixtureGeometry, attack, resupply, state_fixture
from backend.tests.test_battle_kernel import spend, unit

ASSET = ROOT / 'unity-client/Assets/Sandbox/Resources/Replays/tank-supply-preview.json'


def generate():
    definition = json.loads((ROOT / 'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley/map-definition.json').read_text())
    places = {'blue-ground-0': (5500, 5800), 'red-ground-0': (5650, 5800),
              'blue-support-0': (5420, 5800), 'blue-recon-0': (5420, 5750)}
    state = state_fixture()
    state['units'] = [u for u in state['units'] if u['unit_id'] in places]
    state.pop('battle_ledger')
    state = kernel.initialize(state)
    state['manifest']['field_energy_service_policy'] = kernel.FIELD_ENERGY_SERVICE_POLICY
    for u in state['units']:
        x, z = places[u['unit_id']]
        height = definition['heights'][round(z / 50) * 241 + round(x / 50)]
        u['position_mm'] = dict(x_mm=x * 1000, z_mm=z * 1000, height_mm=round(height * 1000))
        u['position'] = dict(x=str(x / 100), y=str(z / 150))
        if u['unit_type'] == 'recon':
            u['air_state'] = 'flying'
            u['position_mm']['height_mm'] += 30000
            spend(state, u, 'energy', Fraction(40))
    # Explicit synthetic clearance. Never use this adapter as real-map acceptance.
    geometry = FixtureGeometry()
    geometry.hover_energy_clear = lambda *_: True
    geometry.flight_position = lambda u: dict(u['position_mm'])
    before = json.dumps(state, sort_keys=True)
    after, record = Executor(geometry)(state, dict(
        blue=[attack(state), resupply(state, kind='recon', quantity='24')], red=[attack(state, 'red')]))
    assert before == json.dumps(state, sort_keys=True), 'input match must not be mutated'
    battle_replay.validate_record(record)
    kernel.validate(after)
    events = [e for f in record['frames'] for e in f['events']]
    for side in ('blue', 'red'):
        assert sum(e['kind'] == 'shot' and e['source_id'] == f'{side}-ground-0' for e in events) == 6
        assert Fraction(unit(after, 'ground', side)['durability']) == 4
    services = [e for e in events if e['kind'] == 'service']
    assert len(record['frames']) == 12 and len(services) == 12
    assert sum(Fraction(e['amount']) for e in services) == 24
    assert Fraction(unit(after, 'recon')['energy']) == 82
    assert Fraction(unit(after, 'support')['cargo']['energy']) == 176
    metadata = dict(label='本页地图预演 · 非正式战果', scope='synthetic_geometry_with_current_map_visuals',
                    map_id=definition['mapId'], map_hash=definition['contentHash'],
                    surface_accepted=False, database_writes=False, simulation_seconds=60,
                    shots=12, energy_delivered='24',
                    limitations='当前地图外观；支撑面、视线及部署合法性使用显式测试适配器，未完成实图验收。',
                    units={u['unit_id']: dict(cargo=u.get('cargo'), position=places[u['unit_id']]) for u in after['units']})
    return dict(metadata=metadata, records=[record])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='replace only the Unity preview resource; never match state')
    args = parser.parse_args()
    result = generate()
    if args.write:
        ASSET.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    else:
        saved = json.loads(ASSET.read_text())
        assert saved['records'] == result['records'], 'asset is stale; inspect changes before using --write'
        assert saved['metadata']['scope'] == result['metadata']['scope']
        assert not saved['metadata']['database_writes'] and not saved['metadata']['surface_accepted']
    print('PASS: 60 seconds; 12 shots; 24 energy delivered; drone=82; stock=176; input unchanged; no DB/network')
