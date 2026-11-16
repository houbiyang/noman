"""只读战斗入口检查；不创建、迁移、启动或推进对局，不将连通等同规则就绪。"""
from __future__ import annotations

import asyncio
import time

from starlette.concurrency import run_in_threadpool

from backend import battle_capacity, rulebook
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
                except (ValueError, OSError, KeyError, TypeError):
                    checks.append(check(key, 'core', name, False, '本机规则来源或清单校验失败，需先审阅版本。'))
        try:
            validate_map(current_map())
            checks.append(check('map', 'core', '当前地图数据', True,
                                '地图文件结构校验通过；不等同新规则服务面与路线已验收。'))
        except (ValueError, OSError, KeyError, TypeError):
            checks.append(check('map', 'core', '当前地图数据', False, '当前地图校验失败。'))
        # 未接入的新执行器永不借用旧局的active/is_demo标记冒充可开战。
        checks.append(check('battle_engine', 'core', '总册战斗执行与播放', False,
            '60秒双提交结算、有限库存与新计分、逐轮播放尚未完整接入；当前只能检查，不能开战。'))
        checks.append(check('battle_scenario', 'core', '新规则对局绑定', False,
            '当前对局保持原版本；尚未发布总册战斗新局入口，不会自动迁移现有部署。'))
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
