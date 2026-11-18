"""真实冻结地图→独立随机PG新局→HTTP双方提交→主持人结算→只读回放。

默认只读预检。--execute 才创建随机测试库，并在退出时删除该库；绝不连接用户
应用库写入、不替换本机页面、不合成几何/发布证明。local_manual_test != 正式实图验收。
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend import battle_setup, battle_replay, database
from backend.runtime import Settings, ServiceError


class VerificationError(Exception):
    pass


def need(condition, label):
    if not condition:
        raise VerificationError(label)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_geometry(args):
    # Fixed server-owned entry points, not an arbitrary module supplied by input.
    from backend import battle_map_geometry as module
    if args.acceptance_candidate:
        need(args.scope == 'local_manual_test', 'CANDIDATE_NOT_FORMAL')
        loader = getattr(module, 'load_acceptance_geometry', None)
        need(loader is not None, 'ACCEPTANCE_LOADER_NOT_READY')
        return loader(args.surfaces, args.interactions, args.scenario)
    if args.scope == 'formal':
        return module.load_server_geometry(args.surfaces, args.interactions, args.scenario, args.evidence)
    loader = getattr(module, 'load_local_manual_geometry', None)
    need(loader is not None, 'LOCAL_MANUAL_LOADER_NOT_READY')
    return loader(args.surfaces, args.interactions, args.scenario, args.evidence)


def prepare_request(path, geometry):
    request = battle_setup.CreateManualBattle.model_validate_json(path.read_bytes())
    bindings = battle_setup.versions(geometry.rules, geometry.scenario, geometry.capacity)
    need(all(str(getattr(request, k)) == v for k, v in bindings.items()), 'REQUEST_VERSION_MISMATCH')
    return request, bindings


def read_plan(path, rounds):
    plan = json.loads(path.read_bytes()) if path else {'rounds': []}
    need(isinstance(plan, dict) and set(plan) == {'rounds'}, 'PLAN_SHAPE_INVALID')
    need(isinstance(plan['rounds'], list) and len(plan['rounds']) <= rounds, 'PLAN_ROUNDS_INVALID')
    for row in plan['rounds']:
        need(isinstance(row, dict) and set(row) <= {'blue', 'red'} and row, 'PLAN_SIDE_INVALID')
        for items in row.values():
            need(isinstance(items, list) and len(items) <= 30, 'PLAN_INTENTS_INVALID')
    return plan['rounds']


def bind_intents(items, view):
    """只补入新局实际生命ID；不替换位置、目标或资源。目标由后端可见性重新核验。"""
    units = {u['unit_id']: u for u in view['units']}
    targets = {u['unit_id']: u for u in view.get('contacts', [])}
    targets.update(units)
    need(isinstance(items, list), 'PLAN_INTENTS_INVALID')
    result = deepcopy(items)
    for item in result:
        need(isinstance(item, dict) and item.get('unit_id') in units, 'PLAN_UNIT_NOT_OWNED')
        own = units[item['unit_id']]
        need(own['side'] == view['role'], 'PLAN_UNIT_NOT_OWNED')
        need('life_id' not in item, 'PLAN_MUST_NOT_FREEZE_LIFE_ID')
        item['life_id'] = own['life_id']
        task = item.get('task') or {}
        need(isinstance(task, dict), 'PLAN_TASK_INVALID')
        if task.get('target_id'):
            need('target_life_id' not in task, 'PLAN_MUST_NOT_FREEZE_TARGET_LIFE_ID')
            need(task['target_id'] in targets, 'PLAN_TARGET_NOT_VISIBLE')
            task['target_life_id'] = targets[task['target_id']]['life_id']
    return result


@contextmanager
def isolated_database():
    """Only this process's newly generated UUID database may be migrated or dropped."""
    import pgserver
    import psycopg
    name = 'qiongtu_manual_test_' + uuid4().hex
    need(re.fullmatch(r'qiongtu_manual_test_[0-9a-f]{32}', name), 'ISOLATION_NAME_INVALID')
    old_bin, old_args, old_password = database.POSTGRES_BIN, database.psql_arguments, database.admin_password
    database.POSTGRES_BIN = Path(pgserver.__file__).parent / 'pginstall/bin'
    def args(dbname, role='battle_admin'):
        value = old_args(dbname, role)
        if role == 'battle_admin':
            value[value.index('-h') + 1] = str(database.LOCAL_ROOT)
        return value
    database.psql_arguments = args
    database.admin_password = lambda: ''
    created = False
    try:
        database.execute('postgres', f'CREATE DATABASE {database.identifier(name)} TEMPLATE template0 ENCODING \'UTF8\';')
        created = True
        database.apply_migrations(name, enable_vectors=False)
        database.configure_privileges(name)
        config = json.loads(database.APPLICATION_CONFIG_PATH.read_bytes())
        config['database'] = name
        settings = Settings(config, secrets.token_hex(32), secrets.token_hex(32), False)
        def admin():
            return psycopg.connect(host=str(database.LOCAL_ROOT), port=database.POSTGRES_PORT,
                user='battle_admin', dbname=name, row_factory=psycopg.rows.dict_row,
                options='-c search_path=sandbox,pg_catalog')
        yield settings, admin
    finally:
        try:
            if created:
                database.execute('postgres', f'DROP DATABASE {database.identifier(name)};')
        finally:
            database.POSTGRES_BIN, database.psql_arguments, database.admin_password = old_bin, old_args, old_password


