"""只修复已授权的桥头接坡，保留平面布局、网格拓扑、建筑、材质及其他高程。"""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from backend.fivepoint_map_audit import audit, PROJECT_ROOT, MAP_FOLDER


def repair_geometry(definition: dict, geometry: dict, ramp_length: float = 100) -> dict:
    """将桥外100米铺装面渐变连接到既有桥面；重复执行不再次改变坡面。

    Args:
        definition: 保留原有道路和桥梁位置的地图定义。
        geometry: 只原位修改接坡范围的铺装顶点高程。
        ramp_length: 每侧接坡长度，单位为米。

    Returns:
        修改片段与顶点数量，写入几何元数据便于原生网格精确同步。
    """
    if geometry.get('bridgeApproachRepair'):
        return deepcopy(geometry['bridgeApproachRepair'])
    if ramp_length <= 0:
        raise ValueError('接坡长度必须大于零')
    nodes = {node['id']: node for node in definition['nodes']}
    ends = []
    for road in definition['roads']:
        if not road.get('isBridge'):
            continue
        first, last = nodes[road['startId']], nodes[road['endId']]
        delta = (last['x'] - first['x'], last['z'] - first['z'])
        length = (delta[0] ** 2 + delta[1] ** 2) ** .5
        if not length:
            raise ValueError('桥梁两端重合，未修复')
        direction = (delta[0] / length, delta[1] / length)
        ends.extend([(road, first, (-direction[0], -direction[1])), (road, last, direction)])
    changed = []
    for part in geometry['parts']:
        if part['layer'] != 'routes' or part['material'] not in ('Asphalt', 'Marking'):
            continue
        count = 0
        vertices = part['vertices']
        for index in range(0, len(vertices), 3):
            horizontal, altitude, depth = vertices[index:index + 3]
            for road, origin, outward in ends:
                offset = (horizontal - origin['x'], depth - origin['z'])
                longitudinal = offset[0] * outward[0] + offset[1] * outward[1]
                lateral = abs(offset[0] * outward[1] - offset[1] * outward[0])
                if not (-1e-6 <= longitudinal < ramp_length and lateral <= road['width'] / 2 + 1e-6):
                    continue
                target = road['surfaceHeight'] + (.04 if part['material'] == 'Marking' else 0)
                corrected = round(target + max(0, longitudinal) / ramp_length * (altitude - target), 6)
                if abs(corrected - altitude) > 1e-7:
                    vertices[index + 1] = corrected
                    count += 1
                break
        if count:
            changed.append(dict(name=part['name'], vertices=count))
    report = dict(version='bridge-approach-repair-v1', ramp_length_m=ramp_length,
                  changed_parts=changed, changed_vertices=sum(item['vertices'] for item in changed))
    geometry['bridgeApproachRepair'] = deepcopy(report)
    return report


