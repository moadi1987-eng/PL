# Unified League Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give PL, La Liga, and WC independent, auditable pre-match learning pipelines that promote the higher-scoring model only when winner-direction accuracy does not decline.

**Architecture:** Add a pure Python lifecycle engine for locked snapshots, evaluation, training, comparison, and promotion. Add a deterministic league predictor for PL and La Liga, adapt the existing WC predictor to the same lifecycle, then expose per-league model state through a small testable JavaScript runtime and the existing AI page.

**Tech Stack:** Python 3.11 standard library, existing `requests` build integration, vanilla JavaScript, Node `vm` for syntax/runtime checks, GitHub Actions.

## Global Constraints

- Optimize total competition points; never promote a candidate with lower winner-direction accuracy.
- Require 30 completed locked predictions before promotion.
- Lock the first valid snapshot within 36 hours before kickoff and never rewrite it.
- Train each completed match at most once.
- Keep PL, La Liga, and WC weights, calibration, snapshots, and promotion history separate.
- Preserve existing PL and WC history and every user-guess file.
- Keep one-hour automatic user-guess filling random and separate from AI learning.
- Do not invent missing lineup, injury, or squad data.
- Use only the existing runtime dependencies.
- One automated build may create at most one GitHub commit.

---

### Task 1: Pure Scoring, Comparison, And Promotion Rules

**Files:**
- Create: `website/league_learning.py`
- Create: `tests/__init__.py`
- Create: `tests/test_league_learning.py`

**Interfaces:**
- Produces: `competition_rule(league: str, fixture: dict) -> dict`
- Produces: `score_pick(pick: dict, fixture: dict, rule: dict) -> dict`
- Produces: `comparison_summary(rows: list[dict], active_strategy: str, candidate_strategy: str) -> dict`
- Produces: `promotion_decision(comparison: dict, minimum_samples: int = 30) -> dict`

- [ ] **Step 1: Write failing scoring and promotion tests**

```python
# tests/test_league_learning.py
import unittest

from website.league_learning import (
    comparison_summary,
    competition_rule,
    promotion_decision,
    score_pick,
)


class LeagueLearningRulesTests(unittest.TestCase):
    def test_league_exact_score_adds_direction_and_exact_points(self):
        fixture = {"hs": 2, "as": 1, "fin": True, "e": 4}
        result = score_pick(
            {"winner": "home", "home_score": 2, "away_score": 1},
            fixture,
            competition_rule("pl", fixture),
        )
        self.assertEqual(8, result["points"])
        self.assertTrue(result["winner_correct"])
        self.assertTrue(result["exact"])

    def test_world_cup_uses_phase_specific_non_additive_points(self):
        group = {"hs": 1, "as": 1, "fin": True, "e": 8, "grp": "A"}
        draw = {"winner": "draw", "home_score": 1, "away_score": 1}
        self.assertEqual(3, score_pick(draw, group, competition_rule("wc", group))["points"])
        phases = [
            (18, 2, 5), (25, 2, 5), (28, 4, 8),
            (32, 5, 10), (34, 5, 10), (35, 8, 15),
        ]
        for day, result_points, exact_points in phases:
            fixture = {"hs": 2, "as": 0, "fin": True, "e": day, "grp": None}
            direction = {"winner": "home", "home_score": 2, "away_score": 1}
            exact = {"winner": "home", "home_score": 2, "away_score": 0}
            self.assertEqual(result_points, score_pick(direction, fixture, competition_rule("wc", fixture))["points"])
            self.assertEqual(exact_points, score_pick(exact, fixture, competition_rule("wc", fixture))["points"])

    def test_candidate_promotes_only_with_more_points_and_no_accuracy_loss(self):
        rows = []
        for match_id in range(30):
            actual = {"hs": 1, "as": 0, "fin": True}
            rows.append({
                "match_id": match_id,
                "fixture": actual,
                "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
                "picks": {
                    "baseline": {"winner": "home", "home_score": 2, "away_score": 0},
                    "v4": {"winner": "home", "home_score": 1, "away_score": 0},
                },
            })
        comparison = comparison_summary(rows, "baseline", "v4")
        decision = promotion_decision(comparison)
        self.assertTrue(decision["promote"])
        self.assertEqual("v4", decision["next_active_strategy"])

    def test_candidate_with_lower_winner_accuracy_never_promotes(self):
        comparison = {
            "total": 30,
            "active_strategy": "baseline",
            "candidate_strategy": "v4",
            "models": {
                "baseline": {"points": 60, "winner_accuracy": 70.0},
                "v4": {"points": 70, "winner_accuracy": 66.7},
            },
        }
        self.assertEqual("winner_guard", promotion_decision(comparison)["status"])

    def test_candidate_collects_until_thirty_rows(self):
        comparison = {
            "total": 29,
            "active_strategy": "baseline",
            "candidate_strategy": "v4",
            "models": {
                "baseline": {"points": 40, "winner_accuracy": 55.2},
                "v4": {"points": 80, "winner_accuracy": 65.5},
            },
        }
        self.assertEqual("collecting", promotion_decision(comparison)["status"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_league_learning -v`

Expected: `ModuleNotFoundError: No module named 'website.league_learning'`.

- [ ] **Step 3: Implement the pure rules**

```python
# website/league_learning.py
from collections import Counter


def _winner(home_score, away_score):
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    return "draw"


def competition_rule(league, fixture):
    if league != "wc":
        return {"key": "league", "result": 3, "exact": 5, "additive": True}
    if fixture.get("grp"):
        return {"key": "group", "result": 1, "exact": 3, "additive": False}
    day = int(fixture.get("e") or 0)
    if day >= 35:
        return {"key": "final", "result": 8, "exact": 15, "additive": False}
    if day == 34:
        return {"key": "third", "result": 5, "exact": 10, "additive": False}
    if day >= 32:
        return {"key": "semi", "result": 5, "exact": 10, "additive": False}
    if day >= 28:
        return {"key": "quarter", "result": 4, "exact": 8, "additive": False}
    if day >= 25:
        return {"key": "r16", "result": 2, "exact": 5, "additive": False}
    return {"key": "r32", "result": 2, "exact": 5, "additive": False}


def score_pick(pick, fixture, rule):
    actual_winner = _winner(fixture["hs"], fixture["as"])
    picked_winner = pick.get("winner") or _winner(pick["home_score"], pick["away_score"])
    exact = pick.get("home_score") == fixture["hs"] and pick.get("away_score") == fixture["as"]
    correct = picked_winner == actual_winner
    if rule.get("additive"):
        points = (rule["result"] if correct else 0) + (rule["exact"] if exact else 0)
    else:
        points = rule["exact"] if exact else rule["result"] if correct else 0
    return {
        "winner": picked_winner,
        "winner_correct": correct,
        "exact": exact,
        "points": points,
        "score": f"{pick.get('home_score')}-{pick.get('away_score')}",
    }


def comparison_summary(rows, active_strategy, candidate_strategy):
    strategies = (active_strategy, candidate_strategy)
    metrics = {name: {"winner_correct": 0, "exact_correct": 0, "points": 0, "scores": Counter()} for name in strategies}
    for row in rows:
        for name in strategies:
            scored = score_pick(row["picks"][name], row["fixture"], row["rule"])
            metrics[name]["winner_correct"] += int(scored["winner_correct"])
            metrics[name]["exact_correct"] += int(scored["exact"])
            metrics[name]["points"] += scored["points"]
            metrics[name]["scores"][scored["score"]] += 1
    total = len(rows)
    for box in metrics.values():
        box["winner_accuracy"] = round(box["winner_correct"] / max(total, 1) * 100, 1)
        box["exact_accuracy"] = round(box["exact_correct"] / max(total, 1) * 100, 1)
        box["unique_scores"] = len(box["scores"])
        box["top_scores"] = [{"score": key, "count": count} for key, count in box["scores"].most_common(6)]
        del box["scores"]
    return {"total": total, "active_strategy": active_strategy, "candidate_strategy": candidate_strategy, "models": metrics}


def promotion_decision(comparison, minimum_samples=30):
    active_name = comparison["active_strategy"]
    candidate_name = comparison["candidate_strategy"]
    if comparison["total"] < minimum_samples:
        return {"promote": False, "status": "collecting", "next_active_strategy": active_name}
    active = comparison["models"][active_name]
    candidate = comparison["models"][candidate_name]
    if candidate["winner_accuracy"] < active["winner_accuracy"]:
        return {"promote": False, "status": "winner_guard", "next_active_strategy": active_name}
    if candidate["points"] <= active["points"]:
        return {"promote": False, "status": "points_guard", "next_active_strategy": active_name}
    return {"promote": True, "status": "promote", "next_active_strategy": candidate_name}
```

