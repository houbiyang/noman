"""工作台3独立回放验收工具测试；全为夹具，不授予实地图开战资格。"""
from copy import deepcopy
import importlib.util
from pathlib import Path
from unittest import TestCase

from backend.runtime import ServiceError
from backend.tests.test_battle_replay import record_fixture

path = Path(__file__).resolve().parents[2] / 'unity-client/Tools/verify_manual_replay.py'
spec = importlib.util.spec_from_file_location('manual_delivery_verifier', path)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class ManualDeliveryEvidenceTests(TestCase):
    def test_valid_cumulative_records_are_not_real_map_acceptance(self):
        one = record_fixture(); two = record_fixture(2, one)
        report = verifier.verify([one, two], required_events=['hit'])
        self.assertEqual(report['rounds'], 2)
        self.assertTrue(report['replay_integrity_passed'])
        self.assertFalse(report['real_map_e2e_accepted'])

    def test_missing_actions_cannot_pass_full_event_matrix(self):
        with self.assertRaises(ValueError):
            verifier.verify([record_fixture()], required_events=['move', 'service', 'capture'])

    def test_missing_repair_cannot_pass_resource_matrix(self):
        with self.assertRaises(ValueError):
            verifier.verify([record_fixture()], required_resources=['repair'])

    def test_tampered_original_hash_is_not_resealed(self):
        one = record_fixture(); one['frames'][-1]['world']['blue_score'] = '999'
        with self.assertRaises(ServiceError): verifier.verify([one])

    def test_single_round_is_explicit_and_cumulative_requires_round_one(self):
        one = record_fixture(); two = record_fixture(2, one)
        with self.assertRaises(ValueError): verifier.verify([two])
        self.assertEqual(verifier.verify([two], single_round=True)['start_seconds'], 60)
        with self.assertRaises(ValueError): verifier.verify([one, two], single_round=True)

    def test_short_terminal_is_not_sixty_round_evidence(self):
        with self.assertRaises(ValueError):
            verifier.verify([record_fixture(duration=30, finished=True)], require_sixty=True)

    def test_sixty_round_terminal_chain_and_zero_mutation(self):
        records = []
        for n in range(1, 61):
            records.append(record_fixture(n, records[-1] if records else None, finished=n == 60))
        before = deepcopy(records)
        report = verifier.verify(records, require_sixty=True)
        self.assertTrue(report['sixty_rounds_verified'])
        self.assertEqual(report['end_seconds'], 3600)
        self.assertEqual(records, before)
        with self.assertRaises(ValueError): verifier.verify(records[:-1], require_sixty=True)

    def test_world_chain_break_is_not_ignored(self):
        one = record_fixture(); two = record_fixture(2, one)
        two['previous_round_sha256'] = 'f' * 64
        with self.assertRaises(ServiceError): verifier.verify([one, two])
