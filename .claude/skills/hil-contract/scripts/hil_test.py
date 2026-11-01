#!/usr/bin/env python3
"""The Adafruit TinyUSB Arduino HIL harness (SKILL.md beside this directory). Linux only.

  hil_test.py run [-b BOARD]... [-t TEST]... [--record-dir DIR] [--run-dir DIR] [--staged MANIFEST] ROSTER
  hil_test.py commission -b BOARD [--record-dir DIR] ROSTER
  hil_test.py flash -b BOARD --build-record R --sketch S --under-hold REASON ROSTER
  hil_test.py restore -b BOARD --build-record R --under-hold REASON [--report PATH] ROSTER
  hil_test.py report RUN_DIR [-b BOARD]...
  hil_test.py lock (hold|release|status) ...        the pinned agentrc hil_lock.py

The last stdout line of run, commission, flash, restore and report is JSON. Exits: 0 pass, 1 a board failed or did not
run (cleaned up or untouched), 2 refused before any hardware access, 4 a cleanup did not finish (board held).
"""

import argparse
import glob
import hashlib
import json
import os
import secrets
import select
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

SCRIPTS = ('skills/hil/scripts/hil_lock.py', 'skills/hil/scripts/hil_flash.py', 'skills/hil/scripts/hil_process.py',
           'skills/hil/scripts/hil_report_core.py', 'skills/hil/scripts/usb_observe.py')
TESTS = {'cdc_echo': 'serial_echo', 'hid_inout': 'hid_generic_inout', 'msc_ramdisk': 'msc_ramdisk'}
RESTORE_SKETCH = 'serial_echo'
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
BOARD_BUDGET = 900
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


def load_agentrc(staged):
    global A
    root = ROOT / 'agentrc' if staged else agentrc_pin.checkout()
    agentrc_pin.closure(root, SCRIPTS)
    hl = agentrc_pin.loader(root)
    A = argparse.Namespace(lock=hl.load_file(SCRIPTS[0]), flash=hl.load_file(SCRIPTS[1]),
                           process=hl.load_file(SCRIPTS[2]), report=hl.load_file(SCRIPTS[3]),
                           usb=hl.load_file(SCRIPTS[4]), root=root)
    return A


def emit(obj):
    print(json.dumps(obj), flush=True)


# ---- roster, staged manifest, build record

def load_roster(path):
    try:
        data = json.loads(Path(path).read_text())
        boards = {b['name']: b for b in data['boards']}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise Refused(f'roster {path}: {e}')
    for name, b in boards.items():
        if name not in check_build.BOARDS or 'span' not in check_build.BOARDS[name]:
            raise Refused(f'roster {path}: {name} has no rig profile in check_build.BOARDS')
        if b.get('flasher', {}).get('name') not in ('openocd', 'jlink'):
            raise Refused(f'roster {path}: {name}: flasher must be openocd or jlink')
        unknown = set(b.get('arduino', {}).get('tests', [])) - set(TESTS)
        if unknown:
            raise Refused(f'roster {path}: {name}: unknown tests {sorted(unknown)}')
    return boards


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


