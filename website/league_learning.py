import copy
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from numbers import Real


STORE_VERSION = 1


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


def _gameweek_accuracy(gw_results):
    total = correct = 0
    for row in gw_results if isinstance(gw_results, list) else []:
        if not isinstance(row, dict):
            continue
        row_total = row.get("total")
        row_correct = row.get("correct_winner")
        if not all(isinstance(value, Real) and not isinstance(value, bool) for value in (row_total, row_correct)):
            continue
        if row_total <= 0:
            continue
        total += row_total
        correct += row_correct
    return round(correct / total * 100, 1) if total else None


def comparison_summary(rows, active_strategy, candidate_strategy):
    strategies = (active_strategy, candidate_strategy)
    metrics = {name: {"winner_correct": 0, "exact_correct": 0, "points": 0, "scores": Counter()} for name in strategies}
    valid_rows = [
        row for row in rows
        if row.get("locked") is True
        and row.get("fixture", {}).get("fin") is True
        and all(
            isinstance(row["fixture"].get(key), Real)
            and not isinstance(row["fixture"].get(key), bool)
            for key in ("hs", "as")
        )
    ]
    for row in valid_rows:
        for name in strategies:
            scored = score_pick(row["picks"][name], row["fixture"], row["rule"])
            metrics[name]["winner_correct"] += int(scored["winner_correct"])
            metrics[name]["exact_correct"] += int(scored["exact"])
            metrics[name]["points"] += scored["points"]
            metrics[name]["scores"][scored["score"]] += 1
    total = len(valid_rows)
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
        and all(
            isinstance(pick.get(score), Real) and not isinstance(pick.get(score), bool)
            for score in ("home_score", "away_score")
        )
    )


def _valid_lock_snapshot(snapshot):
    picks = snapshot.get("picks") if isinstance(snapshot, dict) else None
    return isinstance(picks, dict) and all(_valid_pick(picks.get(strategy)) for strategy in ("baseline", "v4"))


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
                isinstance(score, Real) and not isinstance(score, bool) for score in actual_scores
            ):
                fixture = {"hs": actual_scores[0], "as": actual_scores[1], "e": snapshot.get("round")}
                if snapshot.get("phase") == "group":
                    fixture["grp"] = "legacy"
                rule = snapshot.get("rule") or snapshot.get("phase_rule") or competition_rule(league, fixture)
                snapshot.setdefault("actual_winner", _winner(*actual_scores))
                snapshot.setdefault("rule", rule)
                snapshot["evaluations"] = {
                    name: score_pick(pick, fixture, rule)
                    for name, pick in snapshot["picks"].items()
                }
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
            return copy.deepcopy(default), False
        return value, True
    except FileNotFoundError:
        return copy.deepcopy(default), False
    except (UnicodeError, json.JSONDecodeError, TypeError):
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
        if not _valid_lock_snapshot(snapshot):
            counts["skipped"] += 1
            continue
        snapshot.update({
            "match_id": fixture["id"], "league": league, "round": fixture.get("e"),
            "home_id": fixture.get("h"), "away_id": fixture.get("a"),
            "kickoff_time": fixture.get("ko", ""), "created_at": timestamp,
            "locked": True, "checked": False, "model_trained": False,
            "lifecycle_version": STORE_VERSION, "lock_verified": True,
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
                "locked": snapshot.get("locked") is True and snapshot.get("lock_verified") is True,
                "fixture": {"fin": True, "hs": fixture["hs"], "as": fixture["as"]},
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
    overall_accuracy = _gameweek_accuracy(gw_results)
    if overall_accuracy is None:
        overall_accuracy = comparison["models"].get(model["active_strategy"], {}).get("winner_accuracy", 0)
    history = {
        "gw_results": gw_results,
        "overall_accuracy": overall_accuracy,
        "total_evaluated": sum(row["total"] for row in gw_results),
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
        return normalized
    return model


def merge_learning_history(history, league, league_history):
    merged = copy.deepcopy(history) if isinstance(history, dict) else {}
    existing = merged.get(league)
    combined = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    incoming = copy.deepcopy(league_history) if isinstance(league_history, dict) else {}

    for key, value in incoming.items():
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
    merged[league] = combined
    return merged


def _prepare_persistent_competition(
    *, league, fixtures, teams, prediction_path, model_path, history, now,
    snapshot_builder, model_trainer, default_model, legacy_candidate_builder=None,
):
    raw_store, _ = load_json_state(prediction_path, {})
    store = normalize_prediction_store(raw_store, league, legacy_candidate_builder)
    raw_model, model_valid = load_json_state(model_path, default_model)
    model = _persistent_model(raw_model if model_valid else {}, default_model, league)
    return evolve_competition_state(
        league=league,
        fixtures=fixtures if isinstance(fixtures, list) else [],
        store=store,
        model=model,
        snapshot_builder=snapshot_builder,
        model_trainer=model_trainer,
        now=now,
    )


def run_persistent_competition(
    *, league, fixtures, teams, prediction_path, model_path, history, now,
    snapshot_builder, model_trainer, default_model, legacy_candidate_builder=None,
):
    store, model, league_history, counts = _prepare_persistent_competition(
        league=league,
        fixtures=fixtures,
        teams=teams,
        prediction_path=prediction_path,
        model_path=model_path,
        history=history,
        snapshot_builder=snapshot_builder,
        model_trainer=model_trainer,
        now=now,
        default_model=default_model,
        legacy_candidate_builder=legacy_candidate_builder,
    )
    atomic_save_json(prediction_path, store)
    atomic_save_json(model_path, model)
    return league_history, counts, model
