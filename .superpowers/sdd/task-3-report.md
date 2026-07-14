# Task 3 Report: Shared Season Runtime

## Scope

Implemented the pure browser `SEASON_RUNTIME` global and embedded it into the generated dashboard exactly once. The runtime provides catalog selection, season-key resolution, pack lookup, guess-key generation, and guarded legacy La Liga guess migration.

La Liga legacy storage is migrated only when the selected season is `2025-26`, from `llg` to `llg_2025-26`. The migration leaves `llg` intact, skips invalid JSON, skips when the qualified target already exists, and never writes a `2026-27` target. PL and WC guess-key semantics remain unchanged.

## TDD Evidence

1. Added `tests/test_season_runtime.js` before the runtime implementation.
2. Ran `node tests/test_season_runtime.js` in the RED state.
3. Observed the expected missing-production-file failure:
   `ENOENT: no such file or directory, open ...\\website\\season_runtime.js`.
4. Added the runtime, template marker replacement, and La Liga integration.
5. Re-ran the Node test successfully: `season runtime tests passed`.

## Verification

- `node tests/test_season_runtime.js`: PASS.
- `python -m unittest tests.test_publish_contract tests.test_laliga_seasons tests.test_learning_integration -v`: PASS, 86 tests.
- `git diff --check`: PASS, with only Git's normal LF-to-CRLF working-copy warnings on existing edited text files.

The publication contract verifies one runtime marker, replacement in the builder, no unresolved marker after generation, one global runtime definition, and runtime placement before `function init()`.

## Result

Task 3 implementation is complete and ready in the requested worktree.
