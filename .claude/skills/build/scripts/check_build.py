#!/usr/bin/env python3
"""Build this library's example sketches with arduino-cli: the project's build contract (SKILL.md).

  check_build.py (--scope PATH... | --base REF [--worktree] | --diff-file FILE) [--example E]... [-j N]
  check_build.py --board B... [--example E]... [--shared] [--build-property K=V]...
  check_build.py --board B --shared --record FILE --hil-config ROSTER [--experimental --build-property K=V...]
  check_build.py --select-only (--scope ... | --base REF [--worktree] | --diff-file FILE) [--hil-config ROSTER]

stdout ends with one JSON line. Exit 0 pass, 1 a build or test failed, 2 usage or a missing
prerequisite ("error" names the remedy), 3 an uncovered path. 2 outranks 1, 1 outranks 3.
"""

import argparse
import concurrent.futures
import fcntl
import functools
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BUILD_ROOT = ROOT / 'build'
SCHEMA = 1
COMPILE_TIMEOUT = 900

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
    'CH32V20x_EVT': {'fqbn': 'WCH:ch32v:CH32V20x_EVT', 'family': 'ch32'},
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
}
# what a shared-code change builds: rig boards first, and each family's distinct stacks (samd21/samd51, PIO-USB host)
REPRESENTATIVES = {
    'rp2040': ['raspberry_pi_pico', 'pico_rp2040_tinyusb_host'],
    'samd': ['metro_m4_express', 'metro_m0_tinyusb'],
    'nrf': ['nrf52840'],
    'esp32': ['feather_esp32s3'],
    'ch32': ['CH32V20x_EVT'],
}
PORTABLE_FAMILY = {
    'raspberrypi/rp2040': 'rp2040', 'raspberrypi/pio_usb': 'rp2040', 'microchip/samd': 'samd',
    'nordic/nrf5x': 'nrf', 'synopsys/dwc2': 'esp32', 'wch': 'ch32',
}
# compiled only by MAX3421 host examples on every family
SHARED_PORTABLE = ('analog/max3421',)
# the sketches a HIL record carries (hil_test.py's tests run them; serial_echo is also the restoration image)
HIL_SKETCHES = ('examples/CDC/serial_echo', 'examples/HID/hid_generic_inout', 'examples/MassStorage/msc_ramdisk')
# headers the cores' own sources include from their bundled copy of this library, not from this checkout
# (seen in the .d files of rp2040:rp2040 5.4.3 and adafruit:samd 1.7.13 builds; builds report the live set)
CORE_CONSUMED = ('src/arduino/Adafruit_TinyUSB_API.h', 'src/arduino/Adafruit_USBD_CDC.h',
                 'src/arduino/Adafruit_USBD_Interface.h')
CONTRACT_TESTS = {'build': '.claude/test/test_build.py', 'hil': '.claude/test/test_hil.py',
                  'hil_remote': '.claude/test/test_hil_remote.py'}
HIL_CONTROL = ('.claude/skills/hil-contract/scripts/', 'test/hil/')
NOTHING = ('.claude/', '.github/', '.codespell/', 'docs/', 'tools/', '.agents', '.gitignore', '.codespellrc',
           '.pre-commit-config.yaml', 'library.properties', 'library.json', 'LICENSE')
LIBRARY_NAME = 'Adafruit TinyUSB Library'


def fail(msg, **extra):
    print(json.dumps({'version': SCHEMA, 'pass': False, 'error': msg, **extra}))
    sys.exit(2)


