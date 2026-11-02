#!/usr/bin/env python3
"""The Adafruit TinyUSB Arduino HIL harness (SKILL.md beside this directory). Linux only.

  hil_test.py run [-b BOARD]... [-t TEST]... [--record-dir DIR] [--run-dir DIR] [--staged MANIFEST] ROSTER
  hil_test.py commission -b BOARD [--record-dir DIR] ROSTER
  hil_test.py flash -b BOARD --build-record R --sketch S --under-hold REASON [--staged M] ROSTER
  hil_test.py restore -b BOARD --build-record R --under-hold REASON [--report PATH] [--release] [--staged M] ROSTER
  hil_test.py gdb -b BOARD --build-record R --sketch S --under-hold REASON [--ex CMD]... [--timeout S] [--staged M]
                 ROSTER
  hil_test.py exec -b BOARD --under-hold REASON [--timeout S] [--staged M] ROSTER -- ARGV...
  hil_test.py console -b BOARD --under-hold REASON [--seconds S] [--baud N] [--reset] [--staged M] ROSTER
  hil_test.py report RUN_DIR [-b BOARD]...
  hil_test.py lock (hold|release|status) ...        the pinned agentrc hil_lock.py

flash, restore and gdb take --bundle DIR, a record bundle `hil_remote.py session stage` staged, --build-record then
inside it. restore --release passes only when the hold was released too.

The last stdout line of every command but lock and read-block is JSON. Exits: 0 pass, 1 a board failed or did not
run (cleaned up or untouched), 2 refused before any hardware access, 4 a cleanup did not finish (board held).
"""

import argparse
import contextlib
import fcntl
import glob
import hashlib
import json
import os
import re
import secrets
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'build' / 'scripts'))
import agentrc_pin  # noqa: E402
import check_build  # noqa: E402

LOCK_SCRIPT = 'skills/hil/scripts/hil_lock.py'
SCRIPTS = (LOCK_SCRIPT, 'skills/hil/scripts/hil_flash.py', 'skills/hil/scripts/hil_process.py',
           'skills/hil/scripts/hil_report_core.py', 'skills/hil/scripts/usb_observe.py',
           'skills/hil/scripts/hil_openocd_esp.py')
TESTS = {'cdc_echo': 'serial_echo', 'hid_inout': 'hid_generic_inout', 'msc_ramdisk': 'msc_ramdisk',
         'host_device_info': 'host_device_info'}
ROLE_TESTS = {'device': ('cdc_echo', 'hid_inout', 'msc_ramdisk'), 'host': ('host_device_info',)}
RESTORE_TEST = {'device': 'cdc_echo', 'host': 'host_device_info'}   # each runs its role's restoration sketch
# msc_ramdisk's fixture (examples/MassStorage/msc_ramdisk: DISK_BLOCK_NUM * DISK_BLOCK_SIZE, README_CONTENTS)
MSC_DISK_BYTES = 16 * 512
MSC_README = b'This is TinyUSB MassStorage device demo for Arduino on RAM disk.'
HID_REPORT = 64

# Probe commands that take the DUT off the bus before a flash. A probe reset leaves the RP2040/RP2350 USB controller
# out of reset, and dcd_init resets and reconnects it within microseconds, so without this the host never sees the
# new image arrive: clear SIE_CTRL.PULLUP_EN (USBCTRL_REGS_BASE 0x50110000 + 0x4c, bit 16) through its atomic clear
# alias (+0x3000). RP2040 datasheet 2.1.2 and 4.1.4 (SIE_CTRL); RP2350 datasheet 2.1.3 and 12.7.5 (same layout).
DETACH = {'rp2040': ['mww 0x5011304c 0x00010000']}
DETACH_WAIT = 5

FLASH_TIMEOUT, VERIFY_TIMEOUT, ENUM_TIMEOUT, TEST_TIMEOUT, SETTLE = 120, 60, 20, 30, 2
CONSOLE_MAX = 120   # one console capture, seconds
BOARD_BUDGET = 900
EXEC_MAX = 600   # one session exec or gdb child
GDB_SERVER = ['-c', 'gdb port pipe', '-c', 'gdb memory_map disable', '-c', 'gdb breakpoint_override hard']
# An RP2040 SMP resume restores core1 only after each core is polled (target-debug gdb.md, verified on the Pico)
GDB_DETACH_RESUME = {'target/rp2040.cfg': '{ foreach u [target names] { $u arp_poll }; resume }'}   # by debug cfg
REAP = 10   # agentrc hil_process.REAP_GRACE, per tracked child
STEP_MAX = VERIFY_TIMEOUT + DETACH_WAIT + FLASH_TIMEOUT + VERIFY_TIMEOUT + ENUM_TIMEOUT + TEST_TIMEOUT + \
    4 * (REAP + SETTLE)
CLEANUP_RESERVE = STEP_MAX   # the restore is one more such step
REPORT_SCHEMA = 'adafruit-tinyusb-arduino-hil'


class Refused(Exception):
    """Refused before any hardware access."""


class Unclean(Exception):
    """A child's end is not proven: nothing more may touch the board."""


A = None   # the loaded agentrc modules: lock, flash, process, report, usb


def staged_tree():
    """A rig tree hil_remote.py staged: no .git, its agentrc files under agentrc/."""
    return not (ROOT / '.git').exists()


def agentrc_root(staged):
    return ROOT / 'agentrc' if staged else agentrc_pin.checkout()


def load_agentrc(staged):
    global A
    root = agentrc_root(staged)
    agentrc_pin.closure(root, SCRIPTS)
    hl = agentrc_pin.loader(root)
    A = argparse.Namespace(lock=hl.load_file(SCRIPTS[0]), flash=hl.load_file(SCRIPTS[1]),
                           process=hl.load_file(SCRIPTS[2]), report=hl.load_file(SCRIPTS[3]),
                           usb=hl.load_file(SCRIPTS[4]), esp=lambda: hl.load_file(SCRIPTS[5]), root=root)
    return A


def emit(obj):
    print(json.dumps(obj), flush=True)


# ---- roster, staged manifest, build record

def role(name):
    return check_build.BOARDS[name].get('role', 'device')


def restore_sketch(name):
    return Path(check_build.hil_sketches(name)[0]).name


