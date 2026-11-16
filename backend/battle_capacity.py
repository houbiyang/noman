"""本地战斗测试的软件容量清单；不是游戏规则的固定兵力或性能认证。"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from backend import rulebook
from backend.runtime import sha256

MANIFEST_PATH = Path(__file__).with_name('battle-capacity-v1.json')
RULE_CODE = 'qinglan-software-capacity'
UNIT_TYPES = ('recon', 'ground', 'support')
MINIMUM_PER_TYPE = 0
MAXIMUM_PER_TYPE = 10
MINIMUM_PER_SIDE = 1
MAXIMUM_PER_SIDE = 30
MAXIMUM_PER_MATCH = 60


def manifest() -> dict:
    """每次读取并检查清单，避免可变缓存或非法清单被静默采用。"""
    value = json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))
    expected = dict(minimum_per_type=MINIMUM_PER_TYPE, maximum_per_type=MAXIMUM_PER_TYPE,
                    minimum_per_side=MINIMUM_PER_SIDE, maximum_per_side=MAXIMUM_PER_SIDE,
                    maximum_per_match=MAXIMUM_PER_MATCH)
    if value.get('schema_version') != 'qinglan-software-capacity-v1' or value.get('unit_types') != list(UNIT_TYPES):
        raise ValueError('容量清单版本或兵种不匹配，需显式发布新版本')
    if any(type(value.get(k)) is not int or value[k] != v for k, v in expected.items()):
        raise ValueError('容量清单与本版本输入契约不匹配，拒绝加载')
    if (value.get('scope') != 'local_battle_test_only'
            or value.get('overflow_policy') != 'reject_without_truncation'
            or value.get('asymmetric_allowed') is not True
            or value.get('formal_release_certified') is not False):
        raise ValueError('容量清单不得声明无限容量、截断兵力或未验证的正式认证')
    if MAXIMUM_PER_SIDE != len(UNIT_TYPES) * MAXIMUM_PER_TYPE or MAXIMUM_PER_MATCH != 2 * MAXIMUM_PER_SIDE:
        raise ValueError('容量派生值不一致')
    if value.get('rulebook_anchor') != 'main-4-1' or not isinstance(value.get('release_id'), str) or not value['release_id']:
        raise ValueError('容量清单缺少来源或发布标识')
    return value


def validate_quantities(quantities: dict) -> dict:
    """数量完整、严格整数、允许单类0；超限拒绝，不改写用户输入。"""
    limits = manifest()
    if not isinstance(quantities, dict) or set(quantities) != set(UNIT_TYPES):
        raise ValueError('必须完整提供recon、ground、support三类数量')
    if any(type(n) is not int or not limits['minimum_per_type'] <= n <= limits['maximum_per_type']
           for n in quantities.values()):
        raise ValueError('本测试版本每方每类单位数量必须是0～10的整数')
    if not limits['minimum_per_side'] <= sum(quantities.values()) <= limits['maximum_per_side']:
        raise ValueError('每方开局至少1个单位，最多30个单位')
    return dict(quantities)


def validate_roster(units: list[dict]) -> dict:
    """开局固定名册的容量校验；不用于按存活数重算中途计分分母。"""
    if not isinstance(units, list):
        raise ValueError('开局名册必须为数组')
    counts = {side: Counter({kind: 0 for kind in UNIT_TYPES}) for side in ('red', 'blue')}
    identities = set()
    for unit in units:
        if (not isinstance(unit, dict) or not isinstance(unit.get('side'), str)
                or unit['side'] not in counts or not isinstance(unit.get('unit_type'), str)
                or unit['unit_type'] not in UNIT_TYPES):
            raise ValueError('开局名册存在非法阵营或兵种')
        identity = unit.get('unit_id')
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ValueError('开局单位必须具有非空且全局唯一的身份')
        identities.add(identity)
        counts[unit['side']][unit['unit_type']] += 1
    result = {side: validate_quantities(dict(counts[side])) for side in ('red', 'blue')}
    if len(units) > manifest()['maximum_per_match']:
        raise ValueError('本测试版本双方总单位数不得超过60')
    return dict(quantities=result, total=len(units), asymmetric=result['red'] != result['blue'])


def definition() -> dict:
    """独立、可追溯的发布补充，不改写已登记的总册草案。"""
    base = rulebook.definition()
    return dict(schema_version='qinglan-software-capacity-definition-v1', capacity=manifest(),
                rulebook=dict(rule_version_id=rulebook.identity(base), payload_sha256=sha256(base),
                              source_sha256=base['source']['sha256'], anchor='main-4-1'),
                integration=dict(status='declared_not_activated', gameplay_implemented=False,
                                 migrate_existing_games=False, scenario_binding=None,
                                 remaining_gates=['rulebook_executor', 'map_acceptance',
                                                  'webgl_capacity_load_test', 'battle_flow_acceptance']))


def identity(value: dict) -> str:
    return str(uuid5(NAMESPACE_URL, 'qinglan/software-capacity/' + sha256(value)))


def register(connection) -> dict:
    """事务内幂等登记独立草案；同名内容冲突拒绝覆盖，无场景或对局迁移。"""
    value = definition()
    rule_id, digest = identity(value), sha256(value)
    label = value['capacity']['release_id']
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (RULE_CODE + '/' + label,))
    source = connection.execute('SELECT payload_sha256 FROM rule_versions WHERE id=%s',
                                (value['rulebook']['rule_version_id'],)).fetchone()
    if source is None or source['payload_sha256'] != value['rulebook']['payload_sha256']:
        raise ValueError('引用的总册版本尚未登记或摘要不符，拒绝登记容量补充')
    existing = connection.execute('''SELECT id,definition,payload_sha256,availability,is_demo
        FROM rule_versions WHERE rule_code=%s AND version_label=%s''', (RULE_CODE, label)).fetchone()
    if existing:
        if (str(existing['id']) != rule_id or existing['definition'] != value
                or existing['payload_sha256'] != digest or not existing['is_demo']):
            raise ValueError('同名容量版本内容不同，拒绝覆盖；请显式发布新版本')
    else:
        connection.execute('''INSERT INTO rule_versions
            (id,rule_code,version_label,schema_version,hash_format_version,definition,payload_sha256,availability,is_demo)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',true)''',
            (rule_id, RULE_CODE, label, value['schema_version'], rulebook.HASH_VERSION, Jsonb(value), digest))
    return dict(rule_version_id=rule_id, version_label=label, payload_sha256=digest,
                availability=existing['availability'] if existing else 'draft', inserted=not bool(existing))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'register'))
    args = parser.parse_args()
    value = definition()
    if args.action == 'check':
        result = dict(rule_version_id=identity(value), payload_sha256=sha256(value),
                      capacity=value['capacity'], status='validated_not_registered_by_this_command')
    else:
        from backend.runtime import Settings
        with Settings.from_local().connect() as connection:
            result = register(connection)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