def git(*args, check=True):
    p = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True)
    if check and p.returncode:
        fail(f'git {" ".join(args)}: {p.stderr.strip()}')
    return p.stdout


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def rel(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


# ---- examples and ci-arduino's filters

@functools.lru_cache(maxsize=None)
def examples():
    """Every sketch dir (holding an .ino) under examples/, repo-relative, sorted."""
    return sorted({rel(p.parent) for p in (ROOT / 'examples').rglob('*.ino')})


@functools.lru_cache(maxsize=None)
def eligible(board, example):
    """ci-arduino build_platform.py test_examples_in_folder, for the board's filter platform."""
    platform = BOARDS[board].get('filters', board)
    folder = ROOT / example
    if (folder / f'.{platform}.test.skip').exists():
        return False
    skip_txt = folder / '.skip.txt'
    if skip_txt.exists() and any(line.strip() == platform for line in skip_txt.read_text().splitlines()):
        return False
    if any(folder.glob('.*.test.only')):
        return (folder / f'.{platform}.test.only').exists()
    return True


# ---- selection

def classify(path):
    """(effect, detail, reason) for one changed path, first match wins. effect: family | shared | example | tests |
    hil | nothing | uncovered."""
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
    if path.startswith('src/'):
        return 'shared', None, 'shared library code'
    if path.startswith('examples/'):
        for ex in examples():
            if path.startswith(ex + '/'):
                return 'example', ex, 'example sketch'
        if path.endswith(('.md', '.txt', '.js', '.json', '.py', '.html')):
            return 'nothing', None, 'example documentation or host-side helper'
        return 'uncovered', None, 'under examples/ but in no sketch dir'
    if path.startswith('.claude/skills/build/scripts/'):
        return 'tests', 'build', 'the build contract; its tests and smoke builds verify it'
    if path.startswith(HIL_CONTROL):
        return 'hil', None, 'the HIL harness or rosters: its tests, and the battery on every roster board'
    if path.startswith('.claude/test/'):
        name = Path(path).stem.removeprefix('test_')
        return ('tests', name, 'a contract test') if name in CONTRACT_TESTS else ('nothing', None, 'test helper')
    if path.endswith('.md') or path.startswith(NOTHING):
        return 'nothing', None, 'docs, agent or repo metadata'
    return 'uncovered', None, 'no rule classifies this path'


def select(paths, roster=None, only_examples=None):
    builds, tests, out_paths, uncovered = {}, set(), [], []
    hil_boards, hil_full = set(), False

    def add(board, exs):
        builds.setdefault(board, set()).update(e for e in exs if eligible(board, e))

    for path in paths:
        effect, detail, reason = classify(path)
        out_paths.append({'path': path, 'rule': effect, 'reason': reason})
        if effect == 'uncovered':
            uncovered.append({'path': path, 'reason': reason})
        elif effect in ('family', 'shared'):
            families = [detail] if effect == 'family' else list(REPRESENTATIVES)
            boards = [b for f in families for b in REPRESENTATIVES[f]]
            for b in boards:
                add(b, only_examples or examples())
            if roster:
                hil_boards.update(b for b in roster if BOARDS[b]['family'] in families)
        elif effect == 'example':
            if not any(eligible(b, detail) for b in BOARDS):
                uncovered.append({'path': path, 'reason': 'sketch eligible on no board this contract builds'})
            for b in BOARDS:
                add(b, [detail])
            if roster and detail in HIL_SKETCHES:
                hil_boards.update(roster)
        elif effect == 'tests':
            tests.add(detail)
            if detail == 'build':
                add('raspberry_pi_pico', [HIL_SKETCHES[0]])
                add('metro_m4_express', [HIL_SKETCHES[0]])
        elif effect == 'hil':
            tests.update(('hil', 'hil_remote'))
            hil_full = True
    result = {
        'builds': {b: sorted(e) for b, e in sorted(builds.items()) if e},
        'tests': sorted(tests), 'paths': out_paths, 'uncovered': uncovered,
    }
    if roster is not None:
        result['hil'] = {'boards': sorted(roster) if hil_full else sorted(hil_boards), 'full': hil_full,
                         'uncovered': uncovered_by_roster(paths, roster)}
    return result


def uncovered_by_roster(paths, roster):
    """Firmware families a change reaches that no roster board on this host runs."""
    reached = set()
    for path in paths:
        effect, detail, _ = classify(path)
        if effect == 'family':
            reached.add(detail)
        elif effect == 'shared':
            reached.update(REPRESENTATIVES)
    covered = {BOARDS[b]['family'] for b in roster}
    return [{'family': f, 'reason': 'no roster board of this family on this host'} for f in sorted(reached - covered)]


def changed_paths(args):
    if args.scope:
        out = []
        for s in args.scope:
            p = (ROOT / s).resolve()
            if p != ROOT and ROOT not in p.parents:
                fail(f'--scope {s} is outside this checkout')
            if p.is_dir():
                out += [rel(f) for f in p.rglob('*') if f.is_file() and '.git' not in f.parts]
            else:
                out.append(rel(p))
        return sorted(set(out))
    if args.diff_file:
        return sorted({line.strip() for line in Path(args.diff_file).read_text().splitlines() if line.strip()})
    base = git('merge-base', args.base, 'HEAD').strip()
    paths = set(git('diff', '--name-only', '--no-renames', base, 'HEAD').split())
    if args.worktree:
        paths |= set(git('diff', '--name-only', '--no-renames', 'HEAD').split())
        paths |= set(git('ls-files', '--others', '--exclude-standard').split())
    return sorted(paths)


def rig_roster(path):
    """{name: entry} of a HIL roster whose every board has a rig profile here; ValueError naming why not."""
    try:
        boards = {b['name']: b for b in json.loads(Path(path).read_text())['boards']}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ValueError(f'roster {path}: {e}')
    unknown = [n for n in boards if n not in BOARDS or 'span' not in BOARDS[n]]
    if unknown:
        raise ValueError(f'roster {path}: boards with no rig profile here: {unknown}')
    return boards


def load_roster(path):
    try:
        return rig_roster(path)
    except ValueError as e:
        fail(str(e))


# ---- build

def tool_version():
    try:
        p = subprocess.run(['arduino-cli', 'version', '--format', 'json'], capture_output=True, text=True)
    except OSError as e:
        fail(f'arduino-cli not usable: {e}')
    if p.returncode:
        fail('arduino-cli not usable: ' + p.stderr.strip())
    return json.loads(p.stdout).get('VersionString')


def foreign_headers(build_dir):
    """Headers a build's .d files took from another copy of this library (a core's bundled one, another checkout),
    each with its counterpart path here and whether the bytes match."""
    found, roots, seen = {}, {}, set()
    for dfile in Path(build_dir).rglob('*.d'):
        text = dfile.read_text(errors='replace').replace('\\\n', ' ')
        for token in (t.replace('\\ ', ' ') for t in re.split(r'(?<!\\)\s+', text) if t):
            if token.endswith(':') or not token.startswith('/') or token in seen:
                continue
            seen.add(token)
            p = Path(token)
            if ROOT in p.parents:
                continue
            root = library_root(p, roots)
            if root:
                counterpart = ROOT / p.relative_to(root)
                digest, exists = sha256(p), counterpart.exists()
                found[token] = {'path': token, 'counterpart': rel(counterpart) if exists else
                                p.relative_to(root).as_posix(), 'sha256': digest,
                                'matches': exists and digest == sha256(counterpart)}
    return sorted(found.values(), key=lambda h: h['path'])


def library_root(path, cache):
    """The root of the copy of this library that `path` sits in, or None."""
    walked, root = [], None
    for parent in path.parents:
        if parent in cache:
            root = cache[parent]
            break
        walked.append(parent)
        props = parent / 'library.properties'
        if props.exists():
            name = re.search(r'^name=(.*)$', props.read_text(errors='replace'), re.M)
            root = parent if name and name.group(1).strip() == LIBRARY_NAME else None
            break
    for d in walked:   # every dir walked resolves to the same answer, a root or none
        cache[d] = root
    return root


def compile_sketch(board, example, build_dir, properties):
    shutil.rmtree(build_dir, ignore_errors=True)
    build_dir.mkdir(parents=True)
    cmd = ['arduino-cli', 'compile', '--clean', '--format', 'json', '--warnings', 'all', '--fqbn', BOARDS[board]['fqbn'],
           '--library', str(ROOT), '--build-path', str(build_dir)]
    for prop in properties:
        cmd += ['--build-property', prop]
    cmd.append(str(ROOT / example))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT)
    except OSError as e:
        return {'board': board, 'example': example, 'status': 'error', 'buildDir': rel(build_dir),
                'firstError': f'arduino-cli: {e}', 'remedy': 'install arduino-cli on PATH'}
    except subprocess.TimeoutExpired:
        return {'board': board, 'example': example, 'status': 'failed', 'buildDir': rel(build_dir),
                'firstError': f'arduino-cli timed out after {COMPILE_TIMEOUT}s'}
    log = build_dir / 'check_build.log'
    log.write_text(' '.join(cmd) + '\n' + p.stdout + '\n' + p.stderr)
    row = {'board': board, 'example': example, 'buildDir': rel(build_dir), 'log': rel(log)}
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {}
    err = (out.get('compiler_err') or '') + p.stderr
    if p.returncode or not out.get('success'):
        missing = re.search(r'(Platform .* is not found|platform not installed|Error resolving FQBN[^\n]*)', err)
        if missing:
            row.update(status='error', firstError=missing.group(0),
                       remedy=f'arduino-cli core install {BOARDS[board]["fqbn"].split(":")[0]}:'
                              f'{BOARDS[board]["fqbn"].split(":")[1]}')
        else:
            lines = [ln for ln in err.splitlines() if 'error' in ln.lower()]
            row.update(status='failed', firstError=(lines or err.strip().splitlines()[-1:] or ['(no output)'])[0])
        return row
    result = out.get('builder_result', {})
    used = [lib for lib in result.get('used_libraries', []) if lib.get('name') == LIBRARY_NAME]
    if not used or Path(used[0]['install_dir']).resolve() != ROOT:
        row.update(status='failed', firstError=f'{LIBRARY_NAME} came from '
                   f'{used[0]["install_dir"] if used else "nowhere"}, not this checkout')
        return row
    row.update(status='ok', platform=result.get('board_platform'), buildPlatform=result.get('build_platform'),
               vidPid=vid_pid(build_dir), foreignHeaders=foreign_headers(build_dir),
               artifacts=sorted(rel(f) for f in build_dir.glob('*.ino.*') if f.suffix in ('.elf', '.hex', '.bin',
                                                                                            '.uf2', '.map')))
    return row


