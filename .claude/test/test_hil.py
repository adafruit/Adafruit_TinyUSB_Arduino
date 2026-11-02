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
import signal
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
        ht.esptool_exe.cache_clear()
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
        for role, tests in ht.ROLE_TESTS.items():
            names = [Path(e).name for e in ht.check_build.HIL_SKETCHES[role]]
            self.assertLessEqual({ht.TESTS[t] for t in tests}, set(names))
            self.assertEqual(ht.TESTS[ht.RESTORE_TEST[role]], names[0])   # the restore runs the restoration sketch
        self.assertEqual(set(ht.TESTS), {t for ts in ht.ROLE_TESTS.values() for t in ts})
        self.assertEqual(set(ht.TEST_FUNCS), set(ht.ROLE_TESTS['device']))

    def test_msc_fixture_matches_the_sketch(self):
        sketch = ROOT / 'examples/MassStorage/msc_ramdisk'
        text = (sketch / 'msc_ramdisk.ino').read_text() + (sketch / 'ramdisk.h').read_text()
        num = int(re.search(r'DISK_BLOCK_NUM\s*=?\s*(\d+)', text).group(1))
        size = int(re.search(r'DISK_BLOCK_SIZE\s*=?\s*(\d+)', text).group(1))
        readme = re.search(r'README_CONTENTS\s*\\?\s*"([^"]*)"', text).group(1)
        self.assertEqual((ht.MSC_DISK_BYTES, ht.MSC_README), (num * size, readme.encode()))

    def test_roster_is_valid_and_names_rig_boards(self):
        boards = ht.load_roster(ROOT / 'test/hil/ci.json')
        self.assertEqual(set(boards), {'raspberry_pi_pico', 'adafruit_fruit_jam', 'metro_m4_express',
                                       'raspberry_pi_pico_w', 'raspberry_pi_pico2', 'espressif_s3_devkitm',
                                       'espressif_p4_function_ev'})
        for name, b in boards.items():   # the S3's OTG takes its USB-Serial/JTAG PHY: no debug route
            self.assertEqual('openocd' in b.get('debug', {}), name != 'espressif_s3_devkitm')

    def test_roster_refusals(self):
        good = json.loads((ROOT / 'test/hil/ci.json').read_text())
        cases = (('raspberry_pi_pico', {'flasher': {'name': 'esptool', 'uid': 'x'}}, 'ESP chip profile'),
                 ('raspberry_pi_pico_w', {'arduino': {'tests': ['cdc_echo']}}, 'not host tests'),
                 ('raspberry_pi_pico_w', {'arduino': {'tests': ['host_device_info']}}, 'dev_attached'),
                 ('raspberry_pi_pico2', {'arduino': {'tests': ['host_device_info'], 'dev_attached': [
                     {'vid_pid': '0951_1603', 'serial': 'x'}]}}, 'dev_attached'),
                 ('espressif_s3_devkitm', {'usb_port': None}, 'needs usb_port'),
                 ('espressif_s3_devkitm', {'usb_port': '/dev/x'}, 'bus port'),
                 ('espressif_p4_function_ev', {'debug': {'openocd': '-f x', 'tool': 'openocd-esp32'}}, 'debug.uid'),
                 ('espressif_p4_function_ev', {'debug': {'openocd': '-f board/esp32p4-builtin.cfg', 'uid': 'M',
                                                         'tool': 'openocd-esp32', 'vid_pid': '0x303a 0x1001',
                                                         'usb_port': '11-1.2'}}, 'no vid_pid'),
                 ('espressif_p4_function_ev', {'debug': {'openocd': '-f board/esp32p4-builtin.cfg', 'uid': 'M',
                                                         'tool': 'openocd-esp32'}}, 'debug.usb_port'),
                 ('espressif_p4_function_ev', {'debug': {'openocd': '-f board/esp32s3-builtin.cfg', 'uid': 'M',
                                                         'tool': 'openocd-esp32'}}, 'no vid_pid'),
                 ('raspberry_pi_pico', {'flasher': {'name': 'pyocd'}}, 'flasher must be'))
        for name, over, why in cases:
            data = json.loads(json.dumps(good))
            entry = next(b for b in data['boards'] if b['name'] == name)
            entry.update(over)
            if entry.get('usb_port', '') is None:
                del entry['usb_port']
            path = Path(tempfile.mkdtemp()) / 'r.json'
            path.write_text(json.dumps(data))
            with self.subTest(name=name, why=why), self.assertRaisesRegex(ht.Refused, why):
                ht.load_roster(path)

    def test_budget_reserve_covers_the_restore(self):
        self.assertGreaterEqual(ht.CLEANUP_RESERVE, ht.FLASH_TIMEOUT + ht.VERIFY_TIMEOUT + ht.ENUM_TIMEOUT +
                                ht.TEST_TIMEOUT)


HOST_OUTPUT = '''TinyUSB Host: Device Info Example\r
Device attached, address = 1\r
Device attached, address = 2\r
Device 2: ID 1a86:55d4\r
Device Descriptor:\r
  bLength             18\r
  idVendor            0x1a86\r
  iManufacturer       0     \r
  iProduct            2     USB Single Serial\r
  iSerialNumber       3     52D2002694\r
  bNumConfigurations  1\r
Device 1: ID 2008:2018 USB2.0 Flash Disk\r
Device 2: ID 1a86:55d4  USB Single Serial\r
Device 3: ID 2008:2018\r
Device Descriptor:\r
  iSerialNumber       3     O20070925A002746\r
'''


