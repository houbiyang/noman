"""用户确认沿用P-01至P-05的独立总册场景草案；绝不重写旧场景或宣称地表已验收。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from backend import battle_capacity, capture_map, rulebook
from backend.demo_domain import MAP_PATH, require
from backend.runtime import sha256

SCENARIO_CODE = 'qinglan-manual-p01-p05'
VERSION = 'qinglan-manual-p01-p05-2026-11-14-v1'


def definition():
    raw = MAP_PATH.read_bytes()
    source = json.loads(raw)
    layout, layout_hash = capture_map.current_layout()
    capture_map.validate(layout, source)
    points = capture_map.objectives(source, layout)
    require([p['point_id'] for p in points] == [f'P-{i:02d}' for i in range(1, 6)],
            'BATTLE_POINT_LAYOUT_CHANGED', '新手动场景须沿用已确认的P-01至P-05，不能静默替换')
    require(source['width'] == 12000 and source['length'] == 12000,
            'BATTLE_MAP_SIZE_INVALID', '总册地图平面应为12000×12000米')
    # 只证实底图身份、半径、桥面有限矩形及圆不交叠；不能把它当路线/服务面验收。
    for i, point in enumerate(points):
        x, z, radius = float(point['x']), float(point['z']), point['radius_m']
        require(radius <= x <= source['width'] - radius and radius <= z <= source['length'] - radius,
                'BATTLE_CAPTURE_OUTSIDE', '据点区域超出地图边界')
        for previous in points[:i]:
            dx, dz = x - float(previous['x']), z - float(previous['z'])
            require(dx * dx + dz * dz > (radius + previous['radius_m']) ** 2,
                    'BATTLE_CAPTURE_OVERLAP', '五据点有效区域不能重叠')
    rules = rulebook.definition()
    capacity = battle_capacity.definition()
    return dict(schema_version='qinglan-manual-scenario-draft-v1', version=VERSION,
        battle_mode='manual', map=dict(map_id=source['mapId'], content_hash=source['contentHash'],
            source_sha256=hashlib.sha256(raw).hexdigest(), width_m=source['width'], length_m=source['length']),
        capture_layout_sha256=layout_hash, capture_points=deepcopy(points),
        rule_version_id=rulebook.identity(rules), rule_payload_sha256=sha256(rules),
        capacity_payload_sha256=sha256(capacity),
        round_seconds=60, maximum_rounds=60, mode_frozen_at_start=True,
        asymmetric_allowed=True, migrate_existing_games=False,
        acceptance=dict(status='draft_not_playable', structural_checks_passed=True,
            terrain_masks_validated=False, ground_and_support_routes_validated=False,
            bridge_actual_grade_validated=False, service_surface_isolation_validated=False,
            blocks=['terrain_and_layer_masks', 'ground_and_support_entry_exit_and_stopping',
                    'actual_bridge_grade', 'barracks_service_isolation', 'rulebook_executor']))


def identity(value):
    return str(uuid5(NAMESPACE_URL, 'qinglan/manual/scenario/' + sha256(value)))


def register(connection):
    value = definition()
    registered_rule = rulebook.register(connection)
    identity_value, digest = identity(value), sha256(value)
    connection.execute('SELECT pg_advisory_xact_lock(80644,1115)')
    existing = connection.execute('''SELECT id,definition,payload_sha256,rule_version_id,availability
        FROM scenario_versions WHERE scenario_code=%s AND version_label=%s''', (SCENARIO_CODE, VERSION)).fetchone()
    if existing:
        require(str(existing['id']) == identity_value and existing['definition'] == value
                and existing['payload_sha256'] == digest
                and str(existing['rule_version_id']) == registered_rule['rule_version_id'],
                'BATTLE_SCENARIO_CONTENT_CHANGED', '同名独立场景内容不同，拒绝覆盖旧版本')
    else:
        connection.execute('''INSERT INTO scenario_versions
            (id,scenario_code,version_label,rule_version_id,schema_version,hash_format_version,
             definition,asset_manifest,payload_sha256,availability,is_demo)
            VALUES (%s,%s,%s,%s,%s,'qiongtu-json-v1',%s,%s,%s,'draft',false)''',
            (identity_value, SCENARIO_CODE, VERSION, value['rule_version_id'], value['schema_version'],
             Jsonb(value), Jsonb(dict(map=value['map'], capture_layout_sha256=value['capture_layout_sha256'])), digest))
    return dict(scenario_version_id=identity_value, payload_sha256=digest,
                inserted=existing is None, availability=existing['availability'] if existing else 'draft')
