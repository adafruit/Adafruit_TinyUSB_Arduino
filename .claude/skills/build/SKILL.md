---
name: build
description: The Adafruit TinyUSB Arduino build contract. Use when a change must be verified by building, when an agent needs the boards and example sketches a diff affects, when a HIL run or debug session needs firmware and its build record, or when a workflow asks for "the project's build". Resolves scope to boards, compiles the examples with arduino-cli against this checkout, reports pass/fail with the first error.
---

# Build contract

`scripts/check_build.py` is the entry point for verifying a change; agents and workflows call it rather than composing
`arduino-cli compile` lines. It is this library's profile for agentrc's `arduino` skill (`arduino_build.py`), loaded
from the installed agentrc (`${AGENTRC_DIR:-$HOME/.claude}`), never fetched; CI fetches the commit
`test/hil/agentrc.json` pins. `.claude/skills/hil-contract/scripts/agentrc_pin.py check` names the one in use. A record
binds that agentrc's commit and file digests: after its files change, build again. That skill's page has the mechanics
this library shares with Adafruit SPIFlash.

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
`nothingToBuild`; `warnings`; `input` (HEAD, dirty); `narrowed`; `record` when one was written. Exit 0 pass, 1 a build,
test or record check failed, 2 usage or a missing prerequisite, 3 an uncovered path; 2 outranks 1, and 1 outranks 3.
Each build's log is `<buildDir>/check_build.log`.

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
    (`REPRESENTATIVES`: rig boards first, then each distinct stack or core: RP2350 beside RP2040, SAMD21 beside
    SAMD51, the RP2040 native-host build, the ESP32-P4's high-speed OTG, and WCH alongside community CH32), every
    example each one may build. `analog/max3421`,
    all other `src/` code and `library.properties` build every family's representatives.
  - A port or driver no CI platform builds (`ports/stm32`, `st/stm32_fsdev`, `renesas/rusb2`) is uncovered.
  - The headers the cores compile their own sources against (`CORE_CONSUMED`: `Adafruit_TinyUSB_API.h`,
    `Adafruit_USBD_CDC.h`, `Adafruit_USBD_Interface.h` under `src/arduino/`) are uncovered: both cores include
    those three from their bundled copy of this library, never from this checkout (`-I` to it in the rp2040
    core's usbstack flags), so no build here shows a change to them working.
  - A sketch dir under `examples/` builds on every board in the table whose ci-arduino filters allow it.
    `MassStorage/msc_esp32_file_browser` builds on none (its `.test.only` names funhouse, magtag and metroesp32s2,
    in neither this table nor CI's matrix): no build here compiles it, and `test_build.py` pins that list.
  - This script builds its tests plus a `serial_echo` smoke build per image kind (`raspberry_pi_pico`,
    `metro_m4_express`, `espressif_s3_devkitm`); a HIL fixture (`test/hil/esp32_ts3usb30.cpp`) builds the HIL
    sketches of its boards; the HIL harness and rosters (`.claude/skills/hil-contract/scripts/`, `test/hil/`) and the
    HIL workflow (`HIL_WORKFLOW`) run every contract test but the build's and select every roster board for HIL; the
    report workflow (`REPORT_WORKFLOW`), run from the default branch, runs its tests only; docs, agent and repo
    metadata build nothing; anything else is uncovered.
- **Boards** (`BOARDS`): CI's matrix (`.github/workflows/githubci.yml`) with adafruit/ci-arduino `all_platforms.py`'s
  FQBNs under the same names, except `ch32_community_ch32v20x`: both the workflow and `BOARDS` append
  `usb=tinyusb_usbd` because ci-arduino's default disables USB. The contract tests check that override matches.
  The table also includes the rig boards named as in tinyusb's HIL farm: `raspberry_pi_pico`,
  `adafruit_fruit_jam`, `metro_m4_express`, the native-host `raspberry_pi_pico_w` and `raspberry_pi_pico2` (`role`
  host), and `espressif_s3_devkitm` and `espressif_p4_function_ev`. Each rig board builds on the core ci-arduino
  installs for its CI platform (`filters`), which the HIL CI sets up (the HIL contract's CI). An ESP32 rig board
  compiles a copy of each sketch beside its build dir with its `fixture` added, leaving `examples/` untouched, and
  snapshots the core's `boot_app0.bin` into the build dir for `flash_args`. Example eligibility mirrors ci-arduino
  `build_platform.py`: `.<platform>.test.skip`, a `.skip.txt` line equal to the platform, and `.<platform>.test.only`
  when any `.*.test.only` exists; a rig board takes its CI platform's files (`filters`). A board whose core is not
  installed is exit 2 naming `arduino-cli core install`. Local cores need not be CI's versions; the record names what
  built. For CH32, install the appropriate core with its additional index URL:

  ```bash
  arduino-cli core install ch32-community:ch32v --additional-urls https://github.com/ch32-community/board_manager_files/raw/main/package_ch32-commmunity_index.json
  arduino-cli core install WCH:ch32v --additional-urls https://github.com/openwch/board_manager_files/raw/main/package_ch32v_index.json
  ```

- **Dependencies** (`arduino` skill): `library.properties` `depends=` plus `EXTRA_DEPENDENCIES` (CI's `ARDUINO_LIBS`
  beyond it), and theirs, transitively, must be installed before a build: a missing one is exit 2 naming the
  `arduino-cli lib install` line. A compile that takes any other library, outside the core, fails its sketch; the
  JSON's `dependencies` and each row's `libraries` say what was used.
- **Every compile** is `arduino-cli compile --clean --format json --warnings all --fqbn <F> --library <this checkout>
  --build-path <dir>`, so the core and every library rebuild from scratch and this library comes from this checkout
  whatever else is installed (the primary checkout under `~/Arduino/libraries`, a core's bundled copy). The build
  fails if arduino-cli's `used_libraries` names another directory for it. `foreignHeaders` lists every header the
  build's `.d` files took from another copy of this library, with `matches` against this checkout's copy; a
  mismatch is a warning on a plain build.
- **Private dirs for parallel agents.** Each build uses `build/agent-<pid>/<board>/<sketch>`, removed when it passes
  (its `artifacts` say what it produced); a failed one is kept for its log. `--shared` builds into
  `build/hil/<board>/<sketch>` and keeps it for flashing, holding `build/hil/.lock` while it builds (a second shared
  build is refused, never waited for; the HIL copies what it flashes once the hashes match). `-j` (default 4) compiles
  in parallel.
- **A HIL record** (`--board B --shared --record FILE --hil-config ROSTER`, `FILE` inside the checkout, by convention
  `build/hil/<board>.build.json`) builds the HIL sketches of the board's role (`HIL_SKETCHES`: device `serial_echo`,
  `hid_generic_inout`, `msc_ramdisk`; host `host_device_info`; the first is the restoration image) for one roster
  board and is the HIL contract's build receipt. It requires a clean tree before and the
  same source identity after the build, and checks the image the roster's flasher writes against the board's `span`
  (metro_m4_express [0x4000, 0x80000), above the UF2 bootloader; raspberry_pi_pico [0x10000000, 0x10200000);
  adafruit_fruit_jam [0x10000000, 0x11000000); the ESP32 boards their 4 MB SPI flash). For an openocd board that
  image is the ELF, its load segments (physical addresses of `PT_LOAD` with file bytes) inside the span. For a J-Link
  board it is the HEX, its runs equal to the ELF's allocated sections and inside the span; the ELF's own segments are
  not checked, since the SAMD ELF's first starts at 0x0. For an esptool board it is the core's `flash_args`: a line of
  options, then each image at its offset, every file in the build dir, ascending, not overlapping, inside the span.
  A device sketch needs its VID:PID: a `-D` define, on ESP32 the variant's `pins_arduino.h` (the S3's 303a:1001),
  else the library's fallback. A differing foreign header refuses unless the build's core bundles it, its
  drift then noted in the HIL report. It records (schema 2, the `arduino`
  skill's The HIL build record) HEAD, the FQBN, the platform (id, version, install dir, and commit and dirty state
  when the core is a git checkout), the arduino-cli version, the resolved dependencies with their contents' sha256,
  the roster's sha256, the contract files' and the agentrc engines' sha256, the board's role, ESP `chip` and `fixture` (path and sha256), and per sketch its
  images with sha256, load spans, VID:PID, foreign headers, used libraries and, for esptool, `segments` (the build's `flash_args`
  hashed beside them); paths are checkout-relative so the record stages to a rig unchanged.
- **Experimental records** (`--experimental`, with `--record`) are for debug and instrumentation: a dirty tree and
  `--build-property` are allowed, untracked files under `src/`, `examples/` or `test/hil/` are refused (put
  instrumentation in tracked files), the source must not change during the build, and `git diff HEAD` is saved beside
  the record (its path with `.patch` for `.json`, named and hashed in `patch`). Any drifted foreign header is allowed
  and listed. The HIL battery and the restore refuse them; a manual flash or GDB session under a hold accepts them.
- **Debug builds**: both cores compile with `-g`, so the kept ELF (`build/hil/<board>/<sketch>/<sketch>.ino.elf`)
  carries symbols at the release optimisation (`-Os`). Other flags go through `--build-property` (each core's
  `platform.txt` names its extra-flag properties) into an experimental record.
- **Printing a command is not verification.** A verdict is the JSON of a run that happened; an agent that cannot run
  the script reports the build as not run.
