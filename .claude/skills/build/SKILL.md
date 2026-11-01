---
name: build
description: The Adafruit TinyUSB Arduino build contract. Use when a change must be verified by building, when an agent needs the boards and example sketches a diff affects, when a HIL run or debug session needs firmware and its build record, or when a workflow asks for "the project's build". Resolves scope to boards, compiles the examples with arduino-cli against this checkout, reports pass/fail with the first error.
---

# Build contract

`scripts/check_build.py` is the entry point for verifying a change; agents and workflows call it rather than composing
`arduino-cli compile` lines.

```bash
B=.claude/skills/build/scripts/check_build.py
python3 $B --scope <changed paths or dirs>          # boards and sketches a change affects
python3 $B --base origin/master                      # same, from the branch diff
python3 $B --base origin/master --worktree           # same, plus uncommitted and untracked changes
python3 $B --select-only (--scope ... | --base REF [--worktree] | --diff-file FILE) [--hil-config ROSTER]
python3 $B --board metro_m4_express [--board ...] [--example examples/CDC/serial_echo ...]
python3 $B --board raspberry_pi_pico --shared --record build/hil/raspberry_pi_pico.build.json --hil-config test/hil/ci.json
python3 $B --board raspberry_pi_pico --shared --record build/hil/raspberry_pi_pico.exp.json --hil-config test/hil/ci.json \
  --experimental --build-property 'compiler.cpp.extra_flags=-DCFG_TUSB_DEBUG=2'
```

