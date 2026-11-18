"""真实本地手动测试候选/冻结工具：不填passed，不执行数据库发布。"""
import argparse
from copy import deepcopy
from hashlib import sha256 as byte_hash
import json
import os
from pathlib import Path

from backend import battle_capacity, battle_scenario, battle_setup, rulebook
from backend.battle_map_geometry import load_acceptance_geometry, load_local_manual_geometry
from backend.demo_domain import require
from backend.runtime import sha256


def read(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 不覆盖任何已冻结版本、旧部署或验收证据。
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def bind_setup(deployment, scenario):
    return battle_setup.CreateManualBattle.model_validate(dict(
        title='青岚谷本地手动测试', mode='manual',
        **battle_setup.versions(rulebook.definition(), scenario, battle_capacity.definition()),
        **{side: deepcopy(deployment[side]) for side in ('blue', 'red')})).model_dump(mode='json')


def candidate(surface_path, interaction_path, output, *, version, limitations, deployment_path=None):
    scenario = battle_scenario.local_manual_definition(read(surface_path), read(interaction_path),
                                                     version=version, limitations=limitations)
    output = Path(output)
    write_new(output, scenario)
    # loader执行真实来源完整校验；失败文件保留candidate标记，绝不产生已验收bundle。
    load_acceptance_geometry(surface_path, interaction_path, output)
    if deployment_path:
        write_new(output.with_name(output.stem+'-setup.json'), bind_setup(read(deployment_path), scenario))
    return scenario


def evidence_report(scenario, evidence_directory, *, source_domain_audit_sha256=None):
    cases = []
    for case_id in sorted(battle_scenario.LOCAL_MANUAL_CASES | {battle_scenario.HOVER_ENERGY_CASE}):
        path = Path(evidence_directory) / (case_id+'.json')
        require(path.is_file(), 'BATTLE_ACCEPTANCE_EVIDENCE_MISSING', f'缺少实际原始用例 {case_id}', 503)
        raw = path.read_bytes()
        record = json.loads(raw)
        require(battle_scenario.local_manual_record_matches(record, case_id,
                    geometry_binding=scenario['geometry_binding'], scenario=scenario),
                'BATTLE_ACCEPTANCE_EVIDENCE_STALE', f'原始用例不完整或来源不符: {case_id}', 503)
        if case_id == 'source_integrity' and source_domain_audit_sha256 is not None:
            require(record.get('source_domain_audit_sha256') == source_domain_audit_sha256,
                    'BATTLE_ACCEPTANCE_EVIDENCE_STALE', 'source_integrity未绑定实际活动域原始审计', 503)
        cases.append(dict(case_id=case_id, passed=True, source='real_map', evidence_sha256=byte_hash(raw).hexdigest()))
    return dict(schema_version='qinglan-local-manual-acceptance-v1', environment='isolated_real_map',
        release_scope=battle_scenario.LOCAL_MANUAL_SCOPE, competitive_ready=False, passed=True,
        geometry_binding=deepcopy(scenario['geometry_binding']),
        field_energy_service_policy=scenario['field_energy_service_policy'], cases=cases)


def freeze(surface_path, interaction_path, candidate_path, evidence_directory, output, *, deployment_path=None):
    load_acceptance_geometry(surface_path, interaction_path, candidate_path)
    scenario = read(candidate_path)
    final = battle_scenario.freeze_local_manual(scenario, evidence_report(scenario, evidence_directory,
        source_domain_audit_sha256=read(interaction_path)['source_domain_audit_sha256']))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 先完整验证临时资格，再独占写入最终文件；无效证据不留下永久accepted文件。
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(prefix='local-manual-verify-', dir=output.parent) as temporary:
        trial = Path(temporary)/'scenario.json'
        write_new(trial, final)
        load_local_manual_geometry(surface_path, interaction_path, trial, evidence_directory)
    write_new(output, final)
    relative = lambda p: os.path.relpath(Path(p).resolve(), output.parent.resolve())
    bundle = dict(schema_version='qinglan-manual-server-bundle-v1', release_scope=battle_scenario.LOCAL_MANUAL_SCOPE,
                  surface_path=relative(surface_path), interaction_path=relative(interaction_path),
                  scenario_path=output.name, evidence_directory=relative(evidence_directory))
    if deployment_path:
        setup_path = output.with_name(output.stem+'-setup.json')
        write_new(setup_path, bind_setup(read(deployment_path), final))
        bundle['default_setup_path'] = setup_path.name
    bundle_path = output.with_name(output.stem+'-bundle.json')
    write_new(bundle_path, bundle)
    from backend.manual_server import load_bundle
    load_bundle(bundle_path)
    return bundle_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('candidate', 'freeze'))
    parser.add_argument('--surface', type=Path, required=True)
    parser.add_argument('--interactions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deployment', type=Path)
    parser.add_argument('--version')
    parser.add_argument('--limitation', action='append')
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    if args.operation == 'candidate':
        candidate(args.surface, args.interactions, args.output, version=args.version,
                  limitations=args.limitation, deployment_path=args.deployment)
    else:
        require(args.candidate is not None and args.evidence is not None,
                'BATTLE_ACCEPTANCE_EVIDENCE_MISSING', 'freeze需要候选文件和实际证据目录', 503)
        print(freeze(args.surface, args.interactions, args.candidate, args.evidence, args.output,
                     deployment_path=args.deployment))


if __name__ == '__main__':
    main()
