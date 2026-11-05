#!/usr/bin/env python3
"""Build this library's example sketches with arduino-cli: the project's build contract (SKILL.md).

  check_build.py (--scope PATH... | --base REF [--worktree] | --diff-file FILE) [--example E]... [-j N]
  check_build.py --board B... [--example E]... [--shared] [--build-property K=V]...
  check_build.py --board B --shared --record FILE --hil-config ROSTER [--experimental --build-property K=V...]
  check_build.py --select-only (--scope ... | --base REF [--worktree] | --diff-file FILE) [--hil-config ROSTER]

stdout ends with one JSON line. Exit 0 pass, 1 a build or test failed, 2 usage or a missing
prerequisite ("error" names the remedy), 3 an uncovered path. 2 outranks 1, 1 outranks 3.

This file is the library's profile for agentrc's arduino_build.py (the `arduino` skill), loaded from the agentrc
commit test/hil/agentrc.json pins; the mechanics are there.
"""

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'))
import agentrc_pin  # noqa: E402

LIBRARY_NAME = 'Adafruit TinyUSB Library'

# CI's matrix (.github/workflows/githubci.yml), FQBNs from adafruit/ci-arduino all_platforms.py, plus the rig boards
# named as in tinyusb's HIL farm. `filters` is the ci-arduino platform whose .test.only/.skip files apply.
BOARDS = {
    'feather_esp32_v2': {'fqbn': 'esp32:esp32:adafruit_feather_esp32_v2', 'family': 'esp32'},
    'feather_esp32s2': {'fqbn': 'esp32:esp32:adafruit_feather_esp32s2', 'family': 'esp32'},
    'feather_esp32s3': {'fqbn': 'esp32:esp32:adafruit_feather_esp32s3_nopsram', 'family': 'esp32'},
    'esp32p4': {'fqbn': 'esp32:esp32:esp32p4:JTAGAdapter=default,PSRAM=disabled,USBMode=default,CDCOnBoot=cdc,'
                        'MSCOnBoot=default,DFUOnBoot=default,UploadMode=default,PartitionScheme=default,FlashMode=qio,'
                        'FlashFreq=80,FlashSize=4M,UploadSpeed=921600,DebugLevel=none,EraseFlash=none',
                'family': 'esp32'},
    'cpb': {'fqbn': 'adafruit:nrf52:cplaynrf52840:softdevice=s140v6,debug=l0', 'family': 'nrf'},
    'nrf52840': {'fqbn': 'adafruit:nrf52:feather52840:softdevice=s140v6,debug=l0', 'family': 'nrf'},
    'feather_rp2040_tinyusb': {'fqbn': 'rp2040:rp2040:adafruit_feather:flash=8388608_0,freq=120,dbgport=Disabled,'
                                       'dbglvl=None,usbstack=tinyusb', 'family': 'rp2040'},
    'pico_rp2040_tinyusb_host': {'fqbn': 'rp2040:rp2040:rpipico:flash=2097152_0,freq=120,dbgport=Disabled,'
                                         'dbglvl=None,usbstack=tinyusb_host', 'family': 'rp2040'},
    'metro_m0_tinyusb': {'fqbn': 'adafruit:samd:adafruit_metro_m0:usbstack=tinyusb', 'family': 'samd'},
    'metro_m4_tinyusb': {'fqbn': 'adafruit:samd:adafruit_metro_m4:speed=120,usbstack=tinyusb', 'family': 'samd'},
    'CH32V20x_EVT': {'fqbn': 'WCH:ch32v:CH32V20x_EVT:usb=tinyusb_usbd', 'family': 'ch32'},
    # rig boards: `span` is where a HIL image may load (XIP base: RP2040/RP2350 datasheets; sizes: the board's flash,
    # SAMD51 keeps the UF2 bootloader below 0x4000)
    'raspberry_pi_pico': {'fqbn': 'rp2040:rp2040:rpipico:flash=2097152_0,freq=120,dbgport=Disabled,dbglvl=None,'
                                  'usbstack=tinyusb', 'family': 'rp2040', 'filters': 'feather_rp2040_tinyusb',
                          'span': (0x10000000, 0x10200000)},
    'adafruit_fruit_jam': {'fqbn': 'rp2040:rp2040:adafruit_fruitjam:flash=16777216_0,dbgport=Disabled,dbglvl=None,'
                                   'usbstack=tinyusb', 'family': 'rp2040', 'filters': 'feather_rp2040_tinyusb',
                           'span': (0x10000000, 0x11000000)},
    'metro_m4_express': {'fqbn': 'adafruit:samd:adafruit_metro_m4:speed=120,usbstack=tinyusb', 'family': 'samd',
                         'filters': 'metro_m4_tinyusb', 'span': (0x4000, 0x80000)},
    # USB wired as host on the farm: the native-host build of the CI host platform
    'raspberry_pi_pico_w': {'fqbn': 'rp2040:rp2040:rpipicow:flash=2097152_0,dbgport=Disabled,dbglvl=None,'
                                    'usbstack=tinyusb_host', 'family': 'rp2040', 'filters': 'pico_rp2040_tinyusb_host',
                            'span': (0x10000000, 0x10200000), 'role': 'host'},
    'raspberry_pi_pico2': {'fqbn': 'rp2040:rp2040:rpipico2:flash=4194304_0,arch=arm,dbgport=Disabled,dbglvl=None,'
                                   'usbstack=tinyusb_host', 'family': 'rp2040', 'filters': 'pico_rp2040_tinyusb_host',
                           'span': (0x10000000, 0x10400000), 'role': 'host'},
    # esp32:esp32, what ci-arduino installs for their CI platforms. `span` is the SPI flash (FlashSize), `chip`
    # esptool's --chip, `fixture` a HIL file compiled into every sketch of the board.
    'espressif_s3_devkitm': {'fqbn': 'esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=cdc,FlashSize=4M,'
                                     'PSRAM=disabled', 'family': 'esp32', 'filters': 'feather_esp32s3',
                             'span': (0, 0x400000), 'chip': 'esp32s3', 'fixture': 'test/hil/esp32_ts3usb30.cpp'},
    # ChipVariant prev3: the farm's P4 is revision v0.1 (openocd-esp32's examination)
    'espressif_p4_function_ev': {'fqbn': 'esp32:esp32:esp32p4:USBMode=default,CDCOnBoot=cdc,FlashSize=4M,'
                                         'ChipVariant=prev3', 'family': 'esp32', 'filters': 'esp32p4',
                                 'span': (0, 0x400000), 'chip': 'esp32p4', 'fixture': 'test/hil/esp32_ts3usb30.cpp'},
}
# what a shared-code change builds: rig boards first, and each family's distinct stacks (RP2350, samd21/samd51,
# native host, ESP32-P4's high-speed OTG)
REPRESENTATIVES = {
    'rp2040': ['raspberry_pi_pico', 'adafruit_fruit_jam', 'pico_rp2040_tinyusb_host'],
    'samd': ['metro_m4_express', 'metro_m0_tinyusb'],
    'nrf': ['nrf52840'],
    'esp32': ['feather_esp32s3', 'espressif_p4_function_ev'],
    'ch32': ['CH32V20x_EVT'],
}
PORTABLE_FAMILY = {
    'raspberrypi/rp2040': 'rp2040', 'raspberrypi/pio_usb': 'rp2040', 'microchip/samd': 'samd',
    'nordic/nrf5x': 'nrf', 'synopsys/dwc2': 'esp32', 'wch': 'ch32',
}
# compiled only by MAX3421 host examples on every family
SHARED_PORTABLE = ('analog/max3421',)
# the sketches a HIL record carries per board role (hil_test.py's tests run them), the first also the restoration image
HIL_SKETCHES = {'device': ('examples/CDC/serial_echo', 'examples/HID/hid_generic_inout',
                           'examples/MassStorage/msc_ramdisk'),
                'host': ('examples/Host/Simple/host_device_info',)}
