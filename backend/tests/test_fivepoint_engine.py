"""规则总册的独立预期验收；矩形测试场景不代表当前 Unity 地图已经通过。"""

from copy import deepcopy
from fractions import Fraction
import json
import unittest

from backend import fivepoint_engine as engine
from backend.fivepoint_parameters import RuleViolation, roster_baseline, score, segment_cost, slope_factor, number
from backend.fivepoint_geometry import RuleGeometry, distance, line_clear, forest_depth, full_stop_allowed
from backend.fivepoint_routes import integrate
from backend.fivepoint_playback import playback_rate, frame_at, can_produce


class FixtureGeometry(RuleGeometry):
    """测试专用平面，显式指定无遮挡和合法地表；禁止用于实际地图发布。"""

    def verify(self, map_hash):
        """测试身份必须匹配，不能被当作青岚地图验收。"""
        if map_hash != "fixture-only":
            raise RuleViolation("MAP_NOT_VERIFIED", "仅限测试地图")

    def service_energy_cost(self, unit, own_view):
        """测试假定服务已在当前位置可得；不代表真实地图成本。"""
        return 0

    def movement_service_energy_cost(self, unit, movement, own_view):
        """测试路径全程使用显式零服务成本。"""
        return 0

    def can_observe(self, observer, target):
        """使用测试平面观察范围。"""
        return distance(observer["position"]["xy"], target["position"]["xy"]) <= 900

    def can_fire(self, shooter, target):
        """使用测试平面射程。"""
        return distance(shooter["position"]["xy"], target["position"]["xy"]) <= (800 if target["flight"] not in ("ground", "docked") else 600)

    def in_point(self, unit, point):
        """测试有效层与圆，供占领时序验收使用。"""
        return unit["position"]["layer"] == point["layer"] and distance(unit["position"]["xy"], point["xy"]) <= 250

    def can_service(self, source, target):
        """测试相同支撑面与100米半径。"""
        return source["position"]["layer"] == target["position"]["layer"] and distance(source["position"]["xy"], target["position"]["xy"]) <= 100

    def can_dock(self, unit, units):
        """测试动态敌军占地。"""
        return not any(engine.alive(other) and other["side"] != unit["side"] and other["flight"] == "ground"
                       and distance(other["position"]["xy"], unit["position"]["xy"]) < 6 for other in units)

    def move(self, unit, destination, seconds, available_energy, reserve_factor, units):
        """仅用于轴向整米测试路径，不作为生产路线算法。"""
        length = distance(unit["position"]["xy"], destination["xy"])
        cost = segment_cost(unit["unit_type"], "road", length)
        ratio = min(Fraction(1), Fraction(seconds) / cost["seconds"] if length else 1,
                    available_energy / (cost["energy"] * reserve_factor) if length else 1)
        position = dict(layer=destination["layer"], xy=[str(number(start) + (number(end) - number(start)) * ratio)
                        for start, end in zip(unit["position"]["xy"], destination["xy"])])
        return dict(position=position, energy=cost["energy"] * ratio, moved=bool(length * ratio), remainder=0)


def location(horizontal, vertical=0, layer="ground"):
    """构造明确米制测试坐标。"""
    return dict(xy=[horizontal, vertical], layer=layer)


def fixture(kinds=("ground", "support", "recon")):
    """双方编制由测试显式提供，每方三营和五点。"""
    rosters = {side: [dict(unit_id=f"{side}-{index}", unit_type=kind, position=location(0 if side == "red" else 500))
                      for index, kind in enumerate(kinds)] for side in ("red", "blue")}
    camps = [dict(camp_id=f"{side}-{kind}", side=side, unit_type=kind, position=location(-1000 if side == "red" else 2000))
             for side in ("red", "blue") for kind in ("ground", "support", "recon")]
    points = [dict(point_id=f"point-{index}", xy=[index * 2000, 0], layer="ground") for index in range(5)]
    return engine.initialize(rosters, points, camps, "fixture-only", FixtureGeometry(), 60)


