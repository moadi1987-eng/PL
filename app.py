import time
import json
import os
from flask import Flask, render_template, jsonify, request, send_from_directory
import requests
from functools import wraps

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
GUESSES_FILE = os.path.join(DATA_DIR, "user_guesses.json")
AI_PREDS_FILE = os.path.join(DATA_DIR, "ai_predictions.json")

FPL_BASE = "https://fantasy.premierleague.com/api"
PL_BADGE = "https://resources.premierleague.com/premierleague/badges/70/t{code}.png"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_CACHE_TTL = 600  # 10 min to save quota

_cache = {}
CACHE_TTL = 300
LIVE_CACHE_TTL = 5


def cached(key_fn, ttl=CACHE_TTL):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            now = time.time()
            if key in _cache and now - _cache[key]["ts"] < ttl:
                return _cache[key]["data"]
            result = fn(*args, **kwargs)
            _cache[key] = {"data": result, "ts": now}
            return result
        return wrapper
    return decorator


def fpl_get(endpoint):
    url = f"{FPL_BASE}/{endpoint}"
    headers = {"User-Agent": "PL-Dashboard/1.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


@cached(lambda: "bootstrap")
def get_bootstrap():
    return fpl_get("bootstrap-static/")


@cached(lambda: "fixtures")
def get_all_fixtures():
    return fpl_get("fixtures/")


def get_all_fixtures_live():
    """Shorter cache for live updates."""
    key = "fixtures"
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < LIVE_CACHE_TTL:
        return _cache[key]["data"]
    data = fpl_get("fixtures/")
    _cache[key] = {"data": data, "ts": now}
    return data


def check_live_matches():
    """Check if any matches are currently in progress."""
    all_fix = get_all_fixtures()
    return any(f.get("started") and not f.get("finished") for f in all_fix)


def build_team_map():
    data = get_bootstrap()
    teams = {}
    for t in data.get("teams", []):
        teams[t["id"]] = {
            "id": t["id"],
            "name": t["name"],
            "short_name": t["short_name"],
            "code": t["code"],
            "badge": PL_BADGE.format(code=t["code"]),
            "strength": t["strength"],
            "strength_overall_home": t.get("strength_overall_home", 0),
            "strength_overall_away": t.get("strength_overall_away", 0),
            "strength_attack_home": t.get("strength_attack_home", 0),
            "strength_attack_away": t.get("strength_attack_away", 0),
            "strength_defence_home": t.get("strength_defence_home", 0),
            "strength_defence_away": t.get("strength_defence_away", 0),
        }
    return teams


def get_current_gameweek():
    data = get_bootstrap()
    for ev in data.get("events", []):
        if ev.get("is_current"):
            return ev["id"]
    finished = [ev for ev in data.get("events", []) if ev.get("finished")]
    if finished:
        return finished[-1]["id"]
    return 1


def get_gameweeks_info():
    data = get_bootstrap()
    gws = []
    for ev in data.get("events", []):
        gws.append({
            "id": ev["id"],
            "name": ev["name"],
            "finished": ev.get("finished", False),
            "is_current": ev.get("is_current", False),
            "is_next": ev.get("is_next", False),
            "deadline_time": ev.get("deadline_time", ""),
        })
    return gws


def fixtures_for_gameweek(gw, live=False):
    from datetime import datetime, timezone
    all_fix = get_all_fixtures_live() if live else get_all_fixtures()
    team_map = build_team_map()
    matches = []
    now = datetime.now(timezone.utc)
    for f in all_fix:
        if f.get("event") == gw:
            home = team_map.get(f["team_h"], {})
            away = team_map.get(f["team_a"], {})
            started = f.get("started", False)
            finished = f.get("finished", False)
            finished_provisional = f.get("finished_provisional", False)
            minutes = f.get("minutes", 0)
            elapsed = 0

            ko = f.get("kickoff_time", "")
            if ko and started:
                try:
                    kick = datetime.fromisoformat(ko.replace("Z", "+00:00"))
                    elapsed = (now - kick).total_seconds() / 60
                except Exception:
                    elapsed = 0

            if started and not finished:
                if minutes == 0 and elapsed > 0:
                    minutes = max(0, int(elapsed))

                # Detect likely finished: >115 min since kickoff = match is over
                if elapsed > 115:
                    finished = True
                    minutes = 90
                elif minutes > 45 and minutes <= 48 and elapsed < 65:
                    minutes = 45  # HT
                elif minutes > 90:
                    minutes = 90  # show as 90+

            if finished_provisional:
                finished = True

            is_live = started and not finished

            matches.append({
                "id": f["id"],
                "home_team": home.get("name", "Unknown"),
                "home_short": home.get("short_name", "???"),
                "home_badge": home.get("badge", ""),
                "home_score": f.get("team_h_score"),
                "away_team": away.get("name", "Unknown"),
                "away_short": away.get("short_name", "???"),
                "away_badge": away.get("badge", ""),
                "away_score": f.get("team_a_score"),
                "finished": finished,
                "started": started,
                "is_live": is_live,
                "minutes": minutes,
                "kickoff_time": ko,
                "home_id": f.get("team_h"),
                "away_id": f.get("team_a"),
            })
    return matches


def team_last_n_matches(team_id, n=5, before_gw=None):
    all_fix = get_all_fixtures()
    team_map = build_team_map()
    relevant = []
    for f in all_fix:
        if not f.get("finished"):
            continue
        if before_gw and f.get("event", 99) >= before_gw:
            continue
        if f["team_h"] == team_id or f["team_a"] == team_id:
            is_home = f["team_h"] == team_id
            goals_for = f["team_h_score"] if is_home else f["team_a_score"]
            goals_against = f["team_a_score"] if is_home else f["team_h_score"]
            opponent_id = f["team_a"] if is_home else f["team_h"]
            opponent = team_map.get(opponent_id, {})

            if goals_for is None or goals_against is None:
                continue

            if goals_for > goals_against:
                result = "W"
            elif goals_for == goals_against:
                result = "D"
            else:
                result = "L"

            relevant.append({
                "gameweek": f.get("event"),
                "opponent": opponent.get("name", "Unknown"),
                "opponent_short": opponent.get("short_name", "???"),
                "opponent_badge": opponent.get("badge", ""),
                "is_home": is_home,
                "goals_for": goals_for,
                "goals_against": goals_against,
                "result": result,
            })

    relevant.sort(key=lambda x: x["gameweek"], reverse=True)
    return relevant[:n]


def compute_team_stats(team_id, n=5, before_gw=None):
    matches = team_last_n_matches(team_id, n, before_gw)
    if not matches:
        return {"matches": [], "wins": 0, "draws": 0, "losses": 0,
                "goals_for": 0, "goals_against": 0, "points": 0, "form_score": 0}

    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")
    gf = sum(m["goals_for"] for m in matches)
    ga = sum(m["goals_against"] for m in matches)
    pts = wins * 3 + draws
    form_score = round(pts / (len(matches) * 3) * 100, 1) if matches else 0

    return {
        "matches": matches,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": gf,
        "goals_against": ga,
        "goal_diff": gf - ga,
        "points": pts,
        "form_score": form_score,
    }


def build_standings(live=False):
    all_fix = get_all_fixtures_live() if live else get_all_fixtures()
    team_map = build_team_map()
    table = {}
    for tid in team_map:
        table[tid] = {"team": team_map[tid], "played": 0, "won": 0, "drawn": 0,
                       "lost": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0}

    for f in all_fix:
        has_score = f.get("team_h_score") is not None
        is_relevant = f.get("finished") or (live and f.get("started") and has_score)
        if not is_relevant or not has_score:
            continue
        h, a = f["team_h"], f["team_a"]
        hs, as_ = f["team_h_score"], f["team_a_score"]
        if h in table:
            table[h]["played"] += 1
            table[h]["gf"] += hs
            table[h]["ga"] += as_
            if hs > as_:
                table[h]["won"] += 1
                table[h]["points"] += 3
            elif hs == as_:
                table[h]["drawn"] += 1
                table[h]["points"] += 1
            else:
                table[h]["lost"] += 1
        if a in table:
            table[a]["played"] += 1
            table[a]["gf"] += as_
            table[a]["ga"] += hs
            if as_ > hs:
                table[a]["won"] += 1
                table[a]["points"] += 3
            elif as_ == hs:
                table[a]["drawn"] += 1
                table[a]["points"] += 1
            else:
                table[a]["lost"] += 1

    for tid in table:
        table[tid]["gd"] = table[tid]["gf"] - table[tid]["ga"]

    standings = sorted(table.values(),
                       key=lambda x: (x["points"], x["gd"], x["gf"]),
                       reverse=True)
    for i, row in enumerate(standings):
        row["position"] = i + 1

    return standings


def fetch_epl_odds():
    """Fetch EPL 1X2 odds from The Odds API (aggregates Bet365 and others). Returns list of {home_team, away_team, home_pct, draw_pct, away_pct, source} or []."""
    if not ODDS_API_KEY:
        return []
    key = "epl_odds"
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ODDS_CACHE_TTL:
        return _cache[key]["data"]
    try:
        r = requests.get(
            f"{ODDS_API_BASE}/sports/soccer_epl/odds",
            params={"apiKey": ODDS_API_KEY, "regions": "uk", "markets": "h2h", "oddsFormat": "decimal"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:
        _cache[key] = {"data": [], "ts": now}
        return []

    result = []
    for event in data:
        home_name = (event.get("home_team") or "").strip()
        away_name = (event.get("away_team") or "").strip()
        bookmakers = event.get("bookmakers") or []
        # Prefer bet365 if present, else first bookmaker
        bm = None
        for b in bookmakers:
            if b.get("key") == "bet365":
                bm = b
                break
        if not bm and bookmakers:
            bm = bookmakers[0]
        if not bm:
            continue
        markets = bm.get("markets") or []
        h2h = next((m for m in markets if m.get("key") == "h2h"), None)
        if not h2h:
            continue
        outcomes = {}
        for o in (h2h.get("outcomes") or []):
            name, price = o.get("name"), o.get("price")
            if name is not None and price:
                try:
                    outcomes[name] = float(price)
                except (TypeError, ValueError):
                    pass
        if len(outcomes) != 3:
            continue
        # Implied probability = 100/odd; normalize to 100%
        inv = {k: 100.0 / v for k, v in outcomes.items()}
        total_inv = sum(inv.values())
        if total_inv <= 0:
            continue
        home_pct = round(inv.get(home_name, 0) / total_inv * 100, 1)
        away_pct = round(inv.get(away_name, 0) / total_inv * 100, 1)
        draw_pct = round(inv.get("Draw", 0) / total_inv * 100, 1)
        result.append({
            "home_team": home_name,
            "away_team": away_name,
            "home_pct": home_pct,
            "draw_pct": draw_pct,
            "away_pct": away_pct,
            "source": bm.get("title") or "Market",
        })
    _cache[key] = {"data": result, "ts": time.time()}
    return result


def _norm(s):
    """Normalize team name for matching."""
    if not s:
        return ""
    s = (s or "").lower().replace(" ", "").replace(".", "")
    if s.endswith("fc"):
        s = s[:-2]
    return s.strip()


def get_odds_for_match(home_name, away_name):
    """Get bookmaker percentages for a match by team names. Returns dict or None."""
    odds_list = fetch_epl_odds()
    hn = _norm(home_name)
    an = _norm(away_name)
    for o in odds_list:
        if _norm(o["home_team"]) == hn and _norm(o["away_team"]) == an:
            return o
    return None


def predict_match(home_id, away_id, current_gw):
    """Prediction based on form, team strength, league position, and home advantage."""
    home_stats = compute_team_stats(home_id, 5, current_gw)
    away_stats = compute_team_stats(away_id, 5, current_gw)
    team_map = build_team_map()
    standings = build_standings()
    pos_map = {r["team"]["id"]: r["position"] for r in standings}

    home_team = team_map.get(home_id, {})
    away_team = team_map.get(away_id, {})

    home_form = home_stats["form_score"]
    away_form = away_stats["form_score"]

    h_attack = home_team.get("strength_attack_home", 1000)
    h_defence = home_team.get("strength_defence_home", 1000)
    a_attack = away_team.get("strength_attack_away", 1000)
    a_defence = away_team.get("strength_defence_away", 1000)

    home_pos = pos_map.get(home_id, 10)
    away_pos = pos_map.get(away_id, 10)
    # Higher position (lower number) = stronger. Scale 1-20 to 0-100
    home_pos_score = (21 - home_pos) / 20 * 100
    away_pos_score = (21 - away_pos) / 20 * 100

    # Home advantage is significant in PL (~46% home wins historically)
    HOME_BOOST = 15

    # Weighted: form 25% + strength 25% + league position 25% + home advantage 25%
    home_score = (
        home_form * 0.25 +
        (h_attack / max(a_defence, 1)) * 40 * 0.25 +
        home_pos_score * 0.25 +
        HOME_BOOST * 0.25
    )
    away_score = (
        away_form * 0.25 +
        (a_attack / max(h_defence, 1)) * 40 * 0.25 +
        away_pos_score * 0.25
    )

    total = home_score + away_score if (home_score + away_score) > 0 else 1
    home_pct = home_score / total * 100
    away_pct = away_score / total * 100

    # Draw probability: higher when teams are close in quality
    closeness = 1 - abs(home_pct - away_pct) / 100
    draw_pct = 15 + closeness * 18  # 15-33% range

    # Normalize to 100%
    raw_total = home_pct + draw_pct + away_pct
    home_pct = round(home_pct / raw_total * 100, 1)
    draw_pct = round(draw_pct / raw_total * 100, 1)
    away_pct = round(100 - home_pct - draw_pct, 1)

    # Goals prediction based on form + strength
    h_matches = max(len(home_stats["matches"]), 1)
    a_matches = max(len(away_stats["matches"]), 1)
    avg_home_gf = home_stats["goals_for"] / h_matches
    avg_home_ga = home_stats["goals_against"] / h_matches
    avg_away_gf = away_stats["goals_for"] / a_matches
    avg_away_ga = away_stats["goals_against"] / a_matches

    # Predicted goals: average of team's scoring + opponent's conceding
    pred_home_goals = (avg_home_gf + avg_away_ga) / 2
    pred_away_goals = (avg_away_gf + avg_home_ga) / 2

    # Home boost for goals
    pred_home_goals = round(pred_home_goals * 1.1, 1)
    pred_away_goals = round(pred_away_goals * 0.95, 1)

    return {
        "home_win_pct": home_pct,
        "draw_pct": draw_pct,
        "away_win_pct": away_pct,
        "predicted_home_goals": round(pred_home_goals, 1),
        "predicted_away_goals": round(pred_away_goals, 1),
        "home_form": home_stats,
        "away_form": away_stats,
    }


# ─── AI Prediction Storage ───

def load_ai_preds():
    if os.path.exists(AI_PREDS_FILE):
        with open(AI_PREDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ai_preds(data):
    with open(AI_PREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def store_ai_predictions_for_gw(gw):
    """Generate and store AI predictions for a gameweek. Only before matches start."""
    ai_data = load_ai_preds()
    key = str(gw)
    if key in ai_data:
        return ai_data[key]

    matches = fixtures_for_gameweek(gw)

    # Block if ANY match has already started
    if any(m.get("started") or m.get("finished") for m in matches):
        return {"predictions": [], "locked": True, "reason": "Matches already started"}
    preds = []
    for m in matches:
        pred = predict_match(m["home_id"], m["away_id"], gw)
        home_score = round(pred["predicted_home_goals"])
        away_score = round(pred["predicted_away_goals"])
        # Winner must match the predicted score: 2-2 → Draw, 3-1 → home, etc.
        if home_score > away_score:
            winner = "home"
        elif home_score < away_score:
            winner = "away"
        else:
            winner = "draw"
        preds.append({
            "match_id": m["id"],
            "winner": winner,
            "home_score": home_score,
            "away_score": away_score,
            "home_win_pct": pred["home_win_pct"],
            "draw_pct": pred["draw_pct"],
            "away_win_pct": pred["away_win_pct"],
        })
    ai_data[key] = {"predictions": preds, "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_ai_preds(ai_data)
    return ai_data[key]


def score_ai_for_gw(gw):
    """Score AI predictions against actual results."""
    ai_data = load_ai_preds()
    key = str(gw)
    if key not in ai_data:
        return None

    preds = ai_data[key].get("predictions", [])
    fixtures = fixtures_for_gameweek(gw)
    fix_map = {f["id"]: f for f in fixtures}

    total = 0
    correct_winner = 0
    correct_score = 0

    for p in preds:
        f = fix_map.get(p["match_id"])
        if not f or not f["finished"]:
            continue
        total += 1
        ah, aa = f["home_score"], f["away_score"]
        actual_w = "home" if ah > aa else ("draw" if ah == aa else "away")
        if p["winner"] == actual_w:
            correct_winner += 1
        if p["home_score"] == ah and p["away_score"] == aa:
            correct_score += 1

    return {
        "total": total,
        "correct_winner": correct_winner,
        "correct_score": correct_score,
        "points": correct_winner * 3 + correct_score * 5,
    }


# ─── Routes ───

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/m")
def mobile_page():
    return render_template("index.html")


@app.route("/api/fpl-proxy/<path:endpoint>")
def fpl_proxy(endpoint):
    try:
        data = fpl_get(endpoint)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/teams")
def api_teams():
    try:
        team_map = build_team_map()
        teams = sorted(team_map.values(), key=lambda t: t["name"])
        return jsonify({"teams": teams})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gameweeks")
def api_gameweeks():
    try:
        gws = get_gameweeks_info()
        current = get_current_gameweek()
        return jsonify({"gameweeks": gws, "current_gameweek": current})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fixtures/<int:gw>")
def api_fixtures(gw):
    try:
        live = request.args.get("live", "0") == "1"
        matches = fixtures_for_gameweek(gw, live=live)
        has_live = any(m["is_live"] for m in matches)
        return jsonify({"gameweek": gw, "fixtures": matches, "has_live": has_live})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/standings")
def api_standings():
    try:
        live = request.args.get("live", "0") == "1"
        standings = build_standings(live=live)
        return jsonify({"standings": standings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/team/<int:team_id>/form")
def api_team_form(team_id):
    try:
        n = request.args.get("n", 5, type=int)
        before_gw = request.args.get("before_gw", None, type=int)
        stats = compute_team_stats(team_id, n, before_gw)
        team_map = build_team_map()
        team = team_map.get(team_id, {})
        return jsonify({"team": team, "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare/<int:team1>/<int:team2>")
def api_compare(team1, team2):
    try:
        n = request.args.get("n", 5, type=int)
        stats1 = compute_team_stats(team1, n)
        stats2 = compute_team_stats(team2, n)
        team_map = build_team_map()
        t1 = team_map.get(team1, {})
        t2 = team_map.get(team2, {})
        return jsonify({
            "team1": {"info": t1, "stats": stats1},
            "team2": {"info": t2, "stats": stats2},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/predictions/<int:gw>")
def api_predictions(gw):
    try:
        live = request.args.get("live", "0") == "1"
        matches = fixtures_for_gameweek(gw, live=live)
        predictions = []
        for m in matches:
            pred = predict_match(m["home_id"], m["away_id"], gw)
            max_pct = max(pred["home_win_pct"], pred["draw_pct"], pred["away_win_pct"])
            form_diff = abs(pred["home_form"]["form_score"] - pred["away_form"]["form_score"])
            confidence = round(max_pct * 0.6 + form_diff * 0.4, 1)
            row = {
                "match": m,
                "prediction": pred,
                "confidence": confidence,
            }
            if ODDS_API_KEY:
                odds = get_odds_for_match(m.get("home_team", ""), m.get("away_team", ""))
                if odds:
                    row["odds"] = {
                        "home_pct": odds["home_pct"],
                        "draw_pct": odds["draw_pct"],
                        "away_pct": odds["away_pct"],
                        "source": odds.get("source", "Market"),
                    }
            predictions.append(row)

        # Top 5 best bets = highest confidence
        sorted_preds = sorted(predictions, key=lambda x: x["confidence"], reverse=True)
        top5_ids = {p["match"]["id"] for p in sorted_preds[:5]}
        for p in predictions:
            p["is_top_bet"] = p["match"]["id"] in top5_ids
            p["bet_rank"] = None
        for i, p in enumerate(sorted_preds[:5]):
            for pp in predictions:
                if pp["match"]["id"] == p["match"]["id"]:
                    pp["bet_rank"] = i + 1

        return jsonify({"gameweek": gw, "predictions": predictions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Guesses Storage ───

def load_guesses():
    if os.path.exists(GUESSES_FILE):
        with open(GUESSES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_guesses(data):
    with open(GUESSES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def score_guesses_for_gw(gw):
    """Compare user guesses to actual results and compute score."""
    guesses_data = load_guesses()
    key = str(gw)
    if key not in guesses_data:
        return None

    gw_guesses = guesses_data[key]
    fixtures = fixtures_for_gameweek(gw)
    fix_map = {f["id"]: f for f in fixtures}

    total = 0
    correct_winner = 0
    correct_score = 0
    results = []

    for g in gw_guesses.get("guesses", []):
        mid = g["match_id"]
        f = fix_map.get(mid)
        if not f or not f["finished"]:
            results.append({**g, "scored": False})
            continue

        total += 1
        actual_h = f["home_score"]
        actual_a = f["away_score"]

        if actual_h > actual_a:
            actual_winner = "home"
        elif actual_h == actual_a:
            actual_winner = "draw"
        else:
            actual_winner = "away"

        winner_ok = g.get("winner") == actual_winner
        score_ok = (g.get("home_score") == actual_h and
                    g.get("away_score") == actual_a)

        if winner_ok:
            correct_winner += 1
        if score_ok:
            correct_score += 1

        results.append({
            **g,
            "scored": True,
            "actual_home_score": actual_h,
            "actual_away_score": actual_a,
            "actual_winner": actual_winner,
            "winner_correct": winner_ok,
            "score_correct": score_ok,
        })

    points = correct_winner * 3 + correct_score * 5
    return {
        "gameweek": gw,
        "total_matches": total,
        "correct_winner": correct_winner,
        "correct_score": correct_score,
        "points": points,
        "results": results,
    }


@app.route("/api/guesses/<int:gw>", methods=["GET"])
def api_get_guesses(gw):
    try:
        guesses_data = load_guesses()
        key = str(gw)
        gw_data = guesses_data.get(key, {})
        return jsonify({"gameweek": gw, "data": gw_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/guesses/<int:gw>", methods=["POST"])
def api_save_guesses(gw):
    try:
        body = request.get_json()
        guesses_data = load_guesses()
        key = str(gw)
        guesses_data[key] = {
            "guesses": body.get("guesses", []),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_guesses(guesses_data)
        return jsonify({"ok": True, "gameweek": gw})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/import-phone-guesses", methods=["POST"])
def api_import_phone_guesses():
    try:
        body = request.get_json()
        phone_data = body.get("guesses", {})
        guesses_data = load_guesses()
        imported = 0
        for gw_key, matches in phone_data.items():
            if gw_key not in guesses_data:
                guesses_data[gw_key] = {"guesses": [], "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            existing = {g["match_id"]: g for g in guesses_data[gw_key].get("guesses", [])}
            for mid_str, g in matches.items():
                mid = int(mid_str)
                if mid not in existing and g.get("w"):
                    existing[mid] = {
                        "match_id": mid,
                        "winner": g["w"],
                        "home_score": g.get("hs"),
                        "away_score": g.get("as"),
                    }
                    imported += 1
            guesses_data[gw_key]["guesses"] = list(existing.values())
            guesses_data[gw_key]["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_guesses(guesses_data)
        return jsonify({"ok": True, "imported": imported})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/guesses/<int:gw>/score")
def api_score_guesses(gw):
    try:
        result = score_guesses_for_gw(gw)
        if result is None:
            return jsonify({"gameweek": gw, "has_guesses": False})
        return jsonify({"gameweek": gw, "has_guesses": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_top5_best_bets(gw):
    """Return list of top 5 prediction rows (match + prediction + bet_rank) for the gameweek."""
    matches = fixtures_for_gameweek(gw)
    preds = []
    for m in matches:
        pred = predict_match(m["home_id"], m["away_id"], gw)
        max_pct = max(pred["home_win_pct"], pred["draw_pct"], pred["away_win_pct"])
        form_diff = abs(pred["home_form"]["form_score"] - pred["away_form"]["form_score"])
        confidence = round(max_pct * 0.6 + form_diff * 0.4, 1)
        preds.append({"match": m, "prediction": pred, "confidence": confidence})
    sorted_preds = sorted(preds, key=lambda x: x["confidence"], reverse=True)
    top5 = []
    for i, p in enumerate(sorted_preds[:5]):
        p["bet_rank"] = i + 1
        top5.append(p)
    return top5


@app.route("/api/guesses/<int:gw>/best-bets-score")
def api_best_bets_score(gw):
    """For the 5 Best Bet matches we recommended, how many did the user get right (winner + exact)."""
    try:
        top5 = _get_top5_best_bets(gw)
        if not top5:
            return jsonify({"gameweek": gw, "best_bets": [], "summary": {"total": 0, "winner_ok": 0, "score_ok": 0, "ai_winner_ok": 0}})

        fixtures = fixtures_for_gameweek(gw)
        fix_map = {f["id"]: f for f in fixtures}
        guesses_data = load_guesses()
        user_guesses = guesses_data.get(str(gw), {}).get("guesses", [])
        user_map = {g["match_id"]: g for g in user_guesses}

        W = {"home": "home_short", "draw": "Draw", "away": "away_short"}
        best_bets = []
        winner_ok_count = 0
        score_ok_count = 0
        ai_winner_ok_count = 0

        for p in top5:
            m = p["match"]
            pred = p["prediction"]
            mid = m["id"]
            f = fix_map.get(mid)
            ug = user_map.get(mid, {})
            home_short = m.get("home_short", "?")
            away_short = m.get("away_short", "?")

            actual_winner = None
            actual_home = None
            actual_away = None
            if f and f.get("finished") and f.get("home_score") is not None and f.get("away_score") is not None:
                actual_home = f["home_score"]
                actual_away = f["away_score"]
                if actual_home > actual_away:
                    actual_winner = "home"
                elif actual_home == actual_away:
                    actual_winner = "draw"
                else:
                    actual_winner = "away"

            # User correctness
            user_winner = ug.get("winner")
            user_home = ug.get("home_score")
            user_away = ug.get("away_score")
            winner_ok = bool(actual_winner and user_winner and user_winner == actual_winner)
            score_ok = bool(actual_winner and user_home is not None and user_away is not None and
                            int(user_home) == int(actual_home) and int(user_away) == int(actual_away))
            if winner_ok:
                winner_ok_count += 1
            if score_ok:
                score_ok_count += 1

            # AI recommendation: use same logic as api_guess_advice (strict >) so table matches "Advice"
            if pred["home_win_pct"] > pred["away_win_pct"] and pred["home_win_pct"] > pred["draw_pct"]:
                ai_winner = "home"
            elif pred["away_win_pct"] > pred["home_win_pct"] and pred["away_win_pct"] > pred["draw_pct"]:
                ai_winner = "away"
            else:
                ai_winner = "draw"

            ai_pred_home = int(round(float(pred.get("predicted_home_goals", 0))))
            ai_pred_away = int(round(float(pred.get("predicted_away_goals", 0))))
            # If predicted score is a draw (e.g. 2-2), AI recommendation counts as "draw" for ✓/✗
            ai_winner_for_ok = "draw" if (ai_pred_home == ai_pred_away) else ai_winner
            ai_winner_ok = bool(actual_winner and ai_winner_for_ok == actual_winner)
            # Explicit: AI said draw but actual was not draw → must be ✗ (e.g. FUL 2-1)
            if ai_pred_home == ai_pred_away and actual_winner and actual_winner != "draw":
                ai_winner_ok = False
            if ai_winner_ok:
                ai_winner_ok_count += 1

            u_winner_str = (home_short if user_winner == "home" else away_short if user_winner == "away" else "Draw") if user_winner else "–"
            a_winner_str = (home_short if actual_winner == "home" else away_short if actual_winner == "away" else "Draw") if actual_winner else "–"
            u_score_str = f"{user_home}-{user_away}" if user_home is not None and user_away is not None else "–"
            a_score_str = f"{actual_home}-{actual_away}" if actual_home is not None and actual_away is not None else "–"

            if ai_pred_home == ai_pred_away:
                ai_advice_winner_str = "Draw"
            else:
                ai_advice_winner_str = home_short if ai_winner == "home" else away_short if ai_winner == "away" else "Draw"
            ai_advice_score_str = f"{ai_pred_home}-{ai_pred_away}"
            ai_advice_str = f"{ai_advice_winner_str} ({ai_advice_score_str})"

            best_bets.append({
                "match_id": mid,
                "home_short": home_short,
                "away_short": away_short,
                "rank": p.get("bet_rank"),
                "user_winner": u_winner_str,
                "user_score": u_score_str,
                "actual_winner": a_winner_str,
                "actual_score": a_score_str,
                "ai_advice": ai_advice_str,
                "winner_ok": winner_ok,
                "score_ok": score_ok,
                "ai_winner_ok": ai_winner_ok,
                "finished": bool(actual_winner),
            })

        return jsonify({
            "gameweek": gw,
            "best_bets": best_bets,
            "summary": {"total": len(top5), "winner_ok": winner_ok_count, "score_ok": score_ok_count, "ai_winner_ok": ai_winner_ok_count},
        })
    except Exception as e:
        return jsonify({"error": str(e), "best_bets": [], "summary": {"total": 0, "winner_ok": 0, "score_ok": 0, "ai_winner_ok": 0}}), 200


@app.route("/api/guesses/history")
def api_guesses_history():
    try:
        guesses_data = load_guesses()
        history = []
        for gw_key in sorted(guesses_data.keys(), key=lambda x: int(x)):
            gw = int(gw_key)
            score = score_guesses_for_gw(gw)
            if score:
                history.append(score)
            else:
                history.append({
                    "gameweek": gw,
                    "total_matches": len(guesses_data[gw_key].get("guesses", [])),
                    "correct_winner": 0,
                    "correct_score": 0,
                    "points": 0,
                    "pending": True,
                })
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def build_reasoning(m, pred, rec_winner):
    """Build human-readable reasoning for a prediction."""
    home_name = m["home_short"]
    away_name = m["away_short"]
    hf = pred["home_form"]
    af = pred["away_form"]

    reasons = []

    # Form analysis
    if hf["form_score"] > 0 or af["form_score"] > 0:
        reasons.append(
            f"Form (last 5): {home_name} {hf['wins']}W {hf['draws']}D {hf['losses']}L "
            f"({hf['form_score']}%) | {away_name} {af['wins']}W {af['draws']}D {af['losses']}L "
            f"({af['form_score']}%)"
        )

    # Goals analysis
    if hf["matches"] or af["matches"]:
        h_avg_gf = round(hf["goals_for"] / max(len(hf["matches"]), 1), 1)
        h_avg_ga = round(hf["goals_against"] / max(len(hf["matches"]), 1), 1)
        a_avg_gf = round(af["goals_for"] / max(len(af["matches"]), 1), 1)
        a_avg_ga = round(af["goals_against"] / max(len(af["matches"]), 1), 1)
        reasons.append(
            f"Goals avg: {home_name} scores {h_avg_gf}, concedes {h_avg_ga} | "
            f"{away_name} scores {a_avg_gf}, concedes {a_avg_ga}"
        )

    # Home advantage
    reasons.append(f"{home_name} plays at HOME — home advantage boost applied")

    # Win probability
    reasons.append(
        f"Win probability: {home_name} {pred['home_win_pct']}% | "
        f"Draw {pred['draw_pct']}% | {away_name} {pred['away_win_pct']}%"
    )

    # Conclusion
    winner_labels = {"home": home_name, "draw": "Draw", "away": away_name}
    reasons.append(
        f"Prediction: {winner_labels[rec_winner]} "
        f"({round(pred['predicted_home_goals'])}-{round(pred['predicted_away_goals'])})"
    )

    return reasons


@app.route("/api/guess-advice/<int:gw>")
def api_guess_advice(gw):
    """Return AI recommendation for each match in a gameweek."""
    try:
        matches = fixtures_for_gameweek(gw)
        advice = []
        for m in matches:
            pred = predict_match(m["home_id"], m["away_id"], gw)
            if pred["home_win_pct"] > pred["away_win_pct"] and pred["home_win_pct"] > pred["draw_pct"]:
                rec_winner = "home"
            elif pred["away_win_pct"] > pred["home_win_pct"] and pred["away_win_pct"] > pred["draw_pct"]:
                rec_winner = "away"
            else:
                rec_winner = "draw"

            rec_home = round(pred["predicted_home_goals"])
            rec_away = round(pred["predicted_away_goals"])
            if rec_home == rec_away:
                rec_winner = "draw"

            reasons = build_reasoning(m, pred, rec_winner)

            advice.append({
                "match_id": m["id"],
                "recommended_winner": rec_winner,
                "recommended_home_score": rec_home,
                "recommended_away_score": rec_away,
                "confidence": pred,
                "reasons": reasons,
            })
        store_ai_predictions_for_gw(gw)
        return jsonify({"gameweek": gw, "advice": advice})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-status")
def api_live_status():
    """Check if matches are live, return live fixture data with short cache."""
    try:
        all_fix = get_all_fixtures_live()
        team_map = build_team_map()
        live = []
        has_live = False
        for f in all_fix:
            if f.get("started") and not f.get("finished"):
                has_live = True
                home = team_map.get(f["team_h"], {})
                away = team_map.get(f["team_a"], {})
                live.append({
                    "id": f["id"],
                    "event": f.get("event"),
                    "home_team": home.get("short_name", "???"),
                    "away_team": away.get("short_name", "???"),
                    "home_score": f.get("team_h_score"),
                    "away_score": f.get("team_a_score"),
                    "minutes": f.get("minutes", 0),
                })
        return jsonify({"has_live": has_live, "live_matches": live})
    except Exception as e:
        return jsonify({"has_live": False, "error": str(e)})


@app.route("/api/comparison/<int:gw>")
def api_comparison(gw):
    """Compare user guesses vs AI predictions vs actual results. All logic inline."""
    from datetime import datetime, timezone
    try:
        all_fix = get_all_fixtures_live()
        team_map = build_team_map()

        guesses_data = load_guesses()
        user_guesses = guesses_data.get(str(gw), {}).get("guesses", [])
        user_map = {g["match_id"]: g for g in user_guesses}

        ai_data = load_ai_preds()
        ai_preds = ai_data.get(str(gw), {}).get("predictions", [])
        ai_map = {p["match_id"]: p for p in ai_preds}

        u_total_w = 0
        u_total_s = 0
        a_total_w = 0
        a_total_s = 0
        comparisons = []
        now = datetime.now(timezone.utc)

        for f in all_fix:
            if f.get("event") != gw:
                continue
            fid = f["id"]
            home = team_map.get(f["team_h"], {})
            away = team_map.get(f["team_a"], {})
            hs = f.get("team_h_score")
            aws = f.get("team_a_score")
            fin = f.get("finished", False)
            started = f.get("started", False)
            if f.get("finished_provisional"):
                fin = True
            if started and not fin and hs is not None and aws is not None:
                ko = f.get("kickoff_time", "")
                if ko:
                    try:
                        kick = datetime.fromisoformat(ko.replace("Z", "+00:00"))
                        elapsed = (now - kick).total_seconds() / 60
                        if elapsed > 115:
                            fin = True
                    except Exception:
                        pass

            # Actual winner - also for live matches that have scores
            actual_w = None
            if hs is not None and aws is not None and (fin or started):
                if hs > aws:
                    actual_w = "home"
                elif hs == aws:
                    actual_w = "draw"
                else:
                    actual_w = "away"

            ug = user_map.get(fid, {})
            ap = ai_map.get(fid, {})

            # Winner derived from score: 2-2 → draw, 3-1 → home, 1-2 → away (so display and scoring are consistent)
            def winner_from_score(h, a):
                if h is None or a is None:
                    return None
                h, a = int(h), int(a)
                if h > a:
                    return "home"
                if h < a:
                    return "away"
                return "draw"

            # Normalize scores to int for comparison (API/JSON can be int or string)
            def score_match(ph, pa, ah, aa):
                if ph is None or pa is None or ah is None or aa is None:
                    return False
                return int(ph) == int(ah) and int(pa) == int(aa)

            # Use winner from score when score is set, so 2-2 always shows and counts as Draw
            user_winner = winner_from_score(ug.get("home_score"), ug.get("away_score")) or ug.get("winner")
            ai_winner = winner_from_score(ap.get("home_score"), ap.get("away_score")) or ap.get("winner")

            # User correct?
            u_w_ok = bool(actual_w and user_winner and user_winner == actual_w)
            u_s_ok = bool(actual_w and score_match(ug.get("home_score"), ug.get("away_score"), hs, aws))
            if u_w_ok:
                u_total_w += 1
            if u_s_ok:
                u_total_s += 1

            # AI correct?
            a_w_ok = bool(actual_w and ai_winner and ai_winner == actual_w)
            a_s_ok = bool(actual_w and score_match(ap.get("home_score"), ap.get("away_score"), hs, aws))
            if a_w_ok:
                a_total_w += 1
            if a_s_ok:
                a_total_s += 1

            comparisons.append({
                "home_short": home.get("short_name", "?"),
                "away_short": away.get("short_name", "?"),
                "home_badge": home.get("badge", ""),
                "away_badge": away.get("badge", ""),
                "finished": fin,
                "is_live": started and not fin,
                "minutes": f.get("minutes", 0),
                "actual_home": hs,
                "actual_away": aws,
                "actual_winner": actual_w,
                "user_winner": user_winner,
                "user_home": ug.get("home_score"),
                "user_away": ug.get("away_score"),
                "u_w_ok": u_w_ok,
                "u_s_ok": u_s_ok,
                "ai_winner": ai_winner,
                "ai_home": ap.get("home_score"),
                "ai_away": ap.get("away_score"),
                "a_w_ok": a_w_ok,
                "a_s_ok": a_s_ok,
            })

        u_pts = u_total_w * 3 + u_total_s * 5
        a_pts = a_total_w * 3 + a_total_s * 5

        return jsonify({
            "gameweek": gw,
            "comparisons": comparisons,
            "user_score": {"correct_winner": u_total_w, "correct_score": u_total_s, "points": u_pts},
            "ai_score": {"correct_winner": a_total_w, "correct_score": a_total_s, "points": a_pts},
        })
    except Exception as e:
        return jsonify({
            "gameweek": gw,
            "comparisons": [],
            "user_score": {"correct_winner": 0, "correct_score": 0, "points": 0},
            "ai_score": {"correct_winner": 0, "correct_score": 0, "points": 0},
            "error": str(e),
        }), 200


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=debug, host="0.0.0.0", port=5000)
