#!/usr/bin/env python3
"""The HIL harness's profile: hil_test.py's battery (the CDC, HID and MSC host checks, the host board's console step,
read-block), its tables against the sketches they run, its roster checks and the rosters themselves through agentrc's
arduino_hil.py. The harness mechanics (admission, locks, flashing, sessions, the report) are agentrc's and
tested there. agentrc's flash and USB modules and the serial port are stubbed: stubs prove the plumbing only, hardware
behaviour is a real run's."""
import argparse
import inspect
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'))
import hil_test as ht  # noqa: E402

E = ht.engine()   # agentrc's arduino_hil bound to this profile: a missing agentrc fails here


def roster(name):
    return E.load_roster(ROOT / 'test/hil/ci.json')[name]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.A = argparse.Namespace(lock=mock.Mock(), flash=mock.Mock(), process=mock.Mock(), report=mock.Mock(),
                                    usb=mock.Mock(BUSPORT_RE=E.A.usb.BUSPORT_RE), esp=E.A.esp, root=E.A.root)
        p = mock.patch.object(E, 'A', self.A)
        p.start()
        self.addCleanup(p.stop)


class Tables(unittest.TestCase):
    def test_tests_use_recorded_sketches(self):   # the engine's bind() checks the restore and the role tables
        for role, tests in ht.ROLE_TESTS.items():
            names = [Path(e).name for e in ht.BUILD.HIL_SKETCHES[role]]
            self.assertLessEqual({ht.TESTS[t] for t in tests}, set(names))

    def test_test_functions_take_the_engines_arguments(self):
        for name, fn in ht.TEST_FUNCS.items():
            self.assertEqual(list(inspect.signature(fn).parameters),
                             ['dev', 'nonce', 'deadline', 'tracker', 'run_dir', 'board'], name)
        self.assertEqual(list(inspect.signature(ht.step_host).parameters),
                         ['name', 'board', 'image', 'test', 'run_dir', 'tracker'])

    def test_msc_fixture_matches_the_sketch(self):
        sketch = ROOT / 'examples/MassStorage/msc_ramdisk'
        text = (sketch / 'msc_ramdisk.ino').read_text() + (sketch / 'ramdisk.h').read_text()
        num = int(re.search(r'DISK_BLOCK_NUM\s*=?\s*(\d+)', text).group(1))
        size = int(re.search(r'DISK_BLOCK_SIZE\s*=?\s*(\d+)', text).group(1))
        readme = re.search(r'README_CONTENTS\s*\\?\s*"([^"]*)"', text).group(1)
        self.assertEqual((ht.MSC_DISK_BYTES, ht.MSC_README), (num * size, readme.encode()))

    def test_remote_settings(self):
        self.assertEqual(ROOT / ht.HIL, ht.SCRIPT)
        self.assertTrue((ROOT / ht.REMOTE_ROSTER).is_file())
        self.assertIn(ht.HIL, ht.BUILD.CONTRACT_FILES)

    def test_roster_is_valid_and_names_rig_boards(self):
        boards = E.load_roster(ROOT / 'test/hil/ci.json')
        self.assertEqual(set(boards), {'raspberry_pi_pico', 'adafruit_fruit_jam', 'metro_m4_express',
                                       'raspberry_pi_pico_w', 'raspberry_pi_pico2', 'espressif_s3_devkitm',
                                       'espressif_p4_function_ev'})
        for name, b in boards.items():   # the S3's OTG takes its USB-Serial/JTAG PHY: no debug route
            self.assertEqual('openocd' in b.get('debug', {}), name != 'espressif_s3_devkitm')
        for name in ('espressif_s3_devkitm', 'espressif_p4_function_ev'):   # Arduino on ESP32 reports no serial
            self.assertTrue(boards[name]['no_serial'])

    def test_local_example_roster_is_valid(self):
        self.assertEqual(list(E.load_roster(ROOT / 'test/hil/local.example.json')), ['raspberry_pi_pico'])

    def test_roster_refusals(self):
        data = json.loads((ROOT / 'test/hil/ci.json').read_text())
        entry = next(b for b in data['boards'] if b['name'] == 'raspberry_pi_pico2')
        entry['arduino']['dev_attached'] = [{'vid_pid': 'abcd:1234', 'serial': '\u0409'}]
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'r.json'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(E.Refused, "agentrc's board registry's now"):
            E.load_roster(path)
        data = json.loads((ROOT / 'test/hil/local.example.json').read_text())   # unregistered: its own copy stays
        data['boards'][0].update(name='raspberry_pi_pico2', arduino={'tests': ['host_device_info']},
                                 dev_attached=[{'serial': 'x'}])
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(E.Refused, 'raspberry_pi_pico2: dev_attached: .*vid_pid'):
            E.load_roster(path)

    def test_check_board_takes_device_boards_as_they_are(self):
        ht.check_board('raspberry_pi_pico', {}, 'r.json')
        with self.assertRaisesRegex(E.Refused, 'r.json: raspberry_pi_pico_w: a host board needs a device with a serial'):
            ht.check_board('raspberry_pi_pico_w', {'arduino': {}}, 'r.json')
        with self.assertRaisesRegex(E.Refused, 'needs a device with a serial'):
            ht.check_board('raspberry_pi_pico2', {'dev_attached': [{'vid_pid': '1a86:7523'}]}, 'r.json')

    def test_check_board_refuses_a_malformed_dev_attached(self):   # an unregistered board's own copy
        for devices in ([{'serial': 'x'}], [{'vid_pid': 'ABCD:1234', 'serial': 'x'}],
                        [{'vid_pid': 'abcd:1234', 'serial': 5}], ['abcd:1234'], 'abcd:1234'):
            with self.subTest(devices=devices), \
                    self.assertRaisesRegex(E.Refused, 'r.json: raspberry_pi_pico2: dev_attached: '):
                ht.check_board('raspberry_pi_pico2', {'dev_attached': devices}, 'r.json')
        ht.check_board('raspberry_pi_pico', {'dev_attached': [{'vid_pid': '1a86:7523'}]}, 'r.json')

    def test_a_registered_board_takes_no_dev_attached_of_its_own(self):   # its registry entry lists none
        own = {'dev_attached': [{'vid_pid': 'abcd:1234', 'serial': 'x'}]}
        registry = {'P1': {'name': 'b1'}}
        with mock.patch.object(ht.BUILD.E.hil_boards, 'registry', return_value=registry):
            with self.assertRaisesRegex(E.Refused, "r.json: raspberry_pi_pico2: dev_attached of probe P1 comes only "
                                                   "from agentrc's board registry"):
                ht.check_board('raspberry_pi_pico2', {'probe': 'P1', **own}, 'r.json')
            registry['P1']['dev_attached'] = own['dev_attached']   # resolution put the registry's at the top level
            ht.check_board('raspberry_pi_pico2', {'probe': 'P1', **own}, 'r.json')
            ht.check_board('raspberry_pi_pico2', own, 'r.json')   # unregistered: its own copy stays

    def test_attached_keeps_the_devices_with_a_serial(self):
        board = {'dev_attached': [{'vid_pid': 'abcd:1234', 'serial': '\u0409', 'is_msc': True},
                                  {'vid_pid': '1a86:7523'}]}
        self.assertEqual(ht.attached(board), {('abcd:1234', '\u0409')})


