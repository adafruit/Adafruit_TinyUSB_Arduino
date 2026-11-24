#!/usr/bin/env python3
"""The HIL workflows' glue (SKILL.md beside this directory, CI): hil.yml's selection and per-board build status, and
the report hil_comment.yml posts. Stdlib only; the report reads every artifact as untrusted data through agentrc's
gh_pr_comment.py (the agentrc agentrc_pin.py selects), which hil_comment.yml also runs for the PR, the rig attempt and
the sticky post, freshness checked.

  hil_workflow.py select --event E --repository R --run-id N --tested SHA --out FILE [--boards "B ..."]
      the boards a run tests: none outside UPSTREAM; a dispatch's named roster boards; a pull request's from
      check_build.py --select-only against HEAD^1. Writes the selection and boards/matrix to $GITHUB_OUTPUT.
  hil_workflow.py status --board B --attempt N --outcome O [--versions FILE] --out FILE
      one hosted build leg's outcome, with the platform and the core and arduino-cli versions setup recorded.
  hil_workflow.py render --selection DIR --status DIR --run DIR --run-id N --rig-attempt N --pr-head SHA
                         --run-url URL --conclusion C --out FILE
      exit 2 when no usable agentrc is selected.
"""

import argparse
import functools
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentrc_pin  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
UPSTREAM = 'adafruit/Adafruit_TinyUSB_Arduino'
ROSTER = 'test/hil/ci.json'
STATUS_DIR = re.compile(r'hil-status-([A-Za-z0-9_]+)-([1-9][0-9]*)')   # one build leg's artifact, per attempt
TITLE = '## Hardware-in-the-loop (HIL) Test Report'
TESTS = ('cdc_echo', 'hid_inout', 'msc_ramdisk', 'host_device_info')   # hil_test.py's, in report order
VERDICT = {'pass': '✅ pass', 'fail': '❌ fail', 'blocker': '⛔ blocker', 'not-run': '⚪ not run'}
COVERAGE = ('The battery runs `serial_echo`, `hid_generic_inout` and `msc_ramdisk` on device boards and '
            '`host_device_info` on host boards; no other example, class or family is tested here.')


# ---- hosted: selection and build status

def roster_boards():
    return [b['name'] for b in json.loads((ROOT / ROSTER).read_text())['boards']]


def cmd_select(a):
    sel = {'eligible': False, 'reason': '', 'runId': a.run_id, 'tested': a.tested,
           'hil': {'boards': [], 'full': False, 'uncovered': []}, 'build': {'uncovered': []}}
    if a.repository != UPSTREAM:
        sel['reason'] = f'{a.repository} is not {UPSTREAM}: the farm runs only the upstream repository'
    elif a.event == 'workflow_dispatch':
        boards = sorted(set((a.boards or '').split()))
        unknown = sorted(set(boards) - set(roster_boards()))
        if not boards or unknown:
            print(f'not in {ROSTER}: {unknown or "no board"}', file=sys.stderr)
            return 2
        sel['eligible'], sel['hil']['boards'] = True, boards
    else:
        p = subprocess.run([sys.executable, str(ROOT / '.claude/skills/build/scripts/check_build.py'), '--select-only',
                            '--base', 'HEAD^1', '--hil-config', ROSTER], cwd=ROOT, capture_output=True, text=True)
        try:
            out = json.loads(p.stdout.strip().splitlines()[-1])
            hil, build = out['hil'], out['build']
        except (ValueError, IndexError, KeyError, TypeError):
            print(f'selection failed (exit {p.returncode}): {p.stdout[-2000:]}{p.stderr[-2000:]}', file=sys.stderr)
            return 2
        if p.returncode not in (0, 3):   # 3: an uncovered path, still a selection
            print(f'selection failed (exit {p.returncode}): {out}', file=sys.stderr)
            return 2
        sel.update(eligible=True, hil=hil, build={'uncovered': build.get('uncovered', [])})
    Path(a.out).write_text(json.dumps(sel, indent=1) + '\n')
    boards = sel['hil']['boards']
    lines = [f'boards={" ".join(boards)}', f'matrix={json.dumps([{"board": b} for b in boards])}']
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    print(f'boards: {" ".join(boards) or "none"}' + (f' ({sel["reason"]})' if sel['reason'] else ''), file=sys.stderr)
    return 0


def cmd_status(a):
    try:
        versions = json.loads(Path(a.versions).read_text()) if a.versions else {}
    except (OSError, ValueError):
        versions = {}
    platform_id = versions.get('platform')   # null: setup failed before it named the platform
    cores = (versions.get('cores') or {}).get('platforms') or []
    core = next((c for c in cores if isinstance(c, dict) and c.get('id') == platform_id), {})
    status = {'board': a.board, 'attempt': a.attempt, 'outcome': a.outcome,
              'platform': {'id': platform_id, 'version': core.get('installed_version')},
              'cli': (versions.get('cli') or {}).get('VersionString')}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(status) + '\n')
    print(json.dumps(status))
    return 0


