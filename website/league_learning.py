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


def normalize_prediction_store(raw, league, legacy_candidate_builder=None):
    raw = copy.deepcopy(raw or {})
    if isinstance(raw.get("matches"), dict):
        raw.setdefault("version", STORE_VERSION)
        raw.setdefault("league", league)
        for match_id, snapshot in raw["matches"].items():
            snapshot.setdefault("match_id", int(match_id) if str(match_id).isdigit() else match_id)
            snapshot.setdefault("locked", True)
            if "round" not in snapshot and snapshot.get("day") is not None:
                snapshot["round"] = snapshot["day"]
            if "picks" not in snapshot:
                baseline = copy.deepcopy(snapshot.get("base_v3_prediction") or {
                    "winner": snapshot.get("winner"),
                    "home_score": snapshot.get("home_score"),
                    "away_score": snapshot.get("away_score"),
                })
                candidate = copy.deepcopy(snapshot.get("v4_shadow") or (
                    legacy_candidate_builder(snapshot) if legacy_candidate_builder else baseline
                ))
                snapshot["picks"] = {"baseline": baseline, "v4": candidate}
                snapshot["legacy"] = True
            if snapshot.get("checked") and snapshot.get("legacy"):
                snapshot["model_trained"] = True
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
                "checked": False, "model_trained": True,
                "features": {}, "missing": {"legacy_features": True},
                "picks": {"baseline": baseline, "v4": candidate},
            }
    return {"version": STORE_VERSION, "league": league, "matches": matches, "updated_at": ""}


def load_json_state(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            return copy.deepcopy(default), False
        return value, True
    except (OSError, UnicodeError, ValueError, TypeError):
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
        snapshot.update({
            "match_id": fixture["id"], "league": league, "round": fixture.get("e"),
            "home_id": fixture.get("h"), "away_id": fixture.get("a"),
            "kickoff_time": fixture.get("ko", ""), "created_at": timestamp,
            "locked": True, "checked": False, "model_trained": False,
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
                "locked": snapshot.get("locked") is True,
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
    history = {
        "gw_results": gw_results,
        "overall_accuracy": comparison["models"].get(model["active_strategy"], {}).get("winner_accuracy", 0),
        "total_evaluated": comparison["total"],
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