# headers the cores' own sources include from their bundled copy of this library, not from this checkout
# (seen in the .d files of rp2040:rp2040 5.4.3 and adafruit:samd 1.7.13 builds; builds report the live set)
CORE_CONSUMED = ('src/arduino/Adafruit_TinyUSB_API.h', 'src/arduino/Adafruit_USBD_CDC.h',
                 'src/arduino/Adafruit_USBD_Interface.h')
CONTRACT_TESTS = {'build': '.claude/test/test_build.py', 'hil': '.claude/test/test_hil.py',
                  'hil_remote': '.claude/test/test_hil_remote.py', 'hil_ci': '.claude/test/test_hil_ci.py',
                  'hil_workflow': '.claude/test/test_hil_workflow.py'}
SMOKE_BOARDS = ('raspberry_pi_pico', 'metro_m4_express', 'espressif_s3_devkitm')   # one per image kind
NOTHING = ('.claude/', '.github/', '.codespell/', 'docs/', 'tools/', '.agents', '.gitignore', '.codespellrc',
           '.coderabbit.yaml', '.pre-commit-config.yaml', 'library.json', 'LICENSE')
# CI's ARDUINO_LIBS (.github/workflows/githubci.yml) beyond library.properties depends=
EXTRA_DEPENDENCIES = ('Adafruit seesaw Library', 'Adafruit NeoPixel', 'Adafruit Circuit Playground',
                      'Adafruit InternalFlash', 'SD', 'Pico PIO USB')