def register_verified_sources(settings, admin, geometry, scope, *, acceptance_candidate=False):
    """Explicit isolated publication only; all definitions remain byte/content bound."""
    from backend import rulebook, battle_capacity, battle_scenario
    from backend.service import SandboxService
    need(re.fullmatch(r'qiongtu_manual_test_[0-9a-f]{32}', settings.connection['database']) is not None, 'ISOLATION_REQUIRED')
    SandboxService(settings).seed_versions()
    with admin() as c:
        for value, identity in ((geometry.rules, rulebook.identity(geometry.rules)),
                                (geometry.capacity, battle_capacity.identity(geometry.capacity))):
            from backend.runtime import sha256
            row = c.execute('SELECT definition,payload_sha256 FROM rule_versions WHERE id=%s', (identity,)).fetchone()
            need(row and row['definition'] == value and row['payload_sha256'] == sha256(value), 'REGISTERED_SOURCE_MISMATCH')
            c.execute("UPDATE rule_versions SET availability='active' WHERE id=%s", (identity,))
        if acceptance_candidate:
            need(scope == 'local_manual_test', 'CANDIDATE_NOT_FORMAL')
            register = getattr(battle_scenario, 'register_acceptance_candidate', None)
            need(register is not None, 'ACCEPTANCE_REGISTER_NOT_READY')
            register(c, geometry)
        elif scope == 'formal':
            battle_scenario.register_accepted(c, geometry)
        else:
            register = getattr(battle_scenario, 'register_local_manual', None)
            need(register is not None, 'LOCAL_MANUAL_REGISTER_NOT_READY')
            register(c, geometry)


def db_digest(settings):
    from psycopg import sql
    with settings.connect() as c:
        c.execute('SET TRANSACTION READ ONLY')
        result = {}
        tables = c.execute("SELECT tablename FROM pg_tables WHERE schemaname='sandbox' ORDER BY tablename").fetchall()
        for row in tables:
            table = row['tablename']
            query = sql.SQL("SELECT count(*) AS n,encode(sha256(convert_to(coalesce(string_agg(payload,E'\\n' ORDER BY payload),''),'UTF8')),'hex') AS h FROM (SELECT row_to_json(t)::text AS payload FROM sandbox.{} t) x").format(sql.Identifier(table))
            result[table] = c.execute(query).fetchone()
        return result


