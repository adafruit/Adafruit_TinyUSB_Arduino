#!/usr/bin/env python3
"""The HIL battery in CI (SKILL.md beside this directory, CI): the hosted runner's setup and packed tree per board,
the rig runner's battery over them (agentrc arduino_ci.py, its docstring the full usage).

  hil_ci.py setup --board B --ci DIR [--versions FILE]          (hosted) ci-arduino's setup for B's CI platform
  hil_ci.py pack --board B --record FILE --out DIR --run-id N --attempt N    (hosted) B's packed tree
  hil_ci.py run --hosted DIR --head SHA --run-id N --attempt N --out STATE/<run>-<attempt> [-b B]...
                [--lock-wait S] [--deadline S] ROSTER                        (rig) the battery, summary.json

Exits: setup/pack 0 done, 2 refused or no usable agentrc; run 0 every board passed, 1 a board failed or did not run,
2 a blocker, an aborted run, or refused.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agentrc_pin  # noqa: E402
import hil_test  # noqa: E402


def main(argv=None):
    """hil_test's profile, run by agentrc's arduino_ci.py."""
    try:
        hil_test.engine()
        ci = agentrc_pin.load('skills/arduino/scripts/arduino_ci.py')
    except agentrc_pin.PinError as e:
        print(json.dumps({'pass': False, 'error': str(e), 'remedy': e.remedy}), flush=True)
        return 2
    return ci.run(hil_test, argv)


if __name__ == '__main__':
    sys.exit(main())