class HostTest(Base):
    def test_parser_takes_full_descriptor_dumps_only(self):
        self.assertEqual(ht.host_devices(HOST_OUTPUT), {('1a86:55d4', '52D2002694'), ('2008:2018', 'O20070925A002746')})
        self.assertEqual(ht.host_devices(HOST_OUTPUT.split('Device 3')[0]), {('1a86:55d4', '52D2002694')})
        no_serial = 'Device 1: ID 0951:1603\r\nDevice Descriptor:\r\n  iSerialNumber       0     \r\n'
        self.assertEqual(ht.host_devices(no_serial), {('0951:1603', '')})

    def host_step(self, output, flash_why=''):
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico_w']
        calls, chunks = [], [output.encode()]

        class Port:
            def __init__(self, tty, baud, timeout):
                calls.append(('open', tty, baud))

            def __setattr__(self, name, value):
                calls.append((name, value))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                calls.append('close')

            def reset_input_buffer(self):
                calls.append('drain')

            def read(self, n):
                return chunks.pop(0) if chunks else b''
        import serial
        now = [0.0]

        def tick():
            now[0] += 1
            return now[0]
        with mock.patch.object(ht, 'console_tty', return_value='/dev/ttyACM20'), \
                mock.patch.object(ht, 'flash', lambda *a: calls.append('flash') or flash_why), \
                mock.patch.object(serial, 'Serial', Port), mock.patch.object(ht.time, 'monotonic', tick):
            row = ht.step('raspberry_pi_pico_w', board, {'elf': 'x.elf'}, 'host_device_info', self.dir, None)
        return row, calls

    def test_console_is_drained_before_the_flash_and_every_device_found(self):
        row, calls = self.host_step(HOST_OUTPUT)
        self.assertTrue(row['pass'], row)
        self.assertEqual(calls, [('open', '/dev/ttyACM20', 115200), 'drain', 'flash', 'close'])
        self.assertIn('52D2002694', Path(row['console']).read_text())

    def test_a_missing_device_fails_naming_it(self):
        row, _ = self.host_step(HOST_OUTPUT.split('Device 3')[0])
        self.assertFalse(row['pass'])
        self.assertIn("missing [('2008:2018', 'O20070925A002746')]", row['detail'])

    def test_failed_flash_reads_nothing(self):
        row, calls = self.host_step(HOST_OUTPUT, flash_why='openocd exit 1')
        self.assertEqual((row['pass'], row['detail']), (False, 'openocd exit 1'))

    def test_console_route(self):
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico2']
        ht.A.usb.usb_scan.return_value = [{'busport': '21-2.3', 'dir': '/sys/x'}]
        with mock.patch.object(ht, 'node', return_value='/dev/ttyACM19') as node:
            self.assertEqual(ht.console_tty(board, 5), '/dev/ttyACM19')
        self.assertEqual(ht.A.usb.usb_scan.call_args[1], {'vid_pid': ('2e8a', '000c'), 'serial': 'E6633861A3978538'})
        self.assertEqual(node.call_args[0][1], '*:*/tty/tty*')
        ht.A.flash.esptool_port.return_value = '/dev/ttyUSB1'
        s3 = ht.load_roster(ROOT / 'test/hil/ci.json')['espressif_s3_devkitm']
        self.assertEqual(ht.console_tty(s3, 5), '/dev/ttyUSB1')
        with self.assertRaisesRegex(RuntimeError, 'no console route'):
            ht.console_tty(ht.load_roster(ROOT / 'test/hil/ci.json')['metro_m4_express'], 5)


