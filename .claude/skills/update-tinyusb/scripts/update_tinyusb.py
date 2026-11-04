#!/usr/bin/env python3
"""Sync the vendored TinyUSB core in src/ with hathach/tinyusb at one commit.

  update_tinyusb.py plan   [--ref REF] [--source PATH] [--include P ...] [--skip P ...]
  update_tinyusb.py apply  --ref SHA [--source PATH] [--include P ...] [--skip P ...]
  update_tinyusb.py verify [--source PATH]

plan reports what apply would do for REF (default master) and writes nothing; verify checks the working tree against
upstream.json, which records the upstream url, the synced commit and the vendored files. A vendored file is upstream's
blob byte for byte with its executable bit, except src/tusb_option.h, which also carries the ESP32 branch. New
upstream files under src/ are included unless --skip names them (newOutsideVendored lists those in a directory with no
vendored file); files the library never took stay out unless --include names them. The url is fetched into a
temporary repository; --source reads a local clone's objects instead (full SHAs only; its refs, index and files are
untouched).

Prints one JSON line. Exit 0 ok; 1 verify mismatch or a write failed (written lists the paths written and the one it
failed at); 2 refused, nothing written.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MANIFEST = '.claude/skills/update-tinyusb/upstream.json'
OPTION_H = 'src/tusb_option.h'
PROTECTED_FILES = ('src/Adafruit_TinyUSB.h', 'src/tusb_config.h')
PROTECTED_DIRS = ('src/arduino/',)
MODES = {'100644': False, '100755': True}   # git blob mode -> executable
SHA = re.compile(r'[0-9a-f]{40}')
# the ESP32 core's own TinyUSB headers come first on its include path; this branch makes ours pick its config
ANCHOR = b'#ifdef CFG_TUSB_CONFIG_FILE\n  #include CFG_TUSB_CONFIG_FILE\n'
ANCHOR_TAIL = b'#else\n  #include "tusb_config.h"\n#endif\n'
ESP32_BRANCH = (b'#elif defined(ARDUINO_ARCH_ESP32)\n  // ESP32 out-of-sync\n'
                b'  #include "arduino/ports/esp32/tusb_config_esp32.h"\n')


class Refused(Exception):
    pass


def protected(path):
    return path in PROTECTED_FILES or path.startswith(PROTECTED_DIRS)


def git(*args, stdin=None, env=None, check=True):
    clean = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}   # a hook's GIT_DIR must not leak in
    r = subprocess.run(['git', *args], input=stdin, capture_output=True, env={**clean, **(env or {})})
    if check and r.returncode:
        raise Refused(f'git {" ".join(args)}: {r.stderr.decode(errors="replace").strip()}')
    return r


def blob_id(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def esp32_patch(blob):
    """tusb_option.h with the ESP32 branch inserted after the CFG_TUSB_CONFIG_FILE arm, in the file's newlines."""
    if b'ARDUINO_ARCH_ESP32' in blob:
        raise Refused(f'{OPTION_H} upstream already mentions ARDUINO_ARCH_ESP32: inspect it and update the script')
    block = ANCHOR + ANCHOR_TAIL
    found = [(nl, blob.count(block.replace(b'\n', nl))) for nl in (b'\n', b'\r\n')]
    if sum(n for _, n in found) != 1:
        raise Refused(f'{OPTION_H}: the CFG_TUSB_CONFIG_FILE selection block occurs {sum(n for _, n in found)} times '
                      'upstream, expected once: update the ESP32 branch insertion and its tests')
    nl = next(nl for nl, n in found if n)
    return blob.replace(block.replace(b'\n', nl), (ANCHOR + ESP32_BRANCH + ANCHOR_TAIL).replace(b'\n', nl))


