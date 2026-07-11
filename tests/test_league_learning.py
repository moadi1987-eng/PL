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


if __name__ == "__main__":
    unittest.main()
