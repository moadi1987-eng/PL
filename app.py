import time
import json
import os
import logging
from flask import Flask, render_template, jsonify, request
import requests
from functools import wraps

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
GUESSES_FILE = os.path.join(DATA_DIR, "user_guesses.json")
AI_PREDS_FILE = os.path.join(DATA_DIR, "ai_predictions.json")

FPL_BASE = "https://fantasy.premierleague.com/api"
PL_BADGE = "https://resources.premierleague.com/premierleague/badges/70/t{code}.png"
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ODDS_CACHE_TTL = 600  # 10 min to save quota

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
ESPN_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings"
ESPN_SLUGS = {"laliga": "esp.1"}

LEAGUES = {
    "pl": {"name": "Premier League", "short": "PL", "gw_label": "GW"},
    "laliga": {"name": "La Liga", "short": "LL", "gw_label": "MD"},
}

_cache = {}
CACHE_TTL = 300
LIVE_CACHE_TTL = 3
_CACHE_MAX_SIZE = 200


def _evict_cache():
    """Remove expired entries; if still too large, drop oldest half."""
    now = time.time()
    expired = [k for k, v in _cache.items() if now - v["ts"] > CACHE_TTL * 2]
    for k in expired:
        _cache.pop(k, None)
    if len(_cache) > _CACHE_MAX_SIZE:
        sorted_keys = sorted(_cache, key=lambda k: _cache[k]["ts"])
        for k in sorted_keys[: len(sorted_keys) // 2]:
            _cache.pop(k, None)


def cached(key_fn, ttl=CACHE_TTL):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs)
            now = time.time()
            if key in _cache and now - _cache[key]["ts"] < ttl:
                return _cache[key]["data"]
            result = fn(*args, **kwargs)
            if len(_cache) >= _CACHE_MAX_SIZE:
                _evict_cache()
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


@cached(lambda: "bootstrap", ttl=30)
def get_bootstrap():
    return fpl_get("bootstrap-static/")


def get_all_fixtures():
    key = "fixtures"
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < LIVE_CACHE_TTL:
        return _cache[key]["data"]
    data = fpl_get("fixtures/")
    _cache[key] = {"data": data, "ts": now}
    return data


def get_all_fixtures_live():
    """Same as get_all_fixtures — always fresh."""
    return get_all_fixtures()


def check_live_matches():
    """Check if any matches are currently in progress."""
    from datetime import datetime, timezone
    all_fix = get_all_fixtures()
    now = datetime.now(timezone.utc)
    for f in all_fix:
        if not (f.get("started") and not f.get("finished") and not f.get("finished_provisional")):
            continue
        ko = f.get("kickoff_time", "")
        if ko:
            try:
                kick = datetime.fromisoformat(ko.replace("Z", "+00:00"))
                if (now - kick).total_seconds() / 60 > 115:
                    continue  # treat as finished
            except Exception:
                pass
        return True
    return False


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
    from datetime import datetime, timezone
    data = get_bootstrap()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    all_fix = get_all_fixtures()

    current_id = None
    next_id = None
    for ev in data.get("events", []):
        if ev.get("is_current"):
            current_id = ev["id"]
        if ev.get("is_next"):
            next_id = ev["id"]

    if next_id:
        for f in all_fix:
            if f.get("event") == next_id and f.get("kickoff_time", "").startswith(today):
                return next_id

    if current_id:
        return current_id

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


def build_standings(live=False, up_to_gw=None):
    all_fix = get_all_fixtures_live() if live else get_all_fixtures()
    team_map = build_team_map()
    table = {}
    for tid in team_map:
        table[tid] = {"team": team_map[tid], "played": 0, "won": 0, "drawn": 0,
                       "lost": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0}

    for f in all_fix:
        if up_to_gw and f.get("event") and f["event"] > up_to_gw:
            continue
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


def _get_all_team_matches(team_id, before_gw=None):
    """Get ALL matches for a team (not just last N), for deeper analysis."""
    all_fix = get_all_fixtures()
    matches = []
    for f in all_fix:
        if not f.get("finished") or f.get("team_h_score") is None:
            continue
        if before_gw and f.get("event", 99) >= before_gw:
            continue
        if f["team_h"] == team_id or f["team_a"] == team_id:
            is_home = f["team_h"] == team_id
            gf = f["team_h_score"] if is_home else f["team_a_score"]
            ga = f["team_a_score"] if is_home else f["team_h_score"]
            opp = f["team_a"] if is_home else f["team_h"]
            matches.append({"gw": f.get("event"), "is_home": is_home, "gf": gf, "ga": ga,
                            "opp": opp, "result": "W" if gf > ga else "D" if gf == ga else "L"})
    matches.sort(key=lambda x: x["gw"])
    return matches


def _head_to_head(home_id, away_id):
    """Head-to-head record this season."""
    all_fix = get_all_fixtures()
    h2h = {"home_wins": 0, "draws": 0, "away_wins": 0, "home_goals": 0, "away_goals": 0, "matches": 0}
    for f in all_fix:
        if not f.get("finished") or f.get("team_h_score") is None:
            continue
        if (f["team_h"] == home_id and f["team_a"] == away_id) or \
           (f["team_h"] == away_id and f["team_a"] == home_id):
            hs, as_ = f["team_h_score"], f["team_a_score"]
            h2h["matches"] += 1
            if f["team_h"] == home_id:
                h2h["home_goals"] += hs
                h2h["away_goals"] += as_
                if hs > as_: h2h["home_wins"] += 1
                elif hs == as_: h2h["draws"] += 1
                else: h2h["away_wins"] += 1
            else:
                h2h["home_goals"] += as_
                h2h["away_goals"] += hs
                if as_ > hs: h2h["home_wins"] += 1
                elif as_ == hs: h2h["draws"] += 1
                else: h2h["away_wins"] += 1
    return h2h


def _streak_score(matches, n=5):
    """Momentum: recent streak weighted (most recent = highest weight). Returns 0-100."""
    recent = matches[-n:] if len(matches) >= n else matches
    if not recent:
        return 50
    total = 0
    weight_sum = 0
    for i, m in enumerate(recent):
        w = i + 1
        pts = 3 if m["result"] == "W" else 1 if m["result"] == "D" else 0
        total += pts * w
        weight_sum += 3 * w
    return round(total / max(weight_sum, 1) * 100, 1)


def _home_away_split(matches, is_home):
    """Performance specifically at home or away."""
    filtered = [m for m in matches if m["is_home"] == is_home]
    if not filtered:
        return {"gf_avg": 1.0, "ga_avg": 1.0, "win_rate": 33, "clean_sheets": 0}
    gf = sum(m["gf"] for m in filtered)
    ga = sum(m["ga"] for m in filtered)
    wins = sum(1 for m in filtered if m["result"] == "W")
    cs = sum(1 for m in filtered if m["ga"] == 0)
    n = len(filtered)
    return {"gf_avg": round(gf / n, 2), "ga_avg": round(ga / n, 2),
            "win_rate": round(wins / n * 100, 1), "clean_sheets": cs}


def _opponent_quality(opp_id, standings_map):
    """How strong is the opponent based on league position. Returns multiplier 0.7-1.3."""
    pos = standings_map.get(opp_id, 10)
    return 0.7 + (20 - pos) / 20 * 0.6


def _upset_potential(team_matches, opp_id, standings_map):
    """Detect if a 'weaker' team tends to beat stronger opponents (giant killer)."""
    upsets = 0
    total_vs_top = 0
    for m in team_matches:
        opp_pos = standings_map.get(m["opp"], 10)
        if opp_pos <= 6:
            total_vs_top += 1
            if m["result"] == "W":
                upsets += 1
    if total_vs_top == 0:
        return 0
    return round(upsets / total_vs_top * 100, 1)


def _clean_sheet_rate(matches):
    """Percentage of matches with a clean sheet (0 goals conceded)."""
    if not matches:
        return 0
    cs = sum(1 for m in matches if m["ga"] == 0)
    return round(cs / len(matches) * 100, 1)


def _concede_rate(matches, n=5):
    """Average goals conceded in last N matches."""
    recent = matches[-n:] if len(matches) >= n else matches
    if not recent:
        return 1.5
    return round(sum(m["ga"] for m in recent) / len(recent), 2)


def _draw_tendency(matches, n=8):
    """How often does this team draw? Helps predict draws better."""
    recent = matches[-n:] if len(matches) >= n else matches
    if not recent:
        return 15
    draws = sum(1 for m in recent if m["result"] == "D")
    return round(draws / len(recent) * 100, 1)


# ── Enhanced AI: Player Stats + Poisson Model ──

import math

def _get_team_players(team_id):
    """Get all players for a team with key stats."""
    bs = get_bootstrap()
    return [p for p in bs.get("elements", []) if p.get("team") == team_id]


def _team_xg_stats(team_id):
    """Aggregate xG stats for a team from player data."""
    players = _get_team_players(team_id)
    total_xg = sum(float(p.get("expected_goals", 0)) for p in players)
    total_xa = sum(float(p.get("expected_assists", 0)) for p in players)
    total_xgc = sum(float(p.get("expected_goals_conceded", 0)) for p in players if p.get("element_type") in (1, 2))
    total_mins = sum(p.get("minutes", 0) for p in players)
    matches_est = max(total_mins / 11 / 90, 1)
    return {
        "xg": total_xg, "xa": total_xa, "xgc": total_xgc,
        "xg_per_match": round(total_xg / matches_est, 2),
        "xgc_per_match": round(total_xgc / max(matches_est, 1), 2),
    }


def _key_player_impact(team_id):
    """Check if key players are injured/suspended. Returns penalty 0-1."""
    players = _get_team_players(team_id)
    top_attackers = sorted(players, key=lambda p: float(p.get("expected_goals", 0)), reverse=True)[:3]
    top_creators = sorted(players, key=lambda p: float(p.get("expected_assists", 0)), reverse=True)[:2]
    key = set()
    for p in top_attackers + top_creators:
        key.add(p["id"])

    penalty = 0
    for p in players:
        if p["id"] in key:
            status = p.get("status", "a")
            chance = p.get("chance_of_playing_next_round")
            if status in ("i", "s", "u") or chance == 0:
                xgi = float(p.get("expected_goal_involvements", 0))
                penalty += min(xgi / 15, 0.15)
            elif chance is not None and chance < 75:
                penalty += 0.03
    return round(min(penalty, 0.35), 3)