class Upstream:
    """A temporary bare repository holding upstream objects: fetched from the url, or borrowed from --source."""

    def __init__(self, tmp, url, source):
        self.dir = str(Path(tmp) / 'upstream.git')
        self.source = source
        git('init', '-q', '--bare', self.dir)
        if source:
            common = git('-C', source, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout
            Path(self.dir, 'objects', 'info', 'alternates').write_text(
                str(Path(common.decode().strip()) / 'objects') + '\n')
        else:
            self.git('remote', 'add', 'origin', url)

    def git(self, *args, **kw):
        return git('--git-dir', self.dir, *args, **kw)

    def fetch(self, refs, shallow=False):
        """Each (ref, full) as a commit SHA, fetched in one request; full (and --source) take only a full SHA."""
        for ref, full in refs:
            if (full or self.source) and not SHA.fullmatch(ref):
                raise Refused(f'{ref!r} is not a full commit SHA' + (' (--source takes only full SHAs)'
                                                                     if self.source else ''))
        if self.source:
            for ref, _ in refs:
                kind = self.git('cat-file', '-t', ref, check=False).stdout.decode().strip()
                if kind != 'commit':
                    raise Refused(f'{ref} is not a commit in {self.source}' + (f' (a {kind})' if kind else
                                                                               ': fetch it there, or drop --source'))
            return [ref for ref, _ in refs]
        specs = [f'{ref}:refs/up/{i}' for i, (ref, _) in enumerate(refs)]
        self.git('fetch', '-q', '--no-tags', '--filter=blob:none', *(['--depth=1'] if shallow else []), 'origin',
                 *specs)
        shas = [self.git('rev-parse', f'refs/up/{i}^{{commit}}').stdout.decode().strip() for i in range(len(refs))]
        for (ref, full), sha in zip(refs, shas):
            if full and sha != ref:
                raise Refused(f'{ref} is not a commit (it resolves to {sha})')
        return shas

    def is_ancestor(self, a, b):
        return self.git('merge-base', '--is-ancestor', a, b, check=False).returncode == 0

    def tree(self, sha):
        """{path: (mode, type, oid)} of every entry under src/ at sha."""
        out = {}
        for rec in self.git('ls-tree', '-r', '-z', '--full-tree', sha, '--', 'src').stdout.split(b'\0'):
            if rec:
                meta, path = rec.decode().split('\t', 1)
                mode, kind, oid = meta.split()
                out[path] = (mode, kind, oid)
        return out

    def blobs(self, oids):
        """{oid: bytes}, fetched in one request."""
        oids = sorted(set(oids))
        if not oids:
            return {}
        listing = '\n'.join(oids).encode() + b'\n'
        if not self.source:   # as git's own lazy fetch asks; plain negotiation fails in verify's shallow repo
            self.git('-c', 'fetch.negotiationAlgorithm=noop', 'fetch', '-q', '--no-tags', '--no-write-fetch-head',
                     '--recurse-submodules=no', '--filter=blob:none', '--stdin', 'origin', stdin=listing)
        data = self.git('cat-file', '--batch', stdin=listing, env={'GIT_NO_LAZY_FETCH': '1'}).stdout
        out, pos = {}, 0
        for oid in oids:
            end = data.index(b'\n', pos)
            header = data[pos:end].decode().split()
            if header[1:] == ['missing']:
                raise Refused(f'blob {oid} is missing' + (f' from {self.source} (a partial clone?): drop --source'
                                                          if self.source else ''))
            if header[:2] != [oid, 'blob']:
                raise Refused(f'object {oid} is not a blob: {header}')
            out[oid] = data[end + 1:end + 1 + int(header[2])]
            pos = end + 2 + int(header[2])
        return out

    def log(self, base, target):
        return self.git('log', '--format=%h %s', f'{base}..{target}', '--', 'src').stdout.decode().splitlines()


def load_manifest(root):
    path = root / MANIFEST
    try:
        raw = path.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError) as e:
        raise Refused(f'{MANIFEST} is not readable JSON ({e})') from None
    if not isinstance(data, dict):
        raise Refused(f'{MANIFEST} must hold a JSON object')
    files = data.get('files')
    if not (isinstance(data.get('url'), str) and data['url'] and isinstance(data.get('commit'), str)
            and SHA.fullmatch(data['commit']) and isinstance(files, list)):
        raise Refused(f'{MANIFEST} needs "url", a full "commit" SHA and a "files" list')
    bad = [f for f in files if not isinstance(f, str) or not f.startswith('src/') or protected(f)
           or '..' in f.split('/') or '\\' in f]
    if bad or files != sorted(set(files)):
        raise Refused(f'{MANIFEST} "files" must be sorted, unique, non-protected src/ paths' +
                      (f': {bad[:5]}' if bad else ''))
    return raw, data