def load_roster(path):
    try:
        boards = check_build.rig_roster(path)
    except ValueError as e:
        raise Refused(str(e))
    for name, b in boards.items():
        flasher = b.get('flasher', {}).get('name')
        if flasher not in check_build.IMAGE_KIND:
            raise Refused(f'roster {path}: {name}: flasher must be one of {sorted(check_build.IMAGE_KIND)}')
        if (flasher == 'esptool') != ('chip' in check_build.BOARDS[name]):
            raise Refused(f'roster {path}: {name}: esptool flashes exactly the boards with an ESP chip profile')
        wrong = set(b.get('arduino', {}).get('tests', [])) - set(ROLE_TESTS[role(name)])
        if wrong:
            raise Refused(f'roster {path}: {name}: tests {sorted(wrong)} are not {role(name)} tests')
        attached = b.get('arduino', {}).get('dev_attached')
        if role(name) == 'host' and not (attached and all(re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{4}', d.get('vid_pid', ''))
                                                          and d.get('serial') for d in attached)):
            raise Refused(f'roster {path}: {name}: a host board needs arduino.dev_attached, each a vid_pid '
                          f'"vvvv:pppp" and a serial')
        if 'usb_port' in b and not re.fullmatch(r'\d+-\d+(\.\d+)*', b['usb_port']):
            raise Refused(f'roster {path}: {name}: usb_port {b["usb_port"]!r} is not a sysfs bus port')
        if flasher == 'esptool' and 'usb_port' not in b:
            # Arduino on ESP32 enumerates without an iSerial: the DUT is the device at its port
            raise Refused(f'roster {path}: {name}: an esptool board needs usb_port')
        dbg = b.get('debug', {})
        tool = dbg.get('tool', 'openocd')
        if tool not in ('openocd', 'openocd-esp32') or (tool == 'openocd-esp32') != ('uid' in dbg):
            raise Refused(f'roster {path}: {name}: debug.tool is openocd, or openocd-esp32 with the debug '
                          f'probe\'s debug.uid')
        chip = check_build.BOARDS[name].get('chip')
        if tool == 'openocd-esp32' and ('vid_pid' in dbg or dbg.get('openocd') != f'-f board/{chip}-builtin.cfg'):
            # the USB-Serial/JTAG is pinned by its bus-port (agentrc hil_openocd_esp), never by vid_pid
            raise Refused(f'roster {path}: {name}: an openocd-esp32 debug route is "-f board/{chip}-builtin.cfg" '
                          f'and debug.uid, with no vid_pid')
    return boards


def selection(roster, boards, tests):
    """{board: its tests} for -b/-t: the roster's battery, or with -t the named tests of each board's role, boards with
    none dropped; Refused when nothing is left."""
    names = boards or list(roster)
    unknown = [b for b in names if b not in roster]
    if unknown:
        raise Refused(f'not in the roster: {unknown}')
    picked = {b: [t for t in tests if t in ROLE_TESTS[role(b)]] if tests else
              roster[b].get('arduino', {}).get('tests', []) for b in names}
    picked = {b: ts for b, ts in picked.items() if ts}
    if not picked:
        raise Refused(f'no selected board runs any of {tests or "its tests"}')
    return picked


def verify_staged(manifest_path):
    """A rig tree staged by hil_remote.py: every staged file must hash as the manifest written on the dev PC says."""
    m = json.loads(Path(manifest_path).read_text())
    bad = [rel for rel, digest in m['files'].items()
           if not (ROOT / rel).is_file() or check_build.sha256(ROOT / rel) != digest]
    if bad:
        raise Refused(f'staged files differ from the manifest: {bad[:5]}')
    return m


def git_identity():
    def git(*a):
        return subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True).stdout
    return git('rev-parse', 'HEAD').strip(), git('status', '--porcelain', '--untracked-files=all').strip()


def record_problems(rec, board, head, experimental_ok, where):
    """Why a build record is not admissible for `board` built at `head` (None: any head), its images aside; [] when
    it is. hil_remote.py asks the same before staging."""
    if rec.get('kind') != 'arduino-hil-build' or rec.get('version') != check_build.SCHEMA:
        return [f'not a version {check_build.SCHEMA} arduino-hil-build record']
    why = []
    if rec['board'] != board:
        why.append(f'it is for {rec["board"]}, not {board}')
    if rec['experimental'] and not experimental_ok:
        why.append('it is experimental: the battery and restore take clean records only')
    if head is not None and rec['source']['head'] != head:
        why.append(f'it was built at {rec["source"]["head"][:12]}, the {where} is at {head[:12]}: rebuild it')
    return why


def admit(record_path, board, roster_path, run_dir, *, manifest=None, experimental_ok=False, base=None):
    """The record's images (checkout-relative paths, under `base` for a staged bundle) copied into the run dir once
    they hash as recorded, or Refused."""
    try:
        rec = json.loads(Path(record_path).read_text())
    except (OSError, ValueError) as e:
        raise Refused(f'build record {record_path}: {e}')
    head, status, where = None, '', 'checkout'
    if manifest is not None:
        head, where = manifest['head'], 'staged tree'
    elif not rec.get('experimental'):
        head, status = git_identity()
    why = record_problems(rec, board, head, experimental_ok, where)
    if why:
        raise Refused(f'{record_path}: {"; ".join(why)}')
    if status:
        raise Refused(f'the checkout has changes since the record: {status.splitlines()[:5]}')
    if check_build.sha256(roster_path) != rec['roster']['sha256']:
        raise Refused(f'{roster_path} changed since the record was built: rebuild it')
    flasher = load_roster(roster_path)[board]['flasher']['name']
    want = check_build.IMAGE_KIND[flasher]
    if rec.get('role', 'device') != role(board):
        raise Refused(f'{record_path}: a {rec.get("role", "device")} record, {board} is a {role(board)} board')
    images = {}
    for name, s in rec['sketches'].items():
        if s.get('image') != want:
            raise Refused(f'{record_path}: {name} records a {s.get("image")} image, a {flasher} board flashes the '
                          f'{want}: rebuild against this roster')
        dest = Path(run_dir) / 'images' / name
        dest.mkdir(parents=True, exist_ok=True)
        for rel, digest in s['sha256'].items():
            src = Path(base or ROOT) / rel
            data = src.read_bytes() if src.is_file() else None
            if data is None or hashlib.sha256(data).hexdigest() != digest:
                raise Refused(f'{rel} is missing or changed since the record')
            (dest / Path(rel).name).write_bytes(data)
        images[name] = {'elf': str(dest / Path(s['elf']).name),
                        'hex': str(dest / Path(s['hex']).name) if s.get('hex') else None,
                        'vidPid': s['vidPid'], 'sha256': s['sha256'], 'dir': str(dest),
                        'segments': s.get('segments'), 'chip': rec.get('chip')}
    return rec, images


# ---- tracked children and flashing

class Tracker:
    """Every hardware child runs through agentrc's run_tracked; one not proven ended stops the board."""

    def __init__(self, run_dir):
        # run_tracked trusts a state dir only when neither it nor its parent is writable by others
        self.state_dir = Path(run_dir) / 'tracked'
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(run_dir, 0o700)
        os.chmod(self.state_dir, 0o700)
        A.process.trusted_dir(str(self.state_dir))
        self.receipts, self.pending, self.unclean, self.log = [], [], '', Path(run_dir) / 'hil.log'

    def run(self, argv, timeout, **kw):
        self.check()
        try:
            r = A.process.run_tracked(argv, timeout=timeout, settle=SETTLE, state_dir=str(self.state_dir), **kw)
        except BaseException as e:
            self._note(getattr(e, 'cleanup', 'unknown'), getattr(e, 'receipt', None), argv)
            raise
        self._note(getattr(r, 'cleanup', 'unknown'), getattr(r, 'receipt', None), argv)
        with open(self.log, 'a') as f:
            f.write(f'$ {" ".join(map(str, argv))}\n[exit {r.returncode}]\n{_text(r.stdout)}{_text(r.stderr)}\n')
        return r

    def _note(self, cleanup, receipt, argv):
        if receipt is not None:
            self.receipts.append(receipt)
        if cleanup != 'done':
            self.unclean = f'{Path(str(argv[0])).name}: cleanup {cleanup}'
            # no receipt (the supervisor died): a session nothing can recheck, cleared only by a reviewed recovery
            self.pending.append({'receipt': receipt if receipt is not None else
                                 {'unrecheckable': [str(a) for a in argv], 'at': time.time()}})

    def check(self):
        if self.unclean:
            raise Unclean(self.unclean)