def plan(state, red=None, blue=None):
    """共同采用两个测试方案，显式空方案表示待命。"""
    state = engine.submit_plan(state, "red", red or {}, state["step"])
    return engine.submit_plan(state, "blue", blue or {}, state["step"])


def step(state, geometry=None):
    """提交唯一测试步请求，规划边界由调用者显式处理。"""
    return engine.advance(state, geometry or FixtureGeometry(), state["step"], f"step-{state['step']}")


class ParameterAcceptance(unittest.TestCase):
    """独立核对已确认公式和地形边界。"""

    def test_r01_roster_baselines(self):
        self.assertEqual(roster_baseline(dict(recon=6, ground=10, support=3)),
                         dict(count=19, energy=3700, ammo=1560, material=500, value=154))

    def test_r02_invalid_rosters(self):
        for quantity in (-1, .5, True):
            with self.subTest(quantity=quantity), self.assertRaises(RuleViolation):
                roster_baseline(dict(recon=quantity, ground=1, support=0))
        with self.assertRaises(RuleViolation):
            roster_baseline(dict(recon=0, ground=0, support=0))

    def test_r03_add_tank_baseline(self):
        before = roster_baseline(dict(recon=6, ground=10, support=3))
        after = roster_baseline(dict(recon=6, ground=11, support=3))
        self.assertEqual([after[key] - before[key] for key in ("energy", "ammo", "material", "value")], [100, 60, 0, 10])

    def test_r04_r05_control_and_r26_kill_scores(self):
        resources = dict(energy=3700, ammo=1560, material=500)
        value = score(360, 48, 154, resources, resources)
        self.assertEqual(value["control"], 7)
        self.assertEqual(value["destruction"], Fraction(960, 154))
        self.assertEqual(score(3600, 154, 154, resources, resources)["total"], 120)

    def test_t01_through_t04_reference_costs(self):
        for kind, terrain, grade, seconds, energy in (
            ("ground", "road", 0, 15, Fraction(4, 5)),
            ("ground", "field", 0, 30, Fraction(8, 5)),
            ("support", "field", 0, Fraction(200, 3), Fraction(10, 3)),
            ("ground", "field", ".15", Fraction(600, 17), Fraction(32, 17)),
        ):
            with self.subTest(kind=kind, terrain=terrain, grade=grade):
                self.assertEqual(segment_cost(kind, terrain, 100, grade), dict(seconds=seconds, energy=energy))

    def test_t07_t08_forbidden_terrain(self):
        self.assertEqual(segment_cost("ground", "sparse_forest", 100)["energy"], 2)
        for kind, terrain in [("support", "sparse_forest")] + [("ground", value) for value in ("water", "mud", "dense_forest", "obstacle", "unknown")]:
            with self.subTest(kind=kind, terrain=terrain), self.assertRaises(RuleViolation):
                segment_cost(kind, terrain, 100)

    def test_t09_t10_t11_cross_slope_boundaries(self):
        self.assertEqual(slope_factor("support", ".2", 0), Fraction(7, 10))
        self.assertEqual(slope_factor("ground", ".35", 0), Fraction(3, 5))
        for kind, longitudinal, cross in (("support", ".2001", 0), ("ground", ".3501", 0), ("ground", 0, ".36")):
            with self.subTest(kind=kind, grade=longitudinal, cross=cross), self.assertRaises(RuleViolation):
                slope_factor(kind, longitudinal, cross)

    def test_t13_bridge_uses_actual_slope(self):
        self.assertEqual(segment_cost("ground", "bridge", 100, ".15")["seconds"], Fraction(300, 17))

    def test_t18_forest_segments_union(self):
        patches = [dict(region_id="target", min=[0, 0], max=[300, 20]),
                   dict(region_id="target", min=[200, 0], max=[400, 20]),
                   dict(region_id="target", min=[600, 0], max=[800, 20]),
                   dict(region_id="other", min=[900, 0], max=[1000, 20])]
        self.assertEqual(forest_depth([0, 10], [1200, 10], "target", patches), 600)

    def test_t19_thin_wall_between_old_samples(self):
        solids = [dict(min=[24, -1, 0], max=[25, 1, 3])]
        self.assertFalse(line_clear([0, 0, 2], [100, 0, 2], solids))
        self.assertTrue(line_clear([0, 0, 4], [100, 0, 4], solids))

    def test_t23_t24_t26_stopping_slopes(self):
        self.assertTrue(full_stop_allowed("ground", "field", ".1"))
        self.assertFalse(full_stop_allowed("ground", "field", ".1001"))
        self.assertTrue(full_stop_allowed("recon", "open", ".05", docking=True))
        self.assertFalse(full_stop_allowed("recon", "open", ".0501", docking=True))
        self.assertFalse(full_stop_allowed("ground", "open", ".06", camp=True))

    def test_t05_t06_mixed_surfaces_share_time_budget(self):
        result = integrate("ground", [dict(length=10, terrain="road"), dict(length=100, terrain="field")])
        self.assertEqual((result["distance"], result["seconds"], result["energy"]), (Fraction(65, 3), 5, Fraction(4, 15)))
        split = integrate("ground", [dict(length=5, terrain="road"), dict(length=5, terrain="road"),
                                     dict(length=5, terrain="field"), dict(length=95, terrain="field")])
        self.assertEqual((split["distance"], split["energy"]), (result["distance"], result["energy"]))
        self.assertEqual(integrate("ground", [dict(length=100, terrain="road")], energy="1/10", reserve_factor="5/4")["energy"], Fraction(2, 25))

    def test_r28_complete_score_example(self):
        baseline = dict(energy=3700, ammo=1560, material=500)
        red = score(1620, 48, 154, dict(energy=2220, ammo=780, material=375), baseline)
        blue = score(1080, 33, 154, dict(energy=2590, ammo=1092, material=400), baseline)
        self.assertAlmostEqual(float(red["total"]), 55.7337662338)
        self.assertAlmostEqual(float(blue["total"]), 46.8857142857)

    def test_r35_playback_never_shows_future_result(self):
        frames = [dict(branch_id="current", second=0, result=None), dict(branch_id="current", second=180, result="finished"),
                  dict(branch_id="old", second=60, result="old")]
        self.assertIsNone(frame_at(frames, 60, "current")["result"])
        self.assertEqual(playback_rate(180, 60), 2)
        self.assertEqual(playback_rate(180, 120), 1)
        self.assertEqual(playback_rate(180, 150), Fraction(1, 2))
        self.assertEqual(playback_rate(180, 150, stalled=True), 0)
        self.assertEqual(playback_rate(180, 150, stalled=True, finished=True), Fraction(1, 2))
        self.assertFalse(can_produce(180, 0))
        self.assertTrue(can_produce(180, 60))
        self.assertFalse(can_produce(3600, 3600))


