#!/usr/bin/env python3
"""update_tinyusb.py against throwaway upstream and library repositories; nothing is fetched from the network."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'update-tinyusb' / 'scripts'))
import update_tinyusb as upd  # noqa: E402

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
OPTION = (b'// options\n#ifdef CFG_TUSB_CONFIG_FILE\n  #include CFG_TUSB_CONFIG_FILE\n#else\n'
          b'  #include "tusb_config.h"\n#endif\n')
OPTION_ESP32 = (b'// options\n#ifdef CFG_TUSB_CONFIG_FILE\n  #include CFG_TUSB_CONFIG_FILE\n'
                b'#elif defined(ARDUINO_ARCH_ESP32)\n  // ESP32 out-of-sync\n'
                b'  #include "arduino/ports/esp32/tusb_config_esp32.h"\n#else\n  #include "tusb_config.h"\n#endif\n')


def git(cwd, *a):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', '-c', 'commit.gpgsign=false', *a],
                          cwd=cwd, env=CLEAN_ENV, check=True, capture_output=True).stdout.decode().strip()


def put(root, files):
    for rel, data in files.items():
        f = Path(root) / rel
        if data is None:
            f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)


def commit(root, msg='c'):
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '--allow-empty', '-m', msg)
    return git(root, 'rev-parse', 'HEAD')


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.up, self.lib = t / 'tinyusb', t / 'lib'
        self.up.mkdir()
        git(self.up, 'init', '-q', '-b', 'master')
        put(self.up, {'src/tusb.c': b'v1\n', 'src/tusb_option.h': OPTION, 'src/class/a.c': b'a1\n',
                      'src/class/crlf.h': b'x\r\ny\r\n', 'src/tool.py': b'#!py\n',
                      'src/portable/omitted/dcd.c': b'o\n', 'src/CMakeLists.txt': b'cm\n'})
        os.chmod(self.up / 'src/tool.py', 0o755)
        self.base = commit(self.up)
        self.lib.mkdir()
        git(self.lib, 'init', '-q', '-b', 'master')
        vendored = ['src/class/a.c', 'src/class/crlf.h', 'src/tool.py', 'src/tusb.c', 'src/tusb_option.h']
        for p in vendored:
            put(self.lib, {p: (self.up / p).read_bytes()})
        put(self.lib, {'src/tusb_option.h': OPTION_ESP32, 'src/arduino/w.cpp': b'w\n', 'src/tusb_config.h': b'cfg\n',
                       'src/Adafruit_TinyUSB.h': b'h\n'})
        os.chmod(self.lib / 'src/tool.py', 0o755)
        self.manifest(self.base, vendored)
        commit(self.lib)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, sha, files):
        put(self.lib, {upd.MANIFEST: (json.dumps({'url': str(self.up), 'commit': sha, 'files': files}, indent=2)
                                      + '\n').encode()})

    def run_op(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = upd.main(list(argv), root=self.lib)
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def upstream_change(self, files, mode=None):
        put(self.up, files)
        for p, m in (mode or {}).items():
            os.chmod(self.up / p, m)
        return commit(self.up)

    def files(self):
        return json.loads((self.lib / upd.MANIFEST).read_text())['files']

    # -- happy paths

    def test_verify_clean_tree(self):
        self.assertEqual(self.run_op('verify'), (0, {'operation': 'verify', 'ok': True, 'base': self.base,
                                                     'changed': [], 'missing': [], 'unexpected': []}))

    def test_sync_updates_adds_removes_and_keeps_esp32_branch(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n', 'src/class/a.c': None, 'src/class/b.c': b'b\n',
                                  'src/portable/new/dcd.c': b'n\n', 'src/class/crlf.h': b'x\r\nz\r\n',
                                  'src/tusb_option.h': b'// v2\n' + OPTION})
        code, plan = self.run_op('plan')
        self.assertEqual(code, 0, plan)
        self.assertEqual(plan['target'], t)
        self.assertEqual(plan['updated'], ['src/class/crlf.h', 'src/tusb.c', 'src/tusb_option.h'])
        self.assertEqual(plan['added'], ['src/class/b.c', 'src/portable/new/dcd.c'])
        self.assertEqual(plan['newOutsideVendored'], ['src/portable/new/dcd.c'])
        self.assertEqual(plan['removed'], ['src/class/a.c'])
        self.assertEqual(git(self.lib, 'status', '--porcelain'), '')   # plan writes nothing
        code, out = self.run_op('apply', '--ref', t)
        self.assertEqual(code, 0, out)
        self.assertEqual(out['commitMessage'], f'update tinyusb core to commit {self.up}/commit/{t}')
        self.assertEqual((self.lib / 'src/tusb_option.h').read_bytes(), b'// v2\n' + OPTION_ESP32)
        self.assertEqual((self.lib / 'src/class/crlf.h').read_bytes(), b'x\r\nz\r\n')
        self.assertFalse((self.lib / 'src/class/a.c').exists())
        self.assertEqual(self.files(), ['src/class/b.c', 'src/class/crlf.h', 'src/portable/new/dcd.c',
                                        'src/tool.py', 'src/tusb.c', 'src/tusb_option.h'])
        self.assertEqual(self.run_op('verify')[0], 0)   # before staging
        commit(self.lib)
        code, again = self.run_op('apply', '--ref', t)
        self.assertEqual((code, again['updated'], again['added'], again['written']),
                         (0, [], [], {'created': [], 'modified': [], 'deleted': []}))
        self.assertEqual(git(self.lib, 'status', '--porcelain'), '')

    def test_executable_bit_follows_upstream(self):
        t = self.upstream_change({'src/new.py': b'n\n'}, mode={'src/tool.py': 0o644, 'src/new.py': 0o755})
        code, out = self.run_op('apply', '--ref', t)
        self.assertEqual(code, 0, out)
        self.assertEqual(out['updated'], ['src/tool.py'])   # a mode-only change
        self.assertFalse(os.stat(self.lib / 'src/tool.py').st_mode & 0o111)
        self.assertTrue(os.stat(self.lib / 'src/new.py').st_mode & 0o100)

    def test_directory_replaced_by_a_file(self):
        put(self.up, {'src/class/a.c': None, 'src/class/crlf.h': None})
        (self.up / 'src/class').rmdir()
        t = self.upstream_change({'src/class': b'f\n'})
        code, out = self.run_op('apply', '--ref', t)
        self.assertEqual(code, 0, out)
        self.assertEqual((out['added'], out['removed']), (['src/class'], ['src/class/a.c', 'src/class/crlf.h']))
        self.assertEqual((self.lib / 'src/class').read_bytes(), b'f\n')
        self.assertEqual(self.run_op('verify')[0], 0)

    def test_skip_and_include(self):
        t = self.upstream_change({'src/class/b.c': b'b\n', 'src/class/c.c': b'c\n'})
        code, out = self.run_op('apply', '--ref', t, '--skip', 'src/class/c.c',
                                '--include', 'src/portable/omitted/dcd.c')
        self.assertEqual(code, 0, out)
        self.assertIn('src/portable/omitted/dcd.c', self.files())
        self.assertNotIn('src/class/c.c', self.files())
        commit(self.lib)
        t2 = self.upstream_change({'src/tusb.c': b'v3\n'})
        code, plan = self.run_op('plan', '--ref', t2)
        self.assertEqual((code, plan['added']), (0, []))   # neither the skipped nor the omitted path comes back

    def test_plan_previews_apply_selection(self):
        t = self.upstream_change({'src/class/b.c': b'b\n', 'src/class/c.c': b'c\n'})
        flags = ['--skip', 'src/class/c.c', '--include', 'src/portable/omitted/dcd.c']
        code, plan = self.run_op('plan', '--ref', t, *flags)
        self.assertEqual((code, plan['added']), (0, ['src/class/b.c', 'src/portable/omitted/dcd.c']))
        code, both = self.run_op('plan', '--ref', t, '--skip', 'src/class/b.c', 'src/class/c.c')
        self.assertEqual((code, both['added']), (0, []))
        bad = ['--skip', 'src/tusb.c']
        self.assertEqual(self.run_op('plan', '--ref', t, *bad)[1]['errors'],
                         self.run_op('apply', '--ref', t, *bad)[1]['errors'])
        code, out = self.run_op('apply', '--ref', t, *flags)
        self.assertEqual((code, out['added']), (0, plan['added']))

    def test_include_takes_several_paths(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        omitted = ['src/CMakeLists.txt', 'src/portable/omitted/dcd.c']
        code, plan = self.run_op('plan', '--ref', t, '--include', *omitted)
        self.assertEqual((code, plan['added'], plan['included']), (0, omitted, omitted))
        code, out = self.run_op('apply', '--ref', t, '--include', *omitted)
        self.assertEqual((code, out['added']), (0, omitted))
        for p in omitted:
            self.assertIn(p, self.files())
            self.assertTrue((self.lib / p).is_file())

    def test_refs_resolve_branch_tag_and_the_base_itself(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        git(self.up, 'tag', '-a', '-m', 'r', 'v1.0', t)
        for ref in ('master', 'v1.0'):
            code, plan = self.run_op('plan', '--ref', ref)
            self.assertEqual((code, plan['target'], plan['updated']), (0, t, ['src/tusb.c']))
        tag_object = git(self.up, 'rev-parse', 'v1.0')
        self.assert_refused(['apply', '--ref', tag_object], 'is not a commit')
        self.assert_refused(['apply', '--ref', tag_object, '--source', str(self.up)], 'is not a commit')
        code, same = self.run_op('plan', '--ref', self.base)
        self.assertEqual((code, same['updated'], same['added'], same['removed']), (0, [], [], []))

    def test_source_reads_a_local_clone_without_touching_it(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        put(self.up, {'src/dirty.c': b'wip\n'})
        before = (git(self.up, 'for-each-ref'), git(self.up, 'status', '--porcelain'),
                  (self.up / '.git/index').read_bytes())
        code, out = self.run_op('apply', '--ref', t, '--source', str(self.up))
        self.assertEqual(code, 0, out)
        self.assertEqual(before, (git(self.up, 'for-each-ref'), git(self.up, 'status', '--porcelain'),
                                  (self.up / '.git/index').read_bytes()))
        self.assertEqual(self.run_op('plan', '--ref', 'master', '--source', str(self.up))[0], 2)

    # -- refusals

    def snapshot(self):
        return {p: p.read_bytes() for p in self.lib.rglob('*') if p.is_file() and '.git' not in p.parts}

    def assert_refused(self, argv, needle, unchanged=True):
        before = self.snapshot()
        code, out = self.run_op(*argv)
        self.assertEqual(code, 2, out)
        self.assertTrue(any(needle in e for e in out['errors']), out['errors'])
        if unchanged:
            self.assertEqual(before, self.snapshot())
        return out

    def test_refuses_abbreviated_sha_and_bad_flags(self):
        t = self.upstream_change({'src/class/b.c': b'b\n'})
        self.assert_refused(['apply', '--ref', t[:12]], 'not a full commit SHA')
        self.assert_refused(['apply', '--ref', t, '--skip', 'src/class/b.c', '--include', 'src/class/b.c'],
                            'both --include and --skip')
        self.assert_refused(['apply', '--ref', t, '--skip', 'src/tusb.c'], 'not a file upstream added')
        self.assert_refused(['apply', '--ref', t, '--skip', 'src/CMakeLists.txt'], 'not a file upstream added')
        self.assert_refused(['apply', '--ref', t, '--include', 'src/tusb.c'], 'not an omitted upstream file')
        self.assert_refused(['apply', '--ref', t, '--include', 'src/tusb_config.h'], 'not an omitted upstream file')

    def test_refuses_upstream_adding_a_protected_path(self):
        t = self.upstream_change({'src/arduino/x.h': b'x\n'})
        self.assert_refused(['apply', '--ref', t], 'protected path')
        code, out = self.run_op('apply', '--ref', t, '--skip', 'src/arduino/x.h')
        self.assertEqual(code, 0, out)
        self.assertNotIn('src/arduino/x.h', self.files())
        self.assertFalse((self.lib / 'src/arduino/x.h').exists())

    def test_refuses_a_path_the_manifest_cannot_hold(self):
        t = self.upstream_change({'src/new\\file.c': b'n\n'})
        self.assert_refused(['apply', '--ref', t], 'cannot hold')
        code, out = self.run_op('apply', '--ref', t, '--skip', 'src/new\\file.c')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.run_op('verify')[0], 0)
        commit(self.lib)
        t2 = self.upstream_change({'src/tusb.c': b'v2\n'})
        self.assert_refused(['apply', '--ref', t2, '--include', 'src/new\\file.c'], 'not an omitted upstream file')

    def test_refuses_non_utf8_upstream_path(self):
        (self.up / os.fsdecode(b'src/bad\xff.c')).write_bytes(b'x\n')
        t = commit(self.up)
        self.assert_refused(['apply', '--ref', t], 'not UTF-8')

    def test_refuses_replacing_a_directory_the_removals_leave_behind(self):
        put(self.up, {'src/class/a.c': None, 'src/class/crlf.h': None})
        (self.up / 'src/class').rmdir()
        t = self.upstream_change({'src/class': b'f\n'})
        put(self.lib, {'src/class/extra.c': b'e\n'})
        commit(self.lib)
        self.assert_refused(['apply', '--ref', t], 'src/class: already exists')
        put(self.lib, {'src/class/extra.c': None})
        commit(self.lib)
        (self.lib / 'src/class/empty').mkdir()   # invisible to git, yet write() could not remove src/class
        self.assert_refused(['apply', '--ref', t], 'src/class: already exists')

    def test_refuses_a_path_beneath_an_existing_file(self):
        t = self.upstream_change({'src/class/b.c': b'b\n', 'src/tusb_config.h/x.h': b'x\n'})
        self.assert_refused(['apply', '--ref', t], 'src/tusb_config.h/x.h: src/tusb_config.h is a file')
        (self.lib / 'src/link').symlink_to('class')   # a write through it would land outside its path
        commit(self.lib)
        t = self.upstream_change({'src/link/y.c': b'y\n'})
        self.assert_refused(['apply', '--ref', t], 'src/link/y.c: src/link is a file')

    def test_refuses_local_drift(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        put(self.lib, {'src/class/a.c': b'local fix\n'})
        commit(self.lib)
        self.assert_refused(['apply', '--ref', t], 'src/class/a.c: changed')
        self.assertEqual(self.run_op('verify')[1]['changed'], ['src/class/a.c'])

    def test_verify_catches_missing_extra_mode_and_codespell_edits(self):
        put(self.lib, {'src/tusb_option.h': None, 'src/class/extra.c': b'e\n', 'src/tusb.c': b'v1 fixd\n'})
        os.chmod(self.lib / 'src/class/a.c', 0o755)
        code, out = self.run_op('verify')
        self.assertEqual((code, out['missing'], out['unexpected'], out['changed']),
                         (1, ['src/tusb_option.h'], ['src/class/extra.c'], ['src/class/a.c', 'src/tusb.c']))

    def test_refuses_dirty_untracked_and_collisions(self):
        t = self.upstream_change({'src/class/b.c': b'b\n'})
        put(self.lib, {'src/class/b.c': b'mine\n'})
        self.assert_refused(['apply', '--ref', t], 'not clean')
        commit(self.lib)   # a committed collision is unexpected drift
        self.assert_refused(['apply', '--ref', t], 'src/class/b.c: unexpected')

    def test_refuses_edits_made_while_fetching(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        real = upd.Upstream.blobs

        def edit_during_fetch(up, oids):
            put(self.lib, {'src/tusb.c': b'concurrent\n'})
            return real(up, oids)
        with mock.patch.object(upd.Upstream, 'blobs', edit_during_fetch):
            self.assert_refused(['apply', '--ref', t], 'not clean', unchanged=False)
        self.assertEqual((self.lib / 'src/tusb.c').read_bytes(), b'concurrent\n')

    def test_refuses_manifest_changed_while_fetching(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        real = upd.Upstream.blobs

        def commit_manifest(up, oids):
            self.manifest(self.base, self.files() + ['src/zz.c'])
            commit(self.lib)
            return real(up, oids)
        with mock.patch.object(upd.Upstream, 'blobs', commit_manifest):
            self.assert_refused(['apply', '--ref', t], 'changed while preparing', unchanged=False)
        self.assertIn('src/zz.c', self.files())

    def test_refuses_manifest_removed_while_fetching(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        real = upd.Upstream.blobs

        def remove_manifest(up, oids):
            (self.lib / upd.MANIFEST).unlink()
            return real(up, oids)
        with mock.patch.object(upd.Upstream, 'blobs', remove_manifest):
            self.assert_refused(['apply', '--ref', t], 'changed while preparing', unchanged=False)
        self.assertFalse((self.lib / upd.MANIFEST).exists())

    def test_refuses_target_not_descending_from_base(self):
        git(self.up, 'checkout', '-q', '--orphan', 'other')
        t = self.upstream_change({'src/tusb.c': b'other\n'})
        self.assert_refused(['apply', '--ref', t], 'not an ancestor')

    def test_refuses_symlink_upstream(self):
        os.symlink('tusb.c', self.up / 'src/link.c')
        t = commit(self.up)
        self.assert_refused(['apply', '--ref', t], 'only regular files')

    def test_esp32_anchor_refusals(self):
        for option, needle in ((b'// none\n', 'occurs 0 times'), (OPTION + OPTION, 'occurs 2 times'),
                               (OPTION_ESP32, 'already mentions ARDUINO_ARCH_ESP32')):
            with self.subTest(needle=needle):
                t = self.upstream_change({'src/tusb_option.h': option})
                self.assert_refused(['apply', '--ref', t], needle)

    def test_esp32_branch_goes_only_into_the_complete_block(self):
        stray = b'#ifdef CFG_TUSB_CONFIG_FILE\n  #include CFG_TUSB_CONFIG_FILE\n#endif\n'
        self.assertEqual(upd.esp32_patch(stray + OPTION + stray), stray + OPTION_ESP32 + stray)

    def test_esp32_branch_keeps_crlf(self):
        crlf = OPTION.replace(b'\n', b'\r\n')
        self.assertEqual(upd.esp32_patch(crlf), OPTION_ESP32.replace(b'\n', b'\r\n'))

    def test_partial_write_is_reported(self):
        t = self.upstream_change({'src/class/a.c': None, 'src/class/b.c': b'b\n', 'src/tusb.c': b'v2\n'})
        real = upd.write_file

        def fail_on_tusb(f, data, exe):
            if f.name == 'tusb.c':
                raise OSError('disk full')
            real(f, data, exe)
        with mock.patch.object(upd, 'write_file', fail_on_tusb):
            code, out = self.run_op('apply', '--ref', t)
        self.assertEqual(code, 1)
        self.assertEqual(out['written'], {'created': ['src/class/b.c'], 'modified': [], 'deleted': ['src/class/a.c'],
                                          'failed': 'src/tusb.c'})
        self.assertTrue(any('disk full' in e for e in out['errors']))

    def test_failure_after_bytes_or_at_the_manifest_names_the_path(self):
        t = self.upstream_change({'src/tusb.c': b'v2\n'})
        real = os.chmod

        def chmod_fails(f, mode):
            if Path(f).name == 'tusb.c':
                raise OSError('chmod')
            real(f, mode)
        with mock.patch.object(upd.os, 'chmod', chmod_fails):
            code, out = self.run_op('apply', '--ref', t)
        self.assertEqual((code, out['written']['failed']), (1, 'src/tusb.c'))
        self.assertEqual((self.lib / 'src/tusb.c').read_bytes(), b'v2\n')   # bytes landed before the failure
        git(self.lib, 'checkout', '--', 'src')
        real_write = upd.write_file

        def manifest_fails(f, data, exe):
            if f.name == 'upstream.json':
                raise OSError('ro')
            real_write(f, data, exe)
        with mock.patch.object(upd, 'write_file', manifest_fails):
            code, out = self.run_op('apply', '--ref', t)
        self.assertEqual((code, out['written']), (1, {'created': [], 'modified': ['src/tusb.c'], 'deleted': [],
                                                      'failed': upd.MANIFEST}))

    def test_refuses_malformed_manifest(self):
        for raw in (b'[]', b'null', b'3'):
            put(self.lib, {upd.MANIFEST: raw})
            self.assert_refused(['verify'], 'JSON object')
        put(self.lib, {upd.MANIFEST: b'{"url": "x", "commit": "abc", "files": []}'})
        self.assert_refused(['verify'], 'full "commit" SHA')
        self.manifest(self.base, ['src/tusb.c', 'src/class/a.c'])
        self.assert_refused(['verify'], 'sorted, unique')


if __name__ == '__main__':
    unittest.main()
