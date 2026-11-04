# Adafruit TinyUSB Arduino Agent Instructions

## Working Rules

- An Arduino library with no standalone build: sketches compile against this checkout through the build contract.
  CI's library list (`ARDUINO_LIBS`) and board matrix are in `.github/workflows/githubci.yml`, its FQBNs in
  adafruit/ci-arduino's `all_platforms.py`.
- Code references — wrappers: `src/arduino/` (`Adafruit_USBD_*`, `Adafruit_USBH_Host.*`, class dirs `cdc/`, `hid/`,
  `msc/`, `midi/`, `video/`, `webusb/`); core API and integration macros: `src/arduino/Adafruit_TinyUSB_API.h`;
  ports: `src/arduino/ports/<mcu>/`, their configs dispatched by `src/tusb_config.h`; vendored TinyUSB:
  `src/{class,common,device,host,osal,portable}/`, `src/tusb.{c,h}`, `src/tusb_option.h`; examples:
  `examples/<class>/<sketch>/`, filtered per CI platform by ci-arduino's `.<platform>.test.only`,
  `.<platform>.test.skip` and `.skip.txt`.

## Code Rules

- `.pre-commit-config.yaml` is authoritative: clang-format v15 on the library's own C/C++ (never `examples/` or the
  vendored TinyUSB paths), codespell with `-w`.
- Keep each file's line endings; some are CRLF (`src/tusb_config.h`, several `tusb_config_<mcu>.h`).
- Vendored TinyUSB is hathach/tinyusb's bytes, except `src/tusb_option.h`'s ESP32 branch (the ESP32 core's own
  TinyUSB headers come first on the include path, `tusb_config_esp32.h:32`). Sync and verify it only with the
  `update-tinyusb` skill (`.claude/skills/update-tinyusb/SKILL.md`).
- Ports implement the `TinyUSB_Port_*` hooks of `src/arduino/Adafruit_TinyUSB_API.h`. On cores with built-in support
  (SAMD, nRF, RP2040, CH32) the core initialises and services the stack through that header's `TinyUSB_Device_*`
  functions (nRF from the library's own RTOS task); elsewhere (STM32, mbed RP2040) `TINYUSB_NEED_POLLING_TASK` is set
  and sketches call `TinyUSBDevice.begin()` and `TinyUSBDevice.task()`. ESP32 differs throughout: its core owns
  TinyUSB initialisation and the task, CDC is the core's, the port hooks are empty and `TinyUSB_Device_FlushCDC` is
  not built.
- The RP2040 and SAMD cores compile their own bundled copy of `Adafruit_TinyUSB_API.h`, `Adafruit_USBD_CDC.h` and
  `Adafruit_USBD_Interface.h` (the build reports the live set as foreign headers); changing a declaration they rely
  on needs a matching core update.
- `library.json` (PlatformIO registry) and the `PLATFORMIO` guards serve users; there is no PlatformIO build here.

## Skills

- Agents, skills and workflows beyond this repo's `.claude/` come from [agentrc](https://github.com/hathach/agentrc);
  install it before agent work on Adafruit TinyUSB Arduino.
- Skill script tests live in `.claude/test/test_*.py`; pre-commit runs them against the installed agentrc
  (`${AGENTRC_DIR:-$HOME/.claude}`), whose `arduino` skill holds the mechanics both contracts run (shared with
  Adafruit SPIFlash) and whose HIL scripts they load; CI fetches the commit `test/hil/agentrc.json` pins instead.
- Hardware diagnosis and repair use agentrc's `hw-debugger`; independent hardware claims and committed-fix validation
  use `hw-validator`; routine HIL runs use `hil-operator` — all orchestrated by `chief`.
- Library audit: the `code-audit` workflow with `dirs` (e.g. `src/arduino`, `src/arduino/ports/<mcu>`) and
  `dimensions`: `descriptors and interfaces: configuration descriptor building, interface and endpoint allocation,
  string descriptors, re-enumeration`; `core integration: built-in vs polling cores, the ESP32 exceptions, headers the
  cores bundle`; `concurrency: the TinyUSB task vs the Arduino loop and ISRs, shared buffers`; `style: repo
  conventions and the clang-format scope`.

## Build and Validate

- Build contract: `.claude/skills/build/SKILL.md`. It resolves a change to boards and example sketches and builds them
  with arduino-cli against this checkout; `--shared --record` writes the HIL build record and keeps the images HIL
  flashes.
- HIL contract: `.claude/skills/hil-contract/SKILL.md`. It owns taking and releasing a rig board, names the roster for
  the host you are on, runs the battery on a dev PC's board or the ci.lan farm, and gives hw-validator and hw-debugger
  their manual sessions. Manual runs only: no CI job runs it. Hardware validation comes from its report, never from a
  build; what no roster board covers is reported pending.
- Before submitting, and again after a rebase: `pre-commit run --all-files`.
- For code changes: `python3 .claude/skills/build/scripts/check_build.py --base origin/master --worktree`.

## PRs and Follow-ups

- Before opening or updating a PR, follow Build and Validate; CI does not run HIL.
- A non-draft PR opened on the human's request, unless they declined babysitting, or their request or yes to babysit
  one, authorizes one headless `chief` to drive its reviews and CI to green: push commits to its branch, reply to and
  resolve its review threads through `pr-babysit` with `autoPush: true` and `markSonar: true`. Launch it in the PR
  worktree through agentrc's `headless-chief` skill (`chief_run.py`), never a bare `claude -p`, and relay its exit as
  that skill directs. Merging needs the human's own words asking to merge that PR. `pr-babysit` args:
  `{"pr": <num>, "protected": "^test/hil/[^/]+\\.json$"}`; reviewers are agentrc's default (`protected` keeps the HIL
  rosters and the agentrc pin out of automated fixes).
- The follow-up label is `follow-up`.