def admit(record_path, board, roster_path, run_dir, *, manifest=None, experimental_ok=False):
    """The record's images copied into the run dir once they hash as recorded, or Refused."""
    try:
        rec = json.loads(Path(record_path).read_text())
    except (OSError, ValueError) as e:
        raise Refused(f'build record {record_path}: {e}')
    if rec.get('kind') != 'arduino-hil-build' or rec.get('version') != check_build.SCHEMA:
        raise Refused(f'{record_path} is not a version {check_build.SCHEMA} arduino-hil-build record')
    if rec['board'] != board:
        raise Refused(f'{record_path} is for {rec["board"]}, not {board}')
    if rec['experimental'] and not experimental_ok:
        raise Refused(f'{record_path} is experimental: the battery and restore take clean records only')
    if manifest is not None:
        if rec['source']['head'] != manifest['head']:
            raise Refused(f'record built at {rec["source"]["head"]}, staged tree is {manifest["head"]}')
    elif not rec['experimental']:
        head, status = git_identity()
        if head != rec['source']['head']:
            raise Refused(f'record built at {rec["source"]["head"]}, checkout is at {head}: rebuild it')
        if status:
            raise Refused(f'the checkout has changes since the record: {status.splitlines()[:5]}')
    if check_build.sha256(roster_path) != rec['roster']['sha256']:
        raise Refused(f'{roster_path} changed since the record was built: rebuild it')
    flasher = load_roster(roster_path)[board]['flasher']['name']
    want = 'elf' if flasher == 'openocd' else 'hex'
    images = {}
    for name, s in rec['sketches'].items():
        if s.get('image') != want:
            raise Refused(f'{record_path}: {name} records a {s.get("image")} image, a {flasher} board flashes the '
                          f'{want}: rebuild against this roster')
        dest = Path(run_dir) / 'images' / name
        dest.mkdir(parents=True, exist_ok=True)
        for rel, digest in s['sha256'].items():
            data = (ROOT / rel).read_bytes() if (ROOT / rel).is_file() else None
            if data is None or hashlib.sha256(data).hexdigest() != digest:
                raise Refused(f'{rel} is missing or changed since the record')
            (dest / Path(rel).name).write_bytes(data)
        images[name] = {'elf': str(dest / Path(s['elf']).name),
                        'hex': str(dest / Path(s['hex']).name) if s.get('hex') else None,
                        'vidPid': s['vidPid'], 'example': s['example'], 'sha256': s['sha256']}
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
    words = flasher.get('args', '').split()
    return words[words.index('-device') + 1]


def flash(flasher, image, run_dir, tracker):
    """Program and verify one image. Returns '' or why it failed; raises Unclean when a child outlived it."""
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
    if not cmds or board['flasher']['name'] != 'openocd':
        return ''
    op = A.flash.run_openocd_commands(board['flasher'], cmds, timeout=VERIFY_TIMEOUT, run=tracker.run)
    tracker.check()
    out = _text(op.stdout) + _text(op.stderr)
    if op.returncode or 'Error' in out:
        return f'detach session failed (exit {op.returncode})'
    deadline = time.monotonic() + DETACH_WAIT
    while time.monotonic() < deadline:
        if not scan(vid_pid, board['uid']):
            return 'detached'
        time.sleep(0.2)
    return f'still enumerated {DETACH_WAIT}s after the detach'


def flash_fresh(name, board, image, run_dir, tracker):
    """Detach, then flash: (why it failed or '', detach evidence)."""
    evidence = detach(name, board, image['vidPid'], tracker)
    return flash(board['flasher'], image, run_dir, tracker), evidence


def reset(flasher, tracker):
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


def wait_enumerated(vid_pid, serial, before, timeout=ENUM_TIMEOUT):
    """The device with this VID:PID and serial once it enumerated anew (an enumeration id other than `before`)
    and stayed for a second, or None."""
    deadline, seen, since = time.monotonic() + timeout, None, 0
    while time.monotonic() < deadline:
        devs = [d for d in scan(vid_pid, serial) if d['ino'] != before]
        if len(devs) == 1:
            if seen and seen['ino'] == devs[0]['ino'] and time.monotonic() - since >= 1:
                return devs[0]
            if not seen or seen['ino'] != devs[0]['ino']:
                seen, since = devs[0], time.monotonic()
        else:
            seen = None
        time.sleep(0.25)
    return None


def enumeration_of(vid_pid, serial):
    devs = scan(vid_pid, serial)
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
            port.write(p)
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


def step(name, board, image, test, run_dir, tracker):
    """Flash the test's sketch, wait for the DUT to enumerate anew, run the host check. A dict row."""
    started = time.monotonic()
    vid_pid, uid = image['vidPid'], board['uid']
    before = enumeration_of(vid_pid, uid)
    why, detached = flash_fresh(name, board, image, run_dir, tracker)
    if why:
        return {'test': test, 'pass': False, 'detail': why, 'detach': detached}
    dev = wait_enumerated(vid_pid, uid, before)
    if not dev:
        others = [d['serial'] for d in scan(vid_pid)]
        return {'test': test, 'pass': False, 'detail': f'no {vid_pid} with serial {uid} enumerated within '
                                                       f'{ENUM_TIMEOUT}s (present: {others})'}
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
    row = step(name, board, images[RESTORE_SKETCH], 'cdc_echo', run_dir, tracker)
    return {'pass': row['pass'], 'state': 'serial_echo restored' if row['pass'] else 'unknown',
            'evidence': row['detail']}


# ---- the battery

PENDING_KIND = 'arduino-hil'


