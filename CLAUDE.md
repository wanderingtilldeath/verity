# CLAUDE.md

## Slash commands

Project commands live in `.claude/commands/`.

- `/onboard [focus area]` — `.claude/commands/onboard.md`. Map the codebase (stack, architecture, schema, request trace, system design interview prep) and bootstrap `FEATURES.md`.
- `/ledger-update [feature or change summary]` — `.claude/commands/ledger-update.md`. Sync `FEATURES.md` with recent code changes.

## Feature ledger

`FEATURES.md` in the project root is the living ledger of subsystems. After any change with behavioral or architectural impact (new feature, schema change, refactor, removal), run `/ledger-update`. Never delete historical entries; mark them `Deprecated`.