CONTRACT_FILES = ('.claude/skills/build/scripts/check_build.py', '.claude/skills/hil-contract/scripts/hil_test.py',
                  '.claude/skills/hil-contract/scripts/hil_remote.py',
                  '.claude/skills/hil-contract/scripts/agentrc_pin.py', 'test/hil/agentrc.json')
AGENTRC = None   # set by engine(): agentrc_pin.identity()
HIL_WORKFLOWS = ('.github/workflows/hil.yml', '.github/workflows/hil_comment.yml')


def classify_source(path):
    """The library's own source rules (arduino_build.classify falls through to the common ones on None)."""
    if path in HIL_WORKFLOWS:
        return 'hil', None, 'the HIL workflow: its tests, and the battery on every roster board'
    if path in CORE_CONSUMED:
        return 'uncovered', None, 'the cores compile their own sources against their bundled copy of this header'
    if path.startswith('src/arduino/ports/'):
        port = path.split('/')[3]
        if port in REPRESENTATIVES:
            return 'family', port, f'port {port}'
        return 'uncovered', None, f'port {port} is built by no CI platform'
    if path.startswith('src/portable/'):
        for prefix in SHARED_PORTABLE:
            if path.startswith(f'src/portable/{prefix}/'):
                return 'shared', None, f'{prefix} is compiled by host examples on every family'
        for prefix, family in PORTABLE_FAMILY.items():
            if path.startswith(f'src/portable/{prefix}/'):
                return 'family', family, f'driver {prefix}'
        return 'uncovered', None, 'driver built by no CI platform'
    return None


def vid_pid(build_dir, board=None, roster_entry=None):
    """VID:PID the sketch enumerates with: Adafruit_USBD_Device.cpp's USB_VID/USB_PID as its compile command defines
    them (compile_commands.json) or, on ESP32, as the variant's pins_arduino.h does (esp32s3: 303a:1001), else its
    BOARD_VENDORID/BOARD_PRODUCTID, else its own fallback. The rp2040 core passes USBD_VID/USBD_PID, which this
    library ignores. Other core headers defining USB_VID are not seen."""
    try:
        commands = json.loads((Path(build_dir) / 'compile_commands.json').read_text())
    except (OSError, ValueError):
        return None
    for entry in commands:
        if entry.get('file', '').endswith('Adafruit_USBD_Device.cpp'):
            try:
                argv = entry.get('arguments') or shlex.split(entry.get('command', ''))
            except ValueError:
                return None
            flags = ' '.join(argv)
            break
    else:
        return None
    source = (ROOT / 'src/arduino/Adafruit_USBD_Device.cpp').read_text()
    variant = ''
    if '-DARDUINO_ARCH_ESP32' in flags:
        for arg in argv:
            m = re.match(r'-I(.*/variants/[^/]+)', arg)
            if m:
                header = Path(m.group(1)) / 'pins_arduino.h'
                variant += header.read_text(errors='replace') if header.is_file() else ''
    ids = []
    for name, board_name in (('USB_VID', 'BOARD_VENDORID'), ('USB_PID', 'BOARD_PRODUCTID')):
        defined = re.findall(rf'-D{name}=(\S+)', flags) or \
            re.findall(rf'#define\s+{name}\s+(0x[0-9a-fA-F]+)', variant) or re.findall(rf'-D{board_name}=(\S+)', flags)
        fallback = re.search(rf'#define {name} (0x[0-9a-fA-F]+)', source)
        value = defined[-1] if defined else fallback.group(1) if fallback else None
        if value is None:
            return None
        ids.append(int(value, 0))
    return f'{ids[0]:04x}:{ids[1]:04x}'


E = None   # agentrc's arduino_build, bound to this profile by engine()


def engine():
    """agentrc's arduino_build bound to this profile."""
    global AGENTRC, E
    if E is None:
        AGENTRC = agentrc_pin.identity()
        E = agentrc_pin.load('skills/arduino/scripts/arduino_build.py').bind(sys.modules[__name__])
    return E


def main(argv=None):
    try:
        eng = engine()
    except agentrc_pin.PinError as e:
        print(json.dumps({'version': 2, 'pass': False, 'error': str(e), 'remedy': e.remedy}))
        return 2
    return eng.main(argv)


if __name__ == '__main__':
    sys.modules.setdefault('check_build', sys.modules[__name__])
    sys.exit(main())