The last stdout line is JSON: `pass`; `firstError`, and `error` on exit 2 naming the cause or remedy; `boards[]` (one
row per board and sketch: `status` `ok`, `failed` or `error`, `log`, `artifacts`, `vidPid`, `platform`,
`foreignHeaders`, `removed`); `tests[]`; `paths[]` (each changed path's `rule` and `reason`); `uncovered`;
`nothingToBuild`; `warnings`; `input` (HEAD, dirty); `record` when one was written. Exit 0 pass, 1 a build, test or
record check failed, 2 usage or a missing prerequisite, 3 an uncovered path; 2 outranks 1, and 1 outranks 3. Each
build's log is `<buildDir>/check_build.log`.

`--select-only` prints the selection (`build.{builds,tests,paths,uncovered}`) and stops before any compile. With
`--hil-config <the host's roster>` it adds `hil.{boards,full,uncovered}`: the roster boards the change puts under the
HIL battery, and each MCU family the change reaches with no roster board on this host.

## Reporting

An agent that reports `buildOk` and `notes` (agentrc's `code-writer`) maps the JSON this way: `buildOk` is `pass`. A
pass with no build or test run is "nothing to build": say so in `notes`. Exit 3 is `buildOk` false with each
`uncovered` reason quoted verbatim: firmware no build here verifies, which a human covers or accepts. Exit 1 or 2
quotes `firstError` (and `error`). Each `warnings` entry is quoted in `notes`.

## Judgment

- **Selection is the script's**, first match per path, and `paths[].reason` says why:
  - `src/arduino/ports/<mcu>/` and `src/portable/<vendor>/<driver>/` build that MCU family's representatives
    (`REPRESENTATIVES`: rig boards first, then each distinct stack: SAMD21 beside SAMD51, the RP2040 PIO-USB host
    build), every example each one may build. `analog/max3421` and all other `src/` code build every family's
    representatives.
  - A port or driver no CI platform builds (`ports/stm32`, `st/stm32_fsdev`, `renesas/rusb2`) is uncovered.
  - The headers the cores compile their own sources against (`CORE_CONSUMED`: `Adafruit_TinyUSB_API.h`,
    `Adafruit_USBD_CDC.h`, `Adafruit_USBD_Interface.h` under `src/arduino/`) are uncovered: both cores include
    those three from their bundled copy of this library, never from this checkout (`-I` to it in the rp2040
    core's usbstack flags), so no build here shows a change to them working.
  - A sketch dir under `examples/` builds on every board in the table whose ci-arduino filters allow it.
  - This script builds its tests plus a `serial_echo` smoke build on `raspberry_pi_pico` and `metro_m4_express`;
    the HIL harness and rosters (`.claude/skills/hil-contract/scripts/`, `test/hil/`) run their tests and select every
    roster board for HIL; docs, agent and repo metadata build nothing; anything else is uncovered.
- **Boards** (`BOARDS`): CI's matrix (`.github/workflows/githubci.yml`) with adafruit/ci-arduino `all_platforms.py`'s
  FQBNs under the same names, plus the rig boards `raspberry_pi_pico`, `adafruit_fruit_jam` and `metro_m4_express`,
  named as in tinyusb's HIL farm. Example eligibility mirrors ci-arduino `build_platform.py`: `.<platform>.test.skip`,
  a `.skip.txt` line equal to the platform, and `.<platform>.test.only` when any `.*.test.only` exists; a rig board
  takes its CI platform's files (`filters`). A board whose core is not installed is exit 2 naming
  `arduino-cli core install`; a missing library dependency fails that sketch, the README's `arduino-cli lib install`
  line installs them. Local cores need not be CI's versions; the record names what built.
- **Every compile** is `arduino-cli compile --clean --format json --warnings all --fqbn <F> --library <this checkout>
  --build-path <dir>`, so the core and every library rebuild from scratch and this library comes from this checkout
  whatever else is installed (the primary checkout under `~/Arduino/libraries`, a core's bundled copy). The build
  fails if arduino-cli's `used_libraries` names another directory for it. `foreignHeaders` lists every header the
  build's `.d` files took from another copy of this library, with `matches` against this checkout's copy; a
  mismatch is a warning on a plain build and refuses a HIL record.
- **Private dirs for parallel agents.** Each build uses `build/agent-<pid>/<board>/<sketch>`, removed when it passes
  (its `artifacts` say what it produced); a failed one is kept for its log. `--shared` builds into
  `build/hil/<board>/<sketch>` and keeps it for flashing, holding `build/hil/.lock` while it builds (a second shared
  build is refused, never waited for; the HIL copies what it flashes once the hashes match). `-j` (default 4) compiles
  in parallel.
- **A HIL record** (`--board B --shared --record FILE --hil-config ROSTER`, `FILE` inside the checkout, by convention
  `build/hil/<board>.build.json`) builds the HIL sketches (`HIL_SKETCHES`: `serial_echo`, `hid_generic_inout`,
  `msc_ramdisk`) for one roster board and is the HIL contract's build receipt. It requires a clean tree before and the
  same source identity after the build, and checks each image: the ELF's load segments (physical addresses of
  `PT_LOAD` with file bytes) inside the board's `span` (metro_m4_express [0x4000, 0x80000), above the UF2 bootloader;
  raspberry_pi_pico [0x10000000, 0x10200000); adafruit_fruit_jam [0x10000000, 0x11000000)), the HEX's runs equal to
  those spans where the core makes one, and no foreign header that differs. It records HEAD, the FQBN, the platform
  (id, version, install dir, and commit and dirty state when the core is a git checkout), the arduino-cli version, the
  roster's and harness's sha256, and per sketch its ELF and HEX with sha256, load spans, VID:PID and foreign headers;
  paths are checkout-relative so the record stages to a rig unchanged.
- **Experimental records** (`--experimental`, with `--record`) are for debug and instrumentation: a dirty tree and
  `--build-property` are allowed, untracked files under `src/`, `examples/` or `test/hil/` are refused (put
  instrumentation in tracked files), the source must not change during the build, and `git diff HEAD` is saved beside
  the record (its path with `.patch` for `.json`, named and hashed in `patch`). A drifted foreign header is allowed and listed. The HIL battery and
  the restore refuse them; a manual flash under a hold accepts them.
- **Debug builds**: both cores compile with `-g`, so the kept ELF (`build/hil/<board>/<sketch>/<sketch>.ino.elf`)
  carries symbols at the release optimisation (`-Os`). Other flags go through `--build-property` (each core's
  `platform.txt` names its extra-flag properties) into an experimental record.
- **Printing a command is not verification.** A verdict is the JSON of a run that happened; an agent that cannot run
  the script reports the build as not run.
