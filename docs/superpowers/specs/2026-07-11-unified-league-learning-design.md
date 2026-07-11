# Unified League Learning Design

## Purpose

Build one reliable learning pipeline for Premier League, La Liga, and World Cup predictions while keeping each competition's data, weights, calibration, and promotion decisions independent.

The primary optimization target is total game points. A candidate model may become active only when it earns more points than the active model without reducing winner-direction accuracy.

## Current State

- World Cup has locked pre-match snapshots, one-time post-match training, dedicated weights, and a useful v4 comparison.
- Premier League evaluates stored gameweek predictions and updates shared league weights, but it has no consistent 2026/27 per-match snapshot pipeline.
- La Liga can evaluate a prediction file, but no prediction file or dedicated weights currently exist. Its browser predictions therefore use Premier League weights.
- The browser already supports a baseline-versus-v4 scoreline choice, but model state and promotion metadata are not represented consistently across competitions.
- One-hour automatic user-guess filling is random by explicit product choice and is separate from AI learning.

## Scope

### In Scope

- A shared learning engine with competition-specific configuration.
- Dedicated prediction and weight state for PL, La Liga, and WC.
- Immutable pre-match snapshots created within 36 hours of kickoff.
- One-time post-match evaluation and training.
- Active-versus-candidate comparison and automatic promotion.
- Competition-aware points, exact-score, winner, calibration, and error metrics.
- Migration that preserves all existing PL and WC history.
- Clear model status on the AI analysis page.
- Automated unit and integration tests.
- GitHub Actions persistence for every learning state file.

### Out of Scope

- Replacing random one-hour user-guess auto-fill with AI.
- A paid external odds or prediction service.
- Inventing missing injury, lineup, or squad data.
- Rebuilding the Flask desktop application prediction stack.

## Architecture

Create `website/league_learning.py` as the single state and evaluation engine. Competition adapters provide normalized fixtures, teams, scoring rules, and feature snapshots. `website/update_pl_mobile.py` remains responsible for fetching source data, building normalized competition payloads, invoking the engine, and embedding the resulting state in the static site.

`website/ml_engine.py` becomes a compatibility layer for legacy PL history during migration. New learning behavior lives in the shared engine so changes cannot diverge between PL and La Liga. The existing WC prediction mathematics may initially remain in `update_pl_mobile.py`, but snapshot lifecycle, evaluation, promotion, and persistence use the shared interfaces.

## State Files

Each competition owns two files:

| Competition | Predictions | Model state |
| --- | --- | --- |
| PL | `ai_predictions.json` | `ai_weights.json` |
| La Liga | `ai_predictions_laliga.json` | `ai_weights_laliga.json` |
| WC | `ai_predictions_wc.json` | `ai_weights_wc.json` |

Prediction stores use a versioned object with a `matches` map keyed by stable match ID. Each match row contains:

- league, season, round/day, teams, and kickoff time;
- snapshot creation time and model version;
- feature values and explicit missing-data flags;
- active and candidate probabilities and scorelines;
- competition scoring rule used for evaluation;
- checked/trained flags and timestamps;
- actual result and evaluation metrics after full time.

Model state contains active and candidate versions, factor weights, calibration, training metadata, promotion history, and the most recent comparison summary.

## Snapshot Lifecycle

1. Ignore finished, started, placeholder, or invalid fixtures.
2. Create the first snapshot when a real fixture enters the 36-hour window before kickoff.
3. Never rewrite a locked snapshot because team form, probabilities, or model code changed later.
4. Record missing inputs rather than fabricating values. Model defaults may be used only when the snapshot marks the input as missing.
5. After a reliable full-time result arrives, evaluate the stored active and candidate picks against that result.
6. Train from the match once, then set `model_trained` so later builds cannot count it again.
7. Keep the snapshot permanently for audit and historical charts.

## Features

The normalized feature set supports:

- recent form;
- team strength and attack/defence ratings;
- table or group position;
- home advantage, disabled for neutral matches;
- streak and goals trend;
- home/away split;
- head-to-head where pre-match history exists;
- clean-sheet and draw tendencies;
- upset risk;
- squad availability when a trusted source provides it.

