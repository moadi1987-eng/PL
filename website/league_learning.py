import copy
import json
import math
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from numbers import Real


STORE_VERSION = 1
LEDGER_VERSION = 1
PENDING_VERSION = 2
STATE_LOADED = "loaded"
STATE_MISSING = "missing"


class StateFileError(RuntimeError):
    """An existing state file cannot be trusted or safely migrated."""


class StateConsistencyError(StateFileError):
    """Persisted files disagree in a way that cannot be repaired safely."""


def _winner(home_score, away_score):
    if home_score > away_score:
        return "home"
    if away_score > home_score:
        return "away"
    return "draw"


def _valid_score(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_result(fixture):
    if not isinstance(fixture, dict) or fixture.get("fin") is not True:
        return False
    home_score = fixture.get("hs")
    away_score = fixture.get("as")
    if not (_valid_score(home_score) and _valid_score(away_score)):
        return False
    declared = fixture.get("winner")
    return declared is None or declared == _winner(home_score, away_score)


def competition_rule(league, fixture):
    if league in {"pl", "laliga"}:
        return {"key": "league", "result": 3, "exact": 5, "additive": True}
    if league != "wc" or not isinstance(fixture, dict):
        raise ValueError(f"invalid competition: {league!r}")
    day = fixture.get("e")
    if not _valid_score(day):
        raise ValueError("invalid World Cup phase")
    if fixture.get("grp"):
        if 1 <= day <= 17:
            return {"key": "group", "result": 1, "exact": 3, "additive": False}
        raise ValueError("invalid World Cup group round")
    if day == 35:
        return {"key": "final", "result": 8, "exact": 15, "additive": False}
    if day == 34:
        return {"key": "third", "result": 5, "exact": 10, "additive": False}
    if 32 <= day <= 33:
        return {"key": "semi", "result": 5, "exact": 10, "additive": False}
    if 28 <= day <= 31:
        return {"key": "quarter", "result": 4, "exact": 8, "additive": False}
    if 25 <= day <= 27:
        return {"key": "r16", "result": 2, "exact": 5, "additive": False}
    if 18 <= day <= 24:
        return {"key": "r32", "result": 2, "exact": 5, "additive": False}
    raise ValueError("invalid World Cup phase")


def _normalized_rule(rule):
    if not isinstance(rule, dict):
        raise ValueError("invalid competition rule")
    key = rule.get("key")
    expected = {
        "league": (3, 5, True),
        "group": (1, 3, False),
        "r32": (2, 5, False),
        "r16": (2, 5, False),
        "quarter": (4, 8, False),
        "semi": (5, 10, False),
        "third": (5, 10, False),
        "final": (8, 15, False),
    }.get(key)
    if expected is None:
        raise ValueError("invalid competition rule")
    result, exact, additive = expected
    if rule.get("result") != result or rule.get("exact") != exact:
        raise ValueError("invalid competition rule")
    if "additive" in rule and rule.get("additive") is not additive:
        raise ValueError("invalid competition rule")
    return {"key": key, "result": result, "exact": exact, "additive": additive}


def _canonical_snapshot_rule(snapshot, league):
    if league in {"pl", "laliga"}:
        return competition_rule(league, {})
    if league != "wc" or not isinstance(snapshot, dict):
        raise ValueError("invalid competition snapshot")
    phase = snapshot.get("phase")
    round_id = snapshot.get("round", snapshot.get("day"))
    if phase not in {"group", "r32", "r16", "quarter", "semi", "third", "final"}:
        raise ValueError("invalid World Cup phase context")
    if not _valid_score(round_id):
        raise ValueError("invalid World Cup round context")
    fixture = {"e": round_id}
    if phase == "group":
        fixture["grp"] = True
    rule = competition_rule("wc", fixture)
    if rule["key"] != phase:
        raise ValueError("inconsistent World Cup phase context")
    return rule


def score_pick(pick, fixture, rule):
    if not _valid_result(fixture):
        raise ValueError("invalid completed result")
    if not _valid_pick(pick):
        raise ValueError("invalid prediction")
    rule = _normalized_rule(rule)
    actual_winner = _winner(fixture["hs"], fixture["as"])
    picked_winner = pick["winner"]
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


def _finite_number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _probability_vector(value):
    if not isinstance(value, dict) or set(value) != {"home", "draw", "away"}:
        raise ValueError("invalid probability vector")
    numbers = [value[name] for name in ("home", "draw", "away")]
    if not all(_finite_number(number) and number >= 0 for number in numbers):
        raise ValueError("invalid probability vector")
    total = sum(numbers)
    if not (
        math.isclose(total, 100.0, abs_tol=0.11)
        or math.isclose(total, 1.0, abs_tol=0.0011)
    ):
        raise ValueError("invalid probability vector")
    numbers = [number / total for number in numbers]
    return dict(zip(("home", "draw", "away"), numbers))


def _strategy_probabilities(row, strategy):
    evidence = row.get("probabilities")
    if evidence is None:
        return None
    if isinstance(evidence, dict) and set(evidence) == {"home", "draw", "away"}:
        return _probability_vector(evidence)
    if not isinstance(evidence, dict) or set(evidence) != {"baseline", "v4"}:
        raise ValueError("invalid probability evidence")
    return _probability_vector(evidence[strategy])


def _positive_evaluation_count(value):
    return _finite_number(value) and value > 0


def _gameweek_accuracy(gw_results):
    total = correct = 0
    for row in gw_results if isinstance(gw_results, list) else []:
        if not isinstance(row, dict):
            continue
        row_total = row.get("total")
        row_correct = row.get("correct_winner")
        if not all(_finite_number(value) for value in (row_total, row_correct)):
            continue
        if row_total <= 0:
            continue
        total += row_total
        correct += row_correct
    return round(correct / total * 100, 1) if total else None


def _comparison_accuracy(history):
    comparison = history.get("model_comparison") if isinstance(history, dict) else None
    if not isinstance(comparison, dict) or not _positive_evaluation_count(comparison.get("total")):
        return None
    active = comparison.get("active_strategy")
    models = comparison.get("models")
    metrics = models.get(active) if isinstance(models, dict) and isinstance(active, str) else None
    accuracy = metrics.get("winner_accuracy") if isinstance(metrics, dict) else None
    return round(accuracy, 1) if _finite_number(accuracy) else None


def _has_accuracy_evidence(history):
    comparison = history.get("model_comparison") if isinstance(history, dict) else None
    comparison_total = comparison.get("total") if isinstance(comparison, dict) else None
    return (
        _gameweek_accuracy(history.get("gw_results") if isinstance(history, dict) else None) is not None
        or _positive_evaluation_count(comparison_total)
        or _positive_evaluation_count(history.get("total_evaluated") if isinstance(history, dict) else None)
    )


def comparison_summary(rows, active_strategy, candidate_strategy):
    strategies = (active_strategy, candidate_strategy)
    metrics = {
        name: {
            "winner_correct": 0,
            "exact_correct": 0,
            "points": 0,
            "draw_picks": 0,
            "goal_error": 0.0,
            "brier_total": 0.0,
            "brier_samples": 0,
            "scores": Counter(),
        }
        for name in strategies
    }
    valid_rows = []
    for row in rows:
        if row.get("locked") is not True or row.get("fixture", {}).get("fin") is not True:
            continue
        if not _valid_result(row.get("fixture")):
            raise ValueError("invalid comparison result")
        _normalized_rule(row.get("rule"))
        picks = row.get("picks")
        if not isinstance(picks, dict) or not all(_valid_pick(picks.get(name)) for name in strategies):
            raise ValueError("invalid comparison prediction")
        for name in strategies:
            _strategy_probabilities(row, name)
        valid_rows.append(row)
    for row in valid_rows:
        actual_winner = _winner(row["fixture"]["hs"], row["fixture"]["as"])
        for name in strategies:
            scored = score_pick(row["picks"][name], row["fixture"], row["rule"])
            pick = row["picks"][name]
            metrics[name]["winner_correct"] += int(scored["winner_correct"])
            metrics[name]["exact_correct"] += int(scored["exact"])
            metrics[name]["points"] += scored["points"]
            metrics[name]["draw_picks"] += int(pick["winner"] == "draw")
            metrics[name]["goal_error"] += (
                abs(pick["home_score"] - row["fixture"]["hs"])
                + abs(pick["away_score"] - row["fixture"]["as"])
            ) / 2
            metrics[name]["scores"][scored["score"]] += 1
            probabilities = _strategy_probabilities(row, name)
            if probabilities is not None:
                metrics[name]["brier_total"] += sum(
                    (probabilities[outcome] - int(outcome == actual_winner)) ** 2
                    for outcome in ("home", "draw", "away")
                )
                metrics[name]["brier_samples"] += 1
    total = len(valid_rows)
    for box in metrics.values():
        box["winner_accuracy"] = round(box["winner_correct"] / max(total, 1) * 100, 1)
        box["exact_accuracy"] = round(box["exact_correct"] / max(total, 1) * 100, 1)
        box["goal_mae"] = round(box["goal_error"] / max(total, 1), 3)
        box["outcome_brier"] = (
            round(box["brier_total"] / box["brier_samples"], 4)
            if box["brier_samples"] else None
        )
        box["draw_pick_rate"] = round(box["draw_picks"] / max(total, 1) * 100, 1)
        top_count = max(box["scores"].values(), default=0)
        box["scoreline_concentration"] = round(top_count / max(total, 1) * 100, 1)
        box["sample_size"] = total
        box["completeness_pct"] = 100.0 if total else 0.0
        box["brier_sample_size"] = box["brier_samples"]
        box["brier_completeness_pct"] = round(box["brier_samples"] / max(total, 1) * 100, 1)
        box["unique_scores"] = len(box["scores"])
        box["top_scores"] = [{"score": key, "count": count} for key, count in box["scores"].most_common(6)]
        for internal in ("scores", "goal_error", "brier_total", "brier_samples"):
            del box[internal]
    return {
        "total": total,
        "sample_size": total,
        "completeness_pct": round(total / max(len(rows), 1) * 100, 1),
        "active_strategy": active_strategy,
        "candidate_strategy": candidate_strategy,
        "models": metrics,
    }


def promotion_decision(comparison, minimum_samples=30):
    active_name = comparison["active_strategy"]
    candidate_name = comparison["candidate_strategy"]
    if comparison["total"] < minimum_samples:
        return {"promote": False, "status": "collecting", "next_active_strategy": active_name}
    active = comparison["models"][active_name]
    candidate = comparison["models"][candidate_name]
    if "winner_correct" not in active or "winner_correct" not in candidate:
        return {"promote": False, "status": "winner_guard", "next_active_strategy": active_name}
    if candidate["winner_correct"] < active["winner_correct"]:
        return {"promote": False, "status": "winner_guard", "next_active_strategy": active_name}
    if candidate["points"] <= active["points"]:
        return {"promote": False, "status": "points_guard", "next_active_strategy": active_name}
    return {"promote": True, "status": "promote", "next_active_strategy": candidate_name}


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


def _valid_pick(pick):
    return (
        isinstance(pick, dict)
        and pick.get("winner") in {"home", "away", "draw"}
        and _valid_score(pick.get("home_score"))
        and _valid_score(pick.get("away_score"))
        and pick.get("winner") == _winner(pick["home_score"], pick["away_score"])
    )


def _valid_unverified_legacy_pick(pick):
    return (
        isinstance(pick, dict)
        and pick.get("winner") in {"home", "away", "draw"}
        and _valid_score(pick.get("home_score"))
        and _valid_score(pick.get("away_score"))
    )


def _is_unverified_legacy(snapshot):
    return (
        isinstance(snapshot, dict)
        and snapshot.get("legacy") is True
        and snapshot.get("lock_verified") is False
    )


def _valid_lock_snapshot(snapshot):
    picks = snapshot.get("picks") if isinstance(snapshot, dict) else None
    if not isinstance(picks, dict) or not all(_valid_pick(picks.get(strategy)) for strategy in ("baseline", "v4")):
        return False
    try:
        for strategy in ("baseline", "v4"):
            _strategy_probabilities(snapshot, strategy)
    except ValueError:
        return False
    return True


def _default_season(league):
    return "2026" if league == "wc" else "2025-26"


def competition_match_key(league, season, source_fixture_id):
    if league not in {"pl", "laliga", "wc"}:
        raise ValueError("invalid competition identity")
    season_text = str(season or "").strip()
    if not season_text or isinstance(source_fixture_id, bool) or source_fixture_id in (None, ""):
        raise ValueError("incomplete competition identity")
    return f"{league}:{season_text}:{source_fixture_id}"


def _snapshot_identity(snapshot, league, fallback_id=None):
    source_fixture_id = snapshot.get("source_fixture_id", snapshot.get("match_id", fallback_id))
    season = snapshot.get("season") or _default_season(league)
    return season, source_fixture_id, competition_match_key(league, season, source_fixture_id)


def _fixture_identity(fixture, league):
    source_fixture_id = fixture.get("source_fixture_id", fixture.get("id"))
    season = fixture.get("season") or _default_season(league)
    return season, source_fixture_id, competition_match_key(league, season, source_fixture_id)


def _legacy_active_strategy(snapshot):
    picks = snapshot["picks"]
    active = snapshot.get("active_strategy_at_lock")
    if active in picks:
        return active
    prediction_strategy = str(snapshot.get("prediction_strategy") or "").lower()
    if ("v4" in prediction_strategy or snapshot.get("model_version") == 4) and "v4" in picks:
        return "v4"
    if "baseline" in picks:
        return "baseline"
    return "v4" if "v4" in picks else None


_FACTOR_KEYS = {
    "form", "strength", "position", "home_adv", "streak", "h2h",
    "home_away_split", "goals_trend", "upset", "clean_sheet", "draw_tendency",
}
_CALIBRATION_BOUNDS = {
    "goal_mult": (0.82, 1.22),
    "home_goal_bias": (-1.5, 1.5),
    "away_goal_bias": (-1.5, 1.5),
    "draw_bias": (0.25, 2.0),
    "zero_zero_penalty": (0.0, 1.0),
}
_COUNT_KEYS = {"locked", "checked", "trained", "skipped", "promoted"}
_STRATEGIES = {"baseline", "v4"}


def _state_error(message):
    raise StateConsistencyError(message)


def _require_verified_current_lock(snapshot):
    if snapshot.get("locked") is not True or snapshot.get("lock_verified") is not True:
        _state_error("current lifecycle snapshot requires a verified lock")


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _identity_value(value):
    return not isinstance(value, bool) and isinstance(value, (int, str)) and bool(str(value).strip())


def _nonnegative_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_generation(packet, label):
    if "generation_id" in packet and not _nonempty_string(packet["generation_id"]):
        _state_error(f"invalid {label} generation")


def _validate_factors(factors, label="model factors"):
    if not isinstance(factors, dict):
        _state_error(f"invalid {label}")
    for key, value in factors.items():
        if not _nonempty_string(key) or not _finite_number(value) or not 0 <= value <= 1:
            _state_error(f"invalid {label}")


def _validate_calibration(calibration, label="model calibration"):
    if not isinstance(calibration, dict):
        _state_error(f"invalid {label}")
    for key, (low, high) in _CALIBRATION_BOUNDS.items():
        if key in calibration and (
            not _finite_number(calibration[key]) or not low <= calibration[key] <= high
        ):
            _state_error(f"invalid {label}")


def _validate_meta(meta, label="model metadata"):
    if not isinstance(meta, dict):
        _state_error(f"invalid {label}")
    for key in ("trained_matches", "last_batch_size"):
        if key in meta and not _nonnegative_int(meta[key]):
            _state_error(f"invalid {label}")
    if "last_trained_at" in meta and not isinstance(meta["last_trained_at"], str):
        _state_error(f"invalid {label}")
    for key in ("avg_pred_goals", "avg_actual_goals"):
        if key in meta and (not _finite_number(meta[key]) or meta[key] < 0):
            _state_error(f"invalid {label}")
    for key in ("pred_draw_rate", "actual_draw_rate", "pred_zero_zero_rate", "actual_zero_zero_rate"):
        if key in meta and (not _finite_number(meta[key]) or not 0 <= meta[key] <= 1):
            _state_error(f"invalid {label}")


def _validate_top_scores(rows, label):
    if not isinstance(rows, list):
        _state_error(f"invalid {label}")
    for row in rows:
        if (
            not isinstance(row, dict)
            or not _nonempty_string(row.get("score"))
            or not _nonnegative_int(row.get("count"))
        ):
            _state_error(f"invalid {label}")


def _validate_metric_box(box, label):
    if not isinstance(box, dict):
        _state_error(f"invalid {label}")
    for key in (
        "winner_correct", "exact_correct", "points", "draw_picks", "unique_scores",
        "sample_size", "brier_sample_size",
    ):
        if key in box and not _nonnegative_int(box[key]):
            _state_error(f"invalid {label}")
    for key in ("winner_accuracy", "exact_accuracy", "draw_pick_rate", "scoreline_concentration", "completeness_pct", "brier_completeness_pct"):
        if key in box and (not _finite_number(box[key]) or not 0 <= box[key] <= 100):
            _state_error(f"invalid {label}")
    for key in ("goal_mae", "outcome_brier"):
        if key in box and box[key] is not None and (not _finite_number(box[key]) or box[key] < 0):
            _state_error(f"invalid {label}")
    if "top_scores" in box:
        _validate_top_scores(box["top_scores"], f"{label} top scores")


def _validate_comparison(comparison, label="model comparison"):
    if not isinstance(comparison, dict):
        _state_error(f"invalid {label}")
    for key in ("total", "sample_size"):
        if key in comparison and not _nonnegative_int(comparison[key]):
            _state_error(f"invalid {label}")
    for key in ("active_strategy", "candidate_strategy"):
        if key in comparison and comparison[key] not in _STRATEGIES:
            _state_error(f"invalid {label}")
    if "models" in comparison:
        if not isinstance(comparison["models"], dict):
            _state_error(f"invalid {label}")
        for strategy, box in comparison["models"].items():
            if strategy not in _STRATEGIES:
                _state_error(f"invalid {label}")
            _validate_metric_box(box, f"{label} {strategy}")
    for key in ("baseline", "challenger"):
        if key in comparison:
            _validate_metric_box(comparison[key], f"{label} {key}")
    for key in ("score_histograms", "lifecycle_score_histograms"):
        if key in comparison:
            if not isinstance(comparison[key], dict):
                _state_error(f"invalid {label}")
            for histogram in comparison[key].values():
                if not isinstance(histogram, dict) or any(
                    not _nonempty_string(score) or not _nonnegative_int(count)
                    for score, count in histogram.items()
                ):
                    _state_error(f"invalid {label}")
    if "score_histograms_complete" in comparison and (
        not isinstance(comparison["score_histograms_complete"], dict)
        or any(not isinstance(value, bool) for value in comparison["score_histograms_complete"].values())
    ):
        _state_error(f"invalid {label}")


def _validate_model_status(status, label="model status"):
    if not isinstance(status, dict):
        _state_error(f"invalid {label}")
    if "promote" in status and not isinstance(status["promote"], bool):
        _state_error(f"invalid {label}")
    if "status" in status and not _nonempty_string(status["status"]):
        _state_error(f"invalid {label}")
    for key in ("next_active_strategy", "active_strategy", "candidate_strategy"):
        if key in status and status[key] not in _STRATEGIES:
            _state_error(f"invalid {label}")
    for key in ("verified_lifecycle_samples", "display_archive_samples"):
        if key in status and not _nonnegative_int(status[key]):
            _state_error(f"invalid {label}")


def _validate_counts(counts, label="learning counts"):
    if not isinstance(counts, dict):
        _state_error(f"invalid {label}")
    for key in _COUNT_KEYS:
        if key in counts and not _nonnegative_int(counts[key]):
            _state_error(f"invalid {label}")


def _validate_evaluations(evaluations, picks):
    if not isinstance(evaluations, dict):
        _state_error("invalid snapshot evaluations")
    for strategy, evaluation in evaluations.items():
        if not _nonempty_string(strategy) or not isinstance(evaluation, dict):
            _state_error("invalid snapshot evaluations")
        if isinstance(picks, dict) and strategy not in picks:
            _state_error("evaluation is absent from snapshot picks")
        if "winner" in evaluation and evaluation["winner"] not in {"home", "away", "draw"}:
            _state_error("invalid snapshot evaluation winner")
        for key in ("winner_correct", "exact"):
            if key in evaluation and not isinstance(evaluation[key], bool):
                _state_error("invalid snapshot evaluations")
        if "points" in evaluation and (
            not _finite_number(evaluation["points"]) or evaluation["points"] < 0
        ):
            _state_error("invalid snapshot evaluations")
        if "score" in evaluation and not _nonempty_string(evaluation["score"]):
            _state_error("invalid snapshot evaluations")


def _validate_canonical_checked_snapshot(snapshot, picks, league):
    required_result = ("actual_home_score", "actual_away_score", "actual_winner")
    if not all(key in snapshot for key in required_result):
        _state_error("incomplete checked lifecycle result")
    fixture = {
        "fin": True,
        "hs": snapshot["actual_home_score"],
        "as": snapshot["actual_away_score"],
        "winner": snapshot["actual_winner"],
    }
    if not _valid_result(fixture):
        _state_error("invalid checked lifecycle result")
    try:
        rule = _normalized_rule(snapshot.get("rule"))
        if rule != _canonical_snapshot_rule(snapshot, league):
            raise ValueError("competition rule does not match locked context")
    except ValueError as error:
        raise StateConsistencyError("invalid checked lifecycle rule") from error
    evaluations = snapshot.get("evaluations")
    if (
        not isinstance(picks, dict)
        or not isinstance(evaluations, dict)
        or not evaluations
        or set(evaluations) != set(picks)
    ):
        _state_error("incomplete checked lifecycle evaluations")
    for strategy, pick in picks.items():
        try:
            canonical = score_pick(pick, fixture, rule)
        except ValueError as error:
            raise StateConsistencyError("invalid checked lifecycle evidence") from error
        evaluation = evaluations[strategy]
        if any(evaluation.get(key) != value for key, value in canonical.items()):
            _state_error("inconsistent checked lifecycle evaluation")


def _validate_snapshot(snapshot, league, fallback_id, lifecycle_store):
    if not isinstance(snapshot, dict):
        _state_error("invalid prediction snapshot")
    for key in ("locked", "legacy", "lock_verified", "checked", "model_trained"):
        if key in snapshot and not isinstance(snapshot[key], bool):
            _state_error("invalid lifecycle flag")
    if "lifecycle_version" in snapshot and snapshot["lifecycle_version"] != STORE_VERSION:
        _state_error("unsupported snapshot lifecycle version")
    if "league" in snapshot and snapshot["league"] != league:
        _state_error("snapshot league mismatch")
    if "season" in snapshot and not _nonempty_string(snapshot["season"]):
        _state_error("invalid snapshot season")
    for key in ("match_id", "source_fixture_id"):
        if key in snapshot and not _identity_value(snapshot[key]):
            _state_error("invalid snapshot source identity")
    for key in ("round", "day"):
        if key in snapshot and not _nonnegative_int(snapshot[key]):
            _state_error("invalid snapshot round")
    try:
        season, source_fixture_id, expected_key = _snapshot_identity(snapshot, league, fallback_id)
    except (TypeError, ValueError) as error:
        raise StateConsistencyError("invalid snapshot identity") from error
    if not _identity_value(source_fixture_id) or not _nonempty_string(str(season)):
        _state_error("invalid snapshot identity")
    if "match_key" in snapshot and snapshot["match_key"] != expected_key:
        _state_error("snapshot match key mismatch")

    pre_normalized_legacy = not (
        lifecycle_store or snapshot.get("lifecycle_version") == STORE_VERSION
    )
    unverified_legacy = _is_unverified_legacy(snapshot)
    legacy_flag = snapshot.get("legacy")
    lock_verified = snapshot.get("lock_verified")
    if legacy_flag is True and lock_verified is True:
        _state_error("contradictory legacy lifecycle flags")
    if snapshot.get("locked") is True and lock_verified is False and legacy_flag is not True:
        _state_error("nonlegacy snapshot cannot claim unverified lifecycle")
    if not pre_normalized_legacy and legacy_flag is True and not unverified_legacy:
        _state_error("invalid legacy lifecycle boundary")
    legacy_compatibility = pre_normalized_legacy or unverified_legacy
    if not legacy_compatibility:
        _require_verified_current_lock(snapshot)
        if league == "wc":
            try:
                _canonical_snapshot_rule(snapshot, league)
            except ValueError as error:
                raise StateConsistencyError("invalid World Cup lifecycle context") from error
    pick_validator = _valid_unverified_legacy_pick if unverified_legacy else _valid_pick
    picks = snapshot.get("picks")
    if "picks" in snapshot:
        if not isinstance(picks, dict) or not picks:
            _state_error("invalid snapshot picks")
        for strategy, pick in picks.items():
            if not _nonempty_string(strategy) or not pick_validator(pick):
                _state_error("invalid snapshot picks")
        if not legacy_compatibility and not _STRATEGIES.issubset(picks):
            _state_error("incomplete lifecycle snapshot picks")
    for key in ("base_v3_prediction", "v4_shadow"):
        if key in snapshot and not pick_validator(snapshot[key]):
            _state_error("invalid legacy snapshot pick")
    legacy_pick_fields = ("winner", "home_score", "away_score")
    if any(key in snapshot for key in legacy_pick_fields) and not pick_validator({
        key: snapshot.get(key) for key in legacy_pick_fields
    }):
        _state_error("invalid legacy snapshot pick")
    if "active_strategy_at_lock" in snapshot and (
        snapshot["active_strategy_at_lock"] not in _STRATEGIES
        or isinstance(picks, dict) and snapshot["active_strategy_at_lock"] not in picks
    ):
        _state_error("invalid snapshot strategy")

    actual_present = any(key in snapshot for key in ("actual_home_score", "actual_away_score", "actual_winner"))
    if actual_present:
        home_score = snapshot.get("actual_home_score")
        away_score = snapshot.get("actual_away_score")
        if not _valid_score(home_score) or not _valid_score(away_score):
            _state_error("invalid snapshot result")
        actual_winner = _winner(home_score, away_score)
        if snapshot.get("actual_winner", actual_winner) != actual_winner:
            _state_error("inconsistent snapshot result")
    if "evaluations" in snapshot:
        _validate_evaluations(snapshot["evaluations"], picks)
    for key in ("rule", "phase_rule"):
        if key in snapshot:
            try:
                _normalized_rule(snapshot[key])
            except ValueError as error:
                raise StateConsistencyError("invalid snapshot rule") from error
    for key in ("probabilities", "evaluation_probabilities"):
        if key in snapshot:
            evidence = {"probabilities": snapshot[key]}
            try:
                for strategy in _STRATEGIES:
                    _strategy_probabilities(evidence, strategy)
            except ValueError as error:
                raise StateConsistencyError("invalid snapshot probability evidence") from error
    for key in ("features", "factor_edges", "missing"):
        if key in snapshot and not isinstance(snapshot[key], dict):
            _state_error("invalid snapshot feature state")
    for key in ("created_at", "checked_at", "trained_at", "kickoff_time"):
        if key in snapshot and not isinstance(snapshot[key], str):
            _state_error("invalid snapshot timestamp")
    if snapshot.get("checked") is True and not legacy_compatibility:
        _validate_canonical_checked_snapshot(snapshot, picks, league)
    return expected_key


def validate_prediction_store(raw, league):
    if not isinstance(raw, dict):
        _state_error("prediction store must be an object")
    if "version" in raw and not _nonnegative_int(raw["version"]):
        _state_error("invalid prediction store version")
    if "lifecycle_version" in raw and raw["lifecycle_version"] != STORE_VERSION:
        _state_error("unsupported prediction lifecycle version")
    if "league" in raw and raw["league"] != league:
        _state_error("prediction store league mismatch")
    _validate_generation(raw, "prediction store")
    if "updated_at" in raw and not isinstance(raw["updated_at"], str):
        _state_error("invalid prediction store timestamp")
    if "matches" not in raw:
        seen_ids = set()
        for round_key, packet in raw.items():
            if not str(round_key).isdigit():
                continue
            if not isinstance(packet, dict) or not isinstance(packet.get("predictions"), list):
                _state_error("invalid legacy prediction round")
            for pick in packet["predictions"]:
                if (
                    not isinstance(pick, dict)
                    or not _identity_value(pick.get("match_id"))
                    or not _valid_unverified_legacy_pick(pick)
                ):
                    _state_error("invalid legacy prediction row")
                match_id = str(pick["match_id"])
                if match_id in seen_ids:
                    _state_error("duplicate legacy prediction identity")
                seen_ids.add(match_id)
        return raw
    matches = raw["matches"]
    if not isinstance(matches, (dict, list)):
        _state_error("invalid prediction match collection")
    lifecycle_store = raw.get("lifecycle_version") == STORE_VERSION
    seen_keys = set()
    iterable = matches.items() if isinstance(matches, dict) else enumerate(matches)
    for fallback_id, snapshot in iterable:
        match_key = _validate_snapshot(snapshot, league, fallback_id, lifecycle_store)
        if match_key in seen_keys:
            _state_error(f"duplicate lifecycle identity: {match_key}")
        seen_keys.add(match_key)
    return raw


def validate_model_state(model, league):
    if not isinstance(model, dict):
        _state_error("model state must be an object")
    if "version" in model and not _nonnegative_int(model["version"]):
        _state_error("invalid model version")
    if "league" in model and model["league"] != league:
        _state_error("model league mismatch")
    _validate_generation(model, "model")
    for key in ("active_strategy", "candidate_strategy"):
        if key in model and model[key] not in _STRATEGIES:
            _state_error("invalid model strategy")
    if all(key in model for key in ("active_strategy", "candidate_strategy")) and model["active_strategy"] == model["candidate_strategy"]:
        _state_error("model strategies must differ")
    if "factors" in model:
        _validate_factors(model["factors"])
    else:
        for key in _FACTOR_KEYS:
            if key in model and (not _finite_number(model[key]) or not 0 <= model[key] <= 1):
                _state_error("invalid legacy model factors")
    if "calibration" in model:
        _validate_calibration(model["calibration"])
    if "meta" in model:
        _validate_meta(model["meta"])
    if "applied_ledger_version" in model and model["applied_ledger_version"] != LEDGER_VERSION:
        _state_error("invalid applied-match ledger version")
    if "applied_match_keys" in model:
        keys = model["applied_match_keys"]
        if (
            not isinstance(keys, list)
            or not all(_nonempty_string(key) for key in keys)
            or len(keys) != len(set(keys))
        ):
            _state_error("invalid applied-match ledger")
    if "promotion_history" in model:
        rows = model["promotion_history"]
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            _state_error("invalid promotion history")
        for row in rows:
            for key in ("from", "to"):
                if key in row and row[key] not in _STRATEGIES:
                    _state_error("invalid promotion history")
            if "at" in row and not isinstance(row["at"], str):
                _state_error("invalid promotion history")
            if "comparison" in row:
                _validate_comparison(row["comparison"], "promotion comparison")
    if "comparison" in model:
        _validate_comparison(model["comparison"])
    if "status" in model:
        _validate_model_status(model["status"])
    return model


def _validate_history_row(row, league):
    if not isinstance(row, dict):
        _state_error("invalid history gameweek row")
    if "season" in row and not _nonempty_string(row["season"]):
        _state_error("invalid history season")
    if "gw" in row and not _nonnegative_int(row["gw"]):
        _state_error("invalid history round")
    for key in ("total", "correct_winner", "correct_score", "exact_score", "points"):
        if key in row and not _nonnegative_int(row[key]):
            _state_error("invalid history gameweek row")
    total = row.get("total")
    if _nonnegative_int(total):
        for key in ("correct_winner", "correct_score", "exact_score"):
            if key in row and row[key] > total:
                _state_error("inconsistent history gameweek row")
    for key in ("accuracy_pct", "score_acc_pct"):
        if key in row and (not _finite_number(row[key]) or not 0 <= row[key] <= 100):
            _state_error("invalid history accuracy")


def validate_history_entry(entry, league):
    if not isinstance(entry, dict):
        _state_error(f"invalid {league} history entry")
    _validate_generation(entry, f"{league} history")
    if "gw_results" in entry:
        if not isinstance(entry["gw_results"], list):
            _state_error("invalid history gameweek rows")
        for row in entry["gw_results"]:
            _validate_history_row(row, league)
    for key in ("total_evaluated", "snapshots_locked", "trained_this_build", "refreshed_predictions"):
        if key in entry and not _nonnegative_int(entry[key]):
            _state_error(f"invalid {league} history count")
    for key in ("overall_accuracy", "data_completeness_pct"):
        if key in entry and (not _finite_number(entry[key]) or not 0 <= entry[key] <= 100):
            _state_error(f"invalid {league} history metric")
    if "current_weights" in entry:
        _validate_factors(entry["current_weights"], "history weights")
    if "calibration" in entry:
        _validate_calibration(entry["calibration"], "history calibration")
    if "model_meta" in entry:
        _validate_meta(entry["model_meta"], "history model metadata")
    if "model_comparison" in entry:
        _validate_comparison(entry["model_comparison"], "history model comparison")
    if "model_status" in entry:
        _validate_model_status(entry["model_status"], "history model status")
    if "counts" in entry:
        _validate_counts(entry["counts"], "history counts")
    if "current_season" in entry and not _nonempty_string(entry["current_season"]):
        _state_error("invalid current history season")
    if "available_seasons" in entry and (
        not isinstance(entry["available_seasons"], list)
        or not all(_nonempty_string(season) for season in entry["available_seasons"])
    ):
        _state_error("invalid available history seasons")
    if "merged_lifecycle_match_ids" in entry and (
        not isinstance(entry["merged_lifecycle_match_ids"], list)
        or not all(_nonempty_string(match_id) for match_id in entry["merged_lifecycle_match_ids"])
        or len(entry["merged_lifecycle_match_ids"]) != len(set(entry["merged_lifecycle_match_ids"]))
    ):
        _state_error("invalid merged lifecycle identities")
    return entry


def validate_global_history(history):
    if not isinstance(history, dict):
        _state_error("learning history must be an object")
    for league in ("pl", "laliga", "wc"):
        if league in history:
            validate_history_entry(history[league], league)
    return history


def normalize_prediction_store(raw, league, legacy_candidate_builder=None):
    raw = copy.deepcopy({} if raw is None else raw)
    validate_prediction_store(raw, league)
    if isinstance(raw.get("matches"), list):
        matches = {}
        for snapshot in raw["matches"]:
            if not isinstance(snapshot, dict):
                continue
            match_id = snapshot.get("match_id", snapshot.get("id"))
            if match_id is not None:
                matches[str(match_id)] = snapshot
        raw["matches"] = matches
    if isinstance(raw.get("matches"), dict):
        is_lifecycle_store = raw.get("lifecycle_version") == STORE_VERSION
        raw.setdefault("version", STORE_VERSION)
        raw.setdefault("league", league)
        raw.setdefault("lifecycle_version", STORE_VERSION)
        for match_id, snapshot in list(raw["matches"].items()):
            if not isinstance(snapshot, dict):
                del raw["matches"][match_id]
                continue
            is_legacy_snapshot = _is_unverified_legacy(snapshot) or not (
                is_lifecycle_store or snapshot.get("lifecycle_version") == STORE_VERSION
            )
            unverified_legacy = _is_unverified_legacy(snapshot)
            snapshot.setdefault("match_id", int(match_id) if str(match_id).isdigit() else match_id)
            season, source_fixture_id, match_key = _snapshot_identity(snapshot, league, match_id)
            snapshot.setdefault("season", season)
            snapshot.setdefault("source_fixture_id", source_fixture_id)
            snapshot.setdefault("match_key", match_key)
            snapshot.setdefault("league", league)
            if unverified_legacy:
                continue
            snapshot.setdefault("locked", True)
            if is_legacy_snapshot:
                snapshot["legacy"] = True
                snapshot["lock_verified"] = False
            else:
                snapshot.setdefault("lifecycle_version", STORE_VERSION)
                snapshot.setdefault("lock_verified", snapshot.get("locked") is True)
            if "round" not in snapshot and snapshot.get("day") is not None:
                snapshot["round"] = snapshot["day"]
            if "picks" not in snapshot:
                baseline = copy.deepcopy(snapshot.get("base_v3_prediction") or {
                    "winner": snapshot.get("winner"),
                    "home_score": snapshot.get("home_score"),
                    "away_score": snapshot.get("away_score"),
                })
                prediction_strategy = str(snapshot.get("prediction_strategy") or "").lower()
                active_v4 = {
                    "winner": snapshot.get("winner"),
                    "home_score": snapshot.get("home_score"),
                    "away_score": snapshot.get("away_score"),
                }
                candidate = copy.deepcopy(snapshot.get("v4_shadow") or (
                    active_v4 if "v4" in prediction_strategy or snapshot.get("model_version") == 4
                    else legacy_candidate_builder(snapshot) if legacy_candidate_builder else baseline
                ))
                snapshot["picks"] = {"baseline": baseline, "v4": candidate}
                snapshot["legacy"] = True
                snapshot["lock_verified"] = False
            picks = snapshot.get("picks")
            snapshot["picks"] = {
                name: pick for name, pick in picks.items()
                if _valid_pick(pick)
            } if isinstance(picks, dict) else {}
            if snapshot.get("checked") and _is_unverified_legacy(snapshot):
                snapshot["model_trained"] = True
            if _is_unverified_legacy(snapshot) and snapshot.get("active_strategy_at_lock") not in snapshot["picks"]:
                active_strategy = _legacy_active_strategy(snapshot)
                if active_strategy:
                    snapshot["active_strategy_at_lock"] = active_strategy
            actual_scores = (snapshot.get("actual_home_score"), snapshot.get("actual_away_score"))
            if snapshot.get("checked") and not snapshot.get("evaluations") and all(
                _valid_score(score) for score in actual_scores
            ):
                fixture = {"fin": True, "hs": actual_scores[0], "as": actual_scores[1], "e": snapshot.get("round")}
                if snapshot.get("phase") == "group":
                    fixture["grp"] = "legacy"
                try:
                    rule = _normalized_rule(
                        snapshot.get("rule") or snapshot.get("phase_rule") or competition_rule(league, fixture)
                    )
                    actual_winner = _winner(*actual_scores)
                    if snapshot.get("actual_winner") not in (None, actual_winner):
                        raise ValueError("inconsistent stored winner")
                    snapshot.setdefault("actual_winner", actual_winner)
                    snapshot.setdefault("rule", rule)
                    snapshot["evaluations"] = {
                        name: score_pick(pick, fixture, rule)
                        for name, pick in snapshot["picks"].items()
                    }
                except ValueError:
                    pass
        return raw
    matches = {}
    for round_key, packet in raw.items():
        if not isinstance(packet, dict):
            continue
        packet_metadata = {
            key: copy.deepcopy(value)
            for key, value in packet.items()
            if key != "predictions"
        }
        for pick in packet.get("predictions", []):
            match_id = str(pick.get("match_id") or "")
            if not match_id:
                continue
            baseline = copy.deepcopy(pick)
            candidate = legacy_candidate_builder(baseline) if legacy_candidate_builder else copy.deepcopy(baseline)
            matches[match_id] = {
                "match_id": pick.get("match_id"), "source_fixture_id": pick.get("match_id"),
                "match_key": competition_match_key(league, _default_season(league), pick.get("match_id")),
                "league": league, "season": _default_season(league), "round": int(round_key),
                "created_at": packet.get("created_at", ""), "locked": True, "legacy": True,
                "lock_verified": False, "checked": False, "model_trained": True,
                "features": {}, "missing": {"legacy_features": True},
                "legacy_packet_metadata": copy.deepcopy(packet_metadata),
                "picks": {"baseline": baseline, "v4": candidate},
            }
    return {
        "version": STORE_VERSION,
        "lifecycle_version": STORE_VERSION,
        "league": league,
        "matches": matches,
        "updated_at": "",
    }


def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _validate_finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for nested in value.values():
            _validate_finite_json(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_finite_json(nested)


def load_json_state(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle, parse_constant=_reject_json_constant)
        if not isinstance(value, dict):
            raise StateFileError(f"existing state is not a JSON object: {path}")
        _validate_finite_json(value)
        return value, STATE_LOADED
    except FileNotFoundError:
        return copy.deepcopy(default), STATE_MISSING
    except (UnicodeError, TypeError, ValueError) as error:
        raise StateFileError(f"existing state is invalid: {path}") from error


def atomic_save_json(path, value):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".learning-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


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
    timestamp = _utc(now).strftime("%Y-%m-%dT%H:%M:%SZ")

    snapshots_by_key = {}
    storage_keys_by_match = {}
    for storage_key, snapshot in store["matches"].items():
        if not _is_unverified_legacy(snapshot):
            _require_verified_current_lock(snapshot)
        match_key = snapshot.get("match_key")
        if match_key in snapshots_by_key:
            raise StateConsistencyError(f"duplicate lifecycle identity: {match_key}")
        snapshots_by_key[match_key] = snapshot
        storage_keys_by_match[match_key] = str(storage_key)

    fixture_map = {}
    normalized_fixtures = []
    for fixture in fixtures if isinstance(fixtures, list) else []:
        if not isinstance(fixture, dict):
            counts["skipped"] += 1
            continue
        try:
            season, source_fixture_id, match_key = _fixture_identity(fixture, league)
        except ValueError:
            counts["skipped"] += 1
            continue
        normalized = copy.deepcopy(fixture)
        normalized.update({
            "league": league,
            "season": season,
            "source_fixture_id": source_fixture_id,
            "match_key": match_key,
        })
        if match_key in fixture_map:
            raise StateConsistencyError(f"duplicate fixture identity: {match_key}")
        fixture_map[match_key] = normalized
        normalized_fixtures.append(normalized)

    ledger_present = model.get("applied_ledger_version") == LEDGER_VERSION
    raw_ledger = model.get("applied_match_keys", [])
    if ledger_present and (
        not isinstance(raw_ledger, list)
        or not all(isinstance(key, str) and key for key in raw_ledger)
        or len(raw_ledger) != len(set(raw_ledger))
    ):
        raise StateConsistencyError("invalid applied-match ledger")
    applied = set(raw_ledger if ledger_present else [])
    if not ledger_present:
        applied.update(
            snapshot["match_key"]
            for snapshot in store["matches"].values()
            if snapshot.get("model_trained") is True or snapshot.get("checked") is True or _is_unverified_legacy(snapshot)
        )
    model["applied_ledger_version"] = LEDGER_VERSION
    model["applied_match_keys"] = sorted(applied)

    for fixture in normalized_fixtures:
        match_key = fixture["match_key"]
        if match_key in snapshots_by_key:
            continue
        if not eligible_to_lock(fixture, now, lock_hours):
            counts["skipped"] += 1
            continue
        locked_round = fixture.get("e", fixture.get("day"))
        try:
            if league == "wc" and not _valid_score(locked_round):
                raise ValueError("invalid World Cup round context")
            rule_fixture = {**fixture, "e": locked_round} if league == "wc" else fixture
            locked_rule = competition_rule(league, rule_fixture)
        except ValueError:
            counts["skipped"] += 1
            continue
        snapshot = copy.deepcopy(snapshot_builder(fixture, model))
        if not _valid_lock_snapshot(snapshot):
            counts["skipped"] += 1
            continue
        snapshot.update({
            "match_id": fixture.get("id", fixture["source_fixture_id"]),
            "source_fixture_id": fixture["source_fixture_id"],
            "match_key": match_key,
            "league": league,
            "season": fixture["season"],
            "round": locked_round,
            "home_id": fixture.get("h"), "away_id": fixture.get("a"),
            "kickoff_time": fixture.get("ko", ""), "created_at": timestamp,
            "locked": True, "checked": False, "model_trained": False,
            "lifecycle_version": STORE_VERSION, "lock_verified": True,
            "active_strategy_at_lock": model.get("active_strategy", "baseline"),
        })
        if league == "wc":
            snapshot["phase"] = locked_rule["key"]
        preferred_storage_key = str(fixture["source_fixture_id"])
        storage_key = preferred_storage_key if preferred_storage_key not in store["matches"] else match_key
        store["matches"][storage_key] = snapshot
        snapshots_by_key[match_key] = snapshot
        storage_keys_by_match[match_key] = storage_key
        counts["locked"] += 1

    comparison_rows = []
    train_batch = []
    for snapshot in store["matches"].values():
        match_key = snapshot["match_key"]
        if _is_unverified_legacy(snapshot) and snapshot.get("checked") is True:
            continue
        if not _is_unverified_legacy(snapshot):
            _require_verified_current_lock(snapshot)
        fixture = fixture_map.get(match_key)
        if not snapshot.get("checked") and fixture and fixture.get("fin") is True:
            try:
                if not _valid_result(fixture):
                    raise ValueError("invalid completed result")
                if _is_unverified_legacy(snapshot) and (
                    historical_rule := snapshot.get("rule") or snapshot.get("phase_rule")
                ):
                    rule = _normalized_rule(historical_rule)
                else:
                    rule = _canonical_snapshot_rule(snapshot, league)
                probabilities = snapshot.get("probabilities")
                if probabilities is not None:
                    for strategy in ("baseline", "v4"):
                        _strategy_probabilities(snapshot, strategy)
                evaluations = {
                    name: score_pick(pick, fixture, rule)
                    for name, pick in snapshot.get("picks", {}).items()
                }
            except ValueError:
                counts["skipped"] += 1
                continue
            snapshot["checked"] = True
            snapshot["checked_at"] = timestamp
            snapshot["actual_home_score"] = fixture["hs"]
            snapshot["actual_away_score"] = fixture["as"]
            snapshot["actual_winner"] = _winner(fixture["hs"], fixture["as"])
            snapshot["rule"] = rule
            snapshot["evaluations"] = evaluations
            if probabilities is not None:
                snapshot["evaluation_probabilities"] = copy.deepcopy(probabilities)
            counts["checked"] += 1

        if _is_unverified_legacy(snapshot):
            continue
        if match_key in applied:
            snapshot["model_trained"] = True
        elif ledger_present and snapshot.get("model_trained") is True:
            raise StateConsistencyError(f"trained flag is absent from model ledger: {match_key}")

        if not snapshot.get("checked"):
            continue
        stored_fixture = {
            "fin": True,
            "hs": snapshot.get("actual_home_score"),
            "as": snapshot.get("actual_away_score"),
            "winner": snapshot.get("actual_winner"),
        }
        try:
            if not _valid_result(stored_fixture):
                raise ValueError("invalid stored result")
            rule = _normalized_rule(snapshot.get("rule"))
            if rule != _canonical_snapshot_rule(snapshot, league):
                raise ValueError("stored rule does not match locked context")
            picks = snapshot.get("picks", {})
            if not all(_valid_pick(picks.get(strategy)) for strategy in ("baseline", "v4")):
                raise ValueError("invalid stored prediction")
            probabilities = snapshot.get("evaluation_probabilities", snapshot.get("probabilities"))
            evidence_row = {
                "match_id": snapshot.get("match_id"),
                "match_key": match_key,
                "locked": snapshot.get("locked") is True and snapshot.get("lock_verified") is True,
                "fixture": stored_fixture,
                "rule": rule,
                "picks": picks,
            }
            if probabilities is not None:
                evidence_row["probabilities"] = probabilities
                for strategy in ("baseline", "v4"):
                    _strategy_probabilities(evidence_row, strategy)
        except ValueError:
            counts["skipped"] += 1
            continue

        if {"baseline", "v4"}.issubset(snapshot.get("picks", {})):
            comparison_rows.append({
                **evidence_row,
            })
        if match_key not in applied and not _is_unverified_legacy(snapshot):
            training_row = copy.deepcopy(snapshot)
            training_row["fixture"] = copy.deepcopy(stored_fixture)
            train_batch.append(training_row)

    if train_batch:
        model = model_trainer(model, train_batch)
        if not isinstance(model, dict):
            raise StateConsistencyError("model trainer returned invalid state")
        for row in train_batch:
            match_key = row["match_key"]
            applied.add(match_key)
            stored = snapshots_by_key[match_key]
            stored["model_trained"] = True
            stored["trained_at"] = timestamp
        counts["trained"] = len(train_batch)
    model["applied_ledger_version"] = LEDGER_VERSION
    model["applied_match_keys"] = sorted(applied)

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
        if not _is_unverified_legacy(snapshot):
            _require_verified_current_lock(snapshot)
        strategy_at_lock = snapshot.get("active_strategy_at_lock", "baseline")
        evaluation = (snapshot.get("evaluations") or {}).get(strategy_at_lock)
        if not evaluation:
            continue
        round_id = int(snapshot.get("round") or 0)
        season = str(snapshot.get("season") or _default_season(league))
        row = by_round.setdefault((season, round_id), {"season": season, "gw": round_id, "total": 0, "correct_winner": 0, "correct_score": 0, "exact_score": 0, "points": 0})
        row["total"] += 1
        row["correct_winner"] += int(evaluation["winner_correct"])
        row["correct_score"] += int(evaluation["exact"])
        row["exact_score"] += int(evaluation["exact"])
        row["points"] += evaluation["points"]
        missing_flags = list((snapshot.get("missing") or {}).values())
        if missing_flags:
            completeness_values.append(sum(not bool(value) for value in missing_flags) / len(missing_flags))
    gw_results = []
    for row in sorted(by_round.values(), key=lambda item: (item["season"], item["gw"])):
        row["accuracy_pct"] = round(row["correct_winner"] / max(row["total"], 1) * 100, 1)
        row["score_acc_pct"] = round(row["correct_score"] / max(row["total"], 1) * 100, 1)
        gw_results.append(row)
    overall_accuracy = _gameweek_accuracy(gw_results)
    if overall_accuracy is None:
        overall_accuracy = comparison["models"].get(model["active_strategy"], {}).get("winner_accuracy", 0)
    available_seasons = sorted({row["season"] for row in gw_results})
    fixture_seasons = sorted({fixture["season"] for fixture in normalized_fixtures})
    current_season = fixture_seasons[-1] if fixture_seasons else (available_seasons[-1] if available_seasons else _default_season(league))
    history = {
        "gw_results": gw_results,
        "overall_accuracy": overall_accuracy,
        "total_evaluated": sum(row["total"] for row in gw_results),
        "current_weights": model.get("factors", {}),
        "calibration": model.get("calibration", {}),
        "model_meta": model.get("meta", {}),
        "model_comparison": comparison,
        "model_status": {
            **decision,
            "active_strategy": model["active_strategy"],
            "candidate_strategy": model["candidate_strategy"],
            "verified_lifecycle_samples": comparison["total"],
        },
        "data_completeness_pct": round(sum(completeness_values) / len(completeness_values) * 100, 1) if completeness_values else 100.0,
        "snapshots_locked": len(store["matches"]),
        "trained_this_build": counts["trained"],
        "counts": copy.deepcopy(counts),
        "current_season": current_season,
        "available_seasons": sorted(set(available_seasons + fixture_seasons)),
    }
    return store, model, history, counts


def _persistent_model(raw_model, default_model, league):
    default = copy.deepcopy(default_model) if isinstance(default_model, dict) else {}
    validate_model_state(raw_model, league)
    raw = raw_model
    model = copy.deepcopy(default)

    if "factors" in raw or "calibration" in raw or "meta" in raw:
        for key, value in raw.items():
            if key in {"factors", "calibration", "meta"}:
                continue
            model[key] = copy.deepcopy(value)
        for key in ("factors", "calibration", "meta"):
            default_value = model.get(key)
            raw_value = raw.get(key)
            if isinstance(default_value, dict):
                merged = copy.deepcopy(default_value)
                if isinstance(raw_value, dict):
                    merged.update(copy.deepcopy(raw_value))
                model[key] = merged
            elif isinstance(raw_value, dict):
                model[key] = copy.deepcopy(raw_value)
    elif isinstance(model.get("factors"), dict):
        model["factors"].update({
            key: copy.deepcopy(value)
            for key, value in raw.items()
            if key in model["factors"]
        })

    model["league"] = league
    model.setdefault("active_strategy", "baseline")
    model.setdefault(
        "candidate_strategy",
        "v4" if model["active_strategy"] == "baseline" else "baseline",
    )
    model.setdefault("promotion_history", [])

    if league in {"pl", "laliga"}:
        try:
            from .league_predictor import normalize_model_state
        except ImportError:
            from league_predictor import normalize_model_state
        normalized = normalize_model_state(model, league, model["active_strategy"])
        normalized["promotion_history"] = copy.deepcopy(model["promotion_history"])
        for key in ("applied_ledger_version", "applied_match_keys", "generation_id", "comparison", "status"):
            if key in model:
                normalized[key] = copy.deepcopy(model[key])
        if isinstance(model.get("meta"), dict):
            for key, value in model["meta"].items():
                if key not in {"trained_matches", "last_trained_at", "last_batch_size"}:
                    normalized["meta"][key] = copy.deepcopy(value)
        return normalized
    return model


def merge_learning_history(history, league, league_history):
    validate_global_history(history)
    validate_history_entry(league_history, league)
    merged = copy.deepcopy(history)
    existing = merged.get(league)
    combined = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    incoming = copy.deepcopy(league_history) if isinstance(league_history, dict) else {}
    incoming_has_evidence = _has_accuracy_evidence(incoming)

    for packet in (combined, incoming):
        rows = packet.get("gw_results")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    row.setdefault("season", _default_season(league))

    for key, value in incoming.items():
        if key == "overall_accuracy" and not incoming_has_evidence and _finite_number(combined.get(key)):
            continue
        if key == "gw_results" and not value and combined.get(key):
            continue
        if (
            key == "model_comparison"
            and isinstance(value, dict)
            and not value.get("total")
            and isinstance(combined.get(key), dict)
            and combined[key].get("total")
        ):
            continue
        combined[key] = value
    overall_accuracy = _gameweek_accuracy(combined.get("gw_results"))
    if overall_accuracy is not None:
        combined["overall_accuracy"] = overall_accuracy
    elif (comparison_accuracy := _comparison_accuracy(incoming)) is not None:
        combined["overall_accuracy"] = comparison_accuracy
    merged[league] = combined
    validate_global_history(merged)
    return merged


def _prepare_persistent_competition(
    *, league, fixtures, teams, prediction_path, model_path, history, now,
    snapshot_builder, model_trainer, default_model, legacy_candidate_builder=None,
):
    raw_store, _ = load_json_state(prediction_path, {})
    store = normalize_prediction_store(raw_store, league, legacy_candidate_builder)
    raw_model, model_status = load_json_state(model_path, default_model)
    model = _persistent_model(raw_model if model_status != STATE_MISSING else {}, default_model, league)
    return evolve_competition_state(
        league=league,
        fixtures=fixtures if isinstance(fixtures, list) else [],
        store=store,
        model=model,
        snapshot_builder=snapshot_builder,
        model_trainer=model_trainer,
        now=now,
    )


def _bundle_generation(store, model, history_entry, counts):
    model_generation = model.get("generation_id")
    if model_generation and (
        store.get("generation_id") != model_generation
        or history_entry.get("generation_id") != model_generation
    ):
        return model_generation
    if model_generation and not any(counts.values()):
        return model_generation
    return uuid.uuid4().hex


def _validate_pending_bundle(pending, league):
    if not isinstance(pending, dict) or pending.get("league") != league:
        raise StateConsistencyError("invalid pending persistence bundle")
    for key in ("store", "model", "counts"):
        if not isinstance(pending.get(key), dict):
            raise StateConsistencyError("invalid pending persistence bundle")
    version = pending.get("version")
    if version == 1:
        if "history_entry" in pending or not isinstance(pending.get("history"), dict):
            raise StateConsistencyError("ambiguous legacy pending persistence bundle")
        history_entry = pending["history"].get(league)
    elif version == PENDING_VERSION:
        if "history" in pending or not isinstance(pending.get("history_entry"), dict):
            raise StateConsistencyError("ambiguous pending persistence bundle")
        history_entry = pending["history_entry"]
    else:
        raise StateConsistencyError("unsupported pending persistence version")
    if not isinstance(history_entry, dict):
        raise StateConsistencyError("invalid pending competition history entry")
    generation = pending.get("generation_id")
    if not isinstance(generation, str) or not generation:
        raise StateConsistencyError("invalid pending persistence generation")
    if any(
        packet.get("generation_id") != generation
        for packet in (pending["store"], pending["model"], history_entry)
    ):
        raise StateConsistencyError("inconsistent pending persistence generation")
    if any(packet.get("league") != league for packet in (pending["store"], pending["model"])):
        raise StateConsistencyError("inconsistent pending persistence league")
    if version == 1:
        validate_global_history(pending["history"])
    validate_prediction_store(pending["store"], league)
    validate_model_state(pending["model"], league)
    validate_history_entry(history_entry, league)
    _validate_counts(pending["counts"], "pending counts")
    return {
        "version": PENDING_VERSION,
        "league": league,
        "generation_id": generation,
        "store": copy.deepcopy(pending["store"]),
        "model": copy.deepcopy(pending["model"]),
        "history_entry": copy.deepcopy(history_entry),
        "counts": copy.deepcopy(pending["counts"]),
    }


def _write_persistent_bundle(
    *, prediction_path, model_path, history_path, pending, history_fallback=None,
):
    league = pending.get("league") if isinstance(pending, dict) else None
    pending = _validate_pending_bundle(pending, league)
    if history_path:
        latest_history, history_status = load_json_state(history_path, {})
        if history_status == STATE_MISSING and isinstance(history_fallback, dict):
            latest_history = copy.deepcopy(history_fallback)
        merged_history = merge_learning_history(
            latest_history,
            pending["league"],
            pending["history_entry"],
        )
        atomic_save_json(model_path, pending["model"])
        atomic_save_json(prediction_path, pending["store"])
        atomic_save_json(history_path, merged_history)
        return merged_history
    merged_history = merge_learning_history(
        history_fallback if isinstance(history_fallback, dict) else {},
        pending["league"],
        pending["history_entry"],
    )
    atomic_save_json(model_path, pending["model"])
    atomic_save_json(prediction_path, pending["store"])
    return merged_history


def _remove_pending(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def recover_pending_competitions(configurations, *, history_path):
    if not isinstance(configurations, list):
        raise StateConsistencyError("pending recovery configuration must be a list")
    loaded_history, _ = load_json_state(history_path, {})
    validate_global_history(loaded_history)
    pending_bundles = []
    seen_leagues = set()
    for configuration in configurations:
        if not isinstance(configuration, dict):
            raise StateConsistencyError("invalid pending recovery configuration")
        league = configuration.get("league")
        prediction_path = configuration.get("prediction_path")
        model_path = configuration.get("model_path")
        if (
            not isinstance(league, str)
            or not league
            or league in seen_leagues
            or not isinstance(prediction_path, str)
            or not prediction_path
            or not isinstance(model_path, str)
            or not model_path
        ):
            raise StateConsistencyError("invalid pending recovery configuration")
        seen_leagues.add(league)
        raw_store, _ = load_json_state(prediction_path, {})
        raw_model, _ = load_json_state(model_path, {})
        validate_prediction_store(raw_store, league)
        validate_model_state(raw_model, league)
        pending_path = f"{model_path}.pending"
        pending, pending_status = load_json_state(pending_path, {})
        if pending_status == STATE_LOADED:
            pending_bundles.append({
                "prediction_path": prediction_path,
                "model_path": model_path,
                "pending_path": pending_path,
                "pending": _validate_pending_bundle(pending, league),
            })

    for bundle in pending_bundles:
        loaded_history = _write_persistent_bundle(
            prediction_path=bundle["prediction_path"],
            model_path=bundle["model_path"],
            history_path=history_path,
            pending=bundle["pending"],
            history_fallback=loaded_history,
        )
        _remove_pending(bundle["pending_path"])
    return loaded_history


def run_persistent_competition(
    *, league, fixtures, teams, prediction_path, model_path, history, now,
    snapshot_builder, model_trainer, default_model, legacy_candidate_builder=None,
    history_path=None, history_transform=None,
):
    raw_store, _ = load_json_state(prediction_path, {})
    raw_model, model_status = load_json_state(model_path, default_model)
    validate_prediction_store(raw_store, league)
    validate_model_state(raw_model if model_status != STATE_MISSING else {}, league)
    if history_path:
        loaded_history, history_status = load_json_state(history_path, {})
        if history_status == STATE_MISSING:
            if not isinstance(history, dict):
                raise StateFileError("learning history must be an object")
            loaded_history = copy.deepcopy(history)
    else:
        if not isinstance(history, dict):
            raise StateFileError("learning history must be an object")
        loaded_history = copy.deepcopy(history)
    validate_global_history(loaded_history)

    pending_path = f"{model_path}.pending"
    pending, pending_status = load_json_state(pending_path, {}) if history_path else ({}, STATE_MISSING)
    if pending_status == STATE_LOADED:
        pending = _validate_pending_bundle(pending, league)
        recovered_history = _write_persistent_bundle(
            prediction_path=prediction_path,
            model_path=model_path,
            history_path=history_path,
            pending=pending,
            history_fallback=loaded_history,
        )
        _remove_pending(pending_path)
        return recovered_history, copy.deepcopy(pending["counts"]), copy.deepcopy(pending["model"])

    store = normalize_prediction_store(raw_store, league, legacy_candidate_builder)
    model = _persistent_model(raw_model if model_status != STATE_MISSING else {}, default_model, league)
    store, model, league_history, counts = evolve_competition_state(
        league=league,
        fixtures=fixtures if isinstance(fixtures, list) else [],
        store=store,
        model=model,
        snapshot_builder=snapshot_builder,
        model_trainer=model_trainer,
        now=now,
    )
    previous_entry = copy.deepcopy(loaded_history.get(league, {}))
    if history_transform is not None:
        league_history = history_transform(previous_entry, league_history, store)
    merged_history = merge_learning_history(loaded_history, league, league_history)
    generation = _bundle_generation(store, model, merged_history.get(league, {}), counts)
    store["generation_id"] = generation
    model["generation_id"] = generation
    merged_history[league]["generation_id"] = generation
    pending = {
        "version": PENDING_VERSION,
        "league": league,
        "generation_id": generation,
        "store": store,
        "model": model,
        "history_entry": copy.deepcopy(merged_history[league]),
        "counts": counts,
    }
    pending = _validate_pending_bundle(pending, league)
    if history_path:
        atomic_save_json(pending_path, pending)
    persisted_history = _write_persistent_bundle(
        prediction_path=prediction_path,
        model_path=model_path,
        history_path=history_path,
        pending=pending,
        history_fallback=loaded_history,
    )
    if history_path:
        _remove_pending(pending_path)
    return persisted_history, counts, model
