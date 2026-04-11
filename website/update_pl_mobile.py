"""
Run this to update pl_mobile.html with fresh PL data.
Automatically uploads to GitHub Pages if GITHUB_TOKEN is set.

Usage:  python update_pl_mobile.py
"""
import json, requests, os, base64

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
html = html.replace("/*__GUESSES__*/", "var EMBEDDED_GUESSES=" + guesses_json + ";")
html = html.replace("/*__GUESSES_LL__*/", "var EMBEDDED_GUESSES_LL=" + ll_guesses_json + ";")
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

# ── Auto-upload to GitHub Pages ──
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = "moadi1987-eng/PL"

if GITHUB_TOKEN:
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

    # Upload live.json (small, fast — browser fetches this for live updates)
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
    live_json = json.dumps(live_data, ensure_ascii=False, separators=(",", ":"))
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