- [ ] **Step 4: Run the focused tests**

Run: `python -m unittest tests.test_league_learning -v`

Expected: 5 tests pass.

- [ ] **Step 5: Commit the pure rules**

```bash
git add website/league_learning.py tests/__init__.py tests/test_league_learning.py
git commit -m "feat: add competition learning rules"
```

---

### Task 2: Immutable Snapshot State, Migration, And One-Time Training

**Files:**
- Modify: `website/league_learning.py`
- Modify: `tests/test_league_learning.py`

**Interfaces:**
- Produces: `eligible_to_lock(fixture: dict, now: datetime, lock_hours: int = 36) -> bool`
- Produces: `normalize_prediction_store(raw: dict, league: str, legacy_candidate_builder: Callable | None = None) -> dict`
- Produces: `evolve_competition_state(*, league: str, fixtures: list, store: dict, model: dict, snapshot_builder: Callable[[dict, dict], dict], model_trainer: Callable[[dict, list], dict], now: datetime, lock_hours: int = 36, minimum_samples: int = 30) -> tuple[dict, dict, dict, dict]`
- Produces: `load_json_state(path: str, default: dict) -> tuple[dict, bool]`
- Produces: `atomic_save_json(path: str, value: dict) -> None`

- [ ] **Step 1: Add failing lifecycle and migration tests**

```python
# append to tests/test_league_learning.py
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from website.league_learning import (
    atomic_save_json,
    eligible_to_lock,
    evolve_competition_state,
    load_json_state,
    normalize_prediction_store,
)


class SnapshotLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        self.fixture = {
            "id": 77, "e": 1, "h": 1, "a": 2,
            "ko": (self.now + timedelta(hours=20)).isoformat(),
            "st": False, "fin": False, "hs": None, "as": None,
        }
        self.model = {
            "version": 1, "league": "pl", "active_strategy": "baseline",
            "candidate_strategy": "v4", "promotion_history": [], "meta": {},
        }

    @staticmethod
    def snapshot_builder(fixture, model):
        return {
            "features": {"form": 0.4}, "missing": {"squad": True},
            "picks": {
                "baseline": {"winner": "home", "home_score": 2, "away_score": 1},
                "v4": {"winner": "home", "home_score": 1, "away_score": 0},
            },
        }

    def test_only_locks_inside_thirty_six_hour_window(self):
        self.assertTrue(eligible_to_lock(self.fixture, self.now))
        later = dict(self.fixture, ko=(self.now + timedelta(hours=37)).isoformat())
        self.assertFalse(eligible_to_lock(later, self.now))

    def test_locked_snapshot_is_not_rewritten(self):
        store, model, history, counts = evolve_competition_state(
            league="pl", fixtures=[self.fixture], store={}, model=self.model,
            snapshot_builder=self.snapshot_builder, model_trainer=lambda state, rows: state,
            now=self.now,
        )
        first = json.dumps(store["matches"]["77"], sort_keys=True)
        store2, _, _, counts2 = evolve_competition_state(
            league="pl", fixtures=[self.fixture], store=store, model=model,
            snapshot_builder=lambda fixture, state: {"picks": {}, "features": {"changed": True}},
            model_trainer=lambda state, rows: state, now=self.now + timedelta(minutes=5),
        )
        self.assertEqual(first, json.dumps(store2["matches"]["77"], sort_keys=True))
        self.assertEqual(0, counts2["locked"])

    def test_finished_match_trains_exactly_once(self):
        store, model, _, _ = evolve_competition_state(
            league="pl", fixtures=[self.fixture], store={}, model=self.model,
            snapshot_builder=self.snapshot_builder, model_trainer=lambda state, rows: state,
            now=self.now,
        )
        finished = dict(self.fixture, fin=True, st=True, hs=1, as=0)
        calls = []
        def trainer(state, rows):
            calls.append([row["match_id"] for row in rows])
            return state
        store, model, _, _ = evolve_competition_state(
            league="pl", fixtures=[finished], store=store, model=model,
            snapshot_builder=self.snapshot_builder, model_trainer=trainer,
            now=self.now + timedelta(days=1),
        )
        store, model, _, _ = evolve_competition_state(
            league="pl", fixtures=[finished], store=store, model=model,
            snapshot_builder=self.snapshot_builder, model_trainer=trainer,
            now=self.now + timedelta(days=1, minutes=5),
        )
        self.assertEqual([[77]], calls)
        self.assertTrue(store["matches"]["77"]["model_trained"])

    def test_legacy_pl_rows_are_preserved(self):
        legacy = {"28": {"created_at": "before", "predictions": [{"match_id": 9, "winner": "home", "home_score": 2, "away_score": 1}]}}
        migrated = normalize_prediction_store(legacy, "pl")
        self.assertEqual("home", migrated["matches"]["9"]["picks"]["baseline"]["winner"])
        self.assertTrue(migrated["matches"]["9"]["legacy"])

    def test_invalid_json_does_not_replace_valid_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            value, valid = load_json_state(str(path), {"safe": True})
            self.assertEqual({"safe": True}, value)
            self.assertFalse(valid)
            atomic_save_json(str(path), {"safe": "new"})
            self.assertEqual({"safe": "new"}, json.loads(path.read_text(encoding="utf-8")))
```

- [ ] **Step 2: Run the new tests and confirm missing-symbol failures**

Run: `python -m unittest tests.test_league_learning.SnapshotLifecycleTests -v`

Expected: import failures for the lifecycle functions.

- [ ] **Step 3: Implement state normalization and lifecycle evolution**

Add these behaviors to `website/league_learning.py`:

```python
import copy
import json
import os
import tempfile
from datetime import datetime, timezone


STORE_VERSION = 1


def _utc(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def eligible_to_lock(fixture, now, lock_hours=36):
    if fixture.get("fin") or fixture.get("st") or not fixture.get("ko"):
        return False
    try:
        seconds = (_utc(fixture["ko"]) - _utc(now)).total_seconds()
    except (TypeError, ValueError):
        return False
    return 0 <= seconds <= lock_hours * 3600


def normalize_prediction_store(raw, league, legacy_candidate_builder=None):
    raw = copy.deepcopy(raw or {})
    if isinstance(raw.get("matches"), dict):
        raw.setdefault("version", STORE_VERSION)
        raw.setdefault("league", league)
        for match_id, snapshot in raw["matches"].items():
            snapshot.setdefault("match_id", int(match_id) if str(match_id).isdigit() else match_id)
            snapshot.setdefault("locked", True)
            if "picks" not in snapshot:
                baseline = copy.deepcopy(snapshot.get("base_v3_prediction") or {
                    "winner": snapshot.get("winner"),
                    "home_score": snapshot.get("home_score"),
                    "away_score": snapshot.get("away_score"),
                })
                candidate = copy.deepcopy(snapshot.get("v4_shadow") or (
                    legacy_candidate_builder(snapshot) if legacy_candidate_builder else baseline
                ))
                snapshot["picks"] = {"baseline": baseline, "v4": candidate}
        return raw
    matches = {}
    for round_key, packet in raw.items():
        if not isinstance(packet, dict):
            continue
        for pick in packet.get("predictions", []):
            match_id = str(pick.get("match_id") or "")
            if not match_id:
                continue
            baseline = copy.deepcopy(pick)
            candidate = legacy_candidate_builder(baseline) if legacy_candidate_builder else copy.deepcopy(baseline)
            matches[match_id] = {
                "match_id": pick.get("match_id"), "league": league, "round": int(round_key),
                "created_at": packet.get("created_at", ""), "locked": True, "legacy": True,
                "checked": False, "model_trained": True,
                "features": {}, "missing": {"legacy_features": True},
                "picks": {"baseline": baseline, "v4": candidate},
            }
    return {"version": STORE_VERSION, "league": league, "matches": matches, "updated_at": ""}


def load_json_state(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), True
    except (OSError, ValueError, TypeError):
        return copy.deepcopy(default), False


def atomic_save_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".learning-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
```

