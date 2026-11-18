"""只读战斗入口检查；不创建、迁移、启动或推进对局，不将连通等同规则就绪。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import time

from starlette.concurrency import run_in_threadpool

from backend import battle_capacity, battle_scenario, manual_battle, rulebook
from backend.agent_contracts import ModelConnectionTest
from backend.demo_domain import require
from backend.map_catalog import current_map, validate as validate_map
from backend.retrieval_models import endpoint_ready
from backend.runtime import ServiceError, sha256


def check(key, category, name, passed, message, *, status=None):
    return dict(key=key, category=category, name=name,
                status=status or ('passed' if passed else 'failed'), message=message)


def summarize(checks, mode):
    """手动仅可绕过AI失败，不可绕过DB、来源、场景和执行器门禁。"""
    core = [c for c in checks if c['category'] != 'ai']
    core_ok = bool(core) and all(c['status'] == 'passed' for c in core)
    ai = [c for c in checks if c['category'] == 'ai']
    ai_ok = bool(ai) and all(c['status'] in ('passed', 'not_required') for c in ai)
    ai_failed = any(c['status'] == 'failed' for c in ai)
    return dict(ready=core_ok and (mode == 'manual' or ai_ok),
                manual_fallback_available=core_ok and ai_failed,
                can_start=False,  # 检查不是开局授权；新模式写入接口尚未发布。
                read_only=True)


REQUIRED_BATTLE_COMMANDS = frozenset(('unit_order', 'battle_draft', 'battle_submit', 'battle_resolve',
    'enable_fivepoint', 'submit_fivepoint_plan', 'retry_fivepoint_planning', 'abort_fivepoint', 'fivepoint_planning'))


def command_schema_check(row):
    """只读系统目录，不向应用账号开放管理迁移表，也不试写指令。"""
    expression = row.get('expression', '') if row else ''
    # 仅接受迁移器产生的单一白名单CHECK；不能因任意表达式中出现命令名就判定兼容。
    match = re.fullmatch(r"CHECK \(\(command_type = ANY \(ARRAY\[((?:'[a-z_]+'::text)(?:, '[a-z_]+'::text)*)\]\)\)\)", expression)
    names = set(re.findall(r"'([a-z_]+)'::text", match[1])) if match else set()
    valid = bool(row and row['convalidated'] and REQUIRED_BATTLE_COMMANDS <= names)
    return check('battle_command_schema', 'core', '手动与跨分支命令兼容', valid,
        '数据库已校验的命令约束支持手动提交、结算及远端历史；未执行写入探针。' if valid else
        '命令约束缺失、未验证或未包含所需命令；需先由管理员核对012/013迁移，不会自动迁移。')


def engine_check(executor, state):
    """能力检查使用副本，不推进战局；不以有对象或旧引擎在线冒充可执行。"""
    ready = False
    message = '尚未装配已验收的总册地图执行器；不会借用旧引擎或测试动画推进战斗。'
    if executor is not None:
        try:
            manual_battle.validate(state)
            ready = executor.supports(deepcopy(state)) is True
            message = ('执行器通过本局冻结状态的只读能力检查；仍须通过场景与其余开战门禁。' if ready else
                '执行器不支持当前对局的冻结规则、地图或状态；不会回退到旧版裁决。')
        except (ServiceError, ValueError, OSError, KeyError, TypeError, AttributeError, RuntimeError):
            message = '执行器能力校验未通过；未执行战斗、改写状态或返回内部错误。'
    return check('battle_engine', 'core', '总册战斗执行与播放', ready, message)


def scenario_check(game, row, *, scenario=None):
    """核对独立新局绑定及来源；草案或旧局即使位于同一地图也不能开战。"""
    state = game['state_payload']
    message = '当前是旧版对局；需要独立总册新局，不会自动迁移现有部署。'
    ready = False
    if manual_battle.enabled(state):
        try:
            manual_battle.validate(state)
            value, rules = (battle_scenario.definition() if scenario is None else deepcopy(scenario)), rulebook.definition()
            scenario_id, rule_id = battle_scenario.identity(value), rulebook.identity(rules)
            manifest = state['manifest']
            matches = bool(row and str(game['scenario_version_id']) == scenario_id
                and str(game['rule_version_id']) == rule_id
                and str(manifest.get('scenario_version_id')) == scenario_id
                and str(manifest.get('rule_version_id')) == rule_id
                and battle_scenario.service_policy_matches(manifest, value)
                and battle_scenario.release_scope_matches(manifest, value)
                and manifest.get('battle_scenario_sha256') == sha256(value)
                and manifest.get('battle_rule_sha256') == sha256(rules)
                and row['definition'] == value and row['payload_sha256'] == sha256(value)
                and row['rule_definition'] == rules and row['rule_payload_sha256'] == sha256(rules))
            released = bool(matches and row['availability'] == 'active' and row['rule_availability'] == 'active'
                and battle_scenario.manual_accepted(value))
            ready = released
            message = ('本局绑定已发布的独立总册规则与场景，来源和摘要一致。' if released else
                '总册场景仍为草案或未完成地图/服务面验收；登记存在不代表已发布。' if matches else
                '本局规则、场景绑定或摘要与登记来源不一致；拒绝自动覆盖或升级。')
        except (ServiceError, ValueError, OSError, KeyError, TypeError):
            message = '总册新局绑定或来源校验失败；未改写规则、场景或旧对局。'
    return check('battle_scenario', 'core', '新规则对局绑定与发布', ready, message)


def setup_acceptance_checks(scenario, executor):
    """配置页的只读验收清单；来源声明不代替具体部署或数据库发布检查。"""
    acceptance = scenario.get('acceptance', {})
    fields = (
        ('terrain_masks_validated', '地表与遮挡层',
         '核验真实支撑层、森林与水域覆盖、建筑及桥体遮挡；局部网格测试不能代替完整场景。'),
        ('ground_and_support_routes_validated', '移动与部署',
         '核验批准道路、完整占地、坡度、敌方扫掠碰撞、停止面和双方部署区。'),
        ('bridge_actual_grade_validated', '桥面与连接',
         '按现地图真实高度核验桥面及连接；不自动修桥，不把不连续桥头当作合法通路。'),
        ('service_surface_isolation_validated', '兵营、补给与据点',
         '核验六座兵营完整占地、服务锚点、合法接触及五据点有效地表。'),
    )
    checks = [check(key, 'core', name, acceptance.get(key) is True,
                    ('场景来源已声明此项验收；仍需检查本次部署。' if acceptance.get(key) is True else message))
              for key, name, message in fields]
    # 没有新局状态时不得调用supports或伪造通过；装配对象不代表完成部署验收。
    checks.append(check('battle_engine', 'core', '总册执行器接入', False,
        '执行器已装配，需在新局配置提交时核验完整状态；尚未执行本次部署验收。' if executor is not None else
        '尚未装配真实地图执行器；不能在现地图结算移动、攻击与占点。',
        status='not_tested' if executor is not None else 'failed'))
    checks.append(check('creation_validation', 'core', '新局最终验收', False,
        '提交时还须核对数据库版本发布、命令兼容及逐单位部署；本清单不启动对局。', status='not_tested'))
    return checks


class BattlePreflight:
    def __init__(self, service, agents, models):
        self.service, self.agents, self.models = service, agents, models

    def _local(self, game_id, bearer):
        """先授权，短事务核对真实登记与头；网络前释放连接，无锁无写入。"""
        checks = []
        with self.service.settings.connect() as connection:
            principal = self.service._principal(connection, bearer, game_id)
            require(principal['role'] == 'host', 'BATTLE_HOST_REQUIRED', '请切换主持人视角执行双方开战检查', 403)
            game = self.service._game(connection, game_id)
            phase = game['state_payload']['lifecycle']['phase']
            constraint = connection.execute("""SELECT c.convalidated,pg_get_constraintdef(c.oid) AS expression
                FROM pg_constraint c WHERE c.conrelid='sandbox.game_commands'::regclass
                AND c.conname='game_commands_command_type_check' AND c.contype='c'""").fetchone()
            checks.append(command_schema_check(constraint))
            binding = connection.execute("""SELECT s.definition,s.payload_sha256,s.availability,
                r.definition AS rule_definition,r.payload_sha256 AS rule_payload_sha256,
                r.availability AS rule_availability FROM scenario_versions s JOIN rule_versions r
                ON s.rule_version_id=r.id WHERE s.id=%s AND r.id=%s""",
                (game['scenario_version_id'], game['rule_version_id'])).fetchone()
            checks.append(scenario_check(game, binding, scenario=self.service._manual_sources()[1]))
            if game['state_payload'].get('manifest', {}).get('release_scope') == battle_scenario.LOCAL_MANUAL_SCOPE:
                checks.append(check('local_manual_scope', 'ai', '本地手动测试范围', False,
                    '此版本仅本地手动测试，不具备正式竞技或Agent模式资格。'))
            checks.append(check('backend_database', 'core', '后端与数据库读取', True,
                                '本次请求已完成席位授权及对局权威头读取；未执行写入探针。'))
            checks.append(check('deployment', 'core', '部署阶段', phase == 'preparing',
                                '部署阶段可检查新开局条件。' if phase == 'preparing' else '当前对局不在部署阶段；不会重置或改写旧局。'))
            for key, name, module in (('rulebook', '总册来源与数据库登记', rulebook),
                                      ('capacity', '软件容量声明与数据库登记', battle_capacity)):
                try:
                    value = module.definition()
                    identity = module.identity(value)
                    row = connection.execute('SELECT definition,payload_sha256 FROM rule_versions WHERE id=%s',
                                             (identity,)).fetchone()
                    valid = row is not None and row['payload_sha256'] == sha256(value) and row['definition'] == value
                    checks.append(check(key, 'core', name, valid,
                        '本机来源与已登记草案摘要一致；草案未代表执行器已激活。' if valid else '登记缺失或内容不一致；拒绝静默覆盖。'))
                except (ServiceError, ValueError, OSError, KeyError, TypeError):
                    checks.append(check(key, 'core', name, False, '本机规则来源或清单校验失败，需先审阅版本。'))
        try:
            validate_map(current_map())
            checks.append(check('map', 'core', '当前地图数据', True,
                                '地图文件结构校验通过；不等同新规则服务面与路线已验收。'))
        except (ValueError, OSError, KeyError, TypeError):
            checks.append(check('map', 'core', '当前地图数据', False, '当前地图校验失败。'))
        checks.append(engine_check(self.service.battle_executor, game['state_payload']))
        return checks

    async def _model(self, side, kind, configuration, request, budget=8):
        key, name = side + '_' + kind, ('蓝方' if side == 'blue' else '红方') + {'model': '消息模型', 'embedding': '向量模型', 'rerank': '重排模型'}[kind]
        try:
            config = getattr(configuration, kind)
            if not config.enabled:
                optional = kind != 'model'
                return check(key, 'ai', name, False,
                    '未启用，此模型不是当前配置的依赖。' if optional else '未启用消息模型，不能运行Agent对抗。',
                    status='not_required' if optional else 'failed')
            if not endpoint_ready(config):
                return check(key, 'ai', name, False, '接口、模型标识或服务端凭据未配置完整。')
            if not request.probe_ai:
                return check(key, 'ai', name, False, '已配置但未联网验证；不能视作连通成功。', status='not_tested')
            if budget <= 0:
                return check(key, 'ai', name, False, '本方检查时间预算耗尽，未发送此请求；不自动重试。')
            # 同方复用既有模型测试互斥锁，依次请求；红蓝并发，总预算每方8秒。
            async with asyncio.timeout(budget):
                result = await self.models.test(side, ModelConnectionTest(kind=kind, configuration=config,
                                               acknowledge_cost=True), scope=str(self.agents.config_root))
            if result.get('success') is not True:
                return check(key, 'ai', name, False, '模型未返回有效连通结果；不自动重试。')
            return check(key, 'ai', name, True, '本次真实接口请求成功；不代表未来规划必定成功。')
        except TimeoutError:
            return check(key, 'ai', name, False, '接口8秒内未完成；远端可能已计费，不自动重试。')
        except ServiceError:
            return check(key, 'ai', name, False, '接口请求未完成或格式不兼容；远端用量可能已产生，不自动重试。')
        except (ValueError, OSError, KeyError, TypeError):
            return check(key, 'ai', name, False, '模型配置无效；未保存或改写任何配置。')

    async def _side_models(self, side, configuration, request):
        deadline = time.monotonic() + 8
        results = []
        for kind in ('model', 'embedding', 'rerank'):
            results.append(await self._model(side, kind, configuration, request, deadline - time.monotonic()))
        return results

    async def run(self, game_id, bearer, request):
        checks = await run_in_threadpool(self._local, game_id, bearer)
        # 已明确禁用Agent的本地版本不联网探测模型，避免费用或资格误报。
        if any(c['key'] == 'local_manual_scope' for c in checks):
            return dict(mode=request.mode, checks=checks, **summarize(checks, request.mode))
        jobs = []
        for side in ('blue', 'red'):
            try:
                configuration = await run_in_threadpool(self.agents.load_configuration, side)
            except (ValueError, OSError, KeyError, TypeError):
                checks.append(check(side + '_configuration', 'ai', '蓝方模型配置' if side == 'blue' else '红方模型配置',
                                    False, '模型配置读取失败；未泄漏地址、密钥或私有建议。'))
                continue
            jobs.append(self._side_models(side, configuration, request))
        for results in await asyncio.gather(*jobs):
            checks.extend(results)
        # 网络后重新授权，过期/撤销/切换阶段的旧检查不能被当作最新状态。
        checks = await run_in_threadpool(self._local, game_id, bearer) + [c for c in checks if c['category'] == 'ai']
        return dict(mode=request.mode, checks=checks, **summarize(checks, request.mode))
