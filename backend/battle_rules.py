"""上传文档的 War Goals 子集。目标裁定由主持人逐项提交，服务器计算且不可重写。

不推断占领速度/击毁价值/骤死阈值；演示机动仍不是正式战斗引擎。
"""
from copy import deepcopy

from backend.demo_domain import host_only, require
from backend import qinglan_rules


def pending_stage(state: dict) -> bool:
    """当前时间是否卡在未确认的阶段边界。"""
    return enabled(state) and any(state["lifecycle"]["simulation_step"] >= s["stage"] * 15 and not s["confirmed"] for s in state["victory"]["stages"])

VERSION = "broken-arrow-war-goals-2026-11-02-v1"
GOALS = {**{f"capture-{i}": "capture" for i in range(1, 4)},
         **{f"destroy-{i}": "destroy" for i in range(1, 4)}}
DEFINITION = dict(mode="war_goals", rule_version=VERSION, stage_minutes=15, stages=3,
                  weights=[1, 2, 3], goals=GOALS, adjudication="host_confirmed",
                  sudden_death_enabled=False, tie_breaker=None, team_size=1)


def initialize(state: dict) -> None:
    state["manifest"]["victory_rules"] = deepcopy(DEFINITION)
    state["lifecycle"]["deadline_step"] = 45
    state["victory"] = dict(mode="war_goals", ruleVersion=VERSION, preview=False,
                            elapsedMinutes=0, stages=[dict(stage=i, redGoals=0, blueGoals=0,
                            confirmed=False, evidence="", goals=[]) for i in range(1, 4)])


def enabled(state: dict) -> bool:
    return state["manifest"].get("victory_rules") == DEFINITION


def evidence(state: dict) -> dict | None:
    if not scoring_enabled(state): return None
    value = deepcopy(state.get("victory"))
    if qinglan_rules.enabled(state):
        value["liveObjectives"] = qinglan_rules.observe(state)
    return value


def scoring_enabled(state: dict) -> bool:
    return enabled(state) or qinglan_rules.enabled(state)


