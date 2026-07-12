import json
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
                histories[league] = history
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

    def test_malformed_state_falls_back_to_independent_default_model(self):
        now = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction_path = root / "predictions.json"
            model_path = root / "weights.json"
            prediction_path.write_text("{bad", encoding="utf-8")
            model_path.write_text("[]", encoding="utf-8")
            history, counts, model = run_persistent_competition(
                league="laliga", fixtures=[self._fixture(now)], teams={},
                prediction_path=str(prediction_path), model_path=str(model_path),
                history={}, now=now, snapshot_builder=self._snapshot_builder,
                model_trainer=self._trainer, default_model=default_model_state("laliga"),
            )

            stored = json.loads(prediction_path.read_text(encoding="utf-8"))
            saved_model = json.loads(model_path.read_text(encoding="utf-8"))

        self.assertEqual(1, counts["locked"])
        self.assertEqual("laliga", model["league"])
        self.assertEqual("laliga", saved_model["league"])
        self.assertIn("77", stored["matches"])
        self.assertEqual(1, history["snapshots_locked"])

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
                "id": 10, "e": 18, "grp": "A", "h": 1, "a": 2,
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
            "id": 11, "e": 19, "grp": "A", "h": 1, "a": 2,
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
        self.assertEqual(["11"], wc["merged_lifecycle_match_ids"])
        self.assertEqual(1, wc["model_status"]["verified_lifecycle_samples"])
        self.assertEqual(66, wc["model_comparison"]["total"])
        self.assertEqual(41, wc["model_comparison"]["baseline"]["winner_correct"])
        self.assertEqual(51, wc["model_comparison"]["baseline"]["points"])
        self.assertEqual(43, wc["model_comparison"]["challenger"]["winner_correct"])
        self.assertEqual(15, wc["model_comparison"]["challenger"]["exact_correct"])
        self.assertEqual(75, wc["model_comparison"]["challenger"]["points"])
        self.assertEqual({"gw": 19, "total": 1, "correct_winner": 1, "correct_score": 1, "exact_score": 1, "points": 3, "accuracy_pct": 100.0, "score_acc_pct": 100.0}, next(row for row in wc["gw_results"] if row["gw"] == 19))
        self.assertEqual(66, rerun["wc"]["total_evaluated"])
        self.assertEqual(["11"], rerun["wc"]["merged_lifecycle_match_ids"])

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

    def test_league_prepare_failure_saves_neither_competition(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl_prediction = root / "pl-predictions.json"
            pl_model = root / "pl-model.json"
            pl_prediction.write_text('{"before":"prediction"}', encoding="utf-8")
            pl_model.write_text('{"before":"model"}', encoding="utf-8")
            namespace["PL_PREDICTIONS_FILE"] = str(pl_prediction)
            namespace["PL_WEIGHTS_FILE"] = str(pl_model)
            namespace["LL_PREDICTIONS_FILE"] = str(root / "ll-predictions.json")
            namespace["LL_WEIGHTS_FILE"] = str(root / "ll-model.json")
            saves = []
            namespace["atomic_save_json"] = lambda path, value: saves.append(path)
            namespace["_prepare_persistent_competition"] = lambda **kwargs: (
                ({"matches": {}}, {"league": "pl"}, {"snapshots_locked": 0}, {})
                if kwargs["league"] == "pl" else (_ for _ in ()).throw(PermissionError("laliga denied"))
            )
            with self.assertRaisesRegex(PermissionError, "laliga denied"):
                namespace["run_league_learning"]([], {}, [], {}, {})
            self.assertEqual([], saves)
            self.assertEqual('{"before":"prediction"}', pl_prediction.read_text(encoding="utf-8"))
            self.assertEqual('{"before":"model"}', pl_model.read_text(encoding="utf-8"))

    def test_wc_merges_prepared_store_without_post_save_read(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        store = {"matches": {}}
        prepared = (store, {"league": "wc"}, {"gw_results": [], "model_comparison": {"total": 0}, "model_status": {}}, {"locked": 0, "checked": 0, "trained": 0})
        saves = []
        namespace["_prepare_persistent_competition"] = lambda **kwargs: prepared
        namespace["_load_json_file"] = lambda *args: (_ for _ in ()).throw(PermissionError("post-save read"))
        namespace["atomic_save_json"] = lambda path, value: saves.append(path)
        history = namespace["run_wc_learning"](json.dumps({"teams": {}, "fix": []}), {})
        self.assertEqual([], history["wc"]["gw_results"])
        self.assertEqual([namespace["WC_PREDICTIONS_FILE"], namespace["WC_WEIGHTS_FILE"], namespace["LEARNING_HISTORY_FILE"]], saves)

    def test_wc_prepare_read_error_saves_nothing(self):
        update_path = Path(__file__).parents[1] / "website" / "update_pl_mobile.py"
        source = update_path.read_text(encoding="utf-8")
        prefix = source[:source.index("\ndef _pl_official_int")]
        namespace = {"__name__": "website.update_pl_mobile_test", "__package__": "website", "__file__": str(update_path)}
        exec(compile(prefix, str(update_path), "exec"), namespace)
        saves = []
        namespace["_prepare_persistent_competition"] = lambda **kwargs: (_ for _ in ()).throw(PermissionError("wc denied"))
        namespace["atomic_save_json"] = lambda path, value: saves.append(path)
        with self.assertRaisesRegex(PermissionError, "wc denied"):
            namespace["run_wc_learning"](json.dumps({"teams": {}, "fix": []}), {})
        self.assertEqual([], saves)


if __name__ == "__main__":
    unittest.main()
