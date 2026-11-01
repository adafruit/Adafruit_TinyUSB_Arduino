---
name: hil-contract
description: The Adafruit TinyUSB Arduino HIL contract, under agentrc's `hil` rules. Use when running the Arduino HIL battery on rig boards (Raspberry Pi Pico, Adafruit Fruit Jam, Metro M4), when a HIL run fails, hangs or reports a board locked, when reading its report, or for a manual session (flash, GDB, a command) on a rig board from hw-validator or hw-debugger.
---

# HIL contract

Read agentrc's `hil` skill first; this page supplies its contract sections. Manual runs only: no CI job runs this
battery. Scripts, all in `scripts/`:

- `hil_test.py`: the harness on the host with the boards.
- `hil_remote.py`: drives the harness on ci.lan from the dev PC that built the firmware.
- `agentrc_pin.py`: the agentrc commit both load their shared scripts from.

Each script's docstring has its full usage.

```bash
H=.claude/skills/hil-contract/scripts
python3 $H/agentrc_pin.py fetch            # once per pin: test/hil/agentrc.json into ~/.cache/adafruit-tinyusb-arduino/
```

## Hosts/rosters

| Host           | How to tell                          | Roster                                                       |
|----------------|--------------------------------------|--------------------------------------------------------------|
| ci.lan         | the HIL farm; reached over ssh       | `test/hil/ci.json` (tracked)                                 |
| a dev PC       | a board on this PC's USB             | `test/hil/local.json` (ignored; copy `local.example.json`)   |

ci.lan has no arduino-cli and no checkout of this library: firmware is built on the dev PC and staged by
`hil_remote.py`. On a dev PC without `test/hil/local.json`, ask the user for one; an agent that cannot ask returns
each requested board not-run naming the missing file.

A roster entry:

- `name`: the board's `check_build.py` rig name, which is also its lock name. The names are tinyusb's farm names,
  so this harness and tinyusb's exclude each other on a shared board.
- `uid`: the DUT's USB serial under Arduino firmware (Limits, commissioning).
- `flasher`: `name` `openocd` or `jlink`, `uid` the probe serial, `args`, for openocd `vid_pid` and `verify`.
- `debug.openocd`: the openocd config the GDB session uses.
- `arduino.tests`: the tests the battery runs.

ci.lan's boards are also on tinyusb's roster (tinyusb `test/hil/tinyusb.json`) and tinyuf2's. This contract performs
no rig-wide operation (hub power, port cycle, controller rebind).

**Farm check.** There is no launcher of its own: these boards are tinyusb farm boards, so use tinyusb's:
`ssh ci.lan 'cd ~/code/tinyusb && python3 test/hil/helper/hil_farm_check.py -b raspberry_pi_pico -b adafruit_fruit_jam
-b metro_m4_express'`.

## Selection

A fixed battery per board, the roster's `arduino.tests`:

- `cdc_echo`: `serial_echo` echoes random payloads upper-cased.
- `hid_inout`: `hid_generic_inout` echoes a 64-byte report over hidraw.
- `msc_ramdisk`: `msc_ramdisk`'s 8 KiB disk reads back with its FAT12 `README.TXT`.

`-t` narrows the tests and `-b` the boards. The PR-scoped selector is the build contract's:

```bash
python3 .claude/skills/build/scripts/check_build.py --select-only --base origin/master --hil-config test/hil/ci.json
```

Its `hil.boards` are the roster boards to run, `hil.full` means the harness or a roster changed (every board), and
`hil.uncovered` names each MCU family the change reaches with no board on that roster: report those uncovered, never
pass.

## Build

Each run board needs a clean HIL record at HEAD, which binds the run to its firmware (build skill, "A HIL record"):

```bash
python3 .claude/skills/build/scripts/check_build.py --board <board> --shared --record build/hil/<board>.build.json \
  --hil-config <roster>
```

The harness admits a record only when:

- it is for that board and the roster's sha256;
- it was built at the checkout's HEAD on a clean tree, or the staged manifest's head on a rig;
- every image hashes as recorded.

A record carries the image its flasher writes (the ELF for openocd, the HEX for J-Link). The battery and the restore
take clean records only. A manual flash or GDB session also takes an experimental record (`--experimental`, same
HEAD, a dirty tree allowed, its diff saved beside it).

## Execution

Budgets: a flash 120 s, a verify or reset session 60 s, re-enumeration 20 s, a test 30 s, a board 900 s. A test
starts only when its worst case plus one restore still fits the board budget. Boards run one after another.

**On ci.lan, from the dev PC** (records built first):

```bash
python3 $H/hil_remote.py run --run-id <id> [-b BOARD]... [-t TEST]...
python3 $H/hil_remote.py wait <id> --timeout 570    # repeat while it exits 3
python3 $H/hil_test.py report .hil-remote/<id>/hil-run
```

