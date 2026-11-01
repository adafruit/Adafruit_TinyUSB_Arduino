#!/usr/bin/env python3
"""The build contract: check_build.py's selection rules, ci-arduino filters, provenance checks and HIL record
refusals. arduino-cli is stubbed; a real build is evidence only from a run of the script itself."""
import contextlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / '.claude' / 'skills' / 'build' / 'scripts' / 'check_build.py'
spec = importlib.util.spec_from_file_location('check_build', SCRIPT)
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = cb.main(argv)
        except SystemExit as e:
            code = e.code
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def elf32(segments, sections=None):
    """A minimal little-endian ELF32: PT_LOAD/other segments (type, paddr, filesz), each at file offset
    0x1000 * (index + 1), and allocated PROGBITS sections (segment index, offset in it, size); by default one
    section spanning each segment."""
    if sections is None:
        sections = [(i, 0, n) for i, (t, _, n) in enumerate(segments) if t == 1 and n]
    phoff, n = 52, len(segments)
    offsets = [0x1000 * (i + 1) for i in range(n)]
    shoff = 0x1000 * (n + 1)
    head = bytearray(52)
    head[:6] = b'\x7fELF\x01\x01'
    struct.pack_into('<II', head, 28, phoff, shoff)
    struct.pack_into('<HHHH', head, 42, 32, n, 40, len(sections))
    ph = b''.join(struct.pack('<IIIIIIII', t, off, paddr, paddr, filesz, filesz, 5, 4)
                  for (t, paddr, filesz), off in zip(segments, offsets))
    body = bytearray(bytes(head) + ph)
    body += bytes(shoff - len(body))
    for seg, delta, size in sections:
        body += struct.pack('<IIIIIIIIII', 0, 1, 0x2, 0, offsets[seg] + delta, size, 0, 0, 4, 0)
    return bytes(body)


def ihex(start, data):
    lines = [f':02000004{start >> 16:04X}{(-(2 + 4 + (start >> 24) + ((start >> 16) & 0xff))) & 0xff:02X}']
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        addr = (start + off) & 0xffff
        rec = bytes([len(chunk), addr >> 8, addr & 0xff, 0]) + chunk
        lines.append(':' + (rec + bytes([(-sum(rec)) & 0xff])).hex().upper())
    lines.append(':00000001FF')
    return '\n'.join(lines) + '\n'


class Tables(unittest.TestCase):
    def test_representatives_and_families_name_known_boards(self):
        for family, boards in cb.REPRESENTATIVES.items():
            for b in boards:
                self.assertEqual(cb.BOARDS[b]['family'], family)
        self.assertEqual(set(cb.PORT_FAMILY.values()) | set(cb.PORTABLE_FAMILY.values()), set(cb.REPRESENTATIVES))

    def test_ci_matrix_is_in_the_table(self):
        wf = (ROOT / '.github/workflows/githubci.yml').read_text()
        matrix = [ln.strip().strip("-' ") for ln in wf.split('arduino-platform:')[1].split('steps:')[0].splitlines()
                  if ln.strip().startswith('- ')]
        self.assertTrue(matrix)
        for platform in matrix:
            self.assertIn(platform, cb.BOARDS)

    def test_rig_boards_have_a_span_and_filters(self):
        for name in ('raspberry_pi_pico', 'adafruit_fruit_jam', 'metro_m4_express'):
            self.assertIn('span', cb.BOARDS[name])
            self.assertIn(cb.BOARDS[name]['filters'], cb.BOARDS)

    def test_hil_sketches_exist(self):
        for e in cb.HIL_SKETCHES:
            self.assertIn(e, cb.examples())

    def test_every_portable_and_port_dir_is_classified(self):
        for d in (ROOT / 'src/portable').glob('*/*'):
            effect, _, _ = cb.classify(f'src/portable/{d.parent.name}/{d.name}/x.c')
            self.assertIn(effect, ('family', 'shared', 'uncovered'))


