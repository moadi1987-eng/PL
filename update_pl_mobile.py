"""
Run this to update pl_mobile.html with fresh PL data.
Automatically uploads to GitHub Pages if GITHUB_TOKEN is set.

Usage:  python update_pl_mobile.py
"""
import json, requests, os, base64

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

FPL = "https://fantasy.premierleague.com/api"
BADGE = "https://resources.premierleague.com/premierleague/badges/70/t{}.png"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pl_mobile.html")
TPL = os.path.join(HERE, "pl_mobile_template.html")

from datetime import datetime, timezone

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/esp.1/standings"

print("Fetching Premier League data...")
hdr = {"User-Agent": "PL-Dashboard/1.0"}
bs = requests.get(f"{FPL}/bootstrap-static/", headers=hdr, timeout=20).json()
fx = requests.get(f"{FPL}/fixtures/", headers=hdr, timeout=20).json()

teams = {}
for t in bs["teams"]:
    teams[t["id"]] = {
        "id": t["id"], "n": t["name"], "s": t["short_name"], "c": t["code"],
        "b": BADGE.format(t["code"]),
        "sah": t.get("strength_attack_home", 1000),
        "sdh": t.get("strength_defence_home", 1000),
        "saa": t.get("strength_attack_away", 1000),
        "sda": t.get("strength_defence_away", 1000),
    }

gws = [{"id": e["id"], "fin": e["finished"], "cur": e["is_current"]} for e in bs["events"]]

fixtures = [{
    "id": f["id"], "e": f.get("event"), "h": f["team_h"], "a": f["team_a"],
    "hs": f.get("team_h_score"), "as": f.get("team_a_score"),
    "fin": f.get("finished", False), "st": f.get("started", False),
    "ko": f.get("kickoff_time", ""),
} for f in fx if f.get("event") is not None]

data = json.dumps({"teams": teams, "gws": gws, "fix": fixtures}, ensure_ascii=False, separators=(",", ":"))
print(f"Teams: {len(teams)}, GWs: {len(gws)}, Fixtures: {len(fixtures)}")

guesses_file = os.path.join(HERE, "user_guesses.json")
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

    ll_fixtures = []
    for idx, ev in enumerate(espn_events):
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
        if started:
            try: hs = int(home.get("score", "0"))
            except: hs = 0
            try: as_score = int(away.get("score", "0"))
            except: as_score = 0
        matchday = (idx // 10) + 1
        ll_fixtures.append({
            "id": int(ev.get("id", 0)), "e": matchday,
            "h": int(home["team"]["id"]), "a": int(away["team"]["id"]),
            "hs": hs, "as": as_score,
            "fin": finished, "st": started, "ko": ev.get("date", ""),
        })

    max_md = max((f["e"] for f in ll_fixtures), default=38)
    ll_gws = []
    for md in range(1, max_md + 1):
        md_fix = [f for f in ll_fixtures if f["e"] == md]
        all_fin = all(f["fin"] for f in md_fix) if md_fix else False
        is_cur = False
        for f in md_fix:
            if f["st"] and not f["fin"]:
                is_cur = True
                break
        if not is_cur and all_fin:
            next_md_fix = [f for f in ll_fixtures if f["e"] == md + 1]
            if next_md_fix and not any(f["fin"] for f in next_md_fix):
                is_cur = True
        ll_gws.append({"id": md, "fin": all_fin, "cur": is_cur})

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
ll_guesses_file = os.path.join(HERE, "user_guesses_laliga.json")
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

weights_file = os.path.join(HERE, "ai_weights.json")
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
    print("Uploading to GitHub Pages...")
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/index.html"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}

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
