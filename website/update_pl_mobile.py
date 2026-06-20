"""
Run this to update pl_mobile.html with fresh PL data.
Automatically uploads to GitHub Pages if GITHUB_TOKEN is set.

Usage:  python update_pl_mobile.py
"""
import json, requests, os, base64, re, unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass

FPL = "https://fantasy.premierleague.com/api"
BADGE = "https://resources.premierleague.com/premierleague/badges/70/t{}.png"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "").strip()
FOOTBALL_API = "https://v3.football.api-sports.io"
OUT = os.path.join(HERE, "pl_mobile.html")
TPL = os.path.join(HERE, "pl_mobile_template.html")

from datetime import datetime, timezone

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/esp.1/standings"
PL_OFFICIAL_COMPSEASON = "841"
PL_OFFICIAL_FIXTURES = "https://footballapi.pulselive.com/football/fixtures"
ESPN_WC_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
ESPN_WC_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/teams/{team_id}/roster"
FIFA_WC_MATCHES = "https://api.fifa.com/api/v3/calendar/matches"
FIFA_WC_COMPETITION = "17"
FIFA_WC_SEASON = "285023"
WC_DATE_RANGE = "20260611-20260719"
WC_AVAILABILITY_FILE = os.path.join(HERE, "wc_availability.json")
WC_TEAM_STRENGTH = {
    "ARG": 1410, "FRA": 1400, "ESP": 1390, "ENG": 1385, "BRA": 1380,
    "POR": 1365, "NED": 1355, "BEL": 1340, "GER": 1335, "CRO": 1320,
    "URU": 1315, "COL": 1305, "MAR": 1295, "USA": 1280, "SUI": 1275,
    "JPN": 1270, "MEX": 1265, "SEN": 1260, "ECU": 1255, "AUT": 1250,
    "NOR": 1240, "TUR": 1235, "SWE": 1230, "KOR": 1225, "CZE": 1220,
    "CIV": 1215, "PAR": 1210, "IRN": 1205, "CAN": 1195, "AUS": 1190,
    "SCO": 1185, "TUN": 1180, "ALG": 1175, "EGY": 1170, "GHA": 1165,
    "QAT": 1150, "KSA": 1145, "BIH": 1140, "UZB": 1135, "COD": 1130,
    "PAN": 1120, "RSA": 1110, "NZL": 1105, "IRQ": 1095, "CPV": 1090,
    "JOR": 1080, "HAI": 1065, "CUW": 1045,
}


def wc_seed_strength(abbr):
    return WC_TEAM_STRENGTH.get(str(abbr or "").upper(), 1120)


def _plain_name(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def wc_team_key(name):
    key = re.sub(r"[^a-z0-9]+", " ", _plain_name(name).lower()).strip()
    aliases = {
        "cabo verde": "cape verde",
        "cape verde": "cape verde",
        "ir iran": "iran",
        "iran": "iran",
        "korea republic": "south korea",
        "republic of korea": "south korea",
        "south korea": "south korea",
        "turkiye": "turkey",
        "turkey": "turkey",
        "cote d ivoire": "ivory coast",
        "cote divoire": "ivory coast",
        "ivory coast": "ivory coast",
        "curacao": "curacao",
        "czechia": "czechia",
        "czech republic": "czechia",
    }
    return aliases.get(key, key)


def _localized_description(value):
    if isinstance(value, list):
        if not value:
            return ""
        for item in value:
            if str(item.get("Locale", "")).lower().startswith("en"):
                return item.get("Description", "")
        return value[0].get("Description", "")
    return value or ""


def _score_int(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_key(value):
    if not value:
        return ""
    try:
        if "T" in value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        return datetime.strptime(value, "%m/%d/%Y %H:%M:%S").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    except Exception:
        return str(value)[:16]


def wc_match_key(home_name, away_name, kickoff):
    return f"{_date_key(kickoff)}|{wc_team_key(home_name)}|{wc_team_key(away_name)}"


def _fifa_iso(value):
    try:
        return datetime.strptime(value, "%m/%d/%Y %H:%M:%S").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return value or ""


def fetch_fifa_wc_matches():
    params = {
        "language": "en",
        "count": "120",
        "idCompetition": FIFA_WC_COMPETITION,
        "idSeason": FIFA_WC_SEASON,
    }
    resp = requests.get(FIFA_WC_MATCHES, params=params, headers=hdr, timeout=20)
    resp.raise_for_status()
    matches = []
    for item in resp.json().get("Results", []):
        home = item.get("Home", {}) or {}
        away = item.get("Away", {}) or {}
        home_name = _localized_description(home.get("TeamName"))
        away_name = _localized_description(away.get("TeamName"))
        kickoff = _fifa_iso(item.get("Date", ""))
        status = _score_int(item.get("MatchStatus"))
        home_score = _score_int(home.get("Score"))
        away_score = _score_int(away.get("Score"))
        finished = status == 0
        started = finished or status == 3 or home_score is not None or away_score is not None
        matches.append({
            "id": str(item.get("IdMatch", "")),
            "key": wc_match_key(home_name, away_name, kickoff),
            "hs": home_score,
            "as": away_score,
            "st": started,
            "fin": finished,
            "sx": {0: "STATUS_FULL_TIME", 1: "STATUS_SCHEDULED", 3: "STATUS_IN_PROGRESS"}.get(status, f"FIFA_{status}"),
        })
    return matches


def _athlete_stat(athlete, names):
    names = set(names)
    splits = (athlete.get("statistics", {}) or {}).get("splits", {}) or {}
    for cat in splits.get("categories", []) or []:
        for stat in cat.get("stats", []) or []:
            if stat.get("name") in names:
                try:
                    return float(stat.get("value") or 0)
                except (TypeError, ValueError):
                    return 0.0
    return 0.0


def _availability_status(athlete):
    injuries = athlete.get("injuries") or []
    status = athlete.get("status") or {}
    status_type = str(status.get("type") or status.get("name") or "").lower()
    if injuries:
        first = injuries[0] or {}
        label = (
            first.get("status")
            or first.get("type")
            or first.get("details")
            or first.get("description")
            or "injury"
        )
        return str(label), True
    if status_type and status_type not in ("active", "available"):
        return status.get("name") or status.get("type") or "unavailable", True
    return "active", False


def _player_availability_impact(athlete):
    pos = ((athlete.get("position") or {}).get("abbreviation") or "").upper()
    base = {"G": 7.0, "D": 5.0, "M": 6.0, "F": 7.0}.get(pos, 5.0)
    goals = _athlete_stat(athlete, ("totalGoals", "goals"))
    assists = _athlete_stat(athlete, ("goalAssists", "assists"))
    apps = _athlete_stat(athlete, ("appearances",))
    shots = _athlete_stat(athlete, ("shotsOnTarget", "totalShots"))
    saves = _athlete_stat(athlete, ("saves",))
    impact = base + goals * 2.5 + assists * 2 + min(apps * 1.2, 4) + min(shots * 0.25, 3)
    if pos == "G":
        impact += min(saves * 0.12, 3)
    return round(max(3.0, min(15.0, impact)), 1)


def _fetch_espn_wc_roster(team_id):
    url = ESPN_WC_ROSTER.format(team_id=team_id)
    resp = requests.get(url, headers=hdr, timeout=15)
    resp.raise_for_status()
    athletes = resp.json().get("athletes", []) or []
    counts = {"G": 0, "D": 0, "M": 0, "F": 0}
    unavailable = []
    for athlete in athletes:
        pos = ((athlete.get("position") or {}).get("abbreviation") or "").upper()
        if pos in counts:
            counts[pos] += 1
        status_label, is_unavailable = _availability_status(athlete)
        if is_unavailable:
            unavailable.append({
                "n": athlete.get("shortName") or athlete.get("displayName") or athlete.get("fullName") or "?",
                "pos": pos,
                "status": status_label,
                "xgi": _player_availability_impact(athlete),
                "src": "espn",
            })
    unavailable.sort(key=lambda p: p.get("xgi", 0), reverse=True)
    return {
        "sq": {
            "src": "ESPN roster",
            "n": len(athletes),
            "g": counts["G"],
            "d": counts["D"],
            "m": counts["M"],
            "f": counts["F"],
            "out": len(unavailable),
        },
        "inj": unavailable[:5],
    }


def _team_lookup(teams):
    out = {}
    for team in teams.values():
        out[str(team.get("id", ""))] = team
        if team.get("s"):
            out[str(team["s"]).upper()] = team
        if team.get("n"):
            out[wc_team_key(team["n"])] = team
    return out


def _load_wc_availability_overrides():
    if not os.path.exists(WC_AVAILABILITY_FILE):
        return {}
    try:
        with open(WC_AVAILABILITY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"World Cup availability overrides skipped: {e}")
        return {}


def apply_wc_availability(wc_teams):
    actual = {tid: t for tid, t in wc_teams.items() if not t.get("ph")}
    roster_ok = 0
    roster_issues = 0
    if actual:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_fetch_espn_wc_roster, tid): tid for tid in actual}
            for fut in as_completed(futs):
                tid = futs[fut]
                try:
                    info = fut.result()
                except Exception:
                    continue
                team = wc_teams.get(tid)
                if not team:
                    continue
                team["sq"] = info["sq"]
                if info["inj"]:
                    team["inj"] = info["inj"]
                    roster_issues += len(info["inj"])
                roster_ok += 1

    overrides = _load_wc_availability_overrides()
    manual_issues = 0
    lookup = _team_lookup(wc_teams)
    for key, cfg in (overrides.get("teams") or {}).items():
        team = lookup.get(str(key).upper()) or lookup.get(wc_team_key(key))
        if not team:
            continue
        sq = team.setdefault("sq", {"src": "manual", "n": 0, "g": 0, "d": 0, "m": 0, "f": 0, "out": 0})
        if sq.get("src") and "manual" not in sq["src"].lower():
            sq["src"] = sq["src"] + "+manual"
        for item in cfg.get("inj", []) or []:
            name = item.get("n") or item.get("name")
            if not name:
                continue
            impact = item.get("impact", item.get("xgi", 6))
            try:
                impact = float(impact)
            except (TypeError, ValueError):
                impact = 6.0
            entry = {
                "n": name,
                "pos": str(item.get("pos", "")).upper(),
                "status": item.get("status", "out"),
                "xgi": round(max(1.0, min(15.0, impact)), 1),
                "src": "manual",
            }
            if item.get("note"):
                entry["note"] = item["note"]
            existing = [str(p.get("n", "")).lower() for p in team.get("inj", [])]
            if entry["n"].lower() not in existing:
                team.setdefault("inj", []).append(entry)
                manual_issues += 1
        if team.get("inj"):
            team["inj"].sort(key=lambda p: p.get("xgi", 0), reverse=True)
            team["inj"] = team["inj"][:5]
            sq["out"] = len(team["inj"])
    return roster_ok, roster_issues, manual_issues


