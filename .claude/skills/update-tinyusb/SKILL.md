---
name: update-tinyusb
description: Sync the vendored TinyUSB core under src/ with hathach/tinyusb at a commit (default master tip), then carry the library through it - wrapper and port fixes, ESP32 core compatibility, build, pre-commit and HIL. Use for "update/bump/sync tinyusb", a new upstream fix the library needs, or when checking that src/ still matches the upstream commit it records.
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
3. **Wrappers and port configs.** Check `src/arduino/` and `src/arduino/ports/*/tusb_config_*.h` against that change:
   renamed or removed API, changed callback signatures (the weak callbacks the wrappers implement), new or removed
   `CFG_*` options and changed defaults. Fixes go in their own commit after the sync commit.
4. **ESP32.** The arduino-esp32 core bundles its own TinyUSB (`arduino_tinyusb`), whose headers come first on its
   include path, so wrapper code and any library source compiled there must build against that version. Check the
   ESP32 builds' `.d` files or include paths and that core's TinyUSB version; the build's `foreignHeaders` covers only
   bundled copies of this library (SAMD, RP2040), not ESP32's. Fix ESP32 breakage in the `ESP32 out-of-sync` section
   of `tusb_config_esp32.h` or in `src/arduino/`; a fix that needs another vendored patch is the human's decision and
   extends the script. List `grep -rn "ESP32 out-of-sync" src` and report sites that look obsolete for the minimum
   ESP32 core `tusb_config_esp32.h` requires; removing them is separate scope.
5. **Headers the RP2040 and SAMD cores bundle**: see CLAUDE.md's Code Rules; report a change forced on them.
6. **No release changes**: no `library.properties` / `library.json` version bump and no `TINYUSB_API_VERSION` change
   come with a sync.
7. **Commit** the staged sync with the JSON's `commitMessage`, then the wrapper fixes. No pre-commit hook may change
   vendored bytes: if one does, restore them, narrow that hook's scope (codespell: an exact line in
   `.codespell/exclude-file.txt` for a genuine upstream typo, `ignore-words.txt` only for a false positive; another
   hook: its exclude), rerun the hooks, then `verify`.
8. **Validate** the committed sync, wrapper fixes included, through CLAUDE.md's Build and Validate and then the HIL
   contract; any later change or rebase needs fresh records.

A sync does not call for an audit of every upstream change.

## When the script refuses

- **Drift**: someone patched vendored code. Find why (`git log -p` on the file) and move the fix into `src/arduino/`,
  the port config or upstream before syncing.
- **ESP32 anchor**: updating the insertion is its own change, with its tests.
- **A write failed**: restore the paths `written` names one by one after looking at them, never with a blanket
  checkout of `src/`.
