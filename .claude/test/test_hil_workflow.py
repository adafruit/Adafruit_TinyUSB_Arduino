#!/usr/bin/env python3
"""hil_workflow.py, the HIL workflows' glue: selection, build status and the report rendered from untrusted artifacts
through agentrc's gh_pr_comment (the agentrc agentrc_pin.py selects), whose PR, job attempt and freshness lookups
hil_comment.yml runs and agentrc tests. check_build.py is stubbed: stubs prove the plumbing only; the workflows are
verified by a real CI run."""
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

G = W.comment_lib()   # a missing or too old agentrc fails here, naming the remedy

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
        self.assertEqual(outputs, {'boards': 'raspberry_pi_pico', 'matrix': '[{"board": "raspberry_pi_pico"}]'})

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
        self.assertEqual((code, s['eligible'], s['hil']), (0, True, {'boards': ['metro_m4_express', 'raspberry_pi_pico'],
                                                                   'full': False, 'uncovered': []}))
        sp.assert_not_called()
        code, s, outputs, _, err = self.select(event='workflow_dispatch', boards='nope')
        self.assertEqual((code, s), (2, None))
        self.assertIn('nope', err)


class Status(Tmp):
    def test_records_outcome_core_and_cli(self):
        versions = self.tmp / 'v.json'
        versions.write_text(json.dumps({'platform': 'rp2040:rp2040', 'cli': {'VersionString': '1.3.1'}, 'cores': {'platforms': [
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
        self.assertEqual(json.loads(out.read_text())['platform'], {'id': None, 'version': None})


class CommentWorkflow(unittest.TestCase):
    def test_the_comment_job_asks_for_the_rig_job_of_hil_yml(self):
        """job-attempt and fresh name hil.yml and its rig job, whose attempt names the summary artifact."""
        comment = (ROOT / '.github/workflows/hil_comment.yml').read_text()
        self.assertIn('--job hil-run', comment)
        self.assertIn('--workflow hil.yml', comment)
        self.assertIn('name: hil-run-${{ steps.rig.outputs.attempt }}', comment)
        hil = (ROOT / '.github/workflows/hil.yml').read_text()
        self.assertIn('\n  hil-run:\n', hil)
        self.assertIn('name: hil-run-${{ github.run_attempt }}', hil)


def board(name, verdict='pass', tests=(('cdc_echo', True),), **over):
    r = {'board': name, 'verdict': verdict, 'detail': '', 'flaky': False, 'retry': '', 'waitedS': 0, 'runS': 75,
         'tests': [{'test': t, 'pass': p, 'detail': f'{t} detail'} for t, p in tests],
         'cleanup': {'pass': True, 'state': 'restored', 'holder': None},
         'firmware': {'platform': {'id': 'rp2040:rp2040', 'version': '5.4.4', 'git': None}}}
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
        fw = {'platform': {'id': 'rp2040:rp2040', 'version': '5.4.5', 'git': None}}
        self.summary([board('raspberry_pi_pico', tests=[('cdc_echo', True), ('hid_inout', True)], firmware=fw)])
        text = self.render()
        self.assertTrue(text.startswith(W.TITLE))
        self.assertIn('**1** pass', text)
        row = next(ln for ln in text.splitlines() if ln.startswith('| `raspberry_pi_pico`'))
        self.assertEqual(row, '| `raspberry_pi_pico` | ✅ | ✅ pass | ✅ | ✅ | — | — | ✅ | 1m15s | '
                              '`rp2040:rp2040 5.4.5, non-git: id/version only` |')  # the firmware's outranks the status
        self.assertIn('[run 9 attempt 2](https://github.com/x/y/actions/runs/9)', text)
        self.summary([board('raspberry_pi_pico', tests=[('cdc_echo', True), ('hid_inout', True)], firmware=None)])
        self.assertIn(row.replace('5.4.5, non-git: id/version only', '5.4.4'), self.render())  # else the build status's

    def test_the_core_column_shows_the_records_core_provenance(self):
        plat = {'id': 'adafruit:samd', 'version': '1.7.17'}
        commit, content = 'a' * 40, 'c' * 64
        cases = [
            (None, '`adafruit:samd 1.7.17, non-git: id/version only`'),
            ({'commit': commit, 'dirty': False, 'content': content},
             f'`adafruit:samd 1.7.17, commit {"a" * 12}, content {"c" * 12}`'),
            ({'commit': commit, 'dirty': True, 'content': content},
             f'`adafruit:samd 1.7.17, commit {"a" * 12}, modified, content {"c" * 12}`'),
            ({'commit': commit, 'dirty': True, 'content': None, 'contentError': 'not | a `tree`'},
             f'`` adafruit:samd 1.7.17, commit {"a" * 12}, modified, content unavailable: not \\| a `tree` ``'),
            ({'commit': 7, 'dirty': 'yes', 'content': ['x']},
             '`adafruit:samd 1.7.17, commit ?, dirty ?, content unavailable`'),
            ('dirty', '`adafruit:samd 1.7.17, malformed git provenance`'),
        ]
        for git, cell in cases:
            with self.subTest(git=git):
                self.assertEqual(W.core_of(None, {'firmware': {'platform': {**plat, 'git': git}}}), cell)
        # a record always writes git; a build status's platform has id/version only
        self.assertEqual(W.core_of(None, {'firmware': {'platform': plat}}),
                         '`adafruit:samd 1.7.17, malformed git provenance`')
        self.assertEqual(W.core_of({'platform': {**plat, 'git': None}}, None), '`adafruit:samd 1.7.17`')

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

    def test_a_core_copy_that_drifted_is_noted_even_on_pass(self):
        self.selection(boards=('metro_m4_express', 'raspberry_pi_pico'))
        fw = {'platform': {'id': 'adafruit:samd', 'version': '1.7.17'},
              'driftedHeaders': ['src/arduino/Adafruit_TinyUSB_API.h', 7, 'src/arduino/Adafruit_USBD_CDC.h']}
        self.summary([board('metro_m4_express', firmware=fw), board('raspberry_pi_pico')])
        text = self.render()
        self.assertIn('**2** pass', text)
        self.assertIn('- `metro_m4_express`: the core\'s bundled copy of this library differs from this checkout in '
                      '`src/arduino/Adafruit_TinyUSB_API.h`, `src/arduino/Adafruit_USBD_CDC.h`; the result covers '
                      'that mix', text)
        self.assertNotIn('`raspberry_pi_pico`: the core', text)
        self.summary([board('metro_m4_express', firmware={'driftedHeaders': 'x'}), board('raspberry_pi_pico')])
        self.assertNotIn('bundled copy', self.render())

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
        (self.tmp / 'run' / 'summary.json').write_text('x' * (G.MAX_JSON + 1))
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

    def test_malformed_summary_fields_render_as_unknown(self):
        self.selection(boards=('metro_m4_express', 'raspberry_pi_pico'))
        self.write('status/hil-status-raspberry_pi_pico-2/raspberry_pi_pico.json',
                   {'board': 'raspberry_pi_pico', 'attempt': 2, 'outcome': []})
        pico = board('raspberry_pi_pico', verdict=[])
        pico['tests'] = 1
        self.summary([board([]), pico])
        text = self.render()
        self.assertIn('**2** not reported', text)
        self.assertIn('| `metro_m4_express` | ❔ | ❔ |', text)
        self.assertIn('| `raspberry_pi_pico` | ❔ | ❔ | — | — | — | — |', text)
        self.summary(1)
        self.assertIn('| `raspberry_pi_pico` | ❔ | ❔ |', self.render())
        self.selection(hil={'boards': 1, 'uncovered': 1}, build=[])
        self.assertIn('No farm board is reached by this change.', self.render())

    def test_a_non_finite_duration_renders_as_unknown(self):
        self.selection()
        for run_s in ('1e309', 'NaN'):
            self.summary([board('raspberry_pi_pico', runS='RUN_S')])
            p = self.tmp / 'run' / 'summary.json'
            p.write_text(p.read_text().replace('"RUN_S"', run_s))
            self.assertIn('| ✅ | — | `rp2040:rp2040 5.4.4, non-git: id/version only` |', self.render())

    def test_a_huge_report_is_truncated(self):
        self.selection(boards=tuple(f'b{i}' for i in range(400)))
        self.summary([board(f'b{i}', 'fail', detail='x' * 400) for i in range(400)])
        with mock.patch.object(G, 'cap', lambda text: text):
            full = self.render()
        self.assertEqual(full[G.MAX_COMMENT - 1:G.MAX_COMMENT + 1], 'xx')   # the limit falls inside a code span
        text = self.render()
        self.assertLessEqual(len(text), G.MAX_COMMENT)
        body, marker = text.split('\n…truncated', 1)
        self.assertIn('see the run', marker)
        for ln in body.splitlines():   # every line kept whole, so no code span is left open
            self.assertIn(ln, full.splitlines())


if __name__ == '__main__':
    unittest.main()
