"""按明确白名单打包本机多智能体交付物，排除数据库、凭据和开发缓存。"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def public_path(relative):
    """拒绝私有目录、路径穿越和明确凭据文件名。"""
    path = PurePosixPath(relative)
    forbidden = {'.local', '.git', '__pycache__', '.venv', 'node_modules', 'admin-password', 'bootstrap-key'}
    return (not path.is_absolute() and '..' not in path.parts and ':' not in relative and '\\' not in relative
            and not forbidden.intersection(path.parts) and not path.name.startswith('.env')
            and path.suffix.lower() not in ('.key', '.pem', '.pfx', '.p12', '.pyc'))


def delivery_files():
    """仅枚举指定源码、地图、网页构建与手册，不扫描私有运行目录。"""
    paths = set()
    for pattern in ('*.py', '*.html', '*.ps1', 'requirements*.txt'):
        paths.update((PROJECT_ROOT / 'backend').glob(pattern))
    for folder, pattern in (('backend/migrations', '*.sql'), ('backend/tests', '*.py')):
        paths.update((PROJECT_ROOT / folder).glob(pattern))
    for folder in ('unity-client/Assets/Sandbox/Resources/Maps/QinglanValley',
                   'unity-client/Assets/Sandbox/Resources/Battlefield', 'unity-client/Builds/Web'):
        paths.update(path for path in (PROJECT_ROOT / folder).rglob('*') if path.is_file())
    for relative in ('start-agents.ps1', 'set-agent-keys.ps1', 'unity-client/start-web.ps1',
                     'unity-client/Tools/serve_web.py', 'docs/14多智能体编排实现与使用.md',
                     'docs/15多智能体本机交付与验收.md', 'backend/Verification/多智能体本机交付验收_2026-11-10.md'):
        paths.add(PROJECT_ROOT / relative)
    result = {}
    for path in sorted(paths):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(PROJECT_ROOT):
            raise ValueError('交付文件不存在或超出项目边界')
        if not public_path(relative):
            raise ValueError('交付白名单包含私有路径')
        result[relative] = path
    result['README.md'] = PROJECT_ROOT / 'docs/15多智能体本机交付与验收.md'
    evidence = {
        'evidence/environment.json': 'backend/.local/agent-delivery-environment.json',
        'evidence/tests.log': 'backend/.local/agent-delivery-tests-final-20261110.log',
        'evidence/workbench.png': 'backend/.local/agent-delivery-workbench-20261110.png',
    }
    for name, relative in evidence.items():
        path = PROJECT_ROOT / relative
        if not path.is_file():
            raise FileNotFoundError('交付验收证据不完整')
        result[name] = path
    return result


def verify_archive(path):
    """逐个核对归档成员与摘要，不解压或运行归档中的代码。"""
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not all(public_path(name) for name in names):
            raise ValueError('归档存在重复或私有路径')
        manifest = json.loads(archive.read('delivery-manifest.json'))
        if set(names) != set(manifest['files']) | {'delivery-manifest.json'}:
            raise ValueError('归档文件与清单不一致')
        for name, expected in manifest['files'].items():
            with archive.open(name) as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            if digest != expected['sha256'] or archive.getinfo(name).file_size != expected['bytes']:
                raise ValueError('归档文件摘要或长度不一致')
    return dict(verified=True, files=len(manifest['files']), real_model_verified=manifest['real_model_verified'])


def build_archive():
    """生成不可覆盖的新归档，记录归档中实际字节的摘要并立即独立复核。"""
    paths = delivery_files()
    environment = json.loads(paths['evidence/environment.json'].read_text(encoding='utf-8'))
    if not environment.get('software_ready'):
        raise ValueError('软件运行检查尚未通过，未建立交付包')
    if not paths['evidence/tests.log'].read_text(encoding='utf-8-sig').rstrip().endswith('OK'):
        raise ValueError('回归测试未成功结束，未建立交付包')
    output_root = PROJECT_ROOT / '.local/deliveries'
    output_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = output_root / ('qiongtu-agents-windows-local-' + stamp + '.zip')
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(),
                    deployment_kind='existing-windows-local-installation',
                    requires_existing_private_configuration=True, real_model_verified=False, files={})
    with ZipFile(output, 'x', compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in sorted(paths.items()):
            digest, size = hashlib.sha256(), 0
            with path.open('rb') as source, archive.open(name, 'w') as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            manifest['files'][name] = dict(sha256=digest.hexdigest(), bytes=size)
        archive.writestr('delivery-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    verification = verify_archive(output)
    with output.open('rb') as source:
        archive_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    output.with_suffix('.sha256').write_text(archive_hash + '  ' + output.name + '\n', encoding='utf-8')
    return dict(path=str(output), bytes=output.stat().st_size, sha256=archive_hash, **verification)


def main():
    """建立本机交付归档或只读核对指定归档，不包含数据库恢复操作。"""
    parser = argparse.ArgumentParser(description='穹图多智能体本机交付打包与摘要验证')
    parser.add_argument('--verify', type=Path, help='只读核对已有归档，不创建交付包')
    arguments = parser.parse_args()
    result = verify_archive(arguments.verify) if arguments.verify else build_archive()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
