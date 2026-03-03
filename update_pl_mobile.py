"""
Run this to update pl_mobile.html with fresh PL data.
The file syncs to your phone via OneDrive and works offline.

Usage:  python update_pl_mobile.py
"""
import json, requests, os

FPL = "https://fantasy.premierleague.com/api"
BADGE = "https://resources.premierleague.com/premierleague/badges/70/t{}.png"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pl_mobile.html")
TPL = os.path.join(HERE, "pl_mobile_template.html")

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

html = open(TPL, "r", encoding="utf-8").read()
html = html.replace("/*__DATA__*/", "var EMBEDDED=" + data + ";")
html = html.replace("/*__GUESSES__*/", "var EMBEDDED_GUESSES=" + guesses_json + ";")

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Updated: {OUT}")
print("Open pl_mobile.html in Chrome on your PC or phone!")