def vid_pid(build_dir):
    """VID:PID the sketch enumerates with: Adafruit_USBD_Device.cpp's USB_VID/USB_PID as its compile command defines
    them (compile_commands.json), else its BOARD_VENDORID/BOARD_PRODUCTID, else its own fallback. The rp2040 core
    passes USBD_VID/USBD_PID, which this library ignores. A core header defining USB_VID is not seen."""
    try:
        commands = json.loads((Path(build_dir) / 'compile_commands.json').read_text())
    except (OSError, ValueError):
        return None
    for entry in commands:
        if entry.get('file', '').endswith('Adafruit_USBD_Device.cpp'):
            flags = ' '.join(entry.get('arguments') or [entry.get('command', '')])
            break
    else:
        return None
    source = (ROOT / 'src/arduino/Adafruit_USBD_Device.cpp').read_text()
    ids = []
    for name, board_name in (('USB_VID', 'BOARD_VENDORID'), ('USB_PID', 'BOARD_PRODUCTID')):
        defined = re.findall(rf'-D{name}=(\S+)', flags) or re.findall(rf'-D{board_name}=(\S+)', flags)
        fallback = re.search(rf'#define {name} (0x[0-9a-fA-F]+)', source)
        value = defined[-1] if defined else fallback.group(1) if fallback else None
        if value is None:
            return None
        ids.append(int(value, 0))
    return f'{ids[0]:04x}:{ids[1]:04x}'


