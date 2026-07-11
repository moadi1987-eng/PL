import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from website.league_learning import (
    atomic_save_json,
    comparison_summary,
    competition_rule,
    eligible_to_lock,
    evolve_competition_state,
    load_json_state,
    normalize_prediction_store,
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
                "locked": True,
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

    def test_missing_winner_counts_never_promote_after_sample_threshold(self):
        comparison = {
            "total": 30,
            "active_strategy": "baseline",
            "candidate_strategy": "v4",
            "models": {
                "baseline": {"points": 60, "winner_accuracy": 70.0},
                "v4": {"points": 70, "winner_accuracy": 70.0},
            },
        }
        decision = promotion_decision(comparison)
        self.assertFalse(decision["promote"])
        self.assertEqual("winner_guard", decision["status"])

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

    def test_unlocked_and_incomplete_rows_do_not_count_toward_sample_gate(self):
        rows = []
        for match_id in range(29):
            fixture = {"hs": 1, "as": 0, "fin": True}
            rows.append({
                "match_id": match_id,
                "locked": True,
                "fixture": fixture,
                "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
                "picks": {
                    "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                    "v4": {"winner": "home", "home_score": 1, "away_score": 0},
                },
            })
        rows.append({
            "match_id": 29,
            "locked": False,
            "fixture": {"hs": 1, "as": 0, "fin": True},
            "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
            "picks": {
                "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                "v4": {"winner": "home", "home_score": 1, "away_score": 0},
            },
        })
        rows.append({
            "match_id": 30,
            "locked": True,
            "fixture": {"hs": 1, "as": 0, "fin": False},
            "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
            "picks": {
                "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                "v4": {"winner": "home", "home_score": 1, "away_score": 0},
            },
        })
        comparison = comparison_summary(rows, "baseline", "v4")
        self.assertEqual(29, comparison["total"])
        self.assertEqual("collecting", promotion_decision(comparison)["status"])

    def test_promotion_uses_exact_winner_counts_at_rounded_boundary(self):
        comparison = {
            "total": 2000,
            "active_strategy": "baseline",
            "candidate_strategy": "v4",
            "models": {
                "baseline": {"points": 60, "winner_correct": 1000, "winner_accuracy": 50.0},
                "v4": {"points": 70, "winner_correct": 999, "winner_accuracy": 50.0},
            },
        }
        self.assertEqual("winner_guard", promotion_decision(comparison)["status"])


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
        finished = {**self.fixture, "fin": True, "st": True, "hs": 1, "as": 0}
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


if __name__ == "__main__":
    unittest.main()
