import ast
import copy
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import website.league_learning as learning
from website.github_atomic_publish import resolve_target_repository
from website.league_learning import (
    StateFileError,
    comparison_summary,
    competition_match_key,
    competition_rule,
    evolve_competition_state,
    load_json_state,
    run_persistent_competition,
    score_pick,
)
from website.league_predictor import default_model_state


class FinalReviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def empty_current_store(league="pl"):
        return {
            "version": learning.STORE_VERSION,
            "lifecycle_version": learning.STORE_VERSION,
            "league": league,
            "matches": {},
        }

    @staticmethod
    def snapshot_builder(fixture, model):
        return {
            "features": {"strength": 0.4},
            "factor_edges": {"strength": 1.0},
            "missing": {},
            "probabilities": {
                "baseline": {"home": 0.7, "draw": 0.2, "away": 0.1},
                "v4": {"home": 0.6, "draw": 0.2, "away": 0.2},
            },
            "expected_home_goals": 1.4,
            "expected_away_goals": 0.8,
            "picks": {
                "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                "v4": {"winner": "home", "home_score": 2, "away_score": 0},
            },
        }

    def fixture(self, *, season="2025-26", finished=False, score=(1, 0)):
        return {
            "id": 77,
            "source_fixture_id": 77,
            "season": season,
            "e": 4,
            "h": 1,
            "a": 2,
            "ko": (self.now + timedelta(hours=10)).isoformat(),
            "fin": finished,
            "st": finished,
            "hs": score[0] if finished else None,
            "as": score[1] if finished else None,
        }

    @staticmethod
    def representative_raw_pl_packet_store():
        return {
            "29": {
                "predictions": [
                    {
                        "match_id": 284,
                        "winner": "home",
                        "home_score": 1,
                        "away_score": 1,
                        "home_win_pct": 46.1,
                        "draw_pct": 12.4,
                        "away_win_pct": 41.5,
                    },
                    {
                        "match_id": 282,
                        "winner": "home",
                        "home_score": 2,
                        "away_score": 1,
                        "home_win_pct": 50.1,
                        "draw_pct": 11.5,
                        "away_win_pct": 38.4,
                    },
                ],
                "created_at": "2026-02-27 21:26:48",
                "version": 4,
                "model_updated": "2026-02-27 21:30:00",
                "custom_packet_field": {"keep": True},
            },
        }

    @staticmethod
    def raw_pl_packets_from_production_store(store):
        if "matches" not in store:
            return copy.deepcopy(store)
        matches = store["matches"]
        if isinstance(matches, dict):
            rows = matches.values()
        elif isinstance(matches, list):
            rows = matches
        else:
            raise AssertionError("canonical PL matches must be a collection")
        packets = {}
        for snapshot in rows:
            if (
                not isinstance(snapshot, dict)
                or snapshot.get("legacy") is not True
                or snapshot.get("lock_verified") is not False
                or not isinstance(snapshot.get("round"), int)
                or isinstance(snapshot.get("round"), bool)
            ):
                raise AssertionError("canonical PL row must be exact unverified legacy evidence")
            picks = snapshot.get("picks")
            baseline = picks.get("baseline") if isinstance(picks, dict) else None
            if (
                not isinstance(baseline, dict)
                or baseline.get("match_id") != snapshot.get("match_id")
            ):
                raise AssertionError("canonical PL baseline must preserve the raw prediction")
            metadata = snapshot.get("legacy_packet_metadata")
            if not isinstance(metadata, dict) or "predictions" in metadata:
                raise AssertionError("canonical PL row must preserve packet metadata")
            if snapshot.get("created_at", "") != metadata.get("created_at", ""):
                raise AssertionError("canonical PL packet timestamp must remain exact")
            round_key = str(snapshot["round"])
            packet = packets.setdefault(
                round_key,
                {**copy.deepcopy(metadata), "predictions": []},
            )
            if {key: value for key, value in packet.items() if key != "predictions"} != metadata:
                raise AssertionError("canonical PL packet metadata must agree within a round")
            packet["predictions"].append(copy.deepcopy(baseline))
        return packets

    @staticmethod
    def actual_pl_unverified_legacy_store():
        return {
            "version": 1,
            "lifecycle_version": 1,
            "league": "pl",
            "updated_at": "2026-07-12T21:00:37Z",
            "matches": {
                "284": {
                    "match_id": 284,
                    "league": "pl",
                    "round": 29,
                    "created_at": "2026-02-27 21:26:48",
                    "locked": True,
                    "legacy": True,
                    "lock_verified": False,
                    "checked": True,
                    "model_trained": True,
                    "features": {},
                    "missing": {"legacy_features": True},
                    "picks": {
                        "baseline": {
                            "match_id": 284,
                            "winner": "home",
                            "home_score": 1,
                            "away_score": 1,
                            "home_win_pct": 46.1,
                            "draw_pct": 12.4,
                            "away_win_pct": 41.5,
                        },
                        "v4": {
                            "winner": "home",
                            "home_score": 1,
                            "away_score": 0,
                            "reason": "tight-win",
                        },
                    },
                    "active_strategy_at_lock": "baseline",
                    "checked_at": "2026-07-12T21:00:37Z",
                    "actual_home_score": 2,
                    "actual_away_score": 0,
                    "actual_winner": "home",
                    "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
                    "evaluations": {
                        "baseline": {
                            "winner": "home",
                            "winner_correct": True,
                            "exact": False,
                            "points": 3,
                            "score": "1-1",
                        },
                        "v4": {
                            "winner": "home",
                            "winner_correct": True,
                            "exact": False,
                            "points": 3,
                            "score": "1-0",
                        },
                    },
                },
                "285": {
                    "match_id": 285,
                    "league": "pl",
                    "round": 29,
                    "created_at": "2026-02-27 21:26:48",
                    "locked": True,
                    "legacy": True,
                    "lock_verified": False,
                    "checked": True,
                    "model_trained": True,
                    "features": {},
                    "missing": {"legacy_features": True},
                    "picks": {
                        "baseline": {
                            "match_id": 285,
                            "winner": "away",
                            "home_score": 2,
                            "away_score": 2,
                            "home_win_pct": 41.6,
                            "draw_pct": 12.4,
                            "away_win_pct": 46.0,
                        },
                        "v4": {
                            "winner": "away",
                            "home_score": 1,
                            "away_score": 3,
                            "reason": "open-win",
                        },
                    },
                    "active_strategy_at_lock": "baseline",
                    "checked_at": "2026-07-12T21:00:37Z",
                    "actual_home_score": 0,
                    "actual_away_score": 1,
                    "actual_winner": "away",
                    "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
                    "evaluations": {
                        "baseline": {
                            "winner": "away",
                            "winner_correct": True,
                            "exact": False,
                            "points": 3,
                            "score": "2-2",
                        },
                        "v4": {
                            "winner": "away",
                            "winner_correct": True,
                            "exact": False,
                            "points": 3,
                            "score": "1-3",
                        },
                    },
                },
                "286": {
                    "match_id": 286,
                    "league": "pl",
                    "round": 29,
                    "created_at": "2026-02-27 21:26:48",
                    "locked": True,
                    "legacy": True,
                    "lock_verified": False,
                    "checked": True,
                    "model_trained": True,
                    "features": {},
                    "missing": {"legacy_features": True},
                    "picks": {
                        "baseline": {
                            "match_id": 286,
                            "winner": "home",
                            "home_score": 1,
                            "away_score": 1,
                            "home_win_pct": 49.1,
                            "draw_pct": 11.7,
                            "away_win_pct": 39.2,
                        },
                        "v4": {
                            "winner": "home",
                            "home_score": 1,
                            "away_score": 0,
                            "reason": "tight-win",
                        },
                    },
                    "active_strategy_at_lock": "baseline",
                    "checked_at": "2026-07-12T21:00:37Z",
                    "actual_home_score": 0,
                    "actual_away_score": 1,
                    "actual_winner": "away",
                    "rule": {"key": "league", "result": 3, "exact": 5, "additive": True},
                    "evaluations": {
                        "baseline": {
                            "winner": "home",
                            "winner_correct": False,
                            "exact": False,
                            "points": 0,
                            "score": "1-1",
                        },
                        "v4": {
                            "winner": "home",
                            "winner_correct": False,
                            "exact": False,
                            "points": 0,
                            "score": "1-0",
                        },
                    },
                },
            },
        }

    @staticmethod
    def valid_checked_current_store():
        fixture = {"fin": True, "hs": 1, "as": 0}
        rule = competition_rule("pl", fixture)
        picks = {
            "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
            "v4": {"winner": "home", "home_score": 2, "away_score": 0},
        }
        return {
            "version": 1,
            "lifecycle_version": 1,
            "league": "pl",
            "matches": {
                "77": {
                    "match_id": 77,
                    "source_fixture_id": 77,
                    "match_key": "pl:2025-26:77",
                    "league": "pl",
                    "season": "2025-26",
                    "round": 4,
                    "lifecycle_version": 1,
                    "locked": True,
                    "lock_verified": True,
                    "checked": True,
                    "model_trained": True,
                    "active_strategy_at_lock": "baseline",
                    "features": {},
                    "missing": {},
                    "picks": picks,
                    "actual_home_score": 1,
                    "actual_away_score": 0,
                    "actual_winner": "home",
                    "rule": rule,
                    "evaluations": {
                        strategy: score_pick(pick, fixture, rule)
                        for strategy, pick in picks.items()
                    },
                },
            },
        }

    @staticmethod
    def valid_unchecked_current_store():
        store = FinalReviewLifecycleTests.valid_checked_current_store()
        snapshot = store["matches"]["77"]
        snapshot["checked"] = False
        snapshot["model_trained"] = False
        for key in (
            "actual_home_score",
            "actual_away_score",
            "actual_winner",
            "rule",
            "evaluations",
        ):
            snapshot.pop(key, None)
        return store

    @staticmethod
    def valid_checked_context_store(league, round_id, phase=None):
        fixture = {"fin": True, "hs": 1, "as": 0, "e": round_id}
        if phase == "group":
            fixture["grp"] = "A"
        rule = competition_rule(league, fixture)
        picks = {
            "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
            "v4": {"winner": "home", "home_score": 2, "away_score": 0},
        }
        season = "2026" if league == "wc" else "2025-26"
        snapshot = {
            "match_id": 77,
            "source_fixture_id": 77,
            "match_key": f"{league}:{season}:77",
            "league": league,
            "season": season,
            "round": round_id,
            "lifecycle_version": 1,
            "locked": True,
            "lock_verified": True,
            "checked": True,
            "model_trained": True,
            "active_strategy_at_lock": "baseline",
            "features": {},
            "missing": {},
            "picks": picks,
            "actual_home_score": 1,
            "actual_away_score": 0,
            "actual_winner": "home",
            "rule": rule,
            "evaluations": {
                strategy: score_pick(pick, fixture, rule)
                for strategy, pick in picks.items()
            },
        }
        if league == "wc":
            snapshot["phase"] = phase
        return {
            "version": 1,
            "lifecycle_version": 1,
            "league": league,
            "matches": {"77": snapshot},
        }

    def test_actual_pl_unverified_legacy_rows_pass_preflight_and_preserve_raw_content(self):
        raw = self.actual_pl_unverified_legacy_store()
        original = copy.deepcopy(raw)

        learning.validate_prediction_store(raw, "pl")
        normalized = learning.normalize_prediction_store(raw, "pl")

        self.assertEqual(list(original["matches"]), list(normalized["matches"]))
        for storage_key, source in original["matches"].items():
            migrated = normalized["matches"][storage_key]
            self.assertEqual(source, {key: migrated[key] for key in source})
            self.assertEqual(f"pl:2025-26:{storage_key}", migrated["match_key"])
            self.assertEqual("2025-26", migrated["season"])
            self.assertEqual(int(storage_key), migrated["source_fixture_id"])

    def test_raw_pl_packet_preflight_and_migration_preserve_historical_evidence(self):
        raw = self.representative_raw_pl_packet_store()
        original = copy.deepcopy(raw)

        learning.validate_prediction_store(raw, "pl")
        normalized = learning.normalize_prediction_store(raw, "pl")

        self.assertEqual(original, raw)
        snapshot = normalized["matches"]["284"]
        self.assertEqual(original["29"]["predictions"][0], snapshot["picks"]["baseline"])
        self.assertEqual(
            {key: value for key, value in original["29"].items() if key != "predictions"},
            snapshot["legacy_packet_metadata"],
        )
        self.assertEqual(29, snapshot["round"])
        self.assertEqual(original["29"]["created_at"], snapshot["created_at"])
        self.assertIs(snapshot["legacy"], True)
        self.assertIs(snapshot["lock_verified"], False)
        self.assertEqual("pl:2025-26:284", snapshot["match_key"])

    def test_all_current_raw_pl_rows_migrate_read_only_without_evidence_loss(self):
        path = Path(__file__).parents[1] / "ai_predictions.json"
        production_store = json.loads(path.read_text(encoding="utf-8"))
        production_original = copy.deepcopy(production_store)
        learning.validate_prediction_store(production_store, "pl")
        raw = self.raw_pl_packets_from_production_store(production_store)
        original = copy.deepcopy(raw)

        learning.validate_prediction_store(raw, "pl")
        normalized = learning.normalize_prediction_store(raw, "pl")

        self.assertEqual(production_original, production_store)
        self.assertEqual(original, raw)
        expected_ids = set()
        inconsistent_ids = set()
        for round_key, packet in original.items():
            if not str(round_key).isdigit():
                continue
            metadata = {key: value for key, value in packet.items() if key != "predictions"}
            for pick in packet["predictions"]:
                match_id = str(pick["match_id"])
                expected_ids.add(match_id)
                expected_winner = (
                    "home" if pick["home_score"] > pick["away_score"]
                    else "away" if pick["away_score"] > pick["home_score"]
                    else "draw"
                )
                if pick["winner"] != expected_winner:
                    inconsistent_ids.add(int(match_id))
                snapshot = normalized["matches"][match_id]
                self.assertEqual(pick, snapshot["picks"]["baseline"])
                self.assertEqual(metadata, snapshot["legacy_packet_metadata"])
                self.assertEqual(int(round_key), snapshot["round"])
                self.assertIs(snapshot["legacy"], True)
                self.assertIs(snapshot["lock_verified"], False)

        self.assertEqual(108, len(expected_ids))
        self.assertEqual(expected_ids, set(normalized["matches"]))
        self.assertEqual({284, 286, 285, 289, 280, 274, 273, 275, 271}, inconsistent_ids)

    def test_raw_pl_rows_never_train_compare_or_promote(self):
        path = Path(__file__).parents[1] / "ai_predictions.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        trainer_calls = []

        store, model, history, counts = evolve_competition_state(
            league="pl",
            fixtures=[],
            store=raw,
            model=default_model_state("pl"),
            snapshot_builder=lambda fixture, state: self.fail("raw archive must not relock"),
            model_trainer=lambda state, rows: trainer_calls.append(rows) or state,
            now=self.now,
        )

        inconsistent_ids = {"284", "286", "285", "289", "280", "274", "273", "275", "271"}
        self.assertTrue(inconsistent_ids.issubset(store["matches"]))
        self.assertTrue(all(
            store["matches"][match_id]["legacy"] is True
            and store["matches"][match_id]["lock_verified"] is False
            for match_id in inconsistent_ids
        ))
        self.assertEqual([], trainer_calls)
        self.assertEqual(0, counts["trained"])
        self.assertEqual(0, counts["promoted"])
        self.assertEqual(0, model["comparison"]["total"])
        self.assertFalse(model["status"]["promote"])
        self.assertEqual(0, history["total_evaluated"])

    def test_raw_packet_corruption_rejects_without_relaxing_modern_picks(self):
        cases = {}
        invalid_packet = self.representative_raw_pl_packet_store()
        invalid_packet["29"] = []
        cases["packet_container"] = invalid_packet
        invalid_predictions = self.representative_raw_pl_packet_store()
        invalid_predictions["29"]["predictions"] = {}
        cases["predictions_container"] = invalid_predictions
        invalid_row = self.representative_raw_pl_packet_store()
        invalid_row["29"]["predictions"][0] = []
        cases["row_container"] = invalid_row
        invalid_identity = self.representative_raw_pl_packet_store()
        invalid_identity["29"]["predictions"][0]["match_id"] = True
        cases["identity"] = invalid_identity
        for name, value in (("negative", -1), ("fractional", 1.5), ("boolean", True)):
            invalid_score = self.representative_raw_pl_packet_store()
            invalid_score["29"]["predictions"][0]["home_score"] = value
            cases[f"score_{name}"] = invalid_score
        invalid_winner = self.representative_raw_pl_packet_store()
        invalid_winner["29"]["predictions"][0]["winner"] = "invalid"
        cases["winner"] = invalid_winner
        duplicate = self.representative_raw_pl_packet_store()
        duplicate["30"] = {
            "predictions": [copy.deepcopy(duplicate["29"]["predictions"][0])],
            "created_at": "later",
        }
        cases["duplicate"] = duplicate

        for name, raw in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(raw, "pl")

        modern = self.valid_checked_current_store()
        modern["matches"]["77"]["picks"]["baseline"].update(
            winner="home", home_score=1, away_score=1,
        )
        with self.assertRaises(learning.StateConsistencyError):
            learning.validate_prediction_store(modern, "pl")

    def test_prediction_schema_markers_without_matches_fail_closed(self):
        marker_cases = {
            "version": {"version": learning.STORE_VERSION},
            "lifecycle_version": {"lifecycle_version": learning.STORE_VERSION},
            "league": {"league": "pl"},
            "generation_id": {"generation_id": "generation"},
            "updated_at": {"updated_at": "2026-07-13T00:00:00Z"},
            "combined": {
                "version": learning.STORE_VERSION,
                "lifecycle_version": learning.STORE_VERSION,
                "league": "pl",
            },
        }

        for case, raw in marker_cases.items():
            original = copy.deepcopy(raw)
            for operation in (
                learning.validate_prediction_store,
                learning.normalize_prediction_store,
            ):
                with self.subTest(case=case, operation=operation.__name__):
                    with self.assertRaisesRegex(
                        learning.StateConsistencyError,
                        "current prediction store is missing matches",
                    ):
                        operation(raw, "pl")
                    self.assertEqual(original, raw)

    def test_prediction_schema_rejects_unknown_root_metadata_and_existing_empty_object(self):
        for payload_name, payload in (
            ("object", {"keep": True}),
            ("list", ["keep"]),
            ("scalar", "keep"),
        ):
            raw = self.representative_raw_pl_packet_store()
            raw["metadata"] = payload
            original = copy.deepcopy(raw)
            for operation in (
                learning.validate_prediction_store,
                learning.normalize_prediction_store,
            ):
                with self.subTest(payload=payload_name, operation=operation.__name__):
                    with self.assertRaisesRegex(
                        learning.StateConsistencyError,
                        "unknown prediction store root key",
                    ):
                        operation(raw, "pl")
                    self.assertEqual(original, raw)

        for operation in (
            learning.validate_prediction_store,
            learning.normalize_prediction_store,
        ):
            with self.subTest(payload="existing_empty", operation=operation.__name__):
                with self.assertRaisesRegex(
                    learning.StateConsistencyError,
                    "empty prediction store is ambiguous",
                ):
                    operation({}, "pl")

    def test_valid_current_empty_prediction_store_still_passes(self):
        raw = {
            "version": learning.STORE_VERSION,
            "lifecycle_version": learning.STORE_VERSION,
            "league": "pl",
            "generation_id": "generation",
            "updated_at": "2026-07-13T00:00:00Z",
            "matches": {},
        }
        original = copy.deepcopy(raw)

        learning.validate_prediction_store(raw, "pl")
        normalized = learning.normalize_prediction_store(raw, "pl")

        self.assertEqual(original, raw)
        self.assertEqual(original, normalized)

    def test_damaged_versioned_store_aborts_before_training_journal_save_or_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            history_path = root / "history.json"
            pending_path = Path(str(model_path) + ".pending")
            prediction_path.write_bytes(
                b'{"version":1,"lifecycle_version":1,"league":"pl"}'
            )
            model_path.write_text(json.dumps(default_model_state("pl")), encoding="utf-8")
            history_path.write_bytes(b'{"other":{"keep":true}}')
            tracked_paths = (prediction_path, model_path, history_path)
            before = {path: path.read_bytes() for path in tracked_paths}
            trainer_calls = []
            save_calls = []

            with patch.object(
                learning,
                "atomic_save_json",
                side_effect=lambda path, value: save_calls.append(path),
            ):
                with self.assertRaisesRegex(
                    learning.StateConsistencyError,
                    "current prediction store is missing matches",
                ):
                    run_persistent_competition(
                        league="pl",
                        fixtures=[],
                        teams={},
                        prediction_path=str(prediction_path),
                        model_path=str(model_path),
                        history_path=str(history_path),
                        history={},
                        now=self.now,
                        snapshot_builder=lambda fixture, state: self.fail(
                            "damaged state must not lock"
                        ),
                        model_trainer=lambda state, rows: trainer_calls.append(rows) or state,
                        default_model=default_model_state("pl"),
                    )

            self.assertEqual([], trainer_calls)
            self.assertEqual([], save_calls)
            self.assertFalse(pending_path.exists())
            self.assertEqual(before, {path: path.read_bytes() for path in tracked_paths})

    def test_missing_prediction_file_default_initializes_current_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            history_path = root / "history.json"
            trainer_calls = []

            run_persistent_competition(
                league="pl",
                fixtures=[],
                teams={},
                prediction_path=str(prediction_path),
                model_path=str(model_path),
                history_path=str(history_path),
                history={},
                now=self.now,
                snapshot_builder=lambda fixture, state: self.fail(
                    "empty state must not lock without fixtures"
                ),
                model_trainer=lambda state, rows: trainer_calls.append(rows) or state,
                default_model=default_model_state("pl"),
            )

            persisted = json.loads(prediction_path.read_text(encoding="utf-8"))
            self.assertEqual([], trainer_calls)
            self.assertEqual(learning.STORE_VERSION, persisted["version"])
            self.assertEqual(learning.STORE_VERSION, persisted["lifecycle_version"])
            self.assertEqual("pl", persisted["league"])
            self.assertEqual({}, persisted["matches"])

    def test_actual_pl_unverified_legacy_rows_keep_history_but_never_train_or_compare(self):
        original = self.actual_pl_unverified_legacy_store()
        trainer_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            history_path = root / "history.json"
            prediction_path.write_text(json.dumps(original), encoding="utf-8")

            history, counts, model = run_persistent_competition(
                league="pl",
                fixtures=[],
                teams={},
                prediction_path=str(prediction_path),
                model_path=str(model_path),
                history_path=str(history_path),
                history={},
                now=self.now,
                snapshot_builder=lambda fixture, state: self.fail("legacy rows must not relock"),
                model_trainer=lambda state, rows: trainer_calls.append(copy.deepcopy(rows)) or state,
                default_model=default_model_state("pl"),
            )
            persisted = json.loads(prediction_path.read_text(encoding="utf-8"))

        self.assertEqual([], trainer_calls)
        self.assertEqual(0, counts["trained"])
        self.assertEqual(0, model["comparison"]["total"])
        self.assertEqual("collecting", model["status"]["status"])
        self.assertEqual(3, history["pl"]["total_evaluated"])
        self.assertEqual(3, sum(row["total"] for row in history["pl"]["gw_results"]))
        self.assertEqual(list(original["matches"]), list(persisted["matches"]))
        for storage_key, source in original["matches"].items():
            migrated = persisted["matches"][storage_key]
            self.assertEqual(source, {key: migrated[key] for key in source})

    def test_checked_current_snapshot_requires_canonical_evaluations(self):
        cases = (
            "empty",
            "missing_strategy",
            "evaluation_winner",
            "winner_correct",
            "exact",
            "score",
            "points",
            "missing_actual_winner",
        )
        for case in cases:
            with self.subTest(case=case):
                store = self.valid_checked_current_store()
                snapshot = store["matches"]["77"]
                if case == "empty":
                    snapshot["evaluations"] = {}
                elif case == "missing_strategy":
                    del snapshot["evaluations"]["v4"]
                elif case == "missing_actual_winner":
                    del snapshot["actual_winner"]
                else:
                    invalid_values = {
                        "evaluation_winner": ("winner", "away"),
                        "winner_correct": ("winner_correct", False),
                        "exact": ("exact", False),
                        "score": ("score", "01-00"),
                        "points": ("points", 3),
                    }
                    key, value = invalid_values[case]
                    snapshot["evaluations"]["baseline"][key] = value

                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "pl")

    def test_lifecycle_compatibility_rejects_every_nonexact_flag_claim(self):
        cases = (
            ("verified_legacy", True, True),
            ("unverified_nonlegacy", False, False),
            ("unverified_current", None, False),
        )
        for name, legacy, lock_verified in cases:
            with self.subTest(case=name):
                store = self.valid_checked_current_store()
                snapshot = store["matches"]["77"]
                if legacy is None:
                    snapshot.pop("legacy", None)
                else:
                    snapshot["legacy"] = legacy
                snapshot["lock_verified"] = lock_verified

                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "pl")

    def test_lifecycle_boundary_accepts_exact_legacy_modern_and_pre_normalized_wc_controls(self):
        exact_legacy = self.actual_pl_unverified_legacy_store()
        modern = self.valid_checked_current_store()
        pre_normalized_wc = {
            "version": 4,
            "matches": {
                "91": {
                    "match_id": 91,
                    "day": 6,
                    "phase": "group",
                    "winner": "home",
                    "home_score": 2,
                    "away_score": 1,
                    "checked": True,
                    "actual_home_score": 2,
                    "actual_away_score": 1,
                    "v4_shadow": {"winner": "home", "home_score": 1, "away_score": 0},
                },
            },
        }

        learning.validate_prediction_store(exact_legacy, "pl")
        learning.validate_prediction_store(modern, "pl")
        learning.validate_prediction_store(pre_normalized_wc, "wc")
        migrated = learning.normalize_prediction_store(pre_normalized_wc, "wc")

        self.assertTrue(migrated["matches"]["91"]["legacy"])
        self.assertFalse(migrated["matches"]["91"]["lock_verified"])
        self.assertEqual(6, migrated["matches"]["91"]["round"])

    def test_modern_snapshot_requires_exact_verified_lock_invariant(self):
        cases = (
            ("missing_locked", self.valid_checked_current_store, lambda row: row.pop("locked")),
            ("false_locked", self.valid_unchecked_current_store, lambda row: row.update(locked=False)),
            ("checked_unlocked", self.valid_checked_current_store, lambda row: row.update(locked=False)),
            (
                "missing_lock_verified",
                self.valid_checked_current_store,
                lambda row: row.pop("lock_verified"),
            ),
            (
                "false_lock_verified",
                self.valid_checked_current_store,
                lambda row: row.update(lock_verified=False),
            ),
        )
        for name, store_builder, mutate in cases:
            with self.subTest(case=name):
                store = store_builder()
                mutate(store["matches"]["77"])
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "pl")

        learning.validate_prediction_store(self.valid_checked_current_store(), "pl")
        learning.validate_prediction_store(self.actual_pl_unverified_legacy_store(), "pl")

    def test_invalid_preexisting_lock_claim_fails_before_occupying_fixture_identity(self):
        store = self.valid_unchecked_current_store()
        store["matches"]["77"]["locked"] = False
        original = copy.deepcopy(store)
        builder_calls = []
        trainer_calls = []

        with self.assertRaises(learning.StateConsistencyError):
            evolve_competition_state(
                league="pl",
                fixtures=[self.fixture()],
                store=store,
                model=default_model_state("pl"),
                snapshot_builder=lambda fixture, model: builder_calls.append(fixture) or self.snapshot_builder(
                    fixture, model,
                ),
                model_trainer=lambda model, rows: trainer_calls.append(rows) or model,
                now=self.now,
            )

        self.assertEqual(original, store)
        self.assertEqual([], builder_calls)
        self.assertEqual([], trainer_calls)

    def test_defensive_lock_invariant_precedes_empty_ledger_evidence(self):
        store = self.valid_checked_current_store()
        snapshot = store["matches"]["77"]
        snapshot["locked"] = False
        snapshot["model_trained"] = False
        model = default_model_state("pl")
        model["applied_ledger_version"] = learning.LEDGER_VERSION
        model["applied_match_keys"] = []
        trainer_calls = []

        with patch.object(
            learning,
            "normalize_prediction_store",
            return_value=copy.deepcopy(store),
        ), patch.object(
            learning,
            "comparison_summary",
            wraps=learning.comparison_summary,
        ) as comparison:
            with self.assertRaises(learning.StateConsistencyError):
                evolve_competition_state(
                    league="pl",
                    fixtures=[],
                    store=store,
                    model=model,
                    snapshot_builder=lambda fixture, state: self.fail("no fixture should lock"),
                    model_trainer=lambda state, rows: trainer_calls.append(rows) or state,
                    now=self.now,
                )

        self.assertEqual([], trainer_calls)
        comparison.assert_not_called()

    def test_valid_current_snapshot_trains_with_present_empty_ledger(self):
        store = self.valid_checked_current_store()
        store["matches"]["77"]["model_trained"] = False
        model = default_model_state("pl")
        model["applied_ledger_version"] = learning.LEDGER_VERSION
        model["applied_match_keys"] = []
        trainer_calls = []

        store, model, history, counts = evolve_competition_state(
            league="pl",
            fixtures=[],
            store=store,
            model=model,
            snapshot_builder=lambda fixture, state: self.fail("no fixture should lock"),
            model_trainer=lambda state, rows: trainer_calls.append(copy.deepcopy(rows)) or state,
            now=self.now,
        )

        self.assertEqual(1, len(trainer_calls))
        self.assertEqual(["pl:2025-26:77"], model["applied_match_keys"])
        self.assertTrue(store["matches"]["77"]["model_trained"])
        self.assertEqual(1, counts["trained"])
        self.assertEqual(1, model["comparison"]["total"])
        self.assertEqual(1, history["total_evaluated"])

    def test_checked_modern_rules_are_bound_to_competition_and_every_wc_phase(self):
        wc_contexts = (
            ("group", 6),
            ("r32", 18),
            ("r16", 25),
            ("quarter", 28),
            ("semi", 32),
            ("third", 34),
            ("final", 35),
        )
        wc_rules = {
            phase: competition_rule(
                "wc",
                {"e": round_id, **({"grp": "A"} if phase == "group" else {})},
            )
            for phase, round_id in wc_contexts
        }

        for league in ("pl", "laliga"):
            with self.subTest(control=league):
                learning.validate_prediction_store(
                    self.valid_checked_context_store(league, 4), league,
                )
            with self.subTest(wrong_wc_rule=league):
                store = self.valid_checked_context_store(league, 4)
                self._replace_checked_rule(store, wc_rules["group"])
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, league)

        with self.subTest(wc_wrong_league_rule=True):
            store = self.valid_checked_context_store("wc", 6, "group")
            self._replace_checked_rule(store, competition_rule("pl", {"e": 6}))
            with self.assertRaises(learning.StateConsistencyError):
                learning.validate_prediction_store(store, "wc")

        phases = [phase for phase, _round_id in wc_contexts]
        for index, (phase, round_id) in enumerate(wc_contexts):
            with self.subTest(control=phase):
                learning.validate_prediction_store(
                    self.valid_checked_context_store("wc", round_id, phase), "wc",
                )
            wrong_phase = phases[(index + 1) % len(phases)]
            with self.subTest(expected=phase, wrong=wrong_phase):
                store = self.valid_checked_context_store("wc", round_id, phase)
                self._replace_checked_rule(store, wc_rules[wrong_phase])
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "wc")

    def test_wc_lock_persists_phase_and_evaluation_ignores_mutable_feed_phase(self):
        upcoming = self.fixture(season="2026")
        upcoming.update({"e": 18, "grp": None})
        store, model, _history, _counts = evolve_competition_state(
            league="wc",
            fixtures=[upcoming],
            store={"version": 1, "league": "wc", "matches": {}},
            model=default_model_state("wc"),
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )

        snapshot = store["matches"]["77"]
        self.assertEqual("r32", snapshot.get("phase"))
        self.assertEqual(18, snapshot["round"])

        completed = copy.deepcopy(upcoming)
        completed.update({"e": 28, "fin": True, "st": True, "hs": 1, "as": 0})
        store, _model, history, counts = evolve_competition_state(
            league="wc",
            fixtures=[completed],
            store=store,
            model=model,
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )

        snapshot = store["matches"]["77"]
        self.assertEqual("r32", snapshot["rule"]["key"])
        self.assertEqual(5, history["gw_results"][0]["points"])
        self.assertEqual(1, counts["checked"])

    def test_wc_group_lock_requires_immutable_round_context(self):
        upcoming = self.fixture(season="2026")
        upcoming.pop("e")
        upcoming["grp"] = "A"

        store, _model, _history, counts = evolve_competition_state(
            league="wc",
            fixtures=[upcoming],
            store={"version": 1, "league": "wc", "matches": {}},
            model=default_model_state("wc"),
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )

        self.assertEqual({}, store["matches"])
        self.assertEqual(0, counts["locked"])
        self.assertEqual(1, counts["skipped"])

    def test_wc_group_rule_and_lock_accept_only_rounds_one_through_seventeen(self):
        invalid_rounds = (0, 18, 35, None, 1.5, -1, True)
        for round_id in invalid_rounds:
            with self.subTest(round=round_id):
                upcoming = self.fixture(season="2026")
                upcoming["grp"] = "A"
                if round_id is None:
                    upcoming.pop("e")
                else:
                    upcoming["e"] = round_id

                with self.assertRaises(ValueError):
                    competition_rule("wc", upcoming)
                store, _model, _history, counts = evolve_competition_state(
                    league="wc",
                    fixtures=[upcoming],
                    store={"version": 1, "league": "wc", "matches": {}},
                    model=default_model_state("wc"),
                    snapshot_builder=self.snapshot_builder,
                    model_trainer=lambda state, rows: state,
                    now=self.now,
                )
                self.assertEqual({}, store["matches"])
                self.assertEqual(0, counts["locked"])
                self.assertEqual(1, counts["skipped"])

        for round_id in (1, 17):
            with self.subTest(control=round_id):
                upcoming = self.fixture(season="2026")
                upcoming.update({"e": round_id, "grp": "A"})
                self.assertEqual("group", competition_rule("wc", upcoming)["key"])
                store, _model, _history, counts = evolve_competition_state(
                    league="wc",
                    fixtures=[upcoming],
                    store={"version": 1, "league": "wc", "matches": {}},
                    model=default_model_state("wc"),
                    snapshot_builder=self.snapshot_builder,
                    model_trainer=lambda state, rows: state,
                    now=self.now,
                )
                self.assertEqual("group", store["matches"]["77"]["phase"])
                self.assertEqual(round_id, store["matches"]["77"]["round"])
                self.assertEqual(1, counts["locked"])

        knockout = self.fixture(season="2026")
        knockout.update({"e": 18, "grp": None})
        self.assertEqual("r32", competition_rule("wc", knockout)["key"])
        store, _model, _history, counts = evolve_competition_state(
            league="wc",
            fixtures=[knockout],
            store={"version": 1, "league": "wc", "matches": {}},
            model=default_model_state("wc"),
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )
        self.assertEqual("r32", store["matches"]["77"]["phase"])
        self.assertEqual(1, counts["locked"])

    def test_loaded_checked_wc_group_accepts_only_rounds_one_through_seventeen(self):
        for round_id in (0, 18, 35, None, 1.5, -1, True):
            with self.subTest(round=round_id):
                store = self.valid_checked_context_store("wc", 1, "group")
                snapshot = store["matches"]["77"]
                if round_id is None:
                    snapshot.pop("round")
                else:
                    snapshot["round"] = round_id
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "wc")

        for round_id in (1, 17):
            with self.subTest(control=round_id):
                learning.validate_prediction_store(
                    self.valid_checked_context_store("wc", round_id, "group"),
                    "wc",
                )
        learning.validate_prediction_store(
            self.valid_checked_context_store("wc", 18, "r32"),
            "wc",
        )

    def test_loaded_unchecked_wc_context_rejects_invalid_group_rounds(self):
        for round_id in (0, 18, 35, None):
            with self.subTest(round=round_id):
                store = self.valid_checked_context_store("wc", 1, "group")
                snapshot = store["matches"]["77"]
                snapshot["checked"] = False
                snapshot["model_trained"] = False
                for key in (
                    "actual_home_score",
                    "actual_away_score",
                    "actual_winner",
                    "rule",
                    "evaluations",
                ):
                    snapshot.pop(key)
                if round_id is None:
                    snapshot.pop("round")
                else:
                    snapshot["round"] = round_id
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "wc")

        for phase, round_id in (("group", 1), ("group", 17), ("r32", 18)):
            with self.subTest(control=(phase, round_id)):
                store = self.valid_checked_context_store("wc", round_id, phase)
                snapshot = store["matches"]["77"]
                snapshot["checked"] = False
                snapshot["model_trained"] = False
                for key in (
                    "actual_home_score",
                    "actual_away_score",
                    "actual_winner",
                    "rule",
                    "evaluations",
                ):
                    snapshot.pop(key)
                learning.validate_prediction_store(store, "wc")

    @staticmethod
    def _replace_checked_rule(store, rule):
        snapshot = store["matches"]["77"]
        fixture = {
            "fin": True,
            "hs": snapshot["actual_home_score"],
            "as": snapshot["actual_away_score"],
        }
        snapshot["rule"] = copy.deepcopy(rule)
        snapshot["evaluations"] = {
            strategy: score_pick(pick, fixture, rule)
            for strategy, pick in snapshot["picks"].items()
        }

    def test_valid_checked_current_snapshot_remains_comparison_evidence(self):
        trainer_calls = []
        store, model, history, counts = evolve_competition_state(
            league="pl",
            fixtures=[],
            store=self.valid_checked_current_store(),
            model=default_model_state("pl"),
            snapshot_builder=lambda fixture, state: self.fail("checked row must not relock"),
            model_trainer=lambda state, rows: trainer_calls.append(copy.deepcopy(rows)) or state,
            now=self.now,
        )

        self.assertEqual([], trainer_calls)
        self.assertEqual(0, counts["trained"])
        self.assertEqual(1, model["comparison"]["total"])
        self.assertEqual(1, history["total_evaluated"])
        self.assertEqual(8, history["gw_results"][0]["points"])
        self.assertEqual(
            self.valid_checked_current_store()["matches"]["77"]["evaluations"],
            store["matches"]["77"]["evaluations"],
        )

    def test_contradictory_checked_current_evaluation_aborts_before_comparison_or_save(self):
        store = self.valid_checked_current_store()
        store["matches"]["77"]["evaluations"]["baseline"]["points"] = 3
        trainer_calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            history_path = root / "history.json"
            prediction_path.write_text(json.dumps(store), encoding="utf-8")
            before = prediction_path.read_bytes()

            with patch.object(learning, "comparison_summary", wraps=learning.comparison_summary) as comparison, patch.object(
                learning, "atomic_save_json", wraps=learning.atomic_save_json,
            ) as save:
                with self.assertRaises(learning.StateConsistencyError):
                    run_persistent_competition(
                        league="pl",
                        fixtures=[],
                        teams={},
                        prediction_path=str(prediction_path),
                        model_path=str(model_path),
                        history_path=str(history_path),
                        history={},
                        now=self.now,
                        snapshot_builder=lambda fixture, state: self.fail("checked row must not relock"),
                        model_trainer=lambda state, rows: trainer_calls.append(copy.deepcopy(rows)) or state,
                        default_model=default_model_state("pl"),
                    )

            comparison.assert_not_called()
            save.assert_not_called()
            self.assertEqual([], trainer_calls)
            self.assertEqual(before, prediction_path.read_bytes())
            self.assertFalse(model_path.exists())
            self.assertFalse(history_path.exists())

    def test_persistence_boundaries_recover_without_reapplying_training(self):
        for failed_name in ("weights.json", "predictions.json", "history.json"):
            with self.subTest(boundary=failed_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prediction_path = root / "predictions.json"
                model_path = root / "weights.json"
                history_path = root / "history.json"
                calls = []

                def trainer(model, rows):
                    calls.append([row["match_key"] for row in rows])
                    updated = copy.deepcopy(model)
                    meta = updated.setdefault("meta", {})
                    meta["application_count"] = meta.get("application_count", 0) + len(rows)
                    return updated

                run_persistent_competition(
                    league="pl",
                    fixtures=[self.fixture()],
                    teams={},
                    prediction_path=str(prediction_path),
                    model_path=str(model_path),
                    history_path=str(history_path),
                    history={},
                    now=self.now,
                    snapshot_builder=self.snapshot_builder,
                    model_trainer=trainer,
                    default_model=default_model_state("pl"),
                )

                real_save = learning.atomic_save_json
                failed = False

                def fail_once(path, value):
                    nonlocal failed
                    if Path(path).name == failed_name and not failed:
                        failed = True
                        raise OSError(f"injected {failed_name} failure")
                    return real_save(path, value)

                with patch.object(learning, "atomic_save_json", side_effect=fail_once):
                    with self.assertRaisesRegex(OSError, "injected"):
                        run_persistent_competition(
                            league="pl",
                            fixtures=[self.fixture(finished=True)],
                            teams={},
                            prediction_path=str(prediction_path),
                            model_path=str(model_path),
                            history_path=str(history_path),
                            history={},
                            now=self.now + timedelta(days=1),
                            snapshot_builder=self.snapshot_builder,
                            model_trainer=trainer,
                            default_model=default_model_state("pl"),
                        )

                history, _, _ = run_persistent_competition(
                    league="pl",
                    fixtures=[self.fixture(finished=True)],
                    teams={},
                    prediction_path=str(prediction_path),
                    model_path=str(model_path),
                    history_path=str(history_path),
                    history={},
                    now=self.now + timedelta(days=1, minutes=5),
                    snapshot_builder=self.snapshot_builder,
                    model_trainer=trainer,
                    default_model=default_model_state("pl"),
                )

                store = json.loads(prediction_path.read_text(encoding="utf-8"))
                model = json.loads(model_path.read_text(encoding="utf-8"))
                saved_history = json.loads(history_path.read_text(encoding="utf-8"))
                snapshot = next(iter(store["matches"].values()))
                match_key = competition_match_key("pl", "2025-26", 77)
                self.assertEqual([[match_key]], calls)
                self.assertEqual(1, model["meta"]["application_count"])
                self.assertEqual([match_key], model["applied_match_keys"])
                self.assertTrue(snapshot["model_trained"])
                self.assertEqual(match_key, snapshot["match_key"])
                generation = model["generation_id"]
                self.assertEqual(generation, store["generation_id"])
                self.assertEqual(generation, history["pl"]["generation_id"])
                self.assertEqual(generation, saved_history["pl"]["generation_id"])
                self.assertFalse(Path(str(model_path) + ".pending").exists())

    def test_legacy_full_history_pending_replays_only_owned_competition_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wc_prediction = root / "wc-predictions.json"
            wc_model = root / "wc-weights.json"
            history_path = root / "history.json"
            generation = "wc-generation"
            wc_prediction.write_text(
                json.dumps({"league": "wc", "generation_id": generation, "matches": {}}),
                encoding="utf-8",
            )
            wc_model.write_text(
                json.dumps({"league": "wc", "generation_id": generation}),
                encoding="utf-8",
            )
            history_path.write_text(
                json.dumps({"laliga": {"generation_id": "new-laliga", "total_evaluated": 9}}),
                encoding="utf-8",
            )
            pending_path = Path(str(wc_model) + ".pending")
            pending_path.write_text(json.dumps({
                "version": 1,
                "league": "wc",
                "generation_id": generation,
                "store": {"league": "wc", "generation_id": generation, "matches": {}},
                "model": {"league": "wc", "generation_id": generation},
                "history": {
                    "laliga": {"generation_id": "stale-laliga", "total_evaluated": 2},
                    "wc": {"generation_id": generation, "total_evaluated": 1},
                },
                "counts": {"locked": 0, "checked": 1, "trained": 1, "skipped": 0, "promoted": 0},
            }), encoding="utf-8")

            recovered = learning.recover_pending_competitions(
                [{
                    "league": "wc",
                    "prediction_path": str(wc_prediction),
                    "model_path": str(wc_model),
                }],
                history_path=str(history_path),
            )

            self.assertEqual("new-laliga", recovered["laliga"]["generation_id"])
            self.assertEqual(9, recovered["laliga"]["total_evaluated"])
            self.assertEqual(generation, recovered["wc"]["generation_id"])
            self.assertFalse(pending_path.exists())

    def test_all_pending_journals_validate_before_any_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "history.json"
            history_path.write_text('{"safe":{"keep":true}}', encoding="utf-8")
            configurations = []
            tracked_paths = [history_path]
            for league, generation in (("pl", "pl-generation"), ("wc", "wc-generation")):
                prediction_path = root / f"{league}-predictions.json"
                model_path = root / f"{league}-weights.json"
                pending_path = Path(str(model_path) + ".pending")
                store = {"league": league, "generation_id": generation, "matches": {}}
                model = {"league": league, "generation_id": generation}
                history_entry = {"generation_id": generation}
                counts = {}
                if league == "wc":
                    store["matches"] = {"77": "corrupt"}
                    model["factors"] = "corrupt"
                    history_entry["gw_results"] = ["corrupt"]
                    counts["trained"] = "corrupt"
                prediction_path.write_text(json.dumps(store), encoding="utf-8")
                model_path.write_text(json.dumps(model), encoding="utf-8")
                pending_path.write_text(json.dumps({
                    "version": 2,
                    "league": league,
                    "generation_id": generation,
                    "store": store,
                    "model": model,
                    "history_entry": history_entry,
                    "counts": counts,
                }), encoding="utf-8")
                configurations.append({
                    "league": league,
                    "prediction_path": str(prediction_path),
                    "model_path": str(model_path),
                })
                tracked_paths.extend((prediction_path, model_path, pending_path))
            before = {path: path.read_bytes() for path in tracked_paths}

            with self.assertRaises(learning.StateConsistencyError):
                learning.recover_pending_competitions(
                    configurations,
                    history_path=str(history_path),
                )

            self.assertEqual(before, {path: path.read_bytes() for path in tracked_paths})

    def test_structurally_invalid_loaded_roles_abort_before_training_replay_or_save(self):
        for role in ("prediction", "model", "history", "pending"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                prediction_path = root / "predictions.json"
                model_path = root / "weights.json"
                history_path = root / "history.json"
                pending_path = Path(str(model_path) + ".pending")
                fixture = self.fixture()
                run_persistent_competition(
                    league="pl", fixtures=[fixture], teams={},
                    prediction_path=str(prediction_path), model_path=str(model_path),
                    history_path=str(history_path), history={}, now=self.now,
                    snapshot_builder=self.snapshot_builder,
                    model_trainer=lambda model, rows: model,
                    default_model=default_model_state("pl"),
                )

                if role == "prediction":
                    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
                    prediction["matches"] = {"77": "corrupt"}
                    prediction_path.write_text(json.dumps(prediction), encoding="utf-8")
                elif role == "model":
                    model = json.loads(model_path.read_text(encoding="utf-8"))
                    model.update({
                        "active_strategy": "corrupt",
                        "factors": {"strength": "corrupt"},
                        "calibration": [],
                        "meta": "corrupt",
                    })
                    model_path.write_text(json.dumps(model), encoding="utf-8")
                elif role == "history":
                    history = json.loads(history_path.read_text(encoding="utf-8"))
                    history["pl"]["gw_results"] = ["corrupt"]
                    history_path.write_text(json.dumps(history), encoding="utf-8")
                else:
                    generation = "pending-generation"
                    store = json.loads(prediction_path.read_text(encoding="utf-8"))
                    model = json.loads(model_path.read_text(encoding="utf-8"))
                    history_entry = json.loads(history_path.read_text(encoding="utf-8"))["pl"]
                    store.update({"generation_id": generation, "matches": {"77": "corrupt"}})
                    model.update({"generation_id": generation, "factors": "corrupt"})
                    history_entry.update({"generation_id": generation, "gw_results": ["corrupt"]})
                    pending_path.write_text(json.dumps({
                        "version": 2,
                        "league": "pl",
                        "generation_id": generation,
                        "store": store,
                        "model": model,
                        "history_entry": history_entry,
                        "counts": {"trained": "corrupt"},
                    }), encoding="utf-8")

                tracked_paths = [prediction_path, model_path, history_path]
                if pending_path.exists():
                    tracked_paths.append(pending_path)
                before = {path: path.read_bytes() for path in tracked_paths}
                trainer_calls = []

                def trainer(model, rows):
                    trainer_calls.append(copy.deepcopy(rows))
                    return model

                with patch.object(learning, "atomic_save_json", wraps=learning.atomic_save_json) as save, patch.object(
                    learning, "_write_persistent_bundle", wraps=learning._write_persistent_bundle,
                ) as replay:
                    with self.assertRaises(learning.StateConsistencyError):
                        run_persistent_competition(
                            league="pl",
                            fixtures=[self.fixture(finished=True)],
                            teams={}, prediction_path=str(prediction_path), model_path=str(model_path),
                            history_path=str(history_path), history={}, now=self.now + timedelta(days=1),
                            snapshot_builder=self.snapshot_builder, model_trainer=trainer,
                            default_model=default_model_state("pl"),
                        )

                self.assertEqual([], trainer_calls)
                save.assert_not_called()
                replay.assert_not_called()
                self.assertEqual(before, {path: path.read_bytes() for path in tracked_paths})

    def test_cross_competition_recovery_precedes_new_training_and_preserves_both(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_path = root / "history.json"
            paths = {
                league: {
                    "prediction": root / f"{league}-predictions.json",
                    "model": root / f"{league}-weights.json",
                }
                for league in ("pl", "laliga", "wc")
            }
            training_calls = {"laliga": 0, "wc": 0}

            def trainer(league):
                def apply(model, rows):
                    training_calls[league] += len(rows)
                    updated = copy.deepcopy(model)
                    updated.setdefault("meta", {})["application_count"] = training_calls[league]
                    return updated
                return apply

            laliga_fixture = self.fixture(season="2026-27")
            wc_fixture = self.fixture(season="2026")
            wc_fixture.update({"id": 88, "source_fixture_id": 88, "e": 17, "grp": "A"})

            history, _, _ = run_persistent_competition(
                league="laliga", fixtures=[laliga_fixture], teams={},
                prediction_path=str(paths["laliga"]["prediction"]),
                model_path=str(paths["laliga"]["model"]),
                history_path=str(history_path), history={}, now=self.now,
                snapshot_builder=self.snapshot_builder, model_trainer=trainer("laliga"),
                default_model=default_model_state("laliga"),
            )
            history, _, _ = run_persistent_competition(
                league="wc", fixtures=[wc_fixture], teams={},
                prediction_path=str(paths["wc"]["prediction"]),
                model_path=str(paths["wc"]["model"]),
                history_path=str(history_path), history=history, now=self.now,
                snapshot_builder=self.snapshot_builder, model_trainer=trainer("wc"),
                default_model=default_model_state("wc"),
            )

            real_save = learning.atomic_save_json

            def fail_wc_history(path, value):
                if Path(path) == history_path:
                    raise OSError("injected stale WC history boundary")
                return real_save(path, value)

            with patch.object(learning, "atomic_save_json", side_effect=fail_wc_history):
                with self.assertRaisesRegex(OSError, "stale WC"):
                    run_persistent_competition(
                        league="wc",
                        fixtures=[dict(wc_fixture, fin=True, st=True, hs=2, **{"as": 0})],
                        teams={}, prediction_path=str(paths["wc"]["prediction"]),
                        model_path=str(paths["wc"]["model"]), history_path=str(history_path),
                        history=history, now=self.now + timedelta(days=1),
                        snapshot_builder=self.snapshot_builder, model_trainer=trainer("wc"),
                        default_model=default_model_state("wc"),
                    )

            configurations = [
                {
                    "league": league,
                    "prediction_path": str(paths[league]["prediction"]),
                    "model_path": str(paths[league]["model"]),
                }
                for league in ("pl", "laliga", "wc")
            ]
            history = learning.recover_pending_competitions(
                configurations,
                history_path=str(history_path),
            )
            history, _, _ = run_persistent_competition(
                league="laliga",
                fixtures=[dict(laliga_fixture, fin=True, st=True, hs=1, **{"as": 0})],
                teams={}, prediction_path=str(paths["laliga"]["prediction"]),
                model_path=str(paths["laliga"]["model"]), history_path=str(history_path),
                history=history, now=self.now + timedelta(days=1),
                snapshot_builder=self.snapshot_builder, model_trainer=trainer("laliga"),
                default_model=default_model_state("laliga"),
            )
            history, _, _ = run_persistent_competition(
                league="wc",
                fixtures=[dict(wc_fixture, fin=True, st=True, hs=2, **{"as": 0})],
                teams={}, prediction_path=str(paths["wc"]["prediction"]),
                model_path=str(paths["wc"]["model"]), history_path=str(history_path),
                history=history, now=self.now + timedelta(days=1, minutes=5),
                snapshot_builder=self.snapshot_builder, model_trainer=trainer("wc"),
                default_model=default_model_state("wc"),
            )

            saved_history = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual({"laliga": 1, "wc": 1}, training_calls)
            self.assertEqual(1, saved_history["laliga"]["total_evaluated"])
            self.assertEqual(1, saved_history["wc"]["total_evaluated"])
            for league in ("laliga", "wc"):
                model = json.loads(paths[league]["model"].read_text(encoding="utf-8"))
                self.assertEqual(model["generation_id"], saved_history[league]["generation_id"])
                self.assertFalse(Path(str(paths[league]["model"]) + ".pending").exists())

    def test_checked_snapshot_remains_comparable_when_feed_omits_or_corrects_fixture(self):
        model = default_model_state("pl")
        store, model, _, _ = evolve_competition_state(
            league="pl",
            fixtures=[self.fixture()],
            store=self.empty_current_store(),
            model=model,
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )
        store, model, first_history, _ = evolve_competition_state(
            league="pl",
            fixtures=[self.fixture(finished=True, score=(1, 0))],
            store=store,
            model=model,
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now + timedelta(days=1),
        )
        first_snapshot = copy.deepcopy(next(iter(store["matches"].values())))

        omitted_store, omitted_model, omitted_history, _ = evolve_competition_state(
            league="pl",
            fixtures=[],
            store=store,
            model=model,
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: self.fail("stored ledger should skip retraining"),
            now=self.now + timedelta(days=2),
        )
        corrected_store, _, corrected_history, _ = evolve_competition_state(
            league="pl",
            fixtures=[self.fixture(finished=True, score=(0, 2))],
            store=omitted_store,
            model=omitted_model,
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: self.fail("checked result should not retrain"),
            now=self.now + timedelta(days=3),
        )

        corrected_snapshot = next(iter(corrected_store["matches"].values()))
        self.assertEqual(1, omitted_history["model_comparison"]["total"])
        self.assertEqual(first_history["model_comparison"], omitted_history["model_comparison"])
        self.assertEqual(first_history["model_comparison"], corrected_history["model_comparison"])
        self.assertEqual(first_snapshot["actual_home_score"], corrected_snapshot["actual_home_score"])
        self.assertEqual(first_snapshot["actual_away_score"], corrected_snapshot["actual_away_score"])
        self.assertEqual(first_snapshot["evaluations"], corrected_snapshot["evaluations"])

    def test_same_source_id_and_round_are_independent_across_seasons(self):
        fixtures = [self.fixture(season="2025-26"), self.fixture(season="2026-27")]
        store, model, _, counts = evolve_competition_state(
            league="pl",
            fixtures=fixtures,
            store=self.empty_current_store(),
            model=default_model_state("pl"),
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )
        self.assertEqual(2, counts["locked"])
        self.assertEqual(
            {"pl:2025-26:77", "pl:2026-27:77"},
            {snapshot["match_key"] for snapshot in store["matches"].values()},
        )

        finished = [self.fixture(season="2025-26", finished=True), self.fixture(season="2026-27", finished=True)]
        store, model, history, _ = evolve_competition_state(
            league="pl",
            fixtures=finished,
            store=store,
            model=model,
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now + timedelta(days=1),
        )
        self.assertEqual(2, len(model["applied_match_keys"]))
        self.assertEqual(["2025-26", "2026-27"], [row["season"] for row in history["gw_results"]])
        self.assertEqual(2, history["model_comparison"]["total"])

    def test_comparison_exposes_all_design_metrics(self):
        rows = [{
            "locked": True,
            "fixture": {"fin": True, "hs": 1, "as": 0},
            "rule": competition_rule("pl", {"e": 1}),
            "picks": self.snapshot_builder({}, {})["picks"],
            "probabilities": self.snapshot_builder({}, {})["probabilities"],
        }]
        summary = comparison_summary(rows, "baseline", "v4")
        baseline = summary["models"]["baseline"]
        candidate = summary["models"]["v4"]
        self.assertEqual(1, baseline["sample_size"])
        self.assertEqual(100.0, baseline["completeness_pct"])
        self.assertEqual(100.0, baseline["winner_accuracy"])
        self.assertEqual(100.0, baseline["exact_accuracy"])
        self.assertEqual(0.0, baseline["goal_mae"])
        self.assertEqual(0.14, baseline["outcome_brier"])
        self.assertEqual(0.0, baseline["draw_pick_rate"])
        self.assertEqual(100.0, baseline["scoreline_concentration"])
        self.assertEqual(0.5, candidate["goal_mae"])
        self.assertEqual(0.24, candidate["outcome_brier"])

    def test_rounded_probability_vectors_use_their_measured_total_for_brier(self):
        row = {
            "locked": True,
            "fixture": {"fin": True, "hs": 1, "as": 0},
            "rule": competition_rule("pl", {"e": 1}),
            "picks": self.snapshot_builder({}, {})["picks"],
            "probabilities": {
                "baseline": {"home": 0.699, "draw": 0.2, "away": 0.1},
                "v4": {"home": 69.9, "draw": 20.0, "away": 10.0},
            },
        }

        summary = comparison_summary([row], "baseline", "v4")

        self.assertEqual(0.1403, summary["models"]["baseline"]["outcome_brier"])
        self.assertEqual(0.1403, summary["models"]["v4"]["outcome_brier"])

    def test_invalid_scores_winners_rules_and_probabilities_are_rejected(self):
        valid_pick = {"winner": "home", "home_score": 1, "away_score": 0}
        rule = competition_rule("pl", {"e": 1})
        for invalid in (True, -1, 1.5, math.nan, math.inf):
            with self.subTest(score=invalid):
                with self.assertRaises(ValueError):
                    score_pick(valid_pick, {"fin": True, "hs": invalid, "as": 0}, rule)
        with self.assertRaises(ValueError):
            score_pick({"winner": "away", "home_score": 1, "away_score": 0}, {"fin": True, "hs": 1, "as": 0}, rule)
        with self.assertRaises(ValueError):
            competition_rule("wc", {"e": 12, "grp": None})
        with self.assertRaises(ValueError):
            comparison_summary([{
                "locked": True,
                "fixture": {"fin": True, "hs": 1, "as": 0},
                "rule": rule,
                "picks": {"baseline": valid_pick, "v4": valid_pick},
                "probabilities": {"baseline": {"home": 0.8, "draw": 0.8, "away": -0.6}},
            }], "baseline", "v4")

        invalid_phase = self.fixture()
        invalid_phase.update({"e": 12, "grp": None, "season": "2026"})
        store, _, _, counts = evolve_competition_state(
            league="wc",
            fixtures=[invalid_phase],
            store=self.empty_current_store("wc"),
            model=default_model_state("wc"),
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )
        self.assertEqual(0, counts["locked"])
        self.assertEqual({}, store["matches"])


class FinalReviewStateLoadingTests(unittest.TestCase):
    @staticmethod
    def _valid_current_store():
        pick = {"winner": "home", "home_score": 1, "away_score": 0}
        return {
            "version": 1,
            "lifecycle_version": 1,
            "league": "pl",
            "matches": {
                "77": {
                    "match_id": 77,
                    "source_fixture_id": 77,
                    "match_key": "pl:2025-26:77",
                    "league": "pl",
                    "season": "2025-26",
                    "round": 4,
                    "lifecycle_version": 1,
                    "locked": True,
                    "lock_verified": True,
                    "checked": False,
                    "model_trained": False,
                    "picks": {"baseline": pick, "v4": copy.deepcopy(pick)},
                    "custom_snapshot_field": {"keep": True},
                },
            },
            "custom_store_field": {"keep": True},
        }

    def test_prediction_role_rejects_invalid_flags_picks_results_evaluations_identities_and_duplicates(self):
        invalid_stores = []
        invalid = self._valid_current_store()
        invalid["matches"]["77"]["checked"] = "yes"
        invalid_stores.append(("flag", invalid))
        invalid = self._valid_current_store()
        invalid["matches"]["77"]["picks"]["v4"] = []
        invalid_stores.append(("pick", invalid))
        invalid = self._valid_current_store()
        invalid["matches"]["77"].update({
            "checked": True,
            "actual_home_score": "two",
            "actual_away_score": 1,
            "actual_winner": "home",
            "rule": competition_rule("pl", {"e": 4}),
            "evaluations": {},
        })
        invalid_stores.append(("result", invalid))
        invalid = self._valid_current_store()
        invalid["matches"]["77"]["evaluations"] = []
        invalid_stores.append(("evaluations", invalid))
        invalid = self._valid_current_store()
        invalid["matches"]["77"]["match_key"] = "pl:2025-26:other"
        invalid_stores.append(("identity", invalid))
        invalid = self._valid_current_store()
        duplicate = copy.deepcopy(invalid["matches"]["77"])
        duplicate["match_id"] = 88
        invalid["matches"]["88"] = duplicate
        invalid_stores.append(("duplicate", invalid))

        for name, store in invalid_stores:
            with self.subTest(name=name):
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_prediction_store(store, "pl")

    def test_model_role_rejects_each_malformed_present_field(self):
        invalid_fields = (
            {"active_strategy": "corrupt"},
            {"candidate_strategy": 4},
            {"factors": []},
            {"factors": {"strength": "corrupt"}},
            {"calibration": []},
            {"calibration": {"goal_mult": 99}},
            {"meta": []},
            {"meta": {"trained_matches": -1}},
            {"applied_ledger_version": "1"},
            {"applied_ledger_version": 1, "applied_match_keys": ["pl:2025-26:77", "pl:2025-26:77"]},
            {"promotion_history": ["corrupt"]},
            {"generation_id": ""},
        )
        for fields in invalid_fields:
            with self.subTest(fields=fields):
                model = default_model_state("pl")
                model.update(copy.deepcopy(fields))
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_model_state(model, "pl")

    def test_history_role_rejects_malformed_league_rows_comparisons_generation_and_counts(self):
        invalid_histories = (
            {"pl": "corrupt"},
            {"pl": {"generation_id": 3}},
            {"pl": {"gw_results": "corrupt"}},
            {"pl": {"gw_results": ["corrupt"]}},
            {"pl": {"gw_results": [{"gw": 1, "total": 1, "correct_winner": 2}]}},
            {"pl": {"model_comparison": []}},
            {"pl": {"model_comparison": {"total": -1}}},
            {"pl": {"model_status": "corrupt"}},
            {"pl": {"counts": {"trained": "corrupt"}}},
        )
        for history in invalid_histories:
            with self.subTest(history=history):
                with self.assertRaises(learning.StateConsistencyError):
                    learning.validate_global_history(history)

    def test_compatible_legacy_roles_validate_and_preserve_unknown_fields(self):
        legacy_store = {
            "version": 4,
            "matches": [{
                "match_id": 760,
                "winner": "away",
                "home_score": 0,
                "away_score": 2,
                "checked": True,
                "actual_home_score": 0,
                "actual_away_score": 2,
                "custom_snapshot_field": {"keep": True},
            }],
            "custom_store_field": {"keep": True},
        }
        learning.validate_prediction_store(legacy_store, "wc")
        normalized = learning.normalize_prediction_store(legacy_store, "wc")
        self.assertEqual({"keep": True}, normalized["matches"]["760"]["custom_snapshot_field"])
        self.assertEqual({"keep": True}, normalized["custom_store_field"])

        legacy_model = {"strength": 0.2, "form": 0.1, "custom_model_field": {"keep": True}}
        learning.validate_model_state(legacy_model, "pl")
        legacy_history = {
            "wc": {
                "gw_results": [{"gw": 18}],
                "model_comparison": {"total": 65, "custom_comparison_field": {"keep": True}},
                "custom_history_field": {"keep": True},
            },
            "other": {"keep": True},
        }
        learning.validate_global_history(legacy_history)
        self.assertEqual({"keep": True}, legacy_history["other"])

    def test_missing_state_initializes_but_existing_invalid_state_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            value, status = load_json_state(str(missing), {"safe": True})
            self.assertEqual({"safe": True}, value)
            self.assertEqual("missing", status)
            for name, content, binary in (
                ("malformed.json", "{bad", False),
                ("list.json", "[]", False),
                ("invalid-utf8.json", b"\xff", True),
            ):
                path = root / name
                path.write_bytes(content if binary else content.encode("utf-8"))
                before = path.read_bytes()
                with self.assertRaises(StateFileError):
                    load_json_state(str(path), {})
                self.assertEqual(before, path.read_bytes())

    def test_atomic_save_rejects_non_finite_json_without_replacing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"safe":true}', encoding="utf-8")
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                learning.atomic_save_json(str(path), {"bad": math.nan})
            self.assertEqual(before, path.read_bytes())

    def test_all_state_roles_reject_recursive_non_finite_json_without_changing_companions(self):
        roles = ("predictions", "model", "history", "pending")
        constants = ("NaN", "Infinity", "-Infinity", "1e999")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for role in roles:
                for constant in constants:
                    with self.subTest(role=role, constant=constant):
                        paths = {name: root / f"{role}-{constant}-{name}.json" for name in roles}
                        for path in paths.values():
                            path.write_text('{"safe":{"value":1}}', encoding="utf-8")
                        paths[role].write_text(
                            '{"outer":[{"bad":' + constant + '}]}',
                            encoding="utf-8",
                        )
                        before = {path: path.read_bytes() for path in paths.values()}

                        with self.assertRaises(StateFileError):
                            load_json_state(str(paths[role]), {})

                        self.assertEqual(before, {path: path.read_bytes() for path in paths.values()})


class FinalReviewDashboardContractTests(unittest.TestCase):
    def test_learning_failures_are_not_swallowed_before_embedding_or_publication(self):
        source = (Path(__file__).parents[1] / "website" / "update_pl_mobile.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        swallowed = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            names = {
                child.func.id
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
            swallowed.extend(names & {"run_league_learning", "run_wc_learning"})
        self.assertEqual([], swallowed)
        self.assertNotIn("ML learning skipped", source)
        self.assertNotIn("World Cup ML skipped", source)
        for league in ("pl", "laliga", "wc"):
            self.assertIn(f'_log_learning_counts("{league}"', source)

    def test_all_pending_recovery_runs_before_any_dashboard_evolution(self):
        source = (Path(__file__).parents[1] / "website" / "update_pl_mobile.py").read_text(encoding="utf-8")
        recovery = source.index("learning_history = recover_pending_competitions(")
        self.assertLess(recovery, source.index("learning_history = run_league_learning("))
        self.assertLess(recovery, source.index("learning_history = run_wc_learning("))
        for league in ("pl", "laliga", "wc"):
            self.assertIn(f'"league": "{league}"', source[recovery:source.index("learning_history = run_league_learning(")])


class FinalReviewPublisherTests(unittest.TestCase):
    def test_repository_resolves_from_explicit_configuration_or_origin(self):
        self.assertEqual("owner/project", resolve_target_repository("owner/project", cwd="."))

        class Result:
            returncode = 0
            stdout = "git@github.com:actual/repository.git\n"
            stderr = ""

        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return Result()

        self.assertEqual("actual/repository", resolve_target_repository(None, cwd="checkout", runner=runner))
        self.assertEqual(1, len(calls))
        with self.assertRaisesRegex(RuntimeError, "GitHub repository"):
            resolve_target_repository("not-a-repository", cwd=".")

    def test_explicit_publish_errors_propagate_before_final_success(self):
        source = (Path(__file__).parents[1] / "website" / "update_pl_mobile.py").read_text(encoding="utf-8")
        self.assertIn('GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()', source)
        self.assertIn("resolve_target_repository(GITHUB_REPO or None", source)
        self.assertNotIn("GitHub atomic publish failed:", source)
        self.assertNotIn("no GITHUB_TOKEN is set — skipping", source)


if __name__ == "__main__":
    unittest.main()
