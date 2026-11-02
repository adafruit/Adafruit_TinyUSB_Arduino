#!/usr/bin/env python3
"""hil_remote.py, a wrapper running the pinned agentrc arduino_remote.py with hil_test.py's profile, and agentrc_pin.py,
which fetches and loads that pin. The staging and session mechanics are agentrc's and tested there; nothing here
reaches a rig. Stubs prove the plumbing only; the transport is verified by a real run."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'))
import agentrc_pin  # noqa: E402
import hil_remote  # noqa: E402
import hil_test  # noqa: E402

hil_test.engine()   # the pinned checkout: a missing pin fails here, naming the fetch


def git(root, *a):
    subprocess.run(['git', '-C', str(root), *a], check=True, capture_output=True)


class Wrapper(unittest.TestCase):
    def test_runs_the_pinned_remote_engine_with_the_hil_profile(self):
        remote = mock.Mock()
        remote.run.return_value = 3
        with mock.patch.object(agentrc_pin, 'load', return_value=remote) as load:
            self.assertEqual(hil_remote.main(['wait', 'r1']), 3)
        load.assert_called_once_with('skills/arduino/scripts/arduino_remote.py')
        remote.run.assert_called_once_with(hil_test, ['wait', 'r1'])

    def test_a_missing_pin_exits_2_naming_why(self):
        with mock.patch.object(hil_test, 'engine', side_effect=agentrc_pin.PinError('agentrc pin: gone')), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(hil_remote.main(['wait', 'r1']), 2)
        self.assertEqual(err.getvalue(), 'error: agentrc pin: gone\n')

    def test_the_real_engine_parses_the_profiles_tests(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as e:
            hil_remote.main(['run', '--run-id', 'r1', '-t', 'nope'])
        self.assertEqual(e.exception.code, 2)
        # newer argparse quotes each choice
        self.assertIn(', '.join(sorted(hil_test.TESTS)), err.getvalue().replace("'", ''))

    def test_the_rig_gets_every_contract_file(self):
        for f in hil_test.BUILD.CONTRACT_FILES:
            self.assertTrue((ROOT / f).is_file(), f)
        self.assertIn('.claude/skills/hil-contract/scripts/hil_remote.py', hil_test.BUILD.CONTRACT_FILES)
        self.assertIn('.claude/skills/hil-contract/scripts/agentrc_pin.py', hil_test.BUILD.CONTRACT_FILES)


class Pin(unittest.TestCase):
    def test_identity_is_the_pin_and_the_engines_closure(self):
        ident = agentrc_pin.identity()
        self.assertEqual(ident['commit'], agentrc_pin.pin()[1])
        self.assertEqual(ident['root'], agentrc_pin.cache_dir(ident['commit']))
        self.assertLessEqual({*agentrc_pin.ENGINES, 'skills/hil/scripts/hil_load.py',
                              'skills/hil/scripts/hil_remote_core.py'}, set(ident['files']))
        self.assertEqual(hil_test.BUILD.AGENTRC, ident)

    def test_a_staged_tree_loads_its_own_copy(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(agentrc_pin, 'ROOT', Path(d)), \
                mock.patch.object(agentrc_pin, 'checkout', side_effect=AssertionError('a rig has no cache')):
            self.assertTrue(agentrc_pin.staged())
            self.assertEqual(agentrc_pin.root(), Path(d) / 'agentrc')
        self.assertFalse(agentrc_pin.staged())

    def test_a_staged_tree_without_the_engines_is_a_pin_error(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(agentrc_pin, 'ROOT', Path(d)), \
                self.assertRaisesRegex(agentrc_pin.PinError, 'lacks'):
            agentrc_pin.load('skills/arduino/scripts/arduino_build.py')

    def test_closure_reads_files_without_importing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'a.py').write_text("import nothing_here\nFILES = ('b.py',)\n")
            (Path(d) / 'b.py').write_text('')
            self.assertEqual(agentrc_pin.closure(d, ['a.py']), ['a.py', 'b.py'])
            (Path(d) / 'b.py').unlink()
            with self.assertRaisesRegex(agentrc_pin.PinError, r"lacks \['b.py'\]"):
                agentrc_pin.closure(d, ['a.py'])


class PinFetch(unittest.TestCase):
    """agentrc_pin.fetch against a local repository standing in for the pinned agentrc."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.tmp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(mock.patch.dict(os.environ, {
            **{k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, 'XDG_CACHE_HOME': str(self.tmp)},
            clear=True))
        repo = self.tmp / 'upstream'
        repo.mkdir()
        git(repo, 'init', '-q')
        (repo / 'f').write_text('x')
        git(repo, 'add', '-A')
        git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'pin')
        self.commit = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                                     text=True).stdout.strip()
        self.stack.enter_context(mock.patch.object(agentrc_pin, 'pin', lambda: (str(repo), self.commit)))
        self.dest = agentrc_pin.cache_dir(self.commit)

    def tearDown(self):
        self.stack.close()

    def race(self, winner):
        """fetch() with `winner` put at the cache dir by a concurrent fetch while this one checks out."""
        real = agentrc_pin._git

        def git_then_win(root, *args):
            p = real(root, *args)
            if args[0] == 'checkout':
                winner()
            return p
        with mock.patch.object(agentrc_pin, '_git', git_then_win):
            return agentrc_pin.fetch()

    def test_a_concurrent_first_fetch_reuses_the_winner(self):
        won = self.tmp / 'won'
        shutil.copytree(agentrc_pin.fetch(), won, symlinks=True)
        shutil.rmtree(self.dest)
        self.assertEqual(self.race(lambda: shutil.copytree(won, self.dest, symlinks=True)), self.dest)
        self.assertEqual(agentrc_pin.problem(self.dest, self.commit), '')
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], [self.dest.name])

    def test_a_bad_winner_is_refused(self):
        def bad():
            self.dest.mkdir(parents=True)
            (self.dest / 'junk').write_text('x')
        with self.assertRaisesRegex(agentrc_pin.PinError, 'not a git checkout'):
            self.race(bad)

    def test_a_moved_or_modified_cache_is_refused(self):
        agentrc_pin.fetch()
        self.assertEqual(agentrc_pin.checkout(), self.dest)
        (self.dest / 'f').write_text('edited')
        with self.assertRaisesRegex(agentrc_pin.PinError, 'local changes.*agentrc_pin.py fetch'):
            agentrc_pin.checkout()
        self.assertIn('is not a git checkout', agentrc_pin.problem(self.dest.parent, self.commit))
        self.assertIn(f'not the pin {"0" * 40}', agentrc_pin.problem(self.dest, '0' * 40))


if __name__ == '__main__':
    unittest.main()