class Esp(Base):
    def image(self, segments=2):
        d = self.dir / 'img'
        d.mkdir(exist_ok=True)
        (d / 'flash_args').write_text('--flash-mode dio\n0x0 b.bin\n0x10000 a.bin\n')
        return {'dir': str(d), 'chip': 'esp32s3', 'segments': [[0, 'b.bin'], [0x10000, 'a.bin']][:segments],
                'elf': str(d / 'a.elf'), 'vidPid': '303a:1001'}

    def test_flash_runs_flash_args_and_counts_verified_images(self):
        f = ht.A.flash
        f.esptool_input_problem.return_value, f.esptool_problem.return_value = '', ''
        f.esptool_port.return_value, f.esptool_runtime.return_value = '/dev/ttyUSB1', {'exe': '/opt/esptool'}
        f.flash_esptool.return_value = mock.Mock(stdout='Hash of data verified.\nHash of data verified.\n', stderr='')
        tracker = mock.Mock()
        flasher = {'name': 'esptool', 'uid': '3ea6', 'args': '-b 1500000'}
        self.assertEqual(ht.flash(flasher, self.image(), self.dir, tracker), '')
        kw = f.flash_esptool.call_args[1]
        self.assertEqual((kw['chip'], kw['port'], kw['flash_args'], kw['esptool']),
                         ('esp32s3', '/dev/ttyUSB1', '--flash-mode dio 0x0 b.bin 0x10000 a.bin', '/opt/esptool'))
        f.flash_esptool.return_value.stdout = 'Hash of data verified.\n'
        self.assertEqual(ht.flash(flasher, self.image(), self.dir, tracker), 'esptool verified 1 of 2 images')
        f.esptool_problem.return_value = "exit 0, but the output says 'Chip was NOT reset'"
        self.assertIn('NOT reset', ht.flash(flasher, self.image(), self.dir, tracker))
        f.esptool_input_problem.return_value = 'flash_args names b.bin, which is missing'
        f.flash_esptool.reset_mock()
        self.assertIn('esptool inputs', ht.flash(flasher, self.image(), self.dir, tracker))
        f.flash_esptool.assert_not_called()
        f.esptool_input_problem.return_value, f.esptool_port.side_effect = '', RuntimeError('want one link')
        self.assertIn('want one link', ht.flash(flasher, self.image(), self.dir, tracker))

    def device(self, busport, ino, serial):
        d = self.dir / f'dev{ino}'
        d.mkdir()
        if serial:   # the app's iSerialNumber names an empty string: the kernel then makes no `serial`
            (d / 'serial').write_text(serial + '\n')
        return {'busport': busport, 'ino': ino, 'dir': str(d)}

    def test_dut_is_the_device_at_its_port(self):
        s3 = ht.load_roster(ROOT / 'test/hil/ci.json')['espressif_s3_devkitm']
        app, rom = self.device('11-1.3', 5, ''), self.device('11-1.3', 7, '84:F7:03:C0:84:E4')   # the ROM USJ's MAC
        gone = {'busport': '11-1.3', 'ino': 8, 'dir': str(self.dir / 'gone')}
        ht.A.usb.usb_devices.return_value = [app, rom, gone, self.device('11-1.1', 6, '')]
        ht.A.usb.enumeration_id = lambda d: int(Path(d).name[3:]) if Path(d).exists() else None   # as recorded
        self.assertEqual(ht.dut_devices('303a:1001', s3), [app])
        self.assertEqual(ht.A.usb.usb_devices.call_args[1], {'vid_pid': ('303a', '1001')})
        ht.A.usb.usb_scan.assert_not_called()
        self.assertEqual(ht.enumeration_of('303a:1001', s3), 5)
        ht.A.usb.enumeration_id = lambda d: 99   # re-enumerated since the record was taken
        self.assertEqual(ht.dut_devices('303a:1001', s3), [])

    def test_probe_presence_and_reset_through_the_bridge(self):
        flasher = {'name': 'esptool', 'uid': '3ea6'}
        ht.A.flash.esptool_port.side_effect = RuntimeError('found none')
        self.assertEqual(ht.probe_present(flasher), 'found none')
        ht.A.flash.esptool_port.side_effect, ht.A.flash.esptool_port.return_value = None, '/dev/ttyUSB1'
        self.assertEqual(ht.probe_present(flasher), '')
        ht.A.flash.esptool_runtime.return_value, ht.A.flash.esptool_problem.return_value = {'exe': 'e'}, ''
        self.assertEqual(ht.reset(flasher, mock.Mock()), '')
        self.assertEqual(ht.A.flash.reset_esptool.call_args[1]['port'], '/dev/ttyUSB1')
        ht.reset(flasher, mock.Mock())
        self.assertEqual(ht.A.flash.esptool_runtime.call_count, 1)   # identity once per invocation
        self.assertEqual(ht.A.flash.esptool_port.call_count, 4)      # the tty afresh each time

    def test_gdb_runs_espressif_openocd_on_the_usb_serial_jtag(self):
        esp = mock.Mock()
        esp.admit.return_value = ('esp32p4', ['board/esp32p4-builtin.cfg'])
        esp.find_openocd.return_value = {'exe': '/o/bin/openocd', 'scripts': '/o/scripts'}
        ht.A.esp = lambda: esp
        p4 = ht.load_roster(ROOT / 'test/hil/ci.json')['espressif_p4_function_ev']
        with mock.patch.object(ht, 'usj_at', return_value='11-1.2'):
            pipe = ht.shlex.split(ht.gdb_argv('espressif_p4_function_ev', p4, 'fw.elf', [])[4]
                                  .removeprefix('target extended-remote | '))
        esp.usj_location.assert_not_called()   # it refuses while the S3's serial-less DUT is on the bus
        self.assertEqual(esp.admit.call_args[0][0], {'name': 'openocd_esp', 'uid': '60:55:F9:F9:87:15',
                                                     'args': '-f board/esp32p4-builtin.cfg'})
        self.assertEqual(esp.find_openocd.call_args[0][0], 'openocd-esp32')
        esp.check_cfgs.assert_called_once_with('esp32p4', ['board/esp32p4-builtin.cfg'], '/o/scripts')
        flash_size, board_cfg = pipe.index('set ESP_FLASH_SIZE 0'), pipe.index('/o/scripts/board/esp32p4-builtin.cfg')
        self.assertEqual(pipe[:3], ['/o/bin/openocd', '-s', '/o/scripts'])
        self.assertLess(flash_size, board_cfg)   # read when the target cfg is sourced (esp_common.cfg:37-39)
        self.assertEqual(pipe[board_cfg + 1:board_cfg + 5], ['-c', 'adapter usb location 11-1.2',
                                                             '-c', 'adapter serial 60:55:F9:F9:87:15'])
        self.assertFalse(any('vid_pid' in w for w in pipe))
        self.assertIn('tcl port disabled', pipe)
        self.assertIn('foreach t [target names] { $t configure -event gdb-detach { resume } }', pipe)
        esp.admit.return_value = ('esp32s3', ['board/esp32s3-builtin.cfg'])
        with self.assertRaisesRegex(ValueError, 'names esp32s3'):
            ht.gdb_argv('espressif_p4_function_ev', p4, 'fw.elf', [])

    def test_flash_names_what_the_flasher_wrote(self):
        image = {'elf': '/r/serial_echo.ino.elf', 'hex': '/r/serial_echo.ino.hex', 'segments': [[0, 'a'], [1, 'b']]}
        self.assertEqual([ht.written({'name': n}, image) for n in ('openocd', 'jlink', 'esptool')],
                         ['serial_echo.ino.elf', 'serial_echo.ino.hex', '2 flash_args images'])
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['metro_m4_express']
        dev = {'busport': '9-1.4', 'devnum': 95, 'ino': 3, 'after': 1.9}
        with mock.patch.object(ht, 'flash_and_enumerate', return_value=('', 'n/a', dev)):
            row = ht.flash_only('metro_m4_express', board, {**image, 'vidPid': '239a:8020'}, self.dir, None)
        self.assertEqual((row['detail'], row['device']), ('flashed serial_echo.ino.hex; enumerated as 239a:8020 at '
                                                          '9-1.4', {'busport': '9-1.4', 'devnum': 95, 'after': 1.9}))

    def test_usb_serial_jtag_is_checked_at_its_roster_port(self):
        root = self.dir / 'sys'
        usj = root / '11-1.2'
        usj.mkdir(parents=True)
        (usj / 'idVendor').write_text('303a\n')
        (usj / 'idProduct').write_text('1001\n')
        ht.A.usb.SYS_USB_DEVICES = str(root)
        ht.A.usb.enumeration_id = lambda d: 7 if Path(d).exists() else None
        dbg = {'uid': '60:55:F9:F9:87:15', 'usb_port': '11-1.2'}
        ht.A.usb.read_sysfs = lambda p: '60:55:F9:F9:87:15'
        self.assertEqual(ht.usj_at(dbg), '11-1.2')
        for serial, port in (('84:F7:03:C0:84:E4', '11-1.2'), (None, '11-1.2'), ('60:55:F9:F9:87:15', '11-1.3')):
            ht.A.usb.read_sysfs = lambda p, s=serial: s
            with self.subTest(serial=serial, port=port), self.assertRaisesRegex(RuntimeError, 'no USB-Serial/JTAG'):
                ht.usj_at({**dbg, 'usb_port': port})
        ids = iter([7, 8])   # re-enumerated during the check
        ht.A.usb.enumeration_id = lambda d: next(ids)
        ht.A.usb.read_sysfs = lambda p: '60:55:F9:F9:87:15'
        with self.assertRaisesRegex(RuntimeError, 'no USB-Serial/JTAG'):
            ht.usj_at(dbg)

    def test_admit_carries_the_flash_inputs_and_checks_the_role(self):
        self.roster.write_text(json.dumps({'boards': [
            {'name': 'espressif_s3_devkitm', 'uid': 'U', 'usb_port': '11-1.3',
             'flasher': {'name': 'esptool', 'uid': '3ea6'}, 'arduino': {'tests': ['cdc_echo']}}]}))
        root = self.dir / 'root'
        b = root / 'build/hil/espressif_s3_devkitm/serial_echo'
        b.mkdir(parents=True)
        files = {'serial_echo.ino.elf': b'elf', 'flash_args': b'--x y\n0x0 a.bin\n', 'a.bin': b'app'}
        for n, data in files.items():
            (b / n).write_bytes(data)
        rel = 'build/hil/espressif_s3_devkitm/serial_echo/'
        rec = {'kind': 'arduino-hil-build', 'version': 1, 'board': 'espressif_s3_devkitm', 'experimental': False,
               'role': 'device', 'chip': 'esp32s3', 'source': {'head': 'abc'},
               'roster': {'sha256': hashlib.sha256(self.roster.read_bytes()).hexdigest()},
               'sketches': {'serial_echo': {'image': 'esptool', 'elf': rel + 'serial_echo.ino.elf', 'hex': None,
                                            'vidPid': '303a:1001', 'segments': [[0, 'a.bin']],
                                            'sha256': {rel + n: hashlib.sha256(d).hexdigest()
                                                       for n, d in files.items()}}}}
        path = self.dir / 'rec.json'
        path.write_text(json.dumps(rec))
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'git_identity', return_value=('abc', '')):
            _, images = ht.admit(path, 'espressif_s3_devkitm', self.roster, self.dir / 'run')
            img = images['serial_echo']
            self.assertEqual((img['chip'], img['segments']), ('esp32s3', [[0, 'a.bin']]))
            self.assertEqual(sorted(p.name for p in Path(img['dir']).iterdir()), sorted(files))
            path.write_text(json.dumps(dict(rec, role='host')))
            with self.assertRaisesRegex(ht.Refused, 'a host record'):
                ht.admit(path, 'espressif_s3_devkitm', self.roster, self.dir / 'run2')


