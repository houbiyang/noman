"""核对当前五点规则交付证据；代码存在、算法通过与整局可交付分别记录。"""

import argparse
import hashlib
import json
from pathlib import Path

from backend.fivepoint_parameters import VERSION, TERRAIN_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAP_FOLDER = "unity-client/Assets/Sandbox/Resources/Maps/QinglanValley"
REQUIRED_MAP_FIELDS = {
    "support_layers": "缺少显式地表层、桥面高度与层连接",
    "forest_regions": "缺少独立于道路的连通森林覆盖数据",
    "point_surfaces": "缺少五点有效地表掩码",
    "camp_service_regions": "缺少兵营服务区及目标隔离验证",
    "docking_surfaces": "缺少无人机合法起降面",
    "route_acceptance": "缺少五点双兵种进出路线和对应侧独立路线验证",
    "fairness_acceptance": "缺少双方最快合法到达时间的公平性验证",
}


def audit(root: Path = PROJECT_ROOT) -> dict:
    """读取当前资产结构及发布证据，缺失项逐条报告，不把旧演示验收挪给新版本。"""
    map_path = root / MAP_FOLDER / "map-definition.json"
    map_data = json.loads(map_path.read_text(encoding="utf-8"))
    map_hash = hashlib.sha256(map_path.read_bytes()).hexdigest()
    rule_map_path = root / "backend/fivepoint-map.json"
    rule_map = json.loads(rule_map_path.read_text(encoding="utf-8")) if rule_map_path.exists() else {}
    checks = []
    for field, message in REQUIRED_MAP_FIELDS.items():
        checks.append(dict(id=field, passed=bool(rule_map.get(field)), message=message))
    identity = (rule_map.get("source_sha256") == map_hash and rule_map.get("terrain_version") == TERRAIN_VERSION)
    checks.append(dict(id="map_version", passed=identity, message="规则地图必须绑定当前显示地图摘要及地形规则版本"))
    evidence_path = root / "backend/Verification/fivepoint-release.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
    matching_evidence = evidence.get("rule_version") == VERSION and evidence.get("map_sha256") == map_hash
    for field, message in (
        ("persistence", "待验收720步原子快照、并发幂等、分支恢复和终局归档"),
        ("agent_rounds", "待验收真实模型双边共同规划、120秒预算和晚到结果隔离"),
        ("unity_ui", "待验收Unity五点、资源、动作、比分及同一播放时刻的展示"),
        ("capacity", "待在真实地图和客户端声明并验证单位容量"),
        ("full_match", "待完成当前地图整场720步对局及恢复、回放验收"),
    ):
        checks.append(dict(id=field, passed=bool(matching_evidence and evidence.get(field, {}).get("passed")), message=message))
    return dict(rule_version=VERSION, terrain_version=TERRAIN_VERSION, map_id=map_data["mapId"],
                map_sha256=map_hash, delivery_ready=all(item["passed"] for item in checks),
                checks=checks, blockers=[item["message"] for item in checks if not item["passed"]])


def main() -> None:
    """输出可复核的交付检查结果；任何阻塞存在时返回非零状态。"""
    argparse.ArgumentParser(description="核对五点规则正式交付条件").parse_args()
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["delivery_ready"] else 1)


if __name__ == "__main__":
    main()