class Selection(unittest.TestCase):
    def test_rules(self):
        cases = {
            'src/arduino/ports/samd/Adafruit_TinyUSB_samd.cpp': ('family', 'samd'),
            'src/arduino/ports/stm32/x.cpp': ('uncovered', None),
            'src/portable/raspberrypi/rp2040/dcd_rp2040.c': ('family', 'rp2040'),
            'src/portable/analog/max3421/hcd_max3421.c': ('shared', None),
            'src/portable/st/stm32_fsdev/dcd.c': ('uncovered', None),
            'src/class/cdc/cdc_device.c': ('shared', None),
            'src/arduino/Adafruit_USBD_CDC.h': ('uncovered', None),
            'examples/CDC/serial_echo/serial_echo.ino': ('example', 'examples/CDC/serial_echo'),
            'examples/HID/hid_generic_inout/hid_test.py': ('example', 'examples/HID/hid_generic_inout'),
            '.claude/skills/build/scripts/check_build.py': ('tests', 'build'),
            '.claude/skills/hil-contract/scripts/hil_test.py': ('hil', None),
            'test/hil/ci.json': ('hil', None),
            '.claude/test/test_hil.py': ('tests', 'hil'),
            'README.md': ('nothing', None),
            'library.properties': ('nothing', None),
            'weird/file.c': ('uncovered', None),
        }
        for path, (effect, detail) in cases.items():
            got = cb.classify(path)
            self.assertEqual(got[:2], (effect, detail), path)

    def test_family_change_builds_that_familys_representatives(self):
        sel = cb.select(['src/arduino/ports/rp2040/Adafruit_TinyUSB_rp2040.cpp'])
        self.assertEqual(set(sel['builds']), set(cb.REPRESENTATIVES['rp2040']))

    def test_shared_change_builds_every_representative(self):
        sel = cb.select(['src/device/usbd.c'], only_examples=['examples/CDC/serial_echo'])
        self.assertEqual(set(sel['builds']), {b for bs in cb.REPRESENTATIVES.values() for b in bs
                                              if cb.eligible(b, 'examples/CDC/serial_echo')})

    def test_example_change_honours_ci_filters(self):
        sel = cb.select(['examples/CDC/serial_echo/serial_echo.ino'])
        self.assertNotIn('pico_rp2040_tinyusb_host', sel['builds'])   # .skip.txt
        self.assertIn('raspberry_pi_pico', sel['builds'])
        sel = cb.select(['examples/MassStorage/msc_internal_flash_samd/msc_internal_flash_samd.ino'])
        self.assertEqual(set(sel['builds']), {'metro_m0_tinyusb'})    # .metro_m0_tinyusb.test.only

    def test_skip_txt_lines(self):
        self.assertFalse(cb.eligible('CH32V20x_EVT', 'examples/WebUSB/webusb_rgb'))
        self.assertTrue(cb.eligible('metro_m4_express', 'examples/WebUSB/webusb_rgb'))

    def test_uncovered_and_hil(self):
        sel = cb.select(['src/arduino/ports/stm32/x.cpp', 'test/hil/ci.json'], roster=['raspberry_pi_pico'])
        self.assertEqual([u['path'] for u in sel['uncovered']], ['src/arduino/ports/stm32/x.cpp'])
        self.assertEqual(sel['hil'], {'boards': ['raspberry_pi_pico'], 'full': True, 'uncovered': []})
        self.assertEqual(sel['tests'], ['hil', 'hil_remote'])

    def test_hil_selection_names_families_the_roster_lacks(self):
        sel = cb.select(['src/arduino/ports/nrf/x.cpp', 'src/arduino/ports/samd/y.cpp'], roster=['metro_m4_express'])
        self.assertEqual(sel['hil']['boards'], ['metro_m4_express'])
        self.assertEqual([u['family'] for u in sel['hil']['uncovered']], ['nrf'])

    def test_select_only_exit_and_shape(self):
        code, out = run_main(['--select-only', '--scope', 'README.md'])
        self.assertEqual(code, 0)
        self.assertEqual(out['build']['builds'], {})