def pending_problem(name):
    """'' when no earlier invocation left a child on the board unproven, else why (agentrc PendingSessions)."""
    try:
        A.lock.PendingSessions(name, PENDING_KIND).clear(lambda s: A.process.recheck(s['receipt']) == 'done')
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
    if flasher['name'] == 'jlink':
        serials = {d['busport']: d['serial'] for d in A.usb.usb_scan(vid='1366')}
        found = A.usb.jlink_serial_candidates(flasher['uid'], serials)
    else:
        vid_pid = tuple(v[2:] for v in flasher['vid_pid'].split()) if 'vid_pid' in flasher else None
        found = [d['busport'] for d in A.usb.usb_scan(vid_pid=vid_pid) if d['serial'].lower() ==
                 flasher['uid'].lower()]
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
    names = args.board or list(roster)
    unknown = [b for b in names if b not in roster]
    if unknown:
        raise Refused(f'not in {args.roster}: {unknown}')
    try:
        import serial  # noqa: F401
    except ImportError:
        raise Refused('pyserial is missing (python3 -m pip install pyserial, or the distro package)')
    for b in names:
        tool = {'openocd': 'openocd', 'jlink': 'JLinkExe'}[roster[b]['flasher']['name']]
        if not any(os.access(os.path.join(p, tool), os.X_OK) for p in os.environ.get('PATH', '').split(':')):
            raise Refused(f'{b}: {tool} not on PATH')
    run_root = Path(args.run_dir or ROOT / 'build' / 'hil-run' / time.strftime('%Y%m%d-%H%M%S'))
    run_root.mkdir(parents=True, exist_ok=True)
    sch = schema()
    attempt = A.report.start_attempt(run_root, sch, accumulate=False)
    rows, termination = [], 'completed'
    try:
        for b in names:
            tests = args.test or roster[b].get('arduino', {}).get('tests', [])
            record = Path(args.record_dir) / f'{b}.build.json'
            rows.append(run_board(b, roster[b], tests, record, Path(args.roster), run_root, staged))
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
    load_agentrc(staged=not (ROOT / '.git').exists())
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


def manual(args, sketch, experimental_ok, cleanup):
    load_agentrc(False)
    roster = load_roster(args.roster)
    if args.board not in roster:
        raise Refused(f'{args.board} is not in {args.roster}')
    held_by(args.board, args.under_hold)
    why = pending_problem(args.board)
    if why:
        raise Refused(why)
    run_dir = ROOT / 'build' / 'hil-manual' / f'{args.board}-{time.strftime("%Y%m%d-%H%M%S")}'
    run_dir.mkdir(parents=True, exist_ok=True)
    if getattr(args, 'report', None):
        failed = json.loads(Path(args.report).read_text())
        open_receipts = [r for row in failed['results'] if row['board'] == args.board
                         for r in row['cleanup'].get('receipts', [])
                         if A.process.recheck(r) != 'done']
        if open_receipts:
            raise Refused(f'children of the failed run are not proven ended: {open_receipts}')
    rec, images = admit(args.build_record, args.board, args.roster, run_dir, experimental_ok=experimental_ok)
    if sketch not in images:
        raise Refused(f'{args.build_record} has no sketch {sketch}; it has {sorted(images)}')
    tracker = Tracker(run_dir)
    try:
        if cleanup:
            row = restore(args.board, roster[args.board], images, run_dir, tracker)
            ok = row['pass']
        else:
            row = flash_only(args.board, roster[args.board], images[sketch], run_dir, tracker)
            ok = row['pass']
    except Unclean as e:
        emit({'pass': False, 'board': args.board, 'error': f'a child outlived its operation: {e}',
              'survivors': tracker.pending, 'markError': mark_pending(args.board, tracker),
              'note': 'keep the hold: every later invocation refuses until these end (Recovery)'})
        return 4
    emit({'pass': ok, 'board': args.board, 'record': str(args.build_record), 'experimental': rec['experimental'],
          **row, 'survivors': [], 'log': str(tracker.log)})
    return 0 if ok else 1


def flash_only(name, board, image, run_dir, tracker):
    before = enumeration_of(image['vidPid'], board['uid'])
    why, detached = flash_fresh(name, board, image, run_dir, tracker)
    if why:
        return {'pass': False, 'detail': why, 'detach': detached}
    dev = wait_enumerated(image['vidPid'], board['uid'], before)
    return {'pass': True, 'detail': f'flashed {Path(image["elf"]).name}; ' +
            (f'enumerated as {image["vidPid"]} at {dev["busport"]}' if dev else
             f'no {image["vidPid"]} with serial {board["uid"]} enumerated (the image may not run USB)')}


