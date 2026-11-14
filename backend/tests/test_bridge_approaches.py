"""桥头修复只改变接坡区域内路面高程，保留地图其他内容。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.bridge_approaches import apply, repair_geometry
from backend.fivepoint_map_audit import inspect_map, MAP_FOLDER


class BridgeApproachTests(unittest.TestCase):
    """以小型桥面验证两端接坡、范围隔离及重复执行。"""

    def fixture(self):
        """桥面22米，两岸路面20.32米，接坡长度100米。"""
        definition = dict(mapId='test', contentHash='hash', nodes=[dict(id='a', x=200, z=0), dict(id='b', x=300, z=0)],
                          roads=[dict(id='bridge', startId='a', endId='b', isBridge=True, surfaceHeight=22, width=20)])
        parts = []
        for name, start, end, altitude in [('west', 0, 200, 20.32), ('bridge', 200, 300, 22), ('east', 300, 500, 20.32)]:
            parts.append(dict(name=name, layer='routes', material='Asphalt',
                vertices=[start, altitude, -10, end, altitude, -10, end, altitude, 10, start, altitude, 10], triangles=[0, 1, 2, 0, 2, 3]))
        parts.append(dict(name='building', layer='settlement', material='Asphalt', vertices=[200, 99, 0], triangles=[]))
        return definition, dict(mapId='test', contentHash='hash', parts=parts)

    def test_bridge_ends_join_without_moving_horizontal_positions(self):
        definition, geometry = self.fixture()
        before = deepcopy(geometry)
        result = repair_geometry(definition, geometry)
        self.assertEqual(len(result['changed_parts']), 2)
        self.assertEqual(inspect_map(definition, geometry)['bridge_discontinuities'], [])
        for old, new in zip(before['parts'], geometry['parts']):
            self.assertEqual(old['vertices'][::3], new['vertices'][::3])
            self.assertEqual(old['vertices'][2::3], new['vertices'][2::3])
            self.assertEqual(old['triangles'], new['triangles'])
        self.assertEqual(before['parts'][-1], geometry['parts'][-1])
        self.assertEqual(geometry['parts'][0]['vertices'][1], 20.32)

    def test_repeat_does_not_bend_ramp_twice(self):
        definition, geometry = self.fixture()
        repair_geometry(definition, geometry)
        first = deepcopy(geometry)
        repair_geometry(definition, geometry)
        self.assertEqual(first, geometry)

    def test_markings_join_at_the_existing_bridge_marking_offset(self):
        definition, geometry = self.fixture()
        marking = deepcopy(geometry['parts'][0])
        marking.update(name='marking', material='Marking')
        marking['vertices'][1::3] = [20.4] * 4
        geometry['parts'].append(marking)
        repair_geometry(definition, geometry)
        self.assertEqual(marking['vertices'][4], 22.04)
        self.assertEqual(marking['vertices'][1], 20.4)

    def prepare_files(self, root):
        """为失败回滚准备独立文件，不读取或覆盖真实地图。"""
        folder = root / MAP_FOLDER
        folder.mkdir(parents=True)
        definition, geometry = self.fixture()
        paths = [folder / 'map-definition.json', folder / 'map-geometry.json']
        for path, value in zip(paths, (definition, geometry)):
            path.write_text(json.dumps(value), encoding='utf-8')
        return paths, [path.read_bytes() for path in paths]

    def test_audit_exception_restores_both_original_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, originals = self.prepare_files(root)
            with patch('backend.bridge_approaches.audit', side_effect=ValueError('三角面无效')):
                with self.assertRaisesRegex(ValueError, '三角面无效'):
                    apply(root)
            self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_second_replace_failure_restores_first_file(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, originals = self.prepare_files(root)
            original_replace = Path.replace

            def replace_checked(path, target):
                """只让第二份暂存发布文件失败，恢复文件仍可正常替换。"""
                if path.name == '1.new':
                    raise OSError('模拟第二份文件发布失败')
                return original_replace(path, target)

            with patch.object(Path, 'replace', replace_checked):
                with self.assertRaisesRegex(OSError, '第二份'):
                    apply(root)
            self.assertEqual([path.read_bytes() for path in paths], originals)

    def test_existing_marker_does_not_skip_identity_validation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths, _ = self.prepare_files(root)
            with patch('backend.bridge_approaches.audit', return_value=dict(geometry_ready=True, bridge_ends=[{}, {}])):
                apply(root)
            geometry = json.loads(paths[1].read_bytes())
            geometry['contentHash'] = '已经被改动'
            paths[1].write_text(json.dumps(geometry), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '身份'):
                apply(root)