class Images(unittest.TestCase):
    def test_elf_spans_skip_empty_and_merge_adjacent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.elf'
            p.write_bytes(elf32([(1, 0x4000, 0x100), (1, 0x4100, 0x10), (1, 0x20000000, 0), (6, 0, 0x40)]))
            self.assertEqual(cb.elf_load_spans(p), [(0x4000, 0x4110)])
            p.write_bytes(elf32([(1, 0x0, 0x4020)], [(0, 0x4000, 0x20)]))
            self.assertEqual((cb.elf_load_spans(p), cb.elf_section_spans(p)), ([(0, 0x4020)], [(0x4000, 0x4020)]))

    def test_hex_runs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.hex'
            p.write_text(ihex(0x4000, bytes(range(40))))
            self.assertEqual(cb.hex_runs(p), [(0x4000, bytes(range(40)))])
            p.write_text(':0100000000FE\n')
            with self.assertRaises(ValueError):
                cb.hex_runs(p)

    def test_vid_pid_from_the_library_compile_command(self):
        def ids(*flags):
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / 'compile_commands.json').write_text(json.dumps([
                    {'file': '/x/core.cpp', 'arguments': ['-DUSB_VID=0x1111']},
                    {'file': '/lib/src/arduino/Adafruit_USBD_Device.cpp', 'arguments': ['g++', *flags]}]))
                return cb.vid_pid(d)
        self.assertEqual(ids('-DUSB_VID=0x239A', '-DUSB_PID=0x8020'), '239a:8020')      # samd
        self.assertEqual(ids('-DUSBD_VID=0x2e8a', '-DUSBD_PID=0x000a'), '239a:cafe')    # rp2040: library fallback
        self.assertEqual(ids('-DBOARD_VENDORID=0x2341', '-DBOARD_PRODUCTID=0x005e'), '2341:005e')
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(cb.vid_pid(d))