- `run` refuses a dirty tree, a missing or stale record and an image that no longer matches. It then stages into a
  create-only attempt `~/hil-remote/adafruit-tinyusb-arduino/<id>`: the harness, roster and records, the agentrc
  files the harness loads (under `agentrc/`), and `manifest.json` pinning each by digest.
- The battery is launched detached (`hil_test.py run --staged manifest.json`), so a dropped ssh does not stop it.
- A run id is used once; a retry takes a new one.
- `wait` exits 0 done (collected into `.hil-remote/<id>/`), 3 still running, 4 dead, 5 inconclusive.
- `collect <id>` fetches what the attempt has at any time; its collection is `complete` only once the run is done.
- Completion is the attempt's done record plus `hil-run/hil_report.json`.

**On the dev PC with the board:** `python3 $H/hil_test.py run [-b BOARD]... [-t TEST]... test/hil/local.json`
(report under `build/hil-run/<timestamp>/`).

**A manual session** (hw-validator, hw-debugger) on a ci.lan board:

```bash
python3 $H/hil_remote.py session open --run-id <id> -b <board>      # stages the clean record, takes the hold
python3 $H/hil_remote.py session stage <id> <record>...             # prints the --bundle/--build-record to use
python3 $H/hil_remote.py session exec <id> flash --bundle B --build-record R --sketch S
python3 $H/hil_remote.py session exec <id> gdb --bundle B --build-record R --sketch S --ex 'bt' [--ex ...]
python3 $H/hil_remote.py session exec <id> exec [--timeout S] -- <argv>   # a command on the rig, in the tree
python3 $H/hil_remote.py session close <id>                         # restore serial_echo, then release
```

- `gdb` runs `gdb-multiarch -batch` over `target extended-remote | openocd ... -c "gdb_port pipe"`, with the roster's
  `debug.openocd` pinned to the board's probe serial. The attach halts the core and the detach resumes it, so the
  firmware runs on: an echo after the detach checked it for read-only and breakpoint sessions on Pico, read-only on
  Fruit Jam. openocd exits when GDB detaches. Breakpoints are hardware ones, and there is no flash `load`: reflash
  with `flash`.
- A refused `session open` (the board is held) prints the holder and leaves its attempt dir unused. Within the
  lock-wait budget, retry `session open` with a new run id; never wait on a recovery blocker.
- Each command and child is bounded (`exec`/`gdb` `--timeout` at most 600 s) and runs through agentrc's
  `run_tracked`.
- One session command runs at a time per tree and board. It ignores SIGHUP; SIGTERM ends it as SIGINT does, its
  child ended or recorded pending.
- Each command's JSON is the last stdout line and `result.json` in its `build/hil-manual/<board>-<cmd>-<time>/`.
- On a dev PC the same commands are `hil_test.py flash|gdb|exec|restore -b B --under-hold <reason> ... ROSTER`, under
  `hil_test.py lock hold B --reason <reason>`.

## Reservation

The lock helper is `hil_test.py lock (hold|release|status) ...`, agentrc's `hil_lock.py` at the pin, with lock dir
`/tmp/tinyusb-hil-locks`.

- `hil_test.py run` and `commission` lock each board themselves (protected, reason `hil_test.py`, `hil_test.py
  commission`): never pre-hold for them.
- A manual session holds the board under `arduino-hil session <id>`. Every session command checks that hold, and
  refuses while the board's pending-session record (`arduino-hil`) lists a child not proven ended.
- Cleanup state is `serial_echo` flashed and echoing, every child proven ended, and the hold released.
- The battery restores `serial_echo` after each board. `session close` restores, verifies a CDC echo and releases
  in one command, and releases nothing when the restore fails.

## Results

- **`hil_test.py run`** writes `hil_report.json` (schema `adafruit-tinyusb-arduino-hil`, agentrc `hil_report_core`,
  with a Markdown view) in its run dir. It prints the same body as its last stdout line.
- **Exits:** 0 every board passed, 1 a board failed or did not run (each cleaned up or untouched), 2 refused before any
  hardware access, 4 a cleanup did not finish (the board handed to a cleanup holder) or the run was aborted.
- **Report rows**, one per board:
  - `ran`, `pass`, `locked`, `recoveryBlocker`, `detail`, `firmware` (the record);
  - `tests[]`, each with `test`, `pass`, `detail`, `device` and the detach evidence;
  - `cleanup`, with `pass`, `state`, `evidence`, `holder` and `receipts`.
- **Delegated output:** `hil_test.py report <run dir> [-b BOARD]...` prints `{pass, results, caveat, wedged,
  reportPath}`, each result carrying its verdict. That is the `hil-operator` reply, copied verbatim. `wedged` is the
  boards that ran with cleanup pending or unknown. A run that started nothing gets one not-run row per requested
  board.
