#!/usr/bin/env python3
"""The HIL workflows' glue (SKILL.md beside this directory, CI): hil.yml's selection and per-board build status, and
hil_comment.yml's PR, rig attempt, report and freshness check. Stdlib only, `gh` for the API; hil_comment.yml runs it
from the default branch, every artifact read as untrusted data.

  hil_workflow.py select --event E --repository R --run-id N --tested SHA --out FILE [--boards "B ..."]
      the boards a run tests: none outside UPSTREAM; a dispatch's named roster boards; a pull request's from
      check_build.py --select-only against HEAD^1. Writes the selection and boards/matrix to $GITHUB_OUTPUT.
  hil_workflow.py status --board B --attempt N --outcome O [--versions FILE] --out FILE
      one hosted build leg's outcome, with the platform and the core and arduino-cli versions setup recorded.
  hil_workflow.py pr --repo R --head-sha SHA --head-branch BR --head-repo R2 --prs JSON
      the open PR of R a HIL run is for, from trusted workflow_run metadata; prints its number or nothing (none, or
      ambiguous); exit 2 when the API cannot tell.
  hil_workflow.py rig-attempt --repo R --run-id N --attempt N
      the attempt the hil-run job of run N attempt N ran in; prints nothing when it did not run.
  hil_workflow.py render --selection DIR --status DIR --run DIR --run-id N --rig-attempt N --pr-head SHA
                         --run-url URL --conclusion C --out FILE
  hil_workflow.py fresh --repo R --pr N --run-id N --attempt N --head-sha SHA
      exit 0 when run N attempt N is the newest HIL run of the PR's current head, 1 stale (why on stdout), 2 when the
      API cannot tell.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[4]
UPSTREAM = 'adafruit/Adafruit_TinyUSB_Arduino'
ROSTER = 'test/hil/ci.json'
WORKFLOW = 'hil.yml'
RIG_JOB = 'hil-run'
STATUS_DIR = re.compile(r'hil-status-([A-Za-z0-9_]+)-([1-9][0-9]*)')   # one build leg's artifact, per attempt
TITLE = '## Hardware-in-the-loop (HIL) Test Report'
MAX_JSON = 1 << 20   # bytes of one artifact file read
MAX_TEXT = 300       # characters of one report string shown
MAX_COMMENT = 60000  # GitHub caps a comment at 65536
TESTS = ('cdc_echo', 'hid_inout', 'msc_ramdisk', 'host_device_info')   # hil_test.py's, in report order
VERDICT = {'pass': '✅ pass', 'fail': '❌ fail', 'blocker': '⛔ blocker', 'not-run': '⚪ not run'}
COVERAGE = ('The battery runs `serial_echo`, `hid_generic_inout` and `msc_ramdisk` on device boards and '
            '`host_device_info` on host boards; no other example, class or family is tested here.')


class ApiError(Exception):
    pass


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


# ---- the default branch: PR, rig attempt, freshness

def gh(path, paginate_key=None):
    """The API's JSON at `path`; with paginate_key, that list across every page."""
    argv = ['gh', 'api', '-H', 'Accept: application/vnd.github+json', path]
    if paginate_key:
        argv[2:2] = ['--paginate', '--jq', f'.{paginate_key}[]']
    p = subprocess.run(argv, capture_output=True, text=True)
    if p.returncode:
        raise ApiError(f'gh api {path}: {p.stderr.strip()[-300:]}')
    try:
        return [json.loads(ln) for ln in p.stdout.splitlines() if ln.strip()] if paginate_key else json.loads(p.stdout)
    except ValueError as e:
        raise ApiError(f'gh api {path}: {e}')


def cmd_pr(a):
    """Same-repo PRs are listed in the run's pull_requests; a fork's is found by its head repo, branch and SHA."""
    try:
        prs = json.loads(a.prs or '[]')
    except ValueError:
        prs = []
    numbers = [p.get('number') for p in prs if isinstance(p, dict) and
               ((p.get('base') or {}).get('repo') or {}).get('url', '').endswith(f'/repos/{a.repo}')]
    if not numbers and a.head_branch and a.head_repo:
        try:
            head = quote(f'{a.head_repo.split("/")[0]}:{a.head_branch}', safe='')   # a branch may hold '#', '+', '&'
            pulls = gh(f'repos/{a.repo}/pulls?state=open&per_page=100&head={head}')
        except ApiError as e:   # unknown, not absent: the comment job must not pass as if no PR existed
            print(e, file=sys.stderr)
            return 2
        numbers = [p['number'] for p in pulls if p.get('head', {}).get('sha') == a.head_sha and
                   p.get('base', {}).get('repo', {}).get('full_name') == a.repo]
    if len(numbers) == 1:
        print(numbers[0])
    elif numbers:
        print(f'ambiguous: PRs {numbers} share this run: no comment', file=sys.stderr)
    return 0


def cmd_rig_attempt(a):
    try:
        jobs = gh(f'repos/{a.repo}/actions/runs/{a.run_id}/attempts/{a.attempt}/jobs?per_page=100', 'jobs')
    except ApiError as e:
        print(e, file=sys.stderr)
        return 2
    rig = [j for j in jobs if j.get('name') == RIG_JOB]
    if rig and rig[0].get('conclusion') not in (None, 'skipped'):
        print(rig[0].get('run_attempt') or a.attempt)
    return 0


