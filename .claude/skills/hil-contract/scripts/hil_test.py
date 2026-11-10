#!/usr/bin/env python3
"""The Adafruit TinyUSB Arduino HIL harness (SKILL.md beside this directory). Linux only.

This file is the library's HIL profile for agentrc's arduino_hil.py (the `arduino` skill): its battery, host step
and roster checks; the mechanics are there, loaded from the agentrc agentrc_pin.py selects.

  hil_test.py run [-b BOARD]... [-t TEST]... [--record-dir DIR] [--run-dir DIR] [--staged MANIFEST] ROSTER
  hil_test.py commission -b BOARD [--record-dir DIR] ROSTER
  hil_test.py flash -b BOARD --build-record R --sketch S --under-hold REASON [--staged M] ROSTER
  hil_test.py restore -b BOARD --build-record R --under-hold REASON [--report PATH] [--release] [--staged M] ROSTER
  hil_test.py gdb -b BOARD --build-record R --sketch S --under-hold REASON [--ex CMD]... [--timeout S] [--staged M]
                 ROSTER
  hil_test.py exec -b BOARD --under-hold REASON [--timeout S] [--staged M] ROSTER -- ARGV...
  hil_test.py console -b BOARD --under-hold REASON [--seconds S] [--baud N] [--reset] [--staged M] ROSTER
  hil_test.py report RUN_DIR [-b BOARD]...
  hil_test.py lock (hold|release|status) ...        agentrc's hil_lock.py

flash, restore and gdb take --bundle DIR, a record bundle `hil_remote.py session stage` staged, --build-record then
inside it. restore --release passes only when the hold was released too.

The last stdout line of every command but lock and read-block is JSON. Exits: 0 pass, 1 a board failed or did not
run (cleaned up or untouched), 2 refused before any hardware access, 3 a session command (--under-hold) whose hold the
lock record shows no live holder of, 4 a cleanup could not be proven (logged, the board released) or the run was
aborted; restore --release also exits 5 not released (the hold may still stand) or 6 restore failed (logged,
released).
"""

import json
import os
import re
import secrets
import select
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SCRIPT = Path(__file__).resolve()
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'build' / 'scripts'))
import agentrc_pin  # noqa: E402
import check_build as BUILD  # noqa: E402

TESTS = {'cdc_echo': 'serial_echo', 'hid_inout': 'hid_generic_inout', 'msc_ramdisk': 'msc_ramdisk',
         'host_device_info': 'host_device_info'}
ROLE_TESTS = {'device': ('cdc_echo', 'hid_inout', 'msc_ramdisk'), 'host': ('host_device_info',)}
RESTORE_TEST = {'device': 'cdc_echo', 'host': 'host_device_info'}   # each runs its role's restoration sketch
REPORT_SCHEMA = 'adafruit-tinyusb-arduino-hil'
# hil_remote.py (agentrc arduino_remote.py)
REMOTE_BASE = '~/hil-remote/adafruit-tinyusb-arduino'
REMOTE_ROSTER = 'test/hil/ci.json'
HIL = '.claude/skills/hil-contract/scripts/hil_test.py'
# msc_ramdisk's fixture (examples/MassStorage/msc_ramdisk: DISK_BLOCK_NUM * DISK_BLOCK_SIZE, README_CONTENTS)
MSC_DISK_BYTES = 16 * 512
MSC_README = b'This is TinyUSB MassStorage device demo for Arduino on RAM disk.'
HID_REPORT = 64


E = None   # agentrc's arduino_hil, bound to this profile by engine()


def attached(board):
    """The devices on a host board's port that host_device_info can name: agentrc's board registry's `dev_attached`
    (resolved into the entry) with a serial."""
    return {(d['vid_pid'], d['serial']) for d in board.get('dev_attached', []) if 'serial' in d}


def check_board(name, b, path):
    if 'dev_attached' in b.get('arduino', {}):
        raise E.Refused(f'roster {path}: {name}: arduino.dev_attached is agentrc\'s board registry\'s now; drop it '
                        f'(a board without a registered probe lists its own top-level dev_attached)')
    # the roster's own copy, which resolution keeps unchecked for a board the registry lists no devices for
    if 'dev_attached' in b and (why := BUILD.E.hil_boards.dev_attached_problem(b['dev_attached'])):
        raise E.Refused(f'roster {path}: {name}: dev_attached: {why}')
    if BUILD.BOARDS[name].get('role') == 'host' and not attached(b):
        raise E.Refused(f'roster {path}: {name}: a host board needs a device with a serial in its dev_attached, '
                        f'from agentrc\'s board registry or, without a registered probe, its own top-level one')


def test_cdc(dev, nonce, deadline, tracker, run_dir, board=None):
    import serial
    tty = E.node(dev, '*:*/tty/tty*', deadline)
    payloads = [f'hil-{nonce}-{i}'.encode() for i in range(3)]
    with serial.Serial(tty, 115200, timeout=0.2) as port:
        port.reset_input_buffer()
        for p in payloads:
            port.write_timeout = max(0.1, deadline - time.monotonic())   # a stalled OUT must not outlast the test
            try:
                port.write(p)
            except serial.SerialTimeoutException:
                return False, f'{tty}: write of {p!r} timed out'
            got = b''
            while len(got) < len(p) and time.monotonic() < deadline:
                got += port.read(len(p) - len(got))
            if got != p.upper():
                return False, f'{tty}: sent {p!r}, got {got!r}'
    return True, f'{tty}: {len(payloads)} payloads echoed upper-cased'


