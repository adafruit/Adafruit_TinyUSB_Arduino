#!/usr/bin/env python3
"""Run the HIL harness on a remote rig (default ci.lan) from the dev PC that built the firmware.

SKILL.md beside this directory is the contract. The rig gets binaries only: the harness, the roster, the build records
with their images and the agentrc files the harness loads, each pinned by digest in manifest.json, in a create-only
attempt dir <base>/<run-id> (agentrc hil_remote_core). Local records and collections live under .hil-remote/.

  hil_remote.py run --run-id ID [--host H] [--roster R] [-b BOARD]... [-t TEST]...
      stage HEAD's clean build records and launch `hil_test.py run` detached; prints the launch JSON
  hil_remote.py wait ID [--timeout S]      until done (collected into .hil-remote/ID), or the timeout
  hil_remote.py collect ID                 fetch whatever the attempt has now, whatever its state
  hil_remote.py session open --run-id ID -b BOARD [--host H] [--roster R]
      stage the board's clean record and take a hold on the board under the reason `arduino-hil session ID`
  hil_remote.py session stage ID RECORD...                 stage more records (experimental ones too)
  hil_remote.py session exec ID flash|gdb|restore OPTS...  hil_test.py's command under the session's hold
  hil_remote.py session exec ID exec [--timeout S] -- ARGV...
  hil_remote.py session close ID           restore serial_echo, check its echo, release the hold

Exits: run, session open/stage 0 launched/held/staged, 1 refused or failed (an `error:` line names why), 2 the agentrc
pin is missing; wait 0 done, 3 running, 4 dead, 5 inconclusive (hil_remote_core); collect 0 complete (a session's:
fetched); session exec/close pass the rig command's exit through.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
import agentrc_pin  # noqa: E402
import hil_test  # noqa: E402

RUNS = ROOT / '.hil-remote'
BASE = '~/hil-remote/adafruit-tinyusb-arduino'
ROSTER = 'test/hil/ci.json'
HIL = '.claude/skills/hil-contract/scripts/hil_test.py'
HARNESS = (HIL, '.claude/skills/hil-contract/scripts/agentrc_pin.py', '.claude/skills/build/scripts/check_build.py',
           'test/hil/agentrc.json')
RUN_DIR = 'hil-run'
MANIFEST = 'manifest.json'
SESSION_CMDS = ('flash', 'gdb', 'restore', 'exec')
OWN_OPTS = ('-b', '--board', '--under-hold', '--staged')

core = None


def load_core():
    global core
    core = agentrc_pin.loader(agentrc_pin.checkout()).load_file('skills/hil/scripts/hil_remote_core.py')
    return core


def rel(path):
    p = Path(path).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        core.fail(f'{path} is not inside the checkout {ROOT}')


def head_of_clean_tree():
    head = core.git(ROOT, 'rev-parse', 'HEAD')
    dirty = core.git(ROOT, 'status', '--porcelain', '--untracked-files=normal')
    if dirty:
        core.fail(f'the tree is not clean, so the staged harness would not be HEAD\'s:\n{dirty}')
    return head


def record_files(path, head, board=None, clean=True):
    """The checkout-relative files a build record stages (itself, its images, an experimental record's patch),
    refusing one this run's HEAD and roster would refuse on the rig anyway."""
    try:
        rec = json.loads(Path(path).read_text())
        sketches, source = rec['sketches'], rec['source']
    except (OSError, ValueError, KeyError, TypeError) as e:
        core.fail(f'build record {path}: {e!r}')
    why = [board and rec.get('board') != board and f'it is for {rec.get("board")}, not {board}',
           clean and rec.get('experimental') and 'it is experimental',
           source.get('head') != head and f'it was built at {str(source.get("head"))[:12]}, HEAD is {head[:12]}']
    why = [w for w in why if w]
    if why:
        core.fail(f'build record {path}: {"; ".join(why)}; rebuild with the build skill\'s --record')
    files = [rel(path), *(f for s in sketches.values() for f in s['sha256'])]
    patch = Path(f'{path}.patch')
    if rec.get('experimental') and patch.is_file():
        files.append(rel(patch))
    return rec, files


def snapshot(into, files, with_agentrc):
    """Copy files (checkout-relative) and, when asked, the agentrc closure the harness loads (under agentrc/) into
    `into`; {tree-relative path: sha256} of the copies, which is what gets staged."""
    digests = core.snapshot(ROOT, [ROOT / f for f in files], into)
    if with_agentrc:
        ag = agentrc_pin.checkout()
        closure = agentrc_pin.closure(ag, hil_test.SCRIPTS)
        digests.update({f'agentrc/{k}': v for k, v in
                        core.snapshot(ag, [ag / f for f in closure], Path(into) / 'agentrc').items()})
    return digests


def check_images(rec, digests):
    bad = [f for s in rec['sketches'].values() for f, d in s['sha256'].items() if digests.get(f) != d]
    if bad:
        core.fail(f'{rec["board"]}: {", ".join(bad[:3])} changed since the build record; rebuild')


def stage(tree, into, digests):
    core.stage_tree(tree, into)
    bad = core.verify_remote(tree, digests)
    if bad:
        core.fail(f'staged files differ on {tree.host}: {", ".join(bad[:5])}')


def staged_tree(run_id, host, roster, records, head):
    """Create attempt run_id on host holding the harness, roster, agentrc closure and records (record_files
    already checked), the snapshot pinned by manifest.json; its Tree."""
    with tempfile.TemporaryDirectory(prefix='hil-remote-') as tmp:
        into = Path(tmp)
        files = [*HARNESS, roster, *(f for _, fs in records for f in fs)]
        digests = snapshot(into, files, True)
        for rec, _ in records:
            check_images(rec, digests)
        drift = core.checkout_drift(ROOT, head)
        if drift:
            core.fail(f'the checkout moved while it was copied: {"; ".join(drift)}')
        core.write_json(into / MANIFEST, {'head': head, 'files': digests})
        tree = core.create_attempt(ROOT, RUNS, run_id, host, BASE)
        stage(tree, into, {**digests, MANIFEST: core.digest(into / MANIFEST)})
    return tree


def roster_boards(roster):
    try:
        return hil_test.load_roster(ROOT / roster)
    except hil_test.Refused as e:
        core.fail(str(e))


def cmd_run(a):
    roster = rel(a.roster)
    boards = roster_boards(roster)
    names = a.board or list(boards)
    unknown = [b for b in names if b not in boards]
    if unknown:
        core.fail(f'not in {roster}: {unknown}')
    head = head_of_clean_tree()
    records = [record_files(ROOT / 'build' / 'hil' / f'{b}.build.json', head, b) for b in names]
    tree = staged_tree(a.run_id, a.host, roster, records, head)
    argv = ['python3', '-u', HIL, 'run', '--staged', MANIFEST, '--run-dir', RUN_DIR,
            *(w for b in names for w in ('-b', b)), *(w for t in a.test or [] for w in ('-t', t)), roster]
    launch = core.launch(tree, argv)
    print(json.dumps({'runId': a.run_id, 'host': tree.host, 'dir': tree.dir, 'boards': names, 'launch': launch,
                      'next': f'hil_remote.py wait {a.run_id}'}))
    return 0


def report_present(path):
    return [] if (Path(path) / RUN_DIR / 'hil_report.json').is_file() else [f'{RUN_DIR}/hil_report.json']


def summary(run_id, st, dest):
    done = Path(dest) / core.RECORDS / 'done.json'
    out = {'runId': run_id, 'state': st.get('state'), 'dest': str(dest)}
    if done.is_file():
        out['rigExit'] = json.loads(done.read_text()).get('exit')
    if (Path(dest) / RUN_DIR / 'hil_report.json').is_file():
        out['report'] = f'python3 {HIL} report {Path(dest) / RUN_DIR}'
    return out


def cmd_wait(a):
    tree = core.load_attempt(ROOT, RUNS, a.run_id)
    dest = RUNS / a.run_id
    rc, st = core.wait_attempt(tree, a.timeout, dest=dest, groups=[[f'{RUN_DIR}/']], accept=report_present)
    out = summary(a.run_id, st, dest)
    if 'collection' in st:
        out['collection'] = st['collection']['state']
    print(json.dumps(out))
    return rc


def cmd_collect(a):
    tree = core.load_attempt(ROOT, RUNS, a.run_id)
    dest = RUNS / a.run_id
    session = session_record(a.run_id, required=False)
    if session:
        record = core.collect(tree, dest, optional=['build/hil-manual/'])
    else:
        record = core.collect(tree, dest, groups=[[f'{RUN_DIR}/']], accept=report_present)
    print(json.dumps({**summary(a.run_id, {'state': record['remoteState']}, dest), 'collection': record['state']}))
    return 0 if record['state'] == 'complete' or session else 1


# ---- sessions

def session_path(run_id):
    core.check_run_id(run_id)
    return RUNS / f'{run_id}.session.json'


def session_record(run_id, required=True):
    p = session_path(run_id)
    if not p.is_file():
        if required:
            core.fail(f'no session {run_id} was opened from this checkout')
        return None
    return json.loads(p.read_text())


def rig(tree, argv):
    return core.run_remote(tree, ['python3', '-u', *argv])


def cmd_session_open(a):
    roster = rel(a.roster)
    if a.board not in roster_boards(roster):
        core.fail(f'{a.board} is not in {roster}')
    head = head_of_clean_tree()
    record = record_files(ROOT / 'build' / 'hil' / f'{a.board}.build.json', head, a.board)
    tree = staged_tree(a.run_id, a.host, roster, [record], head)
    reason = f'arduino-hil session {a.run_id}'
    data = {'runId': a.run_id, 'board': a.board, 'reason': reason, 'roster': roster, 'head': head,
            'restoreRecord': record[1][0], 'held': False}
    core.write_json(session_path(a.run_id), data)
    rc = rig(tree, [HIL, 'lock', 'hold', a.board, '--reason', reason])
    if rc:
        print(json.dumps({**data, 'error': f'the hold was not taken (exit {rc})'}))
        return 1
    data['held'] = True
    core.write_json(session_path(a.run_id), data)
    print(json.dumps({**data, 'dir': tree.dir, 'next': f'hil_remote.py session exec {a.run_id} ...; '
                                                      f'hil_remote.py session close {a.run_id}'}))
    return 0


def cmd_session_stage(a):
    s = session_record(a.run_id)
    tree = core.load_attempt(ROOT, RUNS, a.run_id)
    records = [record_files(r, s['head'], s['board'], clean=False) for r in a.records]
    with tempfile.TemporaryDirectory(prefix='hil-remote-') as tmp:
        digests = snapshot(Path(tmp), [f for _, fs in records for f in fs], False)
        for rec, _ in records:
            check_images(rec, digests)
        stage(tree, Path(tmp), digests)
    print(json.dumps({'runId': a.run_id, 'staged': [fs[0] for _, fs in records]}))
    return 0


def session_argv(s, cmd, opts, argv):
    clash = [o for o in opts if o.split('=')[0] in OWN_OPTS]
    if clash:
        core.fail(f'{", ".join(clash)} come from the session; leave them out')
    if cmd == 'exec' and not argv:
        core.fail('session exec ID exec needs `-- ARGV...`')
    return [HIL, cmd, '-b', s['board'], '--under-hold', s['reason'], '--staged', MANIFEST, *opts, s['roster'],
            *(['--', *argv] if cmd == 'exec' else [])]


def cmd_session_exec(run_id, rest):
    s = session_record(run_id)
    if not rest or rest[0] not in SESSION_CMDS:
        core.fail(f'session exec ID takes one of {", ".join(SESSION_CMDS)}, then its options')
    cmd, rest = rest[0], rest[1:]
    opts, argv = (rest[:rest.index('--')], rest[rest.index('--') + 1:]) if '--' in rest else (rest, [])
    tree = core.load_attempt(ROOT, RUNS, run_id)
    return rig(tree, session_argv(s, cmd, opts, argv))


def cmd_session_close(a):
    s = session_record(a.run_id)
    tree = core.load_attempt(ROOT, RUNS, a.run_id)
    rc = rig(tree, session_argv(s, 'restore', ['--build-record', s['restoreRecord']], []))
    if rc:
        print(json.dumps({'runId': a.run_id, 'board': s['board'], 'closed': False, 'restoreExit': rc,
                          'note': 'the hold stays: recover the board under it (SKILL.md, Recovery)'}))
        return rc
    rc = rig(tree, [HIL, 'lock', 'release', s['board']])
    core.write_json(session_path(a.run_id), {**s, 'held': rc != 0, 'closed': rc == 0})
    print(json.dumps({'runId': a.run_id, 'board': s['board'], 'closed': rc == 0, 'releaseExit': rc}))
    return rc


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--run-id', required=True)
    r.add_argument('--host', default='ci.lan')
    r.add_argument('--roster', default=str(ROOT / ROSTER))
    r.add_argument('-b', '--board', action='append')
    r.add_argument('-t', '--test', action='append', choices=sorted(hil_test.TESTS))
    w = sub.add_parser('wait')
    w.add_argument('run_id')
    w.add_argument('--timeout', type=lambda t: core.wait_secs(t), default=570)
    c = sub.add_parser('collect')
    c.add_argument('run_id')
    s = sub.add_parser('session').add_subparsers(dest='session', required=True)
    o = s.add_parser('open')
    o.add_argument('--run-id', required=True)
    o.add_argument('-b', '--board', required=True)
    o.add_argument('--host', default='ci.lan')
    o.add_argument('--roster', default=str(ROOT / ROSTER))
    st = s.add_parser('stage')
    st.add_argument('run_id')
    st.add_argument('records', nargs='+')
    s.add_parser('exec', help='session exec ID flash|gdb|restore|exec ... (parsed by hil_test.py on the rig)')
    cl = s.add_parser('close')
    cl.add_argument('run_id')
    return ap.parse_args(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        core or load_core()
    except agentrc_pin.PinError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    if argv[:2] == ['session', 'exec']:   # its options are hil_test.py's, `--` included: not ours to parse
        if len(argv) < 3:
            core.fail('session exec needs a session id')
        return cmd_session_exec(argv[2], argv[3:])
    a = parse(argv)
    core.check_run_id(a.run_id)
    if a.cmd == 'session':
        return {'open': cmd_session_open, 'stage': cmd_session_stage, 'close': cmd_session_close}[a.session](a)
    return {'run': cmd_run, 'wait': cmd_wait, 'collect': cmd_collect}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
