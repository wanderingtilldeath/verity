---
description: Automatically sync FEATURES.md with recent code changes, new features, schema updates, or refactors
argument-hint: "[optional feature name or change summary]"
---

Inspect recent repository changes and update the living `FEATURES.md` ledger.

### Parameters
- Hint / Feature Name: $ARGUMENTS (if provided, match against this feature area)

---

### Protocol

1. **Detect Changes**:
   - Run `git status --short` and `git diff HEAD` (and `git diff --staged` if applicable) to identify modified, added, or deleted files. Outside of git, check files modified during the current session.
   - Ignore changes with no behavioral or architectural impact (pure formatting, comment updates, typo fixes, or minor test updates).

2. **Open `FEATURES.md`**:
   - Check the project root for `FEATURES.md`. If it does not exist, create it with a top-level heading: `# Features & Architecture Ledger`.

3. **Match & Update**:
   - Check if the modified files belong to an existing subsystem in `FEATURES.md`.
   - **If modifying an existing feature**: Update the entry in place, mark `Status: Modified`, refresh the file paths/summaries, and bump `Last Updated` to the current date and commit hash.
   - **If introducing a new capability**: Append a new entry at the bottom with `Status: Active`.
   - **If deprecating/removing code**: Mark `Status: Deprecated` and note what replaces it (never delete historical entries).

4. **Entry Template**:
```markdown
### [Feature / Subsystem Name]
- **Status**: Active | Modified | Deprecated
- **Last Updated**: YYYY-MM-DD (short commit hash)
- **Entry Points / Key Files**:
  - `path/to/entry.ext`: router or handler
  - `path/to/service.ext`: business logic
  - `path/to/schema.ext`: data model
- **Implementation Summary**: Plain-language description of how it works internally (2 to 5 sentences).
- **Data & Schema Dependencies**: Tables, collections, external APIs, queues, or caches touched.
- **Production Trade-Offs**: Edge cases, locking/idempotency behavior, caching rules, scaling limits, or known gaps.
```
