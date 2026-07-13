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
                prediction_path.write_text(json.dumps(store), encoding="utf-8")
                model_path.write_text(json.dumps(model), encoding="utf-8")
                pending_path.write_text(json.dumps({
                    "version": 2,
                    "league": league,
                    "generation_id": "mismatch" if league == "wc" else generation,
                    "store": store,
                    "model": model,
                    "history_entry": {"generation_id": generation},
                    "counts": {},
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
            wc_fixture.update({"id": 88, "source_fixture_id": 88, "e": 18, "grp": "A"})

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
            store={},
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
            store={},
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
            store={},
            model=default_model_state("wc"),
            snapshot_builder=self.snapshot_builder,
            model_trainer=lambda state, rows: state,
            now=self.now,
        )
        self.assertEqual(0, counts["locked"])
        self.assertEqual({}, store["matches"])


class FinalReviewStateLoadingTests(unittest.TestCase):
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
