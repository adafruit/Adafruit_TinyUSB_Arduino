#!/usr/bin/env python3
"""The build contract's profile: check_build.py's board tables against CI's matrix and ci-arduino's filters, its
source rules and the selections they make through the pinned agentrc arduino_build.py, its dependencies and its
VID:PID adapter. The mechanics (compiles, provenance, images, the HIL record) are agentrc's and tested there. Nothing
is compiled; a real build is evidence only from a run of the script itself."""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'build' / 'scripts'))
import check_build as cb  # noqa: E402

eng = cb.engine()   # the pinned agentrc's arduino_build bound to this profile: a missing pin fails here


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = cb.main(argv)
        except SystemExit as e:
            code = e.code
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


class Tables(unittest.TestCase):
    def test_representatives_and_families_name_known_boards(self):
        for family, boards in cb.REPRESENTATIVES.items():
            for b in boards:
                self.assertEqual(cb.BOARDS[b]['family'], family)
        self.assertEqual(set(cb.PORTABLE_FAMILY.values()), set(cb.REPRESENTATIVES))
        ports = {d.name for d in (ROOT / 'src/arduino/ports').iterdir() if d.is_dir()}
        self.assertEqual(ports - set(cb.REPRESENTATIVES), {'stm32'})   # no CI platform builds it: uncovered

    def test_ci_matrix_is_in_the_table(self):
        wf = (ROOT / '.github/workflows/githubci.yml').read_text()
        matrix = [ln.strip().strip("-' ") for ln in wf.split('arduino-platform:')[1].split('steps:')[0].splitlines()
                  if ln.strip().startswith('- ')]
        self.assertTrue(matrix)
        for platform in matrix:
            self.assertIn(platform, cb.BOARDS)

    def test_dependencies_are_cis_install_list(self):
        wf = (ROOT / '.github/workflows/githubci.yml').read_text()
        libs = re.findall(r'\\"([^"\\]+)\\"', re.search(r'ARDUINO_LIBS: (.*)', wf).group(1))
        self.assertTrue(libs)
        self.assertEqual(sorted(eng.declared_dependencies()), sorted(libs))

    def test_rig_boards_have_a_span_and_filters(self):
        roster = {b['name'] for b in json.loads((ROOT / 'test/hil/ci.json').read_text())['boards']}
        self.assertEqual({b for b, v in cb.BOARDS.items() if 'span' in v}, roster)
        for name in roster:
            self.assertIn(cb.BOARDS[name]['filters'], cb.BOARDS)
            self.assertIn(eng.role(name), cb.HIL_SKETCHES)
            fixture = cb.BOARDS[name].get('fixture')
            self.assertTrue(fixture is None or (ROOT / fixture).is_file())

    def test_smoke_boards_cover_each_image_kind(self):
        flashers = {b['name']: b['flasher']['name'] for b in json.loads((ROOT / 'test/hil/ci.json').read_text())
                    ['boards']}
        self.assertEqual(sorted(eng.IMAGE_KIND[flashers[b]] for b in cb.SMOKE_BOARDS), ['elf', 'esptool', 'hex'])

    def test_contract_files_and_tests_exist(self):
        for f in (*cb.CONTRACT_FILES, *cb.CONTRACT_TESTS.values()):
            self.assertTrue((ROOT / f).is_file(), f)
        self.assertIn('test/hil/agentrc.json', cb.CONTRACT_FILES)   # a record binds the pin
        self.assertEqual(cb.LIBRARY_NAME, re.search(r'^name=(.*)$', (ROOT / 'library.properties').read_text(),
                                                    re.M).group(1))

    def test_hil_sketches_exist_and_are_eligible_on_their_boards(self):
        for role, sketches in cb.HIL_SKETCHES.items():
            for e in sketches:
                self.assertIn(e, eng.examples())
        for b in ('raspberry_pi_pico_w', 'raspberry_pi_pico2'):
            self.assertTrue(all(eng.eligible(b, e) for e in eng.hil_sketches(b)))

    def test_every_portable_and_port_dir_is_classified(self):
        for d in (ROOT / 'src/portable').glob('*/*'):
            effect, _, _ = eng.classify(f'src/portable/{d.parent.name}/{d.name}/x.c')
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
            'test/hil/agentrc.json': ('hil', None),
            'test/hil/esp32_ts3usb30.cpp': ('fixture', 'test/hil/esp32_ts3usb30.cpp'),
            '.claude/test/test_hil.py': ('tests', 'hil'),
            '.claude/test/test_hil_remote.py': ('tests', 'hil_remote'),
            'README.md': ('nothing', None),
            'library.properties': ('nothing', None),
            '.coderabbit.yaml': ('nothing', None),
            'weird/file.c': ('uncovered', None),
        }
        for path, (effect, detail) in cases.items():
            self.assertEqual(eng.classify(path)[:2], (effect, detail), path)

    def test_family_change_builds_that_familys_representatives(self):
        sel = eng.select(['src/arduino/ports/rp2040/Adafruit_TinyUSB_rp2040.cpp'])
        self.assertEqual(set(sel['builds']), set(cb.REPRESENTATIVES['rp2040']))

    def test_shared_change_builds_every_representative(self):
        sel = eng.select(['src/device/usbd.c'], only_examples=['examples/CDC/serial_echo'])
        self.assertEqual(set(sel['builds']), {b for bs in cb.REPRESENTATIVES.values() for b in bs
                                              if eng.eligible(b, 'examples/CDC/serial_echo')})

    def test_example_change_honours_ci_filters(self):
        sel = eng.select(['examples/CDC/serial_echo/serial_echo.ino'])
        self.assertNotIn('pico_rp2040_tinyusb_host', sel['builds'])   # .skip.txt
        self.assertIn('raspberry_pi_pico', sel['builds'])
        sel = eng.select(['examples/MassStorage/msc_internal_flash_samd/msc_internal_flash_samd.ino'])
        self.assertEqual(set(sel['builds']), {'metro_m0_tinyusb'})    # .metro_m0_tinyusb.test.only

    def test_example_eligible_on_no_board_is_uncovered(self):
        sketch = 'examples/MassStorage/msc_esp32_file_browser'    # .funhouse/.magtag/.metroesp32s2.test.only
        self.assertFalse(any(eng.eligible(b, sketch) for b in cb.BOARDS))
        path = f'{sketch}/msc_esp32_file_browser.ino'
        sel = eng.select([path])
        self.assertEqual(sel['builds'], {})
        self.assertEqual([u['path'] for u in sel['uncovered']], [path])

    def test_skip_txt_lines(self):
        self.assertFalse(eng.eligible('CH32V20x_EVT', 'examples/WebUSB/webusb_rgb'))
        self.assertTrue(eng.eligible('metro_m4_express', 'examples/WebUSB/webusb_rgb'))

    def test_uncovered_and_hil(self):
        sel = eng.select(['src/arduino/ports/stm32/x.cpp', 'test/hil/ci.json'], roster=['raspberry_pi_pico'])
        self.assertEqual([u['path'] for u in sel['uncovered']], ['src/arduino/ports/stm32/x.cpp'])
        self.assertEqual(sel['hil'], {'boards': ['raspberry_pi_pico'], 'full': True, 'uncovered': []})
        self.assertEqual(sel['tests'], ['hil', 'hil_remote'])

    def test_hil_selection_names_families_the_roster_lacks(self):
        sel = eng.select(['src/arduino/ports/nrf/x.cpp', 'src/arduino/ports/samd/y.cpp'], roster=['metro_m4_express'])
        self.assertEqual(sel['hil']['boards'], ['metro_m4_express'])
        self.assertEqual([u['family'] for u in sel['hil']['uncovered']], ['nrf'])

    def test_hil_sketch_change_selects_only_boards_of_its_role(self):
        roster = ['raspberry_pi_pico', 'raspberry_pi_pico_w', 'espressif_s3_devkitm']
        sel = eng.select(['examples/Host/Simple/host_device_info/host_device_info.ino'], roster=roster)
        self.assertEqual(sel['hil']['boards'], ['raspberry_pi_pico_w'])
        sel = eng.select(['examples/CDC/serial_echo/serial_echo.ino'], roster=roster)
        self.assertEqual(sel['hil']['boards'], ['espressif_s3_devkitm', 'raspberry_pi_pico'])

    def test_fixture_change_builds_and_selects_its_boards(self):
        sel = eng.select(['test/hil/esp32_ts3usb30.cpp'], roster=['raspberry_pi_pico', 'espressif_p4_function_ev'])
        sketches = list(cb.HIL_SKETCHES['device'])
        self.assertEqual(sel['builds'], {'espressif_p4_function_ev': sketches, 'espressif_s3_devkitm': sketches})
        self.assertEqual(sel['hil']['boards'], ['espressif_p4_function_ev'])

    def test_build_contract_change_smoke_builds_each_image_kind(self):
        sel = eng.select(['.claude/skills/build/scripts/check_build.py'])
        self.assertEqual(sel['builds'], {b: ['examples/CDC/serial_echo'] for b in
                                         ('espressif_s3_devkitm', 'metro_m4_express', 'raspberry_pi_pico')})

    def test_select_only_exit_and_shape(self):
        code, out = run_main(['--select-only', '--scope', 'README.md'])
        self.assertEqual(code, 0)
        self.assertEqual(out['build']['builds'], {})
        code, out = run_main(['--select-only', '--scope', 'test/hil/ci.json', '--hil-config',
                              str(ROOT / 'test/hil/ci.json')])
        self.assertEqual((code, out['hil']['full']), (0, True))

    def test_a_missing_pin_names_the_fetch(self):
        with mock.patch.object(cb, 'E', None), \
                mock.patch.object(cb.agentrc_pin, 'checkout', side_effect=cb.agentrc_pin.PinError('agentrc pin: gone')):
            code, out = run_main(['--select-only', '--scope', 'README.md'])
        self.assertEqual((code, out['error'], out['remedy']), (2, 'agentrc pin: gone', cb.agentrc_pin.FETCH_HINT))


