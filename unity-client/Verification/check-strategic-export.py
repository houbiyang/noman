"""检查 Blender 实际导出的米制地图、同源哈希与地形正面；只使用标准库。"""

import hashlib
import json
import math
from pathlib import Path


def main():
    """核对真实资源，不运行生成器，也不将结构校验视为画面验收。"""
    root = Path(__file__).resolve().parents[1]
    resources = root / "Assets/Sandbox/Resources/Maps/QinglanValley"
    definition = json.loads((resources / "map-definition.json").read_text(encoding="utf-8"))
    geometry = json.loads((resources / "map-geometry.json").read_text(encoding="utf-8"))
    summary = json.loads((root / "MapPreview/Strategic12km/map-build-summary.json").read_text(encoding="utf-8"))
    expected_hash = definition.pop("contentHash")
    canonical_definition = json.dumps(definition, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    canonical_parts = json.dumps(geometry["parts"], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    actual_hash = hashlib.sha256(canonical_definition.encode()+b"\n"+canonical_parts.encode()).hexdigest()
    assert actual_hash == expected_hash == geometry["contentHash"] == summary["contentHash"], "同源资源哈希不一致"
    assert definition["width"] == definition["length"] == 12000 and definition["coordinateUnit"] == "meter"
    assert summary["areaSquareKm"] == 144
    models = {building["model"] for building in definition["buildings"]}
    for model in models:
        path = root / "Assets/Sandbox/Resources/Battlefield/Models" / (model+".fbx")
        assert path.is_file() and path.read_bytes().startswith(b"Kaydara FBX Binary"), "缺少有效 Blender FBX："+model
    assert len(models) == 15 and len(definition["buildings"]) == summary["buildings"]
    triangles = 0
    for part in geometry["parts"]:
        vertices, indices = part["vertices"], part["triangles"]
        triangles += len(indices)//3
        assert all(math.isfinite(value) for value in vertices), "网格包含非有限值"
        assert len(vertices)//3 <= 65535 and all(0 <= index < len(vertices)//3 for index in indices)
        if part["layer"] != "terrain":
            continue
        for offset in range(0, len(indices), 3):
            points = [vertices[index*3:index*3+3] for index in indices[offset:offset+3]]
            first_x, first_z = points[1][0]-points[0][0], points[1][2]-points[0][2]
            second_x, second_z = points[2][0]-points[0][0], points[2][2]-points[0][2]
            assert first_z*second_x-first_x*second_z > 0, "地形面朝下或退化"
    assert triangles == summary["triangles"]
    lookup = {node["id"]: node for node in definition["nodes"]}
    for road in definition["roads"]:
        start, end = lookup[road["startId"]], lookup[road["endId"]]
        assert math.hypot(start["x"]-end["x"],start["z"]-end["z"]) > 1, "存在零长度道路"
        if road["isBridge"]:
            assert road["surfaceHeight"] == 22 and road["width"] in (24, 32), "桥面米制比例异常"
    assert sorted(road["width"] for road in definition["roads"] if road["isBridge"]) == [24, 24, 32], "三处主桥宽度与地图设计不符"
    assert any(part["layer"] == "landscape" for part in geometry["parts"]), "缺少外围远景"
    print(json.dumps({"mapId":definition["mapId"], "hash":actual_hash, "areaSquareKm":144,
                      "models":len(models), "buildings":summary["buildings"], "trees":summary["trees"],
                      "parts":len(geometry["parts"]), "triangles":triangles}, ensure_ascii=False))


if __name__ == "__main__":
    main()
