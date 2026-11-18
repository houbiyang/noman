"""只读验收持久回放导出；不连接数据库、不生成事件、不证明地图合法性。"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.battle_replay import ReplayCursor
from backend.runtime import ServiceError


def verify(records, *, single_round=False, require_sixty=False, required_events=(), required_resources=()):
    cursor = ReplayCursor(records)  # 原摘要、世界连续性、生命、事件和60秒边界均须真实成立。
    if single_round:
        if len(records) != 1:
            raise ValueError('单轮验收只能包含一轮')
    elif records[0]['round_number'] != 1:
        raise ValueError('累计验收必须从第一轮开始，不接受缺少前轮的导出')
    if require_sixty and (len(records) != 60 or cursor.first_seconds != 0
            or cursor.last_seconds != 3600 or not records[-1]['finished']):
        raise ValueError('60轮验收须包含0至3600秒完整终局，不能用提前结束替代')
    events = Counter(e['kind'] for r in records for f in r['frames'] for e in f['events'])
    resources = Counter(e['resource'] for r in records for f in r['frames'] for e in f['events']
                        if e['kind'] in ('service', 'barracks_service') and e.get('resource'))
    missing = set(required_events) - events.keys()
    missing_resources = set(required_resources) - resources.keys()
    if missing or missing_resources:
        raise ValueError('缺少实际事件/交付资源: ' + ','.join(sorted(missing | missing_resources)))
    # 每轮单独可读，累计游标可回拖；播放不能改变导出账本。
    frozen = json.dumps(records, sort_keys=True)
    for record in records:
        single = ReplayCursor([record]); single.play(); single.advance(60)
        if single.seconds != record['end_seconds'] or single.playing:
            raise ValueError('单轮播放未停在真实末帧')
    cursor.play(); cursor.advance(3600)
    if cursor.seconds != cursor.last_seconds or cursor.playing:
        raise ValueError('累计播放未停在已完成末帧')
    cursor.seek(cursor.first_seconds); cursor.pause(); cursor.advance(60)
    if cursor.seconds != cursor.first_seconds or frozen != json.dumps(records, sort_keys=True):
        raise ValueError('暂停/回拖修改账本或时钟异常')
    return dict(replay_integrity_passed=True, real_map_e2e_accepted=False,
                boundary='仅导出回放结构与播放验证；不证明真实部署、动作合法、持久化来源、幂等或阵营隔离',
                rounds=len(records), start_seconds=cursor.first_seconds, end_seconds=cursor.last_seconds,
                finished=records[-1]['finished'], sixty_rounds_verified=require_sixty,
                rule_sha256=records[0]['rule_sha256'], scenario_sha256=records[0]['scenario_sha256'],
                event_counts=dict(sorted(events.items())), resource_delivery_counts=dict(sorted(resources.items())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('export', type=Path, help='仅含records数组的主持人回放导出JSON，不含会话/令牌')
    parser.add_argument('--single-round', action='store_true')
    parser.add_argument('--require-sixty-rounds', action='store_true')
    parser.add_argument('--require-events', nargs='*', default=[])
    parser.add_argument('--require-resources', nargs='*', choices=['energy', 'ammo', 'repair'], default=[])
    args = parser.parse_args()
    try:
        value = json.loads(args.export.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or set(value) != {'records'}:
            raise ValueError('导出只能包含records，不能混入会话/令牌或其他元数据')
        report = verify(value['records'], single_round=args.single_round,
            require_sixty=args.require_sixty_rounds, required_events=args.require_events,
            required_resources=args.require_resources)
    except (ServiceError, ValueError, TypeError, KeyError, OSError):
        # 不打印输入、解析详情或完整异常，避免误传含敏感信息的文件时泄漏。
        print(json.dumps(dict(replay_integrity_passed=False, real_map_e2e_accepted=False,
                             error='回放导出无效或必需的实际事件缺失'), ensure_ascii=False))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