# World Cup persistent AI learning.
AI_WEIGHT_DEFAULTS = {
    "form": 0.15, "strength": 0.15, "position": 0.12, "home_adv": 0.08,
    "streak": 0.12, "h2h": 0.08, "home_away_split": 0.08,
    "goals_trend": 0.06, "upset": 0.06, "clean_sheet": 0.05, "draw_tendency": 0.05,
}
LEARNING_HISTORY_FILE = os.path.join(ROOT, "learning_history.json")
WC_PREDICTIONS_FILE = os.path.join(ROOT, "ai_predictions_wc.json")
WC_WEIGHTS_FILE = os.path.join(ROOT, "ai_weights_wc.json")
WC_PREDICTION_LOCK_HOURS = 36


def _load_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def _dt_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _wc_team(data, tid):
    return data.get(str(tid)) or data.get(tid) or {}


def _wc_winner(hs, as_score):
    if hs is None or as_score is None:
        return None
    if hs > as_score:
        return "home"
    if as_score > hs:
        return "away"
    return "draw"


def _wc_before(fixture, match):
    fdt = _dt_utc(fixture.get("ko"))
    mdt = _dt_utc(match.get("ko"))
    if fdt and mdt:
        return fdt < mdt
    return fixture.get("e", 999) < match.get("e", 999)


