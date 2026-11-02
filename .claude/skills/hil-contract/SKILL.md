---
name: hil-contract
description: The Adafruit TinyUSB Arduino HIL contract, under agentrc's `hil` rules. Use when running the Arduino HIL battery on rig boards (Raspberry Pi Pico, Pico W, Pico 2, Adafruit Fruit Jam, Metro M4, ESP32-S3 DevKitM, ESP32-P4 Function EV), when a HIL run fails, hangs or reports a board locked, when reading its report, or for a manual session (flash, GDB, console, a command) on a rig board from hw-validator or hw-debugger.
---

# HIL contract

Read agentrc's `hil` skill first; this page supplies its contract sections. The mechanics are agentrc's `arduino`
skill (`arduino_hil.py`, `arduino_remote.py`), shared with Adafruit SPIFlash; the battery, the host step and the roster
checks are this library's (`hil_test.py`). Manual runs only: no CI job runs this battery. Scripts, all in `scripts/`:

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
- `uid`: the DUT's USB serial under Arduino firmware (Limits, commissioning); for a host board or a `no_serial`
  board, the board's tinyusb farm id, unused here.
- `usb_port`: the sysfs bus port the DUT enumerates at; the DUT must be there. Required for esptool boards.
- `no_serial`: `true` for firmware with an empty serial string (ESP32 under Arduino, Limits): the harness then tells
  the DUT by `usb_port` alone, as the serial-less device there.
- `flasher`: `name` `openocd`, `jlink` or `esptool`, `uid` the probe serial (esptool: its USB-UART bridge's), `args`,
  for openocd `vid_pid` and `verify`.
- `debug.openocd`: the openocd config the GDB session uses; with `debug.tool` `openocd-esp32`, exactly
  `-f board/<chip>-builtin.cfg`, `debug.uid` the chip's USB-Serial/JTAG serial and `debug.usb_port` its bus-port, no
  `vid_pid`. The harness checks that serial at that port and passes the port as openocd's `adapter usb location`;
  agentrc's whole-bus lookup refuses while a serial-less 303a:1001 (the S3's Arduino DUT) is on the host. No `debug`:
  no GDB route.
- `arduino.tests`: the tests the battery runs, all of the board's role (device or host, the build contract's board
  table); a host board's `arduino.dev_attached` lists the devices on its USB port, each a `vid_pid` `vvvv:pppp` and
  its `serial`.

ci.lan's boards are also on tinyusb's roster (tinyusb `test/hil/tinyusb.json`) and tinyuf2's. This contract performs
no rig-wide operation (hub power, port cycle, controller rebind).

**Farm check.** There is no launcher of its own: these boards are tinyusb farm boards, so use tinyusb's:
`ssh ci.lan 'cd ~/code/tinyusb && python3 test/hil/helper/hil_farm_check.py -b <board>...'` with the roster's board
names.

## Selection

A fixed battery per board, the roster's `arduino.tests`. Device boards (USB to this host):

- `cdc_echo`: `serial_echo` echoes random payloads upper-cased.
- `hid_inout`: `hid_generic_inout` echoes a 64-byte report over hidraw.
- `msc_ramdisk`: `msc_ramdisk`'s 8 KiB disk reads back with its FAT12 `README.TXT`.

Host boards (Pico W, Pico 2: devices wired to their USB port):

