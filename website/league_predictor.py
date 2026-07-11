import copy
import math
from datetime import datetime, timezone
from numbers import Real

from website.league_learning import competition_rule


DEFAULT_FACTORS = {
    "form": 0.15,
    "strength": 0.15,
    "position": 0.12,
    "home_adv": 0.08,
    "streak": 0.12,
    "h2h": 0.08,
    "home_away_split": 0.08,
    "goals_trend": 0.06,
    "upset": 0.06,
    "clean_sheet": 0.05,
    "draw_tendency": 0.05,
}


def _clip(value, low, high):
    return max(low, min(high, value))


def _number(value, default=0.0):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _utc(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _strategy(value, default):
    return value if value in {"baseline", "v4"} else default


def _normalized_factors(values):
    sanitized = {
        key: _clip(_number(values.get(key), default), 0.0, 1.0)
        for key, default in DEFAULT_FACTORS.items()
    }
    total = sum(sanitized.values())
    if total <= 0:
        return copy.deepcopy(DEFAULT_FACTORS)
    return {key: value / total for key, value in sanitized.items()}


def _rounded_normalized_factors(values):
    normalized = _normalized_factors(values)
    keys = tuple(DEFAULT_FACTORS)
    scaled = {key: normalized[key] * 10000 for key in keys}
    units = {key: math.floor(scaled[key]) for key in keys}
    remainder = 10000 - sum(units.values())
    for key in sorted(keys, key=lambda item: (-(scaled[item] - units[item]), item))[:remainder]:
        units[key] += 1
    return {key: units[key] / 10000 for key in keys}


def default_model_state(league, active_strategy="baseline"):
    active = _strategy(active_strategy, "baseline")
    return {
        "version": 1,
        "league": league,
        "active_strategy": active,
        "candidate_strategy": "v4" if active == "baseline" else "baseline",
        "factors": copy.deepcopy(DEFAULT_FACTORS),
        "calibration": {
            "goal_mult": 1.0,
            "home_goal_bias": 0.0,
            "away_goal_bias": 0.0,
            "draw_bias": 1.0,
            "zero_zero_penalty": 0.62,
        },
        "meta": {"trained_matches": 0, "last_trained_at": "", "last_batch_size": 0},
        "promotion_history": [],
    }


def normalize_model_state(raw, league, default_active="baseline"):
    default = default_model_state(league, default_active)
    if not isinstance(raw, dict):
        return default

    factor_source = raw.get("factors") if isinstance(raw.get("factors"), dict) else raw
    merged = copy.deepcopy(default)
    merged["version"] = raw.get("version", default["version"])
    merged["active_strategy"] = _strategy(raw.get("active_strategy"), default["active_strategy"])
    merged["candidate_strategy"] = "v4" if merged["active_strategy"] == "baseline" else "baseline"
    merged["factors"] = _normalized_factors(factor_source)

    calibration = raw.get("calibration") if isinstance(raw.get("calibration"), dict) else {}
    merged["calibration"] = {
        "goal_mult": _clip(_number(calibration.get("goal_mult"), 1.0), 0.82, 1.22),
        "home_goal_bias": _clip(_number(calibration.get("home_goal_bias"), 0.0), -1.5, 1.5),
        "away_goal_bias": _clip(_number(calibration.get("away_goal_bias"), 0.0), -1.5, 1.5),
        "draw_bias": _clip(_number(calibration.get("draw_bias"), 1.0), 0.25, 2.0),
        "zero_zero_penalty": _clip(_number(calibration.get("zero_zero_penalty"), 0.62), 0.0, 1.0),
    }

    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    merged["meta"] = {
        "trained_matches": max(0, int(_number(meta.get("trained_matches"), 0))),
        "last_trained_at": meta.get("last_trained_at") if isinstance(meta.get("last_trained_at"), str) else "",
        "last_batch_size": max(0, int(_number(meta.get("last_batch_size"), 0))),
    }
    merged["promotion_history"] = copy.deepcopy(raw["promotion_history"]) if isinstance(raw.get("promotion_history"), list) else []
    return merged


def _prior_fixtures(fixtures, target):
    try:
        target_time = _utc(target.get("ko"))
    except (AttributeError, TypeError, ValueError):
        return []
    prior = []
    for row in fixtures:
        if not isinstance(row, dict) or not row.get("fin") or row.get("hs") is None or row.get("as") is None or not row.get("ko"):
            continue
        try:
            kickoff = _utc(row["ko"])
        except (TypeError, ValueError):
            continue
        if kickoff < target_time:
            prior.append(row)
    return sorted(prior, key=lambda row: (_utc(row["ko"]), str(row.get("id", ""))))


def _team_stats(team_id, prior):
    rows = []
    for item in prior:
        if item.get("h") != team_id and item.get("a") != team_id:
            continue
        home = item.get("h") == team_id
        gf = _number(item.get("hs") if home else item.get("as"))
        ga = _number(item.get("as") if home else item.get("hs"))
        rows.append({"gf": gf, "ga": ga, "home": home, "result": 3 if gf > ga else 1 if gf == ga else 0})
    rows = rows[-8:]
    recent = rows[-5:]
    played = len(recent)
    if not played:
        return {"played": 0, "form": 0.5, "streak": 0.5, "gf": 1.2, "ga": 1.2, "home_gf": 1.2, "away_gf": 1.1, "clean": 0.0, "draw": 0.2, "trend": 1.2}
    weights = list(range(1, played + 1))
    home_rows = [row for row in rows if row["home"]]
    away_rows = [row for row in rows if not row["home"]]
    return {
        "played": played,
        "form": sum(row["result"] for row in recent) / (played * 3),
        "streak": sum(row["result"] * weight for row, weight in zip(recent, weights)) / (3 * sum(weights)),
        "gf": sum(row["gf"] for row in recent) / played,
        "ga": sum(row["ga"] for row in recent) / played,
        "home_gf": sum(row["gf"] for row in home_rows) / len(home_rows) if home_rows else 1.2,
        "away_gf": sum(row["gf"] for row in away_rows) / len(away_rows) if away_rows else 1.1,
        "clean": sum(row["ga"] == 0 for row in recent) / played,
        "draw": sum(row["result"] == 1 for row in rows) / len(rows),
        "trend": sum(row["gf"] for row in rows[-3:]) / len(rows[-3:]),
    }


def _positions(prior, team_ids):
    table = {team_id: {"points": 0, "gd": 0, "gf": 0} for team_id in team_ids}
    for item in prior:
        home, away = item.get("h"), item.get("a")
        hs, away_score = _number(item.get("hs")), _number(item.get("as"))
        table.setdefault(home, {"points": 0, "gd": 0, "gf": 0})
        table.setdefault(away, {"points": 0, "gd": 0, "gf": 0})
        table[home]["points"] += 3 if hs > away_score else 1 if hs == away_score else 0
        table[away]["points"] += 3 if away_score > hs else 1 if hs == away_score else 0
        table[home]["gd"] += hs - away_score
        table[away]["gd"] += away_score - hs
        table[home]["gf"] += hs
        table[away]["gf"] += away_score
    ordered = sorted(table, key=lambda team_id: (-table[team_id]["points"], -table[team_id]["gd"], -table[team_id]["gf"], str(team_id)))
    return {team_id: index + 1 for index, team_id in enumerate(ordered)}


def _h2h_edge(prior, home_id, away_id):
    home_points = away_points = meetings = 0
    for item in prior:
        if {item.get("h"), item.get("a")} != {home_id, away_id}:
            continue
        meetings += 1
        home_goals = _number(item.get("hs") if item.get("h") == home_id else item.get("as"))
        away_goals = _number(item.get("as") if item.get("h") == home_id else item.get("hs"))
        home_points += 3 if home_goals > away_goals else 1 if home_goals == away_goals else 0
        away_points += 3 if away_goals > home_goals else 1 if home_goals == away_goals else 0
    return _clip((home_points - away_points) / max(meetings * 3, 1), -1, 1) if meetings else 0.0


def _poisson(lam, goals):
    return math.exp(-lam) * (lam ** goals) / math.factorial(goals)


def _poisson_grid(lam_h, lam_a, maximum=6):
    grid = {}
    total = home = draw = away = 0.0
    for home_score in range(maximum + 1):
        for away_score in range(maximum + 1):
            probability = _poisson(lam_h, home_score) * _poisson(lam_a, away_score)
            grid[(home_score, away_score)] = probability
            total += probability
            if home_score > away_score:
                home += probability
            elif away_score > home_score:
                away += probability
            else:
                draw += probability
    grid = {score: probability / total for score, probability in grid.items()}
    return grid, {"home": home / total, "draw": draw / total, "away": away / total}


def _score_winner(home_score, away_score):
    return "home" if home_score > away_score else "away" if away_score > home_score else "draw"


def _reweight_score_grid(grid, poisson_outcomes, target_outcomes):
    adjusted = {}
    for (home_score, away_score), probability in grid.items():
        winner = _score_winner(home_score, away_score)
        source = _number(poisson_outcomes.get(winner), 0.0)
        target = max(_number(target_outcomes.get(winner), 0.0), 0.0)
        adjusted[(home_score, away_score)] = probability * target / source if source > 0 else 0.0
    return adjusted


def _expected_points_pick(grid, outcomes, rule):
    best = None
    for (home_score, away_score), exact_probability in grid.items():
        winner = _score_winner(home_score, away_score)
        if rule.get("additive"):
            value = outcomes[winner] * rule["result"] + exact_probability * rule["exact"]
        else:
            value = (outcomes[winner] - exact_probability) * rule["result"] + exact_probability * rule["exact"]
        candidate = (value, exact_probability, -abs(home_score - away_score), -home_score - away_score, home_score, away_score)
        if best is None or candidate > best[0]:
            best = (candidate, {"winner": winner, "home_score": home_score, "away_score": away_score, "expected_points": round(value, 4)})
    return best[1]


def _v4_pick(lam_h, lam_a, probabilities, draw_rate):
    home_probability = probabilities["home"]
    draw_probability = probabilities["draw"]
    away_probability = probabilities["away"]
    if draw_probability >= 0.23 and abs(home_probability - away_probability) <= 0.12:
        goals = 2 if lam_h + lam_a >= 3.1 else 1 if lam_h + lam_a >= 1.8 else 0
        return {"winner": "draw", "home_score": goals, "away_score": goals, "reason": "draw-v4"}
    winner = "home" if home_probability >= away_probability else "away"
    favorite = max(home_probability, away_probability)
    dog_lam = lam_a if winner == "home" else lam_h
    total = lam_h + lam_a
    if favorite >= 0.52 and abs(lam_h - lam_a) >= 0.95:
        favorite_goals, dog_goals, reason = (3, 0, "strong-clean-win") if dog_lam < 0.78 else (3, 1, "strong-open-win") if total >= 3.0 else (2, 0, "strong-controlled-win")
    elif total >= 3.15:
        favorite_goals, dog_goals, reason = 3, 1, "open-win"
    elif dog_lam < 0.75:
        favorite_goals, dog_goals, reason = (1, 0, "low-total-edge") if total < 2.25 else (2, 0, "clean-win")
    else:
        favorite_goals, dog_goals, reason = (1, 0, "tight-win") if total < 2.25 else (2, 1, "balanced-win")
    if winner == "home":
        return {"winner": winner, "home_score": favorite_goals, "away_score": dog_goals, "reason": reason}
    return {"winner": winner, "home_score": dog_goals, "away_score": favorite_goals, "reason": reason}


def _team_strength(team, attack_key, defense_key):
    return (_number(team.get(attack_key), 1000.0) + _number(team.get(defense_key), 1000.0)) / 2


def _has_usable_availability(team):
    return any(team.get(key) for key in ("inj", "sq"))


def predict_league_snapshot(fixture, fixtures, teams, model, league):
    raw_model = model if isinstance(model, dict) else {}
    model = normalize_model_state(raw_model, league, raw_model.get("active_strategy", "baseline"))
    prior = _prior_fixtures(fixtures, fixture)
    home_id, away_id = fixture["h"], fixture["a"]
    home, away = _team_stats(home_id, prior), _team_stats(away_id, prior)
    positions = _positions(prior, set(teams) | {home_id, away_id})
    home_team = teams.get(home_id, {}) if isinstance(teams.get(home_id, {}), dict) else {}
    away_team = teams.get(away_id, {}) if isinstance(teams.get(away_id, {}), dict) else {}
    home_strength = _team_strength(home_team, "sah", "sdh")
    away_strength = _team_strength(away_team, "saa", "sda")
    team_count = max(len(positions), 2)
    factor_edges = {
        "form": _clip(home["form"] - away["form"], -1, 1),
        "strength": _clip((home_strength - away_strength) / 400, -1, 1),
        "position": _clip((positions.get(away_id, team_count) - positions.get(home_id, team_count)) / team_count, -1, 1),
        "home_adv": 0.18,
        "streak": _clip(home["streak"] - away["streak"], -1, 1),
        "h2h": _h2h_edge(prior, home_id, away_id),
        "home_away_split": _clip((home["home_gf"] - away["away_gf"]) / 3, -1, 1),
        "goals_trend": _clip((home["trend"] - away["trend"]) / 3, -1, 1),
        "upset": _clip((away["form"] - home["form"]) * 0.25, -1, 1),
        "clean_sheet": _clip(home["clean"] - away["clean"], -1, 1),
        "draw_tendency": _clip(away["draw"] - home["draw"], -1, 1),
    }
    weights = model["factors"]
    home_factor = 1.0 + sum(weights[key] * max(edge, 0) for key, edge in factor_edges.items())
    away_factor = 1.0 + sum(weights[key] * max(-edge, 0) for key, edge in factor_edges.items())
    factor_home = home_factor / (home_factor + away_factor)
    closeness = 1 - abs(factor_home - (1 - factor_home))
    factor_draw = _clip(0.10 + 0.18 * closeness + 0.15 * ((home["draw"] + away["draw"]) / 2), 0.10, 0.34)
    factor_home *= 1 - factor_draw
    factor_away = 1 - factor_home - factor_draw
    strength_shift = _clip((home_strength - away_strength) / 900, -0.35, 0.35)
    lam_h = 0.30 * home["gf"] + 0.25 * away["ga"] + 0.15 * home["home_gf"] + 0.15 * home["trend"] + 0.15 * (1.35 + strength_shift)
    lam_a = 0.30 * away["gf"] + 0.25 * home["ga"] + 0.15 * away["away_gf"] + 0.15 * away["trend"] + 0.15 * (1.15 - strength_shift)
    calibration = model["calibration"]
    lam_h = _clip(lam_h * calibration["goal_mult"] + calibration["home_goal_bias"], 0.30, 4.40)
    lam_a = _clip(lam_a * calibration["goal_mult"] + calibration["away_goal_bias"], 0.30, 4.40)
    grid, poisson = _poisson_grid(lam_h, lam_a)
    probabilities = {
        "home": factor_home * 0.4 + poisson["home"] * 0.6,
        "draw": factor_draw * 0.4 + poisson["draw"] * 0.6,
        "away": factor_away * 0.4 + poisson["away"] * 0.6,
    }
    probabilities["draw"] *= calibration["draw_bias"]
    total = sum(probabilities.values())
    probabilities = {key: value / total for key, value in probabilities.items()}
    adjusted_grid = _reweight_score_grid(grid, poisson, probabilities)
    rule = competition_rule(league, fixture)
    shown_home = round(probabilities["home"] * 100, 1)
    shown_draw = round(probabilities["draw"] * 100, 1)
    shown_away = round(100.0 - shown_home - shown_draw, 1)
    return {
        "features": {"home": home, "away": away, "positions": {"home": positions.get(home_id), "away": positions.get(away_id)}},
        "factor_edges": factor_edges,
        "missing": {"squad_availability": not (_has_usable_availability(home_team) and _has_usable_availability(away_team))},
        "probabilities": {"home": shown_home, "draw": shown_draw, "away": shown_away},
        "expected_home_goals": round(lam_h, 3),
        "expected_away_goals": round(lam_a, 3),
        "picks": {
            "baseline": _expected_points_pick(adjusted_grid, probabilities, rule),
            "v4": _v4_pick(lam_h, lam_a, probabilities, (home["draw"] + away["draw"]) / 2),
        },
    }


def legacy_v4_pick(pick):
    pick = pick if isinstance(pick, dict) else {}
    probabilities = {
        "home": max(_number(pick.get("home_win_pct")) / 100, 0.0),
        "draw": max(_number(pick.get("draw_pct")) / 100, 0.0),
        "away": max(_number(pick.get("away_win_pct")) / 100, 0.0),
    }
    total = sum(probabilities.values())
    probabilities = ({key: value / total for key, value in probabilities.items()} if total else {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3})
    return _v4_pick(max(_number(pick.get("home_score"), 1.0), 0.3), max(_number(pick.get("away_score"), 1.0), 0.3), probabilities, probabilities["draw"])


def _is_training_number(value):
    if isinstance(value, bool) or value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _is_score_count(value):
    return _is_training_number(value) and float(value) >= 0 and float(value).is_integer()


def _is_training_row(row):
    fixture = row.get("fixture") if isinstance(row, dict) else None
    home_score = fixture.get("hs") if isinstance(fixture, dict) else None
    away_score = fixture.get("as") if isinstance(fixture, dict) else None
    return (
        isinstance(fixture, dict)
        and _is_score_count(home_score)
        and _is_score_count(away_score)
        and all(_is_training_number(row.get(key)) and float(row[key]) >= 0 for key in ("expected_home_goals", "expected_away_goals"))
        and row.get("actual_winner") == _score_winner(float(home_score), float(away_score))
    )


def train_factor_model(model, rows):
    raw_model = model if isinstance(model, dict) else {}
    league = raw_model.get("league") if isinstance(raw_model.get("league"), str) else "pl"
    model = normalize_model_state(raw_model, league, raw_model.get("active_strategy", "baseline"))
    rows = rows if isinstance(rows, list) else []
    valid_rows = [row for row in rows if _is_training_row(row)]
    factor_scores = {key: [] for key in model["factors"]}
    actual_totals = []
    predicted_totals = []
    for row in valid_rows:
        actual = row.get("actual_winner")
        for key, edge in (row.get("factor_edges") or {}).items():
            edge = _number(edge)
            if key in factor_scores and abs(edge) >= 0.05 and actual != "draw":
                factor_scores[key].append(1.0 if (edge > 0 and actual == "home") or (edge < 0 and actual == "away") else 0.0)
        fixture = row.get("fixture") if isinstance(row.get("fixture"), dict) else {}
        actual_totals.append(max(_number(fixture.get("hs")), 0.0) + max(_number(fixture.get("as")), 0.0))
        predicted_totals.append(max(_number(row.get("expected_home_goals")), 0.0) + max(_number(row.get("expected_away_goals")), 0.0))
    for key, values in factor_scores.items():
        if len(values) >= 3:
            model["factors"][key] = _clip(model["factors"][key] + ((sum(values) / len(values)) - 0.5) * 0.04, 0.03, 0.25)
    model["factors"] = _rounded_normalized_factors(model["factors"])
    if valid_rows:
        actual_avg = sum(actual_totals) / len(valid_rows)
        predicted_avg = sum(predicted_totals) / len(valid_rows)
        model["calibration"]["goal_mult"] = round(_clip(model["calibration"]["goal_mult"] + _clip((actual_avg - predicted_avg) * 0.035, -0.06, 0.06), 0.82, 1.22), 4)
    meta = model["meta"]
    meta["trained_matches"] += len(valid_rows)
    meta["last_batch_size"] = len(valid_rows)
    if valid_rows:
        meta["last_trained_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return model