def _text(b):
    return b.decode(errors='replace') if isinstance(b, bytes) else (b or '')


def jlink_device(flasher):
    words = shlex.split(flasher.get('args') or '')   # as hil_flash splits it for the flash session
    return words[words.index('-device') + 1]


def esptool_setup(flasher):
    """(the bridge tty, the esptool executable), resolved under the board's reservation, or RuntimeError."""
    return A.flash.esptool_port(flasher['uid']), A.flash.esptool_runtime()['exe']


def flash_esptool(flasher, image, tracker):
    """The build's flash_args through esptool write_flash, whose per-image hash check is the verify."""
    flash_args = ' '.join(Path(image['dir'], 'flash_args').read_text().split())
    why = A.flash.esptool_input_problem(image['chip'], flash_args, image['dir'])
    if why:
        return f'esptool inputs: {why}'
    try:
        port, exe = esptool_setup(flasher)
    except RuntimeError as e:
        return f'esptool: {e}'
    op = A.flash.flash_esptool(flasher, chip=image['chip'], port=port, flash_args=flash_args, cwd=image['dir'],
                               timeout=FLASH_TIMEOUT, run=tracker.run, esptool=exe)
    tracker.check()
    why = A.flash.esptool_problem(op)
    if why:
        return f'esptool flash: {why}'
    verified = (_text(op.stdout) + _text(op.stderr)).count('Hash of data verified.')
    want = len(image['segments'])
    return '' if verified == want else f'esptool verified {verified} of {want} images'


def flash(flasher, image, run_dir, tracker):
    """Program and verify one image. Returns '' or why it failed; raises Unclean when a child outlived it."""
    if flasher['name'] == 'esptool':
        return flash_esptool(flasher, image, tracker)
    if flasher['name'] == 'openocd':
        op = A.flash.flash_openocd(flasher, image['elf'], timeout=FLASH_TIMEOUT, run=tracker.run)
        tracker.check()
        out = _text(op.stdout) + _text(op.stderr)
        if op.returncode:
            return f'openocd exit {op.returncode}: {out.strip().splitlines()[-1:]}'
        need = ['** Programming Finished **'] + (['** Verified OK **'] if flasher.get('verify', True) else [])
        missing = [n for n in need if n not in out]
        return f'openocd transcript lacks {missing}' if missing else ''
    if not image['hex']:
        return 'no HEX for a J-Link flash'
    op = A.flash.flash_jlink(flasher, image['hex'], timeout=FLASH_TIMEOUT, run=tracker.run, workdir=str(run_dir))
    tracker.check()
    why = A.flash.jlink_problem(op)
    if why:
        return f'J-Link flash: {why}'
    cmds = []
    for i, (addr, data) in enumerate(check_build.hex_runs(image['hex'])):
        path = Path(run_dir) / f'verify-{Path(image["hex"]).stem}-{i}.bin'
        path.write_bytes(data)
        cmds.append(f'verifybin {path},{addr:#x}')
    op = A.flash.run_jlink_commands(flasher, cmds + ['r', 'go'], interface='SWD', speed=4000, autoconnect=True,
                                    device=jlink_device(flasher), timeout=VERIFY_TIMEOUT, run=tracker.run,
                                    workdir=str(run_dir))
    tracker.check()
    why = A.flash.jlink_problem(op)
    return f'J-Link verify: {why}' if why else ''


def detach(name, board, vid_pid, tracker):
    """Take the DUT off the bus through the probe, where its family needs it (DETACH); evidence text. A failed
    write is evidence, not a failure: a firmware without USB has nothing to detach, and the enumeration check after
    the flash decides."""
    cmds = DETACH.get(check_build.BOARDS[name]['family'])
    if not cmds or board['flasher']['name'] != 'openocd' or not vid_pid:   # no vid_pid: a host board
        return ''
    op = A.flash.run_openocd_commands(board['flasher'], cmds, timeout=VERIFY_TIMEOUT, run=tracker.run)
    tracker.check()
    out = _text(op.stdout) + _text(op.stderr)
    if op.returncode or 'Error' in out:
        return f'detach session failed (exit {op.returncode})'
    deadline = time.monotonic() + DETACH_WAIT
    while time.monotonic() < deadline:
        if not dut_devices(vid_pid, board):
            return 'detached'
        time.sleep(0.2)
    return f'still enumerated {DETACH_WAIT}s after the detach'


def flash_and_enumerate(name, board, image, run_dir, tracker):
    """Note the DUT's enumeration, detach, flash, then wait for it to enumerate anew: (why the flash failed or '',
    detach evidence, the new device or None)."""
    before = enumeration_of(image['vidPid'], board)
    detached = detach(name, board, image['vidPid'], tracker)
    why = flash(board['flasher'], image, run_dir, tracker)
    if why:
        return why, detached, None
    return '', detached, wait_enumerated(image['vidPid'], board, before)


def reset(flasher, tracker):
    if flasher['name'] == 'esptool':
        try:
            port, exe = esptool_setup(flasher)
        except RuntimeError as e:
            return f'esptool: {e}'
        op = A.flash.reset_esptool(flasher, port=port, timeout=VERIFY_TIMEOUT, run=tracker.run, esptool=exe)
        tracker.check()
        return A.flash.esptool_problem(op)
    if flasher['name'] == 'openocd':
        op = A.flash.reset_openocd(flasher, timeout=VERIFY_TIMEOUT, run=tracker.run)
        tracker.check()
        return f'openocd reset exit {op.returncode}' if op.returncode else A.flash.reset_problem('openocd', op)
    op = A.flash.reset_jlink(flasher, timeout=VERIFY_TIMEOUT, run=tracker.run)
    tracker.check()
    return A.flash.jlink_problem(op)


# ---- USB

def scan(vid_pid, serial=None):
    return A.usb.usb_scan(vid_pid=tuple(vid_pid.split(':')), serial=serial)


def dut_devices(vid_pid, board):
    """The DUT's enumerations with this VID:PID: the device at the roster's usb_port where the firmware reports no
    iSerial (ESP32 under Arduino), else the one whose serial is the roster uid."""
    if 'usb_port' in board:
        return [d for d in A.usb.usb_devices(vid_pid=tuple(vid_pid.split(':'))) if d['busport'] == board['usb_port']]
    return scan(vid_pid, board['uid'])


def dut_name(board):
    return f'at {board["usb_port"]}' if 'usb_port' in board else f'with serial {board["uid"]}'


def wait_enumerated(vid_pid, board, before, timeout=ENUM_TIMEOUT):
    """The DUT with this VID:PID once it enumerated anew (an enumeration id other than `before`) and stayed for a
    second, or None."""
    deadline, seen, since = time.monotonic() + timeout, None, 0
    while time.monotonic() < deadline:
        devs = [d for d in dut_devices(vid_pid, board) if d['ino'] != before]
        if len(devs) == 1:
            if seen and seen['ino'] == devs[0]['ino'] and time.monotonic() - since >= 1:
                return devs[0]
            if not seen or seen['ino'] != devs[0]['ino']:
                seen, since = devs[0], time.monotonic()
        else:
            seen = None
        time.sleep(0.25)
    return None