class Detach(Base):
    def test_only_the_rp2040_family_clears_the_pull_up(self):
        self.A.flash.run_openocd_commands.return_value = mock.Mock(returncode=0, stdout='', stderr='')
        self.A.usb.usb_devices.return_value = []
        self.assertEqual(E.detach('raspberry_pi_pico', roster('raspberry_pi_pico'), '239a:cafe', mock.Mock()),
                         'detached')
        self.assertEqual(self.A.flash.run_openocd_commands.call_args[0][1], ['mww 0x5011304c 0x00010000'])
        self.A.flash.run_openocd_commands.reset_mock()
        self.assertEqual(E.detach('metro_m4_express', roster('metro_m4_express'), '239a:8020', mock.Mock()), '')
        self.assertEqual(E.detach('raspberry_pi_pico_w', roster('raspberry_pi_pico_w'), None, mock.Mock()), '')
        self.A.flash.run_openocd_commands.assert_not_called()


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

    def test_parser_takes_a_utf8_serial_whole(self):   # the Pico 2's registry device abcd:1234, serial U+0409
        buf = 'Device 1: ID abcd:1234\r\nDevice Descriptor:\r\n  iSerialNumber       3     Љ\r\n'.encode()
        self.assertEqual(ht.host_devices(buf.decode(errors='replace')), {('abcd:1234', 'Љ')})
        cut = buf[:buf.index(b'\xd0\x89') + 1]
        self.assertEqual(ht.host_devices(cut.decode(errors='replace')), set())

    def host_step(self, output, flash_why='', console_error=None):
        board = roster('raspberry_pi_pico_w')
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
        tty = {'side_effect': console_error} if console_error else {'return_value': '/dev/ttyACM20'}
        with mock.patch.object(E, 'console_tty', **tty), \
                mock.patch.object(E, 'flash', lambda *a: calls.append('flash') or flash_why), \
                mock.patch.object(serial, 'Serial', Port), mock.patch.object(ht.time, 'monotonic', tick):
            row = E.step('raspberry_pi_pico_w', board, {'elf': 'x.elf'}, 'host_device_info', self.dir, mock.Mock())
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
        self.assertNotIn('console', row)

    def test_no_console_route_fails_before_the_flash(self):
        row, calls = self.host_step(HOST_OUTPUT, console_error=RuntimeError('probe x: want one on the bus'))
        self.assertEqual((row['pass'], row['detail'], calls), (False, 'console: probe x: want one on the bus', []))


class Cdc(Base):
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
        with mock.patch.object(E, 'node', return_value='/dev/ttyACM9'), \
                mock.patch.object(ht.time, 'monotonic', lambda: now[0]), \
                mock.patch.object(serial, 'Serial', self.cdc_port(seen, stall, now)):
            ok, detail = ht.test_cdc({}, 'ab', now[0] + 5, None, self.dir, roster('raspberry_pi_pico'))
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


