# Adafruit TinyUSB Arduino: Agent Instructions

Read [CLAUDE.md](CLAUDE.md) first: it has this repository's agent instructions. Its skills are in
[.claude/skills/](.claude/skills/).

This file is a regular file, not a symlink to CLAUDE.md, and there is no `.agents` link to `.claude`: the Arduino
Library Manager rejects a release that contains any symlink (arduino-lint rule LS005), so a release with them never
reaches the Library Manager. The `no-symlinks` pre-commit hook checks this.