def enumeration_of(vid_pid, board):
    devs = dut_devices(vid_pid, board)
    return devs[0]['ino'] if len(devs) == 1 else None


def node(dev, pattern, deadline):
    """/dev/<name> of an interface child of `dev` matching the sysfs glob, once it is there and accessible."""
    while time.monotonic() < deadline:
        hits = sorted(glob.glob(os.path.join(dev['dir'], pattern)))
        if hits:
            path = '/dev/' + os.path.basename(hits[0])
            if os.access(path, os.R_OK | os.W_OK):
                return path
        time.sleep(0.2)
    raise TimeoutError(f'no accessible {pattern} under {dev["busport"]} (missing node or permission)')


def test_cdc(dev, nonce, deadline, tracker, run_dir):
    import serial
    tty = node(dev, '*:*/tty/tty*', deadline)
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


def test_hid(dev, nonce, deadline, tracker, run_dir):
    path = node(dev, '*:*/*/hidraw/hidraw*', deadline)
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


def test_msc(dev, nonce, deadline, tracker, run_dir):
    path = node(dev, '*:*/host*/target*/*/block/*', deadline)
    size = int(Path(f'/sys/class/block/{os.path.basename(path)}/size').read_text()) * 512
    if size != MSC_DISK_BYTES:
        return False, f'{path}: {size} bytes, the ramdisk has {MSC_DISK_BYTES}'
    out = Path(run_dir) / f'msc-{nonce}.img'
    r = tracker.run([sys.executable, str(Path(__file__).resolve()), 'read-block', path, str(out)],
                    timeout=max(1.0, deadline - time.monotonic()))
    tracker.check()
    if r.returncode or not out.exists():
        return False, f'{path}: read failed ({_text(r.stdout).strip()[-200:]})'
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


def console_tty(board, deadline):
    """The board's console: its CMSIS-DAP probe's VCOM (the target's UART0, Serial1), or an esptool board's
    USB-UART bridge (UART0). TimeoutError or RuntimeError naming why not."""
    fl = board['flasher']
    if fl['name'] == 'esptool':
        return A.flash.esptool_port(fl['uid'])
    if fl['name'] != 'openocd' or 'vid_pid' not in fl:
        raise RuntimeError(f'no console route for a {fl["name"]} probe without vid_pid')
    probes = A.usb.usb_scan(vid_pid=tuple(v[2:] for v in fl['vid_pid'].split()), serial=fl['uid'])
    if len(probes) != 1:
        raise RuntimeError(f'probe {fl["uid"]}: want one on the bus, found {len(probes)}')
    return node(probes[0], '*:*/tty/tty*', deadline)


def step_host(name, board, image, test, run_dir, tracker):
    """A host board's step: the console open and drained before the flash, so the boot's enumeration output is
    kept; pass when every roster dev_attached device is reported with its serial."""
    import serial
    started, want = time.monotonic(), {(d['vid_pid'], d['serial']) for d in board['arduino']['dev_attached']}
    try:
        tty = console_tty(board, time.monotonic() + 5)
    except (RuntimeError, TimeoutError) as e:
        return {'test': test, 'pass': False, 'detail': f'console: {e}'}
    log = Path(run_dir) / f'console-{test}-{secrets.token_hex(2)}.log'
    with serial.Serial(tty, 115200, timeout=0.2) as port:
        port.reset_input_buffer()
        why = flash(board['flasher'], image, run_dir, tracker)
        if why:
            return {'test': test, 'pass': False, 'detail': why}
        text, deadline = '', time.monotonic() + TEST_TIMEOUT
        while time.monotonic() < deadline and not want <= host_devices(text):
            text += port.read(4096).decode(errors='replace')
    log.write_text(text)
    found = host_devices(text)
    missing = sorted(want - found)
    return {'test': test, 'pass': not missing, 'console': str(log), 'seconds': round(time.monotonic() - started, 1),
            'detail': f'{tty}: reported {sorted(found)}' + (f', missing {missing}' if missing else '')}


def step(name, board, image, test, run_dir, tracker):
    """Flash the test's sketch, wait for the DUT to enumerate anew, run the host check. A dict row."""
    if role(name) == 'host':
        return step_host(name, board, image, test, run_dir, tracker)
    started = time.monotonic()
    vid_pid = image['vidPid']
    why, detached, dev = flash_and_enumerate(name, board, image, run_dir, tracker)
    if why:
        return {'test': test, 'pass': False, 'detail': why, 'detach': detached}
    if not dev:
        others = [d['busport'] for d in A.usb.usb_devices(vid_pid=tuple(vid_pid.split(':')))]
        return {'test': test, 'pass': False, 'detail': f'no {vid_pid} {dut_name(board)} enumerated anew within '
                                                       f'{ENUM_TIMEOUT}s (that VID:PID at: {others})'}
    deadline = time.monotonic() + TEST_TIMEOUT
    try:
        ok, detail = TEST_FUNCS[test](dev, secrets.token_hex(4), deadline, tracker, run_dir)
    except Unclean:
        raise
    except Exception as e:   # a host-side failure of the check is the test's, once its children ended
        tracker.check()
        ok, detail = False, f'{type(e).__name__}: {e}'
    return {'test': test, 'pass': ok, 'detail': detail, 'device': dev['busport'], 'detach': detached,
            'seconds': round(time.monotonic() - started, 1)}


def restore(name, board, images, run_dir, tracker):
    sketch = restore_sketch(name)
    row = step(name, board, images[sketch], RESTORE_TEST[role(name)], run_dir, tracker)
    return {'pass': row['pass'], 'state': f'{sketch} restored' if row['pass'] else 'unknown',
            'evidence': row['detail']}


# ---- the battery

PENDING_KIND = 'arduino-hil'


def proven_ended(receipt):
    """A run_tracked receipt that rechecks done; an unrecheckable entry (Tracker._note) never is."""
    return 'unrecheckable' not in receipt and A.process.recheck(receipt) == 'done'


def pending_problem(name):
    """'' when no earlier invocation left a child on the board unproven, else why (agentrc PendingSessions)."""
    try:
        A.lock.PendingSessions(name, PENDING_KIND).clear(lambda s: proven_ended(s['receipt']))
    except A.lock.PendingRefused as e:
        return f'pending children of an earlier invocation: {e}'
    return ''


def mark_pending(name, tracker):
    """Record the unproven children so every later invocation refuses until they end; errors as text."""
    try:
        A.lock.PendingSessions(name, PENDING_KIND).add(tracker.pending)
        return ''
    except Exception as e:
        return f'pending record not written: {e}'


def probe_present(flasher):
    """'' when exactly one probe with the flasher's serial is on this host's bus, else why not."""
    if flasher['name'] == 'esptool':
        try:
            A.flash.esptool_port(flasher['uid'])
            return ''
        except RuntimeError as e:
            return str(e)
    if flasher['name'] == 'jlink':
        serials = {d['busport']: d['serial'] for d in A.usb.usb_scan(vid='1366')}
        found = A.usb.jlink_serial_candidates(flasher['uid'], serials)
    else:
        vid_pid = tuple(v[2:] for v in flasher['vid_pid'].split()) if 'vid_pid' in flasher else None
        found = [d['busport'] for d in A.usb.usb_scan(vid_pid=vid_pid, serial=flasher['uid'])]
    if len(found) == 1:
        return ''
    return f'probe {flasher["uid"]} is not on this host\'s bus' if not found else \
        f'probe {flasher["uid"]} is ambiguous: {found}'


