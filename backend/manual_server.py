"""显式本机已验收bundle启动；不隐式迁移、发布候选或改写旧局。"""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from backend import battle_scenario, battle_setup
from backend.battle_execution import Executor
from backend.battle_map_geometry import load_local_manual_geometry, load_server_geometry
from backend.demo_domain import require
from backend.runtime import Settings


def load_bundle(path):
    path = Path(path).resolve()
    bundle = json.loads(path.read_bytes())
    require(bundle.get('schema_version') == 'qinglan-manual-server-bundle-v1'
            and bundle.get('release_scope') in ('formal', battle_scenario.LOCAL_MANUAL_SCOPE),
            'BATTLE_BUNDLE_INVALID', '仅接受明确已验收范围的服务端bundle，不能安装候选', 503)
    def resolve(key):
        value = bundle.get(key)
        require(isinstance(value, str) and bool(value), 'BATTLE_BUNDLE_INVALID', 'bundle缺少服务端路径', 503)
        return (path.parent / value).resolve()
    loader = load_local_manual_geometry if bundle['release_scope'] == battle_scenario.LOCAL_MANUAL_SCOPE else load_server_geometry
    geometry = loader(resolve('surface_path'), resolve('interaction_path'), resolve('scenario_path'), resolve('evidence_directory'))
    request = None
    if 'default_setup_path' in bundle:
        request = battle_setup.CreateManualBattle.model_validate_json(resolve('default_setup_path').read_bytes())
        state = battle_setup.build(request, geometry.rules, geometry.scenario, geometry.capacity, geometry)
        # 新局工厂与实际执行器同一完整占地/空域/六营校验，不根据推荐配置跳过检查。
        Executor(geometry).prepare_new_game(state)
    return geometry, request


def create_bundle_app(settings, path, *, register_local=False):
    from backend.main import create_app
    geometry, request = load_bundle(path)
    if register_local:
        with settings.connect() as connection:
            battle_scenario.register_local_manual(connection, geometry)
    app = create_app(settings)
    # 只扩展本独立服务的固定本机新版页面；旧服务CORS与指针不修改。
    from starlette.middleware.cors import CORSMiddleware
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            middleware.kwargs['allow_origins'] = list(dict.fromkeys([
                *middleware.kwargs.get('allow_origins', []),
                'http://127.0.0.1:18768', 'http://localhost:18768',
                'http://127.0.0.1:18769', 'http://localhost:18769']))
    app.state.service.install_manual_geometry(geometry)
    if request is not None:
        app.state.service.manual_recommended_setup = deepcopy(request.model_dump(mode='json'))
    return app


def local_settings(database_config=None):
    """显式选择新独立本地库；旧应用配置、凭据与数据库始终不改写。"""
    import re
    settings = replace(Settings.from_local(), scheduler_enabled=False)
    if database_config is not None:
        connection = json.loads(Path(database_config).read_bytes())
        original = settings.connection
        require(isinstance(connection, dict)
                and re.fullmatch(r'qiongtu_local_manual_[0-9a-f]{32}', str(connection.get('database', '')))
                and {k: v for k, v in connection.items() if k != 'database'}
                    == {k: v for k, v in original.items() if k != 'database'},
                'BATTLE_LOCAL_DATABASE_INVALID', '独立配置仅允许选择全新本地测试库，不改其他连接参数', 503)
        settings = replace(settings, connection=connection)
    return settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18081)
    parser.add_argument('--database-config', type=Path, help='私有独立本地测试库配置；不覆盖旧应用配置')
    parser.add_argument('--register-local', action='store_true', help='显式插入新本地测试场景版本，不修改旧对局')
    parser.add_argument('--check-only', action='store_true', help='仅验证文件和推荐部署，不读取配置或访问数据库')
    args = parser.parse_args()
    if args.check_only:
        geometry, request = load_bundle(args.bundle)
        print(json.dumps(dict(readiness=geometry.readiness(), default_setup_validated=request is not None), ensure_ascii=False))
        return
    import uvicorn
    settings = local_settings(args.database_config)
    app = create_bundle_app(settings, args.bundle, register_local=args.register_local)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