- `host_device_info`: `examples/Host/Simple/host_device_info` on the native host reports every `dev_attached` device,
  VID:PID and serial, on the console (the probe's VCOM, Serial1), opened before the flash so the boot's enumeration
  is kept. Descriptor-level coverage: no transfer to the devices.

`-t` narrows the tests (each board runs those of its role) and `-b` the boards. The PR-scoped selector is the build
contract's:

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

A record carries the image its flasher writes: the ELF for openocd, the HEX for J-Link, and for esptool the ESP
core's `flash_args` with every image it names (`boot_app0.bin` snapshotted from the core) at its offset. ESP32 rig
builds compile `test/hil/esp32_ts3usb30.cpp` into each sketch: its `initVariant()` switches the farm's USB mux
(TS3USB30, GPIO47 high) to this host. The battery and the restore take clean records only. A manual flash or GDB
session also takes an experimental record (`--experimental`, same HEAD, a dirty tree allowed, its diff saved beside
it).

## Execution

Budgets: a flash 120 s, a verify or reset session 60 s, re-enumeration 20 s, a test 30 s, a console capture 120 s,
a board 900 s. A test
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
python3 $H/hil_remote.py session exec <id> console [--seconds S] [--reset]   # the board's console to a log
python3 $H/hil_remote.py session exec <id> check --bundle B --build-record R [--test T]   # a device test, no flash
# the clean record session open staged: --bundle bundles/restore --build-record build/hil/<board>.build.json
python3 $H/hil_remote.py session exec <id> exec [--timeout S] -- <argv>   # a command on the rig, in the tree
python3 $H/hil_remote.py session close <id>                         # restore the board's sketch, then release
```

- `gdb` runs `gdb-multiarch -batch` over `target extended-remote | openocd ... -c "gdb port pipe"`, with the roster's
  `debug.openocd` pinned to the board's probe. The attach halts the target; it resumes when the gdb connection closes,
  at gdb's exit, not at `detach` (in extended-remote gdb stays connected), so an `--ex` after `detach` still sees it
  halted: check the firmware after the session. The session's end runs `resume` (on the RP2040 after polling both
  cores, or core1 stays halted). Breakpoints are hardware ones, and there is no flash `load`: reflash with `flash`. The
  ESP32-P4 goes through `openocd-esp32` on its USB-Serial/JTAG, built by agentrc's `hil_openocd_esp` checks, with
  `ESP_FLASH_SIZE 0` so the attach does not reset a memory-protected app; the ESP32-S3 has no GDB route (Limits).
- `console` captures the board's console for `--seconds` into `console.log` (its tail in the JSON), with
  `console.times` holding one JSON line per chunk (`t`, its capture time after the open, not the wire time) and the
  reset's mark: the CMSIS-DAP probe's VCOM (the target's UART0), or an esptool board's USB-UART bridge (UART0: the ESP
  boot log). `--reset` resets with the console open: through the probe, or on an esptool board esptool's hard reset
  on the open bridge's RTS/DTR lines (EN low 100 ms), so the ROM banner is kept. A serial error ends the capture
  with what was read and fails the command. Never run it while a flash holds the bridge: one session command at a time
  sees to that.
- `check` runs a device test (`--test`, default `cdc_echo`) against the firmware as it runs, without flashing: the one
  DUT enumerated with the record's VID:PID for that test's sketch. Use it after a GDB session or a reset; it shows the
  function works, not which image is flashed nor every core's state (a halted RP2040 core1 leaves Arduino's
  single-core firmware echoing). On an RP2040 or RP2350 board a reset you run yourself (`exec`, a gdb `monitor
  reset`) without the harness's detach leaves the host holding a dead enumeration, so `check` then fails while the
  firmware runs; `flash`, `restore` and `console --reset` detach first. Host boards refuse it: read their console.
- `flash` and `check` name the DUT they chose by bus-port and devnum; `flash` adds `after`, the seconds from the flash's
  end to a stable enumeration (the 1 s hold included).
- A refused `session open` (the board is held) prints the holder and leaves its attempt dir unused. Within the
  lock-wait budget, retry `session open` with a new run id; never wait on a recovery blocker.
- Each command and child is bounded (`exec`/`gdb` `--timeout` at most 600 s) and runs through agentrc's
  `run_tracked`.
- One session command runs at a time per tree and board. It ignores SIGHUP; SIGTERM ends it as SIGINT does, its
  child ended or recorded pending.
- Each command's JSON is the last stdout line and `result.json` in its `build/hil-manual/<board>-<cmd>-<time>/`;
  `gdb` and `exec` keep the last 4000 characters of the child's output there, the whole of it in that dir's `hil.log`.
- On a dev PC the same commands are `hil_test.py flash|gdb|console|check|exec|restore -b B --under-hold <reason> ...
  ROSTER`,
  under
  `hil_test.py lock hold B --reason <reason>`.

## Reservation

The lock helper is `hil_test.py lock (hold|release|status) ...`, agentrc's `hil_lock.py` at the pin, with lock dir
`/tmp/tinyusb-hil-locks`.

- `hil_test.py run` and `commission` lock each board themselves (protected, reason `hil_test.py`, `hil_test.py
  commission`): never pre-hold for them. SIGTERM aborts them as SIGINT does: a board they touched stays held for
  recovery (exit 4), its children ended or recorded pending in the run's report or the commission's `result.json`.
- A manual session holds the board under `arduino-hil session <id>`. Every session command checks that hold, and
  refuses while the board's pending-session record (`arduino-hil`) lists a child not proven ended.
- Cleanup state is the board's restoration sketch flashed and checked (device boards: `serial_echo` echoing; host
  boards: `host_device_info` reporting every attached device), every child proven ended, and the hold released.
- The battery restores it after each board. `session close` restores, checks and releases in one command, and
  releases nothing when the restore fails.

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
  - pass: every test passed and the restoration sketch was restored;
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

When the failed tree's own harness is the defect, run the restore from a newly staged tree of the fixed commit (a
`hil_remote.py run` of the held boards stages one and reports them locked), with `--report` naming the failed run's
report by absolute path; the hold, its token and the receipts stay the failed run's.

1. `hil_test.py lock status`, and `hil_test.py report <run dir> -b <board>` for the row's holder token and receipts;
   for a commission, read them from its `result.json` instead, since it writes no `hil_report.json`.
2. Take the board for the restore, keeping it blocked throughout:
   - A live cleanup holder (its record's reason is `hil_test.py`): restore under it, reason R = `hil_test.py`.
   - A dead holder or a protected record: `hil_test.py lock hold --take-over <board> --reason recovery-<id>` prints
     token T; R = `recovery-<id>`.
3. `hil_test.py restore -b <board> --build-record build/hil/<board>.build.json --under-hold R --report
   <run dir>/hil_report.json <roster>` (`--report <commission dir>/result.json` for a commission). It refuses while a
   child of the failed run is not proven ended: stop and report that receipt, since ending a child is outside this
   contract.
4. Once the restore passed, `hil_test.py lock release --token <the holder's token, or T>`; that release refuses too
   while a receipt is open. On failure, keep the hold and report.

A host board whose restore flashed and verified yet misses a `dev_attached` device keeps its hold like any failed
restore. The Pico 2's Kingston stick once stopped answering the data stage of its first GET_DESCRIPTOR across MCU
resets and reflashes (2026-11-02); nothing in this contract power-cycles a device on a host board's port, so report
that as the blocker for the human rather than retrying.

A failed `session close`: the session keeps its hold (`arduino-hil session <id>`). Once the session's pending
children are proven ended (the next session command clears the record by itself), run `hil_remote.py session close
<id>` again. It restores from the session's own `bundles/restore/` record, and its JSON is the last stdout line and
`build/hil-manual/<board>-restore-<time>/result.json` in the attempt dir. If that close fails too, keep the hold and
report.

A pending-session record clears itself on the next session command once every receipt rechecks done. One holding an
unrecheckable session (its supervisor died) needs a reviewed clear this contract does not give: a blocker for the
human.

## Limits

- **Coverage:** the battery checks three device sketches from this host's side and one host sketch's descriptor
  report on the selected boards. It covers no other example, class, core or MCU family, no host-mode transfer, and no
  ESP32 host mode (the Arduino ESP32 config's host is MAX3421). A host board's check covers only its `dev_attached`
  devices, which need a serial: the CH340 (1a86:7523, no iSerial) behind the Pico 2's hub is not checked.
- **Host latency:** under concurrent sessions ci.lan has taken 0.4-16 s to open a DUT's CDC tty, before any request
  reached the device (usbmon, 2026-11-02). The open is not bounded by the test deadline; allow for it in any host-side
  timing claim.
- **Commissioning.** `hil_test.py commission -b <board> ROSTER` finds the DUT's serial under Arduino firmware
  (exactly one device re-enumerating across a flash and a probe reset, at the roster's `usb_port` when it has one), or
  for a `no_serial` board its port, then checks the restoration sketch with `cdc_echo`. It runs
  from a checkout on the board's host and is not staged by `hil_remote.py`; host boards have nothing to commission.
  The ci.lan boards' uids come from tinyusb's roster; the battery confirmed the Pico's and the Fruit Jam's.
- **ESP32 identity:** under Arduino the ESP32-S3 and ESP32-P4 enumerate with an empty serial string (the library's ESP32
  `TinyUSB_Port_GetSerialNumber` returns none), so the roster marks each `no_serial` and pins it by `usb_port` (S3
  11-1.3, P4 11-1.1 on ci.lan):
  the DUT candidate is the device there with no cached sysfs `serial`, since while esptool runs the S3's ROM
  USB-Serial/JTAG appears at the same port as `303a:1001` with its MAC as serial; a failed serial read would also leave
  none, so the test run on the candidate is the evidence. The S3 enumerates as `303a:1001`, its core variant's
  `USB_VID`/`USB_PID`; the P4 as `239a:cafe`.
- **ESP32-S3 GDB:** its OTG controller takes the PHY its USB-Serial/JTAG uses, and the farm wires no pad JTAG: no GDB
  under Arduino. The P4's USB-Serial/JTAG has its own port (60:55:F9:F9:87:15 at 11-1.2), so its GDB works.
- **RP2040/RP2350:** a probe reset resets only the cores (RP2040 datasheet 2.4.2.9) and leaves the USB controller
  attached; the firmware's `dcd_init` then resets it without a disconnect the host sees, so the host keeps a dead
  enumeration. The harness clears `SIE_CTRL.PULLUP_EN` through the probe before each flash and console reset so the
  host sees the new image enumerate.
- **Shared hub 11-1 on ci.lan:** the S3 (11-1.3) and the P4's OTG (11-1.1) and USB-Serial/JTAG (11-1.2) share a hub
  whose port events Linux handles one port at a time. Each esptool connect to the P4 brings its ROM download device
  up at full speed on 11-1.1, where it never enumerates; such a failing enumeration once held the hub for 32 s and a
  concurrent S3 restore missed its 20 s window (its retried `session close` passed). Run S3 and P4 sessions one after
  the other.
- **Shared VID:PID:** the library ignores the rp2040 core's board VID/PID (`Adafruit_USBD_Device.cpp`), so every RP2040
  and RP2350 board enumerates as `239a:cafe`; the harness tells boards apart by serial.
- **Metro M4 ELF:** the SAMD51 ELF's first load segment starts at 0x0 (headers and padding). Flashing the ELF, or GDB
  `load`, would write the bootloader region, so the J-Link flash writes only the HEX's runs, which lie in
  [0x4000, 0x80000). Use GDB on metro_m4_express for symbols and state, never `load`.
- **Known faults:**
  - The Fruit Jam's openocd reset-init intermittently times out (`rcp_init`, core0 halted in the bootrom NMI
    handler), failing one flash; a retry passes (2 of 36 battery flashes). Cause, supported on the rig (2026-11-02):
    `rp2350.cfg` warm-resets core0 before core1 without halting core1, whose bootrom wait loop can then take an RCP
    fault that is mirrored to core0 (RP2350 datasheet 3.6.3.3-3.6.3.4) and stalls `rcp_init` until the next session's
    reset.
  - A CMSIS-DAP flash on ci.lan once failed with `CMSIS-DAP command mismatch` (Pico W) and left core0 halted in the
    flash stub. A retry passed; reflash before judging the firmware after a failed flash.
  - On the RP2040, and on the RP2350 (Fruit Jam, 1 of 3), a GDB `continue` prints openocd SMP resume errors (core1
    sits in the bootrom). The breakpoint is still reached.
- **Missing documentation:** the Fruit Jam, Metro M4 and the farm's USB mux fixture are not in the reference library.
  The rig facts above rest on the RP2040, RP2350 and SAMD51 datasheets, the TS3USB30 datasheet (S high selects D2),
  tinyusb's ESP32 board files (GPIO47) and observed behaviour.