class Console(Base):
    def capture(self, board_name, reset, fail=False):
        board = ht.load_roster(ROOT / 'test/hil/ci.json')[board_name]
        calls, reads = [], []

        class Port:
            def __init__(self, tty, baud, timeout):
                calls.append(('open', tty, baud))

            def __setattr__(self, name, value):
                calls.append((name, value))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                calls.append('close')

            def reset_input_buffer(self):
                pass

            def read(self, n):
                reads.append(n)
                if fail and len(reads) > 1:
                    raise fail
                return b'boot\n' if len(calls) < 6 else b''
        import serial
        now = [0.0]

        def tick():
            now[0] += 1
            return now[0]
        args = argparse.Namespace(board=board_name, reset=reset, baud=115200, seconds=10)
        with mock.patch.object(ht, 'console_tty', return_value='/dev/ttyX'), \
                mock.patch.object(ht, 'reset', lambda *a: calls.append('reset') or ''), \
                mock.patch.object(serial, 'Serial', Port), mock.patch.object(ht.time, 'monotonic', tick):
            row = ht.op_console(args, board, self.dir, None, None)
        return row, calls

    def test_probe_reset_happens_with_the_console_open(self):
        row, calls = self.capture('raspberry_pi_pico_w', True)
        self.assertEqual(calls[:2], [('open', '/dev/ttyX', 115200), 'reset'])
        self.assertTrue(row['pass'])
        self.assertIn('boot', (self.dir / 'console.log').read_text())

    def test_rp2040_device_console_reset_detaches_first(self):
        with mock.patch.object(ht, 'detach', lambda *a: calls_detach.append(a[2]) or 'detached'):
            calls_detach = []
            _, calls = self.capture('raspberry_pi_pico', True)
        self.assertEqual((calls_detach, calls[:2]), ([None], [('open', '/dev/ttyX', 115200), 'reset']))
        with mock.patch.object(ht, 'detach', return_value='still enumerated 5s after the detach'):
            row, calls = self.capture('raspberry_pi_pico', True)
        self.assertEqual((row['pass'], 'reset' in calls), (False, False))
        self.assertIn('detach: still enumerated', row['detail'])

    def test_detach_without_a_record_waits_for_the_serial(self):
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico']
        ht.A.flash.run_openocd_commands.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        ht.A.usb.usb_scan.side_effect = [[{'busport': '21-1.3'}], []]
        with mock.patch.object(ht.time, 'sleep'):
            self.assertEqual(ht.detach('raspberry_pi_pico', board, None, mock.Mock()), 'detached')
        self.assertEqual(ht.A.usb.usb_scan.call_args[1], {'serial': board['uid']})
        pico_w = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico_w']
        self.assertEqual(ht.detach('raspberry_pi_pico_w', pico_w, None, mock.Mock()), '')   # a host board

    def test_esptool_reset_pulses_en_with_the_bridge_open(self):
        row, calls = self.capture('espressif_s3_devkitm', True)
        self.assertEqual(calls[:4], [('open', '/dev/ttyX', 115200), ('dtr', False), ('rts', True), ('rts', False)])
        self.assertNotIn('reset', calls)
        times = [json.loads(x) for x in (self.dir / 'console.times').read_text().splitlines()]
        self.assertTrue(times[0]['reset'])
        self.assertEqual([t['len'] for t in times[1:]], [5] * (len(times) - 1))
        _, calls = self.capture('espressif_s3_devkitm', False)
        self.assertFalse([c for c in calls if c[0] in ('dtr', 'rts')])

    def test_serial_error_keeps_the_capture_and_fails(self):
        import serial
        for error in (serial.SerialException('device disconnected'), OSError(5, 'device disconnected')):
            row, _ = self.capture('raspberry_pi_pico_w', False, fail=error)
            self.assertFalse(row['pass'])
            self.assertIn('device disconnected', row['detail'])
            self.assertIn('boot', (self.dir / 'console.log').read_text())

    def test_check_is_a_session_command(self):
        with mock.patch.object(ht, 'manual', return_value=0) as manual, mock.patch.object(ht.signal, 'signal'):
            ht.main(['check', '-b', 'raspberry_pi_pico', '--under-hold', 'x', '--build-record', 'r.json', 'ci.json'])
        self.assertEqual((manual.call_args[0][0].cmd, manual.call_args[0][0].test), ('check', None))

    def test_seconds_bounded(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = ht.main(['console', '-b', 'raspberry_pi_pico_w', '--under-hold', 'x', '--seconds', '500', 'r.json'])
        self.assertEqual(code, 2)
        self.assertIn('--seconds', out.getvalue())


class RolesInRun(Base):
    def test_commission_refuses_a_host_board(self):
        args = argparse.Namespace(board='raspberry_pi_pico_w', roster=str(ROOT / 'test/hil/ci.json'),
                                  record_dir=str(self.dir))
        with mock.patch.object(ht, 'load_agentrc'), self.assertRaisesRegex(ht.Refused, 'host board'):
            ht.cmd_commission(args)
        self.assertEqual(self.lock.taken, [])

    def test_commission_by_port(self):
        args = argparse.Namespace(board='espressif_s3_devkitm', roster=str(ROOT / 'test/hil/ci.json'),
                                  record_dir=str(self.dir))
        ino = [0]

        def bump(*a):
            ino[0] += 1
            return ''
        ht.A.usb.usb_devices.side_effect = lambda **k: [{'busport': '11-1.3', 'ino': ino[0], 'dir': '/x'},
                                                         {'busport': '11-1.1', 'ino': 1, 'dir': '/y'}]
        with mock.patch.object(ht, 'ROOT', self.dir), mock.patch.object(ht, 'load_agentrc'), \
                mock.patch.object(ht, 'admit', return_value=({}, {'serial_echo': {'vidPid': '303a:1001'}})), \
                mock.patch.object(ht, 'detach'), mock.patch.object(ht, 'flash', bump), \
                mock.patch.object(ht, 'reset', bump), mock.patch.object(ht.time, 'sleep'), \
                mock.patch.object(ht, 'test_cdc', return_value=(True, 'echoed')), \
                mock.patch.object(ht, 'no_serial', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ht.cmd_commission(args), 0)
        result = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual((result['port'], result['matchesRoster'], result['observed']),
                         ('11-1.3', True, [['11-1.3'], ['11-1.3']]))

    def test_check_runs_a_device_test_without_flashing(self):
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico']
        args = argparse.Namespace(board='raspberry_pi_pico', test=None)
        image = {'vidPid': '239a:cafe'}
        with mock.patch.object(ht, 'admit_sketch', return_value=({'record': 'r'}, image, {})) as admitted, \
                mock.patch.object(ht, 'dut_devices', return_value=[{'busport': '21-1.3'}]), \
                mock.patch.object(ht, 'flash') as flashed, \
                mock.patch.dict(ht.TEST_FUNCS, {'cdc_echo': lambda *a: (True, 'echoed')}):
            row = ht.op_check(args, board, self.dir, mock.Mock(), None)
        self.assertEqual((row['pass'], row['test'], row['device']['busport']), (True, 'cdc_echo', '21-1.3'))
        self.assertEqual(admitted.call_args[0][1], 'serial_echo')
        flashed.assert_not_called()
        with mock.patch.object(ht, 'admit_sketch', return_value=({}, image, {})), \
                mock.patch.object(ht, 'dut_devices', return_value=[]):
            self.assertIn('found 0', ht.op_check(args, board, self.dir, mock.Mock(), None)['detail'])
        args.board = 'raspberry_pi_pico_w'
        with self.assertRaisesRegex(ht.Refused, 'host board'):
            ht.op_check(args, board, self.dir, mock.Mock(), None)

    def test_selection_drops_boards_without_a_requested_test(self):
        roster = ht.load_roster(ROOT / 'test/hil/ci.json')
        picked = ht.selection(roster, ['raspberry_pi_pico', 'raspberry_pi_pico_w'], ['cdc_echo'])
        self.assertEqual(picked, {'raspberry_pi_pico': ['cdc_echo']})
        self.assertEqual(ht.selection(roster, ['raspberry_pi_pico_w'], None),
                         {'raspberry_pi_pico_w': ['host_device_info']})
        with self.assertRaisesRegex(ht.Refused, 'no selected board'):
            ht.selection(roster, ['raspberry_pi_pico_w'], ['cdc_echo'])
        with self.assertRaisesRegex(ht.Refused, 'not in the roster'):
            ht.selection(roster, ['nope'], None)

    def test_run_gives_each_board_the_requested_tests_of_its_role(self):
        seen = {}

        def run_board(name, board, tests, *a):
            seen[name] = tests
            return {'board': name, 'ran': True, 'pass': True, 'locked': False, 'recoveryBlocker': False,
                    'tests': [], 'cleanup': {'pass': True, 'state': 'x'}, 'detail': ''}
        args = argparse.Namespace(board=['raspberry_pi_pico', 'raspberry_pi_pico_w'], test=['cdc_echo'],
                                  roster=str(ROOT / 'test/hil/ci.json'), record_dir=str(self.dir),
                                  run_dir=str(self.dir), staged=None)
        ht.A.report.ReportError, ht.A.report.publish.return_value = Exception, ('hil_report.json',)
        with mock.patch.object(ht, 'load_agentrc'), mock.patch.object(ht, 'run_board', run_board), \
                mock.patch.object(ht.shutil, 'which', return_value='/bin/x'), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ht.cmd_run(args), 0)
        self.assertEqual(seen, {'raspberry_pi_pico': ['cdc_echo']})   # the host board is not run, nor reported


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

    def test_unrecheckable_child_is_recorded_pending_and_reported(self):
        lost = {'receipt': {'unrecheckable': ['JLinkExe'], 'at': 1.0}}

        def step(*a):
            a[-1].pending.append(lost)
            raise ht.Unclean('JLinkExe: cleanup unknown')
        row = self.run_board(step=step)
        self.assertEqual(self.lock.pending[('metro_m4_express', ht.PENDING_KIND)], [lost])
        self.assertEqual((row['cleanup']['pending'], row['cleanup']['markError']), ([lost], ''))
        self.assertEqual((ht.verdict(row), row['cleanup']['holder']), ('blocker', {'token': 'tok123'}))
        row = self.run_board()   # a later run refuses until a reviewed clear
        self.assertEqual((ht.verdict(row), row['recoveryBlocker'], row['ran']), ('blocker', True, False))
        ht.A.process.recheck.assert_not_called()

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

    def test_restore_refuses_an_unrecheckable_child_of_the_report(self):
        rec, root = self.record()
        report = self.dir / 'hil_report.json'
        report.write_text(json.dumps({'results': [{'board': 'metro_m4_express', 'cleanup': {
            'receipts': [], 'pending': [{'receipt': {'unrecheckable': ['JLinkExe'], 'at': 1.0}}]}}]}))
        args = self.held(root, build_record=str(rec), report=str(report), release=False)
        with mock.patch.object(ht, 'ROOT', root), mock.patch.object(ht, 'git_identity', return_value=('abc', '')), \
                mock.patch.object(ht, 'restore', return_value={'pass': True}) as restore:
            with self.assertRaisesRegex(ht.Refused, 'unrecheckable'):
                ht.op_restore(args, None, self.dir / 'run', None, None)
        restore.assert_not_called()

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

    def test_sigterm_unwinds_a_session_command_as_an_interrupt(self):
        old = {s: signal.getsignal(s) for s in (signal.SIGHUP, signal.SIGTERM)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in old.items()])

        def manual(args):   # SIG_DFL would end the harness past _manual's pending bookkeeping
            self.assertTrue(callable(signal.getsignal(signal.SIGTERM)))
            signal.raise_signal(signal.SIGTERM)
        with mock.patch.object(ht, 'manual', manual), self.assertRaises(KeyboardInterrupt):
            ht.main(['exec', '-b', 'metro_m4_express', '--under-hold', 'r', 'ci.json', '--', 'true'])

    def test_sigterm_unwinds_run_and_commission_as_an_interrupt(self):
        old = {s: signal.getsignal(s) for s in (signal.SIGHUP, signal.SIGTERM)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in old.items()])

        def command(args):   # SIG_DFL would skip run_board's finally and cmd_commission's hand-over
            self.assertTrue(callable(signal.getsignal(signal.SIGTERM)))
            signal.raise_signal(signal.SIGTERM)
        for cmd, argv in (('cmd_run', ['run', 'ci.json']), ('cmd_commission', ['commission', '-b', 'b', 'ci.json'])):
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            with self.subTest(cmd), mock.patch.object(ht, cmd, command), self.assertRaises(KeyboardInterrupt):
                ht.main(argv)
            self.assertEqual(signal.getsignal(signal.SIGHUP), signal.SIG_DFL)

    def test_gdb_runs_openocd_on_a_pipe_pinned_to_the_probe(self):
        ht.A.flash.openocd_cmd_base = lambda p: [
            'openocd', '-c', 'gdb_port disabled', '-c', f'adapter serial {p["uid"]}',
            *(['-c', f'adapter usb vid_pid {p["vid_pid"]}'] if 'vid_pid' in p else []), *ht.shlex.split(p['args'])]
        board = ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico']
        argv = ht.gdb_argv('raspberry_pi_pico', board, 'fw.elf', ['info symbol main', 'bt'])
        self.assertEqual(argv[:4], ['gdb-multiarch', '-batch', '-nx', '-ex'])
        self.assertEqual((argv[-5:], argv[-1]), (['-ex', 'info symbol main', '-ex', 'bt', 'fw.elf'], 'fw.elf'))
        pipe = ht.shlex.split(argv[4].removeprefix('target extended-remote | '))
        self.assertEqual(pipe[-8:], ['-c', 'gdb port pipe', '-c', 'gdb memory_map disable',
                                     '-c', 'gdb breakpoint_override hard', '-c',
                                     'foreach t [target names] { $t configure -event gdb-detach '
                                     '{ foreach u [target names] { $u arp_poll }; resume } }'])
        self.assertIn('adapter serial E6614103E72C1D2F', pipe)
        self.assertIn('adapter usb vid_pid 0x2e8a 0x000c', pipe)
        self.assertIn('target/rp2040.cfg', pipe)
        metro = ht.load_roster(ROOT / 'test/hil/ci.json')['metro_m4_express']
        pipe = ht.shlex.split(ht.gdb_argv('metro_m4_express', metro, 'fw.elf', [])[4]
                              .removeprefix('target extended-remote | '))
        self.assertIn('interface/jlink.cfg', pipe)
        self.assertEqual(pipe[-1], 'foreach t [target names] { $t configure -event gdb-detach { resume } }')
        for rp2350 in ('raspberry_pi_pico2', 'adafruit_fruit_jam'):   # rp2040 build family, other silicon
            argv = ht.gdb_argv(rp2350, ht.load_roster(ROOT / 'test/hil/ci.json')[rp2350], 'fw.elf', [])
            self.assertNotIn('arp_poll', argv[4])
        argv = ht.gdb_argv('raspberry_pi_pico_w', ht.load_roster(ROOT / 'test/hil/ci.json')['raspberry_pi_pico_w'],
                           'fw.elf', [])
        self.assertIn('arp_poll', argv[4])
        self.assertFalse(any('vid_pid' in w for w in pipe))

    def test_foreign_or_missing_hold_refused(self):
        with self.assertRaisesRegex(ht.Refused, 'not held'):
            ht.held_by('metro_m4_express', 'mine')
        self.lock.records['metro_m4_express'] = {'reason': 'someone else', 'pid': 1}
        with self.assertRaisesRegex(ht.Refused, 'not held'):
            ht.held_by('metro_m4_express', 'mine')
        self.lock.records['metro_m4_express'] = {'reason': 'mine', 'pid': 1}
        ht.held_by('metro_m4_express', 'mine')