Implement `evolve_competition_state` as this pure transition. Keep helper extraction local if needed, but preserve the signature and returned keys:

```python
def evolve_competition_state(
    *, league, fixtures, store, model, snapshot_builder, model_trainer,
    now, lock_hours=36, minimum_samples=30,
):
    store = normalize_prediction_store(store, league)
    store = copy.deepcopy(store)
    model = copy.deepcopy(model)
    store.setdefault("matches", {})
    model.setdefault("promotion_history", [])
    counts = {"locked": 0, "checked": 0, "trained": 0, "skipped": 0, "promoted": 0}
    fixture_map = {str(item.get("id")): item for item in fixtures if item.get("id") is not None}
    timestamp = _utc(now).strftime("%Y-%m-%dT%H:%M:%SZ")

    for fixture in fixtures:
        match_id = str(fixture.get("id") or "")
        if not match_id or match_id in store["matches"]:
            continue
        if not eligible_to_lock(fixture, now, lock_hours):
            counts["skipped"] += 1
            continue
        snapshot = copy.deepcopy(snapshot_builder(fixture, model))
        snapshot.update({
            "match_id": fixture["id"], "league": league, "round": fixture.get("e"),
            "home_id": fixture.get("h"), "away_id": fixture.get("a"),
            "kickoff_time": fixture.get("ko", ""), "created_at": timestamp,
            "locked": True, "checked": False, "model_trained": False,
            "active_strategy_at_lock": model.get("active_strategy", "baseline"),
        })
        store["matches"][match_id] = snapshot
        counts["locked"] += 1

    comparison_rows = []
    train_batch = []
    for match_id, snapshot in store["matches"].items():
        fixture = fixture_map.get(str(match_id))
        if not fixture or not fixture.get("fin") or fixture.get("hs") is None or fixture.get("as") is None:
            continue
        if not snapshot.get("checked"):
            snapshot["checked"] = True
            snapshot["checked_at"] = timestamp
            snapshot["actual_home_score"] = fixture["hs"]
            snapshot["actual_away_score"] = fixture["as"]
            snapshot["actual_winner"] = _winner(fixture["hs"], fixture["as"])
            snapshot["rule"] = competition_rule(league, fixture)
            snapshot["evaluations"] = {
                name: score_pick(pick, fixture, snapshot["rule"])
                for name, pick in snapshot.get("picks", {}).items()
            }
            counts["checked"] += 1
        if {"baseline", "v4"}.issubset(snapshot.get("picks", {})):
            comparison_rows.append({
                "match_id": snapshot.get("match_id"),
                "fixture": {"hs": fixture["hs"], "as": fixture["as"]},
                "rule": snapshot.get("rule") or competition_rule(league, fixture),
                "picks": snapshot["picks"],
            })
        if not snapshot.get("model_trained") and not snapshot.get("legacy"):
            training_row = copy.deepcopy(snapshot)
            training_row["fixture"] = {"hs": fixture["hs"], "as": fixture["as"]}
            train_batch.append(training_row)

    if train_batch:
        model = model_trainer(model, train_batch)
        for row in train_batch:
            stored = store["matches"][str(row["match_id"])]
            stored["model_trained"] = True
            stored["trained_at"] = timestamp
        counts["trained"] = len(train_batch)

    active = model.setdefault("active_strategy", "baseline")
    candidate = model.get("candidate_strategy", "v4" if active == "baseline" else "baseline")
    model.setdefault("candidate_strategy", candidate)
    comparison = comparison_summary(comparison_rows, active, candidate)
    decision = promotion_decision(comparison, minimum_samples)
    if decision["promote"] and decision["next_active_strategy"] != active:
        model["promotion_history"].append({
            "at": timestamp, "from": active, "to": decision["next_active_strategy"],
            "comparison": copy.deepcopy(comparison),
        })
        model["active_strategy"] = decision["next_active_strategy"]
        model["candidate_strategy"] = active
        counts["promoted"] = 1
    model["comparison"] = comparison
    model["status"] = decision
    store["updated_at"] = timestamp
    by_round = {}
    completeness_values = []
    for snapshot in store["matches"].values():
        if not snapshot.get("checked"):
            continue
        strategy_at_lock = snapshot.get("active_strategy_at_lock", "baseline")
        evaluation = (snapshot.get("evaluations") or {}).get(strategy_at_lock)
        if not evaluation:
            continue
        round_id = int(snapshot.get("round") or 0)
        row = by_round.setdefault(round_id, {"gw": round_id, "total": 0, "correct_winner": 0, "correct_score": 0, "exact_score": 0, "points": 0})
        row["total"] += 1
        row["correct_winner"] += int(evaluation["winner_correct"])
        row["correct_score"] += int(evaluation["exact"])
        row["exact_score"] += int(evaluation["exact"])
        row["points"] += evaluation["points"]
        missing_flags = list((snapshot.get("missing") or {}).values())
        if missing_flags:
            completeness_values.append(sum(not bool(value) for value in missing_flags) / len(missing_flags))
    gw_results = []
    for row in sorted(by_round.values(), key=lambda item: item["gw"]):
        row["accuracy_pct"] = round(row["correct_winner"] / max(row["total"], 1) * 100, 1)
        row["score_acc_pct"] = round(row["correct_score"] / max(row["total"], 1) * 100, 1)
        gw_results.append(row)
    history = {
        "gw_results": gw_results,
        "overall_accuracy": comparison["models"].get(model["active_strategy"], {}).get("winner_accuracy", 0),
        "total_evaluated": comparison["total"],
        "current_weights": model.get("factors", {}),
        "calibration": model.get("calibration", {}),
        "model_meta": model.get("meta", {}),
        "model_comparison": comparison,
        "model_status": {**decision, "active_strategy": model["active_strategy"], "candidate_strategy": model["candidate_strategy"]},
        "data_completeness_pct": round(sum(completeness_values) / len(completeness_values) * 100, 1) if completeness_values else 100.0,
        "snapshots_locked": len(store["matches"]),
        "trained_this_build": counts["trained"],
    }
    return store, model, history, counts
```

- [ ] **Step 4: Run lifecycle tests and the full focused module**

Run: `python -m unittest tests.test_league_learning -v`

Expected: all Task 1 and Task 2 tests pass.

- [ ] **Step 5: Commit lifecycle state support**

```bash
git add website/league_learning.py tests/test_league_learning.py
git commit -m "feat: add immutable prediction lifecycle"
```

---

### Task 3: Deterministic PL And La Liga Predictor With Independent Training

**Files:**
- Create: `website/league_predictor.py`
- Create: `tests/test_league_predictor.py`

**Interfaces:**
- Consumes: `competition_rule` from Task 1.
- Produces: `default_model_state(league: str, active_strategy: str = "baseline") -> dict`
- Produces: `normalize_model_state(raw: dict, league: str, default_active: str = "baseline") -> dict`
- Produces: `predict_league_snapshot(fixture: dict, fixtures: list, teams: dict, model: dict, league: str) -> dict`
- Produces: `train_factor_model(model: dict, rows: list[dict]) -> dict`
- Produces: `legacy_v4_pick(pick: dict) -> dict`

- [ ] **Step 1: Write failing predictor tests**