class Hid(Base):
    def run_hid(self, reply, deadline=5.0):
        written, closed = [], []
        fake_os = argparse.Namespace(O_RDWR=os.O_RDWR, O_NONBLOCK=os.O_NONBLOCK, open=lambda p, f: 7,
                                     write=lambda fd, data: written.append(data) or len(data),
                                     read=lambda fd, n: reply, close=closed.append, path=os.path)
        with mock.patch.object(E, 'node', return_value='/dev/hidraw3') as node, mock.patch.object(ht, 'os', fake_os), \
                mock.patch.object(ht.select, 'select', lambda r, w, x, t: (r if reply is not None else [], [], [])):
            ok, detail = ht.test_hid({}, 'a1b2c3d4', time.monotonic() + deadline, None, self.dir, {})
        self.assertEqual((node.call_args[0][1], closed), ('*:*/*/hidraw/hidraw*', [7]))
        return ok, detail, written

    def test_the_report_echoes(self):
        report = bytes.fromhex('a1b2c3d4') * 16
        ok, detail, written = self.run_hid(report)
        self.assertEqual((ok, detail), (True, '/dev/hidraw3: echoed 64-byte report'))
        self.assertEqual(written, [b'\x00' + report])   # report ID 0, then the 64 bytes

    def test_another_report_or_none_fails(self):
        ok, detail, _ = self.run_hid(bytes(64))
        self.assertFalse(ok)
        self.assertIn('got ' + '00' * 64, detail)
        ok, detail, _ = self.run_hid(None, deadline=0.05)
        self.assertEqual((ok, detail), (False, '/dev/hidraw3: no input report within the test timeout'))


def fat12_image(readme):
    """The msc_ramdisk layout: 512-byte sectors, one reserved, one FAT of one sector, 16 root entries."""
    img = bytearray(ht.MSC_DISK_BYTES)
    img[11:13], img[13], img[14:16], img[16] = (512).to_bytes(2, 'little'), 1, (1).to_bytes(2, 'little'), 1
    img[17:19], img[22:24] = (16).to_bytes(2, 'little'), (1).to_bytes(2, 'little')
    img[1024:1056] = b'README  TXT' + bytes(15) + (2).to_bytes(2, 'little') + len(readme).to_bytes(4, 'little')
    img[1024 + 512:1024 + 512 + len(readme)] = readme
    return bytes(img)


class Msc(Base):
    def test_fat12_reads_the_sketch_image_layout(self):
        img = fat12_image(b'hello')
        self.assertEqual(ht.fat12_file(img, b'README  TXT'), b'hello')
        self.assertIsNone(ht.fat12_file(img, b'OTHER   TXT'))

    def run_msc(self, sectors, image, returncode=0):
        sysfs = self.dir / 'sys/class/block/sdz'
        sysfs.mkdir(parents=True, exist_ok=True)
        (sysfs / 'size').write_text(f'{sectors}\n')
        real = Path

        def path(p, *rest):
            return real(self.dir / str(p).lstrip('/'), *rest) if str(p).startswith('/sys/') else real(p, *rest)
        tracker = mock.Mock()

        def read_block(argv, timeout):
            self.assertEqual(argv[1:4], [str(ht.SCRIPT), 'read-block', '/dev/sdz'])
            if image is not None:
                real(argv[4]).write_bytes(image)
            return mock.Mock(returncode=returncode, stdout=b'read: I/O error')
        tracker.run.side_effect = read_block
        with mock.patch.object(E, 'node', return_value='/dev/sdz'), mock.patch.object(ht, 'Path', path):
            ok, detail = ht.test_msc({}, 'ab12', time.monotonic() + 5, tracker, self.dir, {})
        return ok, detail, tracker

    def test_the_ramdisk_readme_matches(self):
        ok, detail, tracker = self.run_msc(16, fat12_image(ht.MSC_README))
        self.assertEqual((ok, detail), (True, '/dev/sdz: README.TXT matches'))
        tracker.check.assert_called_once_with()   # an unproven read-block child stops the board

    def test_size_content_and_read_failures(self):
        ok, detail, tracker = self.run_msc(32, None)
        self.assertEqual((ok, detail), (False, '/dev/sdz: 16384 bytes, the ramdisk has 8192'))
        tracker.run.assert_not_called()
        ok, detail, _ = self.run_msc(16, fat12_image(b'other'))
        self.assertEqual((ok, detail), (False, "/dev/sdz: README.TXT b'other'"))
        ok, detail, _ = self.run_msc(16, None, returncode=1)
        self.assertEqual((ok, detail), (False, '/dev/sdz: read failed (read: I/O error)'))

    def test_read_block_copies_the_ramdisk(self):
        src, dest = self.dir / 'dev', self.dir / 'out.img'
        src.write_bytes(b'\x01' * ht.MSC_DISK_BYTES + b'\x02' * 512)
        self.assertEqual(ht.main(['read-block', str(src), str(dest)]), 0)
        self.assertEqual(dest.read_bytes(), b'\x01' * ht.MSC_DISK_BYTES)


if __name__ == '__main__':
    unittest.main()
