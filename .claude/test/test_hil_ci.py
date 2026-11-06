#!/usr/bin/env python3
"""hil_ci.py, a wrapper running agentrc's arduino_ci.py (the agentrc agentrc_pin.py selects) with hil_test.py's
profile, and the board-table facts its ci-arduino setup relies on. The setup, pack and rig mechanics are agentrc's
and tested there; nothing here installs a core or reaches a rig. Stubs prove the plumbing only; the workflow is
verified by a real CI run."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'))
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'build' / 'scripts'))
import agentrc_pin  # noqa: E402
import check_build  # noqa: E402
import hil_ci  # noqa: E402
import hil_test  # noqa: E402
import hil_workflow  # noqa: E402

hil_test.engine()   # the selected agentrc: a missing one fails here, naming the remedy


def vendor_arch(fqbn):
    return ':'.join(fqbn.split(':', 2)[:2])


class Wrapper(unittest.TestCase):
    def test_runs_the_ci_engine_with_the_hil_profile(self):
        ci = mock.Mock()
        ci.run.return_value = 1
        with mock.patch.object(agentrc_pin, 'load', return_value=ci) as load:
            self.assertEqual(hil_ci.main(['run', '--hosted', 'x']), 1)
        load.assert_called_once_with('skills/arduino/scripts/arduino_ci.py')
        ci.run.assert_called_once_with(hil_test, ['run', '--hosted', 'x'])

    def test_an_unavailable_agentrc_exits_2_naming_why_and_the_remedy(self):
        with mock.patch.object(hil_test, 'engine', side_effect=agentrc_pin.PinError('agentrc gone', 'install it')), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hil_ci.main(['run']), 2)
        self.assertEqual(json.loads(out.getvalue()), {'pass': False, 'error': 'agentrc gone', 'remedy': 'install it'})

    def test_the_real_engine_refuses_a_board_with_no_ci_platform(self):
        with tempfile.TemporaryDirectory() as ci, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(hil_ci.main(['setup', '--board', 'nrf52840', '--ci', ci]), 2)
        self.assertIn('not a rig board', json.loads(out.getvalue().strip().splitlines()[-1])['error'])


class Tables(unittest.TestCase):
    def test_each_rig_board_builds_on_the_core_its_ci_platform_installs(self):
        """setup installs the core ci-arduino installs for the board's CI platform (`filters`), whose FQBN the
        board table mirrors from all_platforms.py: a rig board on another core would build with none installed."""
        roster = [b['name'] for b in json.loads((ROOT / 'test/hil/ci.json').read_text())['boards']]
        for name in roster:
            board = check_build.BOARDS[name]
            self.assertEqual(vendor_arch(board['fqbn']), vendor_arch(check_build.BOARDS[board['filters']]['fqbn']),
                             name)

    def test_the_pr_report_names_the_contracts_roster_tests_and_sketches(self):
        """hil_workflow.py renders without agentrc, so it keeps its own copies of these."""
        self.assertEqual(hil_workflow.ROSTER, hil_test.REMOTE_ROSTER)
        self.assertEqual(list(hil_workflow.TESTS), list(hil_test.TESTS))
        for sketches in check_build.HIL_SKETCHES.values():
            for sketch in sketches:
                self.assertIn(f'`{Path(sketch).name}`', hil_workflow.COVERAGE)


if __name__ == '__main__':
    unittest.main()
