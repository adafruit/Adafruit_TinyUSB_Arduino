---
name: update-tinyusb
description: Sync the vendored TinyUSB core under src/ with hathach/tinyusb at a commit (default master tip) as a vendor-only change, check the library and cores against it - wrapper and core behaviour, ESP32 core compatibility, build, pre-commit and HIL - and route what it breaks or reviewers find to its owner. Use for "update/bump/sync tinyusb", a new upstream fix the library needs, or when checking that src/ still matches the upstream commit it records.
---

# Update TinyUSB

`scripts/update_tinyusb.py` owns the mechanics and `upstream.json` beside this page their record; the script's
docstring has what it guarantees, its JSON and exit codes, and its refusals name their cause.

```bash
U=.claude/skills/update-tinyusb/scripts/update_tinyusb.py
python3 $U plan [--ref REF] [--skip P ...] [--include P ...]
python3 $U apply --ref <full SHA from plan> [--skip P ...] [--include P ...]
python3 $U verify
```

## Steps

1. Work in a task worktree per the repo's rules, on a clean tree. Run `plan`; read `upstreamLog`, `newUpstream` and
   `newOutsideVendored`.
2. **New upstream files** are included by default; `--skip` needs a concrete reason, and the human decides when
   unsure. `newOutsideVendored` is a review hint, not a verdict: often a lone file of a driver the library omits
   (`portable/chipidea/...` beside no vendored chipidea code), but also a new class or a new subdirectory of a
   vendored component. `--include` takes back a file the library omitted when vendored code now needs it. Run `apply`
   with the same flags, then stage every path in `written` (created, modified, deleted, `upstream.json` included) and
   review `git diff --cached`: that is the whole upstream change to the vendored tree, new files included.
3. **Wrappers, port configs and cores.** Check `src/arduino/` and `src/arduino/ports/*/tusb_config_*.h` against that
   change: renamed or removed API, changed callback signatures (the weak callbacks the wrappers implement), new or
   removed `CFG_*` options and changed defaults. Beyond what compiles, follow the upstream diff to the code the wrappers
   and cores call and check the behaviour they rely on: how a transfer or command completes on failure and what recovery
   it now requires; locking, blocking and re-entrancy across the loop, task and IRQ contexts each core runs the stack
   from, read in the installed core's source, not only the headers it bundles. What this finds goes to its owner
   (Findings on vendored code), never into the sync.
4. **ESP32.** The arduino-esp32 core bundles its own TinyUSB (`arduino_tinyusb`), whose headers come first on its
   include path, so wrapper code and any library source compiled there must build against that version. Check the
   ESP32 builds' `.d` files or include paths and that core's TinyUSB version; the build's `foreignHeaders` covers only
   bundled copies of this library (SAMD, RP2040), not ESP32's. ESP32 breakage is library work outside the
   sync (step 7), in the `ESP32 out-of-sync` section of `tusb_config_esp32.h` or in `src/arduino/`; one that needs
   vendored code changed goes upstream. List `grep -rn "ESP32 out-of-sync" src` and report sites that look obsolete
   for the minimum ESP32 core `tusb_config_esp32.h` requires; removing them is separate scope.
5. **Headers the RP2040 and SAMD cores bundle**: see AGENTS.md's Code Rules; report a change forced on them.
6. **No release changes**: no `library.properties` / `library.json` version bump and no `TINYUSB_API_VERSION` change
   come with a sync.
7. **Commit** the staged sync with the JSON's `commitMessage`. The sync PR holds only that commit, and a drift restore
   before it (When the script refuses): upstream's bytes, the ESP32 insertion, `upstream.json` and any hook exclusion
   the vendored bytes need (below). A wrapper, port-config or core change the sync needs, even one the build needs to
   compile, is separate work: report it with its owner rather than add it, since the sync is not ready without it. No
   pre-commit hook may change vendored bytes: if one does, restore them, narrow that hook's scope (codespell: an exact
   line in `.codespell/exclude-file.txt` for a genuine upstream typo, `ignore-words.txt` only for a false positive;
   another hook: its exclude), rerun the hooks, then `verify`.
8. **Validate** the committed sync through AGENTS.md's Build and Validate and then the HIL contract; any later change or
   rebase needs fresh records. HIL's whole outcome counts, each board's restore included. Before calling a failure
   rig-side, run the PR's firmware and base-equivalent firmware, built from the same inputs, on the same boards under
   the HIL contract and its retry rules: a failure only the sync's firmware reproduces is a sync regression, recorded
   apart from any explanation not yet shown on hardware; a comparison that cannot tell them apart is reported
   inconclusive. A confirmed regression blocks calling the sync ready, its upstream follow-up filed or not.

A sync does not call for an audit of every upstream change.

## Findings on vendored code

Vendored code is never fixed here: a fix that changes it lands in hathach/tinyusb and comes back with a later sync. For
each finding on a sync, from review bots, HIL or your own review:

- **Assess it from evidence** against the pinned upstream commit, independent of a reviewer's or `pr-babysit`'s verdict;
  reuse a verified assessment for code that has not changed. Many or uncertain findings get a `finding-verifier` per
  area, and a hardware or protocol fact goes through `read-doc`. Note the introducing commit when established
  (`git log <base>..<target> -- <path>`), new or pre-existing; a confirmed finding does not wait on it.
- **Confirmed**, with a concrete trigger: a hathach/tinyusb follow-up through agentrc's `followup-issue` skill, which
  searches first: an open issue covering it takes a comment with the new evidence, or nothing; otherwise a new issue
  labelled `follow-up`, plus the `Port ...` label of a port-specific defect, linking the sync PR. Publishing needs a
  grant covering hathach/tinyusb; without one, give the human the issue-ready text.
- **Plausible or refuted**: an explicit disposition, posted only where the task may publish; a plausible finding is
  never answered as refuted.
- **Owner from the code path**, not from where the symptom showed: wrapper, port and core-glue defects are this
  library's (label `follow-up`), an Arduino core's go to that core.

## When the script refuses

- **Drift**: someone patched vendored code. Find why (`git log -p` on the file). When the target upstream commit
  supersedes the patch or keeps its purpose, restore the recorded upstream bytes in a commit of its own before `plan`;
  otherwise move the fix into `src/arduino/`, the port config or upstream before syncing.
- **ESP32 anchor**: updating the insertion is its own change, with its tests.
- **A write failed**: restore the paths `written` names one by one after looking at them, never with a blanket
  checkout of `src/`.