def is_run_of(run, pr, head):
    """Whether pull_request run `run` may be PR `pr`'s, whose head is `head` (tinyusb code_size_ci.py): GitHub lists
    a same-repo PR in pull_requests and leaves a fork's empty, so an unlisted run counts when it built the head."""
    numbers = [p['number'] for p in run.get('pull_requests') or []]
    if numbers:
        return pr in numbers
    return run.get('head_branch') == head.get('ref') and \
        (run.get('head_repository') or {}).get('full_name') == (head.get('repo') or {}).get('full_name')


def cmd_fresh(a):
    try:
        head = gh(f'repos/{a.repo}/pulls/{a.pr}')['head']
        why = None
        if head['sha'] != a.head_sha:
            why = 'the PR head moved on'
        else:
            runs = [r for r in gh(f'repos/{a.repo}/actions/workflows/{WORKFLOW}/runs?head_sha={a.head_sha}'
                                  f'&event=pull_request&per_page=100', 'workflow_runs') if is_run_of(r, a.pr, head)]
            latest = max(runs, key=lambda r: r['run_number'], default=None)
            if latest is None:
                why = 'no HIL run of the PR head found'
            elif latest['id'] != a.run_id:
                why = f'a newer HIL run exists: {latest["id"]}'
            elif gh(f'repos/{a.repo}/actions/runs/{a.run_id}')['run_attempt'] != a.attempt:
                why = 'a newer attempt exists'
    except (ApiError, KeyError, TypeError) as e:
        print(f'unknown: {e}')
        return 2
    print(f'stale: {why}' if why else 'current')
    return 1 if why else 0


# ---- render

def read_json(base, rel):
    """An artifact file as data: a regular file inside base, not a link, at most MAX_JSON bytes; None otherwise."""
    base = Path(base)
    p = base / rel
    try:
        if p.is_symlink() or not p.is_file() or base.resolve() not in p.resolve().parents:
            return None
        if p.stat().st_size > MAX_JSON:
            return None
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, RecursionError):
        return None


def listed(v):
    """v when it is a list, else empty: an artifact's nested types are untrusted too."""
    return v if isinstance(v, list) else []


def as_str(v):
    """v when it is a string, else None, so an untrusted value is a safe dict key."""
    return v if isinstance(v, str) else None


def literal(v, table=False):
    """Text on one line as an inline code span, so no Markdown, HTML, mention or link in it renders; pipes escaped
    in a table cell (GFM unescapes them inside the span)."""
    text = ' '.join(str(v).split())[:MAX_TEXT]
    if not text:
        return ''
    fence = '`' * (max(map(len, re.findall('`+', text)), default=0) + 1)
    pad = ' ' if text[0] in '` ' or text[-1] in '` ' else ''
    if table:
        text = text.replace('|', '\\|')
    return f'{fence}{pad}{text}{pad}{fence}'


def duration(s):
    if not isinstance(s, (int, float)):
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


def core_of(status, r):
    fw = (r or {}).get('firmware') if isinstance((r or {}).get('firmware'), dict) else None
    plat = fw.get('platform') if fw and isinstance(fw.get('platform'), dict) else (status or {}).get('platform')
    if not isinstance(plat, dict) or not plat.get('id'):
        return '—'
    return literal(f'{plat["id"]} {plat.get("version") or "?"}', True)


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
    lines += ['', f'<sub>{COVERAGE} ✅ pass · ❌ fail · ⛔ blocker (board held for recovery) · ⚪ not run · — not part '
              'of the board\'s battery · ❔ unknown</sub>']
    return lines


def cmd_render(a):
    sel = read_json(a.selection, 'hil-selection.json')
    statuses = {}   # the newest attempt's per board: every attempt's artifact is its own dir
    for d in sorted(Path(a.status).iterdir()) if Path(a.status).is_dir() else []:
        m = STATUS_DIR.fullmatch(d.name)
        s = read_json(a.status, f'{d.name}/{m[1]}.json') if m and not d.is_symlink() else None
        if s and s.get('board') == m[1] and s.get('attempt') == int(m[2]) and \
                s['attempt'] > statuses.get(m[1], {}).get('attempt', 0):
            statuses[m[1]] = s
    summary = read_json(a.run, 'summary.json') if a.rig_attempt else None
    text = '\n'.join(render(sel, statuses, summary, a)) + '\n'
    if len(text) > MAX_COMMENT:   # at a line end: a code span never crosses one, so none is left open
        text = text[:text.rfind('\n', 0, MAX_COMMENT) + 1] + '\n…truncated: see the run\'s artifacts.\n'
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
    s = sub.add_parser('pr')
    for k in ('--repo', '--head-sha'):
        s.add_argument(k, required=True)
    for k in ('--head-branch', '--head-repo', '--prs'):
        s.add_argument(k, default='')
    s = sub.add_parser('rig-attempt')
    s.add_argument('--repo', required=True)
    s.add_argument('--run-id', required=True, type=int)
    s.add_argument('--attempt', required=True, type=int)
    s = sub.add_parser('render')
    for k in ('--selection', '--status', '--run', '--run-id', '--pr-head', '--run-url', '--out'):
        s.add_argument(k, required=True)
    s.add_argument('--rig-attempt', default='')
    s.add_argument('--conclusion', default='')
    s = sub.add_parser('fresh')
    s.add_argument('--repo', required=True)
    s.add_argument('--pr', required=True, type=int)
    s.add_argument('--run-id', required=True, type=int)
    s.add_argument('--attempt', required=True, type=int)
    s.add_argument('--head-sha', required=True)
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(sys.argv[1:] if argv is None else argv)
    return {'select': cmd_select, 'status': cmd_status, 'pr': cmd_pr, 'rig-attempt': cmd_rig_attempt,
            'render': cmd_render, 'fresh': cmd_fresh}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