def evaluate(value: dict | None) -> dict:
    """与 Unity 的同版本纯函数保持相同的计分、平局与未完成语义。"""
    result = dict(final=False, verdict="待裁定", winner=None, redPoints=0, bluePoints=0,
                  reason="缺少战争目标逐阶段结算证据，不能从步数、单位击杀或任务状态推定胜负。",
                  calculations=[], milestones=[], stageScores=[], scoreMaximum=0, redScore100=0.0, blueScore100=0.0, redPercent=0.0, bluePercent=0.0)
    project = bool(value and value.get("ruleVersion") == qinglan_rules.VERSION and value.get("mode") == qinglan_rules.MODE)
    stage_minutes, goal_limit = (20, 3) if project else (15, 6)
    if not project and (not value or value.get("ruleVersion") != VERSION or value.get("mode") != "war_goals"):
        return result
    stages, elapsed = value.get("stages"), value.get("elapsedMinutes")
    if type(elapsed) is not int or elapsed < 0 or not isinstance(stages, list) or len(stages) != 3:
        return result
    for i, stage in enumerate(stages, 1):
        if not isinstance(stage, dict) or stage.get("stage") != i or type(stage.get("confirmed")) is not bool:
            return result
        if any(type(stage.get(k)) is not int or not 0 <= stage[k] <= goal_limit for k in ("redGoals", "blueGoals")):
            return result
        if project and stage["confirmed"] and not qinglan_rules.valid_stage(stage):
            return result
        if stage["confirmed"] and not str(stage.get("evidence", "")).strip():
            return result
    if project and not qinglan_rules.valid_evidence(value):
        result['reason'] = '项目计分证据无效：阶段必须按时间顺序封存，未结算阶段不能包含点位分。'
        return result
    complete, previous_lead = True, 0
    for i, stage in enumerate(stages, 1):
        score = dict(stage=i, minute=i * stage_minutes, weight=i, settled=False,
                     redPoints=0, bluePoints=0, redTotal=result['redPoints'], blueTotal=result['bluePoints'])
        result['stageScores'].append(score)
        if elapsed < i * stage_minutes or not stage["confirmed"]:
            complete = False
            result["calculations"].append(f"阶段 {i} · {i * stage_minutes} 分钟：尚未到阶段末或缺少确认的目标证据，不计分")
            continue
        red, blue = stage["redGoals"] * i, stage["blueGoals"] * i
        result["redPoints"] += red
        result["bluePoints"] += blue
        r, b = result["redPoints"], result["bluePoints"]
        score.update(settled=True, redPoints=red, bluePoints=blue, redTotal=r, blueTotal=b)
        result["calculations"].append(f"阶段 {i} · {i * stage_minutes} 分钟结算\n红方 {stage['redGoals']} × {i} = {red} 分    蓝方 {stage['blueGoals']} × {i} = {blue} 分\n累计  {r} : {b}\n依据：{stage['evidence']}")
        if project:
            for point in sorted(stage['objectives'], key=lambda p: p['point_id']):
                red_award, blue_award = (i if point['owner'] == side else 0 for side in ('red', 'blue'))
                label = dict(red='红方驻守', blue='蓝方驻守', contested='双方争夺，不计分', neutral='无人驻守，不计分')[point['owner']]
                result['calculations'][-1] += (f"\n{point['name']} · 红{point['redUnits']} / 蓝{point['blueUnits']} · {label}"
                                               f" → 红+{red_award} / 蓝+{blue_award}")
        lead = (r > b) - (r < b)
        change = "累计战平" if not lead else ("红方" if lead > 0 else "蓝方") + ("反超" if previous_lead and previous_lead != lead else "领先")
        result["milestones"].append(f"{i * stage_minutes}′ · 阶段 {i} 完成 · {change}（{r} : {b}）")
        previous_lead = lead
    # 百分制按本模式原始理论满分折算至100分；不改变积分比较或投降裁决。
    result['scoreMaximum'] = goal_limit * (1 + 2 + 3)
    result['redScore100'] = round(result['redPoints'] * 100 / result['scoreMaximum'], 2)
    result['blueScore100'] = round(result['bluePoints'] * 100 / result['scoreMaximum'], 2)
    # 保留上一版API字段作为兼容别名；新界面使用Score100字段和分值单位。
    result['redPercent'], result['bluePercent'] = result['redScore100'], result['blueScore100']
    if not complete:
        result.update(verdict="未结算", reason="尚未完成全部三个阶段的确认结算；已知累计分不等于最终胜负。")
    elif project and result["redPoints"] == result["bluePoints"]:
        result.update(final=True, verdict="平局", reason="项目适配规则：60 分钟三阶段总分相同，记为平局；不启用加时或骤死。")
    elif result["redPoints"] == result["bluePoints"]:
        result["reason"] = "三阶段积分相同；参考文档未确认平局处理，不增加自定义加时或破平规则。"
    else:
        winner = "red" if result["redPoints"] > result["bluePoints"] else "blue"
        result.update(final=True, winner=winner, verdict="红方获胜" if winner == "red" else "蓝方获胜",
                      reason="第三阶段结束后比较总胜利点；36 分仅是理论满分，不是提前获胜门槛。")
    if project and result["final"] and result["winner"]:
        result["reason"] = "项目适配规则：60 分钟比较三阶段驻守加权总分，理论满分18；不计击毁、资源或未实现的任务分。"
    # 本项目每方仅一个席位：5 分钟后本方可直接投降，无多人投票假设。
    surrender = value.get("surrender")
    if isinstance(surrender, dict) and surrender.get("side") in ("red", "blue") and type(surrender.get("minute")) is int and 5 <= surrender["minute"] <= elapsed and surrender.get("confirmed") is True:
        winner = "blue" if surrender["side"] == "red" else "red"
        result.update(final=True, winner=winner, verdict="红方获胜" if winner == "red" else "蓝方获胜", reason="单席位队伍在对局开始 5 分钟后有效投降；不等待第三阶段。")
        result["milestones"].append(f"{surrender['minute']}′ · {'红方' if surrender['side'] == 'red' else '蓝方'}投降 · 对方获胜")
    return result