def disk_files(root):
    """Every non-protected file (or symlink) under src/, as repo-relative paths."""
    out = set()
    for d, dirs, files in os.walk(root / 'src'):
        rel = Path(d).relative_to(root).as_posix()
        out.update(f'{rel}/{f}' for f in files + [x for x in dirs if os.path.islink(os.path.join(d, x))])
    return {p for p in out if not protected(p)}


def require_upstream(files, tree, sha):
    absent = [p for p in files if p not in tree]
    if absent:
        raise Refused(f'{len(absent)} files of {MANIFEST} are not in upstream {sha[:12]}: {absent[:5]}')


def expected(files, tree, option):
    """{path: (blob id, executable)} each vendored file must hold at that tree; option is upstream's tusb_option.h."""
    out = {}
    for p in files:
        mode, kind, oid = tree[p]
        if kind != 'blob' or mode not in MODES:
            raise Refused(f'{p} is a {kind} of mode {mode} upstream: only regular files are vendored')
        out[p] = (blob_id(esp32_patch(option)) if p == OPTION_H else oid, MODES[mode])
    return out


def mismatches(root, want):
    """How the working tree under src/ differs from want: changed, missing and unexpected paths."""
    changed, missing = [], []
    for p, (oid, exe) in want.items():
        f = root / p
        if f.is_symlink() or not f.is_file():
            (changed if os.path.lexists(f) else missing).append(p)
        elif blob_id(f.read_bytes()) != oid or bool(f.stat().st_mode & 0o100) != exe:
            changed.append(p)
    return {'changed': changed, 'missing': missing, 'unexpected': sorted(disk_files(root) - set(want))}


def verify(root, args):
    _, man = load_manifest(root)
    files = man['files']
    with tempfile.TemporaryDirectory() as tmp:
        up = Upstream(tmp, man['url'], args.source)
        [base] = up.fetch([(man['commit'], True)], shallow=True)
        tree = up.tree(base)
        require_upstream(files, tree, base)
        option = up.blobs([tree[OPTION_H][2]])[tree[OPTION_H][2]] if OPTION_H in files else None
        diff = mismatches(root, expected(files, tree, option))
    ok = not any(diff.values())
    return (0 if ok else 1), {'ok': ok, 'base': base, **diff}


def select(files, new, tt, skip, include, target):
    """The next vendored set, and the errors the --skip/--include choices raise."""
    errors = [f'{p}: upstream added a protected path; the library owns it' for p in new if protected(p)]
    errors += [f'{p}: both --include and --skip' for p in sorted(skip & include)]
    errors += [f'--skip {p}: not a file upstream added since the recorded commit' for p in sorted(skip - set(new))]
    errors += [f'--include {p}: not an omitted upstream file at {target[:12]}' for p in sorted(include)
               if protected(p) or p in files or p in new or p not in tt]
    return sorted((files & tt.keys()) | (set(new) - skip) | include), errors


def check_local(root, raw, have, added):
    """Why the checkout may not be written now; run after every fetch, so an edit made meanwhile is refused."""
    status = git('-C', str(root), 'status', '--porcelain', '--ignored', '--untracked-files=all', '--',
                 'src', MANIFEST).stdout.decode().splitlines()
    errors = [f'not clean: {line}' for line in status]
    if (root / MANIFEST).read_bytes() != raw:
        errors.append(f'{MANIFEST} changed while preparing')
    errors += [f'{p}: {kind} against the recorded upstream commit' for kind, ps in mismatches(root, have).items()
               for p in ps]
    return errors + [f'{p}: already exists' for p in added if os.path.lexists(root / p)]


def write_file(f, data, exe):
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    mode = f.stat().st_mode
    os.chmod(f, mode | ((mode & 0o444) >> 2) if exe else mode & ~0o111)


