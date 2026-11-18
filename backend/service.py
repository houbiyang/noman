"""PostgreSQL 权威交互服务：身份、原子命令、不可变快照和演示边界。"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from fractions import Fraction
from importlib.metadata import version
import heapq
import hmac
import hashlib
import math
import secrets
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from psycopg import sql
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from backend.contracts import CommandRequest, CreateGame, PAYLOAD_MODELS
from backend.agent_contracts import EnableCoordination, ReviewAgentPlan
from backend.demo_domain import (
    CONDITION_NAMES, TASK_NAMES, apply_interaction, delete_unit, host_only, initial_state, map_manifest,
    new_plan, require, validate_state, waiting_sides,
)
from backend.runtime import Settings, ServiceError, canonical, sha256, token_digest, utcnow
from backend import database, spatial, movement, coordination, battle_rules, qinglan_rules, unit_interactions
from backend.map_catalog import current_map, asset_manifest, validate as validate_map
from backend import rulebook, battle_capacity, manual_battle, battle_kernel
from backend.battle_contracts import BattleDraftRequest, BattleRoundRequest
from backend import battle_setup, battle_scenario
from backend.battle_execution import Executor as BattleExecutor


HASH_VERSION = "qiongtu-json-v1"
PAYLOAD_MODELS = {**PAYLOAD_MODELS, "battle_draft": BattleDraftRequest,
    "battle_submit": BattleRoundRequest, "battle_resolve": BattleRoundRequest, "enable_coordination": EnableCoordination, "review_agent_plan": ReviewAgentPlan}
CURRENT_RULE = dict(mode="current-map-demo", formal_rules_available=False, task_types=list(TASK_NAMES),
    step_minutes=1, deadline_step=60, demo_army_per_type=dict(minimum=1, maximum=10),
    navigation="test_only", ai_source="fixed_demo", conditions_effects="record_only", spatial_profile=spatial.profile())
RULE_ID = str(uuid5(NAMESPACE_URL, "qiongtu/current-map/rules/" + sha256(CURRENT_RULE)))
SCENARIO_ID = str(uuid5(NAMESPACE_URL, "qiongtu/current-map/scenario/" + sha256(dict(rule=RULE_ID, map=current_map()["content_sha256"], assets=sha256(asset_manifest())))))

WAR_RULE = dict(CURRENT_RULE, victory_rules=battle_rules.DEFINITION, deadline_step=45)
WAR_RULE_ID = str(uuid5(NAMESPACE_URL, "qiongtu/current-map/rules/" + sha256(WAR_RULE)))
WAR_SCENARIO_ID = str(uuid5(NAMESPACE_URL, "qiongtu/current-map/scenario/" + sha256(dict(rule=WAR_RULE_ID, map=current_map()["content_sha256"], assets=sha256(asset_manifest())))))


CONTROL_RULE = dict(CURRENT_RULE, victory_rules=qinglan_rules.DEFINITION)
CONTROL_RULE_ID = str(uuid5(NAMESPACE_URL, "qiongtu/current-map/rules/" + sha256(CONTROL_RULE)))
CONTROL_SCENARIO_ID = str(uuid5(NAMESPACE_URL, "qiongtu/current-map/scenario/" + sha256(dict(rule=CONTROL_RULE_ID, map=current_map()["content_sha256"], assets=sha256(asset_manifest())))))


def insert(connection, table: str, values: dict) -> None:
    """只供服务端内部调用的参数化插入，不接受客户端表名或列名。"""
    names = list(values)
    parameters = [Jsonb(value) if isinstance(value, (dict, list)) else value for value in values.values()]
    connection.execute(sql.SQL("INSERT INTO sandbox.{} ({}) VALUES ({})").format(
        sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, names)),
        sql.SQL(",").join(sql.Placeholder() for _ in names)), parameters)


def snapshot_hash(state: dict) -> str:
    """摘要覆盖格式、状态、阶段和仿真步，恢复后重新计算。"""
    return sha256(dict(schema_version="unity-demo-world-v1", hash_format_version=HASH_VERSION,
                       simulation_step=state["lifecycle"]["simulation_step"], phase=state["lifecycle"]["phase"], state_payload=state))


def input_view(state: dict, side: str) -> dict:
    """只从本方允许内容构造建议依据，排除登记建议引起的自身变化。"""
    return dict(manifest=state["manifest"], units=[unit for unit in state["units"] if unit["side"] == side],
                tasks=[task for task in state["tasks"] if task["side"] == side])


def manual_scores(state: dict, role: str) -> dict:
    """总册11.3：双方占点分公开；赛中对手击毁/资源/总分不公开。"""
    public = role == 'host' or state['manual_battle']['stage'] == 'finished'
    result = {}
    for side in ('blue', 'red'):
        score = battle_kernel.score(state, side)
        allowed = public or role == side
        result[side + '_control_score'] = score['control']
        result[side + '_score'] = score['total'] if allowed else None
        result[side + '_breakdown'] = score if allowed else None
    return result


class SandboxService:
    """服务实例不持有可写游戏副本，全部事实以 PostgreSQL 提交为准。"""

    def __init__(self, settings: Settings, *, battle_executor=None):
        self.settings = settings
        # 仅由服务端代码装配已验收地图的执行器；不允许请求/环境开关绕过场景验收。
        self.battle_executor = battle_executor

    def seed_versions(self) -> dict:
        """登记当前地图与兼容规则版本，重复启动不重写既有场景。"""
        with self.settings.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(80644,1104)")
            rulebook.register(connection)  # 独立draft版本，不绑定或迁移当前对局。
            battle_capacity.register(connection)  # 软件容量补充也独立登记，不覆盖总册或旧局。
            self.seed_current_version(connection)
            definition = unit_interactions.definition()
            existing = connection.execute("SELECT payload_sha256 FROM rule_versions WHERE id=%s", (unit_interactions.RULE_ID,)).fetchone()
            if existing:
                require(existing['payload_sha256'] == sha256(definition), 'VERSION_CONTENT_CHANGED', '单位规则不可覆盖')
            else:
                insert(connection, 'rule_versions', dict(id=unit_interactions.RULE_ID, rule_code='qinglan-unit-interactions',
                    version_label=definition['version'], schema_version='unit-interactions-v1', hash_format_version=HASH_VERSION,
                    definition=definition, payload_sha256=sha256(definition), availability='active', is_demo=True))
            self.seed_current_version(connection, war_goals=True)
            self.seed_current_version(connection, control=True)
        return dict(rule_version_id=RULE_ID, scenario_version_id=SCENARIO_ID, is_demo=True)

    def seed_current_version(self, connection, war_goals: bool = False, control: bool = False) -> None:
        """在调用方事务中完整登记当前地图，用于初始化及授权的数据重置。"""
        rule_id, scenario_id, rule = (WAR_RULE_ID, WAR_SCENARIO_ID, WAR_RULE) if war_goals else (RULE_ID, SCENARIO_ID, CURRENT_RULE)
        if control: rule_id, scenario_id, rule = CONTROL_RULE_ID, CONTROL_SCENARIO_ID, CONTROL_RULE
        map_data = current_map()
        validate_map(map_data)
        scene = dict(schema_version="current-map-scene-v2", map=map_data, task_types=list(TASK_NAMES), asset_manifest_sha256=sha256(asset_manifest()))
        if war_goals or control: scene["victory_rules"] = deepcopy(rule["victory_rules"])
        for table, identity, definition in (("rule_versions", rule_id, rule), ("scenario_versions", scenario_id, scene)):
            existing = connection.execute(sql.SQL("SELECT payload_sha256 FROM sandbox.{} WHERE id=%s").format(sql.Identifier(table)), (identity,)).fetchone()
            if existing:
                require(existing["payload_sha256"] == sha256(definition), "VERSION_CONTENT_CHANGED", "当前版本内容变化，不能覆盖已有版本")
                continue
            values = dict(id=identity, version_label="current-" + identity[:12], schema_version="current-map-scene-v2",
                hash_format_version=HASH_VERSION, definition=definition, payload_sha256=sha256(definition), availability="active", is_demo=True)
            if table == "rule_versions":
                values["rule_code"] = "current-map-test"
            else:
                values.update(scenario_code=map_data["map_id"], rule_version_id=rule_id, asset_manifest=asset_manifest())
            insert(connection, table, values)

    def health(self) -> dict:
        """分别报告数据库就绪与当前实际功能，不伪造 AI 或规则服务就绪。"""
        with self.settings.connect() as connection:
            table_names = {row["table_name"] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='sandbox'").fetchall()}
            tables = len(table_names)
            vector = connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        required = database.EXPECTED_TABLES - {"chunk_embeddings"}
        ready = required <= table_names and (not vector or "chunk_embeddings" in table_names)
        return dict(status="ok" if ready else "not_ready", database_ready=ready,
                    business_table_count=tables, pgvector_version=vector["extversion"] if vector else None,
                    mode="current-ui-demo", real_ai=False, authoritative_combat=False,
                    authoritative_navigation=False, scheduler_enabled=self.settings.scheduler_enabled,
                    agent_orchestration=dict(engine='langgraph', version=version('langgraph'), roles_per_side=4,
                                             model_configuration='red_blue_separate', coordination_rules='opt_in_test'))

    def bootstrap(self, key: str) -> None:
        """独立引导密钥不通过 URL 获取，不以回环地址代替权限。"""
        require(hmac.compare_digest(key, self.settings.bootstrap_key), "BOOTSTRAP_UNAUTHORIZED", "引导凭据无效", 401)

    def _principal(self, connection, bearer: str, game_id: str | None = None, *, scheduler: bool = False, pausing: bool = False) -> dict:
        """每次请求检查会话过期、撤销和对局绑定。"""
        condition = "sessions.id=%s AND members.role='host'" if scheduler else "sessions.token_digest=%s"
        validity = "" if scheduler and pausing else " AND sessions.revoked_at IS NULL AND sessions.expires_at>clock_timestamp()"
        row = connection.execute("""SELECT sessions.id AS session_id,sessions.game_id,sessions.member_id,
            sessions.expires_at,members.role FROM access_sessions sessions JOIN game_members members
            ON (sessions.game_id,sessions.member_id)=(members.game_id,members.id)
            WHERE """ + condition + validity, (bearer if scheduler else token_digest(bearer),)).fetchone()
        require(row is not None, "SESSION_UNAUTHORIZED", "会话无效、已过期或已撤销", 401)
        require(game_id is None or str(row["game_id"]) == str(game_id), "GAME_NOT_FOUND", "对局不存在或无权访问", 404)
        return row

    def session(self, bearer: str) -> dict:
        """返回凭据自身信息，不返回其他席位或令牌。"""
        with self.settings.connect() as connection:
            member = self._principal(connection, bearer)
            return {key: member[key] for key in ("game_id", "role", "expires_at")}

    def knowledge_principal(self, game_id: str, bearer: str) -> dict:
        """短事务验证本局席位；返回最小上下文后关闭连接，再由路由联网。"""
        with self.settings.connect() as connection:
            member = self._principal(connection, bearer, game_id)
            require(member['role'] in ('red', 'blue'), 'KNOWLEDGE_ROLE_FORBIDDEN', '主持人不能通过业务接口读取私库', 403)
            game = self._game(connection, game_id)
            rules = game['state_payload'].get('manifest', {}).get('victory_rules', {})
            active = rules.get('rule_version') or rules.get('version') or rules.get('mode') or 'demo_no_conquest_rules'
            return dict(side=member['role'], active_game_rules=active)

    def issue_session(self, game_id: str, role: str) -> dict:
        """受控签发一个席位会话，原始令牌只在本次成功响应返回。"""
        raw = secrets.token_urlsafe(32)
        expiry = utcnow() + timedelta(hours=12)
        with self.settings.connect() as connection:
            member = connection.execute("SELECT id FROM game_members WHERE game_id=%s AND role=%s", (game_id, role)).fetchone()
            require(member is not None, "GAME_NOT_FOUND", "对局或席位不存在", 404)
            insert(connection, "access_sessions", dict(id=uuid4(), game_id=game_id, member_id=member["id"],
                   token_digest=token_digest(raw), expires_at=expiry))
        return dict(game_id=game_id, role=role, access_token=raw, token_type="bearer", expires_at=expiry)

    def revoke_session(self, bearer: str) -> dict:
        """撤销当前凭据，不删除历史席位和会话。"""
        with self.settings.connect() as connection:
            member = self._principal(connection, bearer)
            connection.execute("UPDATE access_sessions SET revoked_at=clock_timestamp() WHERE id=%s", (member["session_id"],))
        return dict(revoked=True)

    def _game(self, connection, game_id: str, *, lock: bool = False) -> dict:
        """读取一致头；所有写入先锁同一对局控制行。"""
        if lock:
            # 等锁结束后另发查询，使 READ COMMITTED 使用最新头与对应快照。
            locked = connection.execute("SELECT id FROM games WHERE id=%s FOR NO KEY UPDATE", (game_id,)).fetchone()
            require(locked is not None, "GAME_NOT_FOUND", "对局不存在或无权访问", 404)
        row = connection.execute("""SELECT games.*,snapshots.state_payload,snapshots.revision AS snapshot_revision,
            snapshots.world_revision,snapshots.payload_sha256,snapshots.schema_version AS snapshot_schema_version
            FROM games JOIN game_snapshots snapshots ON (games.id,games.active_branch_id,games.head_snapshot_id)=
            (snapshots.game_id,snapshots.branch_id,snapshots.id) WHERE games.id=%s""", (game_id,)).fetchone()
        require(row is not None, "GAME_NOT_FOUND", "对局不存在或无权访问", 404)
        return row

    def _context(self, game: dict, principal: dict) -> dict:
        """使用不可解码 HMAC 上下文，避免向对方暴露内部修订次数。"""
        identity = dict(game_id=str(game["id"]), branch_id=str(game["active_branch_id"]), snapshot_id=str(game["head_snapshot_id"]))
        basis = dict(**identity, member_id=str(principal["member_id"]), control_revision=game["control_revision"],
                     execution_epoch=str(game["execution_epoch"]))
        identity["context_token"] = hmac.new(self.settings.signing_key.encode(), canonical(basis).encode(), hashlib.sha256).hexdigest()
        return identity

    def _check_context(self, game: dict, principal: dict, context: dict) -> None:
        """写入前在锁内核对有效分支、快照与内部控制版本签名。"""
        expected = self._context(game, principal)
        require(all(str(context[key]) == expected[key] for key in ("game_id", "branch_id", "snapshot_id"))
                and hmac.compare_digest(context["context_token"], expected["context_token"]),
                "STATE_CONFLICT", "状态或分支已变化，请重新读取后提交新操作")

    def _snapshot(self, connection, game_id, branch_id, snapshot_id, state, revision, world_revision,
                  command_id=None, kind="command") -> None:
        """完整快照与关联事实在调用方事务内提交。"""
        if "manual_battle" in state:
            manual_battle.validate(state)
            if 'battle_ledger' in state:
                battle_kernel.validate(state)
                require(state['battle_ledger']['elapsed_seconds'] == state['manual_battle']['elapsed_seconds'],
                        'BATTLE_STATE_INVALID', '完整快照必须同时提交轮次与精确账本时钟')
        if unit_interactions.enabled(state):
            unit_interactions.sync_units(state)
            unit_interactions.observe(state)
            unit_interactions.validate(state)
        validate_state(state)
        insert(connection, "game_snapshots", dict(id=snapshot_id, game_id=game_id, branch_id=branch_id,
               revision=revision, world_revision=world_revision, simulation_step=state["lifecycle"]["simulation_step"],
               phase=state["lifecycle"]["phase"], snapshot_kind=kind, schema_version="unity-demo-world-v1",
               hash_format_version=HASH_VERSION, state_payload=state, payload_sha256=snapshot_hash(state), source_command_id=command_id))

    def _new_proposals(self, connection, game_id, branch_id, snapshot_id, epoch, state, previous=None, world_revision=0, restored=False) -> None:
        """为固定演示建议建立真实持久化记录，来源明确而非冒充模型调用。"""
        if manual_battle.enabled(state):
            return
        for side, plan in state["decisions"]["plans"].items():
            if restored and plan["status"] in ("adopted", "rejected"):
                continue
            old = previous["decisions"]["plans"][side] if previous else None
            if old and old["proposal_id"] == plan["proposal_id"]:
                continue
            if old:
                connection.execute("UPDATE proposals SET status='stale',updated_at=clock_timestamp() WHERE id=%s AND status IN ('draft','ready','pending_review')", (old["proposal_id"],))
            if plan.get('source') == 'multi_agent':
                continue
            view = input_view(state, side)
            source_id = plan.get("source_proposal_id")
            run_id = None
            if not source_id:
                run_id = uuid4()
                insert(connection, "ai_runs", dict(id=run_id, game_id=game_id, branch_id=branch_id, base_snapshot_id=snapshot_id,
                    side=side, execution_epoch=epoch, agent_role="commander", input_view=view,
                    model_manifest=dict(source="fixed_demo", real_model_called=False), knowledge_manifest=dict(source="none", evidence_available=False),
                    input_view_sha256=sha256(view), hash_format_version=HASH_VERSION, checkpoint_thread_id="fixed-demo/" + str(run_id),
                    status="succeeded", output_payload=plan, started_at=utcnow(), finished_at=utcnow()))
            insert(connection, "proposals", dict(id=plan["proposal_id"], game_id=game_id, branch_id=branch_id, base_snapshot_id=snapshot_id,
                   side=side, execution_epoch=epoch, ai_run_id=run_id, source_proposal_id=source_id,
                   proposal_kind="restored" if source_id else "initial", status=plan["status"], requires_confirmation=True,
                   schema_version="demo-text-plan-v1", content_payload=plan, validation_payload=dict(is_demo=True, executable_actions_generated=False),
                   based_on_world_revision=world_revision, input_view_sha256=sha256(view), hash_format_version=HASH_VERSION))

    def create_game(self, request: CreateGame, request_key: UUID) -> tuple[dict, bool]:
        """幂等创建完整对局、席位、根快照和自动存档，不返回三方凭据。"""
        require(request.is_demo, "FORMAL_MODE_UNAVAILABLE", "当前只支持明确标记的既有功能演示，正式规则尚未实现", 409)
        require(request.victory_mode != "qinglan_control" or request.spatial_test, "SPATIAL_MODE_REQUIRED", "青岚驻守计分必须启用空间测试", 422)
        require(not request.unit_interactions or request.spatial_test, 'SPATIAL_MODE_REQUIRED', '单位交互需要空间测试', 422)
        rules = deepcopy(CURRENT_RULE["spatial_profile"]) if request.spatial_test else None
        rule_id, scenario_id = (WAR_RULE_ID, WAR_SCENARIO_ID) if request.victory_mode == "war_goals" else (RULE_ID, SCENARIO_ID)
        if request.victory_mode == "qinglan_control": rule_id, scenario_id = CONTROL_RULE_ID, CONTROL_SCENARIO_ID
        require(request.rule_version_id in (None, UUID(rule_id)) and request.scenario_version_id in (None, UUID(scenario_id)),
                "VERSION_UNSUPPORTED", "请选择已登记的当前演示版本", 422)
        content = request.model_dump(mode="json", exclude={"spatial_test"} if not request.spatial_test else set())
        if request.victory_mode == "none": content.pop("victory_mode", None)
        if not request.unit_interactions: content.pop('unit_interactions', None)
        creation_hash = sha256(content)
        with self.settings.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(request_key),))
            existing = connection.execute("SELECT id,creation_payload_sha256 FROM games WHERE creation_request_key=%s", (request_key,)).fetchone()
            if existing:
                require(existing["creation_payload_sha256"] == creation_hash, "IDEMPOTENCY_KEY_REUSED", "同一创建请求键不能用于不同输入")
                return self._created(connection, existing["id"]), True
            versions = connection.execute("""SELECT scenarios.id FROM scenario_versions scenarios JOIN rule_versions rules
                ON scenarios.rule_version_id=rules.id WHERE scenarios.id=%s AND rules.id=%s AND scenarios.is_demo AND rules.is_demo
                AND scenarios.availability='active' AND rules.availability='active'""", (scenario_id, rule_id)).fetchone()
            require(versions is not None, "SCENARIO_NOT_READY", "请先显式初始化演示版本", 503)
            game_id, branch_id, snapshot_id, epoch = uuid4(), uuid4(), uuid4(), uuid4()
            state = initial_state(scenario_id, rule_id, request.task_type)
            state["manifest"]["capture_layout_sha256"] = current_map()["import_audit"]["capture_layout_sha256"]
            if request.victory_mode == "war_goals": battle_rules.initialize(state)
            if rules:
                state["manifest"].update(spatial_profile=rules, map_source_sha256=map_manifest()["source_file_sha256"])
                state["camps"] = []
                state["logistics"] = dict(formal_rules_available=False, test_only=True)
            if request.unit_interactions: unit_interactions.initialize(state)
            if request.victory_mode == "qinglan_control": qinglan_rules.initialize(state)
            insert(connection, "games", dict(id=game_id, game_no="QT-" + game_id.hex[:16].upper(), title=request.title,
                   creation_request_key=request_key, creation_payload_sha256=creation_hash, creation_hash_format_version=HASH_VERSION,
                   scenario_version_id=scenario_id, rule_version_id=rule_id, active_branch_id=branch_id, head_snapshot_id=snapshot_id,
                   control_revision=0, execution_epoch=epoch, is_demo=True))
            insert(connection, "game_branches", dict(id=branch_id, game_id=game_id))
            for role in ("host", "red", "blue"):
                insert(connection, "game_members", dict(id=uuid4(), game_id=game_id, role=role))
            self._snapshot(connection, game_id, branch_id, snapshot_id, state, 0, 0, kind="initial")
            insert(connection, "game_saves", dict(id=uuid4(), game_id=game_id, branch_id=branch_id, snapshot_id=snapshot_id, save_kind="auto"))
            self._new_proposals(connection, game_id, branch_id, snapshot_id, epoch, state)
            return self._created(connection, game_id), False

    def _manual_sources(self):
        """正式新局只消费服务端适配器已绑定的发布场景，不从请求选择accepted内容。"""
        from backend.battle_map_geometry import MapGeometry, LocalManualMapGeometry, OfflineMapGeometry
        executor = self.battle_executor
        if isinstance(executor, BattleExecutor) and (type(executor.geometry) in (MapGeometry, LocalManualMapGeometry)
                or (getattr(self, '_acceptance_only', False) and type(executor.geometry) is OfflineMapGeometry)):
            executor.geometry._verify_sources()
            geometry = executor.geometry
            return deepcopy(geometry.rules), deepcopy(geometry.scenario), deepcopy(geometry.capacity)
        return battle_setup.sources(hover_energy=True)

    def install_manual_geometry(self, geometry):
        """显式受控装配；未验收真实来源立即报错，不更改现有游戏/数据库发布状态。"""
        from backend.battle_map_geometry import MapGeometry, LocalManualMapGeometry
        require(type(geometry) in (MapGeometry, LocalManualMapGeometry), 'BATTLE_ENGINE_NOT_READY', '只装配已验收的真实地图适配器', 503)
        geometry._verify_sources()
        self._acceptance_only = False
        self.battle_executor = BattleExecutor(geometry)

    def install_acceptance_geometry(self, geometry):
        """显式离线HTTP验收入口；候选仍非发布资格，用户库永远拒绝。"""
        from backend.battle_map_geometry import OfflineMapGeometry
        require(type(geometry) is OfflineMapGeometry, 'BATTLE_ENGINE_NOT_READY', '验收入口仅接真实离线候选', 503)
        with self.settings.connect() as connection:
            battle_scenario.require_acceptance_database(connection, self.settings.connection['database'])
        geometry._verify_sources()
        self._acceptance_only = True
        self.battle_executor = BattleExecutor(geometry)

    def manual_setup(self) -> dict:
        """引导权限保护的配置目录，无凭据、对局状态或隐式登记。"""
        from backend.battle_preflight import setup_acceptance_checks
        rules, scenario, capacity = self._manual_sources()
        from backend.battle_map_geometry import source_readiness
        geometry = getattr(self.battle_executor, 'geometry', None)
        from backend.battle_map_geometry import MapGeometry, LocalManualMapGeometry, OfflineMapGeometry
        geometry_readiness = geometry.readiness() if type(geometry) in (MapGeometry, LocalManualMapGeometry, OfflineMapGeometry) else source_readiness()
        scope = {key: deepcopy(scenario[key]) for key in ('release_scope', 'competitive_ready', 'limitations') if key in scenario}
        return dict(**scope, recommended_setup=deepcopy(getattr(self, 'manual_recommended_setup', None)), geometry_readiness=geometry_readiness, **battle_setup.versions(rules, scenario, capacity), mode='manual', round_seconds=60,
            maximum_rounds=60, field_energy_service_policy=scenario.get('field_energy_service_policy'),
            capacity=capacity['capacity'], map=scenario['map'],
            capture_points=scenario['capture_points'], acceptance=scenario['acceptance'],
            checks=setup_acceptance_checks(scenario, self.battle_executor),
            can_create=False, message='请查看下方待完成验收项；配置提交不代表开战，本接口不自动发布。')

    def create_manual_battle(self, request: battle_setup.CreateManualBattle, request_key: UUID) -> tuple[dict, bool]:
        """独立新局事务；相同创建键串行，根快照/名册/席位/存档一次提交。"""
        from backend.battle_preflight import command_schema_check
        content = dict(protocol='qinglan-manual-creation-v1', payload=request.model_dump(mode='json'))
        creation_hash = sha256(content)
        with self.settings.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(request_key),))
            existing = connection.execute('SELECT id,creation_payload_sha256 FROM games WHERE creation_request_key=%s',
                                          (request_key,)).fetchone()
            if existing:
                require(existing['creation_payload_sha256'] == creation_hash, 'IDEMPOTENCY_KEY_REUSED',
                        '同一创建请求键不能用于不同输入')
                return self._created(connection, existing['id']), True
            rules, scenario, capacity = self._manual_sources()
            binding = battle_setup.versions(rules, scenario, capacity)
            require(all(str(getattr(request, key)) == value for key, value in binding.items()),
                    'BATTLE_VERSION_CHANGED', '配置版本或摘要已变化，请重新读取配置', 409)
            candidate = False
            if getattr(self, '_acceptance_only', False):
                from backend.battle_map_geometry import OfflineMapGeometry
                battle_scenario.require_acceptance_database(connection, self.settings.connection['database'])
                candidate = (type(getattr(self.battle_executor, 'geometry', None)) is OfflineMapGeometry
                    and scenario.get('release_scope') == battle_scenario.LOCAL_MANUAL_SCOPE
                    and scenario.get('acceptance', {}).get('status') == 'local_manual_test_candidate')
            require(battle_scenario.manual_accepted(scenario) or candidate, 'BATTLE_SCENARIO_NOT_READY',
                    '场景尚未通过对应范围真实地图验收；不会发布草案或迁移旧局', 503)
            rows = connection.execute("""SELECT id,definition,payload_sha256,availability FROM rule_versions
                WHERE id IN (%s,%s) FOR SHARE""", (binding['rule_version_id'], binding['capacity_version_id'])).fetchall()
            registered = {str(row['id']): row for row in rows}
            for key, value, digest in (('rule_version_id', rules, binding['rule_sha256']),
                                        ('capacity_version_id', capacity, binding['capacity_sha256'])):
                row = registered.get(binding[key])
                require(row and row['definition'] == value and row['payload_sha256'] == digest
                        and row['availability'] == 'active', 'BATTLE_VERSION_NOT_READY',
                        '总册或容量版本未发布或登记内容不一致', 503)
            row = connection.execute("""SELECT rule_version_id,definition,payload_sha256,availability
                FROM scenario_versions WHERE id=%s FOR SHARE""", (binding['scenario_version_id'],)).fetchone()
            require(row and str(row['rule_version_id']) == binding['rule_version_id']
                    and row['definition'] == scenario and row['payload_sha256'] == binding['scenario_sha256']
                    and row['availability'] == 'active', 'BATTLE_SCENARIO_NOT_READY',
                    '独立总册场景尚未发布或登记不一致', 503)
            constraint = connection.execute("""SELECT c.convalidated,pg_get_constraintdef(c.oid) AS expression
                FROM pg_constraint c WHERE c.conrelid='sandbox.game_commands'::regclass
                AND c.conname='game_commands_command_type_check' AND c.contype='c'""").fetchone()
            require(command_schema_check(constraint)['status'] == 'passed', 'BATTLE_COMMAND_SCHEMA_NOT_READY',
                    '请先核对手动及跨分支命令约束，不会自动迁移', 503)
            executor = self.battle_executor
            require(isinstance(executor, BattleExecutor), 'BATTLE_ENGINE_NOT_READY',
                    '尚未装配总册地图执行器；不会使用旧引擎或预演', 503)
            state = battle_setup.build(request, rules, scenario, capacity, executor.geometry)
            state = executor.initial_observations(deepcopy(state))
            for camp in state['camps']:
                camp['deployment_validated'] = True
            game_id, branch_id, snapshot_id, epoch = uuid4(), uuid4(), uuid4(), uuid4()
            insert(connection, 'games', dict(id=game_id, game_no='QT-' + game_id.hex[:16].upper(), title=request.title,
                creation_request_key=request_key, creation_payload_sha256=creation_hash, creation_hash_format_version=HASH_VERSION,
                scenario_version_id=binding['scenario_version_id'], rule_version_id=binding['rule_version_id'],
                active_branch_id=branch_id, head_snapshot_id=snapshot_id, control_revision=0, execution_epoch=epoch, is_demo=True))
            insert(connection, 'game_branches', dict(id=branch_id, game_id=game_id))
            for role in ('host', 'red', 'blue'):
                insert(connection, 'game_members', dict(id=uuid4(), game_id=game_id, role=role))
            self._snapshot(connection, game_id, branch_id, snapshot_id, state, 0, 0, kind='initial')
            insert(connection, 'game_saves', dict(id=uuid4(), game_id=game_id, branch_id=branch_id,
                                                snapshot_id=snapshot_id, save_kind='auto'))
            # 手动局不生成固定AI建议，不签发任何席位令牌。
            return self._created(connection, game_id), False

    def _created(self, connection, game_id) -> dict:
        """生成受控创建摘要，不暴露内部快照内容。"""
        game = self._game(connection, game_id)
        return dict(game_id=str(game["id"]), game_no=game["game_no"], title=game["title"], is_demo=game["is_demo"],
                    phase=game["state_payload"]["lifecycle"]["phase"], scenario_version_id=str(game["scenario_version_id"]),
                    rule_version_id=str(game["rule_version_id"]), creation_request_key=str(game["creation_request_key"]),
                    creation_payload_sha256=game["creation_payload_sha256"])

    def creation(self, request_key: UUID) -> dict:
        """响应丢失后按原创建键核对结果。"""
        with self.settings.connect() as connection:
            row = connection.execute("SELECT id FROM games WHERE creation_request_key=%s", (request_key,)).fetchone()
            require(row is not None, "CREATION_NOT_FOUND", "尚未查询到已提交的创建结果", 404)
            return self._created(connection, row["id"])

    def _project(self, game: dict, principal: dict, state: dict) -> dict:
        """单位、建议、任务和观察按同一角色过滤，永不返回原始整局快照。"""
        role = principal["role"]
        own = lambda values: deepcopy([value for value in values if role == "host" or value["side"] == role])
        manual = manual_battle.enabled(state)
        wait = ([side for side, submitted in state["manual_battle"]["submitted"].items() if not submitted]
                if manual else waiting_sides(state))
        clock = {key: value for key, value in state["lifecycle"].items() if key != "controller_session_id"}
        source = state['decisions']['plans'][role].get('source', 'fixed_demo') if role != 'host' else 'role_filtered'
        decisions = dict(source=source, real_ai=source == 'multi_agent', waiting_sides=wait)
        if manual:
            decisions = dict(source="local_manual", real_ai=False, waiting_sides=wait)
        if role != "host" and not manual:
            decisions["own_plan"] = deepcopy(state["decisions"]["plans"][role])
        phase = state["lifecycle"]["phase"]
        finished = phase == "finished"
        unit_info = state.get('unit_interactions')
        projected_units = own(state['units'])
        projected_contacts = deepcopy(state['observations'].get(role, {}).get('contacts', []))
        if manual and 'battle_ledger' in state:
            # 防御性白名单：旧快照/测试适配器也不能借contact带出敌方库存或规划。
            projected_contacts = [{key: deepcopy(value) for key, value in contact.items() if key in
                ('unit_id', 'life_id', 'side', 'unit_type', 'name', 'position', 'position_mm',
                 'surface_id', 'durability', 'retired', 'air_state', 'last_seen_step', 'observed_at')}
                for contact in projected_contacts]
            profiles = battle_kernel.profiles()
            for unit in projected_units + projected_contacts:
                profile = profiles[unit['unit_type']]
                # 前端既有数字DTO仅作显示，精确有理数账本仍只读保存在battle_values。
                unit['battle_values'] = {key: str(unit[key]) for key in
                    ('durability', 'energy', 'ammo') if key in unit}
                for key in ('durability', 'energy', 'ammo', 'speed_m_per_minute'):
                    if key in unit:
                        unit[key] = float(Fraction(str(unit[key])))
                if 'cargo' in unit:
                    unit['stocks'] = {key: float(Fraction(str(value))) for key, value in unit['cargo'].items()}
                unit['current_speed_m_per_minute'] = unit.get('speed_m_per_minute', 0)
                unit['speed_m_per_minute'] = profile['speed_m_per_minute']
                unit.update(max_durability=profile['durability'], max_energy=profile['energy'],
                    max_ammo=profile['ammo'], base_speed_m_per_minute=profile['speed_m_per_minute'],
                    docked=unit.get('air_state') == 'docked')
        visible_targets = {u['unit_id'] for u in state['units'] if role == 'host' or u['side'] == role}
        visible_targets.update(c['unit_id'] for c in state['observations'].get(role, {}).get('contacts', []))
        for unit in projected_units:
            if manual:
                task = unit.get('task') or {}
                unit['movement_active'] = bool(unit.get('movement'))
                unit['interaction_action'] = task.get('action', '')
                target_id = task.get('target_id', '')
                unit['interaction_target_id'] = target_id if target_id in visible_targets else ''
                # 公开摘要后移除内部任务/移动计划，避免绕过own_drafts隔离。
                for key in ('task', 'movement', 'movement_progress', 'barracks_registration'):
                    unit.pop(key, None)
            if unit_info:
                order = unit_info.get('orders', {}).get(unit['unit_id'], {})
                unit['interaction_action'] = order.get('action', '')
                target_id = order.get('target_id', '')
                unit['interaction_target_id'] = target_id if target_id in visible_targets else ''

            if 'spatial_profile' in state['manifest']:
                limits = state['manifest']['spatial_profile']['unit_profiles'][unit['unit_type']]
                if unit.get('durability') is not None: unit.setdefault('max_durability', limits['durability'])
                unit.setdefault('speed_m_per_minute', limits['speed_m_per_step'])
        if manual:
            for contact in projected_contacts:
                for key in ('task', 'movement', 'movement_progress', 'barracks_registration',
                            'interaction_action', 'interaction_target_id', 'movement_active'):
                    contact.pop(key, None)
        result = dict(schema_version="game-view-v1", context=self._context(game, principal), role=role,
                    unit_interactions=(dict(enabled=True, rule_version_id=unit_info['rule_version_id'],
                        elapsed_seconds=unit_info['elapsed_seconds'], geometry_policy=unit_info['rules']['geometry_policy']) if unit_info else None),
                    game=dict(game_id=str(game["id"]), game_no=game["game_no"], title=game["title"], is_demo=game["is_demo"],
                              scenario_version_id=str(game["scenario_version_id"]), rule_version_id=str(game["rule_version_id"])),
                    clock=clock, units=projected_units, attack_events=unit_interactions.attack_events(state, role),
                    camps=own(state["camps"]), tasks=own(state["tasks"]),
                    contacts=projected_contacts,
                    last_seen_records=deepcopy(state["observations"].get(role, {}).get("last_seen_records", [])), decisions=decisions,
                    capabilities=dict(can_control_time=role == "host" and phase not in ("preparing", "finished"),
                                      can_start_game=role == "host" and phase == "preparing" and all(state["lifecycle"]["ready"].values()),
                                      can_advance=role == "host" and phase not in ("preparing", "finished") and not wait and not battle_rules.pending_stage(state),
                                      can_review_proposal=role != "host" and not finished and state["decisions"]["plans"][role]["status"] == "pending_review",
                                      can_set_ready=role != "host" and phase == "preparing", can_save=role == "host",
                                      can_manage_units=phase not in ("running", "finished"),
                                      can_move_unit=phase in ("paused", "running") if "spatial_profile" in state["manifest"] else not finished,
                                      can_place_barracks=phase == "preparing", can_delete_unit=phase not in ("running", "finished"),
                                      can_test_movement=phase == "preparing" and "spatial_profile" in state["manifest"],
                                      can_settle_goal_stage=role == "host" and battle_rules.enabled(state) and phase == "paused"
                                          and clock["simulation_step"] in (15,30,45) and any(not s["confirmed"] and s["stage"] * 15 == clock["simulation_step"] for s in state["victory"]["stages"]),
                                      can_surrender=role in ("red", "blue") and battle_rules.scoring_enabled(state) and phase not in ("preparing", "finished") and clock["simulation_step"] >= 5,
                                      authoritative_combat=False, authoritative_navigation=False),
                    resources=dict(instruction_budget=20, executed_instructions=None, source="demo_limit_only"),
                    map=dict(map_id=map_manifest()["map_id"], resource_url="/api/v1/maps/current", coordinate_system_id="unity-demo-120x80-v1",
                             navigation_status="visual_target_only"),
                    save_status=dict(status="persisted", snapshot_id=str(game["head_snapshot_id"])),
                    victory=battle_rules.evidence(state), battle_result=battle_rules.evaluate(battle_rules.evidence(state)),
                    service_status=dict(mode="current-ui-demo", real_ai=False, condition_effects="record_only", scoring=battle_rules.scoring_enabled(state)), observed_at=utcnow())
        if manual:
            # 旧三点/阶段计分不能成为总册手动局的备用结果，避免页面误报100分战果。
            result['victory'] = None
            result['battle_result'] = None
            result['manual_battle'] = manual_battle.projection(state, role)
            for key in ('release_scope', 'competitive_ready', 'limitations'):
                if key in state['manifest']:
                    result['manual_battle'][key] = deepcopy(state['manifest'][key])
            if state['manifest'].get('field_energy_service_policy') == battle_kernel.FIELD_ENERGY_SERVICE_POLICY:
                result['manual_battle']['field_energy_service_policy'] = battle_kernel.FIELD_ENERGY_SERVICE_POLICY
            if 'battle_ledger' in state:
                result['battle_capture_points'] = []
                for point in state['battle_points']:
                    item = {key: point[key] for key in
                            ('point_id', 'owner', 'advancing_side', 'progress_steps', 'contested')}
                    hidden = role != 'host' and not finished and point['advancing_side'] != role
                    item['progress_hidden'] = hidden
                    if hidden:
                        item.update(advancing_side='none', progress_steps=0)
                    result['battle_capture_points'].append(item)
                result['manual_battle'].update(manual_scores(state, role))
                result['manual_battle'].update(winner=(state.get('battle_result') or {}).get('winner', ''),
                    finish_reason=(state.get('battle_result') or {}).get('reason', ''))
            for key in ('can_control_time', 'can_start_game', 'can_advance', 'can_review_proposal',
                        'can_set_ready', 'can_manage_units', 'can_move_unit', 'can_place_barracks',
                        'can_delete_unit', 'can_test_movement', 'can_settle_goal_stage', 'can_surrender'):
                result['capabilities'][key] = False
            # 协议已接入不代表执行器已验收；公开可结算能力保持关闭。
            ready = self.battle_executor is not None and self.battle_executor.supports(state)
            result['manual_battle']['can_resolve'] = ready and result['manual_battle']['can_resolve']
            result['manual_battle']['engine_ready'] = ready
            result['service_status'].update(mode='local-manual', real_ai=False)
        return result

    def battle_rounds(self, game_id: str, bearer: str, round_number: int | None = None) -> dict:
        """主持人专属、只读、活动分支已提交前缀；不返回将来帧、不推进游戏。"""
        from backend.battle_replay_store import read_rounds
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            host_only(principal['role'])
            game = self._game(connection, game_id)
            require(manual_battle.enabled(game['state_payload']), 'MANUAL_MODE_REQUIRED', '旧对局没有总册手动轮次记录')
            return read_rounds(connection, game, round_number)

    def view(self, game_id: str, bearer: str) -> dict:
        """返回同一提交头的角色视图。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            game = self._game(connection, game_id)
            result = self._project(game, principal, game["state_payload"])
            state = game["state_payload"]
            if 'coordination' in state:
                result['coordination'] = {key: deepcopy(value) for key, value in state['coordination'].items() if key != 'plans'}
                result['coordination']['plans'] = ({principal['role']: deepcopy(state['coordination']['plans'][principal['role']])}
                    if principal['role'] in state['coordination']['plans'] else {})
                result['scores'] = deepcopy(state['scores'])
                result['service_status'].update(scoring=True, coordination_test=True, real_ai=result['decisions']['real_ai'])
            visible_units = {unit["unit_id"] for unit in result["units"]}
            result["conditions"] = [dict(condition) for condition in state["conditions"]["instances"]
                if condition["condition_type"] == "weather_change" or condition["target_unit_id"] in visible_units]
            result["map"]["content_hash"] = map_manifest()["content_hash"]
            result["spatial_test"] = "spatial_profile" in state["manifest"]
            if result["spatial_test"]:
                for unit in result['units']:
                    unit['in_barracks']=movement.is_garrisoned(state,unit)
                    if unit['in_barracks']:
                        unit['position']=deepcopy(next(camp['position'] for camp in state['camps'] if camp['camp_id']==unit['camp_id']))
                spatial.map_data(state)
                result["routes"] = [deepcopy(route) for route in state["routes"] if route["unit_id"] in visible_units]
                result["movement_paths"] = [dict(unit_id=route['unit_id'], route_id=route['route_id'], status=route['status'],
                    mode=route['mode'], deployment_test=route['deployment_test'], trace_sequence=route['trace_sequence'],
                    formation_leader_id=route.get('formation_leader_id'),
                    estimated_steps=route['estimated_steps'], estimated_energy=route['estimated_energy'],
                    points=[dict(x=point[0],z=point[1]) for point in route.get('last_trace',[])],
                    route_points=[dict(x=point[0],z=point[1]) for point in route['points']])
                    for route in state['routes'] if route['unit_id'] in visible_units and route.get('movement_profile')]
            result["timeline"] = self._visible_events(connection, game_id, game["active_branch_id"],
                                                      game["snapshot_revision"], principal["role"])[-100:]
            return result

    def routes(self, game_id: str, bearer: str, unit_type: str, from_node_id: str, to_node_id: str) -> dict:
        """基于已锁定地图道路返回确定性路线；不把路线结果扩展为战斗或地形裁决。"""
        require(unit_type in ("ground", "support"), "UNIT_TYPE_INVALID", "道路节点查询仅支持陆兵和保障；无人机采用空中航段", 422)
        with self.settings.connect() as connection:
            self._principal(connection, bearer, game_id)
            game = self._game(connection, game_id)
        manifest = map_manifest()
        nodes = {node["id"]: node for node in manifest["nodes"]}
        require(from_node_id in nodes and to_node_id in nodes, "MAP_NODE_NOT_FOUND", "起点或终点不属于当前地图", 404)
        adjacency = {node_id: [] for node_id in nodes}
        for road in manifest["roads"]:
            start, end = road["startId"], road["endId"]
            require(start in nodes and end in nodes, "MAP_TOPOLOGY_INVALID", "地图道路引用了不存在的节点", 500)
            distance = math.hypot(float(nodes[start]["x"]) - float(nodes[end]["x"]), float(nodes[start]["z"]) - float(nodes[end]["z"]))
            adjacency[start].append((end, distance, road))
            adjacency[end].append((start, distance, road))
        queue = [(0.0, from_node_id)]
        distances = {from_node_id: 0.0}
        previous = {}
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != distances.get(current):
                continue
            if current == to_node_id:
                break
            for neighbor, edge_distance, road in adjacency[current]:
                candidate = distance + edge_distance
                if candidate < distances.get(neighbor, float("inf")):
                    distances[neighbor] = candidate
                    previous[neighbor] = (current, road)
                    heapq.heappush(queue, (candidate, neighbor))
        require(to_node_id in distances, "ROUTE_UNAVAILABLE", "当前地图道路网络没有连通起点和终点", 409)
        node_ids, road_ids, bridge_count = [to_node_id], [], 0
        cursor = to_node_id
        while cursor != from_node_id:
            parent, road = previous[cursor]
            node_ids.append(parent)
            road_ids.append(road["id"])
            bridge_count += int(bool(road.get("isBridge")))
            cursor = parent
        node_ids.reverse(); road_ids.reverse()
        route_key = f"{manifest['map_id']}:{unit_type}:{from_node_id}:{to_node_id}"
        return dict(schema_version="route-view-v1", game_id=game_id, branch_id=str(game["active_branch_id"]),
                    map_id=manifest["map_id"], map_content_hash=manifest["content_hash"],
                    route_id=hashlib.sha256(route_key.encode("utf-8")).hexdigest(), movement_type="ground",
                    unit_type=unit_type, from_node_id=from_node_id, to_node_id=to_node_id,
                    node_ids=node_ids, road_ids=road_ids, bridge_count=bridge_count,
                    distance_meters=round(distances[to_node_id], 3), eligibility="needs_verification",
                    reason="静态道路拓扑可达；正式地形、能耗和交战效果仍由 S4 规则包裁决",
                    estimated_completion_step=None, unknowns=["terrain_effects", "energy_cost", "combat_effects"])

    def preview_preparation(self, game_id: str, bearer: str, context: dict, payload: dict) -> dict:
        """准备预览不写快照，成功凭据绑定席位、参数和当前有效头。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            game = self._game(connection, game_id)
            self._check_context(game, principal, context)
            require("spatial_profile" in game["state_payload"]["manifest"], "SPATIAL_TEST_REQUIRED", "此局没有启用空间测试规则")
            candidate, _ = spatial.set_preparation(game["state_payload"], principal["role"], payload)
            clean = {key: value for key, value in payload.items() if key != "preview_token"}
            preview = self.settings.sign(dict(kind="preparation-preview", context_token=context["context_token"],
                member_id=str(principal["member_id"]), input_sha256=sha256(clean), expires_at=int((utcnow() + timedelta(minutes=5)).timestamp())))
            return dict(valid=True, test_only=True, preview_token=preview,
                camp=next((camp for camp in candidate["camps"] if camp["side"] == principal["role"]), None))

    def _visible_events(self, connection, game_id, branch_id, ceiling, role: str, *, exclude_private_plans=False) -> list[dict]:
        """在指定不可变头内继承分支前缀，地图视图与事件列表共用可见性规则。"""
        segments, visited = [], set()
        while branch_id is not None:
            require(str(branch_id) not in visited, "HISTORY_INVALID", "历史分支存在循环", 500)
            visited.add(str(branch_id))
            segments.append((branch_id, ceiling))
            source = connection.execute("""SELECT branches.parent_branch_id,snapshots.revision FROM game_branches branches
                LEFT JOIN game_snapshots snapshots ON branches.fork_snapshot_id=snapshots.id
                WHERE branches.game_id=%s AND branches.id=%s""", (game_id, branch_id)).fetchone()
            require(source is not None, "HISTORY_INVALID", "历史分支来源缺失", 500)
            branch_id, ceiling = source["parent_branch_id"], source["revision"]
        visible = []
        for branch_id, ceiling in reversed(segments):
            rows = connection.execute("""SELECT id,simulation_step,payload FROM game_events WHERE game_id=%s AND branch_id=%s
                AND snapshot_revision<=%s AND event_type<>'battle_round' AND (visibility='public' OR visibility=%s
                OR (%s='host' AND NOT COALESCE((payload->>'private_plan')::boolean,false)))
                ORDER BY snapshot_revision,event_index""", (game_id, branch_id, ceiling, role, role)).fetchall()
            visible.extend(dict(event_id=str(row["id"]), simulation_step=row["simulation_step"],
                title=row["payload"]["title"], detail=row["payload"]["detail"], side=row["payload"]["side"]) for row in rows if not exclude_private_plans or not row["payload"].get("private_plan", False))
        return visible

    def proposal(self, game_id: str, proposal_id: UUID, bearer: str) -> dict:
        """只向本方返回建议内容；主持人不能借查询读取私有草稿。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            proposal = connection.execute("SELECT side,content_payload,status FROM proposals WHERE game_id=%s AND id=%s", (game_id, proposal_id)).fetchone()
            require(proposal is not None and principal["role"] == proposal["side"], "PROPOSAL_NOT_FOUND", "建议不存在或无权访问", 404)
            return dict(**proposal["content_payload"], persisted_status=proposal["status"])

    def preview_condition(self, game_id: str, bearer: str, context: dict, condition: dict) -> dict:
        """只预览现有事件登记，明确无故障、天气等业务效果。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            host_only(principal["role"])
            game = self._game(connection, game_id)
            self._check_context(game, principal, context)
            require(game["state_payload"]["lifecycle"]["phase"] != "finished", "GAME_FINISHED", "结束后不能登记事件")
            require(any(unit["unit_id"] == condition["target_unit_id"] for unit in game["state_payload"]["units"]),
                    "TARGET_INVALID", "事件目标不属于当前对局", 422)
            payload = dict(kind="condition-preview", game_id=game_id, session_id=str(principal["session_id"]),
                           context_token=context["context_token"], input_sha256=sha256(condition), expires_at=int((utcnow() + timedelta(minutes=5)).timestamp()))
            return dict(valid=True, effect_applied=False, effect_status="record_only", summary=CONDITION_NAMES[condition["condition_type"]] + "仅登记演示事实",
                        preview_token=self.settings.sign(payload))

    def _receipt(self, row: dict, replayed: bool = False) -> dict:
        """收据不返回原请求和内部数据缓存。"""
        return dict(command_id=str(row["id"]), request_key=str(row["request_key"]), command_type=row["command_type"], status=row["status"],
                    origin_branch_id=str(row["origin_branch_id"]), result=row["result_payload"], error=row["error_payload"],
                    completed_at=row["completed_at"], replayed=replayed)

    def command(self, game_id: str, bearer: str, request_key: UUID, request: CommandRequest, *, scheduler: bool = False) -> tuple[dict, int]:
        """原子处理幂等命令；业务拒绝使用保存点回滚其副作用后持久化收据。"""
        model = PAYLOAD_MODELS.get(request.command_type)
        require(model is not None, "COMMAND_UNSUPPORTED", "该命令不属于当前已实现范围", 422)
        try:
            payload = model.model_validate(request.payload).model_dump(mode="json", exclude_none=True)
        except ValidationError as error:
            raise ServiceError(422, "PAYLOAD_INVALID", "命令参数类型、范围或字段不符合契约") from error
        context = request.context.model_dump(mode="json")
        require(game_id == context["game_id"], "CONTEXT_INVALID", "路径与上下文的对局不一致", 422)
        request_hash = sha256(dict(command_type=request.command_type, context=context, payload=payload))
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id, scheduler=scheduler, pausing=request.command_type == "pause")
            game = self._game(connection, game_id, lock=True)
            # 锁内复核会话；排队等待不能使已撤销凭据继续写入。
            principal = self._principal(connection, bearer, game_id, scheduler=scheduler, pausing=request.command_type == "pause")
            if scheduler:
                require(game["state_payload"]["lifecycle"].get("controller_session_id") == str(principal["session_id"]),
                        "CONTROLLER_STALE", "播放控制资格已经变化")
            existing = connection.execute("""SELECT * FROM game_commands WHERE game_id=%s AND origin_branch_id=%s
                AND actor_member_id=%s AND request_key=%s""", (game_id, context["branch_id"], principal["member_id"], request_key)).fetchone()
            if existing:
                require(existing["request_hash"] == request_hash, "IDEMPOTENCY_KEY_REUSED", "同一请求键不能用于不同输入")
                return self._receipt(existing, True), int((existing["error_payload"] or {}).get("http_status", 200))
            self._check_context(game, principal, context)
            require(game["is_demo"], "FORMAL_MODE_UNAVAILABLE", "当前服务没有正式仿真规则")
            command_id = uuid4()
            insert(connection, "game_commands", dict(id=command_id, game_id=game_id, origin_branch_id=context["branch_id"],
                actor_member_id=principal["member_id"], request_key=request_key, request_hash=request_hash, hash_format_version=HASH_VERSION,
                command_type=request.command_type, expected_control_revision=game["control_revision"], base_snapshot_id=context["snapshot_id"],
                request_payload=payload, source_kind="scheduler" if scheduler else "human", execution_epoch=game["execution_epoch"], status="accepted"))
            http_status = 200
            try:
                with connection.transaction():
                    result = self._execute(connection, game, principal, command_id, request.command_type, payload)
                    connection.execute("""UPDATE game_commands SET status='succeeded',result_branch_id=%s,result_snapshot_id=%s,
                        result_payload=%s,completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s""",
                        (result["branch_id"], result["snapshot_id"], Jsonb(result), command_id))
            except ServiceError as error:
                http_status = error.status
                connection.execute("""UPDATE game_commands SET status='rejected',error_payload=%s,
                    completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s""",
                    (Jsonb(dict(code=error.code, message=error.message, http_status=error.status)), command_id))
            row = connection.execute("SELECT * FROM game_commands WHERE id=%s", (command_id,)).fetchone()
            return self._receipt(row), http_status

    def _execute(self, connection, game: dict, principal: dict, command_id, kind: str, payload: dict) -> dict:
        """在已锁定对局的事务中执行状态转换及相关表写入。"""
        old = game["state_payload"]
        manual_battle.guard_command(old, kind)
        state = deepcopy(old)
        actor = principal["role"]
        branch_id, snapshot_id, epoch = game["active_branch_id"], uuid4(), game["execution_epoch"]
        revision = game["snapshot_revision"] + 1
        world_revision = game["world_revision"]
        save_id = None
        agent_review = None
        round_record = None
        event = dict(title="演示操作已提交", detail="", side=actor, visibility="public", private_plan=False)
        if kind == "archive_match":
            archive_id = self._archive(connection, game, principal, payload.get("display_name"), False)
            return dict(branch_id=str(branch_id), snapshot_id=str(game["head_snapshot_id"]), simulation_step=state["lifecycle"]["simulation_step"], archive_id=str(archive_id))
        audit = dict(command_id=str(command_id), member_id=str(principal["member_id"]), branch_id=str(branch_id), base_snapshot_id=str(game["head_snapshot_id"]))
        if kind in ('battle_draft', 'battle_submit'):
            state = (manual_battle.draft if kind == 'battle_draft' else manual_battle.submit)(state, actor, payload)
            if kind == 'battle_submit' and self.battle_executor is not None and self.battle_executor.supports(state):
                self.battle_executor.validate_submission(state, actor)
            event.update(title='本方本轮指令已暂存' if kind == 'battle_draft' else '本方本轮指令已锁定',
                         detail='不推进时钟、不扣资源；等待主持人统一结算', side=actor, visibility=actor, private_plan=True)
        elif kind == 'battle_resolve':
            # 没有可回退的默认执行器；总册地表/资源/同步裁决验收前始终失败关闭。
            executor = self.battle_executor
            if executor is not None and not executor.supports(state):
                executor = None
            state, round_record = manual_battle.settle(state, actor, payload, executor=executor)
            world_revision += 1
            event.update(title='第%d轮共同指令已结算' % round_record['round_number'],
                         detail='结果及动画已与完整快照同时保存；播放不会再次执行指令')
        elif kind == "settle_goal_stage":
            state, event = battle_rules.adjudicate(state, actor, payload, audit)
            world_revision += 1
        elif kind == "surrender":
            state, event = battle_rules.surrender(state, actor, audit)
            world_revision += 1
        elif kind == 'enable_unit_interactions':
            unit_interactions.enable(state, actor)
            world_revision += 1
            event.update(title='单位交互已启用', detail='已冻结v1.1单位参数；一分钟内按12个五秒子步结算，地图几何为测试包络')
        elif kind == 'unit_order':
            unit_interactions.order(state, actor, payload)
            world_revision += 1
            side = next(u['side'] for u in state['units'] if u['unit_id'] == payload['unit_id'])
            event.update(title='单位任务已保存', detail=payload['action'], side=side, visibility=side)
        elif kind == 'enable_coordination':
            require(not unit_interactions.enabled(state), 'MODE_CONFLICT', '旧版协同测试和单位交互不能双重结算')
            host_only(actor)
            coordination.enable(state, payload['rules'])
            world_revision += 1
            event.update(title='协同测试规则已启用', detail='占点、观察和保障使用冻结的测试参数；原地图点位保留')
        elif kind == 'review_agent_plan':
            from backend.agent_service import review_plan
            agent_review = review_plan(connection, game, state, principal, payload)
            if state['lifecycle']['phase'] == 'awaiting_confirmation' and not waiting_sides(state):
                state['lifecycle']['phase'] = 'paused'
            event.update(title=agent_review['summary'], detail='仅采用任务图；实际动作在后续仿真步执行',
                         side=actor, visibility=actor, private_plan=True)
        elif kind == "place_barracks":
            state, event = spatial.place_barracks(state, actor, payload)
            world_revision += 1
        elif kind == "spawn_from_barracks":
            state, event = spatial.spawn_from_barracks(state, actor, payload)
            world_revision += 1
        elif kind == "movement_order":
            unit, member_count = (movement.order_formation(state, actor, payload) if payload.get('formation')
                                  else (movement.order(state, actor, payload), 1))
            if payload['mode'] not in ('stop_move','reset_test_energy'):
                unit_interactions.on_move_order(state,unit['unit_id'])
            event.update(title=unit['status'], detail='部署试走独立于对局时钟和正式能源' if state['lifecycle']['phase']=='preparing'
                         else '按已保存的兵种移动规则执行', side=unit['side'], visibility=unit['side'])
            if member_count > 1:
                event['title'] = '集群移动指令已执行'
                event['detail'] = str(member_count) + ' 个同兵营单位执行 ' + payload['mode'] + '；各自记录位置与测试能源'
            world_revision += 1
        elif kind == "advance_deployment_movement":
            require(state['lifecycle']['phase']=='preparing', 'PHASE_CONFLICT', '试走推进仅允许在部署阶段使用')
            if payload.get('formation'):
                unit, member_count = movement.advance_formation(state, actor, payload['unit_id'])
                event.update(title='集群部署试走中',detail=str(member_count)+' 个同兵营单位按纵队推进',
                             side=unit['side'],visibility=unit['side'])
            else:
                unit = next((value for value in state['units'] if value['unit_id']==payload['unit_id'] and (actor=='host' or value['side']==actor)),None)
                require(unit is not None, 'UNIT_NOT_FOUND', '单位不存在或无权操作',404)
                active = next((route for route in state['routes'] if route['unit_id']==unit['unit_id'] and route.get('deployment_test')),None)
                require(active is not None and active['status'] in ('queued','moving','waiting'), 'TEST_ROUTE_REQUIRED', '没有待推进的部署试走路线')
                require(unit['durability']>0, 'UNIT_UNAVAILABLE', '单位已经失效')
                movement.advance_unit(state,unit,active,deployment=True)
                event.update(title=unit['status'],detail='试走步 '+str(unit.get('movement_test_steps',0))+'；对局时间与正式能源不变',
                             side=unit['side'],visibility=unit['side'])
            world_revision += 1
        elif kind == "delete_unit":
            state, event = delete_unit(state, actor, payload["unit_id"])
            world_revision += 1
        elif kind == "set_preparation":
            require("spatial_profile" in state["manifest"], "SPATIAL_TEST_REQUIRED", "请新建显式启用空间测试规则的对局")
            require(bool(payload.get("preview_token")), "PREVIEW_REQUIRED", "请先预览测试兵营")
            preview = self.settings.verify(payload["preview_token"], "preparation-preview")
            require(preview["context_token"] == self._context(game, principal)["context_token"]
                and preview["member_id"] == str(principal["member_id"])
                and preview["input_sha256"] == sha256({key: value for key, value in payload.items() if key != "preview_token"}),
                "PREVIEW_STALE", "准备预览的输入、席位或状态已变化")
            state, event = spatial.set_preparation(state, actor, payload)
            world_revision += 1
        elif kind == "create_save":
            host_only(actor)
            save_id = self._manual_save(connection, game, principal, command_id, payload.get("display_name"))
            return dict(branch_id=str(branch_id), snapshot_id=str(game["head_snapshot_id"]), simulation_step=old["lifecycle"]["simulation_step"], save_id=str(save_id))
        elif kind == "restore_save":
            host_only(actor)
            require(old["lifecycle"]["phase"] != "finished", "GAME_FINISHED", "已结束对局只能只读复盘")
            saved = connection.execute("""SELECT saves.*,snapshots.state_payload,snapshots.payload_sha256,snapshots.schema_version
                FROM game_saves saves JOIN game_snapshots snapshots ON saves.snapshot_id=snapshots.id
                WHERE saves.id=%s AND saves.game_id=%s""", (payload["save_id"], game["id"])).fetchone()
            require(saved is not None, "SAVE_NOT_FOUND", "存档不存在或不属于本局", 404)
            require(saved["schema_version"] == "unity-demo-world-v1" and saved["payload_sha256"] == snapshot_hash(saved["state_payload"]),
                    "SAVE_INTEGRITY_FAILED", "存档格式或内容校验失败")
            require(saved["state_payload"]["lifecycle"]["phase"] != "finished", "SAVE_READ_ONLY", "结束快照不能继续游戏")
            state = deepcopy(saved["state_payload"])
            state["lifecycle"].update(phase="paused", controller_session_id=None)
            branch_id, epoch, revision = uuid4(), uuid4(), 0
            world_revision = 0
            insert(connection, "game_branches", dict(id=branch_id, game_id=game["id"], parent_branch_id=saved["branch_id"],
                   fork_snapshot_id=saved["snapshot_id"], created_by_command_id=command_id))
            for side, plan in state["decisions"]["plans"].items():
                if plan["status"] not in ("adopted", "rejected"):
                    original_id = plan["proposal_id"]
                    state["decisions"]["plans"][side] = dict(plan, proposal_id=str(uuid4()), version=plan["version"] + 1, source_proposal_id=original_id)
            event.update(title="演示存档已恢复", detail="建立新分支，双方恢复至同一步并保持暂停")
        elif kind == "review_proposal":
            require(state["lifecycle"]["phase"] != "finished", "GAME_FINISHED", "结束后不能再处理建议")
            require(actor in ("red", "blue"), "ROLE_FORBIDDEN", "主持人不能代替双方处理建议", 403)
            plan = state["decisions"]["plans"][actor]
            require(plan["proposal_id"] == payload["proposal_id"] and plan["status"] == "pending_review", "PROPOSAL_STALE", "建议已变化或已最终处理")
            proposal = connection.execute("SELECT * FROM proposals WHERE game_id=%s AND branch_id=%s AND side=%s AND id=%s FOR UPDATE",
                                         (game["id"], branch_id, actor, payload["proposal_id"])).fetchone()
            require(proposal is not None and proposal["execution_epoch"] == epoch
                    and proposal["input_view_sha256"] == sha256(input_view(state, actor)), "PROPOSAL_STALE", "建议的依据或执行资格已失效")
            changes = payload["changes"]
            require(payload["decision"] == "modify_approve" or not changes, "CHANGES_NOT_ALLOWED", "只有修改后批准允许提交修改", 422)
            plan.update(changes)
            plan["status"] = "rejected" if payload["decision"] == "reject" else "adopted"
            connection.execute("UPDATE proposals SET status=%s,updated_at=clock_timestamp() WHERE id=%s", (plan["status"], proposal["id"]))
            event.update(title="本方演示建议已处理", detail="处理结果已持久化；没有执行正式仿真动作", side=actor, visibility=actor, private_plan=True)
            if state["lifecycle"]["phase"] == "awaiting_confirmation" and not waiting_sides(state):
                state["lifecycle"]["phase"] = "paused"
        elif kind == "trigger_condition":
            host_only(actor)
            require(state["lifecycle"]["phase"] != "finished", "GAME_FINISHED", "结束后不能登记事件")
            preview = self.settings.verify(payload["preview_token"], "condition-preview")
            require(preview["game_id"] == str(game["id"]) and preview["session_id"] == str(principal["session_id"])
                    and preview["context_token"] == self._context(game, principal)["context_token"]
                    and preview["input_sha256"] == sha256(payload["condition"]), "PREVIEW_STALE", "预览输入、会话或状态已变化")
            condition = payload["condition"]
            target = next((unit for unit in state["units"] if unit["unit_id"] == condition["target_unit_id"]), None)
            require(target is not None, "TARGET_INVALID", "事件目标已失效", 422)
            state["conditions"]["instances"].append(dict(condition_id=str(uuid4()), **condition,
                registered_step=state["lifecycle"]["simulation_step"], status="record_only", effect_applied=False))
            event.update(title=CONDITION_NAMES[condition["condition_type"]] + "已登记", detail="仅登记演示事件，未改变能力、进度或资源",
                         side=target["side"], visibility="public" if condition["condition_type"] == "weather_change" else target["side"])
        elif kind == "complete_exit":
            host_only(actor)
            require(state["lifecycle"]["phase"] == "paused", "PAUSE_REQUIRED", "退出前请先提交 begin_exit 暂停")
            if payload["choice"] == "save_and_exit":
                save_id = self._manual_save(connection, game, principal, command_id, payload.get("display_name"))
            state["lifecycle"]["controller_session_id"] = None
            event.update(title="演示退出已确认", detail="对局保持暂停，已有成功存档保留")
        else:
            if kind in ("advance_step", "continue"): battle_rules.before_advance(state)
            if "spatial_profile" in state["manifest"]:
                if kind in ("configure_army", "deploy_army"):
                    raise ServiceError(409, "PREPARATION_REQUIRED", "测试规则仅允许在准备阶段整体提交兵营和编制")
                if kind == "set_ready" and payload["ready"]:
                    require(not any(route.get('deployment_test') and route['status'] in ('queued','moving','waiting')
                            and any(unit['unit_id']==route['unit_id'] and unit['side']==actor for unit in state['units'])
                            for route in state['routes']), 'TEST_MOVEMENT_ACTIVE', '请先停止或完成本方部署试走，再准备完成')
                    camps = {camp["camp_id"] for camp in state["camps"] if camp["side"] == actor and camp["deployment_validated"]}
                    require(camps and all(unit.get("camp_id") in camps for unit in state["units"] if unit["side"] == actor),
                            "CAMP_REQUIRED", "请先为本方各兵种配置有效兵营")
                if kind == "move_unit":
                    require(not unit_interactions.enabled(state), 'MOVEMENT_ORDER_REQUIRED', '单位交互对局请使用完整路径移动指令')
                    require(state["lifecycle"]["phase"] in ("paused", "running"), "PHASE_CONFLICT", "开始并采用建议后才能安排路线")
                    unit = next((item for item in state["units"] if item["unit_id"] == payload["unit_id"] and (actor == "host" or item["side"] == actor)), None)
                    require(unit is not None, "UNIT_NOT_FOUND", "单位不存在或无权操作", 404)
                    require(unit["durability"] > 0 and unit["energy"] > 0, "UNIT_UNAVAILABLE", "单位失效或能源耗尽")
                    active = spatial.route(state, unit, payload["position"])
                    state["routes"] = [item for item in state["routes"] if item["unit_id"] != unit["unit_id"]] + [active]
                    unit["status"] = "测试路线已排队"
                    event.update(title="测试路线已保存", detail="按仿真步推进，测试能源随距离扣减", side=unit["side"], visibility=unit["side"])
                else:
                    # 复用生命周期校验；替换旧演示预置位移，避免两种移动叠加。
                    agent_actions = coordination.prepare_step(state) if kind == 'advance_step' else {}
                    before_units = deepcopy(state["units"])
                    state, event = apply_interaction(state, actor, kind, payload)
                    if kind == "advance_step":
                        state["units"] = before_units
                        if unit_interactions.enabled(state):
                            unit_interactions.advance(state)
                        else:
                            spatial.advance(state)
                            coordination.finish_step(state, agent_actions)
                        event.update(title="测试空间步已推进", detail="路线进度和能源已保存；任务与战斗未裁决")
                        if unit_interactions.enabled(state):
                            event.update(title='单位交互步已推进', detail='12次五秒移动、观察、同步伤害和补充结算已入库')
                        if 'coordination' in state:
                            event.update(title='协同测试步已推进', detail='移动、观察、保障、占点和胜负由后端统一结算')
            else:
                state, event = apply_interaction(state, actor, kind, payload)
            if kind == "advance_step":
                battle_rules.after_advance(state)
                if qinglan_rules.enabled(state) and state["lifecycle"]["simulation_step"] in (20,40,60):
                    stage = state["victory"]["stages"][state["lifecycle"]["simulation_step"]//20-1]
                    event.update(title=f"阶段 {stage['stage']} 驻守结算", detail=stage['evidence'], visibility="public")
            if kind == "continue":
                state["lifecycle"]["controller_session_id"] = str(principal["session_id"])
            if kind in ("configure_army", "deploy_army", "move_unit", "set_tasks", "advance_step"):
                world_revision += 1
        self._snapshot(connection, game["id"], branch_id, snapshot_id, state, revision, world_revision, command_id,
                       "fork" if kind == "restore_save" else "final" if state["lifecycle"]["phase"] == "finished" else "command")
        if round_record is not None:
            from backend.battle_replay_store import append_round
            append_round(connection, game_id=game['id'], branch_id=branch_id, snapshot_id=snapshot_id,
                         snapshot_revision=revision, command_id=command_id, record=round_record)
        self._new_proposals(connection, game["id"], branch_id, snapshot_id, epoch, state,
                            previous=None if kind == "restore_save" else old, world_revision=world_revision, restored=kind == "restore_save")
        if kind == "review_proposal":
            plan = state["decisions"]["plans"][actor]
            insert(connection, "proposal_reviews", dict(id=uuid4(), game_id=game["id"], branch_id=branch_id,
                proposal_id=payload["proposal_id"], reviewer_member_id=principal["member_id"], decision=payload["decision"],
                submitted_changes=payload["changes"], adopted_plan=plan if plan["status"] == "adopted" else None,
                command_id=command_id, result_snapshot_id=snapshot_id))
        if agent_review:
            insert(connection, 'proposal_reviews', dict(id=uuid4(), game_id=game['id'], branch_id=branch_id,
                proposal_id=agent_review['proposal_id'], reviewer_member_id=principal['member_id'], decision=payload['decision'],
                submitted_changes={}, adopted_plan=deepcopy(state['coordination']['plans'].get(actor)) if agent_review['adopted'] else None,
                command_id=command_id, result_snapshot_id=snapshot_id))
        connection.execute("""UPDATE games SET active_branch_id=%s,head_snapshot_id=%s,control_revision=control_revision+1,
            execution_epoch=%s,updated_at=clock_timestamp() WHERE id=%s""", (branch_id, snapshot_id, epoch, game["id"]))
        connection.execute("""INSERT INTO game_saves(id,game_id,branch_id,snapshot_id,save_kind,source_command_id)
            VALUES (%s,%s,%s,%s,'auto',%s) ON CONFLICT (game_id,branch_id) WHERE save_kind='auto'
            DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id,source_command_id=EXCLUDED.source_command_id,updated_at=clock_timestamp()""",
            (uuid4(), game["id"], branch_id, snapshot_id, command_id))
        insert(connection, "game_events", dict(id=uuid4(), game_id=game["id"], branch_id=branch_id, snapshot_id=snapshot_id,
            snapshot_revision=revision, command_id=command_id, event_index=0, fact_key=str(command_id), event_type=kind,
            simulation_step=state["lifecycle"]["simulation_step"], visibility=event["visibility"], payload=event))
        if state["lifecycle"]["phase"] == "finished":
            insert(connection, "game_saves", dict(id=uuid4(), game_id=game["id"], branch_id=branch_id, snapshot_id=snapshot_id,
                   save_kind="final", source_command_id=command_id))
            final_game = self._game(connection, game["id"])
            for member in connection.execute("SELECT id AS member_id,role FROM game_members WHERE game_id=%s", (game["id"],)).fetchall():
                self._archive(connection, final_game, member, None, True)
        return dict(branch_id=str(branch_id), snapshot_id=str(snapshot_id), simulation_step=state["lifecycle"]["simulation_step"],
                    save_id=str(save_id) if save_id else None, is_demo=True)

    def _manual_save(self, connection, game: dict, principal: dict, command_id, name: str | None):
        """不复制完整载荷，新增独立入口引用已提交头。"""
        save_id = uuid4()
        insert(connection, "game_saves", dict(id=save_id, game_id=game["id"], branch_id=game["active_branch_id"],
               snapshot_id=game["head_snapshot_id"], save_kind="manual", display_name=name,
               created_by_member_id=principal["member_id"], source_command_id=command_id))
        return save_id

    def receipt(self, game_id: str, bearer: str, *, command_id: UUID | None = None,
                origin_branch_id: UUID | None = None, request_key: UUID | None = None) -> dict:
        """仅原提交席位可核对命令，包括原分支已不再有效的成功读档命令。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            if command_id:
                row = connection.execute("SELECT * FROM game_commands WHERE game_id=%s AND actor_member_id=%s AND id=%s",
                    (game_id, principal["member_id"], command_id)).fetchone()
            else:
                row = connection.execute("SELECT * FROM game_commands WHERE game_id=%s AND actor_member_id=%s AND origin_branch_id=%s AND request_key=%s",
                    (game_id, principal["member_id"], origin_branch_id, request_key)).fetchone()
            require(row is not None, "COMMAND_NOT_FOUND", "尚未查询到有权核对的已提交命令", 404)
            return self._receipt(row, True)

    def saves(self, game_id: str, bearer: str, offset: int = 0, limit: int = 50) -> dict:
        """主持人读取整局存档摘要，不下载全量快照。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            host_only(principal["role"])
            game = self._game(connection, game_id)
            rows = connection.execute("""SELECT saves.id AS save_id,saves.save_kind,saves.display_name,saves.branch_id,saves.snapshot_id,
                saves.created_at AS saved_at,snapshots.simulation_step,snapshots.phase FROM game_saves saves
                JOIN game_snapshots snapshots ON saves.snapshot_id=snapshots.id WHERE saves.game_id=%s
                ORDER BY saves.created_at DESC,saves.id DESC LIMIT %s OFFSET %s""", (game_id, limit, offset)).fetchall()
            for row in rows:
                row.update(is_demo=game["is_demo"], can_restore=row["phase"] != "finished" and game["state_payload"]["lifecycle"]["phase"] != "finished")
            return dict(items=rows, offset=offset, limit=limit)

    def history(self, *, bearer: str | None = None, offset: int = 0, limit: int = 50) -> dict:
        """引导目录只返回摘要；普通席位只能读取自身绑定的对局。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer) if bearer else None
            query = """SELECT games.id AS game_id,games.game_no,games.title,games.is_demo,games.created_at,
                snapshots.phase,snapshots.simulation_step FROM games JOIN game_snapshots snapshots ON games.head_snapshot_id=snapshots.id"""
            parameters = []
            if principal:
                query += " WHERE games.id=%s"
                parameters.append(principal["game_id"])
            rows = connection.execute(query + " ORDER BY games.created_at DESC,games.id DESC LIMIT %s OFFSET %s", (*parameters, limit, offset)).fetchall()
            return dict(items=rows, offset=offset, limit=limit)

    def events(self, game_id: str, bearer: str, limit: int = 50, cursor: str | None = None) -> dict:
        """多级分支按分叉前缀继承，游标冻结头并先做权限过滤再分页。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            game = self._game(connection, game_id)
            offset = 0
            anchor = game["head_snapshot_id"]
            if cursor:
                decoded = self.settings.verify(cursor, "events-cursor")
                require(decoded["game_id"] == game_id and decoded["member_id"] == str(principal["member_id"])
                        and decoded["branch_id"] == str(game["active_branch_id"]), "CURSOR_INVALID", "时间线游标已失效")
                anchor, offset = decoded["snapshot_id"], decoded["offset"]
            selected = connection.execute("SELECT revision FROM game_snapshots WHERE id=%s AND game_id=%s AND branch_id=%s",
                (anchor, game_id, game["active_branch_id"])).fetchone()
            require(selected is not None, "CURSOR_INVALID", "时间线依据快照不存在")
            visible = self._visible_events(connection, game_id, game["active_branch_id"], selected["revision"], principal["role"])
            items = visible[offset:offset + limit]
            next_cursor = None
            if offset + limit < len(visible):
                next_cursor = self.settings.sign(dict(kind="events-cursor", game_id=game_id, member_id=str(principal["member_id"]),
                    branch_id=str(game["active_branch_id"]), snapshot_id=str(anchor), offset=offset + limit,
                    expires_at=int((utcnow() + timedelta(minutes=30)).timestamp())))
            return dict(items=items, next_cursor=next_cursor)

    def replay(self, game_id: str, bearer: str, *, snapshot_id: UUID | None = None, simulation_step: int | None = None,
               branch_id: UUID | None = None) -> dict:
        """冻结快照只读投影，进行中只允许主持人审阅本机演示恢复帧。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            game = self._game(connection, game_id)
            require(principal["role"] == "host" or game["state_payload"]["lifecycle"]["phase"] == "finished",
                    "REPLAY_FORBIDDEN", "进行中复盘仅允许主持人审阅演示帧", 403)
            selected_branch = branch_id or game["active_branch_id"]
            if snapshot_id:
                row = connection.execute("SELECT * FROM game_snapshots WHERE game_id=%s AND branch_id=%s AND id=%s",
                    (game_id, selected_branch, snapshot_id)).fetchone()
            else:
                row = connection.execute("""SELECT * FROM game_snapshots WHERE game_id=%s AND branch_id=%s AND simulation_step<=%s
                    ORDER BY simulation_step DESC,revision DESC LIMIT 1""", (game_id, selected_branch, simulation_step if simulation_step is not None else 60)).fetchone()
            require(row is not None, "FRAME_NOT_FOUND", "复盘帧不存在", 404)
            require(row["payload_sha256"] == snapshot_hash(row["state_payload"]), "SNAPSHOT_INTEGRITY_FAILED", "复盘内容校验失败", 500)
            frame = self._project(game, principal, row["state_payload"])
            frame.update(read_only=True, frame_snapshot_id=str(row["id"]), frame_branch_id=str(row["branch_id"]))
            frame["context"] = None
            frame["capabilities"] = dict(read_only=True, can_control_time=False, can_review_proposal=False, can_save=False, can_manage_units=False)
            frame["save_status"] = dict(status="historical", snapshot_id=str(row["id"]))
            return frame

    def result(self, game_id: str, bearer: str) -> dict:
        """读取同一提交头的证据、算式、关键节点；旧演示仍不编造结果。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            game = self._game(connection, game_id)
            state = game["state_payload"]
            if manual_battle.enabled(state):
                manual_battle.validate(state)
                battle_kernel.validate(state)
                finished = state['manual_battle']['stage'] == 'finished'
                return dict(game_id=game_id, branch_id=str(game['active_branch_id']), snapshot_id=str(game['head_snapshot_id']),
                    is_demo=game['is_demo'], mode='manual', result_status='settled' if finished else 'in_progress',
                    simulation_step=state['lifecycle']['simulation_step'], elapsed_seconds=state['manual_battle']['elapsed_seconds'],
                    completed_rounds=state['manual_battle']['completed_rounds'], round_seconds=60, maximum_rounds=60,
                    **manual_scores(state, principal['role']),
                    result=deepcopy(state.get('battle_result')), read_only=True)
            value = battle_rules.evidence(state)
            if value is None:
                require(state["lifecycle"]["phase"] == "finished", "RESULT_NOT_READY", "当前演示尚未结束")
                status = 'test_adjudicated' if state.get('coordination') else 'not_adjudicated'
                return dict(game_id=game_id, is_demo=game["is_demo"], result_status=status,
                            simulation_step=state["lifecycle"]["simulation_step"], **state["scores"])
            calculated = battle_rules.evaluate(value)
            return dict(game_id=game_id, branch_id=str(game["active_branch_id"]), snapshot_id=str(game["head_snapshot_id"]),
                        is_demo=game["is_demo"], result_status="settled" if calculated["final"] else "pending_adjudication" if state["lifecycle"]["phase"] == "finished" else "in_progress",
                        simulation_step=state["lifecycle"]["simulation_step"], victory=value, result=calculated)

    def _archive(self, connection, game: dict, principal: dict, title: str | None, automatic: bool):
        """冻结当前成员可见的观察档案，排除令牌、上下文、私有计划；不是恢复存档。"""
        identity = uuid4()
        if automatic:
            existing = connection.execute("SELECT id FROM match_archives WHERE game_id=%s AND branch_id=%s AND member_id=%s AND automatic",
                (game["id"], game["active_branch_id"], principal["member_id"])).fetchone()
            if existing: return existing["id"]
        state, role = game["state_payload"], principal["role"]
        own = lambda key: deepcopy([x for x in state[key] if role == "host" or x["side"] == role])
        clock = {k: v for k, v in state["lifecycle"].items() if k != "controller_session_id"}
        value = battle_rules.evidence(state)
        projection = dict(role=role, clock=clock, units=own("units"), camps=own("camps"), tasks=own("tasks"),
            timeline=self._visible_events(connection, game["id"], game["active_branch_id"], game["snapshot_revision"], role, exclude_private_plans=True),
            victory=value, battle_result=battle_rules.evaluate(value), read_only=True)
        if manual_battle.enabled(state):
            # 复用席位只读投影的资源转换/任务脱敏；不保存context、令牌、双方草稿或执行账本。
            published = self._project(game, principal, state)
            projection.update({key: deepcopy(published[key]) for key in
                ('units', 'camps', 'tasks', 'contacts', 'victory', 'battle_result')})
            projection['battle_capture_points'] = deepcopy(published.get('battle_capture_points', []))
            summary = published['manual_battle']
            projection['manual_battle'] = {key: deepcopy(summary[key]) for key in
                ('schema_version', 'mode', 'stage', 'round_number', 'completed_rounds', 'elapsed_seconds',
                 'round_seconds', 'maximum_rounds', 'blue_control_score', 'red_control_score', 'blue_score', 'red_score', 'blue_breakdown',
                 'red_breakdown', 'winner', 'finish_reason', 'release_scope', 'competitive_ready', 'limitations') if key in summary}
        insert(connection, "match_archives", dict(id=identity, game_id=game["id"], member_id=principal["member_id"],
            branch_id=game["active_branch_id"], snapshot_id=game["head_snapshot_id"], title=title or (("对局结束 / " if automatic else "对局记录 / ") + game["title"])[:80],
            automatic=automatic, role=role, phase=clock["phase"], simulation_step=clock["simulation_step"], projection=projection))
        return identity

    def archives(self, game_id: str, bearer: str, *, archive_id=None, offset=0, limit=20, search="", phase="all") -> dict:
        """成员绑定始终由会话取得，不接受客户端 role/member_id。"""
        with self.settings.connect() as connection:
            principal = self._principal(connection, bearer, game_id)
            if archive_id:
                row = connection.execute("SELECT * FROM match_archives WHERE game_id=%s AND member_id=%s AND id=%s",
                    (game_id, principal["member_id"], archive_id)).fetchone()
                require(row is not None, "ARCHIVE_NOT_FOUND", "档案不存在或无权读取", 404)
                row.pop("member_id")
                return dict(item=row)
            where = "game_id=%s AND member_id=%s AND strpos(lower(title),lower(%s))>0 AND (%s='all' OR (%s='finished' AND phase='finished') OR (%s='active' AND phase<>'finished'))"
            args = (game_id, principal["member_id"], search, phase, phase, phase)
            total = connection.execute("SELECT count(*) AS n FROM match_archives WHERE " + where, args).fetchone()["n"]
            rows = connection.execute("SELECT id,title,branch_id,snapshot_id,role,phase,simulation_step,automatic,created_at FROM match_archives WHERE " + where + " ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s", (*args, limit, offset)).fetchall()
            return dict(items=rows, total=total, offset=offset, limit=limit, read_only=True)

    def tick(self) -> int:
        """由本机单进程节奏驱动持久化单步，相同依据通过请求键防重。"""
        with self.settings.connect() as connection:
            running = connection.execute("""SELECT games.id,snapshots.state_payload->'lifecycle'->>'controller_session_id' AS controller
                FROM games JOIN game_snapshots snapshots ON games.head_snapshot_id=snapshots.id WHERE snapshots.phase='running' AND NOT (snapshots.state_payload ? 'manual_battle')""").fetchall()
        advanced = 0
        for row in running:
            if not row["controller"]:
                continue
            try:
                with self.settings.connect() as connection:
                    game = self._game(connection, row["id"])
                    principal = self._principal(connection, row["controller"], str(row["id"]), scheduler=True, pausing=True)
                    active = connection.execute("SELECT revoked_at IS NULL AND expires_at>clock_timestamp() AS active FROM access_sessions WHERE id=%s", (principal["session_id"],)).fetchone()["active"]
                    kind = "advance_step" if active else "pause"
                    request = CommandRequest(context=self._context(game, principal), command_type=kind, payload={})
                    request_key = uuid5(NAMESPACE_URL, f"tick/{game['id']}/{game['execution_epoch']}/{game['head_snapshot_id']}/{kind}")
                receipt, status = self.command(str(row["id"]), row["controller"], request_key, request, scheduler=True)
                advanced += int(status == 200 and not receipt["replayed"] and kind == "advance_step")
            except ServiceError:
                # 控制切换或版本竞争不重试旧输入，下一个节奏重新读取当前依据。
                continue
        return advanced
