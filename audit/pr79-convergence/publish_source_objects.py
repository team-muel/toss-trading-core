"""Reproduce a locally tested source tree without executing source patches."""
from pathlib import Path
from hashlib import sha1
import json
import shutil
import subprocess
import sys

source, research, quant, transport = (Path(value).resolve() for value in sys.argv[1:])
BASE_TREE = '700fd4b35cca4375ed63f231bb9f38ad25a3775b'
TARGET_TREE = 'ffac359f613c092ff7208ad5c9b145c432986ac5'


def git(*args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


def safe_path(name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not path.parts or '.git' in path.parts:
        raise ValueError('invalid source path')
    return source/path


def blob(data):
    return sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()


for root, paths in ((research, [
    'src/alpha_management', 'tests/test_research_campaign.py', 'tests/test_research_campaign_review.py',
    'docs/ama162_review_disposition.md', 'docs/research_reconstruction.md',
]), (quant, [
    'src/alpha_management/arithmetic.py', 'src/alpha_management/dsl.py', 'src/alpha_management/quant.py',
    'tests/test_quant_factor_templates.py', 'docs/quant_factor_migration.md',
])):
    for name in paths:
        src, dst = root/name, safe_path(name)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
git('add', '-A')
assert git('write-tree') == BASE_TREE, 'source baselines differ'
entries = []
for index in range(1, 6):
    entries.extend(json.loads((transport/f'ops-{index:02}.json').read_text()))
assert len({entry['path'] for entry in entries}) == len(entries)
inputs = {}
for entry in entries:
    path = safe_path(entry.get('source_path', entry['path']))
    data = path.read_bytes() if path.exists() else b''
    if entry['before'] is None:
        assert not path.exists(), 'new path already exists'
    else:
        assert blob(data) == entry['before'], f"wrong source blob: {entry['path']}"
    inputs[entry['path']] = data
for entry in entries:
    path = safe_path(entry['path'])
    if entry.get('delete'):
        path.unlink()
        continue
    lines = inputs[entry['path']].decode('utf-8').splitlines(keepends=True)
    edits = entry['edits']
    last = 0
    for first, stop, text in edits:
        assert type(first) is int and type(stop) is int and last <= first <= stop <= len(lines)
        assert isinstance(text, str)
        last = stop
    for first, stop, text in reversed(edits):
        lines[first:stop] = text.splitlines(keepends=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(lines), encoding='utf-8')
    assert entry['mode'] in ('100644', '100755')
    path.chmod(0o755 if entry['mode'] == '100755' else 0o644)
git('add', '-A')
git('diff', '--cached', '--check')
actual = git('write-tree')
print('ACTUAL_TREE='+actual)
if actual != TARGET_TREE:
    print(git('diff', '--cached', '--raw', '--abbrev=40'))
assert actual == TARGET_TREE, 'refuse publication: not the locally tested tree'
print('VERIFIED_TREE='+actual)