def test_hid(dev, nonce, deadline, tracker, run_dir, board=None):
    path = E.node(dev, '*:*/*/hidraw/hidraw*', deadline)
    report = (bytes.fromhex(nonce) * HID_REPORT)[:HID_REPORT]
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, b'\x00' + report)
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if r:
                got = os.read(fd, HID_REPORT + 1)
                return (got == report), f'{path}: {"echoed" if got == report else "got " + got.hex()} ' \
                                         f'{HID_REPORT}-byte report'
    finally:
        os.close(fd)
    return False, f'{path}: no input report within the test timeout'


def test_msc(dev, nonce, deadline, tracker, run_dir, board=None):
    path = E.node(dev, '*:*/host*/target*/*/block/*', deadline)
    size = int(Path(f'/sys/class/block/{os.path.basename(path)}/size').read_text()) * 512
    if size != MSC_DISK_BYTES:
        return False, f'{path}: {size} bytes, the ramdisk has {MSC_DISK_BYTES}'
    out = Path(run_dir) / f'msc-{nonce}.img'
    r = tracker.run([sys.executable, str(SCRIPT), 'read-block', path, str(out)],
                    timeout=max(1.0, deadline - time.monotonic()))
    tracker.check()
    if r.returncode or not out.exists():
        return False, f'{path}: read failed ({E._text(r.stdout).strip()[-200:]})'
    content = fat12_file(out.read_bytes(), b'README  TXT')
    return content == MSC_README, f'{path}: README.TXT {"matches" if content == MSC_README else repr(content)}'


def fat12_file(img, name83):
    """The bytes of a root-directory file of a FAT12 image (8.3 name, space padded), or None."""
    bps, spc = int.from_bytes(img[11:13], 'little'), img[13]
    reserved, nfats = int.from_bytes(img[14:16], 'little'), img[16]
    root_entries, spf = int.from_bytes(img[17:19], 'little'), int.from_bytes(img[22:24], 'little')
    root = (reserved + nfats * spf) * bps
    data = root + root_entries * 32
    for off in range(root, data, 32):
        entry = img[off:off + 32]
        if entry[:11] == name83:
            cluster, size = int.from_bytes(entry[26:28], 'little'), int.from_bytes(entry[28:32], 'little')
            start = data + (cluster - 2) * spc * bps
            return img[start:start + size]
    return None


TEST_FUNCS = {'cdc_echo': test_cdc, 'hid_inout': test_hid, 'msc_ramdisk': test_msc}

# examples/Host/Simple/host_device_info: a mounted device's descriptor dump, from its "Device N: ID" line to its
# iSerialNumber line (index, then the string read back)
HOST_DEVICE_RE = re.compile(r'Device (\d+): ID ([0-9a-f]{4}:[0-9a-f]{4})\r?\nDevice Descriptor:\r?\n'
                            r'(?:  .*\r?\n)*?  iSerialNumber +\d+ +(.*?)\r?\n')


def host_devices(text):
    """{(vid:pid, serial)} host_device_info reported in full."""
    return {(m.group(2), m.group(3).strip()) for m in HOST_DEVICE_RE.finditer(text)}


def step_host(name, board, image, test, run_dir, tracker):
    """A host board's step: the console open and drained before the flash, so the boot's enumeration output is
    kept; pass when every attached device with a serial is reported with it."""
    import serial
    started, want = time.monotonic(), attached(board)
    try:
        tty = E.console_tty(board, time.monotonic() + 5)
    except (RuntimeError, TimeoutError) as e:
        return {'test': test, 'pass': False, 'detail': f'console: {e}'}
    log = Path(run_dir) / f'console-{test}-{secrets.token_hex(2)}.log'
    buf, times, err = bytearray(), [], ''
    try:
        with serial.Serial(tty, 115200, timeout=0.2) as port:
            port.reset_input_buffer()
            why = E.flash(board['flasher'], image, run_dir, tracker)
            if why:
                return {'test': test, 'pass': False, 'detail': why}
            E.read_console(port, time.monotonic() + E.TEST_TIMEOUT, buf, times, time.monotonic(),
                         done=lambda b: want <= host_devices(b.decode(errors='replace')))
    except (serial.SerialException, OSError) as e:
        err = f'; {tty}: {e}'
    log.write_bytes(buf)
    found = host_devices(buf.decode(errors='replace'))
    missing = sorted(want - found)
    return {'test': test, 'pass': not missing and not err, 'console': str(log),
            'seconds': round(time.monotonic() - started, 1),
            'detail': f'{tty}: reported {sorted(found)}' + (f', missing {missing}' if missing else '') + err}


def cmd_read_block(argv):
    """Child of the MSC test (`read-block DEVICE OUT`): read the whole block device into a file (bounded and reaped
    by run_tracked)."""
    device, dest = argv
    with open(device, 'rb', buffering=0) as dev, open(dest, 'wb') as out:
        out.write(dev.read(MSC_DISK_BYTES))
    return 0


def engine():
    """agentrc's arduino_hil bound to this profile (and its build profile)."""
    global E
    if E is None:
        BUILD.engine()
        E = agentrc_pin.load('skills/arduino/scripts/arduino_hil.py').bind(sys.modules[__name__])
    return E


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['read-block']:
        return cmd_read_block(argv[1:])
    try:
        eng = engine()
    except agentrc_pin.PinError as e:
        print(json.dumps({'pass': False, 'error': str(e), 'remedy': e.remedy}), flush=True)
        return 2
    return eng.main(argv)


if __name__ == '__main__':
    sys.modules.setdefault('hil_test', sys.modules[__name__])
    sys.exit(main())
