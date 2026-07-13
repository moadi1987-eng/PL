# La Liga Season Archive Design

## Purpose

Give La Liga the same season-selection behavior as Premier League: the current season opens by default, the previous season remains available as a read-only archive, and no historical guesses or learning evidence are lost.

The first supported pair is:

- `2026-27`: current season, live and editable before kickoff;
- `2025-26`: completed archive, read-only.

## Current Problem

The build currently derives one La Liga date range from the current month and embeds it as `EMBEDDED_LL`. In July 2026 that range is `2026-27`, so the `2025-26` fixtures are absent from the generated site.

The browser also renders the season selector only when `D.league === "pl"`. La Liga therefore cannot select another season even if historical data is available.

Eight committed La Liga guesses from matchday 29, saved in March 2026, still exist in `user_guesses_laliga.json`. They belong to `2025-26`, but the current storage and rendering keys do not identify a season.

## Scope

### In Scope

- Fetch and package La Liga `2025-26` and `2026-27` during the static build.
- Reuse one season selector for PL and La Liga.
- Open `2026-27` by default and retain the selected La Liga season in session storage.
- Treat `2025-26` as a read-only archive.
- Preserve and display the eight existing La Liga guesses under `2025-26`.
- Qualify browser guess keys, prediction identity, history filtering, and live overlays by season.
- Keep current-season live updates and automatic pre-match guess filling working.
- Add automated migration, UI-runtime, build-contract, and regression coverage.

### Out Of Scope

- Adding seasons older than `2025-26`.
- Editing archived guesses or retroactively creating new archived guesses.
- Changing prediction mathematics, promotion rules, or scoring rules.
- Adding a new external data provider.
- Reconstructing missing player or squad data.

## Selected Approach

Use the established Premier League season-package pattern. The build emits one La Liga season catalog containing metadata and normalized data for each season. The browser uses a competition-neutral selector adapter to choose PL or La Liga season data.

This is preferred over on-demand browser fetching because the static page remains deterministic, works without cross-origin API access, and can be fully tested before publication. It is preferred over a one-off static archive because the same build pipeline can refresh or validate both seasons consistently.

## Build Data Model

Add a La Liga catalog parallel to `EMBEDDED_PL_SEASONS`:

```json
{
  "current": "2026-27",
  "items": [
    {"key": "2026-27", "label": "2026/27"},
    {"key": "2025-26", "label": "2025/26"}
  ],
  "data": {
    "2026-27": {"teams": {}, "gws": [], "fix": [], "season": "2026-27", "archive": false},
    "2025-26": {"teams": {}, "gws": [], "fix": [], "season": "2025-26", "archive": true}
  }
}
```

Each fixture keeps the existing normalized shape and must include the explicit season. Match IDs remain source IDs; season-qualified lifecycle keys remain the canonical identity for learning and persistence.

The build fetches fixed date ranges for both supported seasons. Both seasons must have valid teams and fixtures. If either required package cannot be built, the build aborts before generated files are written, so a temporary source failure cannot replace the previously committed site with an empty or partial archive.

## Browser State And Selection

Generalize the existing PL-only season helpers into competition-aware helpers:

- PL reads from the PL season catalog and stores `plSeason`.
- La Liga reads from the La Liga season catalog and stores `llSeason`.
- WC has no season selector.
- The selector is visible only when the active competition has at least two valid season items.
- Switching competition restores that competition's most recent valid season.
- Switching season resets the selected round/date, loads the correct teams, rounds, fixtures, archive flag, guesses, and AI rows, then renders without reloading the page.

The current season remains the default when no valid saved selection exists.

## Archive Behavior

When `archive` is true:

- results, standings, comparison, saved guesses, and AI analysis remain visible;
- guess inputs and automatic fill controls are disabled;
- no local or remote live overlay mutates archived fixtures;
- the selected archived round remains navigable;
- refresh may update current-season data but must leave the archive unchanged.

The archive rule is data-driven rather than hard-coded to a specific season string.

## Guess Migration And Storage

Season-qualify La Liga browser guess keys:

- current format: `llg_<season>`;
- legacy format: `llg`.

On first read, legacy `llg` data is copied to `llg_2025-26` only when the season-qualified key is absent. The legacy source is not deleted during migration.

The committed `user_guesses_laliga.json` packet is embedded under `2025-26`, because its saved timestamp and match IDs belong to that season. It must not appear in `2026-27`.

Automatic one-hour random filling runs only for a non-archived selected season and writes only to that season's key.

## Live Updates

Extend `live.json` with a La Liga season map parallel to `pl_seasons`, while retaining `fix_ll` as the current-season compatibility field during migration.

The browser chooses the live fixture set for the selected La Liga season. Archived seasons reject live replacement even if a response contains matching IDs. The current season continues to receive score, status, minute, and date updates.

## Learning And AI Analysis

The persistent La Liga prediction and model files remain competition-specific. Match lifecycle identity already includes league, season, and source fixture ID; the season catalog must pass the explicit selected season through every normalized fixture.

The AI page filters `learning_history.laliga.gw_results` by the selected La Liga season, just as PL is filtered by its selected season. Historical rows remain visible when the archive is selected. Current-season rows start empty and grow only from verified post-match lifecycle evidence.

There is currently no verified La Liga AI lifecycle history for `2025-26`. Until genuine stored rows exist, the archived AI view states that no verified history is available. It must not synthesize predictions, accuracy, or a trend from user guesses or final scores.

Archived user guesses are presentation evidence only. They do not train the model and cannot enter active-versus-candidate promotion cohorts.

## Error Handling

- Reject malformed season catalogs, duplicate season keys, invalid fixture seasons, and empty current-season packages during build validation.
- Abort before generated writes when either required La Liga season cannot be validated, leaving the committed site unchanged.
- Fall back to the catalog current season when a saved browser selection is missing or invalid.
- Never borrow PL teams, weights, guesses, or fixtures when La Liga data is unavailable.
- Do not publish a partial generated state when catalog, history, model, or page generation fails.

## Testing

Add focused tests for:

- fetching and packaging both La Liga seasons;
- ordering and labeling the season catalog;
- defaulting to `2026-27` and restoring `llSeason`;
- showing the selector for LL and hiding it for WC;
- switching LL seasons without mixing teams, fixtures, rounds, or guesses;
- migrating legacy `llg` to `llg_2025-26` without deletion;
- embedding the eight committed guesses only in `2025-26`;
- disabling guess mutation and live overlays for archives;
- preserving current-season live updates;
- filtering AI rows by selected La Liga season;
- keeping season-qualified learning identities unique;
- failing closed on malformed or partial catalog state.

Run the full Python and JavaScript suites, a controlled no-publish build, JSON and inline-script validation, and Playwright checks for PL and LL season switching on desktop and mobile before publication.

## Publication And Success Criteria

The existing atomic publication flow remains unchanged. One successful build produces at most one generated commit containing the season catalogs, state files, live payload, and byte-identical HTML outputs.

The feature is complete when:

1. LL opens on `2026/27` by default.
2. The selector offers `2026/27` and `2025/26`.
3. Selecting `2025/26` shows its complete results, standings, eight saved guesses, and season-specific AI view.
4. The archived season cannot be edited or changed by live refresh.
5. Returning to `2026/27` restores current fixtures and live behavior.
6. PL and WC behavior is unchanged.
7. All automated, generated-state, and visual checks pass before direct publication to `main`.