def run_board(name, board, tests, record_path, roster_path, run_root, manifest):
    run_dir = Path(run_root) / name
    run_dir.mkdir(parents=True, exist_ok=True)
    row = {'board': name, 'ran': False, 'pass': False, 'locked': False, 'recoveryBlocker': False, 'tests': [],
           'cleanup': {'pass': True, 'state': 'untouched', 'evidence': '', 'holder': None, 'receipts': []},
           'firmware': None, 'detail': ''}
    why = probe_present(board['flasher'])
    if why:
        row['detail'] = f'not run: {why}'
        return row
    try:
        tracker = Tracker(run_dir)
    except OSError as e:
        row['detail'] = f'refused: run dir {run_dir}: {e}'
        return row
    try:
        fh = A.lock.take_board_lock(name, 'hil_test.py', protected=True, locks=[name], runDir=str(run_dir))
    except A.lock.RecoveryBlocked as e:
        row.update(locked=True, recoveryBlocker=True, detail=f'recovery blocker: {e}')
        return row
    except A.lock.BoardLocked as e:
        row.update(locked=True, detail=f'board locked: {e}')
        return row
    except A.lock.LockUnavailable as e:
        row.update(detail=f'board lock unavailable: {e}')
        return row
    started, unclean = time.monotonic(), ''
    try:
        why = pending_problem(name)
        if why:
            row.update(recoveryBlocker=True, detail=why)
            return row
        try:
            rec, images = admit(record_path, name, roster_path, run_dir, manifest=manifest)
        except Refused as e:
            row['detail'] = f'refused: {e}'
            return row
        row['firmware'] = {'buildRecord': str(record_path), 'head': rec['source']['head'], 'fqbn': rec['fqbn'],
                           'platform': rec['platform'], 'sketches': {k: v['sha256'] for k, v in images.items()}}
        for test in tests:
            if BOARD_BUDGET - (time.monotonic() - started) < STEP_MAX + CLEANUP_RESERVE:
                row['tests'].append({'test': test, 'pass': False, 'detail': 'not run: board budget left too small'})
                continue
            row['ran'] = True
            row['cleanup']['state'] = 'unknown'
            row['tests'].append(step(name, board, images[TESTS[test]], test, run_dir, tracker))
        if row['ran']:
            row['cleanup'].update(restore(name, board, images, run_dir, tracker))
        row['pass'] = row['ran'] and all(t['pass'] for t in row['tests']) and row['cleanup']['pass']
    except Unclean as e:
        unclean = str(e)
        row['cleanup'].update({'pass': False, 'state': 'pending', 'evidence': unclean})
        row['detail'] = f'a child outlived its operation: {unclean}'
    except BaseException as e:   # interrupted or a harness bug: the board's state is unknown
        unclean = f'{type(e).__name__}: {e}'
        row['cleanup'].update({'pass': False, 'state': 'unknown', 'evidence': unclean})
        row['detail'] = f'aborted: {unclean}'
        row['interrupted'] = not isinstance(e, Exception)
    finally:
        row['cleanup']['receipts'] = list(tracker.receipts)
        if tracker.pending:   # unrecheckable ones included: every later invocation refuses until a reviewed clear
            row['cleanup'].update(pending=list(tracker.pending), markError=mark_pending(name, tracker))
        if row['cleanup']['pass']:
            A.lock.release_board_lock(fh)
        else:
            row['cleanup']['holder'] = hand_over(fh, name, row, tracker)
    return row


def hand_over(fh, name, row, tracker):
    """Keep the board held for recovery: a cleanup holder, or the protected record left as the blocker."""
    try:
        token = A.lock.hand_to_cleanup_holder({name: fh}, 'hil_test.py', detail=row['cleanup']['evidence'],
                                              receipts=tuple(tracker.receipts))
        return {'token': token}
    except Exception as e:   # the protected record stays on the free flock: a recovery blocker naming this run
        fh.close()
        return {'token': None, 'error': f'hand-over failed, protected record left as the blocker: {e}'}


REPORT_BODY_KEYS = ('pass', 'caveat', 'exitCode', 'results')


def schema():
    def check(body):
        if not all(k in body for k in REPORT_BODY_KEYS) or not isinstance(body['results'], list):
            raise ValueError(f'report body needs {REPORT_BODY_KEYS}')

    return A.report.BodySchema(name=REPORT_SCHEMA, version=1, stem='hil_report', evidence='firmware-validation',
                               check=check, empty=lambda b: not b['results'], render=render)


def render(body):
    tests = sorted({t['test'] for r in body['results'] for t in r['tests']})
    mark = {True: '✅', False: '❌', None: '⚪'}
    rows = []
    for r in body['results']:
        cells = {t['test']: mark[t['pass']] for t in r['tests']}
        status = 'locked' if r['locked'] else ('' if r['ran'] else 'not run')
        rows.append([r['board']] + [cells.get(t, '⚪') for t in tests] +
                    [mark[r['cleanup']['pass']] + ' ' + r['cleanup']['state'], status or r['detail'][:80]])
    table = A.report.table(['board'] + tests + ['cleanup', 'note'], rows)
    lines = ['# HIL report', '', table, '', f'pass: {body["pass"]}, exit {body["exitCode"]}']
    if body['caveat']:
        lines.append(f'caveat: {body["caveat"]}')
    for r in body['results']:
        for t in r['tests']:
            if not t['pass']:
                lines.append(f'- {r["board"]} {t["test"]}: {t["detail"]}')
        if r['detail']:
            lines.append(f'- {r["board"]}: {r["detail"]}')
    return '\n'.join(lines) + '\n'


def verdict(row):
    """The `hil` skill's verdict of one report row."""
    c = row['cleanup']
    if row['recoveryBlocker'] or c.get('holder') or c['state'] in ('pending', 'unknown') and row['ran']:
        return 'blocker'
    if not row['ran']:
        return 'not-run'
    return 'pass' if row['pass'] else 'fail'


def exit_code(rows):
    if any(r['cleanup'].get('holder') for r in rows):
        return 4
    return 0 if rows and all(r['pass'] for r in rows) else 1


