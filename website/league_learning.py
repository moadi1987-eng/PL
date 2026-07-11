from collections import Counter
from numbers import Real


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
