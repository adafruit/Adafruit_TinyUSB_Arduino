#!/usr/bin/env python3
"""agentrc_pin.py as this library configures and uses it: test/hil/agentrc.json and the identity its contracts bind.
The bootstrap's common behavior and this consumer's conformance (the staged round trip, contract files, load()
targets) are agentrc's, run by `agentrc_pin.py test` before these. Nothing is fetched and nothing reaches a rig."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.claude' / 'skills' / 'hil-contract' / 'scripts'))
import agentrc_pin  # noqa: E402
import hil_test  # noqa: E402

hil_test.engine()   # the selected agentrc: a missing one fails here, naming the remedy


class Config(unittest.TestCase):
    def test_the_repository_config_is_this_librarys(self):
        data = agentrc_pin.config()
        self.assertEqual({k: v for k, v in data.items() if k not in ('url', 'commit')},
                         {'cache_namespace': 'adafruit-tinyusb-arduino', 'profile_api': 1, 'features': [],
                          'extra_engines': ['skills/arduino/scripts/arduino_ci.py',
                                            'skills/hil/scripts/gh_pr_comment.py']})


class Selected(unittest.TestCase):
    """The agentrc this checkout really selects: the installed one on a dev PC, the pin in CI."""

    def test_hil_test_binds_the_selected_identity(self):
        ident = agentrc_pin.identity()
        self.assertEqual(ident['source'], 'pin' if agentrc_pin.ci() else 'installed')
        self.assertEqual(hil_test.BUILD.AGENTRC, ident)


if __name__ == '__main__':
    unittest.main()
