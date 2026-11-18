"""用户确认沿用P-01至P-05的独立总册场景草案；绝不重写旧场景或宣称地表已验收。"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from backend import battle_capacity, battle_kernel, capture_map, rulebook
from backend.demo_domain import MAP_PATH, require
from backend.runtime import sha256

SCENARIO_CODE = 'qinglan-manual-p01-p05'
VERSION = 'qinglan-manual-p01-p05-2026-11-14-v1'
HOVER_ENERGY_DRAFT_VERSION = 'qinglan-manual-p01-p05-2026-11-17-hover-energy-v1'
HOVER_ENERGY_CASE = 'hover_energy_service'


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


def hover_energy_definition():
    """新批准例外的可复现服务端草案。保留旧definition内容/UUID，绝不造验收。"""
    value = definition()
    value.update(version=HOVER_ENERGY_DRAFT_VERSION,
                 field_energy_service_policy=battle_kernel.FIELD_ENERGY_SERVICE_POLICY)
    value['acceptance']['blocks'].append(HOVER_ENERGY_CASE)
    return value


def identity(value):
    return str(uuid5(NAMESPACE_URL, 'qinglan/manual/scenario/' + sha256(value)))


def register(connection, *, hover_energy=False):
    # 仅显式离线调用可登记新草案；服务启动仍沿原默认，不隐式写库。
    value = hover_energy_definition() if hover_energy else definition()
    registered_rule = rulebook.register(connection)
    identity_value, digest = identity(value), sha256(value)
    connection.execute('SELECT pg_advisory_xact_lock(80644,1115)')
    existing = connection.execute('''SELECT id,definition,payload_sha256,rule_version_id,availability
        FROM scenario_versions WHERE scenario_code=%s AND version_label=%s''', (SCENARIO_CODE, value['version'])).fetchone()
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
            (identity_value, SCENARIO_CODE, value['version'], value['rule_version_id'], value['schema_version'],
             Jsonb(value), Jsonb(dict(map=value['map'], capture_layout_sha256=value['capture_layout_sha256'])), digest))
    return dict(scenario_version_id=identity_value, payload_sha256=digest,
                inserted=existing is None, availability=existing['availability'] if existing else 'draft')


def accepted(value):
    """发布状态不是客户端选项；所有总册地图验收项均须明确完成。"""
    acceptance = value.get('acceptance', {})
    return ('release_scope' not in value and acceptance.get('status') == 'accepted' and acceptance.get('blocks') == []
        and all(acceptance.get(key) is True for key in (
            'structural_checks_passed', 'terrain_masks_validated', 'ground_and_support_routes_validated',
            'bridge_actual_grade_validated', 'service_surface_isolation_validated')))


# 独立实地图报告至少覆盖这些行为，而非仅存在Geometry对象。报告由受控离线验收器
# 写入服务端文件；HTTP创建请求不接受报告/accepted字段。此处不制造通过报告。
REQUIRED_ACCEPTANCE_CASES = frozenset((
    'source_integrity', 'deployment_full_footprints', 'ground_sweeps_and_energy',
    'enemy_simultaneous_collision', 'bridge_discontinuity_rejected',
    'observer_and_shooter_rays', 'forest_connected_penetration',
    'service_continuous_surface', 'capture_effective_layer_and_residence',
    'airspace_and_landing_conflict', 'manual_round_persistence_and_recovery',
    'sixty_round_terminal_and_role_isolation'))


def service_policy_valid(scenario):
    """旧发布内容缺字段才是旧规则；例外必须带新场景版本。"""
    return ('field_energy_service_policy' not in scenario
            or (scenario['field_energy_service_policy'] == battle_kernel.FIELD_ENERGY_SERVICE_POLICY
                and isinstance(scenario.get('version'), str) and bool(scenario['version'])
                and scenario['version'] != VERSION))


def service_policy_matches(manifest, scenario):
    return (service_policy_valid(scenario)
            and ('field_energy_service_policy' in manifest) == ('field_energy_service_policy' in scenario)
            and manifest.get('field_energy_service_policy') == scenario.get('field_energy_service_policy'))


def acceptance_evidence_valid(report, *, field_energy_service_policy=None):
    """只验证受控报告结构/内容绑定，不能代替执行报告中的真实场景用例。"""
    import re
    if not isinstance(report, dict):
        return False
    required = REQUIRED_ACCEPTANCE_CASES
    if field_energy_service_policy is not None:
        if (field_energy_service_policy != battle_kernel.FIELD_ENERGY_SERVICE_POLICY
                or report.get('field_energy_service_policy') != field_energy_service_policy):
            return False
        required = required | {HOVER_ENERGY_CASE}
    elif 'field_energy_service_policy' in report:
        return False
    cases = report.get('cases')
    if not isinstance(cases, list) or len(cases) != len(required):
        return False
    if any(not isinstance(c, dict) or not isinstance(c.get('case_id'), str) for c in cases):
        return False
    if {c['case_id'] for c in cases} != required:
        return False
    return all(isinstance(c, dict) and c.get('passed') is True
               and c.get('source') == 'real_map'
               and isinstance(c.get('evidence_sha256'), str)
               and re.fullmatch('[0-9a-f]{64}', c['evidence_sha256']) is not None for c in cases)


def acceptance_record_matches(record, case_id, *, geometry_binding, scenario):
    """绑定独立原始证据；新例外不能重用不含明确策略的普通补给证据。"""
    if not (isinstance(record, dict) and record.get('case_id') == case_id
            and record.get('passed') is True and record.get('source') == 'real_map'
            and record.get('geometry_binding') == geometry_binding
            and record.get('map_source_sha256') == scenario['map']['source_sha256']):
        return False
    if case_id == HOVER_ENERGY_CASE:
        return (scenario.get('field_energy_service_policy') == battle_kernel.FIELD_ENERGY_SERVICE_POLICY
                and service_policy_matches(record, scenario))
    return True


def register_accepted(connection, geometry):
    """显式离线发布，使用已绑定真实来源及独立验收的完整适配器；从不由启动隐式调用。

    不更新旧草案/旧局；发布内容寻址的新版本。规则与容量须已独立发布，不自动抬升。
    应在调用方事务中执行，失败全量回滚。无真实报告时必然拒绝。
    """
    from backend.battle_map_geometry import MapGeometry
    require(isinstance(geometry, MapGeometry), 'BATTLE_ENGINE_NOT_READY', '发布需要完整真实Geometry', 503)
    geometry._verify_sources()
    value = deepcopy(geometry.scenario)
    digest = sha256(value); identity_value = identity(value)
    require(value['version'] != VERSION, 'BATTLE_SCENARIO_VERSION_REQUIRED', '验收版本不能覆盖既有草案', 409)
    connection.execute('SELECT pg_advisory_xact_lock(80644,1115)')
    for source, source_id in ((geometry.rules, rulebook.identity(geometry.rules)),
                              (geometry.capacity, battle_capacity.identity(geometry.capacity))):
        row = connection.execute('SELECT definition,payload_sha256,availability FROM rule_versions WHERE id=%s FOR SHARE',
            (source_id,)).fetchone()
        require(row and row['definition'] == source and row['payload_sha256'] == sha256(source)
                and row['availability'] == 'active', 'BATTLE_VERSION_NOT_READY', '规则或容量未发布/内容不符', 503)
    existing = connection.execute('''SELECT id,definition,payload_sha256,availability FROM scenario_versions
        WHERE scenario_code=%s AND version_label=%s FOR UPDATE''', (SCENARIO_CODE, value['version'])).fetchone()
    if existing:
        require(str(existing['id']) == identity_value and existing['definition'] == value
                and existing['payload_sha256'] == digest and existing['availability'] == 'active',
                'BATTLE_SCENARIO_CONTENT_CHANGED', '同名发布版本已有不同内容，不允许覆盖', 409)
    else:
        connection.execute('''INSERT INTO scenario_versions
            (id,scenario_code,version_label,rule_version_id,schema_version,hash_format_version,
             definition,asset_manifest,payload_sha256,availability,is_demo)
            VALUES (%s,%s,%s,%s,%s,'qiongtu-json-v1',%s,%s,%s,'active',false)''',
            (identity_value, SCENARIO_CODE, value['version'], value['rule_version_id'], value['schema_version'],
             Jsonb(value), Jsonb(dict(map=value['map'], capture_layout_sha256=value['capture_layout_sha256'],
                 geometry_binding=value['geometry_binding'], independent_acceptance_sha256=value['independent_acceptance_sha256'])), digest))
    return dict(scenario_version_id=identity_value, payload_sha256=digest, availability='active', inserted=not bool(existing))


LOCAL_MANUAL_SCOPE = 'local_manual_test'
LOCAL_MANUAL_CASES = frozenset((
    'source_integrity', 'deployment_full_footprints', 'ground_sweeps_and_energy',
    'observer_and_shooter_rays', 'service_continuous_surface',
    'airspace_and_landing_conflict', 'manual_round_persistence_and_recovery',
    'sixty_round_terminal_and_role_isolation'))


def local_manual_definition(surface, interactions, *, version, limitations):
    """由真实输入生成离线候选，不宣称验收；scope与正式发布严格区分。"""
    require(isinstance(version, str) and version.strip() and version not in (VERSION, HOVER_ENERGY_DRAFT_VERSION),
            'BATTLE_SCENARIO_VERSION_REQUIRED', '本地测试必须使用独立版本标签', 409)
    require(isinstance(limitations, list) and limitations
            and all(isinstance(item, str) and item.strip() for item in limitations),
            'BATTLE_LOCAL_LIMITATIONS_REQUIRED', '局部测试必须公开未覆盖范围', 409)
    value = hover_energy_definition()
    value.update(version=version, release_scope=LOCAL_MANUAL_SCOPE, competitive_ready=False,
                 limitations=deepcopy(limitations),
                 geometry_binding=dict(surface_catalog_sha256=surface['payload_sha256'],
                    interactions_sha256=interactions['payload_sha256'], geometry_sha256=surface['geometry_sha256'],
                    base_annotations_sha256=surface['base_annotations_sha256']))
    value['acceptance']['status'] = 'local_manual_test_candidate'
    value['acceptance']['blocks'] = ['local_manual_real_evidence']
    return value


def local_manual_evidence_valid(report, scenario):
    import re
    required = LOCAL_MANUAL_CASES | ({HOVER_ENERGY_CASE} if 'field_energy_service_policy' in scenario else set())
    if not (isinstance(report, dict) and report.get('schema_version') == 'qinglan-local-manual-acceptance-v1'
            and report.get('environment') == 'isolated_real_map' and report.get('passed') is True
            and report.get('release_scope') == LOCAL_MANUAL_SCOPE and report.get('competitive_ready') is False
            and report.get('geometry_binding') == scenario.get('geometry_binding')
            and service_policy_matches(report, scenario)):
        return False
    cases = report.get('cases')
    return (isinstance(cases, list) and len(cases) == len(required)
            and all(isinstance(c, dict) and isinstance(c.get('case_id'), str) for c in cases)
            and {c['case_id'] for c in cases} == required
            and all(c.get('passed') is True and c.get('source') == 'real_map'
                    and isinstance(c.get('evidence_sha256'), str)
                    and re.fullmatch('[0-9a-f]{64}', c['evidence_sha256']) is not None for c in cases))


def freeze_local_manual(candidate, report):
    require(candidate.get('release_scope') == LOCAL_MANUAL_SCOPE
            and candidate.get('competitive_ready') is False
            and local_manual_evidence_valid(report, candidate),
            'BATTLE_LOCAL_EVIDENCE_REQUIRED', '本地场景需要全部实际规则验收报告，不能用正式或合成报告代替', 503)
    value = deepcopy(candidate)
    value['acceptance'].update(status='local_manual_test_accepted', blocks=[])
    value['local_manual_acceptance'] = deepcopy(report)
    value['local_manual_acceptance_sha256'] = sha256(report)
    return value


def local_manual_accepted(value):
    return (value.get('battle_mode') == 'manual' and value.get('release_scope') == LOCAL_MANUAL_SCOPE
            and value.get('competitive_ready') is False and service_policy_valid(value)
            and isinstance(value.get('limitations'), list) and bool(value['limitations'])
            and all(isinstance(item, str) and item.strip() for item in value['limitations'])
            and value.get('acceptance', {}).get('status') == 'local_manual_test_accepted'
            and value['acceptance'].get('blocks') == []
            and local_manual_evidence_valid(value.get('local_manual_acceptance'), value)
            and value.get('local_manual_acceptance_sha256') == sha256(value['local_manual_acceptance']))


def manual_accepted(value):
    return accepted(value) or local_manual_accepted(value)


def release_scope_matches(manifest, scenario):
    return (('release_scope' in manifest) == ('release_scope' in scenario)
            and manifest.get('release_scope') == scenario.get('release_scope')
            and ('competitive_ready' in manifest) == ('competitive_ready' in scenario)
            and manifest.get('competitive_ready') == scenario.get('competitive_ready')
            and ('limitations' in manifest) == ('limitations' in scenario)
            and manifest.get('limitations') == scenario.get('limitations'))


def register_local_manual(connection, geometry):
    """显式离线发布本地测试资格；只插独立版本/激活本次新插入来源，不改既有行或对局。"""
    from backend.battle_map_geometry import LocalManualMapGeometry
    require(type(geometry) is LocalManualMapGeometry, 'BATTLE_LOCAL_SCOPE_REQUIRED',
            '本地登记只接受完整真实且已验收的本地适配器', 503)
    return _register_local_geometry(connection, geometry)


def require_acceptance_database(connection, expected=None):
    """候选只能运行在UUID随机命名隔离库；绝不以用户配置别名绕过实际库名。"""
    import re
    row = connection.execute('SELECT current_database() AS database_name').fetchone()
    actual = row['database_name'] if row else None
    require(isinstance(actual, str) and re.fullmatch(r'qiongtu_manual_test_[0-9a-f]{32}', actual)
            and (expected is None or expected == actual), 'BATTLE_ACCEPTANCE_DATABASE_REQUIRED',
            '真实候选仅能在本次随机隔离验收数据库中执行，不允许用户库', 503)
    return actual


def register_acceptance_candidate(connection, geometry):
    """不发布资格：仅在独立随机库登记真实候选以执行实际HTTP持久验收。"""
    from backend.battle_map_geometry import OfflineMapGeometry
    require(type(geometry) is OfflineMapGeometry, 'BATTLE_LOCAL_SCOPE_REQUIRED', '候选入口仅接受真实离线候选', 503)
    require_acceptance_database(connection)
    return _register_local_geometry(connection, geometry)


def _register_local_geometry(connection, geometry):
    geometry._verify_sources()
    value = deepcopy(geometry.scenario)
    registered_rule = rulebook.register(connection)
    registered_capacity = battle_capacity.register(connection)
    require(registered_rule['rule_version_id'] == value['rule_version_id'],
            'BATTLE_VERSION_NOT_READY', '本地场景引用了不同总册', 503)
    connection.execute('SELECT pg_advisory_xact_lock(80644,1115)')
    for payload, source_id, registration in ((geometry.rules, rulebook.identity(geometry.rules), registered_rule),
                               (geometry.capacity, battle_capacity.identity(geometry.capacity), registered_capacity)):
        row = connection.execute('SELECT definition,payload_sha256,availability FROM rule_versions WHERE id=%s FOR UPDATE',
                                 (source_id,)).fetchone()
        require(row and row['definition'] == payload and row['payload_sha256'] == sha256(payload),
                'BATTLE_VERSION_NOT_READY', '本地发布所需原始规则/容量登记不符', 503)
        if row['availability'] != 'active':
            require(registration['inserted'], 'BATTLE_VERSION_NOT_READY',
                    '既有规则/容量未active；本地发布不能改写旧行可用性', 503)
            connection.execute("UPDATE rule_versions SET availability='active' WHERE id=%s", (source_id,))
    row = connection.execute('''SELECT id,definition,payload_sha256,availability FROM scenario_versions
        WHERE scenario_code=%s AND version_label=%s FOR UPDATE''',
        (SCENARIO_CODE + '-local-test', value['version'])).fetchone()
    if row:
        require(str(row['id']) == identity(value) and row['definition'] == value
                and row['payload_sha256'] == sha256(value) and row['availability'] == 'active',
                'BATTLE_SCENARIO_CONTENT_CHANGED', '同名本地版本已存在不同内容，不能覆盖', 409)
    else:
        connection.execute('''INSERT INTO scenario_versions
            (id,scenario_code,version_label,rule_version_id,schema_version,hash_format_version,
             definition,asset_manifest,payload_sha256,availability,is_demo)
            VALUES (%s,%s,%s,%s,%s,'qiongtu-json-v1',%s,%s,%s,'active',true)''',
            (identity(value), SCENARIO_CODE + '-local-test', value['version'], value['rule_version_id'],
             value['schema_version'], Jsonb(value), Jsonb(dict(map=value['map'],
             release_scope=LOCAL_MANUAL_SCOPE, competitive_ready=False, geometry_binding=value['geometry_binding'])), sha256(value)))
    return dict(scenario_version_id=identity(value), payload_sha256=sha256(value),
                release_scope=LOCAL_MANUAL_SCOPE, competitive_ready=False, inserted=not bool(row))


def local_manual_record_matches(record, case_id, *, geometry_binding, scenario):
    """局部资格必须携带真实逐项断言和输入/输出观察，不能只写passed。"""
    if not acceptance_record_matches(record, case_id, geometry_binding=geometry_binding, scenario=scenario):
        return False
    assertions = record.get('assertions')
    if isinstance(assertions, dict):
        assertions = list(assertions.values())
    if not (isinstance(assertions, list) and bool(assertions)
            and all(item is True or (isinstance(item, dict) and item.get('passed') is True) for item in assertions)
            and isinstance(record.get('observations'), dict) and bool(record['observations'])):
        return False
    return (case_id != 'source_integrity'
            or record.get('database_map_content_hash') == scenario['map']['content_hash'])