class EngineAcceptance(unittest.TestCase):
    """检查前值、完整步骤和后值，不以模型文本当作执行证据。"""

    def test_r06_capture_first_income_after_thirty_seconds(self):
        state = fixture(("ground",))
        state["units"][0]["position"] = location(-260)
        state = plan(state, {"red-0": dict(action="move", destination=location(-240))})
        state = step(state)
        self.assertEqual(state["points"][0]["progress"], 0)
        state["plans"]["red"] = {}
        for _ in range(6):
            state = step(state)
        self.assertEqual((state["step"] * 5, state["points"][0]["owner"], state["control"]["red"]), (35, "red", 0))
        state = step(state)
        self.assertEqual(state["control"]["red"], 1)

    def test_r07_r09_enemy_neutralization_two_separate_phases(self):
        state = fixture(("ground",))
        state["points"][0]["owner"] = "blue"
        state = plan(state)
        for _ in range(6):
            state = step(state)
        self.assertIsNone(state["points"][0]["owner"])
        self.assertEqual(state["points"][0]["progress"], 0)
        state["units"][0]["position"] = location(-1000)
        state = step(state)
        self.assertIsNone(state["points"][0]["owner"])

    def test_r08_dead_invader_still_suppresses_step(self):
        state = fixture(("ground", "support"))
        state["points"][0].update(owner="red", candidate="blue", progress=25)
        state["units"][2].update(position=location(100), hp="16")
        state = plan(state, {"red-0": dict(action="attack", target_id="blue-0")})
        state = step(state)
        self.assertEqual(state["units"][2]["retired"], "destroyed")
        self.assertEqual((state["control"]["red"], state["points"][0]["progress"]), (0, 0))
        state = step(state)
        self.assertEqual(state["control"]["red"], 1)

    def test_r10_zero_energy_and_drone_never_capture(self):
        state = fixture(("recon", "ground"))
        engine.spend(state, state["units"][1], "energy", 100)
        state = plan(state)
        for _ in range(6):
            state = step(state)
        self.assertIsNone(state["points"][0]["owner"])

    def test_r12_strict_takeoff_energy_threshold(self):
        state = fixture(("recon",))
        engine.spend(state, state["units"][0], "energy", Fraction(593, 6))
        state = plan(state, {"red-0": dict(action="takeoff")})
        state = step(state)
        self.assertEqual((state["units"][0]["energy"], state["units"][0]["flight"]), ("7/6", "docked"))

    def test_r13_r27_power_loss_has_no_kill_credit(self):
        state = fixture(("recon", "ground"))
        state["units"][0]["flight"] = "airborne"
        engine.spend(state, state["units"][0], "energy", Fraction(599, 6))
        state = step(plan(state))
        self.assertEqual(state["units"][0]["retired"], "power_failure")
        self.assertEqual(state["kills"]["blue"], [])
        self.assertEqual(state["frozen"]["baselines"]["red"]["value"], 15)

    def test_r14_takeoff_cost_and_observation_delay(self):
        state = plan(fixture(("recon",)), {"red-0": dict(action="takeoff")})
        state = step(state)
        self.assertEqual(number(state["units"][0]["energy"]), Fraction(593, 6))
        self.assertEqual(state["visible"]["red"], [])
        state = step(state)
        self.assertEqual(state["visible"]["red"], ["blue-0"])

    def test_r15_cooldown_and_r25_death_ledger(self):
        state = plan(fixture(("ground",)), {"red-0": dict(action="attack", target_id="blue-0")})
        state = step(state)
        self.assertEqual((state["units"][0]["ammo"], state["units"][0]["next_fire"]), (59, 15))
        state = step(state)
        self.assertEqual(state["units"][0]["ammo"], 59)
        state = step(state)
        self.assertEqual(state["units"][0]["ammo"], 58)
        engine.retire(state, state["units"][0], "destroyed")
        self.assertEqual(number(state["ledger"]["red"]["lost"]["ammo"]), 58)
        engine.validate(state)

    def test_r16_move_fire_damage_and_cost(self):
        state = plan(fixture(("ground",)), {"red-0": dict(action="move_attack", target_id="blue-0", destination=location(20))})
        state = step(state)
        self.assertEqual(number(state["units"][1]["hp"]), Fraction(452, 5))
        self.assertEqual(number(state["units"][0]["energy"]), Fraction(499, 5))

    def test_r17_r31_mutual_destruction(self):
        state = fixture(("ground",))
        for unit in state["units"]:
            unit["hp"] = "16"
        state = plan(state, {"red-0": dict(action="attack", target_id="blue-0")}, {"blue-0": dict(action="attack", target_id="red-0")})
        state = step(state)
        self.assertEqual(state["result"]["reason"], "mutual_elimination")
        self.assertIsNone(state["result"]["winner"])
        self.assertEqual(state["kills"], dict(red=["blue-0"], blue=["red-0"]))

    def test_r18_repeated_step_never_repeats_kill(self):
        state = fixture(("ground", "ground"))
        state["units"][2]["hp"] = "16"
        state = plan(state, {identity: dict(action="attack", target_id="blue-0") for identity in ("red-0", "red-1")})
        state = step(state)
        repeated = engine.advance(state, FixtureGeometry(), 0, "step-0")
        self.assertEqual(repeated, state)
        self.assertEqual(state["kills"]["red"], ["blue-0"])

    def test_r19_fractional_partial_energy_transfer(self):
        state = fixture()
        engine.spend(state, state["units"][0], "energy", 1)
        state["units"][1]["cargo"]["energy"] = "1/2"
        state["ledger"]["red"]["lost"]["energy"] = "399/2"
        baseline = engine.balances(state, "red")
        state = plan(state, {"red-1": dict(action="service", target_id="red-0", category="energy", balance=100)})
        state = step(state)
        self.assertEqual(number(state["units"][0]["energy"]), Fraction(199, 2))
        self.assertEqual(engine.balances(state, "red"), baseline)
        self.assertEqual(state["plans"]["red"]["red-1"]["action"], "service")

    def test_r20_r21_damage_before_field_repair(self):
        for hp, expected, consumed in ((20, 6, 2), (16, 0, 0)):
            with self.subTest(hp=hp):
                state = fixture()
                state["units"][0]["hp"] = str(hp)
                state = plan(state, {"red-1": dict(action="service", target_id="red-0", category="hp", balance=100)},
                             {"blue-0": dict(action="attack", target_id="red-0")})
                state = step(state)
                self.assertEqual(number(state["units"][0]["hp"]), expected)
                self.assertEqual(number(state["ledger"]["red"]["consumed"]["material"]), consumed)

    def test_r24_service_chain_rejected(self):
        state = fixture(("support", "support", "ground"))
        with self.assertRaises(RuleViolation) as caught:
            engine.submit_plan(state, "red", {
                "red-0": dict(action="service", target_id="red-1", category="energy", balance=100),
                "red-1": dict(action="service", target_id="red-2", category="energy", balance=100)}, 0)
        self.assertEqual(caught.exception.code, "SERVICE_CONFLICT")
        self.assertEqual(state["pending_plans"], {})

    def test_r32_save_restore_deterministic_and_pause_no_change(self):
        state = plan(fixture())
        restored = json.loads(json.dumps(state))
        self.assertEqual(step(restored), step(state))
        self.assertEqual(state["step"], 0)

    def test_r33_negative_balance_rolls_back(self):
        state = plan(fixture())
        state["ledger"]["red"]["public"]["energy"] = -1
        original = deepcopy(state)
        with self.assertRaises(RuleViolation):
            step(state)
        self.assertEqual(state, original)

    def test_r34_common_planning_barrier(self):
        state = engine.submit_plan(fixture(), "red", {}, 0)
        with self.assertRaises(RuleViolation):
            step(state)
        self.assertEqual(state["phase"], "planning")
        state = engine.submit_plan(state, "blue", {}, 0)
        self.assertEqual(step(state)["step"], 1)

    def test_r36_hidden_position_and_enemy_resources_not_exposed(self):
        state = step(plan(fixture(("ground",))))
        last_seen = deepcopy(state["history"]["red"]["blue-0"])
        state["units"][1]["position"] = location(5000)
        state = step(state)
        view = engine.project(state, "red")
        self.assertEqual(view["contacts"], [])
        self.assertEqual(view["history"]["blue-0"], last_seen)
        self.assertNotIn("5000", json.dumps(view))

    def test_r37_stale_step_and_reused_request(self):
        state = step(plan(fixture()))
        with self.assertRaises(RuleViolation):
            engine.advance(state, FixtureGeometry(), 0, "new-key")
        with self.assertRaises(RuleViolation):
            engine.advance(state, FixtureGeometry(), 1, "step-0")

    def test_r38_default_geometry_cannot_certify_map(self):
        state = plan(fixture())
        with self.assertRaises(RuleViolation) as caught:
            engine.advance(state, RuleGeometry(), 0, "map-check")
        self.assertEqual(caught.exception.code, "MAP_NOT_VERIFIED")

    def test_t20_occluded_shooter_spends_no_ammo(self):
        class Occluded(FixtureGeometry):
            def can_fire(self, shooter, target):
                return False
        state = plan(fixture(("ground",)), {"red-0": dict(action="move_attack", target_id="blue-0", destination=location(20))})
        state = step(state, Occluded())
        self.assertEqual(state["units"][0]["ammo"], 60)
        self.assertEqual(number(state["units"][0]["energy"]), Fraction(2496, 25))
        self.assertEqual(state["units"][0]["next_fire"], 0)

    def test_t12_t22_different_layers_prevent_service(self):
        state = fixture()
        engine.spend(state, state["units"][0], "energy", 10)
        state["units"][0]["position"]["layer"] = "bridge"
        state = plan(state, {"red-1": dict(action="service", target_id="red-0", category="energy", balance=100)})
        state = step(state)
        self.assertEqual(number(state["units"][0]["energy"]), 90)
        self.assertEqual(number(state["units"][1]["cargo"]["energy"]), 200)

    def test_r11_replacement_without_full_resident_resets_progress(self):
        state = fixture(("ground", "ground"))
        state["units"][1]["position"] = location(-260)
        state["points"][0].update(candidate="red", progress=25)
        state = plan(state, {"red-0": dict(action="move", destination=location(-20)),
                             "red-1": dict(action="move", destination=location(-240))})
        state = step(state)
        self.assertEqual(state["points"][0]["progress"], 0)

    def test_r22_camp_cooldown_uses_simulation_time(self):
        state = fixture(("ground",))
        unit = state["units"][0]
        unit.update(position=location(-1000), last_fire=5)
        engine.spend(state, unit, "ammo", 1)
        state = plan(state, {"red-0": dict(action="camp", camp_id="red-ground")})
        for _ in range(3):
            state = step(state)
            self.assertEqual(state["units"][0]["ammo"], 59)
        state = step(state)
        self.assertEqual(state["units"][0]["ammo"], 60)
        self.assertEqual(number(state["ledger"]["red"]["public"]["ammo"]), 599)

    def test_r23_camp_four_slots_and_one_public_ledger(self):
        state = fixture(("ground",) * 6)
        orders = {}
        for index, unit in enumerate(state["units"][:6]):
            unit["position"] = location(-1000)
            engine.spend(state, unit, "energy", 10)
            orders[unit["unit_id"]] = dict(action="camp", camp_id="red-ground" if index < 5 else "red-support")
        state = step(plan(state, orders))
        self.assertEqual(sum(number(unit["energy"]) == 100 for unit in state["units"][:6]), 5)
        self.assertEqual(number(state["ledger"]["red"]["public"]["energy"]), 1150)
        engine.validate(state)

    def test_r29_unrounded_tie_breaks_on_control(self):
        state = fixture(("ground",))
        state["step"] = 720
        state["control"].update(red=1440, blue=1080)
        # 本例在公式层验证精确同分；终局分支通过注入精确比分隔离验证顺序。
        baseline = dict(energy=10, ammo=3, material=3)
        red = score(1440, 0, 10, dict(energy=5, ammo="3/2", material="3/2"), baseline)
        blue = score(1080, 0, 10, dict(energy=8, ammo=2, material=2), baseline)
        self.assertEqual(red["total"], 43)
        self.assertEqual(blue["total"], 43)
        from unittest.mock import patch
        with patch.object(engine, "evaluate", return_value=dict(red=red, blue=blue)):
            engine.finish(state)
        self.assertEqual(state["result"]["winner"], "red")

    def test_r30_elimination_beats_higher_statistical_score(self):
        state = fixture(("ground",))
        state["step"] = 360
        state["control"]["red"] = 1800
        engine.retire(state, state["units"][0], "destroyed")
        engine.finish(state)
        self.assertEqual(state["result"]["winner"], "blue")
        self.assertGreater(number(state["result"]["scores"]["red"]["total"]), number(state["result"]["scores"]["blue"]["total"]))

    def test_failed_plan_discards_both_pending_and_limits_recovery(self):
        state = engine.submit_plan(fixture(), "red", {}, 0)
        state = engine.fail_planning(state, "blue", "MODEL_TIMEOUT")
        self.assertEqual(state["pending_plans"], {})
        for _ in range(2):
            state = engine.retry_planning(state)
            state = engine.fail_planning(state, "red", "MODEL_TIMEOUT")
        with self.assertRaises(RuleViolation):
            engine.retry_planning(state)
        self.assertEqual(state["step"], 0)
        self.assertIsNone(engine.technical_abort(state)["result"]["winner"])

    def test_same_boundary_surrenders_draw_without_advancing(self):
        state = engine.request_surrender(fixture(), "red")
        state = engine.request_surrender(state, "blue")
        state = step(state)
        self.assertIsNone(state["result"]["winner"])
        self.assertEqual(state["step"], 0)

    def test_t25_enemy_arrival_prevents_landing_without_refund(self):
        state = fixture(("recon", "ground"))
        state["units"][0].update(position=location(100), flight="airborne")
        state["units"][3]["position"] = location(130)
        state = plan(state, {"red-0": dict(action="land")}, {"blue-1": dict(action="move", destination=location(100))})
        state = step(state)
        self.assertEqual(state["units"][0]["flight"], "airborne")
        self.assertEqual(number(state["units"][0]["energy"]), Fraction(593, 6))

    def test_full_720_steps_with_serialized_restore(self):
        state = fixture(("ground",))
        planning_rounds = 0
        for index in range(720):
            if state["phase"] == "planning":
                state = plan(state)
                planning_rounds += 1
            state = step(state)
            if index % 120 == 119:
                state = json.loads(json.dumps(state))
                engine.validate(state)
        self.assertEqual(planning_rounds, 60)
        self.assertEqual(state["step"], 720)
        self.assertEqual(state["control"]["red"], 714)
        self.assertEqual(state["result"]["reason"], "time_limit")
        self.assertEqual(state["result"]["winner"], "red")
        self.assertEqual(number(state["result"]["scores"]["red"]["control"]), Fraction(833, 60))
        self.assertEqual(number(state["result"]["scores"]["red"]["resource"]), 30)
        with self.assertRaises(RuleViolation):
            step(state)

    def test_camp_selected_categories_and_partial_request_complete(self):
        state = fixture()
        unit = state["units"][0]
        unit["position"] = location(-1000)
        engine.spend(state, unit, "ammo", 5)
        engine.spend(state, unit, "energy", 20)
        state = step(plan(state, {"red-0": dict(action="camp", camp_id="red-ground", balances=dict(ammo=58))}))
        self.assertEqual(state["units"][0]["ammo"], 58)
        self.assertEqual(number(state["units"][0]["energy"]), 80)
        self.assertEqual(number(state["ledger"]["red"]["public"]["ammo"]), 597)

    def test_frozen_point_change_is_rejected(self):
        state = plan(fixture())
        state["points"][0]["xy"][0] = 10
        with self.assertRaises(RuleViolation) as caught:
            step(state)
        self.assertEqual(caught.exception.code, "POINTS_CHANGED")

    def test_camp_and_field_same_receiver_conflict(self):
        for reverse in (False, True):
            state = fixture()
            orders = {"red-1": dict(action="service", target_id="red-0", category="energy", balance=100),
                      "red-0": dict(action="camp", camp_id="red-ground")}
            if reverse:
                orders = dict(reversed(list(orders.items())))
            with self.subTest(reverse=reverse), self.assertRaises(RuleViolation):
                engine.submit_plan(state, "red", orders, 0)


if __name__ == "__main__":
    unittest.main()