# ---- render

@functools.cache
def comment_lib():
    """agentrc's gh_pr_comment, from the agentrc agentrc_pin.py selects."""
    return agentrc_pin.load('skills/hil/scripts/gh_pr_comment.py')


@functools.cache
def hil_lib():
    """agentrc's arduino_hil, for its core_provenance."""
    return agentrc_pin.load('skills/arduino/scripts/arduino_hil.py')


def listed(v):
    """v when it is a list, else empty: an artifact's nested types are untrusted too."""
    return v if isinstance(v, list) else []


def as_str(v):
    """v when it is a string, else None, so an untrusted value is a safe dict key."""
    return v if isinstance(v, str) else None


def literal(v, table=False):
    return comment_lib().literal(v, table)


def duration(s):
    if not isinstance(s, (int, float)) or isinstance(s, float) and not math.isfinite(s):
        return '—'
    s = int(s)
    return f'{s}s' if s < 60 else f'{s // 60}m{s % 60:02d}s'


def short(sha):
    return literal(str(sha)[:10]) if sha else '—'


def board_row(name, status, r):
    build = {'success': '✅', 'failure': '❌', 'cancelled': '⚪'}.get(as_str((status or {}).get('outcome')), '❔')
    if r is None:
        return [literal(name, True), build, '❔'] + ['—'] * len(TESTS) + ['—', '—', core_of(status, None)]
    results = {t['test']: t.get('pass') for t in listed(r.get('tests'))
               if isinstance(t, dict) and as_str(t.get('test'))}
    tests = [('✅' if results[t] else '❌') if t in results else '—' for t in TESTS]
    cleanup = r.get('cleanup') if isinstance(r.get('cleanup'), dict) else None
    restore = '—' if not cleanup or cleanup.get('state') == 'untouched' else '✅' if cleanup.get('pass') else '❌'
    verdict = VERDICT.get(as_str(r.get('verdict')), '❔') + (' (flaky)' if r.get('flaky') else '')
    return [literal(name, True), build, verdict] + tests + [restore, duration(r.get('runS')), core_of(status, r)]


def firmware_of(r):
    fw = (r or {}).get('firmware')
    return fw if isinstance(fw, dict) else {}


def core_of(status, r):
    rec = firmware_of(r).get('platform')
    plat = rec if isinstance(rec, dict) else (status or {}).get('platform')
    if not isinstance(plat, dict) or not plat.get('id'):
        return '—'
    # a build record's platform carries its git provenance; a build status's has id/version only
    text = hil_lib().core_provenance(plat) if plat is rec else f'{plat["id"]} {plat.get("version") or "?"}'
    return literal(text, True)


