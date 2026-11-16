#!/usr/bin/env python3
"""显式冻结审核范围；只创建新目录，不覆盖已发布语料。离线且不调用模型。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]


def freeze(version):
    if not re.fullmatch(r'conquest-v1-[0-9]{8}(?:-[a-z0-9-]+)?', version):
        raise ValueError('版本名必须为 conquest-v1-YYYYMMDD[-后缀]')
    target = ROOT / 'knowledge' / version
    target.mkdir()  # 必须是新版本，不能原地改写已发布证据。
    documents = []
    sources = [('docs/02仿真规则与参数.md', [
        ('cq-'+str(i).zfill(2), '### CQ-'+str(i).zfill(2)) for i in range(1,9)]),
        ('docs/09智能体执行协议.md', list(zip(
            ['agent-'+str(i).zfill(2) for i in range(1,8)],
            ['## 占点协同协议增量','## 2. 三层职责与实际调用','## 3. 四角色协议表',
             '## 4. 通用输入包','## 5. 输出结构与边界','## 7. 模型调用与失败协议',
             '### 10.5 证据、存档与分析边界'])))]
    try:
        for source, sections in sources:
            text = (ROOT / source).read_text()
            for identity, prefix in sections:
                match = re.search(r'^'+re.escape(prefix)+r'[^\n]*\n', text, re.M)
                if not match:
                    raise ValueError('审核章节缺失，停止冻结')
                heading = match[0].rstrip()
                level = len(heading)-len(heading.lstrip('#'))
                boundary = re.search(r'^#{1,'+str(level)+r'} ', text[match.end():], re.M)
                stop = match.end()+boundary.start() if boundary else len(text)
                content = re.sub(r'\n<a id="[^"]+"></a>\s*$', '\n', text[match.start():stop]).strip()+'\n'
                if identity=='agent-01':
                    content=content.split('\n修订日期：')[0].rstrip()+'\n'
                name=identity+'.txt'
                (target/name).write_text(content)
                documents.append(dict(id=identity,file=name,file_source=version+'--'+name,
                    source_path=source,source_section=heading.lstrip('# '),source_line=text[:match.start()].count('\n')+1,
                    sha256=hashlib.sha256(content.encode()).hexdigest(),status='target_design_test_baseline'))
        manifest=dict(schema_version=1,version=version,ruleset='conquest-v1',planning_only=True,documents=documents)
        (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        shutil.copyfile(ROOT/'knowledge/conquest-v1-20261110/evaluation.json',target/'evaluation.json')
    except Exception:
        shutil.rmtree(target)
        raise
    print('已冻结15章；请人工审核与现行规则的差异及题集，再初始化新索引。')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',required=True)
    args=parser.parse_args()
    try:
        freeze(args.version)
    except (ValueError,OSError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
