"""只读核对本机多智能体交付环境，不安装依赖、创建对局或调用付费模型。"""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
from urllib.request import ProxyHandler, build_opener


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def dependency_versions():
    """核对两份明确的交付清单，返回已安装版本，不改变 Python 环境。"""
    installed = {}
    for filename in ('requirements-agents.txt', 'requirements-delivery.txt'):
        for line in (PROJECT_ROOT / 'backend' / filename).read_text(encoding='utf-8').splitlines():
            requirement = line.strip()
            if not requirement or requirement.startswith(('#', '-r ')):
                continue
            package, separator, expected = requirement.partition('==')
            if not separator:
                raise ValueError('交付依赖必须指定版本')
            actual = version(package)
            if actual != expected:
                raise ValueError('已安装组件与交付清单不一致')
            installed[package] = actual
    return installed


def required_files():
    """确认本机入口、网页构建、地图与私有配置存在，不读取或输出凭据。"""
    paths = (
        'start-agents.ps1', 'backend/agent-workbench.html', 'backend/migrations/009_agent_coordination.sql',
        'unity-client/start-web.ps1', 'unity-client/Tools/serve_web.py',
        'unity-client/Builds/Web/index.html',
        'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley/map-definition.json',
        'backend/.local/application-database-v1.json', 'backend/.local/backend-runtime-v1.json',
        'backend/.local/bootstrap-key', 'backend/.local/admin-password',
    )
    missing = [relative for relative in paths if not (PROJECT_ROOT / relative).is_file()]
    if missing:
        raise FileNotFoundError('本机交付文件不完整')
    build_files = list((PROJECT_ROOT / 'unity-client/Builds/Web/Build').glob('*'))
    if not any(path.name.endswith(('.wasm', '.wasm.unityweb', '.wasm.br', '.wasm.gz')) for path in build_files):
        raise FileNotFoundError('缺少已构建的网页运行文件')
    return dict(required_file_count=len(paths))


def database_readiness():
    """读取实际数据库结构与迁移状态，不执行初始化、迁移或数据清理。"""
    from backend import database

    status = database.database_status()
    expected_migrations = {path.stem for path in (PROJECT_ROOT / 'backend/migrations').glob('*.sql')}
    if not status['all_business_tables_created'] or status['unvalidated_constraints']:
        raise ValueError('业务表或约束未就绪')
    if not expected_migrations.issubset(status['migrations']) or not status['pgvector_enabled']:
        raise ValueError('数据库迁移或已有扩展未就绪')
    return dict(business_table_count=status['business_table_count'], migrations=status['migrations'],
                server_version=status['server_version'], pgvector_version=status['pgvector_version'])


def model_readiness():
    """只核对红蓝配置和当前进程环境中的密钥是否就绪，不请求模型服务。"""
    from backend.agent_service import AgentCoordinator
    from backend.runtime import Settings
    from backend.service import SandboxService

    coordinator = AgentCoordinator(SandboxService(Settings.from_local()))
    result = {}
    for side in ('red', 'blue'):
        configuration = coordinator.load_configuration(side)
        result[side] = dict(configured=coordinator.model_factory.available(configuration),
                            enabled=configuration.model.enabled, model=configuration.model.model,
                            credential_ref=configuration.model.credential_ref)
    return result


def service_readiness():
    """核对固定本机端口、后端代码修订和工作台模板，不申请席位或变更对局。"""
    from backend.agent_graph import AGENT_RUNTIME_REVISION

    opener = build_opener(ProxyHandler({}))
    with opener.open('http://127.0.0.1:18080/api/v1/health', timeout=5) as response:
        health = json.load(response)
    if not health.get('database_ready') or health.get('agent_orchestration', {}).get('runtime_revision') != AGENT_RUNTIME_REVISION:
        raise ValueError('后端未就绪或仍运行旧代码')
    with opener.open('http://127.0.0.1:18766/agents', timeout=5) as response:
        page = response.read().decode('utf-8')
    if '__AGENT_API_BASE__' in page or '__AGENT_MAP_HASH__' in page or '启动四角色规划' not in page:
        raise ValueError('工作台未正确生成')
    return dict(runtime_revision=AGENT_RUNTIME_REVISION, workbench_url='http://127.0.0.1:18766/agents')


def run_check(name, action):
    """让独立检查分别报告结果，未知错误仅公开类型，不泄漏配置和异常原文。"""
    try:
        return dict(name=name, passed=True, details=action())
    except Exception as error:
        return dict(name=name, passed=False, error_type=type(error).__name__)


def collect_checks(require_model=False, services=False):
    """区分软件运行条件与真实模型条件；配置存在不等同于真实模型验收。"""
    checks = [dict(name='python', passed=(3, 11) <= sys.version_info[:2] < (3, 14),
                   details=dict(version=sys.version.split()[0]))]
    checks.append(run_check('dependencies', dependency_versions))
    checks.append(run_check('files', required_files))
    checks.append(run_check('database', database_readiness))
    if services:
        checks.append(run_check('services', service_readiness))
    models = run_check('models', model_readiness)
    models_ready = models['passed'] and all(item['configured'] for item in models['details'].values())
    software_ready = all(check['passed'] for check in checks)
    return dict(software_ready=software_ready, models_configured=models_ready,
                passed=software_ready and (models_ready or not require_model),
                real_model_verified=False, checks=checks, models=models)


def main():
    """输出脱敏交付检查结果；严格模式要求双方模型配置就绪。"""
    parser = argparse.ArgumentParser(description='穹图多智能体本机交付只读检查')
    parser.add_argument('--require-model', action='store_true', help='缺少任一方模型配置时返回失败')
    parser.add_argument('--services', action='store_true', help='额外核对正在运行的后端与工作台')
    parser.add_argument('--json', action='store_true', help='输出脱敏 JSON，供启动脚本读取')
    arguments = parser.parse_args()
    report = collect_checks(arguments.require_model, arguments.services)
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for check in report['checks']:
            print(('通过' if check['passed'] else '失败') + '：' + check['name'])
        print('红蓝模型配置：' + ('就绪，实际推理仍须联调' if report['models_configured'] else '未齐备'))
        print('本检查不会调用模型，不能替代真实推理验收。')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