def build_all(jobs, plan, root, properties):
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(compile_sketch, b, e, root / b / Path(e).name, properties)
                   for b, exs in plan.items() for e in exs]
        for f in concurrent.futures.as_completed(futures):
            rows.append(f.result())
    return sorted(rows, key=lambda r: (r['board'], r['example']))


def run_tests(names):
    rows = []
    for name in names:
        script = ROOT / CONTRACT_TESTS[name]
        if not script.exists():
            rows.append({'test': name, 'status': 'error', 'firstError': f'{rel(script)} missing'})
            continue
        p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
        rows.append({'test': name, 'status': 'ok' if p.returncode == 0 else 'failed',
                     'firstError': '' if p.returncode == 0 else (p.stderr.strip().splitlines() or ['?'])[-1]})
    return rows


# ---- HIL record

def _elf32(path):
    data = Path(path).read_bytes()
    if data[:4] != b'\x7fELF' or data[4] != 1 or data[5] != 1:
        raise ValueError(f'{path}: not a little-endian ELF32')
    return data


def _segments(data):
    """(p_offset, p_paddr, p_filesz) of every PT_LOAD with file bytes."""
    phoff, = struct.unpack_from('<I', data, 28)
    phentsize, phnum = struct.unpack_from('<HH', data, 42)
    out = []
    for i in range(phnum):
        p_type, p_offset, _, p_paddr, p_filesz = struct.unpack_from('<IIIII', data, phoff + i * phentsize)
        if p_type == 1 and p_filesz:
            out.append((p_offset, p_paddr, p_filesz))
    return out


