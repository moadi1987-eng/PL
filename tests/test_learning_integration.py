import copy
import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import website.league_learning as learning
from website import ml_engine
from website.league_learning import (
    _prepare_persistent_competition,
    load_json_state,
    merge_learning_history,
    normalize_prediction_store,
    run_persistent_competition,
)
from website.league_predictor import default_model_state, predict_league_snapshot, train_factor_model


class PersistentCompetitionTests(unittest.TestCase):
    @staticmethod
    def _snapshot_builder(fixture, model):
        return {
            "features": {"source": model["league"]},
            "factor_edges": {"strength": 1.0},
            "missing": {},
            "expected_home_goals": 1.4,
            "expected_away_goals": 0.8,
            "picks": {
                "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                "v4": {"winner": "home", "home_score": 2, "away_score": 0},
            },
        }

    @staticmethod
    def _trainer(model, rows):
        updated = dict(model)
        updated["meta"] = dict(model.get("meta", {}), last_batch_size=len(rows))
        return updated

    @staticmethod
    def _fixture(now, finished=False):
        return {
            "id": 77, "e": 4, "h": 1, "a": 2,
            "ko": (now + timedelta(hours=10)).isoformat(),
            "fin": finished, "st": finished,
            "hs": 2 if finished else None, "as": 0 if finished else None,
        }

    def test_pl_and_laliga_write_independent_state(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        fixture = {
            "id": 1, "e": 1, "h": 1, "a": 2,
            "ko": (now + timedelta(hours=10)).isoformat(),
            "fin": False, "st": False, "hs": None, "as": None,
        }
        teams = {1: {"id": 1, "s": "A"}, 2: {"id": 2, "s": "B"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            histories = {}
            models = {}
            for league in ("pl", "laliga"):
                prediction_path = root / f"{league}-predictions.json"
                model_path = root / f"{league}-weights.json"
                history, counts, model = run_persistent_competition(
                    league=league,
                    fixtures=[fixture],
                    teams=teams,
                    prediction_path=str(prediction_path),
                    model_path=str(model_path),
                    history={},
                    now=now,
                    snapshot_builder=lambda row, state, current=league: predict_league_snapshot(
                        row, [fixture], teams, state, current
                    ),
                    model_trainer=train_factor_model,
                    default_model=default_model_state(league),
                )
                histories[league] = history[league]
                models[league] = json.loads(model_path.read_text(encoding="utf-8"))
                self.assertEqual(1, counts["locked"])
                self.assertEqual(league, model["league"])

            self.assertEqual("pl", models["pl"]["league"])
            self.assertEqual("laliga", models["laliga"]["league"])
            self.assertEqual(1, histories["pl"]["snapshots_locked"])
            self.assertEqual(1, histories["laliga"]["snapshots_locked"])

    def test_persistent_rerun_keeps_locked_picks_and_trains_completed_row_once(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"

            def builder(fixture, model):
                calls.append(fixture["id"])
                return self._snapshot_builder(fixture, model)

            run_persistent_competition(
                league="pl", fixtures=[self._fixture(now)], teams={},
                prediction_path=str(prediction_path), model_path=str(model_path),
                history={}, now=now, snapshot_builder=builder, model_trainer=self._trainer,
                default_model=default_model_state("pl"),
            )
            first = json.loads(prediction_path.read_text(encoding="utf-8"))["matches"]["77"]
            completed = self._fixture(now, finished=True)
            run_persistent_competition(
                league="pl", fixtures=[completed], teams={},
                prediction_path=str(prediction_path), model_path=str(model_path),
                history={}, now=now + timedelta(days=1), snapshot_builder=builder,
                model_trainer=self._trainer, default_model=default_model_state("pl"),
            )
            second = json.loads(prediction_path.read_text(encoding="utf-8"))["matches"]["77"]
            _, counts, _ = run_persistent_competition(
                league="pl", fixtures=[completed], teams={},
                prediction_path=str(prediction_path), model_path=str(model_path),
                history={}, now=now + timedelta(days=2), snapshot_builder=builder,
                model_trainer=self._trainer, default_model=default_model_state("pl"),
            )

        self.assertEqual([77], calls)
        self.assertEqual(first["picks"], second["picks"])
        self.assertTrue(second["model_trained"])
        self.assertEqual(0, counts["trained"])

    def test_malformed_state_aborts_without_changing_any_bundle_file(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            history_path = root / "history.json"
            prediction_path.write_text("{bad", encoding="utf-8")
            model_path.write_text("[]", encoding="utf-8")
            history_path.write_text('{"laliga":{"keep":true}}', encoding="utf-8")
            before = {path: path.read_bytes() for path in (prediction_path, model_path, history_path)}
            with self.assertRaises(learning.StateFileError):
                run_persistent_competition(
                    league="laliga", fixtures=[self._fixture(now)], teams={},
                    prediction_path=str(prediction_path), model_path=str(model_path),
                    history_path=str(history_path), history={}, now=now,
                    snapshot_builder=self._snapshot_builder,
                    model_trainer=self._trainer, default_model=default_model_state("laliga"),
                )
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_default_models_and_physical_paths_do_not_cross_contaminate(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        pl_default = default_model_state("pl")
        ll_default = default_model_state("laliga")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, pl_model = run_persistent_competition(
                league="pl", fixtures=[self._fixture(now)], teams={},
                prediction_path=str(root / "pl-predictions.json"),
                model_path=str(root / "pl-weights.json"), history={}, now=now,
                snapshot_builder=self._snapshot_builder, model_trainer=self._trainer,
                default_model=pl_default,
            )
            pl_model["factors"]["strength"] = 0.99
            _, _, ll_model = run_persistent_competition(
                league="laliga", fixtures=[self._fixture(now)], teams={},
                prediction_path=str(root / "laliga-predictions.json"),
                model_path=str(root / "laliga-weights.json"), history={}, now=now,
                snapshot_builder=self._snapshot_builder, model_trainer=self._trainer,
                default_model=ll_default,
            )

            self.assertTrue((root / "pl-weights.json").exists())
            self.assertTrue((root / "laliga-weights.json").exists())

        self.assertNotEqual(pl_model["factors"], ll_model["factors"])
        self.assertEqual(default_model_state("pl"), pl_default)
        self.assertEqual(default_model_state("laliga"), ll_default)

    def test_wc_list_map_unknown_fields_and_v4_active_pick_survive_normalization(self):
        raw = {
            "version": 4,
            "matches": [{
                "match_id": 760,
                "winner": "away",
                "home_score": 0,
                "away_score": 2,
                "model_version": 4,
                "prediction_strategy": "v4_scoreline",
                "base_v3_prediction": {"winner": "away", "home_score": 1, "away_score": 2},
                "custom_wc_field": "keep",
                "checked": True,
                "model_trained": True,
                "actual_home_score": 0,
                "actual_away_score": 2,
            }],
        }
        normalized = normalize_prediction_store(raw, "wc")
        row = normalized["matches"]["760"]
        self.assertEqual("keep", row["custom_wc_field"])
        self.assertEqual("v4", row["active_strategy_at_lock"])
        self.assertEqual({"winner": "away", "home_score": 0, "away_score": 2}, row["picks"]["v4"])
        self.assertTrue(row["model_trained"])

    def test_merge_preserves_unrelated_history_and_existing_wc_comparison(self):
        original = {
            "other": {"keep": True},
            "wc": {"gw_results": [{"gw": 18}], "model_comparison": {"total": 65}},
            "pl": {"user_guess_note": "keep"},
        }
        merged = merge_learning_history(
            original,
            "pl",
            {"gw_results": [], "model_comparison": {"total": 0}, "snapshots_locked": 1},
        )
        self.assertTrue(merged["other"]["keep"])
        self.assertEqual(65, merged["wc"]["model_comparison"]["total"])
        self.assertEqual("keep", merged["pl"]["user_guess_note"])
        self.assertEqual(1, merged["pl"]["snapshots_locked"])

    def test_merge_recomputes_accuracy_from_retained_gameweek_results(self):
        retained_rows = [
            {"gw": gw, "total": 10 if gw < 11 else 8, "correct_winner": 5 if gw < 11 else 4}
            for gw in range(1, 12)
        ]
        merged = merge_learning_history(
            {"pl": {"gw_results": retained_rows, "overall_accuracy": 50.0}},
            "pl",
            {"gw_results": [], "overall_accuracy": 0.0, "model_comparison": {"total": 0}},
        )

        self.assertEqual(11, len(merged["pl"]["gw_results"]))
        self.assertEqual(108, sum(row["total"] for row in merged["pl"]["gw_results"]))
        self.assertEqual(54, sum(row["correct_winner"] for row in merged["pl"]["gw_results"]))
        self.assertEqual(50.0, merged["pl"]["overall_accuracy"])

    def test_merge_unions_declared_observed_and_current_seasons(self):
        merged = merge_learning_history(
            {
                "pl": {
                    "gw_results": [{
                        "gw": 38,
                        "season": "2025-26",
                        "total": 1,
                        "correct_winner": 1,
                    }],
                    "total_evaluated": 1,
                    "current_season": "2025-26",
                    "available_seasons": ["2024-25"],
                },
            },
            "pl",
            {
                "gw_results": [],
                "total_evaluated": 0,
                "current_season": "2026-27",
                "available_seasons": ["2026-27"],
            },
        )

        self.assertEqual("2026-27", merged["pl"]["current_season"])
        self.assertEqual(
            ["2024-25", "2025-26", "2026-27"],
            merged["pl"]["available_seasons"],
        )

    def test_merge_repairs_stale_total_evaluated_from_preserved_gameweek_history(self):
        retained_rows = [
            {"gw": gw, "total": 10 if gw < 11 else 8, "correct_winner": 5 if gw < 11 else 4}
            for gw in range(1, 12)
        ]
        original = {
            "pl": {
                "gw_results": retained_rows,
                "total_evaluated": 0,
                "overall_accuracy": 50.0,
                "model_comparison": {"total": 108},
            },
        }
        before = copy.deepcopy(original)

        merged = merge_learning_history(
            original,
            "pl",
            {
                "gw_results": [],
                "total_evaluated": 0,
                "overall_accuracy": 0.0,
                "model_comparison": {"total": 0},
            },
        )

        self.assertEqual(11, len(merged["pl"]["gw_results"]))
        self.assertEqual(108, sum(row["total"] for row in merged["pl"]["gw_results"]))
        self.assertEqual(108, merged["pl"]["total_evaluated"])
        self.assertEqual(108, merged["pl"]["model_comparison"]["total"])
        self.assertEqual(before, original)

    def test_merge_keeps_legitimately_empty_history_total_at_zero(self):
        merged = merge_learning_history(
            {},
            "pl",
            {
                "gw_results": [],
                "total_evaluated": 0,
                "overall_accuracy": 0.0,
                "model_comparison": {"total": 0},
            },
        )

        self.assertEqual([], merged["pl"]["gw_results"])
        self.assertEqual(0, merged["pl"]["total_evaluated"])

    def test_merge_preserves_aggregate_when_retained_gameweek_rows_are_sparse(self):
        original = {
            "wc": {
                "gw_results": [{"gw": 18}],
                "total_evaluated": 7,
                "model_comparison": {"total": 7},
            },
        }
        before = copy.deepcopy(original)

        merged = merge_learning_history(
            original,
            "wc",
            {
                "gw_results": [],
                "total_evaluated": 0,
                "model_comparison": {"total": 0},
            },
        )

        self.assertEqual(1, len(merged["wc"]["gw_results"]))
        self.assertNotIn("total", merged["wc"]["gw_results"][0])
        self.assertEqual(7, merged["wc"]["total_evaluated"])
        self.assertEqual(before, original)

    def test_merge_keeps_genuine_zero_accuracy_from_evaluated_rows(self):
        merged = merge_learning_history(
            {"pl": {"gw_results": [{"gw": 1, "total": 10, "correct_winner": 5}], "overall_accuracy": 50.0}},
            "pl",
            {"gw_results": [{"gw": 2, "total": 8, "correct_winner": 0}], "overall_accuracy": 50.0, "model_comparison": {"total": 0}},
        )

        self.assertEqual(0.0, merged["pl"]["overall_accuracy"])

    def test_merge_preserves_comparison_only_accuracy_without_incoming_evidence(self):
        merged = merge_learning_history(
            {"wc": {"gw_results": [], "overall_accuracy": 64.6, "total_evaluated": 65, "model_comparison": {"total": 65}}},
            "wc",
            {"gw_results": [], "overall_accuracy": 0.0, "total_evaluated": 0, "model_comparison": {"total": 0}},
        )

        self.assertEqual(64.6, merged["wc"]["overall_accuracy"])
        self.assertEqual(65, merged["wc"]["model_comparison"]["total"])

    def test_merge_uses_new_comparison_accuracy_without_gameweek_rows(self):
        merged = merge_learning_history(
            {"wc": {"gw_results": [], "overall_accuracy": 64.6, "total_evaluated": 65, "model_comparison": {"total": 65}}},
            "wc",
            {
                "gw_results": [],
                "overall_accuracy": 0.0,
                "total_evaluated": 1,
                "model_comparison": {
                    "total": 1,
                    "active_strategy": "v4",
                    "models": {"v4": {"winner_accuracy": 100.0}},
                },
            },
        )

        self.assertEqual(100.0, merged["wc"]["overall_accuracy"])

    def test_wc_adapter_uses_shared_lifecycle_and_preserves_history(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {
            "__name__": "website.update_pl_mobile_test",
            "__package__": "website",
            "__file__": str(update_path),
        }
        exec(compile(prefix, str(update_path), "exec"), namespace)
        now = datetime.now(timezone.utc)
        payload = {
            "teams": {"1": {"id": 1, "s": "A"}, "2": {"id": 2, "s": "B"}},
            "fix": [{
                "id": 10, "e": 17, "grp": "A", "h": 1, "a": 2,
                "ko": (now + timedelta(hours=1)).isoformat(),
                "fin": False, "st": False, "hs": None, "as": None,
            }],
        }
        raw_prediction = {
            "winner": "home", "home_score": 1, "away_score": 0,
            "home_win_pct": 55, "draw_pct": 25, "away_win_pct": 20,
            "expected_home_goals": 1.3, "expected_away_goals": 0.8,
            "input_snapshot": {"missing": {}}, "signals": {"strength": "home"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            namespace["WC_PREDICTIONS_FILE"] = str(root / "wc-predictions.json")
            namespace["WC_WEIGHTS_FILE"] = str(root / "wc-weights.json")
            namespace["LEARNING_HISTORY_FILE"] = str(root / "history.json")
            namespace["_wc_predict_snapshot"] = lambda *args: dict(raw_prediction)
            namespace["_wc_v4_score_pick"] = lambda raw: {
                "winner": "home", "home_score": 2, "away_score": 0, "reason": "fixture",
            }
            namespace["_wc_update_model"] = lambda model, rows: dict(model)
            history = namespace["run_wc_learning"](
                json.dumps(payload),
                {"other": {"keep": True}, "wc": {"gw_results": [{"gw": 18}], "model_comparison": {"total": 65}}},
            )
            stored = json.loads((root / "wc-predictions.json").read_text(encoding="utf-8"))

        self.assertEqual({"baseline", "v4"}, set(stored["matches"]["10"]["picks"]))
        self.assertEqual("v4", stored["matches"]["10"]["active_strategy_at_lock"])
        self.assertTrue(history["other"]["keep"])
        self.assertEqual(65, history["wc"]["model_comparison"]["total"])

    def test_production_uses_shared_runner_and_ml_engine_deprecates_state_writers(self):
        root = Path(__file__).parents[1]
        update_source = (root / "website" / "update_pl_mobile.py").read_text(encoding="utf-8")
        self.assertIn("learning_history = run_league_learning(", update_source)
        self.assertNotIn("from ml_engine import run_pl_learning, run_ll_learning", update_source)
        with self.assertRaisesRegex(RuntimeError, "run_persistent_competition"):
            ml_engine.run_pl_learning([], {})
        with self.assertRaisesRegex(RuntimeError, "run_persistent_competition"):
            ml_engine.run_ll_learning([])

    def test_wc_completed_lifecycle_row_does_not_replace_legacy_archive_history(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        now = datetime.now(timezone.utc)
        future = {
            "id": 11, "e": 17, "grp": "A", "h": 1, "a": 2,
            "ko": (now + timedelta(hours=1)).isoformat(),
            "fin": False, "st": False, "hs": None, "as": None,
        }
        raw_prediction = {
            "winner": "home", "home_score": 1, "away_score": 0,
            "home_win_pct": 55, "draw_pct": 25, "away_win_pct": 20,
            "expected_home_goals": 1.3, "expected_away_goals": 0.8,
            "input_snapshot": {"missing": {}}, "signals": {"strength": "home"},
        }
        history = {"wc": {
            "gw_results": [{"gw": 18, "total": 65, "correct_winner": 42, "correct_score": 14, "exact_score": 14, "points": 72, "accuracy_pct": 64.6, "score_acc_pct": 21.5}],
            "total_evaluated": 65,
            "overall_accuracy": 64.6,
            "model_comparison": {
                "total": 65,
                "baseline_model": "v3 expected-points",
                "challenger_model": "v4 scoreline",
                "baseline": {"winner_correct": 40, "exact_correct": 4, "points": 50, "unique_scores": 4, "top_scores": []},
                "challenger": {"winner_correct": 42, "exact_correct": 14, "points": 72, "unique_scores": 11, "top_scores": []},
            },
        }}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            namespace["WC_PREDICTIONS_FILE"] = str(root / "wc-predictions.json")
            namespace["WC_WEIGHTS_FILE"] = str(root / "wc-weights.json")
            namespace["LEARNING_HISTORY_FILE"] = str(root / "history.json")
            namespace["_wc_predict_snapshot"] = lambda *args: dict(raw_prediction)
            namespace["_wc_v4_score_pick"] = lambda raw: {"winner": "home", "home_score": 2, "away_score": 0}
            namespace["_wc_update_model"] = lambda model, rows: dict(model)
            payload = {"teams": {"1": {"id": 1, "s": "A"}, "2": {"id": 2, "s": "B"}}, "fix": [future]}
            history = namespace["run_wc_learning"](json.dumps(payload), history)
            completed = dict(future, **{"fin": True, "st": True, "hs": 2, "as": 0})
            history = namespace["run_wc_learning"](json.dumps(dict(payload, fix=[completed])), history)
            rerun = namespace["run_wc_learning"](json.dumps(dict(payload, fix=[completed])), history)

        wc = history["wc"]
        self.assertEqual(66, wc["total_evaluated"])
        self.assertEqual(["wc:2026:11"], wc["merged_lifecycle_match_ids"])
        self.assertEqual(1, wc["model_status"]["verified_lifecycle_samples"])
        self.assertEqual(66, wc["model_comparison"]["total"])
        self.assertEqual(41, wc["model_comparison"]["baseline"]["winner_correct"])
        self.assertEqual(51, wc["model_comparison"]["baseline"]["points"])
        self.assertEqual(43, wc["model_comparison"]["challenger"]["winner_correct"])
        self.assertEqual(15, wc["model_comparison"]["challenger"]["exact_correct"])
        self.assertEqual(75, wc["model_comparison"]["challenger"]["points"])
        self.assertEqual({"season": "2026", "gw": 17, "total": 1, "correct_winner": 1, "correct_score": 1, "exact_score": 1, "points": 3, "accuracy_pct": 100.0, "score_acc_pct": 100.0}, next(row for row in wc["gw_results"] if row["gw"] == 17))
        self.assertEqual(66, rerun["wc"]["total_evaluated"])
        self.assertEqual(["wc:2026:11"], rerun["wc"]["merged_lifecycle_match_ids"])

    def test_transient_prediction_read_error_aborts_before_any_persistent_save(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(learning, "load_json_state", side_effect=PermissionError("denied")), patch.object(learning, "atomic_save_json") as save:
            with self.assertRaisesRegex(PermissionError, "denied"):
                run_persistent_competition(
                    league="pl", fixtures=[self._fixture(now)], teams={},
                    prediction_path="predictions.json", model_path="weights.json", history={}, now=now,
                    snapshot_builder=self._snapshot_builder, model_trainer=self._trainer,
                    default_model=default_model_state("pl"),
                )
        save.assert_not_called()

    def test_transient_model_read_error_aborts_before_any_persistent_save(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(learning, "load_json_state", side_effect=[({}, True), PermissionError("denied")]), patch.object(learning, "atomic_save_json") as save:
            with self.assertRaisesRegex(PermissionError, "denied"):
                run_persistent_competition(
                    league="pl", fixtures=[self._fixture(now)], teams={},
                    prediction_path="predictions.json", model_path="weights.json", history={}, now=now,
                    snapshot_builder=self._snapshot_builder, model_trainer=self._trainer,
                    default_model=default_model_state("pl"),
                )
        save.assert_not_called()

    def test_load_json_state_propagates_transient_permission_error(self):
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "denied"):
                load_json_state("locked.json", {"safe": True})

    def test_update_history_loader_propagates_transient_permission_error(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        with patch.object(namespace["os"].path, "exists", return_value=True), patch("builtins.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "denied"):
                namespace["_load_json_file"]("learning_history.json", {})

    def test_league_runner_failure_propagates_before_dashboard_continues(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        calls = []

        def runner(**kwargs):
            calls.append(kwargs["league"])
            if kwargs["league"] == "laliga":
                raise PermissionError("laliga denied")
            return ({"pl": {"model_comparison": {"total": 0}, "model_status": {"verified_lifecycle_samples": 0}}}, {}, {})

        namespace["run_persistent_competition"] = runner
        with self.assertRaisesRegex(PermissionError, "laliga denied"):
            namespace["run_league_learning"]([], {}, [], {}, {})
        self.assertEqual(["pl", "laliga"], calls)

    def test_wc_delegates_archive_merge_to_persistent_runner(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            return ({"wc": {"gw_results": [], "model_comparison": {"total": 0}, "model_status": {}}}, {"locked": 0, "checked": 0, "trained": 0, "skipped": 0, "promoted": 0}, {})

        namespace["run_persistent_competition"] = runner
        history = namespace["run_wc_learning"](json.dumps({"teams": {}, "fix": []}), {})
        self.assertEqual([], history["wc"]["gw_results"])
        self.assertEqual(1, len(calls))
        self.assertIs(calls[0]["history_transform"], namespace["_wc_merge_verified_archive"])
        self.assertEqual(namespace["LEARNING_HISTORY_FILE"], calls[0]["history_path"])

    def test_wc_persistent_runner_error_propagates(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        namespace["run_persistent_competition"] = lambda **kwargs: (_ for _ in ()).throw(PermissionError("wc denied"))
        with self.assertRaisesRegex(PermissionError, "wc denied"):
            namespace["run_wc_learning"](json.dumps({"teams": {}, "fix": []}), {})

    def test_wc_comparison_preserves_full_schema_and_hidden_legacy_score_histogram(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        score_counts = {"2-1": 20, "1-2": 15, "0-2": 10, "2-0": 8, "1-3": 5, "3-1": 4, "0-0": 3}
        store = {"matches": {}}
        match_id = 1
        for score, count in score_counts.items():
            home, away = (int(part) for part in score.split("-"))
            winner = "home" if home > away else "away" if away > home else "draw"
            for _ in range(count):
                store["matches"][str(match_id)] = {
                    "match_id": match_id, "legacy": True, "lock_verified": False, "checked": True,
                    "picks": {
                        "baseline": {"winner": winner, "home_score": home, "away_score": away},
                        "v4": {"winner": winner, "home_score": home, "away_score": away},
                    },
                }
                match_id += 1
        store["matches"]["999"] = {
            "match_id": 999, "round": 19, "lock_verified": True, "checked": True,
            "active_strategy_at_lock": "v4",
            "picks": {
                "baseline": {"winner": "draw", "home_score": 0, "away_score": 0},
                "v4": {"winner": "draw", "home_score": 0, "away_score": 0},
            },
            "evaluations": {
                "baseline": {"winner_correct": True, "exact": False, "points": 1, "score": "0-0"},
                "v4": {"winner_correct": True, "exact": True, "points": 3, "score": "0-0"},
            },
        }
        top_scores = [{"score": score, "count": count} for score, count in list(score_counts.items())[:6]]
        previous = {"total_evaluated": 65, "gw_results": [{"gw": 18, "total": 65, "correct_winner": 42, "correct_score": 14, "exact_score": 14, "points": 72}], "model_comparison": {
            "total": 65, "custom_top": {"keep": True}, "baseline_model": "v3 expected-points", "challenger_model": "v4 scoreline",
            "baseline": {"winner_correct": 40, "exact_correct": 4, "points": 50, "draw_picks": 5, "unique_scores": 7, "top_scores": top_scores, "custom_box": "baseline"},
            "challenger": {"winner_correct": 42, "exact_correct": 14, "points": 72, "draw_picks": 7, "unique_scores": 7, "top_scores": top_scores, "custom_box": "v4"},
            "models": {"baseline": {"custom_model": "baseline"}, "v4": {"custom_model": "v4"}},
            "delta": {"winner_accuracy": 3.1, "exact_accuracy": 15.3, "points": 22, "unique_scores": 0, "draw_picks": 2, "custom_delta": "keep"},
            "score_histograms": {"custom": {"keep": 1}},
        }}
        lifecycle_history = {"gw_results": [], "model_comparison": {"total": 1}, "model_status": {}}

        merged = namespace["_wc_merge_verified_archive"](previous, lifecycle_history, store)
        rerun = namespace["_wc_merge_verified_archive"](merged, lifecycle_history, store)
        comparison = merged["model_comparison"]

        self.assertEqual({"keep": True}, comparison["custom_top"])
        self.assertEqual("baseline", comparison["baseline"]["custom_box"])
        self.assertEqual("v4", comparison["challenger"]["custom_box"])
        self.assertEqual("baseline", comparison["models"]["baseline"]["custom_model"])
        self.assertEqual("v4", comparison["models"]["v4"]["custom_model"])
        self.assertEqual("keep", comparison["delta"]["custom_delta"])
        self.assertEqual({"keep": 1}, comparison["score_histograms"]["custom"])
        self.assertEqual(6, comparison["baseline"]["draw_picks"])
        self.assertEqual(8, comparison["challenger"]["draw_picks"])
        self.assertEqual(2, comparison["delta"]["draw_picks"])
        self.assertEqual(7, comparison["baseline"]["unique_scores"])
        self.assertEqual(7, comparison["challenger"]["unique_scores"])
        self.assertEqual(4, comparison["score_histograms"]["baseline"]["0-0"])
        self.assertEqual(4, comparison["score_histograms"]["v4"]["0-0"])
        self.assertEqual(66, comparison["total"])
        self.assertEqual(comparison, rerun["model_comparison"])

    def test_wc_opaque_archive_tracks_lifecycle_scores_without_guessing_uniqueness(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        store = {"matches": {
            "999": {
                "match_id": 999, "round": 19, "lock_verified": True, "checked": True,
                "active_strategy_at_lock": "v4",
                "picks": {"baseline": {"winner": "draw", "home_score": 0, "away_score": 0}, "v4": {"winner": "draw", "home_score": 0, "away_score": 0}},
                "evaluations": {"baseline": {"winner_correct": True, "exact": False, "points": 1, "score": "0-0"}, "v4": {"winner_correct": True, "exact": True, "points": 3, "score": "0-0"}},
            },
        }}
        previous = {"total_evaluated": 65, "model_comparison": {
            "total": 65,
            "baseline": {"winner_correct": 40, "exact_correct": 4, "points": 50, "draw_picks": 5, "unique_scores": 11, "top_scores": []},
            "challenger": {"winner_correct": 42, "exact_correct": 14, "points": 72, "draw_picks": 7, "unique_scores": 11, "top_scores": []},
        }}
        merged = namespace["_wc_merge_verified_archive"](previous, {"model_comparison": {"total": 1}, "model_status": {}}, store)
        comparison = merged["model_comparison"]
        self.assertEqual(11, comparison["baseline"]["unique_scores"])
        self.assertEqual(11, comparison["challenger"]["unique_scores"])
        self.assertFalse(comparison["score_histograms_complete"]["baseline"])
        self.assertEqual(1, comparison["lifecycle_score_histograms"]["baseline"]["0-0"])
        self.assertEqual(1, comparison["lifecycle_score_histograms"]["v4"]["0-0"])

    def test_wc_reconstructs_stale_complete_histogram_and_preserves_completeness_metadata(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        store = {"matches": {
            "1": {"legacy": True, "checked": True, "picks": {"baseline": {"winner": "draw", "home_score": 1, "away_score": 1}, "v4": {"winner": "draw", "home_score": 1, "away_score": 1}}},
            "999": {"match_id": 999, "round": 19, "lock_verified": True, "checked": True, "active_strategy_at_lock": "v4", "picks": {"baseline": {"winner": "draw", "home_score": 1, "away_score": 1}, "v4": {"winner": "draw", "home_score": 1, "away_score": 1}}, "evaluations": {"baseline": {"winner_correct": True, "exact": False, "points": 1, "score": "01-01"}, "v4": {"winner_correct": True, "exact": True, "points": 3, "score": "01-01"}}},
        }}
        previous = {"total_evaluated": 1, "model_comparison": {
            "total": 1,
            "baseline": {"winner_correct": 0, "exact_correct": 0, "points": 0, "draw_picks": 0, "unique_scores": 1, "top_scores": []},
            "challenger": {"winner_correct": 0, "exact_correct": 0, "points": 0, "draw_picks": 0, "unique_scores": 1, "top_scores": []},
            "score_histograms": {"baseline": {"01-01": 1, "bad-key": 0}, "v4": {"01-01": 1, "bad-key": 0}},
            "score_histograms_complete": {"baseline": True, "v4": True, "custom_strategy": {"keep": True}},
        }}
        merged = namespace["_wc_merge_verified_archive"](previous, {"model_comparison": {"total": 1}, "model_status": {}}, store)
        comparison = merged["model_comparison"]
        self.assertEqual({"1-1": 2}, comparison["score_histograms"]["baseline"])
        self.assertEqual({"1-1": 2}, comparison["score_histograms"]["v4"])
        self.assertTrue(comparison["score_histograms_complete"]["baseline"])
        self.assertEqual({"keep": True}, comparison["score_histograms_complete"]["custom_strategy"])

    def test_wc_canonicalizes_opaque_lifecycle_score_keys_from_picks(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        store = {"matches": {
            "999": {"match_id": 999, "round": 19, "lock_verified": True, "checked": True, "active_strategy_at_lock": "v4", "picks": {"baseline": {"winner": "draw", "home_score": 1, "away_score": 1}, "v4": {"winner": "draw", "home_score": 1, "away_score": 1}}, "evaluations": {"baseline": {"winner_correct": True, "exact": False, "points": 1, "score": "01-01"}, "v4": {"winner_correct": True, "exact": True, "points": 3, "score": "01-01"}}},
        }}
        previous = {"total_evaluated": 65, "model_comparison": {"total": 65, "baseline": {"unique_scores": 11}, "challenger": {"unique_scores": 11}}}
        merged = namespace["_wc_merge_verified_archive"](previous, {"model_comparison": {"total": 1}, "model_status": {}}, store)
        self.assertEqual({"1-1": 1}, merged["model_comparison"]["lifecycle_score_histograms"]["baseline"])
        self.assertEqual({"1-1": 1}, merged["model_comparison"]["lifecycle_score_histograms"]["v4"])

    def test_wc_archive_exposes_design_metric_schema_for_both_strategies(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        store = {"matches": {"1": {
            "legacy": True,
            "checked": True,
            "actual_home_score": 1,
            "actual_away_score": 0,
            "actual_winner": "home",
            "rule": {"key": "group", "result": 1, "exact": 3, "additive": False},
            "probabilities": {
                "baseline": {"home": 0.7, "draw": 0.2, "away": 0.1},
                "v4": {"home": 0.6, "draw": 0.2, "away": 0.2},
            },
            "picks": {
                "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                "v4": {"winner": "home", "home_score": 2, "away_score": 0},
            },
        }}}
        previous = {"total_evaluated": 1, "model_comparison": {
            "total": 1,
            "baseline": {"winner_correct": 1, "exact_correct": 1, "points": 3},
            "challenger": {"winner_correct": 1, "exact_correct": 0, "points": 1},
        }}
        comparison = namespace["_wc_archive_comparison"](previous, [], store)
        for strategy in ("baseline", "v4"):
            box = comparison["models"][strategy]
            for key in ("goal_mae", "outcome_brier", "draw_pick_rate", "scoreline_concentration", "sample_size", "completeness_pct"):
                self.assertIn(key, box)
        self.assertEqual(0.0, comparison["models"]["baseline"]["goal_mae"])
        self.assertEqual(0.14, comparison["models"]["baseline"]["outcome_brier"])
        self.assertEqual(0.5, comparison["models"]["v4"]["goal_mae"])

    def test_wc_bare_merged_ids_migrate_without_counting_snapshot_twice(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        previous = {
            "merged_lifecycle_match_ids": ["11"],
            "gw_results": [{"season": "2026", "gw": 19, "total": 1, "correct_winner": 1, "correct_score": 1, "exact_score": 1, "points": 3}],
            "total_evaluated": 1,
            "model_comparison": {"total": 1},
        }
        snapshot = {
            "match_id": 11,
            "match_key": "wc:2026:11",
            "season": "2026",
            "round": 19,
            "lock_verified": True,
            "checked": True,
            "active_strategy_at_lock": "v4",
            "picks": {
                "baseline": {"winner": "home", "home_score": 1, "away_score": 0},
                "v4": {"winner": "home", "home_score": 1, "away_score": 0},
            },
            "evaluations": {
                "baseline": {"winner_correct": True, "exact": True, "points": 3},
                "v4": {"winner_correct": True, "exact": True, "points": 3},
            },
        }
        merged = namespace["_wc_merge_verified_archive"](
            previous,
            {"model_comparison": {"total": 1}, "model_status": {}},
            {"matches": {"11": snapshot}},
        )
        self.assertEqual(1, merged["total_evaluated"])
        self.assertEqual(["wc:2026:11"], merged["merged_lifecycle_match_ids"])


    def test_league_learning_replaces_stale_verified_lifecycle_counts(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)

        stale_history = {
            "pl": {"model_comparison": {"total": 108}, "model_status": {"verified_lifecycle_samples": 108}},
            "laliga": {"model_comparison": {"total": 108}, "model_status": {"verified_lifecycle_samples": 108}},
        }

        def runner(**kwargs):
            updated = copy.deepcopy(kwargs["history"])
            league = kwargs["league"]
            packet = updated.setdefault(league, {})
            packet["model_status"] = {"verified_lifecycle_samples": 0}
            return updated, {}, {}

        namespace["run_persistent_competition"] = runner

        updated = namespace["run_league_learning"]([], {}, [], {}, stale_history)

        self.assertEqual(0, updated["pl"]["model_status"]["verified_lifecycle_samples"])
        self.assertEqual(0, updated["laliga"]["model_status"]["verified_lifecycle_samples"])
        self.assertEqual(108, updated["pl"]["model_comparison"]["total"])

    def test_laliga_learning_uses_current_pack_and_records_catalog_metadata(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {
            "__name__": "website.update_pl_mobile_test",
            "__package__": "website",
            "__file__": str(update_path),
        }
        exec(compile(prefix, str(update_path), "exec"), namespace)
        calls = []

        def runner(**kwargs):
            calls.append(kwargs)
            history = copy.deepcopy(kwargs["history"])
            history[kwargs["league"]] = {
                "gw_results": [],
                "total_evaluated": 0,
                "model_comparison": {"total": 0},
                "model_status": {"verified_lifecycle_samples": 0},
            }
            return history, {}, {}

        namespace["run_persistent_competition"] = runner
        current_fixture = {
            "id": 20262701,
            "source_fixture_id": 20262701,
            "season": "2026-27",
            "e": 1,
            "h": 1,
            "a": 2,
            "ko": "2026-08-15T18:00:00Z",
            "fin": False,
            "st": False,
            "hs": None,
            "as": None,
        }
        archive_fixture = {
            **current_fixture,
            "season": "2025-26",
        }

        history = namespace["run_league_learning"](
            [],
            {},
            [archive_fixture, current_fixture],
            {1: {"id": 1}, 2: {"id": 2}},
            {},
            ll_season="2026-27",
            ll_available_seasons=["2026-27", "2025-26"],
        )

        laliga_call = next(call for call in calls if call["league"] == "laliga")
        self.assertEqual([current_fixture], laliga_call["fixtures"])
        self.assertTrue(all(row["season"] == "2026-27" for row in laliga_call["fixtures"]))
        self.assertEqual(
            "laliga:2025-26:20262701",
            learning.competition_match_key("laliga", archive_fixture["season"], archive_fixture["source_fixture_id"]),
        )
        self.assertEqual(
            "laliga:2026-27:20262701",
            learning.competition_match_key("laliga", current_fixture["season"], current_fixture["source_fixture_id"]),
        )
        self.assertNotEqual(
            learning.competition_match_key("laliga", archive_fixture["season"], archive_fixture["source_fixture_id"]),
            learning.competition_match_key("laliga", current_fixture["season"], current_fixture["source_fixture_id"]),
        )
        self.assertEqual("2026-27", history["laliga"]["current_season"])
        self.assertEqual(["2026-27", "2025-26"], history["laliga"]["available_seasons"])
        self.assertEqual([], history["laliga"]["gw_results"])
        self.assertEqual(0, history["laliga"]["total_evaluated"])

    def test_laliga_current_season_only_locks_and_trains_when_source_id_repeats(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        source_fixture_id = 90210
        current_fixture = {
            "id": source_fixture_id,
            "source_fixture_id": source_fixture_id,
            "season": "2026-27",
            "e": 1,
            "h": 1,
            "a": 2,
            "ko": (now + timedelta(hours=10)).isoformat(),
            "fin": False,
            "st": False,
            "hs": None,
            "as": None,
        }
        archive_fixture = {
            **current_fixture,
            "season": "2025-26",
            "ko": (now - timedelta(days=2)).isoformat(),
        }
        archive_key = learning.competition_match_key("laliga", archive_fixture["season"], source_fixture_id)
        current_key = learning.competition_match_key("laliga", current_fixture["season"], source_fixture_id)
        trained_keys = []

        def trainer(model, rows):
            trained_keys.extend(row["match_key"] for row in rows)
            return self._trainer(model, rows)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "laliga-predictions.json"
            model_path = root / "laliga-weights.json"
            history, initial_counts, _ = run_persistent_competition(
                league="laliga", fixtures=[archive_fixture, current_fixture],
                teams={1: {"id": 1}, 2: {"id": 2}},
                prediction_path=str(prediction_path), model_path=str(model_path),
                history={}, now=now, snapshot_builder=self._snapshot_builder,
                model_trainer=trainer, default_model=default_model_state("laliga"),
            )
            completed_current = dict(current_fixture, fin=True, st=True, hs=2)
            completed_current["as"] = 0
            history, completed_counts, _ = run_persistent_competition(
                league="laliga", fixtures=[archive_fixture, completed_current],
                teams={1: {"id": 1}, 2: {"id": 2}},
                prediction_path=str(prediction_path), model_path=str(model_path),
                history=history, now=now + timedelta(days=1), snapshot_builder=self._snapshot_builder,
                model_trainer=trainer, default_model=default_model_state("laliga"),
            )
            stored_keys = {
                snapshot["match_key"]
                for snapshot in json.loads(prediction_path.read_text(encoding="utf-8"))["matches"].values()
            }

        self.assertNotEqual(archive_key, current_key)
        self.assertEqual(1, initial_counts["locked"])
        self.assertEqual(1, completed_counts["trained"])
        self.assertEqual([current_key], trained_keys)
        self.assertEqual({current_key}, stored_keys)

    def test_pl_compaction_preserves_selected_official_season_identity(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        compact = namespace["_compact_pl_learning_fixtures"]([{
            "id": 91, "e": 2, "h": 11, "a": 12,
            "ko": "2026-08-10T19:00:00Z", "fin": False, "st": False,
            "hs": None, "as": None,
        }], "2026-27")
        self.assertEqual({
            "id": 91, "source_fixture_id": 91, "season": "2026-27",
            "e": 2, "h": 11, "a": 12, "ko": "2026-08-10T19:00:00Z",
            "fin": False, "st": False, "hs": None, "as": None,
        }, compact[0])

    @staticmethod
    def _official_adapter_namespace(requests_client):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        compact_source = source[
            source.index("def _compact_pl_learning_fixtures"):source.index("def _set_verified_lifecycle_samples")
        ]
        adapter_source = source[
            source.index("def _pl_official_int"):source.index('print("Fetching Premier League data...")')
        ]
        namespace = {
            "BADGE": "badge/{}.png",
            "PL_OFFICIAL_COMPSEASON": "841",
            "PL_OFFICIAL_FIXTURES": "https://official.test/fixtures",
            "_mark_current_gws": lambda gameweeks, fixtures: None,
            "_plain_name": lambda value: str(value or ""),
            "datetime": datetime,
            "math": math,
            "re": __import__("re"),
            "requests": requests_client,
            "timezone": timezone,
        }
        exec(compile(compact_source, str(update_path), "exec"), namespace)
        exec(compile(adapter_source, str(update_path), "exec"), namespace)
        return namespace

    def test_selected_pl_official_result_is_scored_checked_and_trained_once(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        kickoff = now + timedelta(hours=10)

        def team(team_id, name, score=None):
            side = {
                "team": {
                    "id": team_id,
                    "name": name,
                    "shortName": name[:3].upper(),
                    "altIds": {"opta": f"t{team_id}"},
                },
            }
            if score is not None:
                side["score"] = score
            return side

        def fixture(status, sides):
            return {
                "id": 901,
                "altIds": {"opta": "g901"},
                "gameweek": {"gameweek": 2},
                "kickoff": {"millis": int(kickoff.timestamp() * 1000)},
                "status": status,
                "teams": sides,
            }

        class Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return {"content": self.content}

        class Requests:
            def __init__(self, responses):
                self.responses = list(responses)
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response(self.responses.pop(0))

        scheduled_requests = Requests([[fixture("U", [team(11, "Home"), team(12, "Away")])]])
        scheduled_namespace = self._official_adapter_namespace(scheduled_requests)
        scheduled_pack = scheduled_namespace["fetch_pl_official_season"]({}, {"User-Agent": "test"})
        scheduled = scheduled_namespace["_compact_pl_learning_fixtures"](
            scheduled_pack["fix"], scheduled_pack["season"],
        )

        result = fixture("C", [team(11, "Home", 2), team(12, "Away", 1)])
        completed_requests = Requests([
            [fixture("C", [team(11, "Home"), team(12, "Away")])],
            [result],
        ])
        completed_namespace = self._official_adapter_namespace(completed_requests)
        completed_pack = completed_namespace["fetch_pl_official_season"]({}, {"User-Agent": "test"})
        completed = completed_namespace["_compact_pl_learning_fixtures"](
            completed_pack["fix"], completed_pack["season"],
        )

        self.assertEqual(2, len(completed_requests.calls))
        self.assertEqual("C", completed_requests.calls[1][1]["params"]["statuses"])
        self.assertEqual({"id": 901, "season": "2026-27", "ko": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), "hs": 2, "as": 1}, {
            key: completed[0][key] for key in ("id", "season", "ko", "hs", "as")
        })

        calls = []

        def trainer(model, rows):
            calls.extend(row["match_key"] for row in rows)
            return self._trainer(model, rows)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            history_path = root / "history.json"
            run_persistent_competition(
                league="pl", fixtures=scheduled, teams=scheduled_pack["teams"],
                prediction_path=str(prediction_path), model_path=str(model_path),
                history_path=str(history_path), history={}, now=now,
                snapshot_builder=self._snapshot_builder, model_trainer=trainer,
                default_model=default_model_state("pl"),
            )
            history, counts, _ = run_persistent_competition(
                league="pl", fixtures=completed, teams=completed_pack["teams"],
                prediction_path=str(prediction_path), model_path=str(model_path),
                history_path=str(history_path), history={}, now=now + timedelta(days=1),
                snapshot_builder=self._snapshot_builder, model_trainer=trainer,
                default_model=default_model_state("pl"),
            )
            _, rerun_counts, _ = run_persistent_competition(
                league="pl", fixtures=completed, teams=completed_pack["teams"],
                prediction_path=str(prediction_path), model_path=str(model_path),
                history_path=str(history_path), history=history, now=now + timedelta(days=1, minutes=5),
                snapshot_builder=self._snapshot_builder, model_trainer=trainer,
                default_model=default_model_state("pl"),
            )
            snapshot = next(iter(json.loads(prediction_path.read_text(encoding="utf-8"))["matches"].values()))

        self.assertEqual(["pl:2026-27:901"], calls)
        self.assertEqual(1, counts["checked"])
        self.assertEqual(1, counts["trained"])
        self.assertEqual(0, rerun_counts["trained"])
        self.assertTrue(snapshot["checked"])
        self.assertTrue(snapshot["model_trained"])
        self.assertEqual((2, 1), (snapshot["actual_home_score"], snapshot["actual_away_score"]))

    def test_pl_official_secondary_result_failures_keep_primary_schedule(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)

        def team(team_id, name, score=None):
            side = {
                "team": {
                    "id": team_id,
                    "name": name,
                    "shortName": name[:3].upper(),
                    "altIds": {"opta": f"t{team_id}"},
                },
            }
            if score is not None:
                side["score"] = score
            return side

        def fixture(fixture_id, status, kickoff, sides):
            return {
                "id": fixture_id,
                "altIds": {"opta": f"g{fixture_id}"},
                "gameweek": {"gameweek": 2},
                "kickoff": {"millis": int(kickoff.timestamp() * 1000)},
                "status": status,
                "teams": sides,
            }

        completed_kickoff = now - timedelta(hours=4)
        upcoming_kickoff = now + timedelta(hours=10)
        primary_payload = {"content": [
            fixture(901, "C", completed_kickoff, [team(11, "Home"), team(12, "Away")]),
            fixture(902, "U", upcoming_kickoff, [team(13, "Next"), team(14, "Later")]),
        ]}

        class Response:
            def __init__(self, payload, status_error=None):
                self.payload = payload
                self.status_error = status_error

            def raise_for_status(self):
                if self.status_error is not None:
                    raise self.status_error

            def json(self):
                return self.payload

        class Requests:
            def __init__(self, failure):
                self.failure = failure
                self.calls = 0

            def get(self, url, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Response(primary_payload)
                if self.failure == "timeout":
                    raise TimeoutError("secondary result timeout")
                if self.failure == "http":
                    return Response({}, OSError("secondary result HTTP failure"))
                return Response({"content": "corrupt"})

        for failure in ("timeout", "http", "malformed"):
            with self.subTest(failure=failure):
                requests_client = Requests(failure)
                namespace = self._official_adapter_namespace(requests_client)
                season = namespace["fetch_pl_official_season"]({}, {"User-Agent": "test"})
                compact = namespace["_compact_pl_learning_fixtures"](
                    season["fix"], season["season"],
                )

                self.assertEqual("2026-27", season["season"])
                self.assertEqual(2, requests_client.calls)
                self.assertEqual([901, 902], [row["id"] for row in compact])
                self.assertEqual(
                    [completed_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), upcoming_kickoff.strftime("%Y-%m-%dT%H:%M:%SZ")],
                    [row["ko"] for row in compact],
                )
                self.assertEqual((True, None, None), (
                    compact[0]["fin"], compact[0]["hs"], compact[0]["as"],
                ))
                self.assertTrue(learning.eligible_to_lock(compact[1], now))
                self.assertEqual({11, 12, 13, 14}, set(season["teams"]))

    def test_pl_official_direct_schedule_scores_do_not_need_overlay(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        primary = {
            "id": 901,
            "altIds": {"opta": "g901"},
            "gameweek": {"gameweek": 2},
            "kickoff": {"millis": int(now.timestamp() * 1000)},
            "status": "C",
            "teams": [
                {"team": {"id": 11, "name": "Home", "shortName": "HOM", "altIds": {"opta": "t11"}}, "score": 2},
                {"team": {"id": 12, "name": "Away", "shortName": "AWA", "altIds": {"opta": "t12"}}, "score": 1},
            ],
        }

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"content": [primary]}

        class Requests:
            def __init__(self):
                self.calls = 0

            def get(self, url, **kwargs):
                self.calls += 1
                return Response()

        requests_client = Requests()
        namespace = self._official_adapter_namespace(requests_client)
        season = namespace["fetch_pl_official_season"]({}, {"User-Agent": "test"})

        self.assertEqual(1, requests_client.calls)
        self.assertEqual((2, 1), (season["fix"][0]["hs"], season["fix"][0]["as"]))

    def test_pl_official_scores_must_be_finite_non_negative_integers(self):
        class Requests:
            def get(self, *args, **kwargs):
                raise AssertionError("score parsing must not make a request")

        namespace = self._official_adapter_namespace(Requests())
        parser = namespace["_pl_official_score"]
        for value, expected in ((0, 0), (2, 2), ("3", 3), (4.0, 4)):
            with self.subTest(value=value):
                self.assertEqual(expected, parser(value))
        for value in (True, -1, 1.5, "1.5", math.nan, math.inf, -math.inf, "NaN"):
            with self.subTest(value=value):
                self.assertIsNone(parser(value))


class LearningEmbeddingTests(unittest.TestCase):
    def test_learning_runtime_embedding_is_compact_safe_and_complete(self):
        from website.learning_embed import embed_learning_runtime

        template = "/*__LEARNING_HISTORY__*/\n/*__LEARNING_RUNTIME__*/"
        runtime = "function activeWeights(){return EMBEDDED_MODELS.laliga.factors;}"
        rendered = embed_learning_runtime(
            template,
            {"laliga": {"factors": {"strength": 0.24}, "note": "</script><tag>\u2028\u2029"}},
            {"laliga": {"total_evaluated": 0, "note": "</script><tag>\u2028\u2029"}},
            runtime,
        )

        self.assertIn("var EMBEDDED_MODELS=", rendered)
        self.assertIn('"strength":0.24', rendered)
        self.assertNotIn("/*__LEARNING_HISTORY__*/", rendered)
        self.assertNotIn("/*__LEARNING_RUNTIME__*/", rendered)
        self.assertNotIn("</script", rendered.lower())
        self.assertNotIn("<tag>", rendered)
        self.assertIn("\\u003c/script\\u003e\\u003ctag\\u003e\\u2028\\u2029", rendered)
        self.assertEqual(1, rendered.count("function activeWeights"))
        self.assertLess(rendered.index('"laliga"'), rendered.index("function activeWeights"))

    def test_learning_runtime_embedding_rejects_missing_or_duplicate_markers(self):
        from website.learning_embed import embed_learning_runtime

        for template in (
            "/*__LEARNING_HISTORY__*/",
            "/*__LEARNING_RUNTIME__*/",
            "/*__LEARNING_HISTORY__*/ /*__LEARNING_HISTORY__*/ /*__LEARNING_RUNTIME__*/",
            "/*__LEARNING_HISTORY__*/ /*__LEARNING_RUNTIME__*/ /*__LEARNING_RUNTIME__*/",
        ):
            with self.assertRaises(ValueError):
                embed_learning_runtime(template, {}, {}, "function activeWeights(){}")

    def test_template_uses_shared_runtime_and_verified_lifecycle_status(self):
        root = Path(__file__).parents[1]
        template = (root / "website" / "pl_mobile_template.html").read_text(encoding="utf-8")

        self.assertEqual(1, template.count("/*__LEARNING_RUNTIME__*/"))
        self.assertNotIn("function activeWeights(){", template)
        self.assertNotIn("function activeCalibration(){", template)
        self.assertNotIn("function scoreModelChoice(){", template)
        self.assertIn("verified_lifecycle_samples", template)
        self.assertNotIn("lgData&&lgData.model_comparison&&lgData.model_comparison.total", template)
        self.assertIn("if(verified===0)complete=null", template)
        self.assertIn("trainedMatches", template)
        self.assertIn("var hasOwnWeights=(trainedMatches!==null&&trainedMatches>0)||verifiedSamples>0", template)
        self.assertIn("Base factor weights are shown until this league has enough completed learned rows.", template)
        self.assertIn("activeWeights()", template)
        self.assertNotIn("((lh.pl||{}).current_weights||{})", template)


if __name__ == "__main__":
    unittest.main()
