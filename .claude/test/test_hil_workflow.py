#!/usr/bin/env python3
"""hil_workflow.py, the HIL workflows' glue: selection, build status, the PR and rig attempt of a run, the report
rendered from untrusted artifacts, and the freshness check. `gh` and check_build.py are stubbed: stubs prove the
plumbing only; the workflows are verified by a real CI run."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'))
import hil_workflow as W  # noqa: E402

HEAD = 'a' * 40
MERGE = 'b' * 40


def run(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        code = W.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Tmp(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.tmp = Path(d.name)


class Select(Tmp):
    def select(self, event='pull_request', repository=W.UPSTREAM, boards='', check=None):
        out, gh_out = self.tmp / 'sel.json', self.tmp / 'gh_output'
        for f in (out, gh_out):
            f.unlink(missing_ok=True)
        with mock.patch.dict(os.environ, {'GITHUB_OUTPUT': str(gh_out)}), \
                mock.patch.object(W.subprocess, 'run', return_value=check) as sp:
            code, _, err = run('select', '--event', event, '--repository', repository, '--run-id', '9',
                               '--tested', MERGE, '--boards', boards, '--out', str(out))
        outputs = dict(ln.split('=', 1) for ln in gh_out.read_text().splitlines()) if gh_out.exists() else {}
        return code, json.loads(out.read_text()) if out.exists() else None, outputs, sp, err

    def test_a_pull_request_takes_check_builds_selection(self):
        sel = {'hil': {'boards': ['raspberry_pi_pico'], 'full': False, 'uncovered': [{'family': 'nrf', 'reason': 'r'}]},
               'build': {'uncovered': [{'path': 'src/arduino/Adafruit_USBD_CDC.h', 'reason': 'bundled'}]}}
        check = subprocess.CompletedProcess([], 3, stdout='log\n' + json.dumps(sel) + '\n', stderr='')
        code, s, outputs, sp, _ = self.select(check=check)
        self.assertEqual(code, 0)
        self.assertIn('--select-only', sp.call_args[0][0])
        self.assertEqual((s['eligible'], s['hil']['boards'], s['build']['uncovered'][0]['reason'], s['runId'],
                          s['tested']), (True, ['raspberry_pi_pico'], 'bundled', '9', MERGE))
        self.assertEqual(outputs, {'eligible': 'true', 'boards': 'raspberry_pi_pico',
                                   'matrix': '[{"board": "raspberry_pi_pico"}]'})

    def test_a_failed_selection_fails_rather_than_selecting_nothing(self):
        for check in (subprocess.CompletedProcess([], 2, stdout='{"error": "x"}\n', stderr=''),
                      subprocess.CompletedProcess([], 1, stdout='garbage', stderr='boom')):
            code, s, outputs, _, _ = self.select(check=check)
            self.assertEqual((code, s, outputs), (2, None, {}))

    def test_outside_upstream_nothing_is_eligible(self):
        code, s, outputs, sp, _ = self.select(repository='someone/fork')
        self.assertEqual((code, s['eligible'], outputs['matrix'], outputs['boards']), (0, False, '[]', ''))
        sp.assert_not_called()

    def test_a_dispatch_runs_named_roster_boards_only(self):
        code, s, outputs, sp, _ = self.select(event='workflow_dispatch', boards='metro_m4_express raspberry_pi_pico')
        self.assertEqual((code, s['hil']['boards']), (0, ['metro_m4_express', 'raspberry_pi_pico']))
        sp.assert_not_called()
        code, s, outputs, _, err = self.select(event='workflow_dispatch', boards='nope')
        self.assertEqual((code, s), (2, None))
        self.assertIn('nope', err)


class Status(Tmp):
    def test_records_outcome_core_and_cli(self):
        versions = self.tmp / 'v.json'
        versions.write_text(json.dumps({'cli': {'VersionString': '1.3.1'}, 'cores': {'platforms': [
            {'id': 'esp32:esp32', 'installed_version': '3.3.9'}, {'id': 'rp2040:rp2040', 'installed_version': '5.4.4'}]}}))
        out = self.tmp / 's' / 'raspberry_pi_pico.json'
        code, _, _ = run('status', '--board', 'raspberry_pi_pico', '--attempt', '2', '--outcome', 'failure',
                         '--versions', str(versions), '--out', str(out))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text()), {'board': 'raspberry_pi_pico', 'attempt': 2, 'outcome': 'failure',
                                                       'platform': {'id': 'rp2040:rp2040', 'version': '5.4.4'},
                                                       'cli': '1.3.1'})

    def test_missing_versions_are_unknown(self):
        out = self.tmp / 'x.json'
        run('status', '--board', 'espressif_s3_devkitm', '--attempt', '1', '--outcome', 'failure',
            '--versions', str(self.tmp / 'none.json'), '--out', str(out))
        self.assertEqual(json.loads(out.read_text())['platform'], {'id': 'esp32:esp32', 'version': None})


class Api(unittest.TestCase):
    def gh(self, replies):
        def fake(path, paginate_key=None):
            for prefix, reply in replies.items():
                if path.startswith(prefix):
                    if isinstance(reply, Exception):
                        raise reply
                    return reply
            raise AssertionError(path)
        return mock.patch.object(W, 'gh', fake)

    def test_pr_of_a_same_repo_run_comes_from_its_metadata(self):
        prs = [{'number': 7, 'base': {'repo': {'url': f'https://api.github.com/repos/{W.UPSTREAM}'}}},
               {'number': 8, 'base': {'repo': {'url': 'https://api.github.com/repos/other/repo'}}}]
        with self.gh({}):
            self.assertEqual(run('pr', '--repo', W.UPSTREAM, '--head-sha', HEAD, '--prs', json.dumps(prs))[1], '7\n')

    def test_pr_of_a_fork_run_is_the_open_pr_of_its_head(self):
        pulls = [{'number': 5, 'head': {'sha': HEAD}, 'base': {'repo': {'full_name': W.UPSTREAM}}},
                 {'number': 6, 'head': {'sha': 'c' * 40}, 'base': {'repo': {'full_name': W.UPSTREAM}}}]
        with self.gh({f'repos/{W.UPSTREAM}/pulls?': pulls}):
            out = run('pr', '--repo', W.UPSTREAM, '--head-sha', HEAD, '--head-branch', 'fix', '--head-repo', 'u/fork',
                      '--prs', '[]')[1]
        self.assertEqual(out, '5\n')

    def test_an_ambiguous_or_missing_pr_gets_no_comment(self):
        pulls = [{'number': n, 'head': {'sha': HEAD}, 'base': {'repo': {'full_name': W.UPSTREAM}}} for n in (5, 6)]
        with self.gh({f'repos/{W.UPSTREAM}/pulls?': pulls}):
            code, out, err = run('pr', '--repo', W.UPSTREAM, '--head-sha', HEAD, '--head-branch', 'b',
                                 '--head-repo', 'u/f')
        self.assertEqual((code, out), (0, ''))
        self.assertIn('ambiguous', err)
        with self.gh({f'repos/{W.UPSTREAM}/pulls?': W.ApiError('down')}):
            self.assertEqual(run('pr', '--repo', W.UPSTREAM, '--head-sha', HEAD, '--head-branch', 'b',
                                 '--head-repo', 'u/f')[:2], (2, ''))

    def test_rig_attempt_is_the_hil_run_jobs_own(self):
        jobs = f'repos/{W.UPSTREAM}/actions/runs/9/attempts/3/jobs'
        with self.gh({jobs: [{'name': 'select', 'run_attempt': 3},
                             {'name': 'hil-run', 'run_attempt': 2, 'conclusion': 'success'}]}):
            self.assertEqual(run('rig-attempt', '--repo', W.UPSTREAM, '--run-id', '9', '--attempt', '3')[1], '2\n')
        with self.gh({jobs: [{'name': 'hil-run', 'run_attempt': 3, 'conclusion': 'skipped'}]}):
            self.assertEqual(run('rig-attempt', '--repo', W.UPSTREAM, '--run-id', '9', '--attempt', '3')[1], '')
        with self.gh({jobs: W.ApiError('down')}):
            self.assertEqual(run('rig-attempt', '--repo', W.UPSTREAM, '--run-id', '9', '--attempt', '3')[0], 2)

    def fresh(self, replies):
        with self.gh(replies):
            return run('fresh', '--repo', W.UPSTREAM, '--pr', '7', '--run-id', '9', '--attempt', '2',
                       '--head-sha', HEAD)[:2]

    def test_fresh(self):
        pull = {f'repos/{W.UPSTREAM}/pulls/7': {'head': {'sha': HEAD, 'ref': 'fix', 'repo': {'full_name': 'u/f'}}}}
        runs = f'repos/{W.UPSTREAM}/actions/workflows/{W.WORKFLOW}/runs'
        mine = {'id': 9, 'run_number': 4, 'pull_requests': [{'number': 7}]}
        other_pr = {'id': 12, 'run_number': 6, 'pull_requests': [{'number': 8}]}
        fork = {'id': 11, 'run_number': 5, 'pull_requests': [], 'head_branch': 'fix',
                'head_repository': {'full_name': 'u/f'}}
        self.assertEqual(self.fresh({**pull, runs: [mine, other_pr], f'repos/{W.UPSTREAM}/actions/runs/9':
                                     {'run_attempt': 2}}), (0, 'current\n'))
        self.assertEqual(self.fresh({**pull, runs: [mine, fork]}), (1, 'stale: a newer HIL run exists: 11\n'))
        self.assertEqual(self.fresh({**pull, runs: [mine], f'repos/{W.UPSTREAM}/actions/runs/9': {'run_attempt': 3}}),
                         (1, 'stale: a newer attempt exists\n'))
        moved = {f'repos/{W.UPSTREAM}/pulls/7': {'head': {'sha': 'c' * 40}}}
        self.assertEqual(self.fresh(moved), (1, 'stale: the PR head moved on\n'))
        self.assertEqual(self.fresh({f'repos/{W.UPSTREAM}/pulls/7': W.ApiError('down')})[0], 2)


def board(name, verdict='pass', tests=(('cdc_echo', True),), **over):
    r = {'board': name, 'verdict': verdict, 'detail': '', 'flaky': False, 'retry': '', 'waitedS': 0, 'runS': 75,
         'tests': [{'test': t, 'pass': p, 'detail': f'{t} detail'} for t, p in tests],
         'cleanup': {'pass': True, 'state': 'restored', 'holder': None},
         'firmware': {'platform': {'id': 'rp2040:rp2040', 'version': '5.4.4'}}}
    r.update(over)
    return r


class Render(Tmp):
    def setUp(self):
        super().setUp()
        for d in ('selection', 'status', 'run'):
            (self.tmp / d).mkdir()

    def write(self, rel, data):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return p

    def selection(self, boards=('raspberry_pi_pico',), **over):
        sel = {'eligible': True, 'reason': '', 'runId': '9', 'tested': MERGE,
               'hil': {'boards': list(boards), 'full': False, 'uncovered': []}, 'build': {'uncovered': []}}
        sel.update(over)
        self.write('selection/hil-selection.json', sel)

    def summary(self, boards, run_id='9', attempt=2):
        self.write('run/summary.json', {'kind': 'arduino-hil-ci', 'runId': run_id, 'attempt': attempt,
                                        'boards': boards})

    def status(self, name, attempt, outcome='success'):
        self.write(f'status/hil-status-{name}-{attempt}/{name}.json',
                   {'board': name, 'attempt': attempt, 'outcome': outcome,
                    'platform': {'id': 'rp2040:rp2040', 'version': '5.4.4'}})

    def render(self, rig_attempt='2', conclusion='success'):
        out = self.tmp / 'comment.md'
        code, _, _ = run('render', '--selection', str(self.tmp / 'selection'), '--status', str(self.tmp / 'status'),
                         '--run', str(self.tmp / 'run'), '--run-id', '9', '--rig-attempt', rig_attempt,
                         '--pr-head', HEAD, '--run-url', 'https://github.com/x/y/actions/runs/9',
                         '--conclusion', conclusion, '--out', str(out))
        self.assertEqual(code, 0)
        return out.read_text()

    def test_a_passing_board(self):
        self.selection()
        self.status('raspberry_pi_pico', 2)
        self.summary([board('raspberry_pi_pico', tests=[('cdc_echo', True), ('hid_inout', True)])])
        text = self.render()
        self.assertTrue(text.startswith(W.TITLE))
        self.assertIn('**1** pass', text)
        row = next(ln for ln in text.splitlines() if ln.startswith('| `raspberry_pi_pico`'))
        self.assertEqual(row, '| `raspberry_pi_pico` | ✅ | ✅ pass | ✅ | ✅ | — | — | ✅ | 1m15s | '
                              '`rp2040:rp2040 5.4.4` |')
        self.assertIn('[run 9 attempt 2](https://github.com/x/y/actions/runs/9)', text)

    def test_failures_retries_and_waits_are_noted(self):
        self.selection(boards=('metro_m4_express', 'raspberry_pi_pico'))
        self.summary([board('raspberry_pi_pico', 'fail', tests=[('cdc_echo', False)], detail='echo failed',
                            retry='retry not run: deadline'),
                      board('metro_m4_express', flaky=True, waitedS=40)])
        text = self.render()
        self.assertIn('**1** pass · **1** fail', text)
        self.assertIn('- `raspberry_pi_pico`: `echo failed` (`retry not run: deadline`)', text)
        self.assertIn('  - `cdc_echo`: `cdc_echo detail`', text)
        self.assertIn('✅ pass (flaky)', text)
        self.assertIn('passed on retry, waited 40s for the board', text)

    def test_hostile_report_text_renders_inert(self):
        self.selection()
        self.summary([board('raspberry_pi_pico', 'fail', detail='@hathach | <img src=x> `code` [x](http://e)\n## h',
                            retry='retry not run: @hathach\n## r [x](http://e)')])
        text = self.render()
        self.assertIn('`retry not run: @hathach ## r [x](http://e)`', text)
        self.assertNotIn('\n## r', text)
        line = next(ln for ln in text.splitlines() if ln.startswith('- `raspberry_pi_pico`'))
        self.assertIn('``@hathach | <img src=x> `code` [x](http://e) ## h``', line)
        self.assertNotIn('\n## h', text)

    def test_nothing_reads_as_a_pass_without_a_hardware_result(self):
        self.selection()
        self.status('raspberry_pi_pico', 1, 'failure')
        text = self.render(rig_attempt='', conclusion='failure')
        self.assertIn('The rig job did not run (workflow `failure`)', text)
        self.assertNotIn('| ✅ pass', text)
        self.assertIn('| `raspberry_pi_pico` | ❌ | ❔ |', text)
        text = self.render()   # the rig ran, no summary
        self.assertIn('left no readable summary', text)
        self.summary([board('raspberry_pi_pico')], attempt=1)   # another attempt's summary
        self.assertIn('not this one', self.render())
        self.summary([board('raspberry_pi_pico')], run_id='8')
        self.assertIn('not this one', self.render())

    def test_selection_states(self):
        self.assertIn('selection of this run is unavailable', self.render())
        self.selection(runId='8')
        self.assertIn('selection of this run is unavailable', self.render())
        self.selection(boards=(), hil={'boards': [], 'full': False, 'uncovered': [{'family': 'nrf', 'reason': 'r'}]},
                       build={'uncovered': [{'path': 'src/arduino/Adafruit_USBD_CDC.h', 'reason': 'bundled'}]})
        text = self.render()
        self.assertIn('No farm board is reached by this change.', text)
        self.assertIn('- family `nrf`: `r`', text)
        self.assertIn('- `src/arduino/Adafruit_USBD_CDC.h`: `bundled`', text)
        self.selection(eligible=False, reason='not upstream')
        self.assertIn('HIL not run: `not upstream`', self.render())

    def test_the_newest_attempts_build_status_counts(self):
        self.selection()
        self.status('raspberry_pi_pico', 1, 'failure')
        self.status('raspberry_pi_pico', 2, 'success')
        self.summary([board('raspberry_pi_pico')])
        self.assertIn('| `raspberry_pi_pico` | ✅ |', self.render())

    def test_a_board_whose_rerun_build_failed_says_its_earlier_build_ran(self):
        self.selection()
        self.status('raspberry_pi_pico', 2, 'failure')
        self.summary([board('raspberry_pi_pico')])
        self.assertIn('build `failure` (the rig ran an earlier attempt\'s build)', self.render())

    def test_unsafe_artifact_files_are_ignored(self):
        self.selection()
        outside = self.write('elsewhere/summary.json', {'runId': '9', 'attempt': 2, 'boards': [board('raspberry_pi_pico')]})
        (self.tmp / 'run' / 'summary.json').symlink_to(outside)
        self.assertIn('left no readable summary', self.render())
        (self.tmp / 'run' / 'summary.json').unlink()
        (self.tmp / 'run' / 'summary.json').write_text('x' * (W.MAX_JSON + 1))
        self.assertIn('left no readable summary', self.render())
        self.write('status/hil-status-raspberry_pi_pico-2/raspberry_pi_pico.json',
                   {'board': 'metro_m4_express', 'attempt': 2, 'outcome': 'success'})   # names another board
        self.write('status/hil-status-raspberry_pi_pico-3/raspberry_pi_pico.json',
                   {'board': 'raspberry_pi_pico', 'attempt': 1, 'outcome': 'success'})  # another attempt
        self.summary([board('raspberry_pi_pico')])
        self.assertIn('| `raspberry_pi_pico` | ❔ |', self.render())

    def test_unselected_rows_and_missing_rows_are_shown(self):
        self.selection(boards=('metro_m4_express', 'raspberry_pi_pico'))
        self.summary([board('raspberry_pi_pico'), board('adafruit_fruit_jam')])
        text = self.render()
        self.assertIn('**1** not reported', text)
        self.assertIn('rows for boards not selected: `adafruit_fruit_jam`', text)

    def test_a_huge_report_is_truncated(self):
        self.selection(boards=tuple(f'b{i}' for i in range(400)))
        self.summary([board(f'b{i}', 'fail', detail='x' * 400) for i in range(400)])
        text = self.render()
        self.assertLessEqual(len(text), W.MAX_COMMENT + 100)
        self.assertIn('truncated', text)


if __name__ == '__main__':
    unittest.main()