def elf_load_spans(path):
    """[(start, end)] the ELF's PT_LOAD segments write, by physical (load) address: what an ELF flash writes, ELF
    headers and padding included when a segment starts below the first section (the SAMD core's does, at 0)."""
    return merge(sorted((pa, pa + n) for _, pa, n in _segments(_elf32(path))))


def elf_section_spans(path):
    """[(start, end)] of the allocated sections with file bytes, at their load addresses: what objcopy puts in a
    HEX or BIN."""
    data = _elf32(path)
    segments = _segments(data)
    shoff, = struct.unpack_from('<I', data, 32)
    shentsize, shnum = struct.unpack_from('<HH', data, 46)
    spans = []
    for i in range(shnum):
        _, sh_type, sh_flags, _, sh_offset, sh_size = struct.unpack_from('<IIIIII', data, shoff + i * shentsize)
        if sh_flags & 0x2 and sh_type != 8 and sh_size:   # SHF_ALLOC, not SHT_NOBITS
            for off, pa, n in segments:
                if off <= sh_offset and sh_offset + sh_size <= off + n:
                    spans.append((pa + sh_offset - off, pa + sh_offset - off + sh_size))
                    break
    return merge(sorted(spans))


def hex_runs(path):
    """[(start, bytes)] contiguous runs of an Intel HEX file."""
    runs, base = [], 0   # [(start, bytearray)]
    for line in Path(path).read_text().splitlines():
        rec = bytes.fromhex(line.strip()[1:])
        if sum(rec) & 0xff:
            raise ValueError(f'{path}: bad checksum')
        addr, kind, payload = int.from_bytes(rec[1:3], 'big'), rec[3], rec[4:4 + rec[0]]
        if kind == 0:
            start = base + addr
            if runs and runs[-1][0] + len(runs[-1][1]) == start:
                runs[-1][1].extend(payload)
            else:
                runs.append((start, bytearray(payload)))
        elif kind == 2:
            base = int.from_bytes(payload, 'big') << 4
        elif kind == 4:
            base = int.from_bytes(payload, 'big') << 16
        elif kind == 1:
            break
    return [(start, bytes(data)) for start, data in runs]


def merge(spans):
    out = []
    for s, e in spans:
        if out and out[-1][1] == s:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def source_identity():
    status = git('status', '--porcelain', '--untracked-files=all')
    diff = git('diff', 'HEAD', '--binary')
    return {'head': git('rev-parse', 'HEAD').strip(), 'dirty': bool(status.strip()),
            'diffSha256': hashlib.sha256(diff.encode()).hexdigest(), 'status': status}, diff


def platform_git(install_dir):
    p = subprocess.run(['git', '-C', install_dir, 'rev-parse', 'HEAD'], capture_output=True, text=True)
    if p.returncode:
        return None
    dirty = subprocess.run(['git', '-C', install_dir, 'status', '--porcelain', '--untracked-files=no'],
                           capture_output=True, text=True).stdout.strip()
    return {'commit': p.stdout.strip(), 'dirty': bool(dirty)}


