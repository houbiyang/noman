"""W3真实流程工具的纯边界测试；mock不授予任何真实地图发布资格。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from backend import battle_map_geometry

path = Path(__file__).resolve().parents[2] / 'unity-client/Tools/verify_real_manual_flow.py'
spec = importlib.util.spec_from_file_location('real_manual_flow_tool', path)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class RealManualFlowToolTests(TestCase):
    def test_default_is_local_and_never_falls_back_to_formal_loader(self):
        args = SimpleNamespace(scope='local_manual_test', acceptance_candidate=False,
            surfaces='s', interactions='i', scenario='c', evidence='e')
        with patch.object(battle_map_geometry, 'load_server_geometry') as formal:
            with patch.object(battle_map_geometry, 'load_local_manual_geometry', None, create=True):
                with self.assertRaisesRegex(tool.VerificationError, 'LOCAL_MANUAL_LOADER_NOT_READY'):
                    tool.load_geometry(args)
            formal.assert_not_called()

    def test_candidate_cannot_use_formal_scope(self):
        args = SimpleNamespace(scope='formal', acceptance_candidate=True)
        with self.assertRaisesRegex(tool.VerificationError, 'CANDIDATE_NOT_FORMAL'):
            tool.load_geometry(args)

    def test_candidate_only_uses_explicit_offline_loader(self):
        args = SimpleNamespace(scope='local_manual_test', acceptance_candidate=True,
            surfaces='s', interactions='i', scenario='c', evidence='unused')
        with patch.object(battle_map_geometry, 'load_acceptance_geometry', create=True) as loader:
            tool.load_geometry(args)
            loader.assert_called_once_with('s', 'i', 'c')

    def test_plan_shape_and_count_fail_closed(self):
        with TemporaryDirectory() as d:
            p = Path(d)/'plan.json'
            for value in ({'rounds': [], 'auto_pass': True}, {'rounds': [{'host': []}]},
                          {'rounds': [{'blue': [None]*31}]}, {'rounds': [{}, {}]}):
                p.write_text(json.dumps(value))
                with self.assertRaises(tool.VerificationError): tool.read_plan(p, 1)
            p.write_text(json.dumps({'rounds': [{'blue': []}]}))
            self.assertEqual(tool.read_plan(p, 1), [{'blue': []}])
        self.assertEqual(tool.read_plan(None, 2), [])

    def test_binding_uses_current_owned_life_and_does_not_mutate_plan(self):
        view = dict(role='blue', units=[dict(unit_id='B1', life_id='new-life', side='blue')],
                    contacts=[dict(unit_id='R1', life_id='visible-life')])
        plan = [dict(unit_id='B1', task=dict(action='attack', target_id='R1'))]
        before = deepcopy(plan)
        bound = tool.bind_intents(plan, view)
        self.assertEqual(bound[0]['life_id'], 'new-life')
        self.assertEqual(bound[0]['task']['target_life_id'], 'visible-life')
        self.assertEqual(plan, before)

    def test_invisible_targets_foreign_units_and_frozen_lives_are_not_repaired(self):
        view = dict(role='blue', units=[dict(unit_id='B1', life_id='new', side='blue')], contacts=[])
        for item in (dict(unit_id='R1'), dict(unit_id='B1', life_id='old'),
                     dict(unit_id='B1', task=dict(target_id='invisible')),
                     dict(unit_id='B1', task=['invalid'])):
            with self.assertRaises(tool.VerificationError): tool.bind_intents([item], view)

    def test_publication_guard_precedes_any_database_access(self):
        with patch('backend.service.SandboxService.seed_versions') as seed:
            with self.assertRaisesRegex(tool.VerificationError, 'ISOLATION_REQUIRED'):
                tool.register_verified_sources(SimpleNamespace(connection={'database':'user_database'}), None, None, 'formal')
            seed.assert_not_called()