def cmd_run(args):
    staged = verify_staged(args.staged) if args.staged else None
    load_agentrc(bool(staged))
    roster = load_roster(args.roster)
    picked = selection(roster, args.board, args.test)
    names = list(picked)
    try:
        import serial  # noqa: F401
    except ImportError:
        raise Refused('pyserial is missing (python3 -m pip install pyserial, or the distro package)')
    for b in names:
        tool = {'openocd': 'openocd', 'jlink': 'JLinkExe', 'esptool': 'esptool'}[roster[b]['flasher']['name']]
        if not shutil.which(tool):
            raise Refused(f'{b}: {tool} not on PATH')
    run_root = Path(args.run_dir or ROOT / 'build' / 'hil-run' / time.strftime('%Y%m%d-%H%M%S'))
    run_root.mkdir(parents=True, exist_ok=True)
    sch = schema()
    attempt = A.report.start_attempt(run_root, sch, accumulate=False)
    rows, termination = [], 'completed'
    try:
        for b in names:
            record = Path(args.record_dir) / f'{b}.build.json'
            rows.append(run_board(b, roster[b], picked[b], record, Path(args.roster), run_root, staged))
            if rows[-1].pop('interrupted', False):
                termination = 'aborted'
                break
    except KeyboardInterrupt:   # between boards: none is held
        termination = 'aborted'
    code = exit_code(rows) if termination == 'completed' else 4
    body = {'pass': code == 0, 'caveat': '' if termination == 'completed' else 'aborted by a signal',
            'exitCode': code, 'results': rows}
    meta = A.report.make_meta(sch, attempt, termination=termination, invocation=sys.argv, selection=names,
                              host=socket.gethostname(), roster=A.report.roster_identity(args.roster))
    out = dict(body)
    try:
        out['reportPath'] = str(A.report.publish(run_root, sch, body, meta)[0])
    except A.report.ReportError as e:
        recovery = A.report.recover_publish(run_root, sch, attempt, e)
        out['reportPath'] = str(run_root / 'hil_report.json') if recovery.outcome == 'repaired' else None
        out['reportError'] = f'{e}; recovery {recovery.outcome}: {recovery.problems}'
    emit(out)
    return code


def cmd_report(args):
    load_agentrc(staged_tree())
    sch = schema()
    reading = A.report.read_document(Path(args.run_dir) / 'hil_report.json', sch)
    if reading.outcome != 'valid':
        raise Refused(f'report in {args.run_dir}: {reading.outcome}')
    body, meta = reading.body, reading.meta
    requested = args.board or list(meta['selection'])
    rows = {r['board']: r for r in body['results']}
    verdicts = [{'board': b, 'verdict': verdict(rows[b]), 'detail': rows[b]['detail'], 'evidence': []}
                for b in requested if b in rows]
    folded = A.report.apply_run_rules(sch, requested, verdicts, meta['termination'], body['caveat'])
    results = [dict(rows.get(v['board'], {'board': v['board'], 'ran': None, 'pass': False}), verdict=v['verdict'],
                    verdictDetail=v['detail']) for v in folded['rows']]
    emit({'pass': folded['verdict'] == 'pass', 'results': results, 'caveat': body['caveat'],
          'wedged': [r['board'] for r in results if r.get('ran') and
                     r.get('cleanup', {}).get('state') in ('pending', 'unknown')],
          'reportPath': str(Path(args.run_dir) / 'hil_report.json')})
    return 0


def held_by(name, reason):
    rec = A.lock.read_record(name)
    if not isinstance(rec, dict) or rec.get('reason') != reason or not A.lock.is_locked(name):
        raise Refused(f'{name} is not held under reason {reason!r} by a live holder (record: {rec})')


@contextlib.contextmanager
def one_session_command(name):
    """One session command at a time per tree and board: the hold admits every command under its reason, and a
    command a dropped ssh left running must finish, its pending children recorded, before the next one starts."""
    d = ROOT / 'build' / 'hil-manual'
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f'.{name}.lock', 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused(f'another session command on {name} is still running in {ROOT}')
        yield


def manual(args):
    with one_session_command(args.board):
        return _manual(args)


def _manual(args):
    """A session command on a board the caller holds: OPS[args.cmd](args, board, run_dir, tracker, manifest) -> row.
    The JSON goes to stdout and to result.json in the run dir, which outlives a dropped ssh session."""
    manifest = verify_staged(args.staged) if args.staged else None
    load_agentrc(manifest is not None)
    roster = load_roster(args.roster)
    if args.board not in roster:
        raise Refused(f'{args.board} is not in {args.roster}')
    held_by(args.board, args.under_hold)
    why = pending_problem(args.board)
    if why:
        raise Refused(why)
    run_dir = ROOT / 'build' / 'hil-manual' / f'{args.board}-{args.cmd}-{time.strftime("%Y%m%d-%H%M%S")}'
    run_dir.mkdir(parents=True, exist_ok=True)
    tracker = Tracker(run_dir)
    try:
        row = OPS[args.cmd](args, roster[args.board], run_dir, tracker, manifest)
        out, code = {'pass': row['pass'], 'board': args.board, **row, 'survivors': [], 'log': str(tracker.log)}, \
            0 if row['pass'] else 1
    except BaseException as e:   # an interrupt or crash can leave a child as unproven as Unclean does
        if not tracker.pending and not isinstance(e, Unclean):
            raise
        out, code = {'pass': False, 'board': args.board,
                     'error': f'a child outlived its operation: {type(e).__name__}: {e}',
                     'survivors': tracker.pending, 'markError': mark_pending(args.board, tracker),
                     'note': 'keep the hold: every later invocation refuses until these end (Recovery)'}, 4
    (run_dir / 'result.json').write_text(json.dumps(out) + '\n')
    emit(out)
    return code


def bundle_root(bundle):
    """The tree-relative dir a session staged a record bundle into (hil_remote.py), or the checkout."""
    if not bundle:
        return ROOT
    d = (ROOT / bundle).resolve()
    if not d.is_relative_to(ROOT.resolve()) or not d.is_dir():
        raise Refused(f'--bundle {bundle} is not a directory inside {ROOT}')
    return d


def admit_sketch(args, sketch, run_dir, manifest, experimental_ok):
    base = bundle_root(args.bundle)
    rec, images = admit(base / args.build_record, args.board, args.roster, run_dir, manifest=manifest,
                        experimental_ok=experimental_ok, base=base)
    if sketch not in images:
        raise Refused(f'{args.build_record} has no sketch {sketch}; it has {sorted(images)}')
    return {'record': str(args.build_record), 'experimental': rec['experimental']}, images[sketch], images


def op_flash(args, board, run_dir, tracker, manifest):
    meta, image, _ = admit_sketch(args, args.sketch, run_dir, manifest, True)
    return {**meta, **flash_only(args.board, board, image, run_dir, tracker)}


def op_restore(args, board, run_dir, tracker, manifest):
    if args.report:
        failed = json.loads(Path(args.report).read_text())
        cleanups = [row['cleanup'] for row in failed['results'] if row['board'] == args.board]
        receipts = [r for c in cleanups for r in c.get('receipts', [])]
        receipts += [p['receipt'] for c in cleanups for p in c.get('pending', []) if p['receipt'] not in receipts]
        open_receipts = [r for r in receipts if not proven_ended(r)]
        if open_receipts:
            raise Refused(f'children of the failed run are not proven ended: {open_receipts}')
    meta, _, images = admit_sketch(args, restore_sketch(args.board), run_dir, manifest, False)
    row = {**meta, **restore(args.board, board, images, run_dir, tracker)}
    if args.release and row['pass']:
        row['released'] = release_hold(args.board)
        row['pass'] = row['released']['exit'] == 0
    return row


def release_hold(name):
    """End the caller's hold once its board is restored; under the session lock, so no command slips in between."""
    r = subprocess.run([sys.executable, str(A.root / LOCK_SCRIPT), 'release', name],
                       capture_output=True, text=True, timeout=30)
    return {'exit': r.returncode, 'output': (r.stdout + r.stderr).strip()[-1000:]}


def run_child(argv, timeout, tracker):
    r = tracker.run(argv, timeout=timeout)
    tracker.check()
    out = _text(r.stdout) + _text(r.stderr)
    return {'pass': r.returncode == 0, 'exit': r.returncode, 'output': out[-4000:]}