def write(root, removed, outputs, files, manifest, raw):
    """Apply the sync; (written, error), written naming the path a failure stopped at."""
    written, current = {'created': [], 'modified': [], 'deleted': []}, None
    try:
        for p in removed:
            current = p
            (root / p).unlink()
            written['deleted'].append(p)
            d = (root / p).parent
            while d != root / 'src' and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
        for p, (data, exe) in outputs:
            current = p
            write_file(root / p, data, exe)
            written['modified' if p in files else 'created'].append(p)
        if manifest != raw:
            current = MANIFEST
            write_file(root / MANIFEST, manifest, False)
            written['modified'].append(MANIFEST)
    except OSError as e:
        written['failed'] = current   # may be partly written
        return written, f'write failed at {current}: {e}; recover the written paths one by one'
    return written, None


def sync(root, args, apply):
    raw, man = load_manifest(root)
    url, files = man['url'], set(man['files'])
    with tempfile.TemporaryDirectory() as tmp:
        up = Upstream(tmp, url, args.source)
        base, target = up.fetch([(man['commit'], True), (args.ref, apply)])
        target_url = f'{url.removesuffix(".git").rstrip("/")}/commit/{target}'
        out = {'ok': False, 'base': base, 'target': target, 'targetUrl': target_url,
               'commitMessage': f'update tinyusb core to commit {target_url}'}
        if not up.is_ancestor(base, target):
            raise Refused(f'base {base} is not an ancestor of {target}')
        bt, tt = up.tree(base), up.tree(target)
        require_upstream(files, bt, base)
        new = sorted(p for p in tt if p not in bt)
        dirs = {p.rsplit('/', 1)[0] for p in files}
        nxt, errors = select(files, new, tt, set(args.skip), set(args.include), target)
        out.update(updated=sorted(p for p in files & set(nxt) if bt[p] != tt[p]), added=sorted(set(nxt) - files),
                   removed=sorted(files - tt.keys()), newUpstream=new,
                   newOutsideVendored=[p for p in new if p.rsplit('/', 1)[0] not in dirs], skipped=sorted(args.skip),
                   included=sorted(args.include), upstreamLog=up.log(base, target), errors=errors)
        if errors:
            return 2, out
        writes = sorted(out['added'] + out['updated'])
        sides = ((bt, files), (tt, nxt))
        blobs = up.blobs([tt[p][2] for p in writes] + [t[OPTION_H][2] for t, fs in sides if OPTION_H in fs])
        have, want = (expected(sorted(fs), t, blobs[t[OPTION_H][2]] if OPTION_H in fs else None) for t, fs in sides)
        outputs = [(p, (esp32_patch(blobs[tt[p][2]]) if p == OPTION_H else blobs[tt[p][2]], want[p][1]))
                   for p in writes]

    errors += check_local(root, raw, have, out['added'])
    if errors or not apply:
        out['ok'] = not errors
        return (2 if errors else 0), out
    manifest = (json.dumps({'url': url, 'commit': target, 'files': nxt}, indent=2) + '\n').encode()
    out['written'], error = write(root, out['removed'], outputs, files, manifest, raw)
    if error:
        errors.append(error)
        return 1, out
    errors += [f'{p}: {kind} after writing' for kind, ps in mismatches(root, want).items() for p in ps]
    out['ok'] = not errors
    return (1 if errors else 0), out


def main(argv, root=ROOT):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='op', required=True)
    for name in ('plan', 'apply', 'verify'):
        p = sub.add_parser(name)
        p.add_argument('--source', help='local tinyusb clone to read objects from instead of fetching')
        if name != 'verify':
            p.add_argument('--ref', required=name == 'apply', default='master')
            p.add_argument('--include', action='append', default=[], metavar='PATH')
            p.add_argument('--skip', action='append', default=[], metavar='PATH')
    args = ap.parse_args(argv)
    try:
        if not (root / 'src' / 'arduino').is_dir():
            raise Refused(f'{root} is not an Adafruit TinyUSB Arduino checkout')
        code, out = verify(root, args) if args.op == 'verify' else sync(root, args, args.op == 'apply')
    except Refused as e:
        code, out = 2, {'ok': False, 'errors': [str(e)]}
    print(json.dumps({'operation': args.op, **out}))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
