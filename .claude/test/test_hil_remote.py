#!/usr/bin/env python3
"""hil_remote.py's staging, refusals and session command lines, over the pinned agentrc hil_remote_core with every
call that reaches a rig stubbed. Stubs prove the plumbing only; the transport is verified by a real run."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('hil_remote', SCRIPTS / 'hil_remote.py')
hr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hr)
hr.load_core()   # the pinned checkout: a missing pin fails here, naming the fetch

BOARD = 'raspberry_pi_pico'


def git(root, *a):
    subprocess.run(['git', '-C', str(root), *a], check=True, capture_output=True)


class Checkout(unittest.TestCase):
    """A fake library checkout: the staged harness files, the roster and one clean build record at HEAD."""

    def setUp(self):
        # a git hook's GIT_DIR/GIT_INDEX_FILE would point the fixture's git at the outer repo
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                              if not k.startswith('GIT_')}, clear=True))
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'lib'
        for f in (*hr.HARNESS, hr.ROSTER):
            (self.root / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, self.root / f)
        (self.root / '.gitignore').write_text('/build/\n/.hil-remote/\n')
        git(self.root, 'init', '-q')
        git(self.root, 'add', '-A')
        git(self.root, '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', 'commit', '-qm',
            'init')
        self.head = subprocess.run(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], capture_output=True,
                                   text=True).stdout.strip()
        self.agentrc = Path(self.tmp.name) / 'agentrc'
        (self.agentrc / 'skills/hil/scripts').mkdir(parents=True)
        (self.agentrc / 'skills/hil/scripts/hil_lock.py').write_text('lock')
        self.record()
        closure = ['skills/hil/scripts/hil_lock.py']
        for target, name, value in ((hr, 'ROOT', self.root), (hr, 'RUNS', self.root / '.hil-remote'),
                                    (hr.agentrc_pin, 'checkout', lambda: self.agentrc),
                                    (hr.agentrc_pin, 'closure', lambda root, scripts: closure)):
            self.stack.enter_context(mock.patch.object(target, name, value))
        self.staged = {}
        tree = hr.core.Tree('ci.lan', '/home/u/hil-remote/x/r1', '/home/u/hil-remote/x/r1.lease', 'tok', self.root)
        self.calls = []

        def stage_tree(t, src, dest='.'):
            for p in Path(src).rglob('*'):
                if p.is_file():
                    self.staged[p.relative_to(src).as_posix()] = p.read_bytes()
        def create_attempt(root, runs, *a):   # the real one writes its record under runs first
            Path(runs).mkdir(exist_ok=True)
            return tree
        for name, fn in (('create_attempt', create_attempt), ('load_attempt', lambda *a: tree),
                         ('stage_tree', stage_tree), ('verify_remote', lambda t, d, base='.': []),
                         ('launch', lambda t, argv, env=None, cwd='.': self.calls.append(argv) or {'pid': 9}),
                         ('run_remote', self.run_remote)):
            self.stack.enter_context(mock.patch.object(hr.core, name, fn))
        self.rig_exit = {}

    def run_remote(self, tree, argv, env=None, cwd='.'):
        self.calls.append(argv)
        return self.rig_exit.get(argv[3], 0)

    def tearDown(self):
        self.stack.close()
        self.tmp.cleanup()

    def record(self, **over):
        elf = self.root / f'build/hil/{BOARD}/serial_echo/serial_echo.ino.elf'
        elf.parent.mkdir(parents=True, exist_ok=True)
        elf.write_bytes(b'elf')
        rec = {'kind': 'arduino-hil-build', 'version': 1, 'board': BOARD, 'experimental': False,
               'source': {'head': self.head},
               'sketches': {'serial_echo': {'sha256': {elf.relative_to(self.root).as_posix():
                                                       hashlib.sha256(b'elf').hexdigest()}}}}
        rec.update(over)
        path = self.root / f'build/hil/{BOARD}.build.json'
        path.write_text(json.dumps(rec))
        return path

    def main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = hr.main(list(argv))
        lines = out.getvalue().strip().splitlines()
        return rc, json.loads(lines[-1]) if lines and lines[-1].startswith('{') else None


class Run(Checkout):
    def test_stages_a_manifest_pinned_tree_and_launches_the_battery(self):
        rc, out = self.main('run', '--run-id', 'r1', '-b', BOARD, '-t', 'cdc_echo')
        self.assertEqual((rc, out['boards'], out['launch']), (0, [BOARD], {'pid': 9}))
        manifest = json.loads(self.staged[hr.MANIFEST])
        self.assertEqual(manifest['head'], self.head)
        for f in (*hr.HARNESS, hr.ROSTER, f'build/hil/{BOARD}.build.json',
                  f'build/hil/{BOARD}/serial_echo/serial_echo.ino.elf', 'agentrc/skills/hil/scripts/hil_lock.py'):
            self.assertEqual(manifest['files'][f], hashlib.sha256(self.staged[f]).hexdigest(), f)
        self.assertEqual(self.calls[0], ['python3', '-u', hr.HIL, 'run', '--staged', hr.MANIFEST, '--run-dir',
                                         hr.RUN_DIR, '-b', BOARD, '-t', 'cdc_echo', hr.ROSTER])

    def test_refusals_come_before_the_rig(self):
        cases = ((lambda: self.record(source={'head': 'old'}), 'built at old'),
                 (lambda: self.record(experimental=True), 'experimental'),
                 (lambda: (self.root / f'build/hil/{BOARD}.build.json').unlink(), 'build record'),
                 (lambda: (self.root / hr.ROSTER).write_text((self.root / hr.ROSTER).read_text() + ' '),
                  'not clean'))
        for spoil, why in cases:
            with self.subTest(why=why):
                git(self.root, 'checkout', '-q', '--', '.')
                self.record()
                spoil()
                with self.assertRaisesRegex(SystemExit, why):
                    self.main('run', '--run-id', 'r2', '-b', BOARD)
                self.assertEqual(self.calls, [])

    def test_image_changed_after_the_record_is_refused(self):
        (self.root / f'build/hil/{BOARD}/serial_echo/serial_echo.ino.elf').write_bytes(b'other')
        with self.assertRaisesRegex(SystemExit, 'changed since the build record'):
            self.main('run', '--run-id', 'r3', '-b', BOARD)

    def test_unknown_board_refused(self):
        with self.assertRaisesRegex(SystemExit, 'not in'):
            self.main('run', '--run-id', 'r4', '-b', 'nope')


class Session(Checkout):
    def open(self):
        rc, out = self.main('session', 'open', '--run-id', 's1', '-b', BOARD)
        self.assertEqual((rc, out['held']), (0, True))
        return out

    def test_open_holds_the_board_under_the_session_reason(self):
        out = self.open()
        self.assertEqual(self.calls[-1], ['python3', '-u', hr.HIL, 'lock', 'hold', BOARD, '--reason', out['reason']])
        self.assertEqual(out['restoreRecord'], f'build/hil/{BOARD}.build.json')

    def test_exec_adds_the_session_options_and_keeps_argv_after_the_roster(self):
        out = self.open()
        self.assertEqual(hr.main(['session', 'exec', 's1', 'exec', '--timeout', '9', '--', 'ls', '-l']), 0)
        self.assertEqual(self.calls[-1], ['python3', '-u', hr.HIL, 'exec', '-b', BOARD, '--under-hold',
                                          out['reason'], '--staged', hr.MANIFEST, '--timeout', '9', hr.ROSTER,
                                          '--', 'ls', '-l'])
        hr.main(['session', 'exec', 's1', 'gdb', '--build-record', 'r.json', '--sketch', 'serial_echo',
                 '--ex', 'bt'])
        self.assertEqual(self.calls[-1][-7:], ['--build-record', 'r.json', '--sketch', 'serial_echo', '--ex', 'bt',
                                               hr.ROSTER])
        for bad in (['flash', '-b', 'x'], ['flash', '--under-hold=x'], ['exec'], ['lock', 'status']):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                hr.main(['session', 'exec', 's1', *bad])

    def test_close_is_one_restore_that_releases(self):
        out = self.open()
        self.rig_exit['restore'] = 1
        rc, closed = self.main('session', 'close', 's1')
        self.assertEqual((rc, closed['closed']), (1, False))
        self.assertTrue(json.loads((self.root / '.hil-remote/s1.session.json').read_text())['held'])
        self.rig_exit['restore'] = 0
        rc, closed = self.main('session', 'close', 's1')
        self.assertEqual((rc, closed['closed']), (0, True))
        self.assertEqual(self.calls[-1], ['python3', '-u', hr.HIL, 'restore', '-b', BOARD, '--under-hold',
                                          out['reason'], '--staged', hr.MANIFEST, '--bundle', hr.RESTORE_BUNDLE,
                                          '--build-record', out['restoreRecord'], '--release', hr.ROSTER])
        self.assertNotIn(['lock', 'release'], [c[3:5] for c in self.calls])

    def test_stage_puts_each_record_in_a_bundle_of_its_own(self):
        self.open()
        restore = f'{hr.RESTORE_BUNDLE}/build/hil/{BOARD}/serial_echo/serial_echo.ino.elf'
        self.assertEqual(self.staged[restore], b'elf')
        manifest = json.loads(self.staged[hr.MANIFEST])
        self.assertIn(restore, manifest['files'])
        rec = self.record(experimental=True)
        patch = rec.with_suffix('.patch')
        patch.write_text('diff')
        data = json.loads(rec.read_text())
        data['patch'] = {'path': patch.relative_to(self.root).as_posix(),
                         'sha256': hashlib.sha256(b'diff').hexdigest()}
        rec.write_text(json.dumps(data))
        self.staged.clear()
        rc, out = self.main('session', 'stage', 's1', str(rec))
        bundle = out['bundle']
        self.assertEqual(rc, 0)
        self.assertEqual(out['use'], [f'--bundle {bundle} --build-record build/hil/{BOARD}.build.json'])
        self.assertIn(f'{bundle}/build/hil/{BOARD}.build.patch', self.staged)
        self.assertFalse([p for p in self.staged if p.startswith(hr.RESTORE_BUNDLE) or p == hr.MANIFEST])
        rc, out = self.main('session', 'stage', 's1', str(rec))
        self.assertNotEqual(out['bundle'], bundle)
        patch.write_text('changed')
        with self.assertRaisesRegex(SystemExit, 'changed since the build record'):
            self.main('session', 'stage', 's1', str(rec))
        self.record(board='metro_m4_express')
        with self.assertRaisesRegex(SystemExit, 'not raspberry_pi_pico'):
            self.main('session', 'stage', 's1', str(rec))



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
        pin = hr.agentrc_pin
        self.stack.enter_context(mock.patch.object(pin, 'pin', lambda: (str(repo), self.commit)))
        self.dest = pin.cache_dir(self.commit)

    def tearDown(self):
        self.stack.close()

    def race(self, winner):
        """fetch() with `winner` put at the cache dir by a concurrent fetch while this one checks out."""
        pin, real = hr.agentrc_pin, hr.agentrc_pin._git

        def git_then_win(root, *args):
            p = real(root, *args)
            if args[0] == 'checkout':
                winner()
            return p
        with mock.patch.object(pin, '_git', git_then_win):
            return pin.fetch()

    def test_a_concurrent_first_fetch_reuses_the_winner(self):
        pin = hr.agentrc_pin
        won = self.tmp / 'won'
        shutil.copytree(pin.fetch(), won, symlinks=True)
        shutil.rmtree(self.dest)
        self.assertEqual(self.race(lambda: shutil.copytree(won, self.dest, symlinks=True)), self.dest)
        self.assertEqual(pin.problem(self.dest, self.commit), '')
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], [self.dest.name])

    def test_a_bad_winner_is_refused(self):
        def bad():
            self.dest.mkdir(parents=True)
            (self.dest / 'junk').write_text('x')
        with self.assertRaisesRegex(hr.agentrc_pin.PinError, 'not a git checkout'):
            self.race(bad)

if __name__ == '__main__':
    unittest.main()