def before_advance(state: dict) -> None:
    if not enabled(state):
        return
    require(not pending_stage(state),
            "STAGE_SETTLEMENT_REQUIRED", "阶段边界需主持人确认六个目标后才能继续")


def after_advance(state: dict) -> None:
    if qinglan_rules.enabled(state):
        qinglan_rules.after_advance(state)
        update_scores(state)
        return
    if not enabled(state):
        return
    clock = state["lifecycle"]
    state["victory"]["elapsedMinutes"] = clock["simulation_step"]
    if clock["simulation_step"] in (15, 30, 45):
        clock.update(phase="paused", controller_session_id=None)
    update_scores(state)


def update_scores(state: dict) -> None:
    result = evaluate(evidence(state))
    state["scores"] = dict(available=True, winner=result["winner"], reason=result["reason"],
                           red=result["redPoints"], blue=result["bluePoints"], final=result["final"])


def adjudicate(state: dict, actor: str, payload: dict, audit: dict) -> tuple[dict, dict]:
    host_only(actor)
    require(enabled(state), "VICTORY_MODE_REQUIRED", "仅战争目标模式允许逐阶段裁定")
    clock = state["lifecycle"]
    require(clock["phase"] == "paused", "PAUSE_REQUIRED", "阶段末暂停后才能裁定")
    stage_number = payload["stage"]
    require(clock["simulation_step"] == stage_number * 15, "STAGE_NOT_DUE", "只能在对应阶段末提交裁定")
    require(all(s["confirmed"] for s in state["victory"]["stages"][:stage_number - 1]), "STAGE_ORDER_INVALID", "前序阶段尚未结算")
    stage = state["victory"]["stages"][stage_number - 1]
    require(not stage["confirmed"], "STAGE_ALREADY_SETTLED", "已确认阶段不可覆盖；使用历史快照审计")
    goals = payload["goals"]
    require(len(goals) == 6 and {g["goal_id"] for g in goals} == set(GOALS),
            "GOALS_INVALID", "必须逐项提供三个占领目标和三个摧毁目标，编号不可重复", 422)
    stage.update(confirmed=True, redGoals=sum(g["red_completed"] for g in goals),
                 blueGoals=sum(g["blue_completed"] for g in goals), goals=deepcopy(goals),
                 evidence=payload["evidence"], adjudication=dict(source="host_confirmed", **audit))
    # 双方各自完成目标，不臆造目标互斥条件。
    if stage_number == 3:
        clock.update(phase="finished", controller_session_id=None)
    update_scores(state)
    return state, dict(title=f"阶段 {stage_number} 战争目标已确认", detail=payload["evidence"], side="host", visibility="public", private_plan=False)


def surrender(state: dict, actor: str, audit: dict) -> tuple[dict, dict]:
    require(actor in ("red", "blue"), "ROLE_FORBIDDEN", "只能由本方席位投降，主持人不能代投", 403)
    require(scoring_enabled(state), "VICTORY_MODE_REQUIRED", "旧演示模式不启用正式投降")
    clock = state["lifecycle"]
    require(clock["phase"] not in ("preparing", "finished") and clock["simulation_step"] >= 5,
            "SURRENDER_NOT_AVAILABLE", "开始 5 分钟后才可投降")
    state["victory"]["surrender"] = dict(side=actor, minute=clock["simulation_step"], confirmed=True, **audit)
    clock.update(phase="finished", controller_session_id=None)
    update_scores(state)
    return state, dict(title="本方已投降", detail="单席位队伍有效投降，对方获胜", side=actor, visibility="public", private_plan=False)
