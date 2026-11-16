"""容量清单与新契约边界；不把接口数量测试冒充WebGL性能验收。"""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend import battle_capacity, rulebook
from backend.battle_contracts import BattleArmyQuantities, ConfigureBattleArmy
from backend.contracts import ArmyQuantities
from backend.runtime import sha256


def roster(counts):
    return [dict(unit_id=f'{side}-{kind}-{i}', side=side, unit_type=kind)
            for side, quantities in counts.items() for kind, count in quantities.items() for i in range(count)]


class BattleCapacityTests(unittest.TestCase):
    def test_declared_limits_are_software_limits_not_rulebook_changes(self):
        limits = battle_capacity.manifest()
        self.assertEqual([limits[k] for k in ('minimum_per_type', 'maximum_per_type', 'minimum_per_side',
                                             'maximum_per_side', 'maximum_per_match')], [0, 10, 1, 30, 60])
        self.assertFalse(limits['formal_release_certified'])
        self.assertIsNone(rulebook.definition()['parameters']['roster']['capacity_limit'])
        limits['maximum_per_type'] = 999
        self.assertEqual(battle_capacity.manifest()['maximum_per_type'], 10)

    def test_each_single_type_can_be_the_only_type(self):
        for kind in battle_capacity.UNIT_TYPES:
            quantities = dict.fromkeys(battle_capacity.UNIT_TYPES, 0)
            quantities[kind] = 1
            with self.subTest(kind=kind):
                model = BattleArmyQuantities(**quantities)
                self.assertEqual(model.model_dump(), quantities)
                self.assertEqual(battle_capacity.validate_quantities(quantities), quantities)

    def test_maximum_is_accepted_without_truncation(self):
        quantities = dict(recon=10, ground=10, support=10)
        model = ConfigureBattleArmy(side='red', quantities=quantities)
        self.assertEqual(model.quantities.model_dump(), quantities)
        units = roster(dict(red=quantities, blue=quantities))
        original = deepcopy(units)
        self.assertEqual(battle_capacity.validate_roster(units),
                         dict(quantities=dict(red=quantities, blue=quantities), total=60, asymmetric=False))
        self.assertEqual(units, original)

    def test_zero_total_is_rejected(self):
        for quantities in [dict(recon=0, ground=0, support=0)]:
            with self.assertRaises(ValueError):
                battle_capacity.validate_quantities(quantities)
            with self.assertRaises(ValidationError):
                BattleArmyQuantities(**quantities)

    def test_invalid_count_types_and_ranges_are_rejected(self):
        for bad in (-1, 11, 100000, True, False, '1', 1.0, None, float('inf'), float('nan')):
            with self.subTest(bad=bad):
                quantities = dict(recon=bad, ground=1, support=1)
                with self.assertRaises(ValueError):
                    battle_capacity.validate_quantities(quantities)
                with self.assertRaises(ValidationError):
                    BattleArmyQuantities(**quantities)

    def test_missing_and_unrecognized_types_are_rejected(self):
        for quantities in (dict(recon=1, ground=0), dict(recon=1, ground=0, support=0, extra=1), None, []):
            with self.subTest(quantities=quantities), self.assertRaises(ValueError):
                battle_capacity.validate_quantities(quantities)
        with self.assertRaises(ValidationError):
            BattleArmyQuantities(recon=1, ground=0, support=0, extra=1)

    def test_side_must_be_red_or_blue(self):
        with self.assertRaises(ValidationError):
            ConfigureBattleArmy(side='host', quantities=dict(recon=1, ground=0, support=0))

    def test_asymmetry_is_reported_not_normalized(self):
        quantities = dict(red=dict(recon=0, ground=1, support=0), blue=dict(recon=10, ground=10, support=10))
        result = battle_capacity.validate_roster(roster(quantities))
        self.assertTrue(result['asymmetric'])
        self.assertEqual(result['quantities'], quantities)
        self.assertEqual(result['total'], 31)

    def test_one_side_empty_is_rejected_even_with_enough_total_units(self):
        with self.assertRaises(ValueError):
            battle_capacity.validate_roster(roster(dict(red=dict(recon=10, ground=10, support=10))))
        with self.assertRaises(ValueError):
            battle_capacity.validate_roster([])

    def test_one_type_overflow_is_rejected_before_match_maximum(self):
        units = roster(dict(red=dict(recon=11, ground=0, support=0), blue=dict(recon=0, ground=1, support=0)))
        before = deepcopy(units)
        with self.assertRaises(ValueError):
            battle_capacity.validate_roster(units)
        self.assertEqual(units, before)

    def test_roster_rejects_duplicate_identity_and_invalid_shape(self):
        valid = roster(dict(red=dict(recon=1), blue=dict(ground=1)))
        for field, bad in [('unit_id', valid[1]['unit_id']), ('unit_id', ''), ('unit_id', 1),
                           ('side', 'host'), ('side', []), ('unit_type', 'tank'), ('unit_type', {})]:
            units = deepcopy(valid)
            units[0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                battle_capacity.validate_roster(units)
        for units in (None, {}, [None], ['red']):
            with self.subTest(units=units), self.assertRaises(ValueError):
                battle_capacity.validate_roster(units)

    def test_manifest_mismatch_fails_closed(self):
        for key, bad in [('maximum_per_type', 20), ('maximum_per_type', True), ('maximum_per_side', 31),
                         ('maximum_per_match', 61), ('scope', 'formal'), ('unit_types', ['ground']),
                         ('formal_release_certified', True), ('asymmetric_allowed', False),
                         ('overflow_policy', 'truncate'), ('release_id', ''), ('rulebook_anchor', 'missing')]:
            value = battle_capacity.manifest()
            value[key] = bad
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'capacity.json'
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.subTest(key=key, bad=bad), patch.object(battle_capacity, 'MANIFEST_PATH', path):
                    with self.assertRaises(ValueError):
                        battle_capacity.manifest()
                    with self.assertRaises(ValueError):
                        battle_capacity.validate_quantities(dict(recon=1, ground=0, support=0))

    def test_definition_references_unchanged_base_and_stays_inactive(self):
        value = battle_capacity.definition()
        base = rulebook.definition()
        self.assertEqual(value['rulebook']['rule_version_id'], rulebook.identity(base))
        self.assertEqual(value['rulebook']['payload_sha256'], sha256(base))
        self.assertEqual(value['rulebook']['source_sha256'], base['source']['sha256'])
        self.assertFalse(value['integration']['gameplay_implemented'])
        self.assertFalse(value['integration']['migrate_existing_games'])
        self.assertIsNone(value['integration']['scenario_binding'])
        self.assertEqual(battle_capacity.identity(value), battle_capacity.identity(battle_capacity.definition()))
        changed = deepcopy(value)
        changed['capacity']['maximum_per_type'] += 1
        self.assertNotEqual(battle_capacity.identity(changed), battle_capacity.identity(value))

    def test_legacy_contract_is_not_silently_relaxed(self):
        self.assertEqual(ArmyQuantities(recon=1, ground=1, support=1).recon, 1)
        with self.assertRaises(ValidationError):
            ArmyQuantities(recon=0, ground=1, support=1)


if __name__ == '__main__':
    unittest.main()