def op_exec(args, board, run_dir, tracker, manifest):
    return run_child(args.argv, args.timeout, tracker)


def esp_openocd(dbg, chip):
    """openocd-esp32 on the board's USB-Serial/JTAG through agentrc's hil_openocd_esp checks: the verified build and its
    own scripts, the USJ pinned by bus-port before its serial (a serial alone opens every 303a:1001 on the host first),
    and ESP_FLASH_SIZE 0 before the target cfg, so gdb-attach skips its `reset halt` for memory protection
    (esp_common.cfg:321). ValueError or RuntimeError when the route cannot be built."""
    esp = A.esp()
    found, cfgs = esp.admit({'name': 'openocd_esp', 'uid': dbg['uid'], 'args': dbg['openocd']})
    if found != chip:
        raise ValueError(f'debug.openocd names {found}, the board is an {chip}')
    tool = esp.find_openocd(dbg['tool'])
    esp.check_cfgs(found, cfgs, tool['scripts'])
    files = [os.path.join(tool['scripts'], c) for c in cfgs]
    return [tool['exe'], '-s', tool['scripts'], '-c', 'tcl port disabled', '-c', 'telnet port disabled',
            '-c', 'set ESP_FLASH_SIZE 0', '-f', files[0], '-c', f'adapter usb location {esp.usj_location(dbg["uid"])}',
            '-c', f'adapter serial {dbg["uid"]}', *(w for f in files[1:] for w in ('-f', f))]


def gdb_argv(name, board, elf, commands):
    """gdb-multiarch in batch mode over an openocd it starts on a pipe, configured by the roster's debug.openocd and
    pinned to the board's probe. The firmware must run on once gdb ends:
    - openocd halts the target on gdb's connect and has no default gdb-detach handler, so one resumes it; it runs when
      the connection closes (gdb_server.c gdb_connection_closed), i.e. when gdb exits, not at `detach`;
    - with a memory map openocd probes every flash bank on connect (gdb_server.c gdb_new_connection), and the
      RP2040's probe leaves the QSPI flash out of XIP mode: no map, no probe;
    - without a map gdb takes flash for RAM and would patch a software breakpoint into it, so every breakpoint is
      made a hardware one (openocd.texi, gdb_breakpoint_override).
    debug.tool openocd-esp32 runs Espressif's openocd on the chip's USB-Serial/JTAG (debug.uid, esp_openocd)."""
    dbg = board['debug']
    if dbg.get('tool') == 'openocd-esp32':
        openocd = esp_openocd(dbg, check_build.BOARDS[name]['chip'])
    else:
        probe = {'name': 'openocd', 'uid': board['flasher']['uid'], 'args': dbg['openocd']}
        if board['flasher']['name'] == 'openocd' and 'vid_pid' in board['flasher']:
            probe['vid_pid'] = board['flasher']['vid_pid']
        openocd = A.flash.openocd_cmd_base(probe)
    cfgs = shlex.split(dbg['openocd'])   # the silicon: the RP2350 boards share the rp2040 build family
    handler = next((h for cfg, h in GDB_DETACH_RESUME.items() if cfg in cfgs), '{ resume }')
    openocd += [*GDB_SERVER, '-c', f'foreach t [target names] {{ $t configure -event gdb-detach {handler} }}']
    return ['gdb-multiarch', '-batch', '-nx', '-ex', f'target extended-remote | {shlex.join(openocd)}',
            *(w for c in commands for w in ('-ex', c)), elf]


def op_gdb(args, board, run_dir, tracker, manifest):
    if not board.get('debug', {}).get('openocd'):
        raise Refused(f'{args.board} has no debug.openocd in {args.roster}')
    meta, image, _ = admit_sketch(args, args.sketch, run_dir, manifest, True)
    try:
        argv = gdb_argv(args.board, board, image['elf'], args.ex)
    except (ValueError, RuntimeError) as e:
        return {**meta, 'pass': False, 'detail': f'debug route: {e}'}
    return {**meta, **run_child(argv, args.timeout, tracker)}


def op_console(args, board, run_dir, tracker, manifest):
    """Capture the board's console for --seconds into console.log, after a probe reset with --reset. An esptool
    board's reset takes the same bridge tty, so it runs first and the boot's first lines may be lost."""
    import serial
    reset_first = args.reset and board['flasher']['name'] == 'esptool'
    if reset_first:
        why = reset(board['flasher'], tracker)
        if why:
            return {'pass': False, 'detail': f'reset: {why}'}
    try:
        tty = console_tty(board, time.monotonic() + 5)
    except (RuntimeError, TimeoutError) as e:
        return {'pass': False, 'detail': f'console: {e}'}
    log, text = run_dir / 'console.log', b''
    with serial.Serial(tty, args.baud, timeout=0.2) as port:
        port.reset_input_buffer()
        if args.reset and not reset_first:
            why = reset(board['flasher'], tracker)
            if why:
                return {'pass': False, 'detail': f'reset: {why}'}
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            text += port.read(4096)
    log.write_bytes(text)
    return {'pass': True, 'detail': f'{tty}: {len(text)} bytes in {args.seconds:g} s', 'console': str(log),
            'tail': text.decode(errors='replace')[-2000:]}


OPS = {'flash': op_flash, 'restore': op_restore, 'gdb': op_gdb, 'exec': op_exec, 'console': op_console}


def flash_only(name, board, image, run_dir, tracker):
    if role(name) == 'host':   # no DUT on this host's bus: the console shows what runs
        why = flash(board['flasher'], image, run_dir, tracker)
        return {'pass': not why, 'detail': why or f'flashed {Path(image["elf"]).name} (a host board: read its console)'}
    why, detached, dev = flash_and_enumerate(name, board, image, run_dir, tracker)
    if why:
        return {'pass': False, 'detail': why, 'detach': detached}
    return {'pass': True, 'detail': f'flashed {Path(image["elf"]).name}; ' +
            (f'enumerated as {image["vidPid"]} at {dev["busport"]}' if dev else
             f'no {image["vidPid"]} {dut_name(board)} enumerated (the image may not run USB)')}