def make_record(args, board, rows, before, diff, roster_path):
    """The HIL's build record, or (None, error)."""
    after, _ = source_identity()
    if {k: before[k] for k in ('head', 'diffSha256', 'status')} != {k: after[k] for k in ('head', 'diffSha256',
                                                                                          'status')}:
        return None, 'the source changed while building'
    lo, hi = BOARDS[board]['span']
    flasher = rig_roster(roster_path)[board]['flasher']['name']
    image = 'elf' if flasher == 'openocd' else 'hex'   # what hil_test.py flashes for this probe
    sketches = {}
    for row in rows:
        build_dir = ROOT / row['buildDir']
        if not row['vidPid']:
            return None, f'{row["example"]}: its VID:PID could not be read from compile_commands.json'
        elf = next(build_dir.glob('*.ino.elf'))
        hexfile = next(build_dir.glob('*.ino.hex'), None)
        if image == 'elf':
            spans = elf_load_spans(elf)
        elif not hexfile:
            return None, f'{row["example"]}: a {flasher} board flashes the HEX, and this build made none'
        else:
            spans = merge([(s, s + len(b)) for s, b in hex_runs(hexfile)])
            if spans != elf_section_spans(elf):
                return None, f'{row["example"]}: HEX runs {spans} differ from the ELF sections {elf_section_spans(elf)}'
        if not spans or any(s < lo or e > hi for s, e in spans):
            return None, f'{row["example"]}: the {image.upper()} writes {[(hex(s), hex(e)) for s, e in spans]} ' \
                         f'outside the board span [{lo:#x}, {hi:#x})'
        if not args.experimental:
            bad = [h for h in row['foreignHeaders'] if not h['matches']]
            if bad:
                return None, f'{row["example"]}: compiled against headers that differ from this checkout: ' \
                             f'{[h["path"] for h in bad]}'
        files = [elf] + ([hexfile] if hexfile else [])
        sketches[Path(row['example']).name] = {
            'example': row['example'], 'buildDir': row['buildDir'], 'elf': rel(elf),
            'hex': rel(hexfile) if hexfile else None, 'image': image, 'loadSpans': [[s, e] for s, e in spans],
            'vidPid': row['vidPid'], 'foreignHeaders': row['foreignHeaders'],
            'sha256': {rel(f): sha256(f) for f in files},
        }
    platform = rows[0]['platform']
    record = {
        'version': SCHEMA, 'kind': 'arduino-hil-build', 'board': board, 'fqbn': BOARDS[board]['fqbn'],
        'experimental': bool(args.experimental), 'source': {k: before[k] for k in ('head', 'dirty', 'diffSha256')},
        'buildProperties': args.build_property, 'arduinoCli': tool_version(),
        'platform': {**platform, 'git': platform_git(platform['install_dir'])},
        'roster': {'path': rel(roster_path), 'sha256': sha256(roster_path)},
        'harness': {p: sha256(ROOT / p) for p in ('.claude/skills/build/scripts/check_build.py',
                                                  '.claude/skills/hil-contract/scripts/hil_test.py')
                    if (ROOT / p).exists()},
        'span': [lo, hi], 'sketches': sketches,
    }
    if args.experimental:
        patch = Path(args.record).with_suffix('.patch')
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(diff)
        record['patch'] = {'path': rel(patch), 'sha256': sha256(patch)}
    return record, None


# ---- main