class Provenance(unittest.TestCase):
    def test_foreign_header_detected_and_compared(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / 'core/libraries/Adafruit_TinyUSB_Arduino'
            (lib / 'src/arduino').mkdir(parents=True)
            (lib / 'library.properties').write_text(f'name={cb.LIBRARY_NAME}\n')
            same = lib / 'src/arduino/Adafruit_USBD_CDC.h'
            same.write_bytes((ROOT / 'src/arduino/Adafruit_USBD_CDC.h').read_bytes())
            other = lib / 'src/arduino/Adafruit_TinyUSB_API.h'
            other.write_text('// drifted\n')
            unrelated = Path(d) / 'core/cores/x.h'
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text('')
            spaced = Path(d) / 'my core/libraries/Adafruit_TinyUSB_Arduino'
            (spaced / 'src').mkdir(parents=True)
            (spaced / 'library.properties').write_text(f'name={cb.LIBRARY_NAME}\n')
            (spaced / 'src/tusb.h').write_text('// drifted\n')
            build = Path(d) / 'b'
            build.mkdir()
            (build / 'main.cpp.d').write_text(f'main.cpp.o: \\\n {same} \\\n {other} {unrelated} '
                                              f'{ROOT / "src/tusb.h"} ' + str(spaced / 'src/tusb.h').replace(' ', '\\ ') + '\n')
            got = {h['counterpart']: h['matches'] for h in cb.foreign_headers(build)}
            self.assertEqual(got, {'src/arduino/Adafruit_USBD_CDC.h': True, 'src/arduino/Adafruit_TinyUSB_API.h': False,
                                   'src/tusb.h': False})

    def fake_compile(self, install_dir, success=True, err=''):
        out = {'success': success, 'compiler_err': err, 'builder_result': {
            'used_libraries': [{'name': cb.LIBRARY_NAME, 'install_dir': str(install_dir)}],
            'board_platform': {'id': 'adafruit:samd', 'version': '1.7.13', 'install_dir': '/x'},
            'build_properties': []}}
        return mock.Mock(returncode=0 if success else 1, stdout=json.dumps(out), stderr='')

    def test_library_from_another_checkout_fails(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cb, 'ROOT', Path(d)), \
                mock.patch.object(cb.subprocess, 'run', return_value=self.fake_compile('/home/x/Arduino/libraries/A')):
            row = cb.compile_sketch('metro_m4_express', 'examples/CDC/serial_echo', Path(d) / 'b', [])
        self.assertEqual(row['status'], 'failed')
        self.assertIn('not this checkout', row['firstError'])

    def test_missing_arduino_cli_is_an_error(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cb, 'ROOT', Path(d)), \
                mock.patch.object(cb.subprocess, 'run', side_effect=FileNotFoundError('arduino-cli')):
            row = cb.compile_sketch('metro_m4_express', 'examples/CDC/serial_echo', Path(d) / 'b', [])
        self.assertEqual((row['status'], row['remedy']), ('error', 'install arduino-cli on PATH'))

    def test_ok_build_row(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cb, 'ROOT', Path(d)), \
                mock.patch.object(cb, 'vid_pid', return_value='239a:8020'), \
                mock.patch.object(cb.subprocess, 'run', return_value=self.fake_compile(d)):
            row = cb.compile_sketch('metro_m4_express', 'examples/CDC/serial_echo', Path(d) / 'b', [])
        self.assertEqual((row['status'], row['vidPid'], row['foreignHeaders']), ('ok', '239a:8020', []))

    def test_missing_core_is_an_error_with_remedy(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cb, 'ROOT', Path(d)), mock.patch.object(
                cb.subprocess, 'run', return_value=self.fake_compile(d, False, 'Error resolving FQBN: platform x')):
            row = cb.compile_sketch('cpb', 'examples/CDC/serial_echo', Path(d) / 'b', [])
        self.assertEqual(row['status'], 'error')
        self.assertEqual(row['remedy'], 'arduino-cli core install adafruit:nrf52')


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.roster = self.root / 'test/hil/ci.json'
        self.roster.parent.mkdir(parents=True)
        self.flasher('jlink')
        self.patches = [mock.patch.object(cb, 'ROOT', self.root), mock.patch.object(cb, 'tool_version',
                                                                                    return_value='1.5.1'),
                        mock.patch.object(cb, 'platform_git', return_value=None)]
        for p in self.patches:
            p.start()

    def flasher(self, name):
        self.roster.write_text(json.dumps({'boards': [{'name': 'metro_m4_express', 'flasher': {'name': name}}]}))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def row(self, segments, hexdata=None, headers=(), sections=None):
        b = self.root / 'build/hil/metro_m4_express/serial_echo'
        b.mkdir(parents=True, exist_ok=True)
        (b / 'serial_echo.ino.elf').write_bytes(elf32([(1, s, n) for s, n in segments], sections))
        if hexdata:
            (b / 'serial_echo.ino.hex').write_text(ihex(*hexdata))
        return {'example': 'examples/CDC/serial_echo', 'buildDir': 'build/hil/metro_m4_express/serial_echo',
                'vidPid': '239a:8020', 'foreignHeaders': list(headers),
                'platform': {'id': 'adafruit:samd', 'version': '1.7.13', 'install_dir': '/x'}}

    def samd_row(self, **kw):
        """The SAMD core's layout: one segment from 0 (ELF headers, padding) holding .text at 0x4000."""
        return self.row([(0x0, 0x4020)], (0x4000, bytes(32)), sections=[(0, 0x4000, 0x20)], **kw)

    def record(self, row, experimental=False):
        args = mock.Mock(experimental=experimental, build_property=[], record=str(self.root / 'build/r.json'))
        ident = {'head': 'abc', 'dirty': False, 'diffSha256': 'd', 'status': ''}
        with mock.patch.object(cb, 'source_identity', return_value=(ident, '')):
            return cb.make_record(args, 'metro_m4_express', [row], ident, '', self.roster)

    def test_jlink_board_records_the_hex(self):
        rec, err = self.record(self.samd_row())
        self.assertIsNone(err)
        s = rec['sketches']['serial_echo']
        self.assertEqual((s['image'], s['loadSpans']), ('hex', [[0x4000, 0x4020]]))
        self.assertEqual(rec['roster']['path'], 'test/hil/ci.json')

    def test_elf_flash_over_the_bootloader_refused(self):
        self.flasher('openocd')
        _, err = self.record(self.samd_row())
        self.assertIn('the ELF writes', err)
        self.assertIn('outside the board span', err)

    def test_any_segment_out_of_span_refused(self):
        self.flasher('openocd')
        _, err = self.record(self.row([(0x4000, 0x80000), (0x5000, 0x20)]))
        self.assertIn('outside the board span', err)

    def test_hex_disagreeing_with_elf_sections_refused(self):
        _, err = self.record(self.row([(0x4000, 32)], (0x4000, bytes(16))))
        self.assertIn('differ from the ELF sections', err)

    def test_jlink_board_without_hex_refused(self):
        _, err = self.record(self.row([(0x4000, 32)]))
        self.assertIn('flashes the HEX', err)

    def test_drifted_core_header_refused_unless_experimental(self):
        hdr = [{'path': '/core/x.h', 'matches': False}]
        _, err = self.record(self.samd_row(headers=hdr))
        self.assertIn('differ from this checkout', err)
        rec, err = self.record(self.samd_row(headers=hdr), experimental=True)
        self.assertIsNone(err)
        self.assertTrue(rec['experimental'])
        self.assertTrue((self.root / 'build/r.patch').exists())

    def test_experimental_record_into_a_new_dir(self):
        args = mock.Mock(experimental=True, build_property=[], record=str(self.root / 'build/new/x.json'))
        ident = {'head': 'abc', 'dirty': True, 'diffSha256': 'd', 'status': ' M a'}
        with mock.patch.object(cb, 'source_identity', return_value=(ident, 'diff')):
            rec, err = cb.make_record(args, 'metro_m4_express', [self.samd_row()], ident, 'diff', self.roster)
        self.assertIsNone(err)
        self.assertEqual((self.root / 'build/new/x.patch').read_text(), 'diff')

    def test_source_change_during_build_refused(self):
        args = mock.Mock(experimental=False, build_property=[], record=str(self.root / 'build/r.json'))
        before = {'head': 'abc', 'dirty': False, 'diffSha256': 'd', 'status': ''}
        after = dict(before, status=' M src/tusb.c\n', diffSha256='e')
        with mock.patch.object(cb, 'source_identity', return_value=(after, '')):
            _, err = cb.make_record(args, 'metro_m4_express', [self.samd_row()], before, '', self.roster)
        self.assertIn('changed while building', err)


class Usage(unittest.TestCase):
    def test_refusals(self):
        for argv in (['--worktree', '--scope', 'x'], ['--board', 'nope'], ['--board', 'raspberry_pi_pico', '--record',
                     'build/x.json'], ['--board', 'raspberry_pi_pico', '--experimental'],
                     ['--select-only', '--board', 'raspberry_pi_pico'], ['--board', 'raspberry_pi_pico',
                                                                          '--example', 'examples/nope']):
            code, out = run_main(argv)
            self.assertEqual(code, 2, argv)
            self.assertFalse(out['pass'])

    def test_record_with_properties_must_be_experimental(self):
        code, out = run_main(['--board', 'metro_m4_express', '--shared', '--hil-config', 'test/hil/ci.json',
                              '--record', 'build/hil/x.json', '--build-property', 'a=b'])
        self.assertEqual(code, 2)
        self.assertIn('--experimental', out['error'])


if __name__ == '__main__':
    unittest.main()
