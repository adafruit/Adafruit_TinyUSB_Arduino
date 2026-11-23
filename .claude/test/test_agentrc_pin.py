#!/usr/bin/env python3
"""agentrc_pin.py as this library configures and uses it: test/hil/agentrc.json, the closure its contracts load from,
and the staged copy a rig tree carries. The bootstrap's common behavior is tested in agentrc. Nothing is fetched from
the network and nothing reaches a rig."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import agentrc_pin  # noqa: E402
import hil_test  # noqa: E402

hil_test.engine()   # the selected agentrc: a missing one fails here, naming the remedy

CONFIG = 'test/hil/agentrc.json'
CONSUMER = '.claude/skills/hil-contract/scripts/agentrc_pin.py'
CLEAN_ENV = {k: v for k, v in os.environ.items()
             if not k.startswith('GIT_') and k not in ('AGENTRC_DIR', 'CI', 'GITHUB_ACTIONS')}


def loaded_scripts():
    """Every agentrc script this library's own scripts load through the bootstrap."""
    return sorted({m for f in (ROOT / '.claude' / 'skills').rglob('*.py')
                   for m in re.findall(r"agentrc_pin\.load\('([^']+)'\)", f.read_text(encoding='utf-8'))})


class Config(unittest.TestCase):
    def test_the_repository_config_is_this_librarys(self):
        data = agentrc_pin.config()
        self.assertRegex(data['commit'], r'^[0-9a-f]{40}$')
        self.assertEqual({k: v for k, v in data.items() if k not in ('url', 'commit')},
                         {'cache_namespace': 'adafruit-tinyusb-arduino', 'profile_api': 1, 'features': [],
                          'extra_engines': ['skills/arduino/scripts/arduino_ci.py',
                                            'skills/hil/scripts/gh_pr_comment.py']})

    def test_the_bootstrap_and_its_config_are_contract_files(self):
        self.assertLessEqual({CONSUMER, CONFIG}, set(hil_test.BUILD.CONTRACT_FILES))


class Selected(unittest.TestCase):
    """The agentrc this checkout really selects: the installed one on a dev PC, the pin in CI."""

    def test_hil_test_binds_the_selected_identity(self):
        ident = agentrc_pin.identity()
        self.assertEqual(ident['source'], 'pin' if agentrc_pin.ci() else 'installed')
        self.assertEqual(hil_test.BUILD.AGENTRC, ident)

    def test_every_script_this_library_loads_is_in_the_closure(self):
        scripts = loaded_scripts()
        self.assertIn('skills/arduino/scripts/arduino_ci.py', scripts)
        self.assertLessEqual({*scripts, *agentrc_pin.ENGINES, *agentrc_pin.config()['extra_engines']},
                             set(agentrc_pin.identity()['files']))

    def test_this_copy_is_the_selected_canonical_bootstrap(self):
        canonical = Path(agentrc_pin.identity()['root']) / agentrc_pin.SELF
        self.assertEqual((ROOT / CONSUMER).read_bytes(), canonical.read_bytes())

    def test_a_staged_tree_loads_every_script_from_its_own_copy(self):
        # a fresh interpreter: the staged modules must never replace the engines the other tests load
        helper = agentrc_pin._selection()[0]
        ident = agentrc_pin.identity()
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            for rel in (CONSUMER, CONFIG):
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / rel, tree / rel)
            helper.stage(ident, tree, name='agentrc')
            (tree / 'manifest.json').write_text('{}\n')
            code = '''if True:
                import json, sys
                sys.path.insert(0, sys.argv[1])
                import agentrc_pin as pin
                ident = pin.identity()
                engines = [pin.load(e).__file__ for e in (*pin.ENGINES, *pin.config()['extra_engines'])]
                print(json.dumps({'source': ident['source'], 'commit': ident['commit'], 'sha256': ident['sha256'],
                                  'engines': engines}))
            '''
            p = subprocess.run([sys.executable, '-I', '-B', '-c', code, str((tree / CONSUMER).parent)],
                               capture_output=True, text=True,
                               env={**CLEAN_ENV, 'CI': 'true', 'AGENTRC_DIR': str(tree / 'absent')})
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            got = json.loads(p.stdout)
            self.assertEqual((got['source'], got['commit'], got['sha256']),
                             ('staged', ident['commit'], ident['sha256']))
            for f in got['engines']:
                self.assertTrue(Path(f).is_relative_to(tree / 'agentrc'), f)


if __name__ == '__main__':
    unittest.main()