def render(sel, statuses, summary, a):
    lines = [TITLE, '', f'PR head {short(a.pr_head)} · tested merge {short((sel or {}).get("tested"))} · '
             f'[run {a.run_id}{f" attempt {a.rig_attempt}" if a.rig_attempt else ""}]({a.run_url})', '']
    if sel is None or sel.get('runId') != str(a.run_id):
        return lines + ['⚠️ The selection of this run is unavailable: no HIL result for this push.']
    hil = sel.get('hil') if isinstance(sel.get('hil'), dict) else {}
    boards = [b for b in listed(hil.get('boards')) if isinstance(b, str)]
    if not sel.get('eligible'):
        lines.append(f'HIL not run: {literal(sel.get("reason") or "not eligible")}')
    elif not boards:
        lines.append('No farm board is reached by this change.')
    else:
        rows, why = {}, ''
        if not a.rig_attempt:
            why = f'The rig job did not run (workflow {literal(a.conclusion)}): no hardware result.'
        elif summary is None:
            why = f'The rig job of attempt {a.rig_attempt} left no readable summary: hardware result unknown.'
        elif summary.get('runId') != str(a.run_id) or summary.get('attempt') != int(a.rig_attempt):
            why = (f'The rig summary names run {literal(summary.get("runId"))} attempt '
                   f'{literal(summary.get("attempt"))}, not this one: hardware result unavailable.')
        else:
            rows = {r['board']: r for r in listed(summary.get('boards'))
                    if isinstance(r, dict) and as_str(r.get('board'))}
        if rows:
            counts = {}
            for b in boards:
                v = as_str((rows.get(b) or {}).get('verdict'))
                counts[v] = counts.get(v, 0) + 1
            lines.append(' · '.join(f'**{counts.get(v, 0)}** {VERDICT[v].split(" ", 1)[1]}' for v in VERDICT
                                    if counts.get(v)) + (f' · **{counts[None]}** not reported' if counts.get(None)
                                                         else ''))
            lines.append('')
        if why:
            lines += [why, '']
        head = ['Board', 'Build', 'Result', *TESTS, 'Restore', 'Time', 'Core']
        lines += ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
        for b in boards:
            lines.append('| ' + ' | '.join(board_row(b, statuses.get(b), rows.get(b))) + ' |')
        notes = []
        for b in boards:
            r = rows.get(b)
            st = statuses.get(b)
            if st and st.get('outcome') not in ('success', None):
                notes.append(f'- {literal(b)}: build {literal(st.get("outcome"))}'
                             + (' (the rig ran an earlier attempt\'s build)' if r and r.get('verdict') != 'not-run'
                                else ''))
            if r and (r.get('verdict') != 'pass' or r.get('flaky') or r.get('retry')):
                extra = [x for x in (literal(r.get('retry') or ''), 'passed on retry' if r.get('flaky') else '',
                                     f'waited {duration(r.get("waitedS"))} for the board' if r.get('waitedS') else '')
                         if x]
                notes.append(f'- {literal(b)}: {literal(r.get("detail") or r.get("verdict"))}'
                             + (f' ({", ".join(extra)})' if extra else ''))
                for t in listed(r.get('tests')):
                    if isinstance(t, dict) and t.get('pass') is False:
                        notes.append(f'  - {literal(t.get("test"))}: {literal(t.get("detail"))}')
            drifted = [h for h in listed(firmware_of(r).get('driftedHeaders')) if isinstance(h, str)]
            if drifted:
                notes.append(f'- {literal(b)}: the core\'s bundled copy of this library differs from this checkout in '
                             f'{", ".join(map(literal, drifted))}; the result covers that mix')
        if rows and set(rows) - set(boards):
            notes.append(f'- rows for boards not selected: {literal(" ".join(sorted(map(str, set(rows) - set(boards)))))}')
        if notes:
            lines += [''] + notes
    uncovered = [u for u in listed(hil.get('uncovered')) if isinstance(u, dict)]
    build = sel.get('build') if isinstance(sel.get('build'), dict) else {}
    paths = [u for u in listed(build.get('uncovered')) if isinstance(u, dict)]
    if uncovered or paths:
        lines += ['', 'Not covered by any farm board:']
        lines += [f'- family {literal(u.get("family"))}: {literal(u.get("reason"))}' for u in uncovered]
        lines += [f'- {literal(u.get("path"))}: {literal(u.get("reason"))}' for u in paths]
    lines += ['', f'<sub>{COVERAGE} ✅ pass · ❌ fail · ⛔ blocker (see the board\'s note; may need recovery) · '
              '⚪ not run · — not part of the board\'s battery · ❔ unknown</sub>']
    return lines


def cmd_render(a):
    try:
        G = comment_lib()
        hil_lib()   # a PinError must exit 2 before rendering starts
    except agentrc_pin.PinError as e:
        print(f'error: {e.with_remedy()}', file=sys.stderr)
        return 2
    sel = G.read_artifact_json(a.selection, 'hil-selection.json')
    statuses = {}   # the newest attempt's per board: every attempt's artifact is its own dir
    for d in sorted(Path(a.status).iterdir()) if Path(a.status).is_dir() else []:
        m = STATUS_DIR.fullmatch(d.name)
        s = G.read_artifact_json(a.status, f'{d.name}/{m[1]}.json') if m and not d.is_symlink() else None
        if s and s.get('board') == m[1] and s.get('attempt') == int(m[2]) and \
                s['attempt'] > statuses.get(m[1], {}).get('attempt', 0):
            statuses[m[1]] = s
    summary = G.read_artifact_json(a.run, 'summary.json') if a.rig_attempt else None
    text = G.cap('\n'.join(render(sel, statuses, summary, a)) + '\n')
    Path(a.out).write_text(text)
    print(text)
    return 0


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('select')
    s.add_argument('--event', required=True)
    s.add_argument('--repository', required=True)
    s.add_argument('--run-id', required=True)
    s.add_argument('--tested', required=True)
    s.add_argument('--boards')
    s.add_argument('--out', required=True)
    s = sub.add_parser('status')
    s.add_argument('--board', required=True)
    s.add_argument('--attempt', required=True, type=int)
    s.add_argument('--outcome', required=True)
    s.add_argument('--versions')
    s.add_argument('--out', required=True)
    s = sub.add_parser('render')
    for k in ('--selection', '--status', '--run', '--run-id', '--pr-head', '--run-url', '--out'):
        s.add_argument(k, required=True)
    s.add_argument('--rig-attempt', default='')
    s.add_argument('--conclusion', default='')
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(sys.argv[1:] if argv is None else argv)
    return {'select': cmd_select, 'status': cmd_status, 'render': cmd_render}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