def run_http_flow(settings, geometry, request, plan, args):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from verify_manual_replay import verify
    def new_client():
        app = create_app(settings)
        if args.acceptance_candidate:
            app.state.service.install_acceptance_geometry(geometry)
        else:
            app.state.service.install_manual_geometry(geometry)
        return TestClient(app)
    client = new_client()
    def response(method, path, status=200, **kwargs):
        r = client.request(method, '/api/v1' + path, **kwargs)
        if r.status_code != status:
            try:
                code = r.json().get('error', {}).get('code', 'HTTP_ERROR')
            except ValueError:
                code = 'HTTP_ERROR'
            raise VerificationError(f'{method} {path.rsplit("/",1)[-1]}: {r.status_code} {code}')
        return r.json()
    bootstrap = {'X-Bootstrap-Key': settings.bootstrap_key}
    try:
        catalogue = response('GET', '/battle/setup', headers=bootstrap)
        need(catalogue['round_seconds'] == 60 and catalogue['maximum_rounds'] == 60, 'ROUND_POLICY_INVALID')
        creation_key = str(uuid4())
        create_headers = {**bootstrap, 'Idempotency-Key': creation_key}
        body = request.model_dump(mode='json')
        created = response('POST', '/battle/games', 201, headers=create_headers, json=body)
        creation_digest = db_digest(settings)
        repeated = response('POST', '/battle/games', headers=create_headers, json=body)
        need(creation_digest == db_digest(settings), 'CREATION_RETRY_WROTE_STATE')
        need(created['game_id'] == repeated['game_id'], 'CREATION_NOT_IDEMPOTENT')
        base = '/games/' + created['game_id']
        seats = {role: {'Authorization': 'Bearer ' + response('POST', '/bootstrap' + base + '/sessions',
            headers=bootstrap, json={'role': role})['access_token']} for role in ('blue','red','host')}
        def view(role):
            return response('GET', base + '/view', headers=seats[role])
        def command(role, kind, payload, *, repeat=False):
            envelope = dict(context=view(role)['context'], command_type=kind, payload=payload)
            headers = {**seats[role], 'Idempotency-Key': str(uuid4())}
            result = response('POST', base+'/commands', json=envelope, headers=headers)
            if repeat:
                before = db_digest(settings)
                response('POST', base+'/commands', json=envelope, headers=headers)
                need(before == db_digest(settings), 'COMMAND_RETRY_WROTE_STATE')
            return result
        completed = 0
        for number in range(1, args.rounds + 1):
            host = view('host')['manual_battle']
            if host['stage'] == 'finished':
                break
            need(host['round_number'] == number, 'ROUND_SEQUENCE_INVALID')
            for role in ('blue','red'):
                own = view(role)
                items = plan[number-1].get(role, []) if number <= len(plan) else []
                if items:
                    command(role, 'battle_draft', dict(round_number=number, intents=bind_intents(items, own)))
                command(role, 'battle_submit', dict(round_number=number))
            command('host', 'battle_resolve', dict(round_number=number), repeat=True)
            completed = number
            if number == 1 or number % 10 == 0:
                print(json.dumps(dict(progress='resolved', round_number=number), ensure_ascii=False), flush=True)
        need(completed > 0, 'NO_COMPLETED_ROUND')
        before = db_digest(settings)
        client.close()
        client = new_client()  # Actual route closures and service are rebuilt, not app.state swap.
        index = response('GET', base+'/battle/rounds', headers=seats['host'])
        need(index['read_only'] is True and index['writes_game_state'] is False, 'INDEX_NOT_READ_ONLY')
        need([i['round_number'] for i in index['items']] == list(range(1,completed+1)), 'REPLAY_INDEX_GAP')
        records = []
        for n in range(1,completed+1):
            data = response('GET', base+f'/battle/rounds/{n}', headers=seats['host'])
            need(data['read_only'] is True and data['writes_game_state'] is False, 'ROUND_NOT_READ_ONLY')
            battle_replay.validate_record(data['record'])
            records.append(data['record'])
        for role in ('blue','red'):
            response('GET', base+'/battle/rounds', 403, headers=seats[role])
            response('GET', base+'/battle/rounds/1', 403, headers=seats[role])
        need(before == db_digest(settings), 'REPLAY_READ_WROTE_DATABASE')
        result = verify(records, require_sixty=args.require_sixty_rounds,
            required_events=args.require_events, required_resources=args.require_resources)
        verify(records[-1:], single_round=True)
        write_json(args.output/'replay-cumulative.json', dict(records=records))
        write_json(args.output/'replay-single.json', dict(records=records[-1:]))
        observation = dict(completed_rounds=completed, end_seconds=records[-1]['end_seconds'],
            finished=records[-1]['finished'], round_payload_sha256=[r['payload_sha256'] for r in records],
            replay_export_sha256=hashlib.sha256((args.output/'replay-cumulative.json').read_bytes()).hexdigest(),
            restart='new create_app and route closures', forbidden_roles=['blue', 'red'],
            forbidden_http_status=403, database_digest_before=before, database_digest_after=db_digest(settings))
        assertions = dict(real_http_creation=True, creation_retry_no_writes=True, resolve_retry_no_writes=True,
            route_restart=True, continuous_persisted_replay=True, replay_get_no_writes=True, role_isolation=True)
        case_ids = ['manual_round_persistence_and_recovery']
        if args.require_sixty_rounds:
            need(completed == 60 and records[-1]['end_seconds'] == 3600 and records[-1]['finished'], 'SIXTY_ROUNDS_NOT_COMPLETED')
            assertions['sixty_round_terminal'] = True
            case_ids.append('sixty_round_terminal_and_role_isolation')
        for case_id in case_ids:
            case = dict(case_id=case_id, passed=True, source='real_map',
                geometry_binding=deepcopy(geometry.binding), map_source_sha256=geometry.scenario['map']['source_sha256'],
                assertions=assertions, observations=observation,
                field_energy_service_policy=geometry.scenario.get('field_energy_service_policy'))
            write_json(args.output/('case-'+case_id+'.json'), case)
        return dict(http_flow_passed=True, completed_rounds=completed, end_seconds=records[-1]['end_seconds'],
            finished=records[-1]['finished'], replay=result, restart_verified=True, replay_reads_write_nothing=True,
            role_isolation_passed=True, creation_and_resolution_retry_passed=True, actual_case_ids=case_ids)
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('surfaces','interactions','scenario','evidence','request','output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--scope', choices=('formal','local_manual_test'), default='local_manual_test')
    parser.add_argument('--acceptance-candidate', action='store_true', help='仅隔离PG离线候选执行，不发布本地或正式资格')
    parser.add_argument('--execute', action='store_true', help='显式允许创建及删除随机测试库；从不迁移用户库')
    parser.add_argument('--plan', type=Path, help='{"rounds":[{"blue":[意图去掉life_id],"red":[]}]}；未提供则空任务等待')
    parser.add_argument('--rounds', type=int, default=2, choices=range(1,61))
    parser.add_argument('--require-sixty-rounds', action='store_true')
    parser.add_argument('--require-events', nargs='*', default=[])
    parser.add_argument('--require-resources', nargs='*', default=[], choices=('energy','ammo','repair'))
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)  # Preserve failures; don't overwrite prior evidence.
    report = dict(schema_version='qiongtu-real-manual-flow-v1', scope=args.scope,
        source='real_map', formal_acceptance_claimed=False, real_map_e2e_accepted=False,
        execution_requested=args.execute, acceptance_candidate=args.acceptance_candidate, http_flow_passed=False,
        boundary='仅实际执行的本地范围；不代替正式13case、未知森林或桥头连通验收',
        input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (args.surfaces,args.interactions,args.scenario,args.request,args.plan) if p is not None and p.is_file()})
    try:
        need(not args.require_sixty_rounds or args.rounds == 60, 'SIXTY_ROUNDS_REQUIRED')
        geometry = load_geometry(args)
        request, bindings = prepare_request(args.request, geometry)
        report['bindings'] = bindings
        report['geometry_binding'] = deepcopy(geometry.binding)
        plan = read_plan(args.plan, args.rounds)
        report['geometry_preflight_passed'] = True
        if args.execute:
            with isolated_database() as (settings, admin):
                register_verified_sources(settings, admin, geometry, args.scope, acceptance_candidate=args.acceptance_candidate)
                report.update(run_http_flow(settings, geometry, request, plan, args))
            report['random_database_cleaned_up'] = True
            need(all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                for p,h in report['input_sha256'].items()), 'INPUT_SOURCE_CHANGED_DURING_RUN')
        else:
            report['status'] = 'preflight_only_no_database_writes'
    except ServiceError as error:
        report['blocker'] = dict(code=error.code, status=error.status)
    except VerificationError as error:
        report['blocker'] = dict(code=str(error))
    except Exception as error:
        # Never print source inputs, tokens, SQL, connection passwords or stacktrace.
        report['blocker'] = dict(code='VERIFICATION_FAILED', exception_type=type(error).__name__)
    write_json(args.output/'report.json', report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int('blocker' in report)


if __name__ == '__main__':
    raise SystemExit(main())