```python
# tests/test_league_predictor.py
import copy
import unittest

from website.league_predictor import default_model_state, predict_league_snapshot, train_factor_model


class LeaguePredictorTests(unittest.TestCase):
    def setUp(self):
        self.teams = {
            1: {"id": 1, "s": "AAA", "sah": 1250, "sdh": 1180, "saa": 1190, "sda": 1160},
            2: {"id": 2, "s": "BBB", "sah": 1030, "sdh": 1010, "saa": 1040, "sda": 1000},
        }
        self.history = [
            {"id": 1, "e": 1, "h": 1, "a": 2, "hs": 2, "as": 0, "fin": True, "st": True, "ko": "2026-08-01T12:00:00Z"},
            {"id": 2, "e": 2, "h": 2, "a": 1, "hs": 1, "as": 1, "fin": True, "st": True, "ko": "2026-08-08T12:00:00Z"},
        ]
        self.target = {"id": 3, "e": 3, "h": 1, "a": 2, "hs": None, "as": None, "fin": False, "st": False, "ko": "2026-08-15T12:00:00Z"}

    def test_snapshot_contains_two_deterministic_strategies(self):
        model = default_model_state("pl")
        first = predict_league_snapshot(self.target, self.history + [self.target], self.teams, model, "pl")
        second = predict_league_snapshot(self.target, self.history + [self.target], self.teams, model, "pl")
        self.assertEqual(first, second)
        self.assertEqual({"baseline", "v4"}, set(first["picks"]))
        self.assertAlmostEqual(100.0, sum(first["probabilities"].values()), places=1)

    def test_future_results_do_not_leak_into_snapshot(self):
        model = default_model_state("pl")
        original = predict_league_snapshot(self.target, self.history + [self.target], self.teams, model, "pl")
        leaked = self.history + [self.target, {"id": 99, "e": 4, "h": 2, "a": 1, "hs": 9, "as": 0, "fin": True, "st": True, "ko": "2026-08-22T12:00:00Z"}]
        self.assertEqual(original, predict_league_snapshot(self.target, leaked, self.teams, model, "pl"))

    def test_la_liga_model_is_independent_from_pl_model(self):
        pl = default_model_state("pl")
        ll = default_model_state("laliga")
        ll["factors"]["strength"] = 0.25
        self.assertNotEqual(pl["factors"], ll["factors"])

    def test_missing_squad_data_is_recorded(self):
        snapshot = predict_league_snapshot(self.target, self.history + [self.target], self.teams, default_model_state("pl"), "pl")
        self.assertTrue(snapshot["missing"]["squad_availability"])

    def test_training_normalizes_weights_and_updates_metadata(self):
        model = default_model_state("laliga")
        rows = [{
            "actual_winner": "home", "fixture": {"hs": 2, "as": 0},
            "factor_edges": {"strength": 0.8, "form": 0.4},
            "expected_home_goals": 1.2, "expected_away_goals": 1.1,
            "picks": {"baseline": {"winner": "home", "home_score": 1, "away_score": 0}, "v4": {"winner": "home", "home_score": 2, "away_score": 0}},
        } for _ in range(3)]
        trained = train_factor_model(copy.deepcopy(model), rows)
        self.assertAlmostEqual(1.0, sum(trained["factors"].values()), places=3)
        self.assertEqual(3, trained["meta"]["trained_matches"])
        self.assertGreater(trained["factors"]["strength"], model["factors"]["strength"])
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run: `python -m unittest tests.test_league_predictor -v`

Expected: `ModuleNotFoundError: No module named 'website.league_predictor'`.

- [ ] **Step 3: Implement the deterministic predictor**

Create `website/league_predictor.py` with this structure and formulas. Helper names may remain private, but the four public interfaces must remain unchanged:

```python
import copy
import math
from datetime import datetime, timezone

from website.league_learning import competition_rule


DEFAULT_FACTORS = {
    "form": 0.15, "strength": 0.15, "position": 0.12, "home_adv": 0.08,
    "streak": 0.12, "h2h": 0.08, "home_away_split": 0.08,
    "goals_trend": 0.06, "upset": 0.06, "clean_sheet": 0.05,
    "draw_tendency": 0.05,
}


def _clip(value, low, high):
    return max(low, min(high, value))