def _wc_rating(team):
    vals = [
        team.get("sah"), team.get("sdh"),
        team.get("saa"), team.get("sda"),
    ]
    vals = [float(v) for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else wc_seed_strength(team.get("s", ""))


def _wc_missing_penalty(team, side):
    penalty = 0.0
    for player in team.get("inj", []) or []:
        pos = str(player.get("pos", "")).upper()
        impact = float(player.get("xgi") or player.get("impact") or 0)
        if side == "attack":
            mult = 1.15 if pos == "F" else 0.75 if pos == "M" else 0.45
        else:
            mult = 1.25 if pos == "G" else 1.0 if pos == "D" else 0.45
        penalty += impact * mult
    squad = team.get("sq") or {}
    if squad.get("n") and squad.get("n") < 23:
        penalty += (23 - squad["n"]) * 1.2
    return min(penalty, 55.0)


def _wc_prior_stats(fixtures, tid, match):
    rows = []
    for f in fixtures:
        if not f.get("fin") or f.get("hs") is None or not _wc_before(f, match):
            continue
        if f.get("h") != tid and f.get("a") != tid:
            continue
        is_home = f.get("h") == tid
        gf = f.get("hs") if is_home else f.get("as")
        ga = f.get("as") if is_home else f.get("hs")
        rows.append({"gf": gf, "ga": ga, "res": "W" if gf > ga else "D" if gf == ga else "L"})
    played = len(rows)
    wins = sum(1 for r in rows if r["res"] == "W")
    draws = sum(1 for r in rows if r["res"] == "D")
    pts = wins * 3 + draws
    gf = sum(r["gf"] for r in rows)
    ga = sum(r["ga"] for r in rows)
    clean = sum(1 for r in rows if r["ga"] == 0)
    form = pts / max(played * 3, 1)
    streak_rows = rows[-3:]
    streak_pts = sum(3 if r["res"] == "W" else 1 if r["res"] == "D" else 0 for r in streak_rows)
    return {
        "played": played, "wins": wins, "draws": draws, "pts": pts,
        "gf": gf, "ga": ga, "gd": gf - ga,
        "gf_avg": gf / max(played, 1), "ga_avg": ga / max(played, 1),
        "clean_rate": clean / max(played, 1),
        "draw_rate": draws / max(played, 1),
        "form": form,
        "streak": streak_pts / max(len(streak_rows) * 3, 1),
    }


def _wc_group_rows(teams_obj, fixtures, group, match):
    rows = {}
    if not group:
        return rows
    for raw_id, team in teams_obj.items():
        if team.get("grp") == group and not team.get("ph"):
            tid = int(team.get("id") or raw_id)
            rows[tid] = {"pts": 0, "gf": 0, "ga": 0, "gd": 0, "played": 0}
    for f in fixtures:
        if f.get("grp") != group or not f.get("fin") or f.get("hs") is None or not _wc_before(f, match):
            continue
        h, a = f.get("h"), f.get("a")
        if h not in rows or a not in rows:
            continue
        hs, away_score = f.get("hs"), f.get("as")
        rows[h]["played"] += 1
        rows[a]["played"] += 1
        rows[h]["gf"] += hs
        rows[h]["ga"] += away_score
        rows[a]["gf"] += away_score
        rows[a]["ga"] += hs
        if hs > away_score:
            rows[h]["pts"] += 3
        elif hs < away_score:
            rows[a]["pts"] += 3
        else:
            rows[h]["pts"] += 1
            rows[a]["pts"] += 1
    ordered = sorted(rows.items(), key=lambda x: (-x[1]["pts"], -(x[1]["gf"] - x[1]["ga"]), -x[1]["gf"]))
    for pos, (tid, row) in enumerate(ordered, 1):
        row["gd"] = row["gf"] - row["ga"]
        row["pos"] = pos
    return rows


def _wc_signal(signals, name, h_val, a_val, gap, invert=False):
    diff = h_val - a_val
    if abs(diff) < gap:
        return
    if invert:
        signals[name] = "home" if diff < 0 else "away"
    else:
        signals[name] = "home" if diff > 0 else "away"


def _wc_phase_rule(match):
    if match.get("grp"):
        return {"key": "group", "result": 1, "exact": 3}
    e = int(match.get("e") or 0)
    if e >= 35:
        return {"key": "final", "result": 8, "exact": 15}
    if e == 34:
        return {"key": "third", "result": 5, "exact": 10}
    if e >= 32:
        return {"key": "semi", "result": 5, "exact": 10}
    if e >= 28:
        return {"key": "quarter", "result": 4, "exact": 8}
    if e >= 25:
        return {"key": "r16", "result": 2, "exact": 5}
    if e >= 18:
        return {"key": "r32", "result": 2, "exact": 5}
    return {"key": "group", "result": 1, "exact": 3}


def _wc_score_for_winner(lam_h, lam_a, winner):
    h = max(0, min(5, int(round(lam_h))))
    a = max(0, min(5, int(round(lam_a))))
    if winner == "draw":
        score = max(0, min(4, int(round((lam_h + lam_a) / 2))))
        if score == 0 and lam_h + lam_a > 1.9:
            score = 1
        return score, score
    if winner == "home" and h <= a:
        h = min(5, a + 1)
    if winner == "away" and a <= h:
        a = min(5, h + 1)
    return h, a


def _wc_predict_snapshot(teams_obj, fixtures, match, weights):
    home = _wc_team(teams_obj, match.get("h"))
    away = _wc_team(teams_obj, match.get("a"))
    if not home or not away or home.get("ph") or away.get("ph"):
        return None
    h_stats = _wc_prior_stats(fixtures, match.get("h"), match)
    a_stats = _wc_prior_stats(fixtures, match.get("a"), match)
    h_rating = _wc_rating(home) + h_stats["gd"] * 26 + (h_stats["form"] - 0.33) * 85
    a_rating = _wc_rating(away) + a_stats["gd"] * 26 + (a_stats["form"] - 0.33) * 85
    h_rating -= _wc_missing_penalty(home, "attack") * 1.1 + _wc_missing_penalty(home, "defense") * 0.45
    a_rating -= _wc_missing_penalty(away, "attack") * 1.1 + _wc_missing_penalty(away, "defense") * 0.45

    group_rows = _wc_group_rows(teams_obj, fixtures, match.get("grp"), match)
    h_group = group_rows.get(match.get("h"), {})
    a_group = group_rows.get(match.get("a"), {})
    if h_group.get("played", 0) >= 2 and h_group.get("pts", 0) < 4:
        h_rating += 20
    if a_group.get("played", 0) >= 2 and a_group.get("pts", 0) < 4:
        a_rating += 20

    signals = {}
    _wc_signal(signals, "form", h_stats["form"], a_stats["form"], 0.16)
    _wc_signal(signals, "strength", h_rating, a_rating, 35)
    if h_group and a_group and h_group.get("pos") and a_group.get("pos"):
        _wc_signal(signals, "position", h_group["pos"], a_group["pos"], 1, invert=True)
    _wc_signal(signals, "streak", h_stats["streak"], a_stats["streak"], 0.18)
    _wc_signal(signals, "goals_trend", h_stats["gf_avg"], a_stats["gf_avg"], 0.25)
    _wc_signal(signals, "clean_sheet", h_stats["clean_rate"], a_stats["clean_rate"], 0.28)
    if h_stats["draw_rate"] >= 0.34 and a_stats["draw_rate"] >= 0.34:
        signals["draw_tendency"] = "draw"
    elif h_stats["draw_rate"] < a_stats["draw_rate"] - 0.22:
        signals["draw_tendency"] = "home"
    elif a_stats["draw_rate"] < h_stats["draw_rate"] - 0.22:
        signals["draw_tendency"] = "away"
    if h_stats["played"] and a_stats["played"]:
        _wc_signal(signals, "upset", h_stats["form"] - (_wc_rating(home) / 1500), a_stats["form"] - (_wc_rating(away) / 1500), 0.08)

    h_factor = 0.0
    a_factor = 0.0
    draw_factor = 0.0
    for name, pick in signals.items():
        w = float(weights.get(name, AI_WEIGHT_DEFAULTS.get(name, 0.05)))
        if pick == "home":
            h_factor += w
        elif pick == "away":
            a_factor += w
        elif pick == "draw":
            draw_factor += w

    gap = max(-380, min(380, h_rating - a_rating))
    rating_share = max(0.28, min(0.72, 0.5 + gap / 900))
    factor_share = max(0.30, min(0.70, 0.5 + (h_factor - a_factor) * 0.75))
    home_share = rating_share * 0.72 + factor_share * 0.28
    close = 1 - min(abs(home_share - 0.5) * 2, 1)
    draw_pct = 16 + close * 15 + draw_factor * 24
    if h_group.get("played", 0) >= 2 or a_group.get("played", 0) >= 2:
        if h_group.get("pts", 0) < 4 or a_group.get("pts", 0) < 4:
            draw_pct *= 0.88
    draw_pct = max(14, min(34, draw_pct))
    home_pct = (100 - draw_pct) * home_share
    away_pct = 100 - draw_pct - home_pct

    total_goals = 2.42 + min(abs(gap) / 950, 0.36)
    if h_stats["played"] or a_stats["played"]:
        avg_for = (h_stats["gf_avg"] + a_stats["gf_avg"]) / 2
        avg_against = (h_stats["ga_avg"] + a_stats["ga_avg"]) / 2
        total_goals = total_goals * 0.65 + max(1.75, min(3.25, avg_for + avg_against)) * 0.35
    lam_h = max(0.65, total_goals * home_share)
    lam_a = max(0.55, total_goals * (1 - home_share))
    if _wc_missing_penalty(home, "attack") > 18:
        lam_h *= 0.92
    if _wc_missing_penalty(away, "attack") > 18:
        lam_a *= 0.92
    if _wc_missing_penalty(home, "defense") > 18:
        lam_a *= 1.08
    if _wc_missing_penalty(away, "defense") > 18:
        lam_h *= 1.08

    if home_pct >= away_pct and home_pct >= draw_pct:
        winner = "home"
    elif away_pct >= home_pct and away_pct >= draw_pct:
        winner = "away"
    else:
        winner = "draw"
    if draw_pct * 1.18 > max(home_pct, away_pct) and close > 0.78:
        winner = "draw"
    rh, ra = _wc_score_for_winner(lam_h, lam_a, winner)
    return {
        "match_id": match.get("id"),
        "day": match.get("e"),
        "home_id": match.get("h"),
        "away_id": match.get("a"),
        "home": home.get("s") or home.get("n"),
        "away": away.get("s") or away.get("n"),
        "kickoff_time": match.get("ko", ""),
        "winner": winner,
        "home_score": rh,
        "away_score": ra,
        "home_win_pct": round(home_pct, 1),
        "draw_pct": round(draw_pct, 1),
        "away_win_pct": round(away_pct, 1),
        "expected_home_goals": round(lam_h, 2),
        "expected_away_goals": round(lam_a, 2),
        "signals": signals,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked": False,
    }


def _wc_update_weights(weights, checked_predictions):
    factor_scores = {k: [] for k in weights}
    for pred in checked_predictions:
        actual = pred.get("actual_winner")
        for factor, pick in (pred.get("signals") or {}).items():
            if factor in factor_scores and pick in ("home", "draw", "away"):
                factor_scores[factor].append(1.0 if pick == actual else 0.0)
    new_weights = dict(weights)
    for factor, scores in factor_scores.items():
        if len(scores) < 3:
            continue
        accuracy = sum(scores) / len(scores)
        new_weights[factor] = round(max(0.03, min(0.25, new_weights[factor] + (accuracy - 0.5) * 0.05)), 4)
    total = sum(new_weights.values()) or 1
    return {k: round(v / total, 4) for k, v in new_weights.items()}


def run_wc_learning(wc_payload, history):
    if not wc_payload:
        return history
    wc = json.loads(wc_payload)
    teams_obj = wc.get("teams", {})
    fixtures = wc.get("fix", [])
    pred_store = _load_json_file(WC_PREDICTIONS_FILE, {"version": 1, "matches": {}})
    if isinstance(pred_store.get("matches"), list):
        pred_store["matches"] = {str(p.get("match_id")): p for p in pred_store["matches"]}
    pred_store.setdefault("version", 1)
    pred_store.setdefault("matches", {})
    weights = _load_json_file(WC_WEIGHTS_FILE, dict(AI_WEIGHT_DEFAULTS))
    for k, v in AI_WEIGHT_DEFAULTS.items():
        weights.setdefault(k, v)

    now = datetime.now(timezone.utc)
    new_locked = 0
    for match in fixtures:
        mid = str(match.get("id"))
        if not mid or mid in pred_store["matches"]:
            continue
        if match.get("fin") or match.get("st"):
            continue
        home = _wc_team(teams_obj, match.get("h"))
        away = _wc_team(teams_obj, match.get("a"))
        if home.get("ph") or away.get("ph"):
            continue
        ko = _dt_utc(match.get("ko"))
        if ko and (ko - now).total_seconds() > WC_PREDICTION_LOCK_HOURS * 3600:
            continue
        if ko and (now - ko).total_seconds() > 600:
            continue
        pred = _wc_predict_snapshot(teams_obj, fixtures, match, weights)
        if pred:
            pred_store["matches"][mid] = pred
            new_locked += 1

    fixtures_by_id = {str(f.get("id")): f for f in fixtures}
    checked = []
    for mid, pred in pred_store["matches"].items():
        f = fixtures_by_id.get(mid)
        if not f or not f.get("fin") or f.get("hs") is None:
            continue
        actual_winner = _wc_winner(f.get("hs"), f.get("as"))
        pred["checked"] = True
        pred["actual_home_score"] = f.get("hs")
        pred["actual_away_score"] = f.get("as")
        pred["actual_winner"] = actual_winner
        pred["winner_correct"] = pred.get("winner") == actual_winner
        pred["score_correct"] = pred.get("home_score") == f.get("hs") and pred.get("away_score") == f.get("as")
        rule = _wc_phase_rule(f)
        pred["points"] = rule["exact"] if pred["score_correct"] else rule["result"] if pred["winner_correct"] else 0
        pred["phase"] = rule["key"]
        checked.append(pred)

    if checked:
        weights = _wc_update_weights(weights, checked)

    by_day = {}
    for pred in checked:
        day = str(pred.get("day") or 0)
        row = by_day.setdefault(day, {"gw": int(pred.get("day") or 0), "total": 0, "correct_winner": 0, "correct_score": 0, "points": 0})
        row["total"] += 1
        row["correct_winner"] += 1 if pred.get("winner_correct") else 0
        row["correct_score"] += 1 if pred.get("score_correct") else 0
        row["points"] += int(pred.get("points") or 0)
    rows = []
    for row in by_day.values():
        row["accuracy_pct"] = round(row["correct_winner"] / max(row["total"], 1) * 100, 1)
        row["score_acc_pct"] = round(row["correct_score"] / max(row["total"], 1) * 100, 1)
        rows.append(row)
    rows.sort(key=lambda r: r["gw"])
    total = sum(r["total"] for r in rows)
    correct = sum(r["correct_winner"] for r in rows)
    wc_hist = {
        "gw_results": rows,
        "overall_accuracy": round(correct / max(total, 1) * 100, 1) if total else 0,
        "total_evaluated": total,
        "current_weights": weights,
        "snapshots_locked": len(pred_store["matches"]),
    }
    history["wc"] = wc_hist
    pred_store["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _save_json_file(WC_PREDICTIONS_FILE, pred_store)
    _save_json_file(WC_WEIGHTS_FILE, weights)
    _save_json_file(LEARNING_HISTORY_FILE, history)
    print(f"World Cup ML: {new_locked} new locked predictions, {total} checked, accuracy {wc_hist['overall_accuracy']}%")
    return history


def _pl_official_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _pl_official_team(raw_team, prev_teams, teams_out):
    team = (raw_team or {}).get("team") or raw_team or {}
    club = team.get("club") or {}
    name = team.get("name") or club.get("name") or team.get("shortName") or "Unknown"
    abbr = (club.get("abbr") or team.get("abbr") or team.get("shortName") or name[:3]).upper()
    short = abbr[:3] if len(abbr) > 4 else abbr
    team_id = _pl_official_int(team.get("id") or club.get("id"), len(teams_out) + 1)
    alt_ids = team.get("altIds") or club.get("altIds") or {}
    opta = str(alt_ids.get("opta") or "")
    badge_code = re.sub(r"\D", "", opta) or str(team_id)

    prev_by_abbr = {str(t.get("s", "")).upper(): t for t in prev_teams.values()}
    prev_by_name = {_plain_name(t.get("n", "")).lower(): t for t in prev_teams.values()}
    base = prev_by_abbr.get(short.upper()) or prev_by_abbr.get(abbr.upper()) or prev_by_name.get(_plain_name(name).lower()) or {}
    teams_out[team_id] = {
        "id": team_id,
        "n": name,
        "s": short,
        "c": badge_code,
        "b": BADGE.format(badge_code),
        "sah": base.get("sah", 1100),
        "sdh": base.get("sdh", 1080),
        "saa": base.get("saa", 1060),
        "sda": base.get("sda", 1060),
        "xg": base.get("xg", 1.1),
        "xgc": base.get("xgc", 1.2),
        "inj": base.get("inj", []),
    }
    return team_id


def fetch_pl_official_season(prev_teams, headers):
    pulse_headers = {
        **headers,
        "Origin": "https://www.premierleague.com",
        "Referer": "https://www.premierleague.com/fixtures",
    }
    resp = requests.get(
        PL_OFFICIAL_FIXTURES,
        params={
            "comps": "1",
            "compSeasons": PL_OFFICIAL_COMPSEASON,
            "page": "0",
            "pageSize": "1000",
            "sort": "asc",
            "altIds": "true",
        },
        headers=pulse_headers,
        timeout=30,
    )
    resp.raise_for_status()
    official_fixtures = resp.json().get("content", [])
    if not official_fixtures:
        return None

    teams_out = {}
    fixtures_out = []
    for item in official_fixtures:
        sides = item.get("teams") or []
        if len(sides) < 2:
            continue
        home_id = _pl_official_team(sides[0], prev_teams, teams_out)
        away_id = _pl_official_team(sides[1], prev_teams, teams_out)
        gameweek = item.get("gameweek") or {}
        gw = _pl_official_int(gameweek.get("gameweek") or gameweek.get("id"), 0)
        kickoff = item.get("kickoff") or {}
        ko = ""
        millis = kickoff.get("millis")
        if millis:
            ko = datetime.fromtimestamp(float(millis) / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fid = _pl_official_int(
            item.get("id") or re.sub(r"\D", "", str((item.get("altIds") or {}).get("opta", ""))),
            len(fixtures_out) + 1,
        )
        status = str(item.get("status") or "").upper()
        finished = status in ("C", "FT", "F")
        started = status not in ("", "U", "S") and not finished
        fixtures_out.append({
            "id": fid,
            "e": gw,
            "h": home_id,
            "a": away_id,
            "hs": None,
            "as": None,
            "fin": finished,
            "st": started,
            "ko": ko,
            "mn": 90 if finished else 0,
            "sx": status,
        })

    fixtures_out = [f for f in fixtures_out if f.get("e")]
    fixtures_out.sort(key=lambda f: (f.get("e", 0), f.get("ko", "")))
    max_gw = max((f["e"] for f in fixtures_out), default=38)
    gws_out = []
    for gw in range(1, max_gw + 1):
        gw_fix = [f for f in fixtures_out if f["e"] == gw]
        all_fin = bool(gw_fix) and all(f["fin"] for f in gw_fix)
        is_live = any(f["st"] and not f["fin"] for f in gw_fix)
        gws_out.append({"id": gw, "fin": all_fin, "cur": is_live})
    if not any(g["cur"] for g in gws_out):
        for gw in gws_out:
            if not gw["fin"]:
                gw["cur"] = True
                break

    return {
        "teams": teams_out,
        "gws": gws_out,
        "fix": fixtures_out,
        "season": "2026-27",
        "label": "2026/27",
        "archive": False,
    }

print("Fetching Premier League data...")
hdr = {"User-Agent": "PL-Dashboard/1.0"}
try:
    bs = requests.get(f"{FPL}/bootstrap-static/", headers=hdr, timeout=20).json()
    fx = requests.get(f"{FPL}/fixtures/", headers=hdr, timeout=20).json()
except Exception as e:
    print(f"FPL API fetch failed: {e}")
    raise SystemExit(1)

teams = {}
team_xg = {}
for p in bs.get("elements", []):
    tid = p.get("team")
    if tid not in team_xg:
        team_xg[tid] = {"xg": 0, "xa": 0, "xgc": 0, "mins": 0, "inj": []}
    team_xg[tid]["xg"] += float(p.get("expected_goals", 0))
    team_xg[tid]["xa"] += float(p.get("expected_assists", 0))
    if p.get("element_type") in (1, 2):
        team_xg[tid]["xgc"] += float(p.get("expected_goals_conceded", 0))
    team_xg[tid]["mins"] += p.get("minutes", 0)
    if p.get("status") in ("i", "s", "u"):
        xgi = float(p.get("expected_goal_involvements", 0))
        if xgi > 3:
            team_xg[tid]["inj"].append({"n": p.get("web_name", "?"), "xgi": round(xgi, 1)})

for t in bs["teams"]:
    tid = t["id"]
    tx = team_xg.get(tid, {})
    me = max(tx.get("mins", 990) / 11 / 90, 1)
    teams[tid] = {
        "id": tid, "n": t["name"], "s": t["short_name"], "c": t["code"],
        "b": BADGE.format(t["code"]),
        "sah": t.get("strength_attack_home", 1000),
        "sdh": t.get("strength_defence_home", 1000),
        "saa": t.get("strength_attack_away", 1000),
        "sda": t.get("strength_defence_away", 1000),
        "xg": round(tx.get("xg", 0) / me, 2),
        "xgc": round(tx.get("xgc", 0) / max(me, 1), 2),
        "inj": tx.get("inj", [])[:3],
    }

gws = [{"id": e["id"], "fin": e["finished"], "cur": e["is_current"]} for e in bs["events"]]

fixtures = [{
    "id": f["id"], "e": f.get("event"), "h": f["team_h"], "a": f["team_a"],
    "hs": f.get("team_h_score"), "as": f.get("team_a_score"),
    "fin": f.get("finished", False) or f.get("finished_provisional", False),
    "st": f.get("started", False),
    "ko": f.get("kickoff_time", ""), "mn": f.get("minutes", 0),
} for f in fx if f.get("event") is not None]

# ── Overlay live data from api-football.com (more accurate minutes + status) ──
if FOOTBALL_API_KEY:
    try:
        live_resp = requests.get(f"{FOOTBALL_API}/fixtures",
            params={"live": "all"},
            headers={"x-apisports-key": FOOTBALL_API_KEY}, timeout=15).json()
        live_matches = live_resp.get("response", [])
        pl_live = {m["teams"]["home"]["name"].lower(): m for m in live_matches
                   if m.get("league", {}).get("id") == 39}
        ll_live = {m["teams"]["home"]["name"].lower(): m for m in live_matches
                   if m.get("league", {}).get("id") == 140}

        team_name_map = {t["n"].lower(): t["id"] for t in teams.values()}
        updated = 0
        for fix in fixtures:
            h_name = teams.get(fix["h"], {}).get("n", "").lower()
            for key, lm in pl_live.items():
                if key in h_name or h_name in key:
                    status = lm["fixture"]["status"]
                    fix["mn"] = status.get("elapsed", 0) or 0
                    fix["hs"] = lm["goals"]["home"]
                    fix["as"] = lm["goals"]["away"]
                    fix["st"] = True
                    short = status.get("short", "")
                    if short in ("FT", "AET", "PEN"):
                        fix["fin"] = True
                    elif short == "HT":
                        fix["mn"] = 45
                    updated += 1
                    break
        if updated:
            print(f"api-football: overlaid {updated} live PL matches")
        if ll_live:
            print(f"api-football: {len(ll_live)} live La Liga matches available")
    except Exception as e:
        print(f"api-football: {e}")

pl_2025_data = {
    "teams": teams,
    "gws": gws,
    "fix": fixtures,
    "season": "2025-26",
    "label": "2025/26",
    "archive": bool(fixtures) and all(f["fin"] for f in fixtures),
}
pl_seasons_data = {"2025-26": pl_2025_data}
pl_season_items = [{"key": "2025-26", "label": "2025/26"}]
pl_current_key = "2025-26"

try:
    pl_2026_data = fetch_pl_official_season(teams, hdr)
    if pl_2026_data and pl_2026_data.get("fix"):
        pl_seasons_data["2026-27"] = pl_2026_data
        pl_season_items.insert(0, {"key": "2026-27", "label": "2026/27"})
        pl_current_key = "2026-27"
        print(f"Premier League 2026/27 official fixtures: {len(pl_2026_data['fix'])} loaded")
except Exception as e:
    print(f"Premier League 2026/27 official fetch failed: {e}")

data = json.dumps(pl_seasons_data[pl_current_key], ensure_ascii=False, separators=(",", ":"))
pl_seasons_json = json.dumps({
    "current": pl_current_key,
    "items": pl_season_items,
    "data": pl_seasons_data,
}, ensure_ascii=False, separators=(",", ":"))
print(f"Teams: {len(teams)}, GWs: {len(gws)}, Fixtures: {len(fixtures)}")
print(f"Premier League default season: {pl_current_key}")

guesses_file = os.path.join(ROOT, "user_guesses.json")
guesses = {}
if os.path.exists(guesses_file):
    with open(guesses_file, "r", encoding="utf-8") as gf:
        raw = json.load(gf)
    for gw_key, gw_data in raw.items():
        guesses[gw_key] = {}
        for g in gw_data.get("guesses", []):
            mid = g.get("match_id")
            if mid:
                guesses[gw_key][mid] = {
                    "w": g.get("winner"),
                    "hs": g.get("home_score"),
                    "as": g.get("away_score"),
                }
    print(f"Guesses: {sum(len(v) for v in guesses.values())} across {len(guesses)} gameweeks")
else:
    print("No user_guesses.json found (guesses will come from phone localStorage only)")

guesses_json = json.dumps(guesses, ensure_ascii=False, separators=(",", ":"))

# ── La Liga data from ESPN ──
print("\nFetching La Liga data...")
try:
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    date_range = f"{start_year}0801-{start_year + 1}0630"

    espn_events = requests.get(ESPN_SCOREBOARD, params={"dates": date_range, "limit": "1000"},
                                headers=hdr, timeout=30).json().get("events", [])

    # ESPN doesn't return live status for historical date ranges — fetch today separately
    today_str = now.strftime("%Y%m%d")
    try:
        today_events = requests.get(ESPN_SCOREBOARD, params={"dates": today_str},
                                    headers=hdr, timeout=15).json().get("events", [])
        today_by_id = {ev["id"]: ev for ev in today_events}
        if today_by_id:
            espn_events = [today_by_id.get(ev["id"], ev) for ev in espn_events]
            print(f"La Liga: merged {len(today_by_id)} today's events with live status")
    except Exception as e:
        print(f"La Liga today-fetch: {e}")

    espn_events.sort(key=lambda e: e.get("date", ""))

    espn_standings = requests.get(ESPN_STANDINGS, headers=hdr, timeout=20).json()
    children = espn_standings.get("children", [])
    entries = children[0].get("standings", {}).get("entries", []) if children else []

    ll_teams = {}
    for entry in entries:
        et = entry.get("team", {})
        tid = int(et.get("id", 0))
        logos = et.get("logos", [])
        badge = logos[0]["href"] if logos else ""
        ll_teams[tid] = {
            "id": tid, "n": et.get("displayName", ""), "s": et.get("abbreviation", "???"),
            "c": tid, "b": badge,
            "sah": 1200, "sdh": 1200, "saa": 1100, "sda": 1100,
        }

    for ev in espn_events:
        for comp in ev.get("competitions", []):
            for c in comp.get("competitors", []):
                ct = c.get("team", {})
                ctid = int(ct.get("id", 0))
                if ctid and ctid not in ll_teams:
                    ll_teams[ctid] = {
                        "id": ctid, "n": ct.get("displayName", ct.get("name", "")),
                        "s": ct.get("abbreviation", "???"), "c": ctid,
                        "b": ct.get("logo", ""),
                        "sah": 1100, "sdh": 1100, "saa": 1050, "sda": 1050,
                    }

    import re as _re

    # Build team match count to determine real matchdays
    team_finished = {}
    ll_raw = []
    for ev in espn_events:
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue
        home = away = None
        for c in competitors:
            if c.get("homeAway") == "home":
                home = c
            else:
                away = c
        if not home or not away:
            continue
        status = comp.get("status", {}).get("type", {})
        state = status.get("state", "pre")
        started = state in ("in", "post")
        finished = status.get("completed", False)
        hs = as_score = None
        mn = 0
        if started:
            try: hs = int(home.get("score", "0"))
            except (ValueError, TypeError): hs = 0
            try: as_score = int(away.get("score", "0"))
            except (ValueError, TypeError): as_score = 0
            detail = status.get("shortDetail", "")
            if detail:
                mn_match = _re.search(r"(\d+)'", detail)
                if mn_match:
                    mn = int(mn_match.group(1))
                elif "HT" in detail.upper():
                    mn = 45
                elif "FT" in detail.upper():
                    mn = 90
        hid = int(home["team"]["id"])
        aid = int(away["team"]["id"])
        if finished:
            team_finished[hid] = team_finished.get(hid, 0) + 1
            team_finished[aid] = team_finished.get(aid, 0) + 1
        ll_raw.append({
            "id": int(ev.get("id", 0)),
            "h": hid, "a": aid,
            "hs": hs, "as": as_score,
            "fin": finished, "st": started, "ko": ev.get("date", ""), "mn": mn,
            "sx": status.get("name", ""),
        })

    # Assign matchdays by sorting all matches by date → idx // 10 + 1
    # This guarantees exactly 10 matches per matchday (matches played order)
    ll_raw.sort(key=lambda x: x.get("ko", ""))
    ll_fixtures = []
    for i, f in enumerate(ll_raw):
        f["e"] = i // 10 + 1
        ll_fixtures.append(f)

    n_fin = sum(1 for f in ll_fixtures if f["fin"])
    n_up  = len(ll_fixtures) - n_fin
    cur_md = ll_fixtures[-1]["e"] if ll_fixtures else 1
    # Find current matchday (first with live or first unfinished)
    for f in ll_fixtures:
        if f["st"] and not f["fin"]:
            cur_md = f["e"]; break
    print(f"La Liga: {n_fin} finished, {n_up} upcoming, current MD~{cur_md}")

    max_md = max((f["e"] for f in ll_fixtures), default=38)
    ll_gws = []
    for md in range(1, max_md + 1):
        md_fix = [f for f in ll_fixtures if f["e"] == md]
        all_fin = all(f["fin"] for f in md_fix) if md_fix else False
        is_cur = any(f["st"] and not f["fin"] for f in md_fix)
        ll_gws.append({"id": md, "fin": all_fin, "cur": is_cur})

    # Current = first unfinished matchday (the upcoming one); fallback to last finished
    if not any(g["cur"] for g in ll_gws):
        for g in ll_gws:
            if not g["fin"]:
                g["cur"] = True
                break
    if not any(g["cur"] for g in ll_gws):
        for g in reversed(ll_gws):
            if g["fin"]:
                g["cur"] = True
                break

    ll_data = json.dumps({"teams": ll_teams, "gws": ll_gws, "fix": ll_fixtures},
                          ensure_ascii=False, separators=(",", ":"))
    print(f"La Liga — Teams: {len(ll_teams)}, Matchdays: {len(ll_gws)}, Fixtures: {len(ll_fixtures)}")
except Exception as e:
    print(f"La Liga fetch failed: {e}")
    ll_data = ""

# La Liga guesses
ll_guesses_file = os.path.join(ROOT, "user_guesses_laliga.json")
ll_guesses = {}
if os.path.exists(ll_guesses_file):
    with open(ll_guesses_file, "r", encoding="utf-8") as gf:
        raw = json.load(gf)
    for gw_key, gw_data in raw.items():
        ll_guesses[gw_key] = {}
        for g in gw_data.get("guesses", []):
            mid = g.get("match_id")
            if mid:
                ll_guesses[gw_key][mid] = {"w": g.get("winner"), "hs": g.get("home_score"), "as": g.get("away_score")}
    print(f"La Liga guesses: {sum(len(v) for v in ll_guesses.values())}")

ll_guesses_json = json.dumps(ll_guesses, ensure_ascii=False, separators=(",", ":"))

# ── World Cup data from ESPN ──
print("\nFetching World Cup data...")
try:
    wc_events = requests.get(ESPN_WC_SCOREBOARD, params={"dates": WC_DATE_RANGE, "limit": "200"},
                             headers=hdr, timeout=30).json().get("events", [])

    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        today_events = requests.get(ESPN_WC_SCOREBOARD, params={"dates": today_str, "limit": "50"},
                                    headers=hdr, timeout=15).json().get("events", [])
        today_by_id = {ev["id"]: ev for ev in today_events}
        if today_by_id:
            wc_events = [today_by_id.get(ev["id"], ev) for ev in wc_events]
            print(f"World Cup: merged {len(today_by_id)} today's events with live status")
    except Exception as e:
        print(f"World Cup today-fetch: {e}")

    fifa_wc_by_key = {}
    try:
        fifa_matches = fetch_fifa_wc_matches()
        fifa_wc_by_key = {m["key"]: m for m in fifa_matches if m.get("key")}
        print(f"World Cup: FIFA official feed available ({len(fifa_wc_by_key)} matches)")
    except Exception as e:
        print(f"World Cup FIFA feed: {e}")

    wc_events.sort(key=lambda e: e.get("date", ""))
    wc_teams = {}
    wc_fixtures = []
    wc_days = {}
    fifa_overlays = 0
    import re as _wc_re

    for ev in wc_events:
        comps = ev.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]
        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue
        group_match = _wc_re.search(r"Group\s+([A-L])", comp.get("altGameNote", "") or "")
        wc_group = group_match.group(1) if group_match else None
        home = away = None
        for c in competitors:
            if c.get("homeAway") == "home":
                home = c
            else:
                away = c
        if not home or not away:
            continue
        for c in (home, away):
            ct = c.get("team", {})
            tid = int(ct.get("id", 0))
            wc_name = ct.get("displayName", ct.get("name", ""))
            wc_ph = wc_name.startswith("Group ") or wc_name.startswith("Round ") or \
                wc_name.startswith("Quarterfinal ") or wc_name.startswith("Semifinal ") or \
                wc_name.startswith("Third Place ")
            if tid and tid not in wc_teams:
                wc_rating = 1080 if wc_ph else wc_seed_strength(ct.get("abbreviation", ""))
                wc_teams[tid] = {
                    "id": tid,
                    "n": wc_name,
                    "s": ct.get("abbreviation", "???"),
                    "c": tid,
                    "b": ct.get("logo", ""),
                    "ph": wc_ph,
                    "grp": wc_group if not wc_ph else None,
                    "sah": wc_rating, "sdh": wc_rating, "saa": wc_rating, "sda": wc_rating,
                }
            elif tid and wc_group and not wc_ph and not wc_teams[tid].get("grp"):
                wc_teams[tid]["grp"] = wc_group

        status = comp.get("status", {}).get("type", {})
        state = status.get("state", "pre")
        started = state in ("in", "post")
        finished = status.get("completed", False)
        hs = as_score = None
        mn = 0
        if started:
            try: hs = int(home.get("score", "0"))
            except (ValueError, TypeError): hs = 0
            try: as_score = int(away.get("score", "0"))
            except (ValueError, TypeError): as_score = 0
            detail = status.get("shortDetail", "") or comp.get("status", {}).get("displayClock", "")
            if detail:
                mn_match = _wc_re.search(r"(\d+)'", detail)
                if mn_match:
                    mn = int(mn_match.group(1))
                elif "HT" in detail.upper():
                    mn = 45
                elif "FT" in detail.upper():
                    mn = 90
        if finished:
            mn = 90

        home_name = home.get("team", {}).get("displayName", home.get("team", {}).get("name", ""))
        away_name = away.get("team", {}).get("displayName", away.get("team", {}).get("name", ""))
        fifa_match = fifa_wc_by_key.get(wc_match_key(home_name, away_name, ev.get("date", "")))
        if fifa_match and (fifa_match.get("st") or fifa_match.get("fin")):
            fifa_overlays += 1
            started = started or bool(fifa_match.get("st"))
            finished = finished or bool(fifa_match.get("fin"))
            if fifa_match.get("hs") is not None:
                hs = fifa_match["hs"]
            if fifa_match.get("as") is not None:
                as_score = fifa_match["as"]
            if started and not finished and not mn and ev.get("date"):
                try:
                    ko_dt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
                    mn = max(0, min(130, int((datetime.now(timezone.utc) - ko_dt).total_seconds() // 60)))
                except Exception:
                    mn = 0
            if finished:
                mn = 90

        day = (ev.get("date", "")[:10] or f"match-{len(wc_fixtures)}")
        if day not in wc_days:
            wc_days[day] = len(wc_days) + 1
        wc_fixtures.append({
            "id": int(ev.get("id", 0)),
            "e": wc_days[day],
            "h": int(home["team"]["id"]),
            "a": int(away["team"]["id"]),
            "hs": hs, "as": as_score,
            "fin": finished, "st": started, "ko": ev.get("date", ""), "mn": mn,
            "sx": fifa_match.get("sx") if fifa_match and (fifa_match.get("st") or fifa_match.get("fin")) else status.get("name", ""),
            "grp": wc_group,
            "src": "espn+fifa" if fifa_match and (fifa_match.get("st") or fifa_match.get("fin")) else "espn",
        })
    if fifa_overlays:
        print(f"World Cup: overlaid {fifa_overlays} matches from FIFA official feed")

    roster_ok, roster_issues, manual_issues = apply_wc_availability(wc_teams)
    if roster_ok:
        msg = f"World Cup: ESPN rosters loaded for {roster_ok} teams"
        if roster_issues:
            msg += f", {roster_issues} flagged availability issues"
        if manual_issues:
            msg += f", {manual_issues} manual overrides"
        print(msg)

    max_wc_day = max((f["e"] for f in wc_fixtures), default=1)
    wc_gws = []
    for md in range(1, max_wc_day + 1):
        md_fix = [f for f in wc_fixtures if f["e"] == md]
        all_fin = all(f["fin"] for f in md_fix) if md_fix else False
        is_cur = any(f["st"] and not f["fin"] for f in md_fix)
        wc_gws.append({"id": md, "fin": all_fin, "cur": is_cur})
    if not any(g["cur"] for g in wc_gws):
        for g in wc_gws:
            if not g["fin"]:
                g["cur"] = True
                break
    if not any(g["cur"] for g in wc_gws):
        for g in reversed(wc_gws):
            if g["fin"]:
                g["cur"] = True
                break

    wc_archive = bool(wc_fixtures) and all(f["fin"] for f in wc_fixtures) and datetime.now(timezone.utc).strftime("%Y%m%d") > "20260719"
    wc_data = json.dumps({"teams": wc_teams, "gws": wc_gws, "fix": wc_fixtures, "archive": wc_archive},
                         ensure_ascii=False, separators=(",", ":"))
    n_fin = sum(1 for f in wc_fixtures if f["fin"])
    print(f"World Cup — Teams: {len(wc_teams)}, Days: {len(wc_gws)}, Fixtures: {len(wc_fixtures)}, Finished: {n_fin}, Archive: {wc_archive}")
except Exception as e:
    print(f"World Cup fetch failed: {e}")
    wc_data = ""

# World Cup guesses
wc_guesses_file = os.path.join(ROOT, "user_guesses_wc.json")
wc_guesses = {}
if os.path.exists(wc_guesses_file):
    with open(wc_guesses_file, "r", encoding="utf-8") as gf:
        raw = json.load(gf)
    for gw_key, gw_data in raw.items():
        wc_guesses[gw_key] = {}
        for g in gw_data.get("guesses", []):
            mid = g.get("match_id")
            if mid:
                wc_guesses[gw_key][mid] = {"w": g.get("winner"), "hs": g.get("home_score"), "as": g.get("away_score")}
    print(f"World Cup guesses: {sum(len(v) for v in wc_guesses.values())}")

wc_guesses_json = json.dumps(wc_guesses, ensure_ascii=False, separators=(",", ":"))

import socket
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""

IS_CI = os.environ.get("CI", "").lower() in ("true", "1")
if IS_CI:
    local_ip = ""
    server_url = ""
    print("Running in CI — no local server URL")
else:
    local_ip = get_local_ip()
    server_url = f"http://{local_ip}:5000" if local_ip else ""
    print(f"Local IP: {local_ip or 'unknown'}")

html = open(TPL, "r", encoding="utf-8").read()
html = html.replace("/*__DATA__*/", "var EMBEDDED=" + data + ";")
html = html.replace("/*__DATA_PL_SEASONS__*/", "var EMBEDDED_PL_SEASONS=" + pl_seasons_json + ";")
html = html.replace("/*__DATA_LL__*/", "var EMBEDDED_LL=" + ll_data + ";" if ll_data else "")
html = html.replace("/*__DATA_WC__*/", "var EMBEDDED_WC=" + wc_data + ";" if wc_data else "")
html = html.replace("/*__GUESSES__*/", "var EMBEDDED_GUESSES=" + guesses_json + ";")
html = html.replace("/*__GUESSES_LL__*/", "var EMBEDDED_GUESSES_LL=" + ll_guesses_json + ";")
html = html.replace("/*__GUESSES_WC__*/", "var EMBEDDED_GUESSES_WC=" + wc_guesses_json + ";")
html = html.replace("/*__SERVER__*/", f'var EMBEDDED_SERVER="{server_url}";' if server_url else "")
html = html.replace("/*__BUILD_TIME__*/", f'var EMBEDDED_BUILD_TIME="{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}";')

# ── ML Learning Engine ────────────────────────────────────────────────────
try:
    import sys as _sys_ml; _sys_ml.path.insert(0, HERE)
    from ml_engine import run_pl_learning

    # Compute standings (for position factor in weight updates)
    _pts = {}
    for _f in fx:
        if not _f.get("finished"): continue
        _hs, _as = _f.get("team_h_score"), _f.get("team_a_score")
        if _hs is None or _as is None: continue
        for _tid, _sf, _sa in [(_f["team_h"], _hs, _as), (_f["team_a"], _as, _hs)]:
            if _tid not in _pts: _pts[_tid] = {"pts": 0, "gd": 0}
            _pts[_tid]["pts"] += 3 if _sf > _sa else (1 if _sf == _sa else 0)
            _pts[_tid]["gd"]  += _sf - _sa
    _pos = {tid: i + 1 for i, (tid, _) in enumerate(
        sorted(_pts.items(), key=lambda x: (-x[1]["pts"], -x[1]["gd"]))
    )}
    teams_ml = {tid: {**t, "position": _pos.get(tid, 10)} for tid, t in teams.items()}
    learning_history = run_pl_learning(fx, teams_ml)
    print("ML learning complete")
except Exception as _ml_err:
    learning_history = {"pl": {"gw_results": []}, "laliga": {"gw_results": []}}
    print(f"ML learning skipped: {_ml_err}")

try:
    learning_history = run_wc_learning(wc_data, learning_history)
except Exception as _wc_ml_err:
    learning_history.setdefault("wc", {"gw_results": []})
    print(f"World Cup ML skipped: {_wc_ml_err}")

_lh_json = json.dumps(learning_history, separators=(",", ":"))
html = html.replace("/*__LEARNING_HISTORY__*/", "var LEARNING_HISTORY=" + _lh_json + ";")

weights_file = os.path.join(ROOT, "ai_weights.json")
if os.path.exists(weights_file):
    with open(weights_file, "r") as wf:
        weights_json = wf.read().strip()
    html = html.replace("/*__WEIGHTS__*/", "var EMBEDDED_WEIGHTS=" + weights_json + ";")
    print(f"AI weights embedded")
else:
    html = html.replace("/*__WEIGHTS__*/", "")

weights_wc_file = os.path.join(ROOT, "ai_weights_wc.json")
if os.path.exists(weights_wc_file):
    with open(weights_wc_file, "r", encoding="utf-8") as wf:
        weights_wc_json = wf.read().strip()
    html = html.replace("/*__WEIGHTS_WC__*/", "var EMBEDDED_WEIGHTS_WC=" + weights_wc_json + ";")
    print("World Cup AI weights embedded")
else:
    html = html.replace("/*__WEIGHTS_WC__*/", "")

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Updated: {OUT}")

live_data = {
    "fix": fixtures,
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "pl_current": pl_current_key,
    "pl_seasons": {
        key: {"fix": val.get("fix", []), "gws": val.get("gws", [])}
        for key, val in pl_seasons_data.items()
    },
}
if ll_data:
    try:
        ll_parsed = json.loads(ll_data)
        live_data["fix_ll"] = ll_parsed.get("fix", [])
    except Exception:
        pass
if wc_data:
    try:
        wc_parsed = json.loads(wc_data)
        live_data["fix_wc"] = wc_parsed.get("fix", [])
        live_data["wc_archive"] = wc_parsed.get("archive", False)
    except Exception:
        pass

live_json = json.dumps(live_data, ensure_ascii=False, separators=(",", ":"))
with open(os.path.join(ROOT, "live.json"), "w", encoding="utf-8") as f:
    f.write(live_json)
print("Updated: live.json")

# ── Auto-upload to GitHub Pages ──
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = "moadi1987-eng/PL"

if GITHUB_TOKEN:
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    # Upload live.json (small, fast — browser fetches this for live updates)
    live_b64 = base64.b64encode(live_json.encode("utf-8")).decode()

    print("Uploading live.json...")
    live_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/live.json"
    existing_live = requests.get(live_url, headers=headers, timeout=15)
    live_sha = existing_live.json().get("sha", "") if existing_live.status_code == 200 else ""
    live_payload = {"message": "Live update", "content": live_b64}
    if live_sha:
        live_payload["sha"] = live_sha
    resp_live = requests.put(live_url, headers=headers, json=live_payload, timeout=15)
    if resp_live.status_code in (200, 201):
        print("live.json uploaded!")

    # Upload AI learning state.
    for _ml_file in ("learning_history.json", "ai_weights.json", "ai_predictions_wc.json", "ai_weights_wc.json"):
        _ml_path = os.path.join(ROOT, _ml_file)
        if not os.path.exists(_ml_path): continue
        try:
            _ml_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{_ml_file}"
            _ml_existing = requests.get(_ml_url, headers=headers, timeout=10)
            _ml_sha = _ml_existing.json().get("sha", "") if _ml_existing.status_code == 200 else ""
            _ml_b64 = base64.b64encode(open(_ml_path, "rb").read()).decode()
            _ml_payload = {"message": f"Update {_ml_file}", "content": _ml_b64}
            if _ml_sha: _ml_payload["sha"] = _ml_sha
            _r = requests.put(_ml_url, headers=headers, json=_ml_payload, timeout=15)
            if _r.status_code in (200, 201): print(f"{_ml_file} uploaded!")
        except Exception as _e: print(f"{_ml_file} upload failed: {_e}")

    # Upload index.html (full page rebuild)
    print("Uploading index.html...")
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/index.html"

    existing = requests.get(api_url, headers=headers, timeout=15)
    sha = existing.json().get("sha", "") if existing.status_code == 200 else ""

    content = base64.b64encode(open(OUT, "rb").read()).decode()
    payload = {"message": "Update PL Dashboard data", "content": content}
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if resp.status_code in (200, 201):
        print(f"Uploaded to https://moadi1987-eng.github.io/PL/")
    else:
        print(f"GitHub upload failed: {resp.status_code} {resp.text[:200]}")
else:
    print("No GITHUB_TOKEN set — skipping GitHub upload.")
    print("To enable: set GITHUB_TOKEN in .env or environment variables.")

print("\nDone! Open pl_mobile.html in Chrome or visit https://moadi1987-eng.github.io/PL/")
