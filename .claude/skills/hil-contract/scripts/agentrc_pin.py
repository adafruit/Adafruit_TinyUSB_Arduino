#!/usr/bin/env python3
"""The agentrc commit the HIL harness loads its shared scripts from (agentrc `hil` skill, Shared mechanisms).

  agentrc_pin.py fetch    fetch the pinned commit into the user cache (never touches another agentrc checkout)
  agentrc_pin.py check    exit 0 when the cached checkout is the pin and clean, else 1 naming why
  agentrc_pin.py path     print the cached checkout's path

The pin is test/hil/agentrc.json ({url, commit}). The checkout lives outside this library, in
${XDG_CACHE_HOME:-~/.cache}/adafruit-tinyusb-arduino/agentrc-<commit>, so Library Manager never ships it.
"""

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PIN_FILE = ROOT / 'test' / 'hil' / 'agentrc.json'
FETCH_HINT = 'python3 .claude/skills/hil-contract/scripts/agentrc_pin.py fetch'


class PinError(Exception):
    pass


def pin():
    data = json.loads(PIN_FILE.read_text())
    return data['url'], data['commit']


def cache_dir(commit):
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return Path(base) / 'adafruit-tinyusb-arduino' / f'agentrc-{commit}'


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                          env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})


def problem(root, commit):
    """Why `root` is not a clean checkout of `commit`, '' when it is."""
    if not (Path(root) / '.git').exists():
        return f'{root} is not a git checkout'
    head = _git(root, 'rev-parse', 'HEAD')
    if head.returncode or head.stdout.strip() != commit:
        return f'{root} is at {head.stdout.strip() or "?"}, not the pin {commit}'
    status = _git(root, 'status', '--porcelain')
    if status.returncode or status.stdout.strip():
        return f'{root} has local changes: {status.stdout.strip()[:200]}'
    return ''


def fetch():
    url, commit = pin()
    dest = cache_dir(commit)
    if dest.exists() and not problem(dest, commit):
        return dest
    if dest.exists():
        raise PinError(f'{dest} exists but {problem(dest, commit)}; remove it and fetch again')
    tmp = dest.with_name(dest.name + f'.tmp-{os.getpid()}')
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for args in (('init', '-q'), ('remote', 'add', 'origin', url), ('fetch', '-q', '--depth', '1', 'origin', commit),
                 ('checkout', '-q', '--detach', 'FETCH_HEAD')):
        p = _git(tmp, *args)
        if p.returncode:
            shutil.rmtree(tmp, ignore_errors=True)
            raise PinError(f'git {" ".join(args)}: {p.stderr.strip()}')
    tmp.rename(dest)
    why = problem(dest, commit)
    if why:
        raise PinError(why)
    return dest


def checkout():
    """The pinned checkout, refusing a missing, moved or modified one."""
    _, commit = pin()
    dest = cache_dir(commit)
    why = problem(dest, commit)
    if why:
        raise PinError(f'agentrc pin: {why}; run `{FETCH_HINT}`')
    return dest


def files_of(root, rel):
    """A script's FILES literal (agentrc hil_load.py), read without importing it."""
    tree = ast.parse((Path(root) / rel).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'FILES' for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    return ()


def closure(root, scripts):
    """Every agentrc-relative file `scripts` load or launch, themselves included."""
    out = set()
    for rel in scripts:
        out.add(rel)
        out.update(files_of(root, rel))
    missing = sorted(r for r in out if not (Path(root) / r).is_file())
    if missing:
        raise PinError(f'agentrc at {root} lacks {missing}')
    return sorted(out)


def loader(root):
    """agentrc's hil_load, bootstrapped as its docstring prescribes."""
    path = Path(root) / 'skills' / 'hil' / 'scripts' / 'hil_load.py'
    spec = importlib.util.spec_from_file_location('agentrc_hil_load', path)
    if spec.name not in sys.modules:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules.setdefault(spec.name, mod)
    return sys.modules[spec.name].load(spec.name, spec.origin)


def main(argv):
    cmd = argv[0] if argv else ''
    try:
        if cmd == 'fetch':
            print(fetch())
        elif cmd == 'check':
            print(checkout())
        elif cmd == 'path':
            print(cache_dir(pin()[1]))
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except PinError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