- **Verdicts:**
  - blocker: a cleanup holder, a recovery blocker found at admission, a cleanup `pending` or `unknown`, or an aborted
    run;
  - not-run: probe missing, board locked, record refused;
  - pass: every test passed and `serial_echo` was restored;
  - otherwise fail.
- **Retries:** the caller (the agent that asked for the run) retries a fail once under a new run id, per the `hil`
  Caller rules; `hil-operator` does not retry.

## Recovery

Recovery needs a separate dispatch naming the board. It applies to:

- a report row with `cleanup.holder` (exit 4), or a `commission` that exited 4: its
  `build/hil-manual/commission-<board>-<time>/result.json` carries the holder token and receipts and stands in
  for the report in step 3;
- a protected record left by a run that died;
- a board's `arduino-hil` pending-session record;
- a session whose `close` failed (its hold kept).

The dispatch carries the board, the failed report (or session id), the holder token and the receipts. No
`usb-kernel-recover` rung is part of this contract. On ci.lan, run each command in the failed run's attempt dir
(`ssh ci.lan 'cd ~/hil-remote/adafruit-tinyusb-arduino/<id> && python3 .claude/skills/hil-contract/scripts/hil_test.py
...'`), adding `--staged manifest.json` to `restore`. On a dev PC, run them from the checkout.

1. `hil_test.py lock status`, and `hil_test.py report <run dir> -b <board>` for the row's holder token and receipts.
2. Take the board for the restore, keeping it blocked throughout:
   - A live cleanup holder (its record's reason is `hil_test.py`): restore under it, reason R = `hil_test.py`.
   - A dead holder or a protected record: `hil_test.py lock hold --take-over <board> --reason recovery-<id>` prints
     token T; R = `recovery-<id>`.
3. `hil_test.py restore -b <board> --build-record build/hil/<board>.build.json --under-hold R --report
   <run dir>/hil_report.json <roster>`. It refuses while a child of the failed run is not proven ended: stop and report
   that receipt, since ending a child is outside this contract.
4. Once the restore passed, `hil_test.py lock release --token <the holder's token, or T>`; that release refuses too
   while a receipt is open. On failure, keep the hold and report.

A failed `session close`: the session keeps its hold (`arduino-hil session <id>`). Once the session's pending
children are proven ended (the next session command clears the record by itself), run `hil_remote.py session close
<id>` again. It restores from the session's own `bundles/restore/` record, and its JSON is the last stdout line and
`build/hil-manual/<board>-restore-<time>/result.json` in the attempt dir. If that close fails too, keep the hold and
report.

A pending-session record clears itself on the next session command once every receipt rechecks done. One holding an
unrecheckable session (its supervisor died) needs a reviewed clear this contract does not give: a blocker for the
human.

## Limits

- **USB enumeration:** the battery checks host-side behaviour of three sketches on the selected boards. It covers no
  other example, class, core or MCU family, and no host mode.
- **Commissioning.** `hil_test.py commission -b <board> ROSTER` finds the DUT's serial under Arduino firmware
  (exactly one device re-enumerating across a flash and a probe reset). It runs from a checkout on the board's host
  and is not staged by `hil_remote.py`. The ci.lan boards' uids come from tinyusb's roster; the battery confirmed the
  Pico's and the Fruit Jam's.
- **RP2040/RP2350:** a probe reset leaves the USB controller running. The harness clears `SIE_CTRL.PULLUP_EN` through
  the probe before each flash so the host sees the new image enumerate.
- **Shared VID:PID:** the library ignores the rp2040 core's board VID/PID (`Adafruit_USBD_Device.cpp`), so every RP2040
  and RP2350 board enumerates as `239a:cafe`; the harness tells boards apart by serial.
- **Metro M4 ELF:** the SAMD51 ELF's first load segment starts at 0x0 (headers and padding). Flashing the ELF, or GDB
  `load`, would write the bootloader region, so the J-Link flash writes only the HEX's runs, which lie in
  [0x4000, 0x80000). Use GDB on metro_m4_express for symbols and state, never `load`.
- **Known faults:**
  - The Fruit Jam's openocd reset-init intermittently times out (`rcp_init`, core0 halted in the bootrom NMI
    handler), failing one flash. A retry passes.
  - On the RP2040 a GDB `continue` prints openocd SMP resume errors (core1 sits in the bootrom). The breakpoint is
    still reached.
  - metro_m4_express's J-Link dropped off ci.lan's bus on 2026-11-01. It needs a human re-plug, and its runs are
    not-run until then.
- **Missing documentation:** the Fruit Jam and Metro M4 board documentation is not in the reference library. The
  rig facts above rest on the RP2040, RP2350 and SAMD51 datasheets and on observed behaviour.
