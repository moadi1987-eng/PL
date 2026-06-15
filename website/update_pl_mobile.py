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

data = json.dumps({"teams": teams, "gws": gws, "fix": fixtures}, ensure_ascii=False, separators=(",", ":"))
print(f"Teams: {len(teams)}, GWs: {len(gws)}, Fixtures: {len(fixtures)}")

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

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Updated: {OUT}")

live_data = {
    "fix": fixtures,
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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

    # Upload learning_history.json + ai_weights.json (ML state)
    for _ml_file in ("learning_history.json", "ai_weights.json"):
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