def cmd_commission(args):
    """Find the DUT's serial under Arduino firmware: exactly one device of the record's VID:PID must re-enumerate
    across the flash's reset and again across a probe reset, with one serial."""
    load_agentrc(False)
    roster = load_roster(args.roster)
    if args.board not in roster:
        raise Refused(f'{args.board} is not in {args.roster}')
    board = roster[args.board]
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
        image = images[RESTORE_SKETCH]
        observed = []
        for action in ('flash', 'reset'):
            before = {d['serial']: d['ino'] for d in scan(image['vidPid'])}
            touched = True
            if action == 'flash':
                why = flash_fresh(args.board, board, image, run_dir, tracker)[0]
            else:
                detach(args.board, board, image['vidPid'], tracker)
                why = reset(board['flasher'], tracker)
            if why:
                raise RuntimeError(f'{action}: {why}')
            time.sleep(ENUM_TIMEOUT / 4)
            deadline, changed = time.monotonic() + ENUM_TIMEOUT, set()
            while time.monotonic() < deadline and not changed:
                changed = {d['serial'] for d in scan(image['vidPid']) if before.get(d['serial']) != d['ino']}
                time.sleep(0.5)
            observed.append(sorted(changed))
        out['observed'] = observed
        serials = {s for obs in observed for s in obs}
        if not (all(len(o) == 1 for o in observed) and len(serials) == 1):
            raise RuntimeError('ambiguous: not exactly one device re-enumerated with one serial both times')
        out['serial'] = serials.pop()
        out['matchesRoster'] = out['serial'].lower() == board['uid'].lower()
        dev = scan(image['vidPid'], out['serial'])[0]
        ok, out['cleanup'] = test_cdc(dev, secrets.token_hex(4), time.monotonic() + TEST_TIMEOUT, tracker, run_dir)
        if not ok:
            raise RuntimeError(f'serial_echo left running does not echo: {out["cleanup"]}')
        A.lock.release_board_lock(fh)
    except Refused:
        A.lock.release_board_lock(fh)
        raise
    except BaseException as e:   # after hardware access: the board stays held for recovery
        if not touched:
            A.lock.release_board_lock(fh)
            raise
        out['error'] = f'{type(e).__name__}: {e}'
        out['holder'] = hand_over(fh, args.board, {'cleanup': {'evidence': out['error']}}, tracker)
        emit(out)
        return 4
    emit(out)
    return 0 if out.get('matchesRoster') else 1


def cmd_read_block(args):
    """Child of the MSC test: read the whole block device into a file (bounded and reaped by run_tracked)."""
    with open(args.device, 'rb', buffering=0) as dev, open(args.out, 'wb') as out:
        out.write(dev.read(MSC_DISK_BYTES))
    return 0


def cmd_lock(rest):
    root = agentrc_pin.checkout() if (ROOT / '.git').exists() else ROOT / 'agentrc'
    os.execv(sys.executable, [sys.executable, str(root / 'skills/hil/scripts/hil_lock.py'), *rest])


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
    for name in ('flash', 'restore'):
        m = sub.add_parser(name)
        m.add_argument('-b', '--board', required=True)
        m.add_argument('--build-record', required=True)
        m.add_argument('--under-hold', required=True, metavar='REASON')
        if name == 'flash':
            m.add_argument('--sketch', required=True)
        else:
            m.add_argument('--report', help="a failed run's report: its children must be proven ended first")
        m.add_argument('roster')
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
    try:
        if args.cmd == 'run':
            return cmd_run(args)
        if args.cmd == 'commission':
            return cmd_commission(args)
        if args.cmd == 'flash':
            return manual(args, args.sketch, experimental_ok=True, cleanup=False)
        if args.cmd == 'restore':
            return manual(args, RESTORE_SKETCH, experimental_ok=False, cleanup=True)
        if args.cmd == 'report':
            return cmd_report(args)
        return cmd_read_block(args)
    except (Refused, agentrc_pin.PinError) as e:
        emit({'pass': False, 'error': str(e)})
        return 2


if __name__ == '__main__':
    sys.exit(main())