def content_hash(definition: dict, geometry: dict) -> str:
    """沿用原生成器的共同身份算法，修复标记不参与网格内容摘要。"""
    identity = deepcopy(definition)
    identity.pop('contentHash')
    canonical = json.dumps(identity, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    parts = json.dumps(geometry['parts'], ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256((canonical + '\n' + parts).encode('utf-8')).hexdigest()


def vertex_patch(original: dict, repaired: dict, identity: str) -> dict:
    """生成精确到索引和前后高程的接坡补丁，不授权同片段内其他顶点。"""
    patches = []
    if len(original['parts']) != len(repaired['parts']):
        raise ValueError('接坡不能增删地图片段')
    for old, new in zip(original['parts'], repaired['parts']):
        if ({key: value for key, value in old.items() if key != 'vertices'}
                != {key: value for key, value in new.items() if key != 'vertices'}
                or len(old['vertices']) != len(new['vertices'])
                or old['vertices'][::3] != new['vertices'][::3]
                or old['vertices'][2::3] != new['vertices'][2::3]):
            raise ValueError('接坡不能改变片段、材质、平面或拓扑')
        changed = [dict(index=index // 3, originalY=old['vertices'][index + 1], repairedY=new['vertices'][index + 1],
                        x=new['vertices'][index], z=new['vertices'][index + 2])
                   for index in range(0, len(new['vertices']), 3) if old['vertices'][index + 1] != new['vertices'][index + 1]]
        if changed:
            if new['layer'] != 'routes' or new['material'] not in ('Asphalt', 'Marking'):
                raise ValueError('接坡不能改变非路面片段高程')
            patches.append(dict(name=new['name'], vertices=changed))
    return dict(schemaVersion='bridge-approaches-patch-v1', contentHash=identity, parts=patches)


def publish_checked(root: Path, changes: list) -> dict:
    """先暂存全部新旧内容，再替换并审计；写入或审计异常统一恢复。

    暂存目录位于当前项目内，和地图处于同一文件系统；替换单个文件时
    不暴露半份JSON，审计未完成的修复不能作为成功结果保留。
    """
    local = root / '.local'
    local.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='bridge-stage-', dir=local) as temporary:
        staging = Path(temporary).resolve()
        if not staging.is_relative_to(local.resolve()):
            raise ValueError('接坡暂存目录超出项目范围')
        originals = []
        for index, (path, payload) in enumerate(changes):
            originals.append(path.read_bytes() if path.exists() else None)
            path.parent.mkdir(parents=True, exist_ok=True)
            (staging / f'{index}.new').write_bytes(payload)
            if originals[-1] is not None:
                (staging / f'{index}.old').write_bytes(originals[-1])
        try:
            for index, (path, _) in enumerate(changes):
                (staging / f'{index}.new').replace(path)
            verified = audit(root)
            if not verified['geometry_ready']:
                raise ValueError('桥头实际三角面检查失败，已恢复原文件')
        except BaseException:
            for index, ((path, _), previous) in enumerate(zip(changes, originals)):
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    (staging / f'{index}.old').replace(path)
            raise
    return verified


def apply(root: Path = PROJECT_ROOT) -> dict:
    """备份原始双文件后应用接坡；异常恢复，重复执行也须核验身份和几何。"""
    folder = root / MAP_FOLDER
    paths = [folder / 'map-definition.json', folder / 'map-geometry.json']
    originals = [path.read_bytes() for path in paths]
    definition, geometry = [json.loads(payload) for payload in originals]
    backup = root / '.local/bridge-approaches-before-20261114'
    patch_path = root / 'unity-client/Assets/Sandbox/Maps/QinglanValley/bridge-approaches.patch.json'
    if geometry.get('bridgeApproachRepair'):
        if not (definition['contentHash'] == geometry['contentHash'] == content_hash(definition, geometry)):
            raise ValueError('现有接坡地图内容身份不一致，未跳过核验')
        changes = []
        if not patch_path.exists():
            original_geometry = json.loads((backup / 'map-geometry.json').read_bytes())
            patch = vertex_patch(original_geometry, geometry, definition['contentHash'])
            changes.append((patch_path, (json.dumps(patch, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')))
        else:
            patch = json.loads(patch_path.read_bytes())
            if patch.get('contentHash') != definition['contentHash']:
                raise ValueError('接坡顶点补丁与地图身份不一致')
        publish_checked(root, changes)
        return dict(geometry['bridgeApproachRepair'], already_applied=True)
    backup.mkdir(parents=True, exist_ok=True)
    for path in paths:
        target = backup / path.name
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise ValueError('桥头原始备份已存在且不同，未覆盖')
        if not target.exists():
            shutil.copy2(path, target)
    old_geometry = deepcopy(geometry)
    report = repair_geometry(definition, geometry)
    new_hash = content_hash(definition, geometry)
    report.update(previous_content_hash=definition['contentHash'], content_hash=new_hash,
                  source_geometry_sha256=hashlib.sha256(originals[1]).hexdigest())
    definition['contentHash'] = geometry['contentHash'] = new_hash
    geometry['bridgeApproachRepair'] = deepcopy(report)
    patch = vertex_patch(old_geometry, geometry, new_hash)
    values = [definition, geometry, patch]
    changes = [(path, (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8'))
               for path, value in zip([*paths, patch_path], values)]
    if patch_path.exists() and patch_path.read_bytes() != changes[-1][1]:
        raise ValueError('已有不同顶点补丁，未覆盖')
    verified = publish_checked(root, changes)
    report['bridge_ends_verified'] = len(verified['bridge_ends'])
    report_path = root / '.local/rules-bridge-repair-20261114.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> None:
    """仅在显式命令下修复用户已授权的桥头接坡，其他地图功能不变。"""
    argparse.ArgumentParser(description='修复现有地图六处桥头接坡').parse_args()
    print(json.dumps(apply(), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