class VidPid(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as v:
            variant = Path(v) / 'variants/esp32s3'
            variant.mkdir(parents=True)
            (variant / 'pins_arduino.h').write_text('#define USB_VID 0x303a\n#define USB_PID 0x1001\n')
            self.assertEqual(ids('-DARDUINO_ARCH_ESP32', f'-I{variant}'), '303a:1001')
            self.assertEqual(ids(f'-I{variant}'), '239a:cafe')   # only ESP32 builds take the variant's
            spaced = Path(v) / 'my core/variants/esp32s3'
            spaced.mkdir(parents=True)
            (spaced / 'pins_arduino.h').write_text((variant / 'pins_arduino.h').read_text())
            self.assertEqual(ids('-DARDUINO_ARCH_ESP32', f'-I{spaced}'), '303a:1001')
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / 'compile_commands.json').write_text(json.dumps([
                    {'file': '/lib/src/arduino/Adafruit_USBD_Device.cpp',
                     'command': f'g++ -DARDUINO_ARCH_ESP32 "-I{spaced}" -c x.cpp'}]))
                self.assertEqual(cb.vid_pid(d), '303a:1001')
            (variant / 'pins_arduino.h').write_text('#define LED_BUILTIN 2\n')
            self.assertEqual(ids('-DARDUINO_ARCH_ESP32', f'-I{variant}'), '239a:cafe')   # esp32p4: fallback


if __name__ == '__main__':
    unittest.main()
