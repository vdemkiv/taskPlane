"""Offline security regressions, with explicit optional external scanner gates."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def offline(root: Path) -> list[str]:
    errors = []
    lines = (root/'.github/workflows/ci.yml').read_text().splitlines()
    for number, line in enumerate(lines):
        if 'uses: actions/checkout@' in line and (
                number + 1 >= len(lines) or 'persist-credentials: false' not in lines[number + 1]):
            errors.append(f'CI checkout retains credentials at line {number + 1}')
    for folder in ('taskplane', 'scripts', 'hooks'):
        for path in (root/folder).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id in {'eval', 'exec'}:
                    errors.append(f'{path.relative_to(root)}:{node.lineno}: dynamic execution')
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess':
                    if any(k.arg == 'shell' and not (isinstance(k.value, ast.Constant) and k.value.value is False) for k in node.keywords):
                        errors.append(f'{path.relative_to(root)}:{node.lineno}: configurable shell')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scanners', action='store_true', help='Also require all external scanners; never silently skip missing tools')
    args = parser.parse_args()
    errors = offline(ROOT)
    checks = [{'name':'offline boundaries and CI credentials','status':'fail' if errors else 'pass','details':errors}]
    if args.scanners:
        commands = [
            ['gitleaks', 'git', '--redact', '--config', '.gitleaks.toml', '.'],
            ['semgrep', 'scan', '--config', '.semgrep.yml', '--metrics', 'off', '--error', 'taskplane', 'scripts', 'hooks'],
            ['zizmor', '--offline', '.github/workflows'],
            ['pip-audit', '--disable-pip', '--no-deps', '-r', 'requirements-dev.lock'],
            ['pip-audit', '--disable-pip', '--no-deps', '-r', 'requirements-ci.txt'],
            ['bandit', '-r', 'taskplane', 'hooks', 'scripts', '-x', 'taskplane/tests', '-ll'],
        ]
        with tempfile.TemporaryDirectory(prefix='taskplane-security-') as temp:
            test_lock = Path(temp)/'tests.lock'
            test_lock.write_text('\n'.join(line.removeprefix('# test-lock: ') for line in (ROOT/'requirements-dev.lock').read_text().splitlines() if line.startswith('# test-lock: '))+'\n')
            commands.append(['pip-audit','--disable-pip','--no-deps','-r',str(test_lock)])
            for command in commands:
                tool = shutil.which(command[0])
                if not tool:
                    checks.append({'name':command[0],'status':'unknown','details':'scanner missing'})
                    continue
                result = subprocess.run([tool, *command[1:]], cwd=ROOT, capture_output=True, text=True, timeout=600)
                checks.append({'name':command[0], 'status':'pass' if result.returncode == 0 else 'fail',
                               'exit_code':result.returncode,'details':result.stderr[-2000:]})
    print(json.dumps({'checks':checks,'external_scanners':'requested' if args.scanners else 'not run'},indent=2))
    return 0 if all(check['status'] == 'pass' for check in checks) else 1


if __name__ == '__main__':
    sys.exit(main())
