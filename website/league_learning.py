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
    if fixture.get("grp"):
        return {"key": "group", "result": 1, "exact": 3, "additive": False}
    day = fixture.get("e")
    if not _valid_score(day):
        raise ValueError("invalid World Cup phase")
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
    if math.isclose(total, 100.0, abs_tol=0.11):
        numbers = [number / 100.0 for number in numbers]
    elif not math.isclose(total, 1.0, abs_tol=0.0011):
        raise ValueError("invalid probability vector")
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


def normalize_prediction_store(raw, league, legacy_candidate_builder=None):
    raw = copy.deepcopy(raw or {})
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
            is_legacy_snapshot = snapshot.get("legacy") is True or not (
                is_lifecycle_store or snapshot.get("lifecycle_version") == STORE_VERSION
            )
            snapshot.setdefault("match_id", int(match_id) if str(match_id).isdigit() else match_id)
            season, source_fixture_id, match_key = _snapshot_identity(snapshot, league, match_id)
            snapshot.setdefault("season", season)
            snapshot.setdefault("source_fixture_id", source_fixture_id)
            snapshot.setdefault("match_key", match_key)
            snapshot.setdefault("league", league)
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
            if snapshot.get("checked") and snapshot.get("legacy"):
                snapshot["model_trained"] = True
            if snapshot.get("legacy") and snapshot.get("active_strategy_at_lock") not in snapshot["picks"]:
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
                "picks": {"baseline": baseline, "v4": candidate},
            }
    return {
        "version": STORE_VERSION,
        "lifecycle_version": STORE_VERSION,
        "league": league,
        "matches": matches,
        "updated_at": "",
    }


def load_json_state(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise StateFileError(f"existing state is not a JSON object: {path}")
        return value, STATE_LOADED
    except FileNotFoundError:
        return copy.deepcopy(default), STATE_MISSING
    except (UnicodeError, json.JSONDecodeError, TypeError) as error:
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
            if snapshot.get("model_trained") is True or snapshot.get("checked") is True or snapshot.get("legacy") is True
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
        try:
            competition_rule(league, fixture)
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
            "round": fixture.get("e"),
            "home_id": fixture.get("h"), "away_id": fixture.get("a"),
            "kickoff_time": fixture.get("ko", ""), "created_at": timestamp,
            "locked": True, "checked": False, "model_trained": False,
            "lifecycle_version": STORE_VERSION, "lock_verified": True,
            "active_strategy_at_lock": model.get("active_strategy", "baseline"),
        })
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
        fixture = fixture_map.get(match_key)
        if not snapshot.get("checked") and fixture and fixture.get("fin") is True:
            try:
                if not _valid_result(fixture):
                    raise ValueError("invalid completed result")
                rule = competition_rule(league, fixture)
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
        if match_key not in applied and not snapshot.get("legacy"):
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
    raw = raw_model if isinstance(raw_model, dict) else {}
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
    merged = copy.deepcopy(history) if isinstance(history, dict) else {}
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
    for key in ("store", "model", "history", "counts"):
        if not isinstance(pending.get(key), dict):
            raise StateConsistencyError("invalid pending persistence bundle")
    generation = pending["model"].get("generation_id")
    if not generation or pending["store"].get("generation_id") != generation:
        raise StateConsistencyError("inconsistent pending persistence generation")
    if (pending["history"].get(league) or {}).get("generation_id") != generation:
        raise StateConsistencyError("inconsistent pending persistence generation")


def _write_persistent_bundle(*, prediction_path, model_path, history_path, pending):
    atomic_save_json(model_path, pending["model"])
    atomic_save_json(prediction_path, pending["store"])
    if history_path:
        atomic_save_json(history_path, pending["history"])


def _remove_pending(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def run_persistent_competition(
    *, league, fixtures, teams, prediction_path, model_path, history, now,
    snapshot_builder, model_trainer, default_model, legacy_candidate_builder=None,
    history_path=None, history_transform=None,
):
    raw_store, _ = load_json_state(prediction_path, {})
    raw_model, model_status = load_json_state(model_path, default_model)
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

    pending_path = f"{model_path}.pending"
    pending, pending_status = load_json_state(pending_path, {}) if history_path else ({}, STATE_MISSING)
    if pending_status == STATE_LOADED:
        _validate_pending_bundle(pending, league)
        _write_persistent_bundle(
            prediction_path=prediction_path,
            model_path=model_path,
            history_path=history_path,
            pending=pending,
        )
        _remove_pending(pending_path)
        return copy.deepcopy(pending["history"]), copy.deepcopy(pending["counts"]), copy.deepcopy(pending["model"])

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
        "version": 1,
        "league": league,
        "generation_id": generation,
        "store": store,
        "model": model,
        "history": merged_history,
        "counts": counts,
    }
    if history_path:
        atomic_save_json(pending_path, pending)
    _write_persistent_bundle(
        prediction_path=prediction_path,
        model_path=model_path,
        history_path=history_path,
        pending=pending,
    )
    if history_path:
        _remove_pending(pending_path)
    return merged_history, counts, model