def cmd_commission(args):
    """Find the DUT's identity under Arduino firmware: exactly one device of the record's VID:PID must re-enumerate
    across the flash's reset and again across a probe reset, with one serial (a usb_port board: at one port)."""
    load_agentrc(False)
    roster = load_roster(args.roster)
    if args.board not in roster:
        raise Refused(f'{args.board} is not in {args.roster}')
    if role(args.board) == 'host':
        raise Refused(f'{args.board} is a host board: no DUT enumerates here, its battery checks the console')
    board = roster[args.board]
    by_port = 'usb_port' in board

    def enumerated(vid_pid):   # {identity: enumeration id}
        if by_port:
            return {d['busport']: d['ino'] for d in A.usb.usb_devices(vid_pid=tuple(vid_pid.split(':')))}
        return {d['serial']: d['ino'] for d in scan(vid_pid)}
    run_dir = ROOT / 'build' / 'hil-manual' / f'commission-{args.board}-{time.strftime("%Y%m%d-%H%M%S")}'
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        fh = A.lock.take_board_lock(args.board, 'hil_test.py commission', protected=True, locks=[args.board])
    except (A.lock.BoardLocked, A.lock.RecoveryBlocked, A.lock.LockUnavailable) as e:
        raise Refused(f'{args.board}: {e}')
    tracker, out, touched = Tracker(run_dir), {'board': args.board, 'rosterUid': board['uid']}, False
    try:
        why = pending_problem(args.board)
        if why:
            raise Refused(why)
        _, images = admit(Path(args.record_dir) / f'{args.board}.build.json', args.board, args.roster, run_dir)
        image = images[restore_sketch(args.board)]
        observed = []
        for action in ('flash', 'reset'):
            before = enumerated(image['vidPid'])
            touched = True
            if action == 'flash':
                detach(args.board, board, image['vidPid'], tracker)
                why = flash(board['flasher'], image, run_dir, tracker)
            else:
                detach(args.board, board, image['vidPid'], tracker)
                why = reset(board['flasher'], tracker)
            if why:
                raise RuntimeError(f'{action}: {why}')
            time.sleep(ENUM_TIMEOUT / 4)
            deadline, changed = time.monotonic() + ENUM_TIMEOUT, set()
            while time.monotonic() < deadline and not changed:
                changed = {k for k, ino in enumerated(image['vidPid']).items() if before.get(k) != ino}
                time.sleep(0.5)
            observed.append(sorted(changed))
        out['observed'] = observed
        found = {s for obs in observed for s in obs}
        if not (all(len(o) == 1 for o in observed) and len(found) == 1):
            raise RuntimeError(f'ambiguous: not exactly one device re-enumerated with one '
                               f'{"port" if by_port else "serial"} both times')
        if by_port:
            out['port'] = found.pop()
            out['matchesRoster'] = out['port'] == board['usb_port']
            dev = next(d for d in A.usb.usb_devices(vid_pid=tuple(image['vidPid'].split(':')))
                       if d['busport'] == out['port'])
        else:
            out['serial'] = found.pop()
            out['matchesRoster'] = out['serial'].lower() == board['uid'].lower()
            dev = scan(image['vidPid'], out['serial'])[0]
        ok, out['cleanup'] = test_cdc(dev, secrets.token_hex(4), time.monotonic() + TEST_TIMEOUT, tracker, run_dir)
        if not ok:
            raise RuntimeError(f'serial_echo left running does not echo: {out["cleanup"]}')
    except Refused:
        A.lock.release_board_lock(fh)
        raise
    except BaseException as e:   # after hardware access: the board stays held for recovery
        if not touched:
            A.lock.release_board_lock(fh)
            raise
        out['error'] = f'{type(e).__name__}: {e}'
        row = {'board': args.board, 'cleanup': {'evidence': out['error'], 'receipts': list(tracker.receipts)}}
        if tracker.pending:   # as run_board: every later invocation refuses until they end
            row['cleanup'].update(pending=list(tracker.pending), markError=mark_pending(args.board, tracker))
        out['holder'] = row['cleanup']['holder'] = hand_over(fh, args.board, row, tracker)
        out['results'] = [row]   # result.json is a `restore --report` input
        (run_dir / 'result.json').write_text(json.dumps(out) + '\n')
        emit(out)
        return 4
    else:   # outside the handlers: an interrupt during the release must not hand over a released lock
        A.lock.release_board_lock(fh)
    emit(out)
    return 0 if out.get('matchesRoster') else 1


def cmd_read_block(args):
    """Child of the MSC test: read the whole block device into a file (bounded and reaped by run_tracked)."""
    with open(args.device, 'rb', buffering=0) as dev, open(args.out, 'wb') as out:
        out.write(dev.read(MSC_DISK_BYTES))
    return 0


def cmd_lock(rest):
    os.execv(sys.executable, [sys.executable, str(agentrc_root(staged_tree()) / LOCK_SCRIPT), *rest])


def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('-b', '--board', action='append')
    r.add_argument('-t', '--test', action='append', choices=sorted(TESTS))
    r.add_argument('--record-dir', default=str(ROOT / 'build' / 'hil'))
    r.add_argument('--run-dir')
    r.add_argument('--staged', help='the manifest of a tree hil_remote.py staged (rig side)')
    r.add_argument('roster')
    c = sub.add_parser('commission')
    c.add_argument('-b', '--board', required=True)
    c.add_argument('--record-dir', default=str(ROOT / 'build' / 'hil'))
    c.add_argument('roster')
    for name in ('flash', 'restore', 'gdb', 'exec', 'console'):
        m = sub.add_parser(name)
        m.add_argument('-b', '--board', required=True)
        m.add_argument('--under-hold', required=True, metavar='REASON')
        m.add_argument('--staged', help='the manifest of a tree hil_remote.py staged (rig side)')
        if name == 'console':
            m.add_argument('--seconds', type=float, default=10, help=f'at most {CONSOLE_MAX}')
            m.add_argument('--baud', type=int, default=115200)
            m.add_argument('--reset', action='store_true', help='reset the target through its probe first')
        if name not in ('exec', 'console'):
            m.add_argument('--build-record', required=True)
            m.add_argument('--bundle', help='a staged record bundle (tree-relative); --build-record is inside it')
        if name in ('flash', 'gdb'):
            m.add_argument('--sketch', required=True)
        if name == 'restore':
            m.add_argument('--report', help="a failed run's report: its children must be proven ended first")
            m.add_argument('--release', action='store_true',
                           help="release the hold once the board's restoration sketch is restored")
        if name in ('gdb', 'exec'):
            m.add_argument('--timeout', type=float, default=60, help=f'seconds, at most {EXEC_MAX}')
        if name == 'gdb':
            m.add_argument('--ex', action='append', default=[], help='a gdb command, run in order after the attach')
        m.add_argument('roster')
        if name == 'exec':
            m.add_argument('argv', nargs='+')
    rp = sub.add_parser('report')
    rp.add_argument('run_dir')
    rp.add_argument('-b', '--board', action='append')
    rb = sub.add_parser('read-block')
    rb.add_argument('device')
    rb.add_argument('out')
    return ap.parse_args(argv)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['lock']:
        return cmd_lock(argv[1:])
    args = parse(argv)
    if args.cmd in ('gdb', 'exec') and not 0 < args.timeout <= EXEC_MAX:
        emit({'pass': False, 'error': f'--timeout must be in (0, {EXEC_MAX}]'})
        return 2
    if args.cmd == 'console' and not 0 < args.seconds <= CONSOLE_MAX:
        emit({'pass': False, 'error': f'--seconds must be in (0, {CONSOLE_MAX}]'})
        return 2
    if args.cmd in ('flash', 'restore', 'gdb', 'exec', 'console'):
        # a session command runs over ssh: a dropped link must not end it between a child's start and its proof
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    if args.cmd in ('run', 'commission', 'flash', 'restore', 'gdb', 'exec', 'console'):
        # SIGTERM unwinds as SIGINT does: run_tracked ends the child, and run_board, cmd_commission and _manual
        # record one it could not prove ended
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        if args.cmd == 'run':
            return cmd_run(args)
        if args.cmd == 'commission':
            return cmd_commission(args)
        if args.cmd in ('flash', 'restore', 'gdb', 'exec', 'console'):
            return manual(args)
        if args.cmd == 'report':
            return cmd_report(args)
        return cmd_read_block(args)
    except (Refused, agentrc_pin.PinError) as e:
        emit({'pass': False, 'error': str(e)})
        return 2


if __name__ == '__main__':
    sys.exit(main())
