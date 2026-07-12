"""
Historical PL and La Liga evaluation helpers.

Current competition state is owned by league_learning.run_persistent_competition.
This module remains import-compatible for historical reports and deliberately
does not write prediction, model, or learning-history files.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HISTORY_PATH  = os.path.join(ROOT, "learning_history.json")
WEIGHTS_PATH  = os.path.join(ROOT, "ai_weights.json")
PREDICTIONS_PATH = os.path.join(ROOT, "ai_predictions.json")
LL_PREDICTIONS_PATH = os.path.join(ROOT, "ai_predictions_laliga.json")

DEFAULT_WEIGHTS = {
    "form": 0.15, "strength": 0.15, "position": 0.12, "home_adv": 0.08,
    "streak": 0.12, "h2h": 0.08, "home_away_split": 0.08,
    "goals_trend": 0.06, "upset": 0.06, "clean_sheet": 0.05, "draw_tendency": 0.05,
}

# ── helpers ───────────────────────────────────────────────────────────────────

def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, data):
    return None

def _winner(hs, as_):
    if hs is None or as_ is None:
        return None
    if hs > as_:
        return "home"
    if as_ > hs:
        return "away"
    return "draw"

def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _pred_winner(pred):
    winner = pred.get("winner")
    if winner in ("home", "draw", "away"):
        return winner
    hs = pred.get("home_score")
    as_ = pred.get("away_score")
    if hs is not None and as_ is not None:
        try:
            return _winner(int(hs), int(as_))
        except (TypeError, ValueError):
            pass
    return None

def _score_pred(pred, actual_hs, actual_as):
    actual = _winner(actual_hs, actual_as)
    pick = _pred_winner(pred)
    exact = pred.get("home_score") == actual_hs and pred.get("away_score") == actual_as
    correct = pick == actual
    return {
        "winner": pick,
        "correct": correct,
        "exact": exact,
        "points": (3 if correct else 0) + (5 if exact else 0),
        "score": f"{pred.get('home_score')}-{pred.get('away_score')}",
    }

def _shadow_v4_prediction(pred):
    """Build a deterministic v4-style score shadow from stored pre-match probabilities."""
    hp = _num(pred.get("home_win_pct"))
    dp = _num(pred.get("draw_pct"))
    ap = _num(pred.get("away_win_pct"))
    old_h = _num(pred.get("home_score"), 1)
    old_a = _num(pred.get("away_score"), 1)
    total = max(1.6, min(3.8, old_h + old_a))
    close = abs(hp - ap)

    if dp >= 23 and close <= 12:
        score = 1 if total >= 1.8 else 0
        return {"winner": "draw", "home_score": score, "away_score": score, "reason": "draw-v4-shadow"}

    winner = "home" if hp >= ap else "away"
    fav = max(hp, ap)
    dog = min(hp, ap)
    margin = fav - dog

    if winner == "home":
        if margin >= 25 and ap <= 26:
            return {"winner": "home", "home_score": 2, "away_score": 0, "reason": "strong-clean-win"}
        if margin >= 25 and total >= 3.0:
            return {"winner": "home", "home_score": 3, "away_score": 1, "reason": "open-favorite"}
        if dp >= 25 or ap >= 34:
            return {"winner": "home", "home_score": 2, "away_score": 1, "reason": "balanced-win"}
        return {"winner": "home", "home_score": 1, "away_score": 0, "reason": "controlled-win"}

    if margin >= 25 and hp <= 26:
        return {"winner": "away", "home_score": 0, "away_score": 2, "reason": "strong-clean-win"}
    if margin >= 25 and total >= 3.0:
        return {"winner": "away", "home_score": 1, "away_score": 3, "reason": "open-favorite"}
    if dp >= 25 or hp >= 34:
        return {"winner": "away", "home_score": 1, "away_score": 2, "reason": "balanced-win"}
    return {"winner": "away", "home_score": 0, "away_score": 1, "reason": "controlled-win"}

def _prediction_variants(pred):
    baseline = pred.get("base_v3_prediction") or pred
    if pred.get("v4_shadow"):
        challenger = pred["v4_shadow"]
    elif pred.get("base_v3_prediction"):
        challenger = pred
    else:
        challenger = _shadow_v4_prediction(pred)
    return baseline, challenger

def _comparison_summary(rows):
    if not rows:
        return None
    total = len(rows)
    def box(key):
        scores = {}
        winner = exact = points = draws = 0
        for row in rows:
            item = row[key]
            winner += 1 if item["correct"] else 0
            exact += 1 if item["exact"] else 0
            points += item["points"]
            draws += 1 if item["winner"] == "draw" else 0
            scores[item["score"]] = scores.get(item["score"], 0) + 1
        top = [
            {"score": score, "count": count}
            for score, count in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        ]
        return {
            "winner_correct": winner,
            "winner_accuracy": round(winner / total * 100, 1),
            "exact_correct": exact,
            "exact_accuracy": round(exact / total * 100, 1),
            "points": points,
            "draw_picks": draws,
            "unique_scores": len(scores),
            "top_scores": top,
        }
    b = box("baseline")
    v = box("challenger")
    return {
        "total": total,
        "baseline_model": "stored baseline",
        "challenger_model": "v4 scoreline",
        "note": "v4 is compared with stored pre-match league predictions using the same 3+5 scoring rules as You vs AI.",
        "baseline": b,
        "challenger": v,
        "delta": {
            "winner_accuracy": round(v["winner_accuracy"] - b["winner_accuracy"], 1),
            "exact_accuracy": round(v["exact_accuracy"] - b["exact_accuracy"], 1),
            "points": v["points"] - b["points"],
            "unique_scores": v["unique_scores"] - b["unique_scores"],
            "draw_picks": v["draw_picks"] - b["draw_picks"],
        },
    }

def _fixture_score(fixture, compact=False):
    if compact:
        if not fixture.get("fin") or fixture.get("hs") is None:
            return None
        return fixture.get("hs"), fixture.get("as")
    if not fixture.get("finished") or fixture.get("team_h_score") is None:
        return None
    return fixture.get("team_h_score"), fixture.get("team_a_score")

def _evaluate_prediction_rows(predictions_by_gw, fixtures, compact=False):
    fixture_map = {f.get("id"): f for f in fixtures}
    gw_results = []
    comparison_rows = []
    for gw_key, gw_data in (predictions_by_gw or {}).items():
        try:
            gw = int(gw_key)
        except (TypeError, ValueError):
            continue
        rows = []
        for pred in gw_data.get("predictions", []):
            fixture = fixture_map.get(pred.get("match_id"))
            score = _fixture_score(fixture or {}, compact)
            if not score:
                continue
            hs, as_ = score
            actual_w = _winner(hs, as_)
            scored = _score_pred(pred, hs, as_)
            base_pred, v4_pred = _prediction_variants(pred)
            baseline = _score_pred(base_pred, hs, as_)
            challenger = _score_pred(v4_pred, hs, as_)
            comparison_rows.append({"baseline": baseline, "challenger": challenger})
            rows.append({
                "match_id": pred.get("match_id"),
                "pred_winner": scored["winner"],
                "actual_winner": actual_w,
                "correct_winner": scored["correct"],
                "correct_score": scored["exact"],
                "points": scored["points"],
                "home_pct": pred.get("home_win_pct", 0),
                "draw_pct": pred.get("draw_pct", 0),
                "away_pct": pred.get("away_win_pct", 0),
            })
        if rows:
            total = len(rows)
            correct_w = sum(1 for r in rows if r["correct_winner"])
            correct_s = sum(1 for r in rows if r["correct_score"])
            points = sum(r["points"] for r in rows)
            gw_results.append({
                "gw": gw,
                "total": total,
                "correct_winner": correct_w,
                "correct_score": correct_s,
                "exact_score": correct_s,
                "points": points,
                "accuracy_pct": round(correct_w / total * 100, 1),
                "score_acc_pct": round(correct_s / total * 100, 1),
                "details": rows,
            })
    gw_results.sort(key=lambda x: x["gw"])
    return gw_results, _comparison_summary(comparison_rows)

# ── GW evaluation ─────────────────────────────────────────────────────────────

def evaluate_gw(gw, predictions, all_fixtures):
    """
    Compare stored predictions for one GW against actual results.
    Returns a result dict, or None if no finished matches found.
    """
    finished = {
        f["id"]: f for f in all_fixtures
        if f.get("event") == gw and f.get("finished")
        and f.get("team_h_score") is not None
    }
    if not finished:
        return None

    rows = []
    for pred in predictions:
        mid = pred.get("match_id")
        f = finished.get(mid)
        if not f:
            continue
        hs = f["team_h_score"]
        as_ = f["team_a_score"]
        actual_w = _winner(hs, as_)
        pred_w   = pred.get("winner")
        rows.append({
            "match_id":       mid,
            "pred_winner":    pred_w,
            "actual_winner":  actual_w,
            "correct_winner": pred_w == actual_w,
            "correct_score":  pred.get("home_score") == hs and pred.get("away_score") == as_,
            "points":         _score_pred(pred, hs, as_)["points"],
            "home_pct":       pred.get("home_win_pct", 0),
            "draw_pct":       pred.get("draw_pct", 0),
            "away_pct":       pred.get("away_win_pct", 0),
        })

    if not rows:
        return None

    total     = len(rows)
    correct_w = sum(1 for r in rows if r["correct_winner"])
    correct_s = sum(1 for r in rows if r["correct_score"])
    points = sum(r["points"] for r in rows)
    return {
        "gw":              gw,
        "total":           total,
        "correct_winner":  correct_w,
        "correct_score":   correct_s,
        "exact_score":     correct_s,
        "points":          points,
        "accuracy_pct":    round(correct_w / total * 100, 1),
        "score_acc_pct":   round(correct_s / total * 100, 1),
        "details":         rows,
    }

# ── weight update ─────────────────────────────────────────────────────────────

def _factor_signals(detail_row, home_team, away_team):
    """
    Return per-factor binary signals (1 = factor pointed at actual winner).
    Only returned when the factor has a clear enough signal.
    """
    signals = {}
    actual = detail_row["actual_winner"]

    def add(name, predicted):
        if predicted in ("home", "away"):
            signals[name] = 1.0 if predicted == actual else 0.0

    ht, at = home_team, away_team

    # Form
    hf = ht.get("form_score", 50)
    af = at.get("form_score", 50)
    if abs(hf - af) > 8:
        add("form", "home" if hf > af else "away")

    # FPL strength ratings (attack − opponent defence, home context)
    h_att = ht.get("sah", 1000)
    h_def = ht.get("sdh", 1000)
    a_att = at.get("saa", 1000)
    a_def = at.get("sda", 1000)
    h_net = h_att - a_def
    a_net = a_att - h_def
    if abs(h_net - a_net) > 60:
        add("strength", "home" if h_net > a_net else "away")

    # League position (lower = better)
    hp = ht.get("position", 10)
    ap = at.get("position", 10)
    if abs(hp - ap) >= 3:
        add("position", "home" if hp < ap else "away")

    # Home advantage — always signals "home"
    signals["home_adv"] = 1.0 if actual == "home" else 0.0

    # Prediction confidence alignment (proxy for h2h / combined factors)
    hp_pct = detail_row["home_pct"]
    ap_pct = detail_row["away_pct"]
    if hp_pct > 52:
        add("h2h", "home")
    elif ap_pct > 52:
        add("h2h", "away")

    # Goals trend (xG)
    hxg = ht.get("xg", 1.0)
    axg = at.get("xg", 1.0)
    if abs(hxg - axg) > 0.3:
        add("goals_trend", "home" if hxg > axg else "away")

    # Clean sheet rate
    hxgc = ht.get("xgc", 1.0)
    axgc = at.get("xgc", 1.0)
    if abs(hxgc - axgc) > 0.3:
        # team that concedes less → stronger defensively
        add("clean_sheet", "home" if hxgc < axgc else "away")

    return signals


def update_weights(current_weights, eval_result, all_fixtures, teams):
    """
    Gradient-based weight update.
    For each factor: accuracy > 0.5 → increase weight; < 0.5 → decrease.
    Learning rate = 0.04. Normalize after.
    """
    if not eval_result or eval_result["total"] < 5:
        return current_weights

    details = eval_result["details"]
    fixture_map = {f["id"]: f for f in all_fixtures if f.get("finished")}
    factor_scores = {k: [] for k in current_weights}

    for row in details:
        f = fixture_map.get(row["match_id"])
        if not f:
            continue
        ht = teams.get(f.get("team_h"), {})
        at = teams.get(f.get("team_a"), {})
        for factor, score in _factor_signals(row, ht, at).items():
            if factor in factor_scores:
                factor_scores[factor].append(score)

    lr = 0.04
    new_w = dict(current_weights)
    for factor, scores in factor_scores.items():
        if len(scores) >= 3:
            avg = sum(scores) / len(scores)
            delta = (avg - 0.5) * lr
            new_w[factor] = round(max(0.03, min(0.25, new_w[factor] + delta)), 4)

    total = sum(new_w.values())
    return {k: round(v / total, 4) for k, v in new_w.items()}

# ── La Liga evaluation ────────────────────────────────────────────────────────

def evaluate_ll_gw(gw, ll_predictions, ll_fixtures):
    """
    Same as evaluate_gw but for La Liga fixtures (ESPN format).
    ll_fixtures: list of dicts with keys id, h, a, hs, as, fin, e (matchday)
    """
    finished = {
        f["id"]: f for f in ll_fixtures
        if f.get("e") == gw and f.get("fin")
        and f.get("hs") is not None
    }
    if not finished:
        return None

    rows = []
    for pred in ll_predictions:
        mid = pred.get("match_id")
        f = finished.get(mid)
        if not f:
            continue
        hs  = f["hs"]
        as_ = f["as"]
        actual_w = _winner(hs, as_)
        pred_w   = pred.get("winner")
        points = _score_pred(pred, hs, as_)["points"]
        rows.append({
            "match_id":       mid,
            "correct_winner": pred_w == actual_w,
            "correct_score":  pred.get("home_score") == hs and pred.get("away_score") == as_,
            "points":         points,
        })

    if not rows:
        return None

    total     = len(rows)
    correct_w = sum(1 for r in rows if r["correct_winner"])
    correct_s = sum(1 for r in rows if r["correct_score"])
    points = sum(r["points"] for r in rows)
    return {
        "gw":             gw,
        "total":          total,
        "correct_winner": correct_w,
        "correct_score":  correct_s,
        "exact_score":    correct_s,
        "points":         points,
        "accuracy_pct":   round(correct_w / total * 100, 1),
        "score_acc_pct":  round(correct_s / total * 100, 1),
    }

# ── main entry points ─────────────────────────────────────────────────────────

def run_pl_learning(all_pl_fixtures, teams_map):
    """
    Entry point for Premier League learning.
    all_pl_fixtures: full FPL fixtures list
    teams_map: dict of team_id → team stats dict
    Returns updated history dict.
    """
    raise RuntimeError("run_pl_learning is deprecated; use league_learning.run_persistent_competition")
    history     = _load(HISTORY_PATH, {"pl": {"gw_results": []}, "laliga": {"gw_results": []}})
    weights     = _load(WEIGHTS_PATH, dict(DEFAULT_WEIGHTS))
    predictions = _load(PREDICTIONS_PATH, {})

    pl_hist      = history.setdefault("pl", {"gw_results": []})
    processed    = {r["gw"] for r in pl_hist.get("gw_results", [])}
    full_results, model_comparison = _evaluate_prediction_rows(predictions, all_pl_fixtures)
    if full_results:
        pl_hist["gw_results"] = full_results
    new_results  = []
    weights_updated = False

    for gw_key, gw_data in predictions.items():
        try:
            gw = int(gw_key)
        except ValueError:
            continue
        if gw in processed:
            continue

        preds = gw_data.get("predictions", [])
        if not preds:
            continue

        ev = evaluate_gw(gw, preds, all_pl_fixtures)
        if not ev:
            continue

        print(f"  [ML] PL GW{gw}: {ev['correct_winner']}/{ev['total']} ({ev['accuracy_pct']}%)")
        new_results.append(ev)

        if ev["total"] >= 5:
            weights = update_weights(weights, ev, all_pl_fixtures, teams_map)
            weights_updated = True

    if new_results:
        pl_hist["gw_results"] = sorted(
            (pl_hist.get("gw_results", []) if full_results else pl_hist.get("gw_results", []) + new_results),
            key=lambda x: x["gw"],
        )

    if pl_hist.get("gw_results"):
        all_r = pl_hist["gw_results"]
        tot   = sum(r["total"] for r in all_r)
        cor   = sum(r["correct_winner"] for r in all_r)
        pl_hist["overall_accuracy"] = round(cor / tot * 100, 1) if tot else 0
        pl_hist["total_evaluated"]  = tot
        pl_hist["current_weights"]  = weights
        if model_comparison:
            pl_hist["model_comparison"] = model_comparison

        history["pl"] = pl_hist
        _save(HISTORY_PATH, history)

        if weights_updated:
            _save(WEIGHTS_PATH, weights)
            print(f"  [ML] Weights updated → {weights}")

    return history


def run_ll_learning(ll_fixtures, ll_predictions_by_gw=None):
    """
    Entry point for La Liga learning.
    ll_fixtures: list of ESPN compact fixtures
    ll_predictions_by_gw: dict of gw → [prediction dicts]
    """
    raise RuntimeError("run_ll_learning is deprecated; use league_learning.run_persistent_competition")
    history  = _load(HISTORY_PATH, {"pl": {"gw_results": []}, "laliga": {"gw_results": []}})
    raw_predictions = ll_predictions_by_gw if ll_predictions_by_gw is not None else _load(LL_PREDICTIONS_PATH, {})
    normalized_predictions = {}
    for gw, data in (raw_predictions or {}).items():
        if isinstance(data, dict):
            normalized_predictions[str(gw)] = {"predictions": data.get("predictions", [])}
        else:
            normalized_predictions[str(gw)] = {"predictions": data or []}
    ll_hist  = history.setdefault("laliga", {"gw_results": []})
    processed = {r["gw"] for r in ll_hist.get("gw_results", [])}
    full_results, model_comparison = _evaluate_prediction_rows(normalized_predictions, ll_fixtures, compact=True)
    if full_results:
        ll_hist["gw_results"] = full_results
    new_results = []

    for gw, gw_data in normalized_predictions.items():
        try:
            gw_int = int(gw)
        except (TypeError, ValueError):
            continue
        preds = gw_data.get("predictions", [])
        if gw_int in processed or not preds:
            continue
        ev = evaluate_ll_gw(gw_int, preds, ll_fixtures)
        if not ev:
            continue
        print(f"  [ML] LL MD{gw_int}: {ev['correct_winner']}/{ev['total']} ({ev['accuracy_pct']}%)")
        new_results.append(ev)

    if new_results:
        ll_hist["gw_results"] = sorted(
            (ll_hist.get("gw_results", []) if full_results else ll_hist.get("gw_results", []) + new_results),
            key=lambda x: x["gw"],
        )

    if ll_hist.get("gw_results"):
        all_r = ll_hist["gw_results"]
        tot   = sum(r["total"] for r in all_r)
        cor   = sum(r["correct_winner"] for r in all_r)
        ll_hist["overall_accuracy"] = round(cor / tot * 100, 1) if tot else 0
        ll_hist["total_evaluated"]  = tot
        ll_hist.setdefault("current_weights", dict(DEFAULT_WEIGHTS))
        if model_comparison:
            ll_hist["model_comparison"] = model_comparison

        history["laliga"] = ll_hist
        _save(HISTORY_PATH, history)

    return history
