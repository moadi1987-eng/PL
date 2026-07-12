import copy
import math
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import website.league_predictor as predictor
from website.league_predictor import (
    _poisson_grid,
    default_model_state,
    legacy_v4_pick,
    normalize_model_state,
    predict_league_snapshot,
    train_factor_model,
)


class LeaguePredictorTests(unittest.TestCase):
    def test_top_level_predictor_import_supports_direct_dashboard_build(self):
        root = Path(__file__).resolve().parents[1]
        website = root / "website"
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import league_predictor; print(league_predictor.competition_rule('pl', {}))",
                str(website),
            ],
            cwd=root / "tests",
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)

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
        self.assertEqual("pl", pl["league"])
        self.assertEqual("laliga", ll["league"])

    def test_missing_squad_data_is_recorded(self):
        snapshot = predict_league_snapshot(self.target, self.history + [self.target], self.teams, default_model_state("pl"), "pl")
        self.assertTrue(snapshot["missing"]["squad_availability"])

    def test_squad_data_requires_nonempty_availability_for_both_teams(self):
        incomplete = copy.deepcopy(self.teams)
        incomplete[1]["inj"] = []
        incomplete[2]["sq"] = ["available-player"]
        complete = copy.deepcopy(self.teams)
        complete[1]["inj"] = ["injured-player"]
        complete[2]["sq"] = ["available-player"]
        model = default_model_state("pl")
        self.assertTrue(predict_league_snapshot(self.target, self.history, incomplete, model, "pl")["missing"]["squad_availability"])
        self.assertFalse(predict_league_snapshot(self.target, self.history, complete, model, "pl")["missing"]["squad_availability"])

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
        self.assertEqual("laliga", trained["league"])

    def test_training_exactly_normalizes_factor_weights_after_rounding(self):
        model = default_model_state("pl")
        rows = [{
            "actual_winner": "home", "fixture": {"hs": 2, "as": 0},
            "factor_edges": {"strength": 0.8, "form": 0.4},
            "expected_home_goals": 1.2, "expected_away_goals": 1.1,
        } for _ in range(3)]
        trained = train_factor_model(model, rows)
        self.assertLess(abs(sum(trained["factors"].values()) - 1.0), 1e-12)

    def test_training_rounding_keeps_skewed_factor_weights_nonnegative(self):
        model = default_model_state("pl")
        keys = tuple(model["factors"])
        model["factors"] = {
            **{key: 0.10006 for key in keys[:9]},
            keys[9]: 0.09945,
            keys[10]: 0.00001,
        }
        trained = train_factor_model(model, [])
        self.assertTrue(all(value >= 0 for value in trained["factors"].values()))
        self.assertLess(abs(sum(trained["factors"].values()) - 1.0), 1e-12)

    def test_training_ignores_malformed_rows_for_calibration_and_metadata(self):
        model = default_model_state("pl")
        valid_row = {
            "actual_winner": "home", "fixture": {"hs": 3, "as": 1},
            "factor_edges": {"strength": 0.8},
            "expected_home_goals": 1.2, "expected_away_goals": 1.1,
        }
        trained_valid = train_factor_model(model, [valid_row])
        trained_mixed = train_factor_model(model, [valid_row, None])
        self.assertEqual(trained_valid["calibration"], trained_mixed["calibration"])
        self.assertEqual(trained_valid["factors"], trained_mixed["factors"])
        self.assertEqual(1, trained_mixed["meta"]["trained_matches"])
        self.assertEqual(1, trained_mixed["meta"]["last_batch_size"])

    def test_normalization_sanitizes_malformed_model_numbers(self):
        model = normalize_model_state({
            "factors": {"strength": float("nan"), "form": -4, "position": "bad"},
            "calibration": {"goal_mult": 0, "home_goal_bias": float("inf"), "draw_bias": -2},
            "meta": {"trained_matches": "bad"},
        }, "pl")
        snapshot = predict_league_snapshot(self.target, self.history, self.teams, model, "pl")
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in snapshot["probabilities"].values()))
        self.assertAlmostEqual(100.0, sum(snapshot["probabilities"].values()), places=1)

    def test_legacy_v4_pick_always_has_a_valid_scoreline(self):
        pick = legacy_v4_pick({"home_win_pct": "bad", "draw_pct": None, "away_win_pct": -20, "home_score": -5, "away_score": "bad"})
        self.assertIn(pick["winner"], {"home", "away", "draw"})
        self.assertGreaterEqual(pick["home_score"], 0)
        self.assertGreaterEqual(pick["away_score"], 0)

    def test_training_only_updates_the_passed_model(self):
        pl = default_model_state("pl")
        laliga = default_model_state("laliga")
        trained = train_factor_model(pl, [])
        self.assertEqual(laliga, default_model_state("laliga"))
        self.assertEqual("pl", trained["league"])

    def test_poisson_grid_cells_and_outcomes_share_normalized_mass(self):
        grid, outcomes = _poisson_grid(4.4, 4.4)
        self.assertAlmostEqual(1.0, sum(grid.values()), places=12)
        self.assertAlmostEqual(outcomes["home"], sum(value for (home, away), value in grid.items() if home > away), places=12)
        self.assertAlmostEqual(outcomes["draw"], sum(value for (home, away), value in grid.items() if home == away), places=12)
        self.assertAlmostEqual(outcomes["away"], sum(value for (home, away), value in grid.items() if home < away), places=12)

    def test_baseline_grid_matches_final_blended_outcome_probabilities(self):
        model = default_model_state("pl")
        model["calibration"].update({"goal_mult": 0.82, "home_goal_bias": -1.5, "away_goal_bias": -1.5})
        captured = {}
        original = predictor._expected_points_pick

        def capture(grid, outcomes, rule):
            captured["grid"] = grid
            captured["outcomes"] = outcomes
            return original(grid, outcomes, rule)

        with patch.object(predictor, "_expected_points_pick", side_effect=capture):
            predict_league_snapshot(self.target, [self.target], self.teams, model, "pl")

        grid, outcomes = captured["grid"], captured["outcomes"]
        self.assertAlmostEqual(1.0, sum(grid.values()), places=12)
        for winner, predicate in {
            "home": lambda home, away: home > away,
            "draw": lambda home, away: home == away,
            "away": lambda home, away: home < away,
        }.items():
            cells = [value for (home, away), value in grid.items() if predicate(home, away)]
            self.assertAlmostEqual(outcomes[winner], sum(cells), places=12)
            self.assertTrue(all(value <= outcomes[winner] for value in cells))

    def test_training_rejects_semantically_invalid_rows_without_state_changes(self):
        model = default_model_state("pl")
        invalid_rows = [
            {"actual_winner": "home", "fixture": {"hs": -1, "as": 0}, "factor_edges": {"strength": 0.8}, "expected_home_goals": 1.2, "expected_away_goals": 1.1},
            {"actual_winner": "home", "fixture": {"hs": 2, "as": 0}, "factor_edges": {"strength": 0.8}, "expected_home_goals": -0.1, "expected_away_goals": 1.1},
            {"actual_winner": "draw", "fixture": {"hs": 2, "as": 0}, "factor_edges": {"strength": 0.8}, "expected_home_goals": 1.2, "expected_away_goals": 1.1},
        ]
        trained = train_factor_model(model, invalid_rows)
        self.assertEqual(model["factors"], trained["factors"])
        self.assertEqual(model["calibration"], trained["calibration"])
        self.assertEqual(model["meta"], trained["meta"])

    def test_training_counts_only_semantically_valid_rows_in_mixed_batch(self):
        model = default_model_state("pl")
        valid_row = {"actual_winner": "home", "fixture": {"hs": 2, "as": 0}, "factor_edges": {"strength": 0.8}, "expected_home_goals": 1.2, "expected_away_goals": 1.1}
        invalid_row = {"actual_winner": "away", "fixture": {"hs": 2, "as": 0}, "factor_edges": {"strength": 0.8}, "expected_home_goals": 1.2, "expected_away_goals": 1.1}
        trained = train_factor_model(model, [valid_row, invalid_row])
        self.assertEqual(1, trained["meta"]["trained_matches"])
        self.assertEqual(1, trained["meta"]["last_batch_size"])
        self.assertNotEqual("", trained["meta"]["last_trained_at"])

    def test_reweight_grid_assigns_positive_targets_when_source_mass_is_zero(self):
        adjusted = predictor._reweight_score_grid(
            {(1, 0): 0.0, (0, 0): 1.0},
            {"home": 0.0, "draw": 1.0, "away": 0.0},
            {"home": 0.2, "draw": 0.3, "away": 0.5},
        )
        self.assertAlmostEqual(1.0, sum(adjusted.values()), places=12)
        self.assertAlmostEqual(0.2, sum(value for (home, away), value in adjusted.items() if home > away), places=12)
        self.assertAlmostEqual(0.3, sum(value for (home, away), value in adjusted.items() if home == away), places=12)
        self.assertAlmostEqual(0.5, sum(value for (home, away), value in adjusted.items() if home < away), places=12)
        self.assertEqual(0.5, adjusted[(0, 1)])
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in adjusted.values()))

    def test_training_rejects_invalid_factor_edges_without_state_changes(self):
        model = default_model_state("pl")
        invalid_rows = [
            {"actual_winner": "home", "fixture": {"hs": 2, "as": 0}, "factor_edges": ["strength"], "expected_home_goals": 1.2, "expected_away_goals": 1.1},
            {"actual_winner": "home", "fixture": {"hs": 2, "as": 0}, "factor_edges": {"strength": "bad"}, "expected_home_goals": 1.2, "expected_away_goals": 1.1},
            {"actual_winner": "home", "fixture": {"hs": 2, "as": 0}, "factor_edges": {"strength": float("inf")}, "expected_home_goals": 1.2, "expected_away_goals": 1.1},
        ]
        trained = train_factor_model(model, invalid_rows)
        self.assertEqual(model["factors"], trained["factors"])
        self.assertEqual(model["calibration"], trained["calibration"])
        self.assertEqual(model["meta"], trained["meta"])

    def test_training_rejects_explicit_falsy_factor_edges_and_preserves_precision(self):
        model = default_model_state("pl")
        model["factors"]["form"] = 0.150001
        invalid_rows = [
            {"actual_winner": "home", "fixture": {"hs": 2, "as": 0}, "factor_edges": value, "expected_home_goals": 1.2, "expected_away_goals": 1.1}
            for value in ([], "", 0, None)
        ]
        trained = train_factor_model(model, invalid_rows)
        self.assertEqual(model, trained)


if __name__ == "__main__":
    unittest.main()