def _utc(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def default_model_state(league, active_strategy="baseline"):
    candidate = "v4" if active_strategy == "baseline" else "baseline"
    return {
        "version": 1, "league": league,
        "active_strategy": active_strategy, "candidate_strategy": candidate,
        "factors": copy.deepcopy(DEFAULT_FACTORS),
        "calibration": {"goal_mult": 1.0, "home_goal_bias": 0.0, "away_goal_bias": 0.0, "draw_bias": 1.0, "zero_zero_penalty": 0.62},
        "meta": {"trained_matches": 0, "last_trained_at": "", "last_batch_size": 0},
        "promotion_history": [],
    }


def normalize_model_state(raw, league, default_active="baseline"):
    default = default_model_state(league, default_active)
    if not isinstance(raw, dict):
        return default
    if "factors" not in raw:
        default["factors"].update({key: float(value) for key, value in raw.items() if key in DEFAULT_FACTORS})
        return default
    merged = copy.deepcopy(default)
    for key in ("version", "active_strategy", "candidate_strategy", "promotion_history"):
        if key in raw:
            merged[key] = copy.deepcopy(raw[key])
    merged["factors"].update(raw.get("factors") or {})
    merged["calibration"].update(raw.get("calibration") or {})
    merged["meta"].update(raw.get("meta") or {})
    return merged


def _prior_fixtures(fixtures, target):
    target_time = _utc(target["ko"])
    return [
        row for row in fixtures
        if row.get("fin") and row.get("hs") is not None and row.get("ko")
        and _utc(row["ko"]) < target_time
    ]


def _team_stats(team_id, prior):
    rows = []
    for item in prior:
        if item.get("h") != team_id and item.get("a") != team_id:
            continue
        home = item.get("h") == team_id
        gf = item["hs"] if home else item["as"]
        ga = item["as"] if home else item["hs"]
        rows.append({"gf": gf, "ga": ga, "home": home, "result": 3 if gf > ga else 1 if gf == ga else 0})
    rows = rows[-8:]
    recent = rows[-5:]
    played = len(recent)
    if not played:
        return {"played": 0, "form": 0.5, "streak": 0.5, "gf": 1.2, "ga": 1.2, "split_gf": 1.2, "clean": 0.0, "draw": 0.2, "trend": 1.2}
    weights = list(range(1, played + 1))
    home_rows = [row for row in rows if row["home"]]
    away_rows = [row for row in rows if not row["home"]]
    return {
        "played": played,
        "form": sum(row["result"] for row in recent) / (played * 3),
        "streak": sum(row["result"] * weight for row, weight in zip(recent, weights)) / (3 * sum(weights)),
        "gf": sum(row["gf"] for row in recent) / played,
        "ga": sum(row["ga"] for row in recent) / played,
        "home_gf": sum(row["gf"] for row in home_rows) / len(home_rows) if home_rows else 1.2,
        "away_gf": sum(row["gf"] for row in away_rows) / len(away_rows) if away_rows else 1.1,
        "clean": sum(row["ga"] == 0 for row in recent) / played,
        "draw": sum(row["result"] == 1 for row in rows) / len(rows),
        "trend": sum(row["gf"] for row in rows[-3:]) / len(rows[-3:]),
    }


def _positions(prior, team_ids):
    table = {team_id: {"points": 0, "gd": 0, "gf": 0} for team_id in team_ids}
    for item in prior:
        home, away, hs, as_ = item["h"], item["a"], item["hs"], item["as"]
        table.setdefault(home, {"points": 0, "gd": 0, "gf": 0})
        table.setdefault(away, {"points": 0, "gd": 0, "gf": 0})
        table[home]["points"] += 3 if hs > as_ else 1 if hs == as_ else 0
        table[away]["points"] += 3 if as_ > hs else 1 if hs == as_ else 0
        table[home]["gd"] += hs - as_; table[away]["gd"] += as_ - hs
        table[home]["gf"] += hs; table[away]["gf"] += as_
    ordered = sorted(table, key=lambda team_id: (-table[team_id]["points"], -table[team_id]["gd"], -table[team_id]["gf"], team_id))
    return {team_id: index + 1 for index, team_id in enumerate(ordered)}


def _h2h_edge(prior, home_id, away_id):
    home_points = away_points = meetings = 0
    for item in prior:
        if {item.get("h"), item.get("a")} != {home_id, away_id}:
            continue
        meetings += 1
        home_goals = item["hs"] if item["h"] == home_id else item["as"]
        away_goals = item["as"] if item["h"] == home_id else item["hs"]
        home_points += 3 if home_goals > away_goals else 1 if home_goals == away_goals else 0
        away_points += 3 if away_goals > home_goals else 1 if home_goals == away_goals else 0
    return _clip((home_points - away_points) / max(meetings * 3, 1), -1, 1) if meetings else 0.0


def _poisson(lam, goals):
    return math.exp(-lam) * (lam ** goals) / math.factorial(goals)


def _poisson_grid(lam_h, lam_a, maximum=6):
    grid = {}
    total = home = draw = away = 0.0
    for hs in range(maximum + 1):
        for as_ in range(maximum + 1):
            probability = _poisson(lam_h, hs) * _poisson(lam_a, as_)
            grid[(hs, as_)] = probability
            total += probability
            if hs > as_: home += probability
            elif as_ > hs: away += probability
            else: draw += probability
    return grid, {"home": home / total, "draw": draw / total, "away": away / total}


def _expected_points_pick(grid, outcomes, rule):
    best = None
    for (hs, as_), exact_probability in grid.items():
        winner = "home" if hs > as_ else "away" if as_ > hs else "draw"
        if rule.get("additive"):
            value = outcomes[winner] * rule["result"] + exact_probability * rule["exact"]
        else:
            value = (outcomes[winner] - exact_probability) * rule["result"] + exact_probability * rule["exact"]
        candidate = (value, exact_probability, -abs(hs - as_), -hs - as_, hs, as_)
        if best is None or candidate > best[0]:
            best = (candidate, {"winner": winner, "home_score": hs, "away_score": as_, "expected_points": round(value, 4)})
    return best[1]


def _v4_pick(lam_h, lam_a, probabilities, draw_rate):
    hp, dp, ap = probabilities["home"], probabilities["draw"], probabilities["away"]
    if dp >= 0.23 and abs(hp - ap) <= 0.12:
        goals = 2 if lam_h + lam_a >= 3.1 else 1 if lam_h + lam_a >= 1.8 else 0
        return {"winner": "draw", "home_score": goals, "away_score": goals, "reason": "draw-v4"}
    winner = "home" if hp >= ap else "away"
    favorite = max(hp, ap); dog_lam = lam_a if winner == "home" else lam_h
    total = lam_h + lam_a
    if favorite >= 0.52 and abs(lam_h - lam_a) >= 0.95:
        fav_goals, dog_goals, reason = (3, 0, "strong-clean-win") if dog_lam < 0.78 else (3, 1, "strong-open-win") if total >= 3.0 else (2, 0, "strong-controlled-win")
    elif total >= 3.15:
        fav_goals, dog_goals, reason = 3, 1, "open-win"
    elif dog_lam < 0.75:
        fav_goals, dog_goals, reason = (1, 0, "low-total-edge") if total < 2.25 else (2, 0, "clean-win")
    else:
        fav_goals, dog_goals, reason = (1, 0, "tight-win") if total < 2.25 else (2, 1, "balanced-win")
    if winner == "home":
        return {"winner": winner, "home_score": fav_goals, "away_score": dog_goals, "reason": reason}
    return {"winner": winner, "home_score": dog_goals, "away_score": fav_goals, "reason": reason}


def predict_league_snapshot(fixture, fixtures, teams, model, league):
    model = normalize_model_state(model, league, model.get("active_strategy", "baseline"))
    prior = _prior_fixtures(fixtures, fixture)
    home_id, away_id = fixture["h"], fixture["a"]
    home, away = _team_stats(home_id, prior), _team_stats(away_id, prior)
    positions = _positions(prior, set(teams) | {home_id, away_id})
    home_team, away_team = teams.get(home_id, {}), teams.get(away_id, {})
    home_strength = (float(home_team.get("sah", 1000)) + float(home_team.get("sdh", 1000))) / 2
    away_strength = (float(away_team.get("saa", 1000)) + float(away_team.get("sda", 1000))) / 2
    team_count = max(len(positions), 2)
    factor_edges = {
        "form": _clip(home["form"] - away["form"], -1, 1),
        "strength": _clip((home_strength - away_strength) / 400, -1, 1),
        "position": _clip((positions.get(away_id, team_count) - positions.get(home_id, team_count)) / team_count, -1, 1),
        "home_adv": 0.18,
        "streak": _clip(home["streak"] - away["streak"], -1, 1),
        "h2h": _h2h_edge(prior, home_id, away_id),
        "home_away_split": _clip((home["home_gf"] - away["away_gf"]) / 3, -1, 1),
        "goals_trend": _clip((home["trend"] - away["trend"]) / 3, -1, 1),
        "upset": _clip((away["form"] - home["form"]) * 0.25, -1, 1),
        "clean_sheet": _clip(home["clean"] - away["clean"], -1, 1),
        "draw_tendency": _clip(away["draw"] - home["draw"], -1, 1),
    }
    weights = model["factors"]
    home_factor = 1.0 + sum(weights.get(key, 0) * max(edge, 0) for key, edge in factor_edges.items())
    away_factor = 1.0 + sum(weights.get(key, 0) * max(-edge, 0) for key, edge in factor_edges.items())
    factor_home = home_factor / (home_factor + away_factor)
    closeness = 1 - abs(factor_home - (1 - factor_home))
    factor_draw = _clip(0.10 + 0.18 * closeness + 0.15 * ((home["draw"] + away["draw"]) / 2), 0.10, 0.34)
    factor_home *= 1 - factor_draw; factor_away = 1 - factor_home - factor_draw
    strength_shift = _clip((home_strength - away_strength) / 900, -0.35, 0.35)
    lam_h = 0.30 * home["gf"] + 0.25 * away["ga"] + 0.15 * home["home_gf"] + 0.15 * home["trend"] + 0.15 * (1.35 + strength_shift)
    lam_a = 0.30 * away["gf"] + 0.25 * home["ga"] + 0.15 * away["away_gf"] + 0.15 * away["trend"] + 0.15 * (1.15 - strength_shift)
    calibration = model["calibration"]
    lam_h = _clip(lam_h * calibration["goal_mult"] + calibration["home_goal_bias"], 0.30, 4.40)
    lam_a = _clip(lam_a * calibration["goal_mult"] + calibration["away_goal_bias"], 0.30, 4.40)
    grid, poisson = _poisson_grid(lam_h, lam_a)
    probabilities = {
        "home": factor_home * 0.4 + poisson["home"] * 0.6,
        "draw": factor_draw * 0.4 + poisson["draw"] * 0.6,
        "away": factor_away * 0.4 + poisson["away"] * 0.6,
    }
    probabilities["draw"] *= calibration["draw_bias"]
    total = sum(probabilities.values())
    probabilities = {key: value / total for key, value in probabilities.items()}
    rule = competition_rule(league, fixture)
    shown_home = round(probabilities["home"] * 100, 1)
    shown_draw = round(probabilities["draw"] * 100, 1)
    shown_away = round(100.0 - shown_home - shown_draw, 1)
    return {
        "features": {"home": home, "away": away, "positions": {"home": positions.get(home_id), "away": positions.get(away_id)}},
        "factor_edges": factor_edges,
        "missing": {"squad_availability": not any(key in home_team or key in away_team for key in ("inj", "sq"))},
        "probabilities": {"home": shown_home, "draw": shown_draw, "away": shown_away},
        "expected_home_goals": round(lam_h, 3), "expected_away_goals": round(lam_a, 3),
        "picks": {
            "baseline": _expected_points_pick(grid, probabilities, rule),
            "v4": _v4_pick(lam_h, lam_a, probabilities, (home["draw"] + away["draw"]) / 2),
        },
    }


def legacy_v4_pick(pick):
    probabilities = {"home": float(pick.get("home_win_pct", 0)) / 100, "draw": float(pick.get("draw_pct", 0)) / 100, "away": float(pick.get("away_win_pct", 0)) / 100}
    return _v4_pick(max(float(pick.get("home_score", 1)), 0.3), max(float(pick.get("away_score", 1)), 0.3), probabilities, probabilities["draw"])


def train_factor_model(model, rows):
    model = copy.deepcopy(model)
    factor_scores = {key: [] for key in model["factors"]}
    actual_totals = []; predicted_totals = []
    for row in rows:
        actual = row.get("actual_winner")
        for key, edge in (row.get("factor_edges") or {}).items():
            if key in factor_scores and abs(edge) >= 0.05 and actual != "draw":
                factor_scores[key].append(1.0 if (edge > 0 and actual == "home") or (edge < 0 and actual == "away") else 0.0)
        fixture = row.get("fixture") or {}
        actual_totals.append(float(fixture.get("hs", 0)) + float(fixture.get("as", 0)))
        predicted_totals.append(float(row.get("expected_home_goals", 0)) + float(row.get("expected_away_goals", 0)))
    for key, values in factor_scores.items():
        if len(values) >= 3:
            model["factors"][key] = _clip(model["factors"][key] + ((sum(values) / len(values)) - 0.5) * 0.04, 0.03, 0.25)
    total_weight = sum(model["factors"].values())
    model["factors"] = {key: round(value / total_weight, 4) for key, value in model["factors"].items()}
    if rows:
        actual_avg = sum(actual_totals) / len(actual_totals)
        predicted_avg = sum(predicted_totals) / len(predicted_totals)
        model["calibration"]["goal_mult"] = round(_clip(model["calibration"]["goal_mult"] + _clip((actual_avg - predicted_avg) * 0.035, -0.06, 0.06), 0.82, 1.22), 4)
    meta = model.setdefault("meta", {})
    meta["trained_matches"] = int(meta.get("trained_matches", 0)) + len(rows)
    meta["last_batch_size"] = len(rows)
    meta["last_trained_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return model
```

- [ ] **Step 4: Run predictor and lifecycle suites**

Run: `python -m unittest tests.test_league_predictor tests.test_league_learning -v`

Expected: all tests pass with no warnings.

- [ ] **Step 5: Commit the predictor**

```bash
git add website/league_predictor.py tests/test_league_predictor.py
git commit -m "feat: add independent league predictor"
```

---

### Task 4: Run All Three Competitions Through The Shared Lifecycle

**Files:**
- Modify: `website/update_pl_mobile.py`
- Modify: `website/ml_engine.py`
- Create: `ai_predictions_laliga.json`
- Create: `ai_weights_laliga.json`
- Create: `tests/test_learning_integration.py`

**Interfaces:**
- Consumes: lifecycle and predictor interfaces from Tasks 1-3.
- Produces: `run_persistent_competition(*, league: str, fixtures: list, teams: dict, prediction_path: str, model_path: str, history: dict, now: datetime, snapshot_builder: Callable[[dict, dict], dict], model_trainer: Callable[[dict, list], dict], default_model: dict, legacy_candidate_builder: Callable[[dict], dict] | None = None) -> tuple[dict, dict, dict]` in `website/league_learning.py`.
- Produces: per-league `model_status`, `model_comparison`, `current_weights`, `calibration`, and counters in `learning_history.json`.

- [ ] **Step 1: Write failing persistence and isolation integration tests**

```python
# tests/test_learning_integration.py
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from website.league_learning import run_persistent_competition
from website.league_predictor import default_model_state, predict_league_snapshot, train_factor_model


class PersistentCompetitionTests(unittest.TestCase):
    def test_pl_and_laliga_write_independent_state(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        fixture = {"id": 1, "e": 1, "h": 1, "a": 2, "ko": (now + timedelta(hours=10)).isoformat(), "fin": False, "st": False, "hs": None, "as": None}
        teams = {1: {"id": 1, "s": "A"}, 2: {"id": 2, "s": "B"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            histories = {}
            outputs = {}
            for league in ("pl", "laliga"):
                prediction_path = root / f"{league}-predictions.json"
                model_path = root / f"{league}-weights.json"
                history, counts, model = run_persistent_competition(
                    league=league, fixtures=[fixture], teams=teams,
                    prediction_path=str(prediction_path), model_path=str(model_path),
                    history={}, now=now,
                    snapshot_builder=lambda f, m, lg=league: predict_league_snapshot(f, [fixture], teams, m, lg),
                    model_trainer=train_factor_model,
                    default_model=default_model_state(league),
                )
                outputs[league] = json.loads(model_path.read_text(encoding="utf-8"))
                histories[league] = history
            self.assertEqual("pl", outputs["pl"]["league"])
            self.assertEqual("laliga", outputs["laliga"]["league"])
            self.assertNotEqual(root / "pl-weights.json", root / "laliga-weights.json")
            self.assertEqual(1, histories["pl"]["snapshots_locked"])
            self.assertEqual(1, histories["laliga"]["snapshots_locked"])

    def test_existing_wc_fields_survive_normalization(self):
        from website.league_learning import normalize_prediction_store
        raw = {"version": 4, "matches": {"760": {"match_id": 760, "winner": "home", "model_trained": True, "custom_wc_field": "keep"}}}
        normalized = normalize_prediction_store(raw, "wc")
        self.assertEqual("keep", normalized["matches"]["760"]["custom_wc_field"])
```

- [ ] **Step 2: Run integration tests and confirm `run_persistent_competition` is missing**

Run: `python -m unittest tests.test_learning_integration -v`

Expected: import failure for `run_persistent_competition`.

- [ ] **Step 3: Add the persistence wrapper and wire PL/La Liga**

Add this persistence wrapper to `website/league_learning.py`:

```python
def run_persistent_competition(
    *, league, fixtures, teams, prediction_path, model_path, history, now,
    snapshot_builder, model_trainer, default_model, legacy_candidate_builder=None,
):
    raw_store, _ = load_json_state(prediction_path, {})
    store = normalize_prediction_store(raw_store, league, legacy_candidate_builder)
    raw_model, model_valid = load_json_state(model_path, default_model)
    if not model_valid:
        raw_model = copy.deepcopy(default_model)
    if "factors" not in raw_model:
        raw_model = {**copy.deepcopy(default_model), "factors": copy.deepcopy(raw_model)}
    else:
        merged = copy.deepcopy(default_model)
        merged.update({key: copy.deepcopy(value) for key, value in raw_model.items() if key not in ("factors", "calibration", "meta")})
        merged["factors"].update(raw_model.get("factors") or {})
        merged["calibration"].update(raw_model.get("calibration") or {})
        merged["meta"].update(raw_model.get("meta") or {})
        raw_model = merged
    store, model, league_history, counts = evolve_competition_state(
        league=league, fixtures=fixtures, store=store, model=raw_model,
        snapshot_builder=snapshot_builder, model_trainer=model_trainer,
        now=now,
    )
    atomic_save_json(prediction_path, store)
    atomic_save_json(model_path, model)
    return league_history, counts, model
```

In `website/update_pl_mobile.py`:

- replace `run_pl_learning` and `run_ll_learning` calls with `run_persistent_competition`;
- pass compact PL fixtures and `ll_fixtures` using their dedicated model paths;
- use `legacy_v4_pick` when migrating PL rows;
- create La Liga state from independent defaults;
- preserve the old `learning_history.json` object before merging new league output.

Keep `website/ml_engine.py` as a compatibility module that imports and re-exports legacy evaluation helpers used by historical reports; remove its responsibility for writing current PL or La Liga model files.

- [ ] **Step 4: Adapt WC lifecycle without changing its predictor mathematics**

Refactor `run_wc_learning` in `website/update_pl_mobile.py` into a thin call to `run_persistent_competition`:

- wrap `_wc_predict_snapshot` so it returns `picks={"baseline": base_v3_prediction, "v4": current_v4_pick}` plus the existing feature snapshot;
- use `_wc_update_model` as the model trainer;
- initialize WC with `active_strategy="v4"` and `candidate_strategy="baseline"` because the existing 65-row comparison qualifies v4;
- normalize old WC rows in place while retaining all old keys;
- use the shared competition rule and scoring functions;
- preserve `trained_at`, `model_trained`, actual results, and the existing comparison history.

Use this adapter shape so WC prediction mathematics remains unchanged:

```python
def _wc_shared_snapshot(fixture, model):
    raw = _wc_predict_snapshot(wc_teams, wc_fixtures, fixture, model)
    if not raw:
        return {}
    v4 = _wc_v4_score_pick(raw)
    return {
        "features": raw.get("input_snapshot", {}),
        "factor_edges": {
            key: 1.0 if side == "home" else -1.0 if side == "away" else 0.0
            for key, side in (raw.get("signals") or {}).items()
        },
        "missing": raw.get("input_snapshot", {}).get("missing", {}),
        "probabilities": {"home": raw.get("home_win_pct", 0), "draw": raw.get("draw_pct", 0), "away": raw.get("away_win_pct", 0)},
        "expected_home_goals": raw.get("expected_home_goals", 0),
        "expected_away_goals": raw.get("expected_away_goals", 0),
        "picks": {
            "baseline": raw.get("base_v3_prediction") or {"winner": raw.get("winner"), "home_score": raw.get("home_score"), "away_score": raw.get("away_score")},
            "v4": {"winner": v4.get("winner"), "home_score": v4.get("home_score"), "away_score": v4.get("away_score"), "reason": v4.get("reason")},
        },
    }
```

- [ ] **Step 5: Create valid empty La Liga files**

```json
{"version":1,"league":"laliga","matches":{},"updated_at":""}
```

```json
{"version":1,"league":"laliga","active_strategy":"baseline","candidate_strategy":"v4","factors":{"form":0.15,"strength":0.15,"position":0.12,"home_adv":0.08,"streak":0.12,"h2h":0.08,"home_away_split":0.08,"goals_trend":0.06,"upset":0.06,"clean_sheet":0.05,"draw_tendency":0.05},"calibration":{"goal_mult":1.0,"home_goal_bias":0.0,"away_goal_bias":0.0,"draw_bias":1.0,"zero_zero_penalty":0.62},"meta":{"trained_matches":0,"last_trained_at":"","last_batch_size":0},"promotion_history":[]}
```

- [ ] **Step 6: Run all Python tests**

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass; WC preservation and PL/La Liga isolation are green.

- [ ] **Step 7: Commit shared lifecycle integration**

```bash
git add website/league_learning.py website/update_pl_mobile.py website/ml_engine.py ai_predictions_laliga.json ai_weights_laliga.json tests/test_learning_integration.py
git commit -m "feat: unify learning across competitions"
```

---

### Task 5: Embed Per-League Models And Show Promotion Status

**Files:**
- Create: `website/learning_runtime.js`
- Create: `website/learning_embed.py`
- Create: `tests/test_learning_runtime.js`
- Modify: `website/pl_mobile_template.html`
- Modify: `website/update_pl_mobile.py`
- Modify: `tests/test_learning_integration.py`

**Interfaces:**
- Consumes: per-league model objects and `learning_history.json` from Task 4.
- Produces: `learningModelState(league)`, `activeWeights()`, `activeCalibration()`, and `scoreModelChoice()` in the browser.
- Produces: `embed_learning_runtime(template: str, models: dict, history: dict, runtime_source: str) -> str` in Python.

- [ ] **Step 1: Write the failing Node runtime test**

```javascript
// tests/test_learning_runtime.js
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const source = fs.readFileSync('website/learning_runtime.js', 'utf8');
const context = {
  D: {league: 'laliga'},
  EMBEDDED_MODELS: {
    pl: {active_strategy: 'baseline', factors: {strength: 0.11}, calibration: {goal_mult: 1}},
    laliga: {active_strategy: 'v4', factors: {strength: 0.24}, calibration: {goal_mult: 1.08}},
    wc: {active_strategy: 'v4', factors: {strength: 0.30}, calibration: {goal_mult: 1.12}},
  },
  LEARNING_HISTORY: {
    laliga: {model_status: {status: 'active', active_strategy: 'v4', candidate_strategy: 'baseline'}},
  },
};
vm.createContext(context);
vm.runInContext(source, context);
assert.strictEqual(context.activeWeights().strength, 0.24);
assert.strictEqual(context.activeCalibration().goal_mult, 1.08);
assert.strictEqual(context.scoreModelChoice().strategy, 'v4');
context.D.league = 'pl';
assert.strictEqual(context.activeWeights().strength, 0.11);
assert.strictEqual(context.scoreModelChoice().strategy, 'baseline');
console.log('learning runtime ok');
```

- [ ] **Step 2: Run the Node test and confirm the file is missing**

Run: `node tests/test_learning_runtime.js`

Expected: `ENOENT` for `website/learning_runtime.js`.

- [ ] **Step 3: Implement the runtime and embed it**

```javascript
// website/learning_runtime.js
function learningModelState(league){
  var models=typeof EMBEDDED_MODELS!=="undefined"?EMBEDDED_MODELS:{};
  return models[league]||{};
}
function activeWeights(){
  var state=learningModelState(D.league),fallback=typeof defaultWeights==="function"?defaultWeights():{};
  return state.factors||fallback;
}
function activeCalibration(){
  var state=learningModelState(D.league),fallback=typeof defaultCalibration==="function"?defaultCalibration():{};
  return state.calibration||fallback;
}
function scoreModelChoice(){
  var state=learningModelState(D.league),history=typeof LEARNING_HISTORY!=="undefined"?LEARNING_HISTORY:{};
  var status=(history[D.league]||{}).model_status||{};
  var strategy=status.active_strategy||state.active_strategy||"baseline";
  return {strategy:strategy,useV4:strategy==="v4",reason:status.reason||("active "+strategy)};
}
```

Add `/*__LEARNING_RUNTIME__*/` to the template, remove the duplicate inline implementations, and make `website/update_pl_mobile.py` embed:

```javascript
var EMBEDDED_MODELS={"pl":PL_MODEL,"laliga":LL_MODEL,"wc":WC_MODEL};
```

followed by the contents of `website/learning_runtime.js`.

Implement the build helper as:

```python
# website/learning_embed.py
import json


def embed_learning_runtime(template, models, history, runtime_source):
    model_json = json.dumps(models, ensure_ascii=False, separators=(",", ":"))
    history_json = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
    rendered = template.replace("/*__LEARNING_HISTORY__*/", "var LEARNING_HISTORY=" + history_json + ";")
    rendered = rendered.replace("/*__LEARNING_RUNTIME__*/", "var EMBEDDED_MODELS=" + model_json + ";\n" + runtime_source)
    return rendered
```

- [ ] **Step 4: Update the AI page status panel**

Modify `aiModelComparison` and `rAI` so each selected competition displays:

- active and candidate strategy;
- collecting, guarded, or promoted status;
- sample count and last training time;
- active/candidate points, winner accuracy, and exact accuracy;
- data-completeness percentage or an unavailable-input warning.

Remove the La Liga fallback to PL weights at `rAI`; when no LL rows exist, show its own base weights and `Collecting 0/30`.

Add this renderer and append it immediately after the AI hero in `rAI`:

```javascript
function aiModelStatus(lgData,modelCmp){
  var status=lgData.model_status||{},active=status.active_strategy||"baseline",candidate=status.candidate_strategy||(active==="v4"?"baseline":"v4");
  var models=(modelCmp&&modelCmp.models)||{};
  var activeBox=models[active]||{},candidateBox=models[candidate]||{};
  var total=+(modelCmp&&modelCmp.total||0),state=status.status||"collecting";
  var label=state==="promote"?"Promoted":state==="winner_guard"?"Held: direction guard":state==="points_guard"?"Held: points guard":"Collecting "+Math.min(total,30)+"/30";
  var complete=lgData.data_completeness_pct==null?100:+lgData.data_completeness_pct;
  return '<div class="sec" style="margin-bottom:.25rem">Model Status</div><div class="ainote"><b>'+label+'</b> · active '+active+' · candidate '+candidate+'</div>'+
    '<div class="aimetrics">'+
      '<div class="aimetric"><span>Active '+active+'</span><b>'+(activeBox.points||0)+' pts</b><em>'+(activeBox.winner_accuracy||0)+'% direction · '+(activeBox.exact_accuracy||0)+'% exact</em></div>'+
      '<div class="aimetric"><span>Candidate '+candidate+'</span><b>'+(candidateBox.points||0)+' pts</b><em>'+(candidateBox.winner_accuracy||0)+'% direction · '+(candidateBox.exact_accuracy||0)+'% exact</em></div>'+
      '<div class="aimetric"><span>Data complete</span><b>'+complete.toFixed(1)+'%</b><em>'+(complete<100?'Some trusted inputs unavailable':'All tracked inputs available')+'</em></div>'+
    '</div>';
}
```

Use `h+=aiModelStatus(lgData,modelCmp);` after the `aihero` block. Set `wts=activeWeights()` so the selected league never falls back to another league's state.

- [ ] **Step 5: Extend integration assertions for HTML model embedding**

Add this test to `tests/test_learning_integration.py`:

```python
def test_learning_runtime_embedding_is_complete(self):
    from website.learning_embed import embed_learning_runtime
    template = "/*__LEARNING_HISTORY__*/\n/*__LEARNING_RUNTIME__*/"
    runtime = "function activeWeights(){return EMBEDDED_MODELS.laliga.factors;}"
    rendered = embed_learning_runtime(
        template,
        {"laliga": {"factors": {"strength": 0.24}}},
        {"laliga": {"total_evaluated": 0}},
        runtime,
    )
    self.assertIn("var EMBEDDED_MODELS=", rendered)
    self.assertIn('"strength":0.24', rendered)
    self.assertNotIn("/*__LEARNING_RUNTIME__*/", rendered)
    self.assertEqual(1, rendered.count("function activeWeights"))
```

- [ ] **Step 6: Run JavaScript and Python tests**

Run: `node tests/test_learning_runtime.js`

Expected: `learning runtime ok`.

Run: `python -m unittest discover -s tests -v`

Expected: all Python tests pass.

- [ ] **Step 7: Commit runtime and UI changes**

```bash
git add website/learning_runtime.js website/learning_embed.py website/pl_mobile_template.html website/update_pl_mobile.py tests/test_learning_runtime.js tests/test_learning_integration.py
git commit -m "feat: show per-league learning status"
```

---

### Task 6: Make GitHub Publishing Atomic

**Files:**
- Modify: `website/update_pl_mobile.py`
- Modify: `.github/workflows/update-dashboard.yml`
- Create: `tests/test_publish_contract.py`

**Interfaces:**
- Consumes: generated files from Tasks 4-5.
- Produces: one workflow-owned commit containing every generated state file.

- [ ] **Step 1: Write a failing publishing contract test**

```python
# tests/test_publish_contract.py
import unittest
from pathlib import Path


class PublishContractTests(unittest.TestCase):
    def test_ci_build_does_not_use_contents_api(self):
        source = Path("website/update_pl_mobile.py").read_text(encoding="utf-8")
        self.assertIn('PUBLISH_TO_GITHUB = os.environ.get("PUBLISH_TO_GITHUB", "") == "1"', source)
        self.assertIn("if PUBLISH_TO_GITHUB and not IS_CI:", source)

    def test_workflow_stages_every_learning_file(self):
        workflow = Path(".github/workflows/update-dashboard.yml").read_text(encoding="utf-8")
        required = [
            "ai_predictions.json", "ai_weights.json",
            "ai_predictions_laliga.json", "ai_weights_laliga.json",
            "ai_predictions_wc.json", "ai_weights_wc.json",
            "learning_history.json", "live.json", "index.html", "website/pl_mobile.html",
        ]
        for name in required:
            self.assertIn(name, workflow)
        self.assertNotIn("GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}", workflow)
```

- [ ] **Step 2: Run the contract test and confirm it fails**

Run: `python -m unittest tests.test_publish_contract -v`

Expected: failures because CI still enables the GitHub Contents API and the workflow omits La Liga state.

- [ ] **Step 3: Guard local publishing and remove CI uploads**

In `website/update_pl_mobile.py`, define:

```python
PUBLISH_TO_GITHUB = os.environ.get("PUBLISH_TO_GITHUB", "") == "1"
```

and run the existing Contents API block only under:

```python
if PUBLISH_TO_GITHUB and not IS_CI:
```

Do not pass `GITHUB_TOKEN` to the build step in GitHub Actions.

- [ ] **Step 4: Stage all generated files in one workflow commit**

Replace the workflow staging command with:

```bash
git add index.html website/pl_mobile.html live.json learning_history.json \
  ai_predictions.json ai_weights.json \
  ai_predictions_laliga.json ai_weights_laliga.json \
  ai_predictions_wc.json ai_weights_wc.json
```

Retain the existing rebase-and-retry push behavior and concurrency group.

- [ ] **Step 5: Run the contract and full suites**

Run: `python -m unittest tests.test_publish_contract -v`

Expected: 2 tests pass.

Run: `python -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 6: Commit atomic publishing**

```bash
git add website/update_pl_mobile.py .github/workflows/update-dashboard.yml tests/test_publish_contract.py
git commit -m "fix: publish learning state atomically"
```

---

### Task 7: Build, Regression Verification, And Publication

**Files:**
- Modify generated files only through the build: `index.html`, `website/pl_mobile.html`, `live.json`, `learning_history.json`, prediction/model JSON files.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified and published GitHub Pages state.

- [ ] **Step 1: Run the complete automated suite**

Run: `python -m unittest discover -s tests -v`

Expected: every test passes with zero failures and zero errors.

- [ ] **Step 2: Build the dashboard**

Run: `python website/update_pl_mobile.py`

Expected output includes one learning summary for each of `pl`, `laliga`, and `wc`, paths for `website/pl_mobile.html`, `index.html`, and `live.json`, and no automatic GitHub upload.

- [ ] **Step 3: Validate every inline JavaScript block**

Run:

```bash
node -e "const fs=require('fs'),vm=require('vm');const h=fs.readFileSync('index.html','utf8');const s=[...h.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)].map(x=>x[1]).filter(x=>x.trim());s.forEach((x,i)=>new vm.Script(x,{filename:'inline-'+i+'.js'}));console.log('scripts ok',s.length)"
```

Expected: `scripts ok` followed by the number of inline scripts.

- [ ] **Step 4: Validate generated learning state**

Run:

```bash
python -m json.tool ai_predictions.json > NUL
python -m json.tool ai_weights.json > NUL
python -m json.tool ai_predictions_laliga.json > NUL
python -m json.tool ai_weights_laliga.json > NUL
python -m json.tool ai_predictions_wc.json > NUL
python -m json.tool ai_weights_wc.json > NUL
python -m json.tool learning_history.json > NUL
```

Expected: every command exits `0`.

- [ ] **Step 5: Inspect scope and preserve unrelated changes**

Run: `git status -sb`

Run: `git diff --check`

Run: `git diff --stat HEAD~6..HEAD`

Expected: no whitespace errors; only the planned engine, tests, template, workflow, documentation, and generated learning files changed.

- [ ] **Step 6: Commit generated artifacts when changed**

```bash
git add index.html website/pl_mobile.html live.json learning_history.json \
  ai_predictions.json ai_weights.json \
  ai_predictions_laliga.json ai_weights_laliga.json \
  ai_predictions_wc.json ai_weights_wc.json
git commit -m "build: refresh unified learning data"
```

If the build produces no staged change, skip this commit and record that fact in the final verification report.

- [ ] **Step 7: Rebase onto the latest automated main and rerun verification**

Run: `git fetch origin main`

Run: `git rebase origin/main`

Resolve only generated-state conflicts by rebuilding from the rebased source. Never discard source, test, documentation, or user changes.

Run: `python -m unittest discover -s tests -v`

Run: `python website/update_pl_mobile.py`

Run the inline JavaScript validation from Step 3 again.

Expected: all three commands succeed after the rebase.

- [ ] **Step 8: Push with reusable Git authorization**

Run: `git push origin main`

Request a reusable approval scoped to the `git push` prefix. Git Credential Manager supplies GitHub credentials; no token is written to the repository.

- [ ] **Step 9: Verify the remote commit**

Run: `git ls-remote origin refs/heads/main`

Expected: the returned SHA equals local `git rev-parse HEAD`.