No competition may borrow another competition's learned weights. Missing player availability is neutral and explicitly reported as unavailable.

## Scoring And Metrics

PL and La Liga use the site's existing 3-point correct-direction and 5-point exact-score rules. WC uses the configured phase rules for group stage, round of 32, round of 16, quarter-final, semi-final, third-place match, and final.

For every model and cohort, record:

- total points;
- winner-direction accuracy;
- exact-score accuracy;
- goal mean absolute error;
- Brier score for outcome probabilities;
- draw-pick rate and scoreline concentration;
- sample size and data-completeness rate.

## Promotion Policy

A candidate is eligible only after 30 completed locked predictions in that competition. It is promoted when all conditions hold:

1. Candidate total points are greater than active-model total points on the same cohort.
2. Candidate winner-direction accuracy is greater than or equal to active-model accuracy.
3. Candidate state is valid and every evaluated row has a locked pre-match snapshot.

Exact-score accuracy is reported but does not override the winner-accuracy guard. A failed candidate remains in shadow mode and continues collecting evidence. Promotion records the previous version and metrics so the change is auditable and reversible.

Based on current evidence, WC v4 remains active, PL keeps its current baseline, and La Liga stays in collecting mode until it reaches 30 completed snapshots.

## UI Design

The AI page shows competition-specific status without changing the primary navigation:

- active model and candidate model;
- sample count and last training time;
- points, winner accuracy, and exact-score accuracy for both;
- metric deltas and promotion/collecting status;
- data-completeness warning when trusted inputs were unavailable.

The existing daily accuracy graph remains. It uses only locked predictions for the selected competition and labels PL by gameweek, La Liga by matchday, and WC by Israel match date/day.

## Failure Handling

- Invalid JSON loads as a recoverable error and does not overwrite the last valid state.
- Missing or stale source data skips snapshot creation or evaluation for that match.
- An incomplete result is never used for training.
- Atomic save helpers write complete JSON state; a failed save leaves the previous file intact.
- Build output reports locked, checked, trained, skipped, and promoted counts per competition.
- A model comparison with fewer than 30 rows is displayed as collecting and cannot promote.

## Migration

- Preserve existing PL gameweek history and weights.
- Convert existing PL prediction rows into the versioned store without pretending they contain fields that were not historically captured.
- Preserve all existing WC snapshots, trained flags, results, and model-comparison history.
- Create empty versioned La Liga prediction and weight files with PL-independent defaults.
- Keep the static page compatible with legacy state during one migration release.
- Do not remove user guesses, archived seasons, live data, or learning history.

## GitHub Automation

Update `.github/workflows/update-dashboard.yml` so all six prediction/model files, `learning_history.json`, `live.json`, and generated site files are committed together when changed. CI builds must not upload those files individually through the GitHub Contents API; the workflow owns one atomic commit and push per build. Local builds do not publish unless an explicit publish mode is requested. The existing Git Credential Manager remains the local authentication mechanism. Codex push approval should use a reusable, narrowly scoped `git push` permission.

## Testing

Add Python tests covering:

- 36-hour snapshot eligibility and immutable locks;
- one-time evaluation and training;
- competition isolation;
- PL/La Liga and every WC phase scoring rule;
- candidate promotion success and each rejection condition;
- legacy PL and WC migration without data loss;
- invalid state and incomplete result handling;
- deterministic comparison summaries;
- generated HTML embedding the correct weights for each league.

Run the full Python suite, build `website/update_pl_mobile.py`, parse every generated inline JavaScript block with Node, and inspect the final Git diff before publishing.

## Acceptance Criteria

- PL, La Liga, and WC each persist independent prediction snapshots and model state.
- A snapshot cannot be changed after lock.
- A completed match trains at most once.
- No candidate can promote before 30 matches, with fewer points, or with lower winner accuracy.
- Existing PL and WC history remains available after migration.
- La Liga no longer consumes PL weights.
- The AI page clearly reports active/candidate state and comparable metrics.
- The production build and automated tests pass.
- The published GitHub branch contains the generated site and all required learning files.
- One automated build creates at most one GitHub commit for its generated state.