class Commission(Base):
    def test_unclean_commission_marks_the_board_pending_and_keeps_a_report(self):
        args = argparse.Namespace(board='metro_m4_express', roster=str(self.roster), record_dir=str(self.dir))

        def detach(*a):
            a[-1]._note('pending', {'pid': 7}, ['JLinkExe'])
            a[-1].check()
        with mock.patch.object(ht, 'ROOT', self.dir), mock.patch.object(ht, 'load_agentrc'), \
                mock.patch.object(ht, 'admit', return_value=({}, {'serial_echo': {'vidPid': '239a:8020'}})), \
                mock.patch.object(ht, 'scan', return_value=[]), mock.patch.object(ht, 'detach', detach), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ht.cmd_commission(args), 4)
        self.assertEqual(self.lock.pending[('metro_m4_express', ht.PENDING_KIND)], [{'receipt': {'pid': 7}}])
        ht.A.process.recheck.return_value = 'pending'
        self.assertIn('pending children', ht.pending_problem('metro_m4_express'))
        report = next(self.dir.glob('build/hil-manual/commission-metro_m4_express-*/result.json'))
        self.assertEqual(json.loads(report.read_text())['holder'], {'token': 'tok123'})
        restore = argparse.Namespace(board='metro_m4_express', report=str(report))
        with self.assertRaisesRegex(ht.Refused, 'not proven ended'):
            ht.op_restore(restore, None, self.dir / 'run', None, None)

    def test_interrupt_after_the_release_is_not_a_held_board(self):
        args = argparse.Namespace(board='metro_m4_express', roster=str(self.roster), record_dir=str(self.dir))
        ino = [0]

        def detach(*a):
            ino[0] += 1

        def release(fh):   # SIGTERM lands just after the lock is gone
            self.lock.released.append(fh)
            raise KeyboardInterrupt
        with mock.patch.object(ht, 'ROOT', self.dir), mock.patch.object(ht, 'load_agentrc'), \
                mock.patch.object(ht, 'admit', return_value=({}, {'serial_echo': {'vidPid': '239a:8020'}})), \
                mock.patch.object(ht, 'scan', lambda *a: [{'serial': 'ABC', 'ino': ino[0]}]), \
                mock.patch.object(ht, 'detach', detach), mock.patch.object(ht, 'flash', return_value=''), \
                mock.patch.object(ht, 'reset', return_value=''), mock.patch.object(ht.time, 'sleep'), \
                mock.patch.object(ht, 'test_cdc', return_value=(True, 'echoed')), \
                mock.patch.object(self.lock, 'release_board_lock', release), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            ht.cmd_commission(args)
        self.assertEqual((len(self.lock.released), self.lock.handed), (1, []))
        self.assertFalse(list(self.dir.glob('build/hil-manual/commission-metro_m4_express-*/result.json')))


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

    def cdc_port(self, seen, stall, now):
        import serial

        class Port:
            write_timeout = None
            last = b''

            def __init__(self, *a, **k):
                self.write_timeout = k.get('write_timeout')

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def reset_input_buffer(self):
                pass

            def write(self, data):
                if self.write_timeout is None:
                    raise AssertionError('an unbounded write blocks past the deadline')
                seen.append(self.write_timeout)
                now[0] += 1.0
                if stall:
                    raise serial.SerialTimeoutException('Write timeout')
                self.last = data
                return len(data)

            def read(self, n):
                now[0] += 0.5
                got, self.last = self.last[:n].upper(), self.last[n:]
                return got
        return Port

    def run_cdc(self, stall):
        import serial
        seen, now = [], [0.0]
        with mock.patch.object(ht, 'node', return_value='/dev/ttyACM9'), \
                mock.patch.object(ht.time, 'monotonic', lambda: now[0]), \
                mock.patch.object(serial, 'Serial', self.cdc_port(seen, stall, now)):
            ok, detail = ht.test_cdc({}, 'ab', now[0] + 5, None, self.dir)
        return ok, detail, seen

    def test_cdc_write_is_bounded_by_the_test_deadline(self):
        ok, detail, seen = self.run_cdc(stall=True)
        self.assertFalse(ok)
        self.assertIn('timed out', detail)
        self.assertEqual(seen, [5.0])

    def test_cdc_bounded_write_that_completes_echoes(self):
        ok, detail, seen = self.run_cdc(stall=False)
        self.assertTrue(ok, detail)
        self.assertEqual(seen, [5.0, 3.5, 2.0])   # each write gets the time left, not a fixed bound

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