def parse(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--scope', nargs='+')
    src.add_argument('--base')
    src.add_argument('--diff-file')
    src.add_argument('--board', action='append')
    ap.add_argument('--worktree', action='store_true')
    ap.add_argument('--select-only', action='store_true')
    ap.add_argument('--hil-config')
    ap.add_argument('--example', action='append', help='narrow to these sketch dirs (examples/...)')
    ap.add_argument('--shared', action='store_true', help='keep build/hil/<board>/<sketch> (flash, HIL)')
    ap.add_argument('--record', help='write the HIL build record here (with --board B --shared --hil-config)')
    ap.add_argument('--experimental', action='store_true', help='a record from a dirty tree or with properties')
    ap.add_argument('--build-property', action='append', default=[], metavar='K=V')
    ap.add_argument('-j', '--jobs', type=int, default=4)
    args = ap.parse_args(argv)
    if not (args.scope or args.base or args.diff_file or args.board):
        fail('give --scope, --base, --diff-file or --board')
    if args.worktree and not args.base:
        fail('--worktree goes with --base')
    if args.select_only and args.board:
        fail('--select-only takes --scope, --base or --diff-file')
    if args.record and not (args.board and len(args.board) == 1 and args.shared and args.hil_config):
        fail('--record needs one --board, --shared and --hil-config')
    if args.experimental and not args.record:
        fail('--experimental only qualifies a --record')
    if args.record and args.build_property and not args.experimental:
        fail('a record with --build-property must be --experimental')
    if args.record and ROOT not in Path(args.record).resolve().parents:
        fail('--record must be inside this checkout (build/hil/<board>.build.json)')
    if args.record and args.example:
        fail('a record carries every HIL sketch; drop --example')
    for e in args.example or []:
        if e.rstrip('/') not in examples():
            fail(f'no sketch dir {e}')
    return args


def main(argv=None):
    args = parse(argv if argv is not None else sys.argv[1:])
    roster = load_roster(args.hil_config) if args.hil_config else None
    only = [e.rstrip('/') for e in args.example] if args.example else None
    if args.board:
        unknown = [b for b in args.board if b not in BOARDS]
        if unknown:
            fail(f'unknown board(s) {unknown}; known: {sorted(BOARDS)}')
        sel = {'builds': {}, 'tests': [], 'paths': [], 'uncovered': []}
        for b in args.board:
            if args.record and b not in (roster or []):
                fail(f'{b} is not in {args.hil_config}')
            want = list(HIL_SKETCHES) if args.record else (only or examples())
            sel['builds'][b] = [e for e in want if args.record or eligible(b, e)]
    else:
        sel = select(changed_paths(args), roster, only)
    if args.select_only:
        print(json.dumps({'version': SCHEMA, 'pass': True, 'build': sel, **({'hil': sel.pop('hil')}
                                                                            if 'hil' in sel else {})}))
        return 0

    before, diff = source_identity()
    if args.record:
        if before['dirty'] and not args.experimental:
            fail('a HIL record needs a clean tree (commit first), or --experimental', dirty=before['status'])
        untracked = [ln[3:] for ln in before['status'].splitlines() if ln.startswith('?? ') and
                     ln[3:].startswith(('src/', 'examples/', 'test/hil/'))]
        if untracked:
            fail(f'untracked source would be missing from the record patch: {untracked}')
    shared = BUILD_ROOT / 'hil'
    root = shared if args.shared else BUILD_ROOT / f'agent-{os.getpid()}'
    lock = None
    if args.shared:
        shared.mkdir(parents=True, exist_ok=True)
        lock = open(shared / '.lock', 'w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail(f'{rel(shared)} is in use by another build or HIL run (its .lock is held)')
    rows = build_all(args.jobs, sel['builds'], root, args.build_property)
    tests = run_tests(sel['tests'])
    record_error, record_path = None, None
    if args.record and all(r['status'] == 'ok' for r in rows):
        record, record_error = make_record(args, args.board[0], rows, before, diff, Path(args.hil_config))
        if record:
            record_path = Path(args.record)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(json.dumps(record, indent=1) + '\n')
    if not args.shared:
        for r in rows:
            if r['status'] == 'ok':
                shutil.rmtree(ROOT / r['buildDir'], ignore_errors=True)
                r['removed'] = True
        if root.exists() and not any(root.rglob('check_build.log')):
            shutil.rmtree(root, ignore_errors=True)

    errors = [r for r in rows + tests if r['status'] == 'error']
    failed = [r for r in rows + tests if r['status'] == 'failed']
    warnings = sorted({h['path'] for r in rows for h in r.get('foreignHeaders', []) if not h['matches']})
    first = (errors or failed or [{}])[0]
    out = {
        'version': SCHEMA, 'pass': not errors and not failed and not record_error and not sel['uncovered'],
        'input': {k: before[k] for k in ('head', 'dirty')}, 'boards': rows, 'tests': tests,
        'paths': sel['paths'], 'uncovered': sel['uncovered'],
        'nothingToBuild': not rows and not tests,
        'firstError': record_error or first.get('firstError', ''),
        'warnings': [f'compiled against a foreign copy that differs from this checkout: {h}' for h in warnings],
    }
    if record_path:
        out['record'] = rel(record_path)
    if errors:
        out['error'] = errors[0].get('remedy') or errors[0]['firstError']
    print(json.dumps(out))
    return 2 if errors else 1 if failed or record_error else 3 if sel['uncovered'] else 0


if __name__ == '__main__':
    sys.exit(main())
