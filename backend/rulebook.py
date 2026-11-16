"""规则总册的可追溯参数登记；只存独立草案版本，不改变比赛执行器。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from backend.runtime import Settings, sha256

ROOT = Path(__file__).resolve().parents[1]
PARAMETERS = Path(__file__).with_name('rulebook-parameters-v1.json')
RULE_CODE = 'qinglan-rulebook'
HASH_VERSION = 'qiongtu-json-v1'


def extract_tables(text: str) -> list[dict]:
    """完整保存各编表格及标题锚点；不把示例、验收数值误当基准常量。"""
    lines = text.splitlines()
    tables, anchor, heading, i = [], None, None, 0
    while i < len(lines):
        line = lines[i].strip()
        match = re.fullmatch(r'<a id="([^"]+)"></a>', line)
        if match:
            anchor = match[1]
        if line.startswith('#'):
            heading = line.lstrip('#').strip()
        if (line.startswith('|') and i + 1 < len(lines)
                and re.fullmatch(r'\|[\s:|\-]+\|', lines[i + 1].strip())):
            start = i
            cells = lambda row: [cell.strip() for cell in re.split(r'(?<!\\)\|', row.strip().strip('|'))]
            header, rows = cells(line), []
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(cells(lines[i]))
                i += 1
            tables.append(dict(anchor=anchor, heading=heading, line_start=start + 1,
                               line_end=i, columns=header, rows=rows))
            continue
        i += 1
    return tables


def validate(parameters: dict, source_text: str) -> None:
    """验证来源、参数域和派生关系；不声称地图或规则执行器已验收。"""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def check_numbers(value):
        if isinstance(value, float):
            require(math.isfinite(value), '参数不得为NaN或无穷大')
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            require(value >= 0, '规则参数不得为负')
        if isinstance(value, dict):
            for item in value.values():
                check_numbers(item)
        elif isinstance(value, list):
            for item in value:
                check_numbers(item)

    check_numbers(parameters)
    match, capture, score = (parameters[k] for k in ('match', 'capture', 'score'))
    for key in ('duration_seconds', 'step_seconds', 'plan_interval_seconds'):
        require(type(match[key]) is int and match[key] > 0, '时间必须为正整数仿真秒')
    step = match['step_seconds']
    require(match['duration_seconds'] % step == 0, '总时长须为步长整数倍')
    require(match['plan_interval_seconds'] % step == 0, '规划间隔须为步长整数倍')
    require(match['maximum_steps'] == match['duration_seconds'] // step, '最大步数不一致')
    require(match['maximum_planning_rounds'] == match['duration_seconds'] // match['plan_interval_seconds'], '规划轮数不一致')
    for name in ('capture', 'neutralize'):
        require(capture[name + '_seconds'] > 0 and capture[name + '_seconds'] % step == 0, '占领时间必须为步长整数倍')
        require(capture[name + '_steps'] == capture[name + '_seconds'] // step, '占领步数不一致')
    require(capture['total_control_capacity_steps'] == capture['point_count'] * match['maximum_steps'], '控制分母不一致')
    require(capture['point_count'] == 5 and len(capture['candidate_points']) == 5, '必须恰为五个候选点')
    require(len({p['point_id'] for p in capture['candidate_points']}) == 5, '候选点ID重复')
    for point in capture['candidate_points']:
        require(0 <= point['x_m'] <= parameters['map']['width_m'] and 0 <= point['z_m'] <= parameters['map']['depth_m'], '候选点越界')
        require(point['radius_m'] == capture['radius_m'] > 0, '候选点半径不一致')
    require(score['resource_max'] == score['energy_max'] + score['ammo_max'] + score['material_max'], '资源权重不一致')
    require(score['total_max'] == score['control_max'] + score['kill_max'] + score['resource_max'], '总分权重不一致')
    require([score[k] for k in ('control_max', 'kill_max', 'resource_max', 'total_max')] == [70, 20, 30, 120], '本总册权重必须为70/20/30，总分120')
    require(score['normalize_to_100'] is False, '本总册不得归一化为100分')
    for kind, profile in parameters['unit_profiles'].items():
        for key in ('durability', 'energy', 'speed_m_per_minute', 'energy_per_km', 'observation_m', 'ground_radius_m', 'kill_value'):
            require(type(profile[key]) in (int, float) and profile[key] > 0, '兵种参数必须为有限正数')
        require(type(profile['ammo']) is int and profile['ammo'] == (60 if kind == 'ground' else 0), '自用弹药配置不合法')
    for factors in parameters['terrain']['surface_factors'].values():
        require(all(v is None or type(v) in (int, float) and 0 < v <= 1 for v in factors.values()), '地表倍率不合法')
    require(parameters['roster']['fixed_total_per_side'] is None, '本总册不得强制固定20单位')
    require(parameters['barracks']['reserve_shared_across_barracks'] is True, '公共库存必须全方共享')
    for group, anchors in parameters['provenance'].items():
        require(group in parameters, '来源映射缺少参数组')
        for anchor in anchors:
            require(f'<a id="{anchor}"></a>' in source_text, '来源锚点不存在: ' + anchor)


def definition() -> dict:
    """读取固定参数和完整原文；原文漂移时拒绝静默导入旧参数。"""
    parameters = json.loads(PARAMETERS.read_text(encoding='utf-8'))
    source = parameters['source']
    path = (ROOT / source['path']).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('来源文件必须位于项目内')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source['sha256']:
        raise ValueError('规则总册原文已变化，请审阅参数并发布新版本，不可静默覆盖')
    text = raw.decode('utf-8')
    validate(parameters, text)
    return dict(schema_version='qinglan-rulebook-definition-v1', parameters=parameters,
                source=dict(source, text=text, tables=extract_tables(text)),
                integration=dict(status='registered_not_activated', gameplay_implemented=False,
                    migrate_existing_games=False, scenario_binding=None,
                    blockers=['new_rule_executor', 'candidate_point_surface_and_route_validation',
                              'release_unit_capacity_manifest', 'resource_ledger_and_scoring_integration',
                              'planning_and_playback_integration', 'actual_match_acceptance']))


def identity(value: dict) -> str:
    return str(uuid5(NAMESPACE_URL, 'qinglan/rulebook/' + sha256(value)))


def register(connection, value: dict | None = None) -> dict:
    """调用方事务中幂等插入draft；不更新旧载荷、场景、对局或快照。"""
    value = definition() if value is None else deepcopy(value)
    # 防止调用方传入与发布参数/来源不一致的任意载荷。
    if value != definition():
        raise ValueError('只能登记经校验的当前总册发布载荷')
    rule_id, digest, label = identity(value), sha256(value), value['parameters']['version']
    connection.execute('SELECT pg_advisory_xact_lock(80644, 1113)')
    existing = connection.execute('''SELECT id, definition, payload_sha256, availability
        FROM sandbox.rule_versions WHERE rule_code=%s AND version_label=%s''', (RULE_CODE, label)).fetchone()
    inserted = existing is None
    if existing:
        if str(existing['id']) != rule_id or existing['definition'] != value or existing['payload_sha256'] != digest:
            raise ValueError('同名规则版本内容不同，拒绝覆盖；请发布新版本')
    else:
        connection.execute('''INSERT INTO sandbox.rule_versions
            (id,rule_code,version_label,schema_version,hash_format_version,definition,payload_sha256,availability,is_demo)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',false)''',
            (rule_id, RULE_CODE, label, value['schema_version'], HASH_VERSION, Jsonb(value), digest))
    return dict(rule_version_id=rule_id, version_label=label, payload_sha256=digest,
                availability=existing['availability'] if existing else 'draft', inserted=inserted,
                source_sha256=value['source']['sha256'], source_table_count=len(value['source']['tables']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'register'))
    args = parser.parse_args()
    value = definition()
    if args.action == 'check':
        result = dict(rule_version_id=identity(value), payload_sha256=sha256(value),
                      source_table_count=len(value['source']['tables']), status='validated_not_registered_by_this_command')
    else:
        with Settings.from_local().connect() as connection:
            result = register(connection, value)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