def _poisson_prob(lam, k):
    """P(X=k) for Poisson distribution."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def _poisson_match_probs(lambda_home, lambda_away, max_goals=6):
    """Calculate match outcome probabilities using Poisson model."""
    home_win = draw = away_win = 0
    scorelines = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = _poisson_prob(lambda_home, h) * _poisson_prob(lambda_away, a)
            scorelines[(h, a)] = p
            if h > a:
                home_win += p
            elif h == a:
                draw += p
            else:
                away_win += p
    total = home_win + draw + away_win
    if total > 0:
        home_win /= total
        draw /= total
        away_win /= total
    best_score = max(scorelines, key=scorelines.get)
    return {
        "home_win": round(home_win * 100, 1),
        "draw": round(draw * 100, 1),
        "away_win": round(away_win * 100, 1),
        "best_score": best_score,
        "score_prob": round(scorelines[best_score] * 100, 1),
    }


def _opponent_adjusted_goals(team_matches, pos_map, is_home):
    """Weight goals by opponent quality."""
    if not team_matches:
        return 1.2
    total = 0
    weight_sum = 0
    for i, m in enumerate(team_matches):
        opp_pos = pos_map.get(m["opp"], 10)
        quality = opp_pos / 20.0
        decay = 0.5 + 0.5 * (i / max(len(team_matches) - 1, 1))
        w = quality * decay
        total += m["gf"] * w
        weight_sum += w
    return total / max(weight_sum, 0.01)


AI_ACCURACY_FILE = os.path.join(DATA_DIR, "ai_accuracy.json")
ML_STATE_FILE = os.path.join(DATA_DIR, "ml_state.json")
ML_HISTORY_FILE = os.path.join(DATA_DIR, "ml_history.json")


def _default_ml_state():
    return {
        "factor_accuracy": {
            "form": 0.5, "strength": 0.5, "position": 0.5, "home_adv": 0.5,
            "streak": 0.5, "h2h": 0.5, "home_away_split": 0.5, "goals_trend": 0.5,
            "upset": 0.5, "clean_sheet": 0.5, "draw_tendency": 0.5,
        },
        "team_goal_bias": {},
        "team_result_bias": {},
        "poisson_blend": 0.6,
        "factor_blend": 0.4,
        "confidence_features": {
            "position_gap_weight": 0.15,
            "form_diff_weight": 0.20,
            "max_pct_weight": 0.30,
            "draw_penalty_weight": 0.10,
            "home_fav_bonus": 0.10,
            "h2h_clarity_weight": 0.08,
            "streak_diff_weight": 0.07,
        },
        "confidence_calibration": {},
        "matches_learned": 0,
        "learning_rate": 0.08,
        "last_learned_match_ids": [],
        "gw_accuracy_history": {},
    }


def _load_ml_state():
    if os.path.exists(ML_STATE_FILE):
        try:
            with open(ML_STATE_FILE, "r") as f:
                state = json.load(f)
            defaults = _default_ml_state()
            for k, v in defaults.items():
                if k not in state:
                    state[k] = v
            return state
        except Exception:
            pass
    return _default_ml_state()


def _save_ml_state(state):
    with open(ML_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _load_ml_history():
    if os.path.exists(ML_HISTORY_FILE):
        try:
            with open(ML_HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"matches": []}


def _save_ml_history(history):
    history["matches"] = history["matches"][-2000:]
    with open(ML_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def _extract_match_features(home_id, away_id, gw):
    """Extract all features for a match for learning purposes."""
    team_map = build_team_map()
    standings = build_standings(up_to_gw=gw - 1 if gw > 1 else None)
    pos_map = {r["team"]["id"]: r["position"] for r in standings}
    home_team = team_map.get(home_id, {})
    away_team = team_map.get(away_id, {})
    h_stats = compute_team_stats(home_id, 5, gw)
    a_stats = compute_team_stats(away_id, 5, gw)
    h_all = _get_all_team_matches(home_id, gw)
    a_all = _get_all_team_matches(away_id, gw)

    h_pos = pos_map.get(home_id, 10)
    a_pos = pos_map.get(away_id, 10)
    position_gap = abs(h_pos - a_pos)
    form_diff = abs(h_stats["form_score"] - a_stats["form_score"])

    h_str = (home_team.get("strength_attack_home", 1000) + home_team.get("strength_defence_home", 1000)) / 2
    a_str = (away_team.get("strength_attack_away", 1000) + away_team.get("strength_defence_away", 1000)) / 2

    h2h = _head_to_head(home_id, away_id)
    h2h_clarity = abs(h2h["home_wins"] - h2h["away_wins"]) / max(h2h["matches"], 1)

    h_streak = _streak_score(h_all, 5)
    a_streak = _streak_score(a_all, 5)
    streak_diff = abs(h_streak - a_streak)

    h_draw = _draw_tendency(h_all)
    a_draw = _draw_tendency(a_all)
    avg_draw_tend = (h_draw + a_draw) / 2

    h_cs = _clean_sheet_rate(h_all[-5:])
    a_cs = _clean_sheet_rate(a_all[-5:])

    home_is_favorite = h_pos < a_pos and h_stats["form_score"] > a_stats["form_score"]

    return {
        "home_id": home_id,
        "away_id": away_id,
        "gw": gw,
        "home_pos": h_pos,
        "away_pos": a_pos,
        "position_gap": position_gap,
        "form_diff": form_diff,
        "home_form": h_stats["form_score"],
        "away_form": a_stats["form_score"],
        "home_strength": h_str,
        "away_strength": a_str,
        "h2h_clarity": round(h2h_clarity, 3),
        "h2h_home_wins": h2h["home_wins"],
        "h2h_away_wins": h2h["away_wins"],
        "streak_diff": round(streak_diff, 1),
        "home_streak": h_streak,
        "away_streak": a_streak,
        "avg_draw_tendency": round(avg_draw_tend, 1),
        "home_cs_rate": h_cs,
        "away_cs_rate": a_cs,
        "home_is_favorite": home_is_favorite,
    }


def _compute_factor_predictions(home_id, away_id, gw):
    """What each factor predicts for this match."""
    team_map = build_team_map()
    standings = build_standings(up_to_gw=gw - 1 if gw > 1 else None)
    pos_map = {r["team"]["id"]: r["position"] for r in standings}
    ht = team_map.get(home_id, {})
    at = team_map.get(away_id, {})
    h_stats = compute_team_stats(home_id, 5, gw)
    a_stats = compute_team_stats(away_id, 5, gw)
    h_all = _get_all_team_matches(home_id, gw)
    a_all = _get_all_team_matches(away_id, gw)

    factors = {}
    factors["form"] = "home" if h_stats["form_score"] > a_stats["form_score"] else "away" if a_stats["form_score"] > h_stats["form_score"] else "draw"
    h_str = (ht.get("strength_attack_home", 1000) + ht.get("strength_defence_home", 1000)) / 2
    a_str = (at.get("strength_attack_away", 1000) + at.get("strength_defence_away", 1000)) / 2
    factors["strength"] = "home" if h_str > a_str else "away" if a_str > h_str else "draw"
    factors["position"] = "home" if pos_map.get(home_id, 10) < pos_map.get(away_id, 10) else "away" if pos_map.get(away_id, 10) < pos_map.get(home_id, 10) else "draw"
    factors["home_adv"] = "home"
    factors["streak"] = "home" if _streak_score(h_all) > _streak_score(a_all) else "away"
    h2h = _head_to_head(home_id, away_id)
    factors["h2h"] = "home" if h2h["home_wins"] > h2h["away_wins"] else "away" if h2h["away_wins"] > h2h["home_wins"] else "draw"
    h_split = _home_away_split(h_all, True)
    a_split = _home_away_split(a_all, False)
    factors["home_away_split"] = "home" if h_split["win_rate"] > a_split["win_rate"] else "away"
    h_trend = sum(m["gf"] for m in h_all[-3:]) / max(len(h_all[-3:]), 1) if h_all else 1
    a_trend = sum(m["gf"] for m in a_all[-3:]) / max(len(a_all[-3:]), 1) if a_all else 1
    factors["goals_trend"] = "home" if h_trend > a_trend else "away"
    h_upset = _upset_potential(h_all, away_id, pos_map)
    a_upset = _upset_potential(a_all, home_id, pos_map)
    factors["upset"] = "home" if h_upset > a_upset else "away" if a_upset > h_upset else "draw"
    h_cs = _clean_sheet_rate(h_all[-5:])
    a_cs = _clean_sheet_rate(a_all[-5:])
    factors["clean_sheet"] = "home" if h_cs > a_cs else "away" if a_cs > h_cs else "draw"
    h_draw = _draw_tendency(h_all)
    a_draw = _draw_tendency(a_all)
    avg_draw = (h_draw + a_draw) / 2
    factors["draw_tendency"] = "draw" if avg_draw > 30 else ("home" if h_stats["form_score"] > a_stats["form_score"] else "away")
    return factors


def learn_from_match(match_id, home_id, away_id, actual_hs, actual_as, gw, pred_hs=None, pred_as=None):
    """Core learning function: called after every finished match to update the model."""
    state = _load_ml_state()
    history = _load_ml_history()

    if match_id in state.get("last_learned_match_ids", []):
        return

    actual_winner = "home" if actual_hs > actual_as else "draw" if actual_hs == actual_as else "away"
    lr = state.get("learning_rate", 0.08)

    # 1. Factor accuracy update (online EMA)
    try:
        factor_preds = _compute_factor_predictions(home_id, away_id, gw)
        fa = state["factor_accuracy"]
        for k, pred_winner in factor_preds.items():
            correct = 1.0 if pred_winner == actual_winner else 0.0
            old_acc = fa.get(k, 0.5)
            fa[k] = round(old_acc * (1 - lr) + correct * lr, 6)
        state["factor_accuracy"] = fa

        total_acc = sum(fa.values())
        if total_acc > 0:
            new_weights = {k: round(v / total_acc, 4) for k, v in fa.items()}
            _save_weights(new_weights)
    except Exception:
        pass

    # 2. Team goal bias correction
    if pred_hs is not None and pred_as is not None:
        tgb = state.get("team_goal_bias", {})
        h_key = str(home_id)
        a_key = str(away_id)
        bias_lr = 0.12

        old_h_bias = tgb.get(h_key, 0.0)
        h_goal_error = pred_hs - actual_hs
        tgb[h_key] = round(old_h_bias * (1 - bias_lr) + h_goal_error * bias_lr, 3)

        old_a_bias = tgb.get(a_key, 0.0)
        a_goal_error = pred_as - actual_as
        tgb[a_key] = round(old_a_bias * (1 - bias_lr) + a_goal_error * bias_lr, 3)

        state["team_goal_bias"] = tgb

    # 3. Team result bias (did we predict the right winner?)
    trb = state.get("team_result_bias", {})
    h_key = str(home_id)
    a_key = str(away_id)
    bias_lr = 0.1

    if actual_winner == "home":
        old_h = trb.get(h_key, 0.0)
        trb[h_key] = round(old_h * (1 - bias_lr) + 0.1 * bias_lr, 4)
        old_a = trb.get(a_key, 0.0)
        trb[a_key] = round(old_a * (1 - bias_lr) + (-0.1) * bias_lr, 4)
    elif actual_winner == "away":
        old_h = trb.get(h_key, 0.0)
        trb[h_key] = round(old_h * (1 - bias_lr) + (-0.1) * bias_lr, 4)
        old_a = trb.get(a_key, 0.0)
        trb[a_key] = round(old_a * (1 - bias_lr) + 0.1 * bias_lr, 4)
    state["team_result_bias"] = trb

    # 4. Poisson blend calibration
    try:
        pred = predict_match(home_id, away_id, gw)
        pred_winner_from_pct = "home" if pred["home_win_pct"] > pred["away_win_pct"] and pred["home_win_pct"] > pred["draw_pct"] else \
                               "away" if pred["away_win_pct"] > pred["home_win_pct"] and pred["away_win_pct"] > pred["draw_pct"] else "draw"
        poisson_winner = "home" if pred["poisson_score"][0] > pred["poisson_score"][1] else \
                         "away" if pred["poisson_score"][1] > pred["poisson_score"][0] else "draw"

        overall_correct = pred_winner_from_pct == actual_winner
        poisson_correct = poisson_winner == actual_winner

        pb = state.get("poisson_blend", 0.6)
        if poisson_correct and not overall_correct:
            state["poisson_blend"] = round(min(pb + 0.01, 0.80), 3)
            state["factor_blend"] = round(1 - state["poisson_blend"], 3)
        elif overall_correct and not poisson_correct:
            state["poisson_blend"] = round(max(pb - 0.01, 0.35), 3)
            state["factor_blend"] = round(1 - state["poisson_blend"], 3)
    except Exception:
        pass

    # 5. Confidence calibration
    try:
        features = _extract_match_features(home_id, away_id, gw)
        pred = predict_match(home_id, away_id, gw)
        max_pct = max(pred["home_win_pct"], pred["draw_pct"], pred["away_win_pct"])
        pred_winner = "home" if pred["home_win_pct"] >= pred["away_win_pct"] and pred["home_win_pct"] >= pred["draw_pct"] else \
                      "away" if pred["away_win_pct"] >= pred["home_win_pct"] else "draw"
        was_correct = pred_winner == actual_winner

        conf_bin = str(int(max_pct // 5) * 5)
        cal = state.get("confidence_calibration", {})
        if conf_bin not in cal:
            cal[conf_bin] = {"total": 0, "correct": 0}
        cal[conf_bin]["total"] += 1
        if was_correct:
            cal[conf_bin]["correct"] += 1
        state["confidence_calibration"] = cal

        cf = state["confidence_features"]
        adapt_lr = 0.05

        pos_gap_signal = features["position_gap"] / 20.0
        if was_correct and pos_gap_signal > 0.3:
            cf["position_gap_weight"] = round(min(cf["position_gap_weight"] + adapt_lr * 0.3, 0.30), 4)
        elif not was_correct and pos_gap_signal > 0.3:
            cf["position_gap_weight"] = round(max(cf["position_gap_weight"] - adapt_lr * 0.1, 0.05), 4)

        form_signal = features["form_diff"] / 100.0
        if was_correct and form_signal > 0.2:
            cf["form_diff_weight"] = round(min(cf["form_diff_weight"] + adapt_lr * 0.2, 0.35), 4)
        elif not was_correct and form_signal > 0.2:
            cf["form_diff_weight"] = round(max(cf["form_diff_weight"] - adapt_lr * 0.1, 0.05), 4)

        if was_correct and features.get("avg_draw_tendency", 0) < 20:
            cf["draw_penalty_weight"] = round(min(cf["draw_penalty_weight"] + adapt_lr * 0.1, 0.20), 4)

        state["confidence_features"] = cf

        history["matches"].append({
            "match_id": match_id, "gw": gw,
            "home_id": home_id, "away_id": away_id,
            "actual_hs": actual_hs, "actual_as": actual_as,
            "actual_winner": actual_winner,
            "pred_winner": pred_winner,
            "pred_hs": pred_hs, "pred_as": pred_as,
            "max_pct": max_pct,
            "was_correct": was_correct,
            "features": features,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception:
        pass

    state["matches_learned"] = state.get("matches_learned", 0) + 1

    learned_ids = state.get("last_learned_match_ids", [])
    learned_ids.append(match_id)
    state["last_learned_match_ids"] = learned_ids[-500:]

    _save_ml_state(state)
    _save_ml_history(history)

    print(f"[ML] Learned from match {match_id} (GW{gw}): {actual_hs}-{actual_as} | "
          f"Total learned: {state['matches_learned']} | Poisson blend: {state.get('poisson_blend', 0.6)}")


def check_and_learn():
    """Check for newly finished matches and learn from them."""
    state = _load_ml_state()
    learned_ids = set(state.get("last_learned_match_ids", []))

    try:
        all_fix = get_all_fixtures()
    except Exception:
        return 0

    ai_acc = _load_accuracy()
    pred_map = {}
    for p in ai_acc.get("predictions", []):
        pred_map[p.get("match_id")] = p

    count = 0
    for f in all_fix:
        if not f.get("finished") or f.get("team_h_score") is None:
            continue
        mid = f["id"]
        if mid in learned_ids:
            continue

        pred_hs = None
        pred_as = None
        if mid in pred_map:
            pred_hs = pred_map[mid].get("pred_hs")
            pred_as = pred_map[mid].get("pred_as")

        try:
            learn_from_match(
                match_id=mid,
                home_id=f["team_h"],
                away_id=f["team_a"],
                actual_hs=f["team_h_score"],
                actual_as=f["team_a_score"],
                gw=f.get("event", 0),
                pred_hs=pred_hs,
                pred_as=pred_as,
            )
            count += 1
        except Exception as e:
            print(f"[ML] Error learning from match {mid}: {e}")

    if count > 0:
        print(f"[ML] Learned from {count} new matches")
        try:
            track_prediction_accuracy()
        except Exception:
            pass
        try:
            refreshed = refresh_future_predictions()
            if refreshed:
                for r in refreshed:
                    print(f"[ML] Refreshed GW{r['gw']} predictions: {r['changes']} changes")
        except Exception as e:
            print(f"[ML] Failed to refresh predictions: {e}")

    return count


def learned_confidence_score(match_features, prediction):
    """Calculate confidence using the learned model (replaces naive formula)."""
    state = _load_ml_state()
    cf = state.get("confidence_features", _default_ml_state()["confidence_features"])

    max_pct = max(prediction["home_win_pct"], prediction["draw_pct"], prediction["away_win_pct"])

    position_gap_norm = min(match_features.get("position_gap", 0) / 19.0, 1.0)
    form_diff_norm = min(match_features.get("form_diff", 0) / 100.0, 1.0)
    streak_diff_norm = min(match_features.get("streak_diff", 0) / 100.0, 1.0)
    h2h_clarity = min(match_features.get("h2h_clarity", 0), 1.0)
    draw_penalty = min(match_features.get("avg_draw_tendency", 0) / 50.0, 1.0)
    home_fav_bonus = 1.0 if match_features.get("home_is_favorite", False) else 0.0

    score = (
        max_pct * cf.get("max_pct_weight", 0.30) +
        position_gap_norm * 100 * cf.get("position_gap_weight", 0.15) +
        form_diff_norm * 100 * cf.get("form_diff_weight", 0.20) +
        streak_diff_norm * 80 * cf.get("streak_diff_weight", 0.07) +
        h2h_clarity * 60 * cf.get("h2h_clarity_weight", 0.08) +
        home_fav_bonus * 15 * cf.get("home_fav_bonus", 0.10) -
        draw_penalty * 30 * cf.get("draw_penalty_weight", 0.10)
    )

    cal = state.get("confidence_calibration", {})
    conf_bin = str(int(max_pct // 5) * 5)
    if conf_bin in cal and cal[conf_bin].get("total", 0) >= 5:
        actual_accuracy = cal[conf_bin]["correct"] / cal[conf_bin]["total"]
        score = score * 0.7 + actual_accuracy * 100 * 0.3

    return round(max(score, 1.0), 1)


def get_ml_status():
    """Return current ML learning status for monitoring."""
    state = _load_ml_state()
    history = _load_ml_history()

    recent = history.get("matches", [])[-50:]
    recent_correct = sum(1 for m in recent if m.get("was_correct")) if recent else 0
    recent_total = len(recent)
    recent_accuracy = round(recent_correct / max(recent_total, 1) * 100, 1)

    last10 = history.get("matches", [])[-10:]
    last10_correct = sum(1 for m in last10 if m.get("was_correct"))

    cal = state.get("confidence_calibration", {})
    calibration_summary = {}
    for band, data in sorted(cal.items(), key=lambda x: int(x[0])):
        if data.get("total", 0) > 0:
            calibration_summary[f"{band}-{int(band)+5}%"] = {
                "total": data["total"],
                "correct": data["correct"],
                "accuracy": round(data["correct"] / data["total"] * 100, 1),
            }

    return {
        "matches_learned": state.get("matches_learned", 0),
        "poisson_blend": state.get("poisson_blend", 0.6),
        "factor_blend": state.get("factor_blend", 0.4),
        "learning_rate": state.get("learning_rate", 0.08),
        "recent_accuracy_50": recent_accuracy,
        "last_10_accuracy": f"{last10_correct}/{len(last10)}",
        "factor_accuracy": state.get("factor_accuracy", {}),
        "confidence_features": state.get("confidence_features", {}),
        "confidence_calibration": calibration_summary,
        "top_biased_teams": dict(sorted(
            state.get("team_goal_bias", {}).items(),
            key=lambda x: abs(x[1]), reverse=True
        )[:10]),
    }

def _load_accuracy():
    if os.path.exists(AI_ACCURACY_FILE):
        try:
            with open(AI_ACCURACY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"predictions": [], "stats": {"total": 0, "winner_correct": 0, "score_correct": 0}}


def _save_accuracy(data):
    with open(AI_ACCURACY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def track_prediction_accuracy():
    """Compare past predictions with actual results."""
    acc = _load_accuracy()
    all_fix = get_all_fixtures()
    results = {}
    for f in all_fix:
        if f.get("finished") and f.get("team_h_score") is not None:
            results[f["id"]] = {
                "hs": f["team_h_score"], "as": f["team_a_score"],
                "winner": "home" if f["team_h_score"] > f["team_a_score"] else "draw" if f["team_h_score"] == f["team_a_score"] else "away"
            }

    changed = False
    for pred in acc["predictions"]:
        if pred.get("checked"):
            continue
        mid = pred.get("match_id")
        if mid in results:
            r = results[mid]
            pred["actual_winner"] = r["winner"]
            pred["actual_hs"] = r["hs"]
            pred["actual_as"] = r["as"]
            pred["winner_correct"] = pred.get("pred_winner") == r["winner"]
            pred["score_correct"] = pred.get("pred_hs") == r["hs"] and pred.get("pred_as") == r["as"]
            pred["checked"] = True
            changed = True

    if changed:
        checked = [p for p in acc["predictions"] if p.get("checked")]
        acc["stats"]["total"] = len(checked)
        acc["stats"]["winner_correct"] = sum(1 for p in checked if p.get("winner_correct"))
        acc["stats"]["score_correct"] = sum(1 for p in checked if p.get("score_correct"))
        if checked:
            acc["stats"]["winner_pct"] = round(acc["stats"]["winner_correct"] / len(checked) * 100, 1)
            acc["stats"]["score_pct"] = round(acc["stats"]["score_correct"] / len(checked) * 100, 1)
        _save_accuracy(acc)
    return acc["stats"]


WEIGHTS_FILE = os.path.join(DATA_DIR, "ai_weights.json")

def _load_weights_pl():
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"form": 0.15, "strength": 0.15, "position": 0.12, "home_adv": 0.08,
            "streak": 0.12, "h2h": 0.08, "home_away_split": 0.08, "goals_trend": 0.06,
            "upset": 0.06, "clean_sheet": 0.05, "draw_tendency": 0.05}

def _save_weights(w):
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(w, f, indent=2)


def calibrate_weights():
    """Learn from past gameweeks: adjust weights based on which factors predicted best."""
    all_fix = get_all_fixtures()
    team_map = build_team_map()
    finished_gws = set()
    for f in all_fix:
        if f.get("finished") and f.get("team_h_score") is not None and f.get("event"):
            finished_gws.add(f["event"])

    if len(finished_gws) < 5:
        return _load_weights_pl()

    w = _load_weights_pl()
    factor_names = list(w.keys())
    factor_correct = {k: 0 for k in factor_names}
    factor_total = {k: 0 for k in factor_names}

    test_gws = sorted(finished_gws)[-10:]

    for gw in test_gws:
        gw_matches = [f for f in all_fix if f.get("event") == gw and f.get("finished")
                       and f.get("team_h_score") is not None]
        standings = build_standings(up_to_gw=gw - 1)
        pos_map = {r["team"]["id"]: r["position"] for r in standings}

        for f in gw_matches:
            hid, aid = f["team_h"], f["team_a"]
            hs, as_ = f["team_h_score"], f["team_a_score"]
            actual = "home" if hs > as_ else "draw" if hs == as_ else "away"

            h_all = _get_all_team_matches(hid, gw)
            a_all = _get_all_team_matches(aid, gw)
            ht = team_map.get(hid, {})
            at = team_map.get(aid, {})

            factors = {}
            h_stats = compute_team_stats(hid, 5, gw)
            a_stats = compute_team_stats(aid, 5, gw)
            factors["form"] = "home" if h_stats["form_score"] > a_stats["form_score"] else "away" if a_stats["form_score"] > h_stats["form_score"] else "draw"

            h_str = (ht.get("strength_attack_home", 1000) + ht.get("strength_defence_home", 1000)) / 2
            a_str = (at.get("strength_attack_away", 1000) + at.get("strength_defence_away", 1000)) / 2
            factors["strength"] = "home" if h_str > a_str else "away" if a_str > h_str else "draw"

            factors["position"] = "home" if pos_map.get(hid, 10) < pos_map.get(aid, 10) else "away" if pos_map.get(aid, 10) < pos_map.get(hid, 10) else "draw"
            factors["home_adv"] = "home"
            factors["streak"] = "home" if _streak_score(h_all) > _streak_score(a_all) else "away"

            h2h = _head_to_head(hid, aid)
            factors["h2h"] = "home" if h2h["home_wins"] > h2h["away_wins"] else "away" if h2h["away_wins"] > h2h["home_wins"] else "draw"

            h_split = _home_away_split(h_all, True)
            a_split = _home_away_split(a_all, False)
            factors["home_away_split"] = "home" if h_split["win_rate"] > a_split["win_rate"] else "away"

            h_recent_gf = sum(m["gf"] for m in h_all[-3:]) / max(len(h_all[-3:]), 1) if h_all else 1
            a_recent_gf = sum(m["gf"] for m in a_all[-3:]) / max(len(a_all[-3:]), 1) if a_all else 1
            factors["goals_trend"] = "home" if h_recent_gf > a_recent_gf else "away"

            h_upset = _upset_potential(h_all, aid, pos_map)
            a_upset = _upset_potential(a_all, hid, pos_map)
            factors["upset"] = "home" if h_upset > a_upset else "away" if a_upset > h_upset else "draw"

            h_cs = _clean_sheet_rate(h_all[-5:])
            a_cs = _clean_sheet_rate(a_all[-5:])
            factors["clean_sheet"] = "home" if h_cs > a_cs else "away" if a_cs > h_cs else "draw"

            h_draw_tend = _draw_tendency(h_all)
            a_draw_tend = _draw_tendency(a_all)
            avg_draw = (h_draw_tend + a_draw_tend) / 2
            factors["draw_tendency"] = "draw" if avg_draw > 30 else ("home" if h_stats["form_score"] > a_stats["form_score"] else "away")

            for k in factor_names:
                factor_total[k] += 1
                if factors.get(k) == actual:
                    factor_correct[k] += 1

    new_w = {}
    total_acc = sum(factor_correct[k] / max(factor_total[k], 1) for k in factor_names)
    for k in factor_names:
        acc = factor_correct[k] / max(factor_total[k], 1)
        new_w[k] = round(acc / max(total_acc, 0.01), 4)

    total_w = sum(new_w.values())
    for k in new_w:
        new_w[k] = round(new_w[k] / max(total_w, 0.01), 4)

    _save_weights(new_w)
    return new_w


def predict_match(home_id, away_id, current_gw):
    """Advanced AI prediction with learning from past gameweeks."""
    team_map = build_team_map()
    standings = build_standings(up_to_gw=current_gw - 1 if current_gw > 1 else None)
    pos_map = {r["team"]["id"]: r["position"] for r in standings}

    home_team = team_map.get(home_id, {})
    away_team = team_map.get(away_id, {})
    home_stats = compute_team_stats(home_id, 5, current_gw)
    away_stats = compute_team_stats(away_id, 5, current_gw)
    h_all = _get_all_team_matches(home_id, current_gw)
    a_all = _get_all_team_matches(away_id, current_gw)

    w = _load_weights_pl()

    # Factor 1: Form (last 5)
    home_form = home_stats["form_score"]
    away_form = away_stats["form_score"]

    # Factor 2: Team strength (FPL ratings)
    h_attack = home_team.get("strength_attack_home", 1000)
    h_defence = home_team.get("strength_defence_home", 1000)
    a_attack = away_team.get("strength_attack_away", 1000)
    a_defence = away_team.get("strength_defence_away", 1000)
    h_str = (h_attack / max(a_defence, 1)) * 50
    a_str = (a_attack / max(h_defence, 1)) * 50

    # Factor 3: League position
    home_pos = pos_map.get(home_id, 10)
    away_pos = pos_map.get(away_id, 10)
    h_pos_score = (21 - home_pos) / 20 * 100
    a_pos_score = (21 - away_pos) / 20 * 100

    # Factor 4: Home advantage
    HOME_BOOST = 18

    # Factor 5: Streak/momentum (weighted recent results)
    h_streak = _streak_score(h_all, 5)
    a_streak = _streak_score(a_all, 5)

    # Factor 6: Head-to-head
    h2h = _head_to_head(home_id, away_id)
    h2h_home = 55 if h2h["matches"] == 0 else (h2h["home_wins"] * 3 + h2h["draws"]) / max(h2h["matches"] * 3, 1) * 100
    h2h_away = 45 if h2h["matches"] == 0 else (h2h["away_wins"] * 3 + h2h["draws"]) / max(h2h["matches"] * 3, 1) * 100

    # Factor 7: Home/Away specific performance
    h_split = _home_away_split(h_all, True)
    a_split = _home_away_split(a_all, False)

    # Factor 8: Goals trend (last 3 matches scoring rate)
    h_trend_gf = sum(m["gf"] for m in h_all[-3:]) / max(len(h_all[-3:]), 1) if h_all else 1.2
    a_trend_gf = sum(m["gf"] for m in a_all[-3:]) / max(len(a_all[-3:]), 1) if a_all else 1.0
    h_trend_score = h_trend_gf * 30
    a_trend_score = a_trend_gf * 30

    # Factor 9: Upset potential (giant killers)
    h_upset = _upset_potential(h_all, away_id, pos_map)
    a_upset = _upset_potential(a_all, home_id, pos_map)

    # Factor 10: Clean sheet rate (defensive solidity)
    h_cs = _clean_sheet_rate(h_all[-5:])
    a_cs = _clean_sheet_rate(a_all[-5:])

    # Factor 11: Draw tendency
    h_draw_tend = _draw_tendency(h_all)
    a_draw_tend = _draw_tendency(a_all)

    # Combine all factors with learned weights
    home_score = (
        home_form * w.get("form", 0.15) +
        h_str * w.get("strength", 0.15) +
        h_pos_score * w.get("position", 0.12) +
        HOME_BOOST * w.get("home_adv", 0.08) +
        h_streak * w.get("streak", 0.12) +
        h2h_home * w.get("h2h", 0.08) +
        h_split["win_rate"] * w.get("home_away_split", 0.08) +
        h_trend_score * w.get("goals_trend", 0.06) +
        h_upset * w.get("upset", 0.06) +
        h_cs * w.get("clean_sheet", 0.05) +
        (100 - h_draw_tend) * w.get("draw_tendency", 0.05)
    )
    away_score = (
        away_form * w.get("form", 0.15) +
        a_str * w.get("strength", 0.15) +
        a_pos_score * w.get("position", 0.12) +
        0 +
        a_streak * w.get("streak", 0.12) +
        h2h_away * w.get("h2h", 0.08) +
        a_split["win_rate"] * w.get("home_away_split", 0.08) +
        a_trend_score * w.get("goals_trend", 0.06) +
        a_upset * w.get("upset", 0.06) +
        a_cs * w.get("clean_sheet", 0.05) +
        (100 - a_draw_tend) * w.get("draw_tendency", 0.05)
    )

    total = home_score + away_score if (home_score + away_score) > 0 else 1
    home_pct = home_score / total * 100
    away_pct = away_score / total * 100

    # Draw probability: base + closeness + draw tendency of both teams
    closeness = 1 - abs(home_pct - away_pct) / 100
    avg_draw_tend = (h_draw_tend + a_draw_tend) / 2
    draw_boost = max(0, (avg_draw_tend - 20) * 0.3)
    draw_pct = 10 + closeness * 18 + draw_boost

    raw_total = home_pct + draw_pct + away_pct
    home_pct = round(home_pct / raw_total * 100, 1)
    draw_pct = round(draw_pct / raw_total * 100, 1)
    away_pct = round(100 - home_pct - draw_pct, 1)

    # ── Enhanced Goals Prediction: xG + Poisson + Player Data ──

    h_matches = max(len(home_stats["matches"]), 1)
    a_matches = max(len(away_stats["matches"]), 1)
    avg_home_gf = home_stats["goals_for"] / h_matches
    avg_home_ga = home_stats["goals_against"] / h_matches
    avg_away_gf = away_stats["goals_for"] / a_matches
    avg_away_ga = away_stats["goals_against"] / a_matches

    # Player-level xG data
    h_xg = _team_xg_stats(home_id)
    a_xg = _team_xg_stats(away_id)

    # Opponent-adjusted goals
    h_adj_gf = _opponent_adjusted_goals(h_all, pos_map, True)
    a_adj_gf = _opponent_adjusted_goals(a_all, pos_map, False)

    # Lambda (expected goals) for Poisson: blend form + xG + opponent weakness
    lambda_home = (
        avg_home_gf * 0.20 +
        h_xg["xg_per_match"] * 0.25 +
        avg_away_ga * 0.20 +
        h_split["gf_avg"] * 0.15 +
        h_adj_gf * 0.10 +
        h_trend_gf * 0.10
    )
    lambda_away = (
        avg_away_gf * 0.20 +
        a_xg["xg_per_match"] * 0.25 +
        avg_home_ga * 0.20 +
        a_split["gf_avg"] * 0.15 +
        a_adj_gf * 0.10 +
        a_trend_gf * 0.10
    )

    # Home advantage boost
    lambda_home *= 1.08
    lambda_away *= 0.92

    # Key player injury penalty
    h_injury_pen = _key_player_impact(home_id)
    a_injury_pen = _key_player_impact(away_id)
    lambda_home *= (1 - h_injury_pen)
    lambda_away *= (1 - a_injury_pen)

    # Clean sheet / concede adjustments
    h_concede = _concede_rate(h_all, 5)
    a_concede = _concede_rate(a_all, 5)
    if a_cs > 40:
        lambda_home *= 0.88
    if h_cs > 40:
        lambda_away *= 0.88
    if a_concede > 2.0:
        lambda_home *= 1.08
    if h_concede > 2.0:
        lambda_away *= 1.08

    # Apply learned team goal bias correction
    _ml_state = _load_ml_state()
    h_bias = _ml_state.get("team_goal_bias", {}).get(str(home_id), 0.0)
    a_bias = _ml_state.get("team_goal_bias", {}).get(str(away_id), 0.0)
    lambda_home -= h_bias * 0.5
    lambda_away -= a_bias * 0.5

    # Apply team result bias (subtle shift toward historically under-predicted teams)
    h_rbias = _ml_state.get("team_result_bias", {}).get(str(home_id), 0.0)
    a_rbias = _ml_state.get("team_result_bias", {}).get(str(away_id), 0.0)
    lambda_home += h_rbias * 2.0
    lambda_away += a_rbias * 2.0

    lambda_home = max(lambda_home, 0.3)
    lambda_away = max(lambda_away, 0.3)

    # Poisson probabilities
    poisson = _poisson_match_probs(lambda_home, lambda_away)

    # Blend Poisson probabilities with factor-based probabilities (learned blend)
    ml_state = _load_ml_state()
    poisson_w = ml_state.get("poisson_blend", 0.6)
    factor_w = ml_state.get("factor_blend", 0.4)
    final_home_pct = round(home_pct * factor_w + poisson["home_win"] * poisson_w, 1)
    final_draw_pct = round(draw_pct * factor_w + poisson["draw"] * poisson_w, 1)
    final_away_pct = round(100 - final_home_pct - final_draw_pct, 1)

    pred_home_goals = round(lambda_home, 1)
    pred_away_goals = round(lambda_away, 1)
    best_h, best_a = poisson["best_score"]

    # Save prediction for accuracy tracking
    try:
        acc = _load_accuracy()
        all_fix = get_all_fixtures()
        match_id = None
        for f in all_fix:
            if f.get("event") == current_gw and f.get("team_h") == home_id and f.get("team_a") == away_id:
                match_id = f["id"]
                break
        if match_id and not any(p.get("match_id") == match_id for p in acc["predictions"]):
            recommended = "home" if final_home_pct > final_away_pct and final_home_pct > final_draw_pct else \
                         "away" if final_away_pct > final_home_pct and final_away_pct > final_draw_pct else "draw"
            acc["predictions"].append({
                "match_id": match_id, "gw": current_gw,
                "pred_winner": recommended, "pred_hs": best_h, "pred_as": best_a,
                "home_pct": final_home_pct, "draw_pct": final_draw_pct, "away_pct": final_away_pct,
            })
            acc["predictions"] = acc["predictions"][-500:]
            _save_accuracy(acc)
    except Exception:
        pass

    return {
        "home_win_pct": final_home_pct,
        "draw_pct": final_draw_pct,
        "away_win_pct": final_away_pct,
        "predicted_home_goals": pred_home_goals,
        "predicted_away_goals": pred_away_goals,
        "poisson_score": (best_h, best_a),
        "poisson_prob": poisson["score_prob"],
        "home_xg": h_xg, "away_xg": a_xg,
        "home_injury_pen": h_injury_pen, "away_injury_pen": a_injury_pen,
        "home_form": home_stats,
        "away_form": away_stats,
    }


# ─── ESPN / La Liga Data Layer ───

def _espn_season_dates():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}0801-{start_year + 1}0630"


ESPN_LIVE_CACHE_TTL = 60


@cached(lambda: "espn_laliga_events")
def espn_get_all_events():
    slug = ESPN_SLUGS["laliga"]
    url = ESPN_SCOREBOARD_URL.format(slug=slug)
    resp = requests.get(url, params={"dates": _espn_season_dates(), "limit": "1000"},
                        headers={"User-Agent": "PL-Dashboard/1.0"}, timeout=60)
    resp.raise_for_status()
    return resp.json().get("events", [])


def espn_get_all_events_live():
    key = "espn_laliga_events"
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ESPN_LIVE_CACHE_TTL:
        return _cache[key]["data"]
    slug = ESPN_SLUGS["laliga"]
    url = ESPN_SCOREBOARD_URL.format(slug=slug)
    resp = requests.get(url, params={"dates": _espn_season_dates(), "limit": "1000"},
                        headers={"User-Agent": "PL-Dashboard/1.0"}, timeout=60)
    resp.raise_for_status()
    events = resp.json().get("events", [])
    _cache[key] = {"data": events, "ts": time.time()}
    return events


@cached(lambda: "espn_laliga_standings")
def espn_get_standings_raw():
    slug = ESPN_SLUGS["laliga"]
    url = ESPN_STANDINGS_URL.format(slug=slug)
    resp = requests.get(url, headers={"User-Agent": "PL-Dashboard/1.0"}, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _espn_parse_competitor(comp_list):
    home = away = None
    for c in comp_list:
        if c.get("homeAway") == "home":
            home = c
        else:
            away = c
    return home, away


def espn_normalize_fixtures(events):
    """Convert ESPN events to FPL-like fixture list, sorted by date."""
    events_sorted = sorted(events, key=lambda e: e.get("date", ""))
    fixtures = []
    for ev in events_sorted:
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue
        home, away = _espn_parse_competitor(competitors)
        if not home or not away:
            continue
        status = comp.get("status", {}).get("type", {})
        state = status.get("state", "pre")
        started = state in ("in", "post")
        finished = status.get("completed", False)
        home_score = away_score = None
        if started:
            try:
                home_score = int(home.get("score", "0"))
            except (ValueError, TypeError):
                home_score = 0
            try:
                away_score = int(away.get("score", "0"))
            except (ValueError, TypeError):
                away_score = 0
        minutes = 0
        clock_str = comp.get("status", {}).get("displayClock", "")
        if clock_str and started and not finished:
            try:
                minutes = int("".join(c for c in clock_str.split("'")[0].replace("+", "") if c.isdigit()) or "0")
            except (ValueError, TypeError):
                pass
            if not minutes:
                minutes = 45 if comp.get("status", {}).get("period", 0) >= 2 else 0
        if finished:
            minutes = 90
        fixtures.append({
            "id": int(ev.get("id", 0)),
            "event": None,
            "team_h": int(home["team"]["id"]),
            "team_a": int(away["team"]["id"]),
            "team_h_score": home_score,
            "team_a_score": away_score,
            "finished": finished,
            "finished_provisional": False,
            "started": started,
            "kickoff_time": ev.get("date", ""),
            "minutes": minutes,
        })
    return fixtures


def espn_assign_matchdays(fixtures):
    """Assign matchday numbers by grouping every 10 consecutive fixtures."""
    for i, f in enumerate(fixtures):
        f["event"] = (i // 10) + 1
    return fixtures


def espn_get_all_fixtures():
    key = "espn_laliga_fixtures"
    now = time.time()
    events_key = "espn_laliga_events"
    events_ts = _cache.get(events_key, {}).get("ts", 0)
    cached = _cache.get(key)
    if cached and cached["ts"] >= events_ts:
        return cached["data"]
    events = espn_get_all_events()
    fixtures = espn_assign_matchdays(espn_normalize_fixtures(events))
    _cache[key] = {"data": fixtures, "ts": time.time()}
    return fixtures


def espn_get_all_fixtures_live():
    key = "espn_laliga_fixtures_live"
    now = time.time()
    events_key = "espn_laliga_events"
    events_ts = _cache.get(events_key, {}).get("ts", 0)
    cached = _cache.get(key)
    if cached and cached["ts"] >= events_ts:
        return cached["data"]
    events = espn_get_all_events_live()
    fixtures = espn_assign_matchdays(espn_normalize_fixtures(events))
    _cache[key] = {"data": fixtures, "ts": time.time()}
    return fixtures


def espn_build_team_map():
    key = "espn_laliga_team_map"
    if key in _cache and time.time() - _cache[key]["ts"] < CACHE_TTL:
        return _cache[key]["data"]
    try:
        standings = espn_get_standings_raw()
        children = standings.get("children", [])
        entries = children[0].get("standings", {}).get("entries", []) if children else []
    except Exception:
        entries = []
    teams = {}
    for entry in entries:
        team = entry.get("team", {})
        tid = int(team.get("id", 0))
        logos = team.get("logos", [])
        badge = logos[0]["href"] if logos else ""
        stats_map = {}
        for s in entry.get("stats", []):
            stats_map[s["name"]] = s.get("value", 0)
        rank = len(teams) + 1
        base_strength = 1400 - (rank - 1) * 30
        teams[tid] = {
            "id": tid,
            "name": team.get("displayName", team.get("name", "")),
            "short_name": team.get("abbreviation", "???"),
            "code": tid,
            "badge": badge,
            "strength": max(1, 6 - rank // 4),
            "strength_overall_home": base_strength + 30,
            "strength_overall_away": base_strength - 30,
            "strength_attack_home": base_strength + 40,
            "strength_attack_away": base_strength - 20,
            "strength_defence_home": base_strength + 20,
            "strength_defence_away": base_strength - 40,
        }

    # Fill from fixtures in case standings are incomplete
    events = espn_get_all_events()
    for ev in events:
        for comp in ev.get("competitions", []):
            for c in comp.get("competitors", []):
                t = c.get("team", {})
                tid = int(t.get("id", 0))
                if tid and tid not in teams:
                    logo = t.get("logo", "")
                    teams[tid] = {
                        "id": tid,
                        "name": t.get("displayName", t.get("name", "")),
                        "short_name": t.get("abbreviation", "???"),
                        "code": tid,
                        "badge": logo,
                        "strength": 3,
                        "strength_overall_home": 1100, "strength_overall_away": 1050,
                        "strength_attack_home": 1120, "strength_attack_away": 1060,
                        "strength_defence_home": 1080, "strength_defence_away": 1030,
                    }
    _cache[key] = {"data": teams, "ts": time.time()}
    return teams


def espn_get_current_matchday():
    from datetime import datetime, timezone
    fixtures = espn_get_all_fixtures()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    current_md = 1
    for f in fixtures:
        if f.get("finished"):
            current_md = f.get("event", current_md)
        elif f.get("started") and not f.get("finished"):
            return f.get("event", current_md)

    # Check if next matchday starts today
    next_md = current_md + 1
    for f in fixtures:
        if f.get("event") == next_md and f.get("kickoff_time", "").startswith(today):
            return next_md

    return current_md


def espn_get_gameweeks_info():
    fixtures = espn_get_all_fixtures()
    max_md = max((f.get("event", 0) for f in fixtures), default=38)
    current_md = espn_get_current_matchday()
    gws = []
    for md in range(1, max_md + 1):
        md_fixtures = [f for f in fixtures if f.get("event") == md]
        all_finished = all(f.get("finished") for f in md_fixtures) if md_fixtures else False
        first_ko = min((f.get("kickoff_time", "") for f in md_fixtures), default="")
        gws.append({
            "id": md,
            "name": f"Matchday {md}",
            "finished": all_finished,
            "is_current": md == current_md,
            "is_next": md == current_md + 1,
            "deadline_time": first_ko,
        })
    return gws


# ─── League Dispatch ───

def _get_league(req=None):
    if req is None:
        req = request
    return req.args.get("league", "pl")


def league_get_team_map(league="pl"):
    if league == "laliga":
        return espn_build_team_map()
    return build_team_map()


def league_get_all_fixtures(league="pl", live=False):
    if league == "laliga":
        return espn_get_all_fixtures_live() if live else espn_get_all_fixtures()
    return get_all_fixtures_live() if live else get_all_fixtures()


def league_get_current_gw(league="pl"):
    if league == "laliga":
        return espn_get_current_matchday()
    return get_current_gameweek()


def league_get_gameweeks(league="pl"):
    if league == "laliga":
        return espn_get_gameweeks_info()
    return get_gameweeks_info()


def league_fixtures_for_gw(gw, league="pl", live=False):
    if league == "laliga":
        from datetime import datetime, timezone
        all_fix = league_get_all_fixtures(league, live)
        team_map = league_get_team_map(league)
        matches = []
        now = datetime.now(timezone.utc)
        for f in all_fix:
            if f.get("event") != gw:
                continue
            home = team_map.get(f["team_h"], {})
            away = team_map.get(f["team_a"], {})
            started = f.get("started", False)
            finished = f.get("finished", False)
            minutes = f.get("minutes", 0)
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
                "kickoff_time": f.get("kickoff_time", ""),
                "home_id": f.get("team_h"),
                "away_id": f.get("team_a"),
            })
        return matches
    return fixtures_for_gameweek(gw, live)


def league_build_standings(league="pl", live=False, up_to_gw=None):
    if league == "laliga":
        all_fix = league_get_all_fixtures(league, live)
        team_map = league_get_team_map(league)
        table = {}
        for tid in team_map:
            table[tid] = {"team": team_map[tid], "played": 0, "won": 0, "drawn": 0,
                          "lost": 0, "gf": 0, "ga": 0, "gd": 0, "points": 0}
        for f in all_fix:
            if up_to_gw and f.get("event") and f["event"] > up_to_gw:
                continue
            has_score = f.get("team_h_score") is not None
            is_relevant = f.get("finished") or (live and f.get("started") and has_score)
            if not is_relevant or not has_score:
                continue
            h, a = f["team_h"], f["team_a"]
            hs, as_ = f["team_h_score"], f["team_a_score"]
            if h in table:
                table[h]["played"] += 1; table[h]["gf"] += hs; table[h]["ga"] += as_
                if hs > as_: table[h]["won"] += 1; table[h]["points"] += 3
                elif hs == as_: table[h]["drawn"] += 1; table[h]["points"] += 1
                else: table[h]["lost"] += 1
            if a in table:
                table[a]["played"] += 1; table[a]["gf"] += as_; table[a]["ga"] += hs
                if as_ > hs: table[a]["won"] += 1; table[a]["points"] += 3
                elif as_ == hs: table[a]["drawn"] += 1; table[a]["points"] += 1
                else: table[a]["lost"] += 1
        for tid in table:
            table[tid]["gd"] = table[tid]["gf"] - table[tid]["ga"]
        standings = sorted(table.values(), key=lambda x: (x["points"], x["gd"], x["gf"]), reverse=True)
        for i, row in enumerate(standings):
            row["position"] = i + 1
        return standings
    return build_standings(live, up_to_gw)


def league_team_last_n(team_id, league="pl", n=5, before_gw=None):
    all_fix = league_get_all_fixtures(league)
    team_map = league_get_team_map(league)
    relevant = []
    for f in all_fix:
        if not f.get("finished"):
            continue
        if before_gw and f.get("event", 99) >= before_gw:
            continue
        if f["team_h"] == team_id or f["team_a"] == team_id:
            is_home = f["team_h"] == team_id
            gf = f["team_h_score"] if is_home else f["team_a_score"]
            ga = f["team_a_score"] if is_home else f["team_h_score"]
            opp_id = f["team_a"] if is_home else f["team_h"]
            opp = team_map.get(opp_id, {})
            if gf is None or ga is None:
                continue
            result = "W" if gf > ga else "D" if gf == ga else "L"
            relevant.append({
                "gameweek": f.get("event"),
                "opponent": opp.get("name", "Unknown"),
                "opponent_short": opp.get("short_name", "???"),
                "opponent_badge": opp.get("badge", ""),
                "is_home": is_home, "goals_for": gf, "goals_against": ga, "result": result,
            })
    relevant.sort(key=lambda x: x["gameweek"], reverse=True)
    return relevant[:n]


def league_compute_stats(team_id, league="pl", n=5, before_gw=None):
    matches = league_team_last_n(team_id, league, n, before_gw)
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
    return {"matches": matches, "wins": wins, "draws": draws, "losses": losses,
            "goals_for": gf, "goals_against": ga, "goal_diff": gf - ga,
            "points": pts, "form_score": form_score}


def league_predict_match(home_id, away_id, gw, league="pl"):
    """Unified prediction: uses existing predict_match for PL, same logic for La Liga."""
    if league == "pl":
        return predict_match(home_id, away_id, gw)
    team_map = league_get_team_map(league)
    standings = league_build_standings(league, up_to_gw=gw - 1 if gw > 1 else None)
    pos_map = {r["team"]["id"]: r["position"] for r in standings}
    home_team = team_map.get(home_id, {})
    away_team = team_map.get(away_id, {})
    home_stats = league_compute_stats(home_id, league, 5, gw)
    away_stats = league_compute_stats(away_id, league, 5, gw)
    all_fix = league_get_all_fixtures(league)
    h_all = []; a_all = []
    for f in all_fix:
        if not f.get("finished") or f.get("team_h_score") is None:
            continue
        if gw and f.get("event", 99) >= gw:
            continue
        for tid, arr in [(home_id, h_all), (away_id, a_all)]:
            if f["team_h"] == tid or f["team_a"] == tid:
                is_h = f["team_h"] == tid
                gf = f["team_h_score"] if is_h else f["team_a_score"]
                ga = f["team_a_score"] if is_h else f["team_h_score"]
                arr.append({"gw": f.get("event"), "is_home": is_h, "gf": gf, "ga": ga,
                            "opp": f["team_a"] if is_h else f["team_h"],
                            "result": "W" if gf > ga else "D" if gf == ga else "L"})
    h_all.sort(key=lambda x: x["gw"]); a_all.sort(key=lambda x: x["gw"])
    w = _load_weights(league)
    home_form = home_stats["form_score"]; away_form = away_stats["form_score"]
    h_str = (home_team.get("strength_attack_home", 1000) / max(away_team.get("strength_defence_away", 1000), 1)) * 50
    a_str = (away_team.get("strength_attack_away", 1000) / max(home_team.get("strength_defence_home", 1000), 1)) * 50
    h_pos_score = (21 - pos_map.get(home_id, 10)) / 20 * 100
    a_pos_score = (21 - pos_map.get(away_id, 10)) / 20 * 100
    HOME_BOOST = 18
    h_streak = _streak_score(h_all, 5); a_streak = _streak_score(a_all, 5)
    h2h_data = {"home_wins": 0, "draws": 0, "away_wins": 0, "matches": 0}
    for f in all_fix:
        if not f.get("finished") or f.get("team_h_score") is None:
            continue
        if (f["team_h"] == home_id and f["team_a"] == away_id) or \
           (f["team_h"] == away_id and f["team_a"] == home_id):
            h2h_data["matches"] += 1
            hs, as_ = f["team_h_score"], f["team_a_score"]
            if f["team_h"] == home_id:
                if hs > as_: h2h_data["home_wins"] += 1
                elif hs == as_: h2h_data["draws"] += 1
                else: h2h_data["away_wins"] += 1
            else:
                if as_ > hs: h2h_data["home_wins"] += 1
                elif as_ == hs: h2h_data["draws"] += 1
                else: h2h_data["away_wins"] += 1
    h2h_home = 55 if h2h_data["matches"] == 0 else (h2h_data["home_wins"] * 3 + h2h_data["draws"]) / max(h2h_data["matches"] * 3, 1) * 100
    h2h_away = 45 if h2h_data["matches"] == 0 else (h2h_data["away_wins"] * 3 + h2h_data["draws"]) / max(h2h_data["matches"] * 3, 1) * 100
    h_split = _home_away_split(h_all, True); a_split = _home_away_split(a_all, False)
    h_trend_gf = sum(m["gf"] for m in h_all[-3:]) / max(len(h_all[-3:]), 1) if h_all else 1.2
    a_trend_gf = sum(m["gf"] for m in a_all[-3:]) / max(len(a_all[-3:]), 1) if a_all else 1.0
    h_draw_tend = _draw_tendency(h_all); a_draw_tend = _draw_tendency(a_all)
    h_cs = _clean_sheet_rate(h_all[-5:]); a_cs = _clean_sheet_rate(a_all[-5:])
    h_upset = _upset_potential(h_all, away_id, pos_map); a_upset = _upset_potential(a_all, home_id, pos_map)
    home_score = (
        home_form * w.get("form", 0.15) + h_str * w.get("strength", 0.15) +
        h_pos_score * w.get("position", 0.12) + HOME_BOOST * w.get("home_adv", 0.08) +
        h_streak * w.get("streak", 0.12) + h2h_home * w.get("h2h", 0.08) +
        h_split["win_rate"] * w.get("home_away_split", 0.08) +
        h_trend_gf * 30 * w.get("goals_trend", 0.06) +
        h_upset * w.get("upset", 0.06) + h_cs * w.get("clean_sheet", 0.05) +
        (100 - h_draw_tend) * w.get("draw_tendency", 0.05))
    away_score = (
        away_form * w.get("form", 0.15) + a_str * w.get("strength", 0.15) +
        a_pos_score * w.get("position", 0.12) + 0 +
        a_streak * w.get("streak", 0.12) + h2h_away * w.get("h2h", 0.08) +
        a_split["win_rate"] * w.get("home_away_split", 0.08) +
        a_trend_gf * 30 * w.get("goals_trend", 0.06) +
        a_upset * w.get("upset", 0.06) + a_cs * w.get("clean_sheet", 0.05) +
        (100 - a_draw_tend) * w.get("draw_tendency", 0.05))
    total = home_score + away_score if (home_score + away_score) > 0 else 1
    home_pct = home_score / total * 100; away_pct = away_score / total * 100
    closeness = 1 - abs(home_pct - away_pct) / 100
    avg_draw_tend = (h_draw_tend + a_draw_tend) / 2
    draw_boost = max(0, (avg_draw_tend - 20) * 0.3)
    draw_pct = 10 + closeness * 18 + draw_boost
    raw_total = home_pct + draw_pct + away_pct
    home_pct = round(home_pct / raw_total * 100, 1)
    draw_pct = round(draw_pct / raw_total * 100, 1)
    away_pct = round(100 - home_pct - draw_pct, 1)
    h_matches = max(len(home_stats["matches"]), 1); a_matches = max(len(away_stats["matches"]), 1)
    pred_hg = (home_stats["goals_for"] / h_matches * 0.4 + away_stats["goals_against"] / a_matches * 0.3 +
               h_split["gf_avg"] * 0.2 + h_trend_gf * 0.1)
    pred_ag = (away_stats["goals_for"] / a_matches * 0.4 + home_stats["goals_against"] / h_matches * 0.3 +
               a_split["gf_avg"] * 0.2 + a_trend_gf * 0.1)
    pred_hg = round(max(pred_hg, 0.2), 1); pred_ag = round(max(pred_ag, 0.2), 1)
    return {
        "home_win_pct": home_pct, "draw_pct": draw_pct, "away_win_pct": away_pct,
        "predicted_home_goals": pred_hg, "predicted_away_goals": pred_ag,
        "home_form": home_stats, "away_form": away_stats,
    }


# ─── League-aware Storage ───

def _league_file(base_path, league):
    if league == "pl":
        return base_path
    name, ext = os.path.splitext(base_path)
    return f"{name}_{league}{ext}"


def _load_weights(league="pl"):
    wf = _league_file(WEIGHTS_FILE, league)
    if os.path.exists(wf):
        try:
            with open(wf, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"form": 0.15, "strength": 0.15, "position": 0.12, "home_adv": 0.08,
            "streak": 0.12, "h2h": 0.08, "home_away_split": 0.08, "goals_trend": 0.06,
            "upset": 0.06, "clean_sheet": 0.05, "draw_tendency": 0.05}


def _save_weights_league(w, league="pl"):
    wf = _league_file(WEIGHTS_FILE, league)
    with open(wf, "w") as f:
        json.dump(w, f, indent=2)


def load_guesses_league(league="pl"):
    gf = _league_file(GUESSES_FILE, league)
    if os.path.exists(gf):
        with open(gf, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_guesses_league(data, league="pl"):
    gf = _league_file(GUESSES_FILE, league)
    with open(gf, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_ai_preds_league(league="pl"):
    af = _league_file(AI_PREDS_FILE, league)
    if os.path.exists(af):
        with open(af, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ai_preds_league(data, league="pl"):
    af = _league_file(AI_PREDS_FILE, league)
    with open(af, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── AI Prediction Storage ───

def load_ai_preds():
    if os.path.exists(AI_PREDS_FILE):
        with open(AI_PREDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_ai_preds(data):
    with open(AI_PREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def store_ai_predictions_for_gw(gw, force_refresh=False):
    """Generate and store AI predictions for a gameweek. Only before matches start.
    If force_refresh=True, regenerate even if predictions exist (for post-learning updates)."""
    ai_data = load_ai_preds()
    key = str(gw)

    if key in ai_data and not force_refresh:
        return ai_data[key]

    matches = fixtures_for_gameweek(gw)

    # Block if ANY match has already started (can't change predictions mid-game)
    if any(m.get("started") or m.get("finished") for m in matches):
        if force_refresh:
            return ai_data.get(key, {"predictions": [], "locked": True, "reason": "Matches already started"})
        return {"predictions": [], "locked": True, "reason": "Matches already started"}

    old_preds = ai_data.get(key, {}).get("predictions", [])
    old_map = {p["match_id"]: p for p in old_preds}

    preds = []
    changes = []
    for m in matches:
        pred = predict_match(m["home_id"], m["away_id"], gw)
        home_score = round(pred["predicted_home_goals"])
        away_score = round(pred["predicted_away_goals"])
        if home_score > away_score:
            winner = "home"
        elif home_score < away_score:
            winner = "away"
        else:
            winner = "draw"

        new_pred = {
            "match_id": m["id"],
            "winner": winner,
            "home_score": home_score,
            "away_score": away_score,
            "home_win_pct": pred["home_win_pct"],
            "draw_pct": pred["draw_pct"],
            "away_win_pct": pred["away_win_pct"],
        }
        preds.append(new_pred)

        old = old_map.get(m["id"])
        if old and (old.get("winner") != winner or old.get("home_score") != home_score or old.get("away_score") != away_score):
            changes.append({
                "match_id": m["id"],
                "home": m.get("home_short", "?"),
                "away": m.get("away_short", "?"),
                "old_winner": old.get("winner"),
                "new_winner": winner,
                "old_score": f"{old.get('home_score')}-{old.get('away_score')}",
                "new_score": f"{home_score}-{away_score}",
            })

    version = ai_data.get(key, {}).get("version", 0) + 1 if force_refresh else 1
    ai_data[key] = {
        "predictions": preds,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": version,
        "model_updated": force_refresh,
    }
    if changes:
        ai_data[key]["changes_from_learning"] = changes
        print(f"[ML] GW{gw} predictions updated: {len(changes)} changes after learning")
        for c in changes:
            print(f"  {c['home']} vs {c['away']}: {c['old_winner']}({c['old_score']}) → {c['new_winner']}({c['new_score']})")

    save_ai_preds(ai_data)
    return ai_data[key]


def refresh_future_predictions():
    """After ML learning, refresh predictions for all unstarted gameweeks."""
    try:
        current_gw = get_current_gameweek()
        refreshed = []

        for gw in [current_gw, current_gw + 1]:
            if gw > 38:
                continue
            matches = fixtures_for_gameweek(gw)
            if not matches:
                continue
            if any(m.get("started") or m.get("finished") for m in matches):
                continue
            result = store_ai_predictions_for_gw(gw, force_refresh=True)
            changes = result.get("changes_from_learning", [])
            if changes:
                refreshed.append({"gw": gw, "changes": len(changes)})

        return refreshed
    except Exception as e:
        print(f"[ML] Failed to refresh future predictions: {e}")
        return []


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


@app.route("/api/leagues")
def api_leagues():
    return jsonify({"leagues": LEAGUES})


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
        league = _get_league()
        team_map = league_get_team_map(league)
        teams = sorted(team_map.values(), key=lambda t: t["name"])
        return jsonify({"teams": teams, "league": league})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gameweeks")
def api_gameweeks():
    try:
        league = _get_league()
        gws = league_get_gameweeks(league)
        current = league_get_current_gw(league)
        info = LEAGUES.get(league, LEAGUES["pl"])
        return jsonify({"gameweeks": gws, "current_gameweek": current,
                        "league": league, "gw_label": info["gw_label"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fixtures/<int:gw>")
def api_fixtures(gw):
    try:
        league = _get_league()
        live = request.args.get("live", "0") == "1"
        matches = league_fixtures_for_gw(gw, league, live)
        has_live = any(m["is_live"] for m in matches)
        return jsonify({"gameweek": gw, "fixtures": matches, "has_live": has_live})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/standings")
def api_standings():
    try:
        league = _get_league()
        live = request.args.get("live", "0") == "1"
        gw = request.args.get("gw", None, type=int)
        standings = league_build_standings(league, live, gw)
        return jsonify({"standings": standings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/team/<int:team_id>/form")
def api_team_form(team_id):
    try:
        league = _get_league()
        n = min(max(request.args.get("n", 5, type=int), 1), 50)
        before_gw = request.args.get("before_gw", None, type=int)
        stats = league_compute_stats(team_id, league, n, before_gw)
        team_map = league_get_team_map(league)
        team = team_map.get(team_id, {})
        return jsonify({"team": team, "stats": stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare/<int:team1>/<int:team2>")
def api_compare(team1, team2):
    try:
        league = _get_league()
        n = min(max(request.args.get("n", 5, type=int), 1), 50)
        stats1 = league_compute_stats(team1, league, n)
        stats2 = league_compute_stats(team2, league, n)
        team_map = league_get_team_map(league)
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
        league = _get_league()
        live = request.args.get("live", "0") == "1"
        matches = league_fixtures_for_gw(gw, league, live)
        predictions = []
        for m in matches:
            pred = league_predict_match(m["home_id"], m["away_id"], gw, league)
            max_pct = max(pred["home_win_pct"], pred["draw_pct"], pred["away_win_pct"])
            form_diff = abs(pred["home_form"]["form_score"] - pred["away_form"]["form_score"])

            if league == "pl":
                try:
                    features = _extract_match_features(m["home_id"], m["away_id"], gw)
                    confidence = learned_confidence_score(features, pred)
                except Exception:
                    confidence = round(max_pct * 0.6 + form_diff * 0.4, 1)
            else:
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

        ml_info = {}
        if league == "pl":
            try:
                ml_s = _load_ml_state()
                ml_info = {
                    "model_version": ml_s.get("matches_learned", 0),
                    "poisson_blend": ml_s.get("poisson_blend", 0.6),
                    "last_update": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            except Exception:
                pass

        return jsonify({"gameweek": gw, "predictions": predictions, "ml": ml_info})
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
        actual_h = f["home_score"] or 0
        actual_a = f["away_score"] or 0

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


def score_guesses_for_gw_league(gw, league="pl"):
    if league == "pl":
        return score_guesses_for_gw(gw)
    guesses_data = load_guesses_league(league)
    key = str(gw)
    if key not in guesses_data:
        return None
    gw_guesses = guesses_data[key]
    fixtures = league_fixtures_for_gw(gw, league)
    fix_map = {f["id"]: f for f in fixtures}
    total = correct_winner = correct_score = 0
    results = []
    for g in gw_guesses.get("guesses", []):
        mid = g["match_id"]
        f = fix_map.get(mid)
        if not f or not f["finished"]:
            results.append({**g, "scored": False})
            continue
        total += 1
        ah, aa = f["home_score"], f["away_score"]
        actual_winner = "home" if ah > aa else ("draw" if ah == aa else "away")
        winner_ok = g.get("winner") == actual_winner
        score_ok = (g.get("home_score") == ah and g.get("away_score") == aa)
        if winner_ok: correct_winner += 1
        if score_ok: correct_score += 1
        results.append({**g, "scored": True, "actual_home_score": ah, "actual_away_score": aa,
                        "actual_winner": actual_winner, "winner_correct": winner_ok, "score_correct": score_ok})
    return {"gameweek": gw, "total_matches": total, "correct_winner": correct_winner,
            "correct_score": correct_score, "points": correct_winner * 3 + correct_score * 5, "results": results}


@app.route("/api/guesses/<int:gw>", methods=["GET"])
def api_get_guesses(gw):
    try:
        league = _get_league()
        guesses_data = load_guesses_league(league)
        key = str(gw)
        gw_data = guesses_data.get(key, {})
        return jsonify({"gameweek": gw, "data": gw_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/guesses/<int:gw>", methods=["POST"])
def api_save_guesses(gw):
    try:
        league = _get_league()
        body = request.get_json()
        guesses_data = load_guesses_league(league)
        key = str(gw)
        guesses_data[key] = {
            "guesses": body.get("guesses", []),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_guesses_league(guesses_data, league)
        _trigger_background_push()
        return jsonify({"ok": True, "gameweek": gw})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


_last_push = 0

def _trigger_background_push():
    """Push update to GitHub Pages in background (max once per 30s)."""
    import subprocess, sys, threading
    global _last_push
    now = time.time()
    if now - _last_push < 30:
        return
    _last_push = now
    def run():
        try:
            script = os.path.join(os.path.dirname(__file__), "website", "update_pl_mobile.py")
            if os.path.exists(script):
                subprocess.run([sys.executable, script], cwd=os.path.dirname(__file__),
                               timeout=120, capture_output=True)
        except Exception as e:
            logger.error("[Background push] Failed: %s", e)
    threading.Thread(target=run, daemon=True).start()


@app.route("/api/import-phone-guesses", methods=["POST"])
def api_import_phone_guesses():
    try:
        league = _get_league()
        body = request.get_json()
        phone_data = body.get("guesses", {})
        guesses_data = load_guesses_league(league)
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
        save_guesses_league(guesses_data, league)
        return jsonify({"ok": True, "imported": imported})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/guesses/<int:gw>/score")
def api_score_guesses(gw):
    try:
        league = _get_league()
        result = score_guesses_for_gw_league(gw, league)
        if result is None:
            return jsonify({"gameweek": gw, "has_guesses": False})
        return jsonify({"gameweek": gw, "has_guesses": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_top5_best_bets(gw, league="pl"):
    matches = league_fixtures_for_gw(gw, league)
    preds = []
    for m in matches:
        pred = league_predict_match(m["home_id"], m["away_id"], gw, league)
        max_pct = max(pred["home_win_pct"], pred["draw_pct"], pred["away_win_pct"])
        form_diff = abs(pred["home_form"]["form_score"] - pred["away_form"]["form_score"])

        if league == "pl":
            try:
                features = _extract_match_features(m["home_id"], m["away_id"], gw)
                confidence = learned_confidence_score(features, pred)
            except Exception:
                confidence = round(max_pct * 0.6 + form_diff * 0.4, 1)
        else:
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
    try:
        league = _get_league()
        top5 = _get_top5_best_bets(gw, league)
        if not top5:
            return jsonify({"gameweek": gw, "best_bets": [], "summary": {"total": 0, "winner_ok": 0, "score_ok": 0, "ai_winner_ok": 0}})

        fixtures = league_fixtures_for_gw(gw, league)
        fix_map = {f["id"]: f for f in fixtures}
        guesses_data = load_guesses_league(league)
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
        league = _get_league()
        guesses_data = load_guesses_league(league)
        history = []
        for gw_key in sorted(guesses_data.keys(), key=lambda x: int(x)):
            gw = int(gw_key)
            score = score_guesses_for_gw_league(gw, league)
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
    try:
        league = _get_league()
        matches = league_fixtures_for_gw(gw, league)
        advice = []
        for m in matches:
            pred = league_predict_match(m["home_id"], m["away_id"], gw, league)
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
        if league == "pl":
            store_ai_predictions_for_gw(gw)
        return jsonify({"gameweek": gw, "advice": advice})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live-status")
def api_live_status():
    try:
        from datetime import datetime, timezone
        league = _get_league()
        all_fix = league_get_all_fixtures(league, live=True)
        team_map = league_get_team_map(league)
        now = datetime.now(timezone.utc)
        live = []
        has_live = False
        for f in all_fix:
            if not (f.get("started") and not f.get("finished") and not f.get("finished_provisional")):
                continue
            # Apply elapsed-time heuristic: >115 min since kickoff → treat as finished
            ko = f.get("kickoff_time", "")
            if ko:
                try:
                    kick = datetime.fromisoformat(ko.replace("Z", "+00:00"))
                    if (now - kick).total_seconds() / 60 > 115:
                        continue
                except Exception:
                    pass
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
    from datetime import datetime, timezone
    try:
        league = _get_league()
        all_fix = league_get_all_fixtures(league, live=True)
        team_map = league_get_team_map(league)

        guesses_data = load_guesses_league(league)
        user_guesses = guesses_data.get(str(gw), {}).get("guesses", [])
        user_map = {g["match_id"]: g for g in user_guesses}

        ai_data = load_ai_preds_league(league)
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


@app.route("/api/push-update", methods=["POST"])
def api_push_update():
    """Manually trigger mobile + GitHub Pages update."""
    import subprocess, sys
    try:
        script = os.path.join(os.path.dirname(__file__), "website", "update_pl_mobile.py")
        result = subprocess.run([sys.executable, script], cwd=os.path.dirname(__file__),
                                capture_output=True, text=True, timeout=120)
        return jsonify({"ok": True, "output": result.stdout})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ai-accuracy")
def api_ai_accuracy():
    try:
        stats = track_prediction_accuracy()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ml-status")
def api_ml_status():
    """Monitor the ML learning model status and accuracy."""
    try:
        status = get_ml_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ml-learn", methods=["POST"])
def api_ml_learn():
    """Manually trigger ML learning from all finished matches."""
    try:
        count = check_and_learn()
        status = get_ml_status()
        return jsonify({
            "ok": True,
            "newly_learned": count,
            "total_learned": status.get("matches_learned", 0),
            "recent_accuracy": status.get("recent_accuracy_50", 0),
            "poisson_blend": status.get("poisson_blend", 0.6),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ml-reset", methods=["POST"])
def api_ml_reset():
    """Reset ML state to defaults (fresh start)."""
    try:
        _save_ml_state(_default_ml_state())
        _save_ml_history({"matches": []})
        return jsonify({"ok": True, "message": "ML state reset to defaults"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ml-changes/<int:gw>")
def api_ml_changes(gw):
    """Show what predictions changed for a gameweek after ML learning."""
    try:
        ai_data = load_ai_preds()
        key = str(gw)
        gw_data = ai_data.get(key, {})
        return jsonify({
            "gameweek": gw,
            "version": gw_data.get("version", 1),
            "model_updated": gw_data.get("model_updated", False),
            "created_at": gw_data.get("created_at", ""),
            "changes": gw_data.get("changes_from_learning", []),
            "predictions_count": len(gw_data.get("predictions", [])),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def auto_update_mobile():
    """Auto-update: ML learning + calibrate AI weights + track accuracy + push to GitHub Pages on startup."""
    import subprocess, sys, threading
    def run():
        try:
            print("[AI] Tracking prediction accuracy...")
            acc_stats = track_prediction_accuracy()
            if acc_stats.get("total", 0) > 0:
                print(f"[AI] Accuracy: {acc_stats.get('winner_pct', 0)}% winners ({acc_stats['winner_correct']}/{acc_stats['total']})")
        except Exception as e:
            print(f"[AI] Accuracy tracking failed: {e}")
        try:
            print("[AI] Calibrating prediction weights from past results...")
            new_w = calibrate_weights()
            print(f"[AI] Weights: {new_w}")
        except Exception as e:
            print(f"[AI] Calibration failed: {e}")
        try:
            ml_status = get_ml_status()
            print(f"[ML] Status: {ml_status.get('matches_learned', 0)} matches learned, "
                  f"Recent accuracy: {ml_status.get('recent_accuracy_50', 'N/A')}%, "
                  f"Poisson blend: {ml_status.get('poisson_blend', 0.6)}")
        except Exception as e:
            print(f"[ML] Status check failed: {e}")
        try:
            script = os.path.join(os.path.dirname(__file__), "website", "update_pl_mobile.py")
            if os.path.exists(script):
                print("[Auto-update] Updating mobile & GitHub Pages...")
                subprocess.run([sys.executable, script], cwd=os.path.dirname(__file__), timeout=120)
                print("[Auto-update] Done.")
        except Exception as e:
            print(f"[Auto-update] Failed: {e}")
    threading.Thread(target=run, daemon=True).start()


LIVE_PUSH_INTERVAL = 120  # 2 minutes during live


def _trigger_ci_workflow():
    """Trigger GitHub Actions workflow to rebuild the website."""
    try:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            return
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        resp = requests.post(
            "https://api.github.com/repos/moadi1987-eng/PL/actions/workflows/update-dashboard.yml/dispatches",
            headers=headers, json={"ref": "main"}, timeout=10
        )
        if resp.status_code in (204, 200):
            print(f"[Live-push] CI workflow triggered")
    except Exception as e:
        print(f"[Live-push] CI trigger failed: {e}")


ML_LEARN_INTERVAL = 90  # Check for finished matches every 90 seconds


def start_ml_learning_watcher():
    """Background thread: learns from newly finished matches every 90 seconds."""
    import threading

    def learner():
        time.sleep(30)
        print("[ML] Initial learning pass on startup...")
        try:
            count = check_and_learn()
            if count > 0:
                print(f"[ML] Startup: learned from {count} historical matches")
        except Exception as e:
            print(f"[ML] Startup learning failed: {e}")

        while True:
            time.sleep(ML_LEARN_INTERVAL)
            try:
                count = check_and_learn()
                if count > 0:
                    print(f"[ML] Background: learned from {count} newly finished matches at {time.strftime('%H:%M:%S')}")
            except Exception as e:
                print(f"[ML] Background learning error: {e}")

    threading.Thread(target=learner, daemon=True).start()
    print("[ML] Learning watcher started — checks every 90s for finished matches")


def start_live_push_watcher():
    """Background thread: pushes to GitHub Pages every 2 min when live matches are detected."""
    import subprocess, sys, threading

    def watcher():
        was_live = False
        push_count = 0
        while True:
            time.sleep(LIVE_PUSH_INTERVAL)
            try:
                _cache.pop("fixtures", None)
                is_live = check_live_matches()
            except Exception:
                is_live = False

            if is_live:
                was_live = True
                push_count += 1
                try:
                    script = os.path.join(os.path.dirname(__file__), "website", "update_pl_mobile.py")
                    if os.path.exists(script):
                        print(f"[Live-push] Matches in progress — pushing update #{push_count}...")
                        subprocess.run([sys.executable, script],
                                       cwd=os.path.dirname(__file__), timeout=120, capture_output=True)
                        print(f"[Live-push] Done at {time.strftime('%H:%M:%S')}")
                    if push_count % 3 == 1:
                        _trigger_ci_workflow()
                except Exception as e:
                    print(f"[Live-push] Failed: {e}")
            elif was_live:
                was_live = False
                push_count = 0
                print("[ML] Matches ended — triggering post-matchday learning...")
                try:
                    check_and_learn()
                except Exception as e:
                    print(f"[ML] Post-match learning failed: {e}")
                try:
                    script = os.path.join(os.path.dirname(__file__), "website", "update_pl_mobile.py")
                    if os.path.exists(script):
                        print(f"[Live-push] Matches ended — final push...")
                        subprocess.run([sys.executable, script],
                                       cwd=os.path.dirname(__file__), timeout=120, capture_output=True)
                        _trigger_ci_workflow()
                        print(f"[Live-push] Final push done at {time.strftime('%H:%M:%S')}")
                except Exception as e:
                    print(f"[Live-push] Final push failed: {e}")

    threading.Thread(target=watcher, daemon=True).start()
    print("[Live-push] Watcher started — will auto-push every 2 min during live matches")


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    auto_update_mobile()
    start_ml_learning_watcher()
    start_live_push_watcher()
    app.run(debug=debug, host="0.0.0.0", port=5000)
