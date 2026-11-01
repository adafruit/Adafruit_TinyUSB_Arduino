#!/usr/bin/env python3
"""The HIL harness: record admission, the battery's lock and cleanup paths, verdicts and the report, with agentrc's
lock, flash and USB modules stubbed. Stubs prove the plumbing only; hardware behaviour is a real run's."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts' / 'hil_test.py'
spec = importlib.util.spec_from_file_location('hil_test', SCRIPT)
ht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ht)


class FakeLock:
    class BoardLocked(Exception):
        pass

    class RecoveryBlocked(Exception):
        pass

    class LockUnavailable(Exception):
        pass

    class PendingRefused(Exception):
        pass

    def __init__(self):
        self.taken, self.released, self.handed, self.hand_fails = [], [], [], False
        self.records, self.pending = {}, {}
        lock = self

        class PendingSessions:
            def __init__(self, name, kind):
                self.key = (name, kind)

            def add(self, sessions):
                lock.pending.setdefault(self.key, []).extend(sessions)

            def clear(self, ended):
                left = [s for s in lock.pending.get(self.key, []) if not ended(s)]
                if left:
                    raise FakeLock.PendingRefused(f'{left} not ended')
                lock.pending.pop(self.key, None)
        self.PendingSessions = PendingSessions

    def take_board_lock(self, board, reason, protected=False, **extra):
        self.taken.append((board, reason, protected, extra))
        return mock.Mock(name=f'fh-{board}')

    def release_board_lock(self, fh):
        self.released.append(fh)

    def hand_to_cleanup_holder(self, handles, reason, detail, receipts=()):
        if self.hand_fails:
            raise self.LockUnavailable('no holder')
        self.handed.append((list(handles), detail, receipts))
        return 'tok123'

    def read_record(self, board):
        return self.records.get(board)

    def is_locked(self, board):
        return board in self.records


def body_schema(**kw):
    return argparse.Namespace(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lock = FakeLock()
        ht.A = argparse.Namespace(lock=self.lock, flash=mock.Mock(), process=mock.Mock(), report=mock.Mock(),
                                  usb=mock.Mock())
        self.roster = self.dir / 'ci.json'
        self.roster.write_text(json.dumps({'boards': [
            {'name': 'metro_m4_express', 'uid': 'ABC', 'flasher': {'name': 'jlink', 'uid': '1', 'args': '-device X'},
             'arduino': {'tests': ['cdc_echo']}}]}))

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, **over):
        """A record whose artifacts live under a fake checkout root."""
        root = self.dir / 'root'
        elf = root / 'build/hil/metro_m4_express/serial_echo/serial_echo.ino.elf'
        elf.parent.mkdir(parents=True, exist_ok=True)
        elf.write_bytes(b'elf')
        rec = {'kind': 'arduino-hil-build', 'version': 1, 'board': 'metro_m4_express', 'experimental': False,
               'fqbn': 'f', 'platform': {}, 'source': {'head': 'abc', 'dirty': False},
               'roster': {'sha256': hashlib.sha256(self.roster.read_bytes()).hexdigest()},
               'sketches': {'serial_echo': {'example': 'examples/CDC/serial_echo', 'vidPid': '239a:8020',
                                            'elf': 'build/hil/metro_m4_express/serial_echo/serial_echo.ino.elf',
                                            'hex': None, 'image': 'hex', 'sha256': {
                                                'build/hil/metro_m4_express/serial_echo/serial_echo.ino.elf':
                                                    hashlib.sha256(b'elf').hexdigest()}}}}
        rec.update(over)
        path = self.dir / 'rec.json'
        path.write_text(json.dumps(rec))
        return path, root


class Tables(unittest.TestCase):
    def test_tests_use_recorded_sketches(self):
        names = {Path(e).name for e in ht.check_build.HIL_SKETCHES}
        self.assertLessEqual(set(ht.TESTS.values()) | {ht.RESTORE_SKETCH}, names)
        self.assertEqual(set(ht.TESTS), set(ht.TEST_FUNCS))

    def test_msc_fixture_matches_the_sketch(self):
        sketch = ROOT / 'examples/MassStorage/msc_ramdisk'
        text = (sketch / 'msc_ramdisk.ino').read_text() + (sketch / 'ramdisk.h').read_text()
        num = int(re.search(r'DISK_BLOCK_NUM\s*=?\s*(\d+)', text).group(1))
        size = int(re.search(r'DISK_BLOCK_SIZE\s*=?\s*(\d+)', text).group(1))
        readme = re.search(r'README_CONTENTS\s*\\?\s*"([^"]*)"', text).group(1)
        self.assertEqual((ht.MSC_DISK_BYTES, ht.MSC_README), (num * size, readme.encode()))

    def test_roster_is_valid_and_names_rig_boards(self):
        boards = ht.load_roster(ROOT / 'test/hil/ci.json')
        self.assertEqual(set(boards), {'raspberry_pi_pico', 'adafruit_fruit_jam', 'metro_m4_express'})
        for b in boards.values():
            self.assertIn('openocd', b['debug'])

    def test_budget_reserve_covers_the_restore(self):
        self.assertGreaterEqual(ht.CLEANUP_RESERVE, ht.FLASH_TIMEOUT + ht.VERIFY_TIMEOUT + ht.ENUM_TIMEOUT +
                                ht.TEST_TIMEOUT)


class Fat12(unittest.TestCase):
    def test_reads_the_sketch_image_layout(self):
        img = bytearray(ht.MSC_DISK_BYTES)
        img[11:13], img[13], img[14:16], img[16] = (512).to_bytes(2, 'little'), 1, (1).to_bytes(2, 'little'), 1
        img[17:19], img[22:24] = (16).to_bytes(2, 'little'), (1).to_bytes(2, 'little')
        entry = b'README  TXT' + bytes(15) + (2).to_bytes(2, 'little') + (5).to_bytes(4, 'little')
        img[1024:1056] = entry
        img[1024 + 512:1024 + 517] = b'hello'
        self.assertEqual(ht.fat12_file(bytes(img), b'README  TXT'), b'hello')
        self.assertIsNone(ht.fat12_file(bytes(img), b'OTHER   TXT'))


class Admission(Base):
    def admit(self, rec, root, **kw):
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'git_identity', return_value=('abc', '')):
            return ht.admit(rec, 'metro_m4_express', self.roster, self.dir / 'run', **kw)

    def test_good_record_copies_images(self):
        rec, root = self.record()
        _, images = self.admit(rec, root)
        self.assertEqual(Path(images['serial_echo']['elf']).read_bytes(), b'elf')
        self.assertTrue(images['serial_echo']['elf'].startswith(str(self.dir / 'run')))

    def test_refusals(self):
        rec, root = self.record()
        (root / 'build/hil/metro_m4_express/serial_echo/serial_echo.ino.elf').write_bytes(b'tampered')
        with self.assertRaisesRegex(ht.Refused, 'changed since the record'):
            self.admit(rec, root)
        for over, why in (({'board': 'raspberry_pi_pico'}, 'not metro_m4_express'),
                          ({'experimental': True}, 'experimental'),
                          ({'source': {'head': 'old'}}, 'rebuild'),
                          ({'roster': {'sha256': '0'}}, 'changed since'),
                          ({'kind': 'other'}, 'not a version')):
            rec, root = self.record(**over)
            with self.assertRaisesRegex(ht.Refused, why):
                self.admit(rec, root)

    def test_image_must_match_the_flasher(self):
        rec, root = self.record()
        data = json.loads(rec.read_text())
        data['sketches']['serial_echo']['image'] = 'elf'
        rec.write_text(json.dumps(data))
        with self.assertRaisesRegex(ht.Refused, 'flashes the hex'):
            self.admit(rec, root)

    def test_dirty_checkout_refused(self):
        rec, root = self.record()
        with mock.patch.object(ht, 'ROOT', root), \
                mock.patch.object(ht, 'git_identity', return_value=('abc', ' M src/tusb.c')):
            with self.assertRaisesRegex(ht.Refused, 'changes since'):
                ht.admit(rec, 'metro_m4_express', self.roster, self.dir / 'run')

    def test_experimental_allowed_for_flash(self):
        rec, root = self.record(experimental=True, source={'head': 'other'})
        self.admit(rec, root, experimental_ok=True)

    def test_staged_head_must_match_manifest(self):
        rec, root = self.record()
        with self.assertRaisesRegex(ht.Refused, 'staged tree'):
            self.admit(rec, root, manifest={'head': 'zzz'})
        self.admit(rec, root, manifest={'head': 'abc'})

    def test_staged_manifest_digest_mismatch(self):
        root = self.dir / 'tree'
        (root / 'a').mkdir(parents=True)
        (root / 'a/f').write_text('x')
        m = self.dir / 'manifest.json'
        m.write_text(json.dumps({'head': 'abc', 'files': {'a/f': hashlib.sha256(b'y').hexdigest()}}))
        with mock.patch.object(ht, 'ROOT', root), self.assertRaisesRegex(ht.Refused, 'differ from the manifest'):
            ht.verify_staged(m)


class Battery(Base):
    def run_board(self, step=None, restore=None, admit=None, probe=''):
        rec, root = self.record()
        board = ht.load_roster(self.roster)['metro_m4_express']
        with mock.patch.object(ht, 'probe_present', return_value=probe), mock.patch.object(ht, 'admit', admit or (lambda *a, **k: (json.loads(rec.read_text()),
                                                                        {'serial_echo': {'sha256': {}}}))), \
                mock.patch.object(ht, 'step', step or (lambda *a: {'test': 'cdc_echo', 'pass': True,
                                                                    'detail': 'ok'})), \
                mock.patch.object(ht, 'restore', restore or (lambda *a: {'pass': True, 'state': 'serial_echo restored',
                                                                         'evidence': 'ok'})), \
                mock.patch.object(ht.Tracker, '__init__', lambda s, d: s.__dict__.update(
                    receipts=['r1'], pending=[], unclean='', log=Path(d) / 'hil.log')):
            return ht.run_board('metro_m4_express', board, ['cdc_echo'], rec, self.roster, self.dir / 'run', None)

    def test_pass_releases_the_protected_lock(self):
        row = self.run_board()
        self.assertTrue(row['pass'])
        self.assertEqual(ht.verdict(row), 'pass')
        self.assertTrue(self.lock.taken[0][2])
        self.assertEqual(self.lock.taken[0][3]['locks'], ['metro_m4_express'])
        self.assertEqual(len(self.lock.released), 1)

    def test_failed_test_is_a_fail_after_cleanup(self):
        row = self.run_board(step=lambda *a: {'test': 'cdc_echo', 'pass': False, 'detail': 'no echo'})
        self.assertEqual((ht.verdict(row), len(self.lock.released)), ('fail', 1))
        self.assertEqual(ht.exit_code([row]), 1)

    def test_surviving_child_hands_the_board_to_a_cleanup_holder(self):
        def step(*a):
            raise ht.Unclean('openocd: cleanup pending')
        row = self.run_board(step=step)
        self.assertEqual(ht.verdict(row), 'blocker')
        self.assertEqual(row['cleanup']['state'], 'pending')
        self.assertEqual(row['cleanup']['holder'], {'token': 'tok123'})
        self.assertEqual(self.lock.handed[0][2], ('r1',))
        self.assertEqual(self.lock.released, [])
        self.assertEqual(ht.exit_code([row]), 4)

    def test_failed_restore_keeps_the_board(self):
        row = self.run_board(restore=lambda *a: {'pass': False, 'state': 'unknown', 'evidence': 'no echo'})
        self.assertEqual((ht.verdict(row), row['cleanup']['holder']), ('blocker', {'token': 'tok123'}))

    def test_failed_hand_over_leaves_the_protected_record(self):
        self.lock.hand_fails = True
        row = self.run_board(restore=lambda *a: {'pass': False, 'state': 'unknown', 'evidence': 'x'})
        self.assertIsNone(row['cleanup']['holder']['token'])
        self.assertIn('protected record left', row['cleanup']['holder']['error'])
        self.assertEqual(ht.exit_code([row]), 4)

    def test_harness_error_is_a_blocker(self):
        def step(*a):
            raise RuntimeError('bug')
        row = self.run_board(step=step)
        self.assertEqual((ht.verdict(row), row['cleanup']['state']), ('blocker', 'unknown'))

    def test_refused_record_is_not_run_and_untouched(self):
        def admit(*a, **k):
            raise ht.Refused('stale')
        row = self.run_board(admit=admit)
        self.assertEqual((ht.verdict(row), row['cleanup']['state'], len(self.lock.released)), ('not-run', 'untouched', 1))

    def test_pending_children_of_an_earlier_run_block_the_board(self):
        self.lock.pending[('metro_m4_express', ht.PENDING_KIND)] = [{'receipt': {'pid': 9}}]
        ht.A.process.recheck.return_value = 'pending'
        row = self.run_board()
        self.assertEqual((ht.verdict(row), row['recoveryBlocker'], len(self.lock.released)), ('blocker', True, 1))
        ht.A.process.recheck.return_value = 'done'
        row = self.run_board()
        self.assertEqual(ht.verdict(row), 'pass')

    def test_interrupt_keeps_the_row_and_its_holder(self):
        def step(*a):
            raise KeyboardInterrupt
        row = self.run_board(step=step)
        self.assertTrue(row['interrupted'])
        self.assertEqual(row['cleanup']['holder'], {'token': 'tok123'})

    def test_absent_probe_is_not_run_before_the_lock(self):
        row = self.run_board(probe='probe 1 is not on this host\'s bus')
        self.assertEqual((ht.verdict(row), self.lock.taken), ('not-run', []))

    def test_locked_and_blocked_boards(self):
        def locked(*a, **k):
            raise FakeLock.BoardLocked('held by ci')
        self.lock.take_board_lock = locked
        row = self.run_board()
        self.assertEqual((row['locked'], ht.verdict(row)), (True, 'not-run'))

        def blocked(*a, **k):
            raise FakeLock.RecoveryBlocked('cleanup holder')
        self.lock.take_board_lock = blocked
        row = self.run_board()
        self.assertEqual((row['recoveryBlocker'], ht.verdict(row)), (True, 'blocker'))

    def test_budget_stops_starting_tests(self):
        with mock.patch.object(ht, 'BOARD_BUDGET', ht.STEP_MAX):
            row = self.run_board()
        self.assertFalse(row['ran'])
        self.assertIn('budget', row['tests'][0]['detail'])


class Probe(Base):
    def test_jlink_padded_serial_and_openocd_pin(self):
        usb = ht.A.usb
        usb.jlink_serial_candidates = lambda uid, serials: [k for k, v in serials.items() if v.lstrip('0') == uid]
        usb.usb_scan.return_value = [{'busport': '11-2.4', 'serial': '000000123456'}]
        self.assertEqual(ht.probe_present({'name': 'jlink', 'uid': '123456'}), '')
        usb.usb_scan.return_value = []
        self.assertIn('not on this host', ht.probe_present({'name': 'jlink', 'uid': '123456'}))
        usb.usb_scan.return_value = [{'busport': '21-1.4', 'serial': 'E6614103E72C1D2F'}]
        self.assertEqual(ht.probe_present({'name': 'openocd', 'uid': 'e6614103e72c1d2f', 'vid_pid': '0x2e8a 0x000c'}),
                         '')
        self.assertEqual(usb.usb_scan.call_args[1]['vid_pid'], ('2e8a', '000c'))


class Manual(Base):
    def test_unclean_manual_flash_marks_the_board_pending(self):
        for exc in (ht.Unclean('JLinkExe: cleanup pending'), RuntimeError('boom'), KeyboardInterrupt()):
            with self.subTest(exc=type(exc).__name__):
                self.lock.pending.clear()
                self.check_marks_pending(exc)

    def check_marks_pending(self, exc):
        rec, root = self.record(experimental=True)
        self.lock.records['metro_m4_express'] = {'reason': 'mine', 'pid': 1}
        args = argparse.Namespace(cmd='flash', board='metro_m4_express', under_hold='mine', build_record=str(rec),
                                  roster=str(self.roster), staged=None, sketch='serial_echo', bundle=None)

        def unclean(*a):
            t = a[-1]
            t.pending.append({'receipt': {'pid': 7}})
            raise exc
        out = io.StringIO()
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'load_agentrc'), \
                mock.patch.object(ht, 'flash_only', unclean), contextlib.redirect_stdout(out), \
                mock.patch.object(ht.Tracker, '__init__', lambda s, d: s.__dict__.update(
                    receipts=[], pending=[], unclean='', log=Path(d) / 'hil.log')):
            self.assertEqual(ht.manual(args), 4)
            ht.A.process.recheck.return_value = 'pending'
            with self.assertRaisesRegex(ht.Refused, 'pending children'):
                ht.manual(args)
            ht.A.process.recheck.return_value = 'done'
        self.assertEqual(self.lock.pending[('metro_m4_express', ht.PENDING_KIND)], [{'receipt': {'pid': 7}}])

    def test_staged_flash_admits_against_the_manifest_and_keeps_a_result(self):
        rec, root = self.record()
        self.lock.records['metro_m4_express'] = {'reason': 'mine', 'pid': 1}
        args = argparse.Namespace(cmd='flash', board='metro_m4_express', under_hold='mine', build_record=str(rec),
                                  roster=str(self.roster), staged='manifest.json', sketch='serial_echo',
                                  bundle=None)
        flashed = mock.Mock(return_value={'pass': True, 'detail': 'flashed'})
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'load_agentrc') as load, \
                mock.patch.object(ht, 'verify_staged', return_value={'head': 'abc', 'files': {}}), \
                mock.patch.object(ht, 'git_identity', side_effect=AssertionError('a staged tree has no git')), \
                mock.patch.object(ht, 'flash_only', flashed), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(ht.Tracker, '__init__', lambda s, d: s.__dict__.update(
                    receipts=[], pending=[], unclean='', log=Path(d) / 'hil.log')):
            self.assertEqual(ht.manual(args), 0)
        load.assert_called_once_with(True)
        result = json.loads(next((root / 'build/hil-manual').glob('metro_m4_express-flash-*/result.json')).read_text())
        self.assertEqual((result['pass'], result['record'], result['experimental']), (True, str(rec), False))

    def held(self, root, **kw):
        self.lock.records['metro_m4_express'] = {'reason': 'mine', 'pid': 1}
        return argparse.Namespace(**{'board': 'metro_m4_express', 'under_hold': 'mine', 'roster': str(self.roster),
                                     'staged': None, 'bundle': None, **kw})

    def test_exec_with_a_surviving_child_is_unclean_not_a_pass(self):
        rec, root = self.record()
        args = self.held(root, cmd='exec', argv=['sleep', '9'], timeout=5)
        ht.A.process.run_tracked.return_value = mock.Mock(returncode=0, stdout='', stderr='', cleanup='pending',
                                                          receipt={'pid': 5})
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'load_agentrc'), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ht.manual(args), 4)
        self.assertEqual(json.loads(out.getvalue())['survivors'], [{'receipt': {'pid': 5}}])
        self.assertEqual(self.lock.pending[('metro_m4_express', ht.PENDING_KIND)], [{'receipt': {'pid': 5}}])

    def test_one_session_command_at_a_time(self):
        rec, root = self.record()
        args = self.held(root, cmd='exec', argv=['true'], timeout=5)
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'load_agentrc'):
            with ht.one_session_command('metro_m4_express'):
                with self.assertRaisesRegex(ht.Refused, 'still running'):
                    ht.manual(args)
            with ht.one_session_command('metro_m4_express'):
                pass

    def test_bundle_holds_the_record_and_its_images(self):
        rec, root = self.record()
        bundle = root / 'bundles/1'
        shutil.copytree(root / 'build', bundle / 'build')
        (root / 'build/hil/metro_m4_express/serial_echo/serial_echo.ino.elf').write_bytes(b'overwritten')
        shutil.copy2(rec, bundle / 'rec.json')
        args = self.held(root, build_record='rec.json', bundle='bundles/1')
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'git_identity', return_value=('abc', '')):
            _, image, _ = ht.admit_sketch(args, 'serial_echo', self.dir / 'run', None, False)
            self.assertEqual(Path(image['elf']).read_bytes(), b'elf')
            args.bundle = '../root/../..'
            with self.assertRaisesRegex(ht.Refused, 'not a directory inside'):
                ht.admit_sketch(args, 'serial_echo', self.dir / 'run', None, False)

    def test_restore_release_passes_only_when_the_hold_ends(self):
        rec, root = self.record()
        args = self.held(root, build_record=str(rec), report=None, release=True)
        restored = {'pass': True, 'state': 'serial_echo restored', 'evidence': 'echo'}
        for exit_code, ok in ((0, True), (1, False)):
            with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'restore', return_value=restored), \
                    mock.patch.object(ht, 'git_identity', return_value=('abc', '')), \
                    mock.patch.object(ht, 'release_hold', return_value={'exit': exit_code}) as release:
                row = ht.op_restore(args, None, self.dir / 'run', None, None)
            release.assert_called_once_with('metro_m4_express')
            self.assertEqual(row['pass'], ok)

    def test_exec_takes_the_argv_after_the_roster(self):
        a = ht.parse(['exec', '-b', 'metro_m4_express', '--under-hold', 'r', 'ci.json', '--', 'ls', '-l'])
        self.assertEqual((a.roster, a.argv, a.timeout, a.staged), ('ci.json', ['ls', '-l'], 60, None))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ht.main(['exec', '-b', 'b', '--under-hold', 'r', '--timeout', '601', 'ci.json', 'true']),
                             2)
        self.assertIn('--timeout', out.getvalue())

    def test_gdb_runs_openocd_on_a_pipe_pinned_to_the_probe(self):
        ht.A.flash.openocd_cmd_base = lambda p: [
            'openocd', '-c', 'gdb_port disabled', '-c', f'adapter serial {p["uid"]}',
            *(['-c', f'adapter usb vid_pid {p["vid_pid"]}'] if 'vid_pid' in p else []), *ht.shlex.split(p['args'])]
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico']
        argv = ht.gdb_argv(board, 'fw.elf', ['info symbol main', 'bt'])
        self.assertEqual(argv[:4], ['gdb-multiarch', '-batch', '-nx', '-ex'])
        self.assertEqual((argv[-5:], argv[-1]), (['-ex', 'info symbol main', '-ex', 'bt', 'fw.elf'], 'fw.elf'))
        pipe = ht.shlex.split(argv[4].removeprefix('target extended-remote | '))
        self.assertEqual(pipe[-6:], ['-c', 'gdb_port pipe', '-c', 'gdb_memory_map disable',
                                     '-c', 'gdb_breakpoint_override hard'])
        self.assertIn('adapter serial E6614103E72C1D2F', pipe)
        self.assertIn('adapter usb vid_pid 0x2e8a 0x000c', pipe)
        self.assertIn('target/rp2040.cfg', pipe)
        metro = ht.load_roster(ROOT / 'test/hil/ci.json')['metro_m4_express']
        pipe = ht.shlex.split(ht.gdb_argv(metro, 'fw.elf', [])[4].removeprefix('target extended-remote | '))
        self.assertIn('interface/jlink.cfg', pipe)
        self.assertFalse(any('vid_pid' in w for w in pipe))

    def test_foreign_or_missing_hold_refused(self):
        with self.assertRaisesRegex(ht.Refused, 'not held'):
            ht.held_by('metro_m4_express', 'mine')
        self.lock.records['metro_m4_express'] = {'reason': 'someone else', 'pid': 1}
        with self.assertRaisesRegex(ht.Refused, 'not held'):
            ht.held_by('metro_m4_express', 'mine')
        self.lock.records['metro_m4_express'] = {'reason': 'mine', 'pid': 1}
        ht.held_by('metro_m4_express', 'mine')


class Flash(Base):
    def test_openocd_transcript_must_show_verify(self):
        op = mock.Mock(returncode=0, stdout='** Programming Finished **\n', stderr='', cleanup='done')
        ht.A.flash.flash_openocd.return_value = op
        tracker = mock.Mock()
        why = ht.flash({'name': 'openocd', 'verify': True}, {'elf': 'x.elf'}, self.dir, tracker)
        self.assertIn('Verified OK', why)
        op.stdout += '** Verified OK **\n'
        self.assertEqual(ht.flash({'name': 'openocd', 'verify': True}, {'elf': 'x.elf'}, self.dir, tracker), '')

    def test_jlink_verifies_every_hex_run(self):
        hexfile = self.dir / 'a.hex'
        def rec(addr, data):
            r = bytes([len(data), addr >> 8, addr & 0xff, 0]) + data
            return ':' + (r + bytes([(-sum(r)) & 0xff])).hex().upper()
        hexfile.write_text('\n'.join([rec(0x4000, b'\x01\x02\x03\x04'), rec(0x5000, b'\x05\x06\x07\x08'),
                                     ':00000001FF']) + '\n')
        ht.A.flash.jlink_problem.return_value = ''
        why = ht.flash({'name': 'jlink', 'args': '-device ATSAMD51J19'}, {'hex': str(hexfile)}, self.dir, mock.Mock())
        self.assertEqual(why, '')
        cmds = ht.A.flash.run_jlink_commands.call_args[0][1]
        self.assertEqual([c.split(',')[1] for c in cmds[:2]], ['0x4000', '0x5000'])
        self.assertEqual(cmds[2:], ['r', 'go'])
        self.assertEqual(ht.A.flash.run_jlink_commands.call_args[1]['device'], 'ATSAMD51J19')

    def test_detach_only_where_the_family_needs_it(self):
        ht.A.flash.run_openocd_commands.return_value = mock.Mock(returncode=0, stdout='', stderr='')
        ht.A.usb.usb_scan.return_value = []
        pico = {'uid': 'E66', 'flasher': {'name': 'openocd'}}
        self.assertEqual(ht.detach('raspberry_pi_pico', pico, '239a:cafe', mock.Mock()), 'detached')
        self.assertEqual(ht.A.flash.run_openocd_commands.call_args[0][1], ['mww 0x5011304c 0x00010000'])
        ht.A.flash.run_openocd_commands.reset_mock()
        metro = {'uid': 'ABC', 'flasher': {'name': 'jlink'}}
        self.assertEqual(ht.detach('metro_m4_express', metro, '239a:8020', mock.Mock()), '')
        ht.A.flash.run_openocd_commands.assert_not_called()

    def test_tracker_marks_unclean(self):
        t = ht.Tracker.__new__(ht.Tracker)
        t.receipts, t.pending, t.unclean = [], [], ''
        t._note('pending', {'pid': 1}, ['openocd'])
        with self.assertRaises(ht.Unclean):
            t.check()
        with self.assertRaises(ht.Unclean):   # nothing more is launched
            t.run(['JLinkExe'], timeout=1)
        ht.A.process.run_tracked.assert_not_called()
        self.assertEqual((t.receipts, t.pending), ([{'pid': 1}], [{'receipt': {'pid': 1}}]))
        t._note('unknown', None, ['openocd'])
        self.assertIn('unrecheckable', t.pending[-1]['receipt'])

    def test_failed_check_with_an_unproven_child_is_unclean(self):
        tracker = mock.Mock()
        tracker.check.side_effect = ht.Unclean('read-block: cleanup unknown')
        board = {'uid': 'ABC', 'flasher': {'name': 'jlink'}}
        with mock.patch.object(ht, 'flash_and_enumerate', return_value=('', '', {'busport': '1-1', 'dir': '/x'})), \
                mock.patch.dict(ht.TEST_FUNCS, {'msc_ramdisk': mock.Mock(side_effect=OSError('boom'))}):
            with self.assertRaises(ht.Unclean):
                ht.step('metro_m4_express', board, {'vidPid': '239a:8020'}, 'msc_ramdisk', self.dir, tracker)


    def test_flash_and_enumerate_keeps_snapshot_detach_flash_wait_order(self):
        calls = []
        with mock.patch.object(ht, 'enumeration_of', lambda *a: calls.append('snapshot') or 'ino1'), \
                mock.patch.object(ht, 'detach', lambda *a: calls.append('detach') or 'detached'), \
                mock.patch.object(ht, 'flash', lambda *a: calls.append('flash') or ''), \
                mock.patch.object(ht, 'wait_enumerated', lambda v, u, before: calls.append(('wait', before)) or {}):
            self.assertEqual(ht.flash_and_enumerate('b', {'uid': 'U', 'flasher': {}}, {'vidPid': '1:2'}, self.dir, None),
                             ('', 'detached', {}))
        self.assertEqual(calls, ['snapshot', 'detach', 'flash', ('wait', 'ino1')])
        with mock.patch.object(ht, 'enumeration_of', return_value=None), mock.patch.object(ht, 'detach'), \
                mock.patch.object(ht, 'flash', return_value='openocd exit 1'), \
                mock.patch.object(ht, 'wait_enumerated') as wait:
            self.assertEqual(ht.flash_and_enumerate('b', {'uid': 'U', 'flasher': {}}, {'vidPid': '1:2'}, self.dir, None)[2], None)
        wait.assert_not_called()


class Report(Base):
    def test_render_and_verdict_rows(self):
        ht.A.report.table = lambda headers, rows: '|'.join(headers) + '\n' + '\n'.join('|'.join(r) for r in rows)
        row = {'board': 'b', 'ran': True, 'pass': False, 'locked': False, 'recoveryBlocker': False,
               'tests': [{'test': 'cdc_echo', 'pass': False, 'detail': 'no echo'}],
               'cleanup': {'pass': True, 'state': 'serial_echo restored'}, 'detail': ''}
        md = ht.render({'pass': False, 'caveat': '', 'exitCode': 1, 'results': [row]})
        self.assertIn('b cdc_echo: no echo', md)


class ReportCommand(Base):
    def test_requested_boards_come_from_the_recorded_selection(self):
        ht.A.report.read_document.return_value = argparse.Namespace(
            outcome='valid', meta={'selection': ['metro_m4_express', 'raspberry_pi_pico'], 'termination': 'aborted'},
            body={'caveat': 'aborted by a signal', 'results': []})
        ht.A.report.apply_run_rules.side_effect = lambda sch, req, rows, term, cav: {
            'verdict': 'blocker', 'rows': [{'board': b, 'verdict': 'blocker', 'detail': 'missing'} for b in req]}
        out = io.StringIO()
        with mock.patch.object(ht, 'load_agentrc'), contextlib.redirect_stdout(out):
            ht.cmd_report(argparse.Namespace(run_dir=str(self.dir), board=None))
        got = json.loads(out.getvalue())
        self.assertEqual(ht.A.report.apply_run_rules.call_args[0][1], ['metro_m4_express', 'raspberry_pi_pico'])
        self.assertEqual((got['pass'], got['wedged'], [r['verdict'] for r in got['results']]),
                         (False, [], ['blocker', 'blocker']))


class Cli(unittest.TestCase):
    def test_unknown_board_refused_before_hardware(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(ht, 'load_agentrc'):
            code = ht.main(['run', '-b', 'nope', str(ROOT / 'test/hil/ci.json')])
        self.assertEqual(code, 2)
        self.assertIn('nope', json.loads(out.getvalue())['error'])


if __name__ == '__main__':
    unittest.main()
