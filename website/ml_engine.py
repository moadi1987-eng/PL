"""
ML Learning Engine — PL & La Liga
Runs inside GitHub Actions after every build.

Flow:
  1. Load ai_predictions.json (stored predictions by GW)
  2. Compare with actual results from FPL / ESPN
  3. Update ai_weights.json using factor-accuracy gradient
  4. Save learning_history.json for the dashboard chart
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HISTORY_PATH  = os.path.join(ROOT, "learning_history.json")
WEIGHTS_PATH  = os.path.join(ROOT, "ai_weights.json")
PREDICTIONS_PATH = os.path.join(ROOT, "ai_predictions.json")

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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

def _winner(hs, as_):
    if hs is None or as_ is None:
        return None
    if hs > as_:
        return "home"
    if as_ > hs:
        return "away"
    return "draw"

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
            "home_pct":       pred.get("home_win_pct", 0),
            "draw_pct":       pred.get("draw_pct", 0),
            "away_pct":       pred.get("away_win_pct", 0),
        })

    if not rows:
        return None

    total     = len(rows)
    correct_w = sum(1 for r in rows if r["correct_winner"])
    correct_s = sum(1 for r in rows if r["correct_score"])
    return {
        "gw":              gw,
        "total":           total,
        "correct_winner":  correct_w,
        "correct_score":   correct_s,
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
        rows.append({
            "match_id":       mid,
            "correct_winner": pred_w == actual_w,
            "correct_score":  pred.get("home_score") == hs and pred.get("away_score") == as_,
        })

    if not rows:
        return None

    total     = len(rows)
    correct_w = sum(1 for r in rows if r["correct_winner"])
    correct_s = sum(1 for r in rows if r["correct_score"])
    return {
        "gw":             gw,
        "total":          total,
        "correct_winner": correct_w,
        "correct_score":  correct_s,
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
    history     = _load(HISTORY_PATH, {"pl": {"gw_results": []}, "laliga": {"gw_results": []}})
    weights     = _load(WEIGHTS_PATH, dict(DEFAULT_WEIGHTS))
    predictions = _load(PREDICTIONS_PATH, {})

    pl_hist      = history.setdefault("pl", {"gw_results": []})
    processed    = {r["gw"] for r in pl_hist.get("gw_results", [])}
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
            pl_hist.get("gw_results", []) + new_results,
            key=lambda x: x["gw"],
        )
        all_r = pl_hist["gw_results"]
        tot   = sum(r["total"] for r in all_r)
        cor   = sum(r["correct_winner"] for r in all_r)
        pl_hist["overall_accuracy"] = round(cor / tot * 100, 1) if tot else 0
        pl_hist["total_evaluated"]  = tot
        pl_hist["current_weights"]  = weights

        history["pl"] = pl_hist
        _save(HISTORY_PATH, history)

        if weights_updated:
            _save(WEIGHTS_PATH, weights)
            print(f"  [ML] Weights updated → {weights}")

    return history


def run_ll_learning(ll_fixtures, ll_predictions_by_gw):
    """
    Entry point for La Liga learning.
    ll_fixtures: list of ESPN compact fixtures
    ll_predictions_by_gw: dict of gw → [prediction dicts]
    """
    history  = _load(HISTORY_PATH, {"pl": {"gw_results": []}, "laliga": {"gw_results": []}})
    ll_hist  = history.setdefault("laliga", {"gw_results": []})
    processed = {r["gw"] for r in ll_hist.get("gw_results", [])}
    new_results = []

    for gw, preds in ll_predictions_by_gw.items():
        if gw in processed or not preds:
            continue
        ev = evaluate_ll_gw(gw, preds, ll_fixtures)
        if not ev:
            continue
        print(f"  [ML] LL MD{gw}: {ev['correct_winner']}/{ev['total']} ({ev['accuracy_pct']}%)")
        new_results.append(ev)

    if new_results:
        ll_hist["gw_results"] = sorted(
            ll_hist.get("gw_results", []) + new_results,
            key=lambda x: x["gw"],
        )
        all_r = ll_hist["gw_results"]
        tot   = sum(r["total"] for r in all_r)
        cor   = sum(r["correct_winner"] for r in all_r)
        ll_hist["overall_accuracy"] = round(cor / tot * 100, 1) if tot else 0
        ll_hist["total_evaluated"]  = tot

        history["laliga"] = ll_hist
        _save(HISTORY_PATH, history)

    return history
