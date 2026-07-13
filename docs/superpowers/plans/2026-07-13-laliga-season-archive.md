# La Liga Season Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore La Liga `2025-26` as a read-only archive beside the default live `2026-27` season, preserving the eight historical guesses and keeping every state, live update, and AI view season-safe.

**Architecture:** Extract La Liga event normalization and catalog validation into a focused Python module, then have the existing static builder fetch both fixed season ranges and embed one validated catalog. Add a small pure JavaScript season runtime for catalog selection, season-qualified guess storage, and legacy migration; the existing HTML template consumes that runtime while retaining the established PL/WC rendering and learning engine.

**Tech Stack:** Python 3 standard library and `requests`, static HTML/CSS/JavaScript, Node `vm` tests, Python `unittest`, Playwright/Chrome visual verification, GitHub Actions atomic publication.

## Global Constraints

- `2026-27` is the default live and editable La Liga season.
- `2025-26` is a complete read-only archive.
- Preserve all eight committed La Liga guesses and map them only to `2025-26`.
- Do not synthesize historical AI predictions, accuracy, trends, squad data, or learning evidence.
- Live overlays and one-hour random auto-fill run only for a non-archived selected season.
- Lifecycle identity remains `league:season:source_fixture_id`; no cross-season state collision is allowed.
- PL and WC behavior and data remain unchanged.
- A missing or malformed required La Liga season aborts before generated files are written.
- One successful build creates at most one generated commit and publication remains atomic.

## File Map

- Create `website/laliga_seasons.py`: pure ESPN event normalization, season date ranges, catalog construction, and fail-closed validation.
- Create `website/season_runtime.js`: pure browser helpers for competition catalogs, selected keys, guess keys, and legacy LL migration.
- Create `tests/test_laliga_seasons.py`: Python unit tests for both season packs, catalog validation, and fixed ranges.
- Create `tests/test_season_runtime.js`: Node tests for season resolution, storage separation, and non-destructive migration.
- Modify `website/update_pl_mobile.py`: fetch two LL packs, embed catalogs/guesses, select current fixtures for learning, and emit season-aware live data.
- Modify `website/pl_mobile_template.html`: generic PL/LL season selector, archive guards, selected-season live data, guesses, and AI filtering.
- Modify `tests/test_learning_integration.py`: learning/history metadata and season-qualified lifecycle regression coverage.
- Modify `tests/test_publish_contract.py`: generated catalog, embedding, fail-closed, and exact publication contract checks.
- Modify `tests/test_learning_runtime.js`: selected LL season AI rows and no synthetic archive fallback.
- Modify `.superpowers/sdd/task7-visual-check.js`: verify both LL seasons on desktop and mobile without overflow or mutation.

---

### Task 1: Build And Validate La Liga Season Packs

**Files:**
- Create: `website/laliga_seasons.py`
- Create: `tests/test_laliga_seasons.py`

**Interfaces:**
- Produces: `LALIGA_SEASON_SPECS`, `laliga_date_range(season) -> str`, `merge_events_by_id(base, overlay) -> list`, `build_laliga_season_pack(events, standings, season, archive) -> dict`, and `build_laliga_catalog(packs, current="2026-27", strict=True) -> dict`.
- Consumes: ESPN event/standings dictionaries already returned by the endpoints used in `website/update_pl_mobile.py`.

- [ ] **Step 1: Write failing date-range and pack tests**

Add tests that build scheduled and completed ESPN-shaped events and assert explicit season identity, scores, archive state, teams, 10-match matchday grouping, and deterministic current matchday:

```python
from datetime import datetime, timedelta, timezone

def espn_event(event_id, index, completed):
    kickoff = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=index)
    home_id = index % 20 + 1
    away_id = (index + 1) % 20 + 1
    state = "post" if completed else "pre"
    return {
        "id": str(event_id),
        "date": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": "2" if completed else None,
                 "team": {"id": str(home_id), "displayName": f"Team {home_id}", "abbreviation": f"T{home_id}"}},
                {"homeAway": "away", "score": "1" if completed else None,
                 "team": {"id": str(away_id), "displayName": f"Team {away_id}", "abbreviation": f"T{away_id}"}},
            ],
            "status": {"type": {"state": state, "completed": completed, "name": "STATUS_FULL_TIME" if completed else "STATUS_SCHEDULED"}},
        }],
    }

def make_pack(season, archive, matches=380, teams=20):
    return {
        "season": season,
        "label": season.replace("-", "/"),
        "archive": archive,
        "teams": {team_id: {"id": team_id, "n": f"Team {team_id}"} for team_id in range(1, teams + 1)},
        "gws": [{"id": matchday, "fin": archive, "cur": matchday == 1 and not archive} for matchday in range(1, 39)],
        "fix": [
            {"id": 700000 + index, "source_fixture_id": 700000 + index, "season": season,
             "e": index // 10 + 1, "h": index % teams + 1, "a": (index + 1) % teams + 1,
             "hs": 2 if archive else None, "as": 1 if archive else None,
             "fin": archive, "st": archive, "ko": "2026-05-01T18:00:00Z", "mn": 90 if archive else 0, "sx": ""}
            for index in range(matches)
        ],
    }

class LaligaSeasonPackTests(unittest.TestCase):
    def test_fixed_ranges_cover_previous_and_current_seasons(self):
        self.assertEqual("20250801-20260630", laliga_date_range("2025-26"))
        self.assertEqual("20260801-20270630", laliga_date_range("2026-27"))

    def test_pack_keeps_explicit_season_and_archive_identity(self):
        events = [espn_event(700000 + index, index, completed=True) for index in range(20)]
        pack = build_laliga_season_pack(events, {}, "2025-26", archive=True)
        self.assertEqual("2025-26", pack["season"])
        self.assertIs(pack["archive"], True)
        self.assertEqual([1, 2], [row["id"] for row in pack["gws"]])
        self.assertTrue(all(row["season"] == "2025-26" for row in pack["fix"]))
        self.assertTrue(all(row["fin"] for row in pack["fix"]))
```

The test fixture must include two competitors with `homeAway`, numeric team IDs, ISO kickoff, score strings, and `status.type.state/completed/name` for every event.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `python -m unittest tests.test_laliga_seasons -v`

Expected: import failure because `website.laliga_seasons` does not exist.

- [ ] **Step 3: Implement the pure pack builder**

Implement these exact public constants and validation rules:

```python
LALIGA_SEASON_SPECS = (
    {"key": "2026-27", "label": "2026/27", "archive": False},
    {"key": "2025-26", "label": "2025/26", "archive": True},
)

def laliga_date_range(season):
    match = re.fullmatch(r"(\d{4})-(\d{2})", str(season or ""))
    if not match:
        raise ValueError("invalid La Liga season")
    start = int(match.group(1))
    if int(match.group(2)) != (start + 1) % 100:
        raise ValueError("non-consecutive La Liga season")
    return f"{start}0801-{start + 1}0630"

def merge_events_by_id(base, overlay):
    replacements = {str(row["id"]): row for row in overlay or [] if row.get("id") is not None}
    seen = set()
    merged = []
    for row in base or []:
        key = str(row.get("id"))
        seen.add(key)
        merged.append(replacements.get(key, row))
    merged.extend(row for row in overlay or [] if str(row.get("id")) not in seen)
    return merged
```

Implement the pack builder with explicit validation and no source-dependent global state:

```python
def _score(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None

def _add_team(teams, raw):
    team_id = int(raw.get("id", 0) or 0)
    if not team_id:
        return
    teams.setdefault(team_id, {
        "id": team_id,
        "n": raw.get("displayName", raw.get("name", "")),
        "s": raw.get("abbreviation", "???"),
        "c": team_id,
        "b": raw.get("logo", ""),
        "sah": 1100, "sdh": 1100, "saa": 1050, "sda": 1050,
    })

def build_laliga_season_pack(events, standings, season, archive):
    laliga_date_range(season)
    teams = {}
    children = standings.get("children", []) if isinstance(standings, dict) else []
    entries = children[0].get("standings", {}).get("entries", []) if children else []
    for entry in entries:
        _add_team(teams, entry.get("team", {}))

    rows = []
    seen = set()
    for event in sorted(events or [], key=lambda row: row.get("date", "")):
        competitions = event.get("competitions", [])
        competitors = competitions[0].get("competitors", []) if competitions else []
        if len(competitors) != 2:
            continue
        home = next((row for row in competitors if row.get("homeAway") == "home"), None)
        away = next((row for row in competitors if row.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        _add_team(teams, home.get("team", {}))
        _add_team(teams, away.get("team", {}))
        fixture_id = int(event.get("id", 0) or 0)
        if not fixture_id or fixture_id in seen:
            raise ValueError("invalid or duplicate La Liga fixture id")
        seen.add(fixture_id)
        status = competitions[0].get("status", {}).get("type", {})
        state = status.get("state", "pre")
        started = state in {"in", "post"}
        finished = bool(status.get("completed", False))
        rows.append({
            "id": fixture_id, "source_fixture_id": fixture_id, "season": season,
            "e": 0, "h": int(home["team"]["id"]), "a": int(away["team"]["id"]),
            "hs": _score(home.get("score")) if started else None,
            "as": _score(away.get("score")) if started else None,
            "fin": finished, "st": started, "ko": event.get("date", ""),
            "mn": 90 if finished else 0, "sx": status.get("name", ""),
        })
    if not rows or not teams:
        raise ValueError(f"empty La Liga season {season}")
    for index, row in enumerate(rows):
        row["e"] = index // 10 + 1
    max_matchday = max(row["e"] for row in rows)
    gws = []
    current_set = False
    for matchday in range(1, max_matchday + 1):
        fixtures = [row for row in rows if row["e"] == matchday]
        live = any(row["st"] and not row["fin"] for row in fixtures)
        finished = bool(fixtures) and all(row["fin"] for row in fixtures)
        current = live or (not current_set and not finished)
        current_set = current_set or current
        gws.append({"id": matchday, "fin": finished, "cur": current})
    if not current_set and gws:
        gws[-1]["cur"] = True
    return {"teams": teams, "gws": gws, "fix": rows, "season": season,
            "label": season.replace("-", "/"), "archive": bool(archive)}

def build_laliga_catalog(packs, current="2026-27", strict=True):
    specs = {row["key"]: row for row in LALIGA_SEASON_SPECS}
    for season in specs:
        if season not in packs:
            raise ValueError(f"missing La Liga season {season}")
        pack = packs[season]
        if pack.get("season") != season or bool(pack.get("archive")) != bool(specs[season]["archive"]):
            raise ValueError(f"invalid La Liga season metadata {season}")
        if strict and (len(pack.get("teams", {})) != 20 or len(pack.get("gws", [])) != 38 or len(pack.get("fix", [])) != 380):
            raise ValueError(f"incomplete La Liga season {season}")
        ids = [row.get("id") for row in pack.get("fix", [])]
        if len(ids) != len(set(ids)) or any(row.get("season") != season for row in pack.get("fix", [])):
            raise ValueError(f"invalid La Liga fixtures {season}")
    if current not in packs or packs[current].get("archive"):
        raise ValueError("invalid current La Liga season")
    return {"current": current,
            "items": [{"key": row["key"], "label": row["label"]} for row in LALIGA_SEASON_SPECS],
            "data": {row["key"]: packs[row["key"]] for row in LALIGA_SEASON_SPECS}}
```

- [ ] **Step 4: Add catalog fail-closed tests**

```python
def test_catalog_requires_both_supported_seasons(self):
    current = make_pack("2026-27", archive=False, matches=380, teams=20)
    with self.assertRaisesRegex(ValueError, "missing La Liga season 2025-26"):
        build_laliga_catalog({"2026-27": current})

def test_catalog_orders_current_before_archive(self):
    packs = {
        "2025-26": make_pack("2025-26", archive=True, matches=380, teams=20),
        "2026-27": make_pack("2026-27", archive=False, matches=380, teams=20),
    }
    catalog = build_laliga_catalog(packs)
    self.assertEqual("2026-27", catalog["current"])
    self.assertEqual(["2026-27", "2025-26"], [row["key"] for row in catalog["items"]])
```

In strict mode validate exactly 20 teams, 38 matchdays, 380 unique fixtures, explicit matching season values, `archive=False` for current, and `archive=True` for previous. Unit tests for small packs call `strict=False`; production always uses the default strict mode.

- [ ] **Step 5: Run tests and commit**

Run: `python -m unittest tests.test_laliga_seasons -v`

Expected: all tests PASS.

Commit:

```bash
git add website/laliga_seasons.py tests/test_laliga_seasons.py
git commit -m "feat: build La Liga season catalogs"
```

---

### Task 2: Integrate Both Seasons Into The Static Build

**Files:**
- Modify: `website/update_pl_mobile.py`
- Modify: `website/pl_mobile_template.html`
- Modify: `tests/test_publish_contract.py`
- Modify: `tests/test_learning_integration.py`

**Interfaces:**
- Consumes: all public functions from Task 1.
- Produces: `EMBEDDED_LL_SEASONS`, `EMBEDDED_GUESSES_LL_SEASONS`, `live.json.ll_seasons`, and current compatibility values `EMBEDDED_LL`/`fix_ll`.

- [ ] **Step 1: Write failing source/build contract tests**

Assert that the builder imports Task 1, fetches both fixed ranges, embeds the catalog and season-qualified guesses, feeds only the catalog current pack into learning, and emits both live season packs:

```python
def test_builder_emits_complete_laliga_season_contract(self):
    source = UPDATE_SOURCE.read_text(encoding="utf-8")
    template = (ROOT / "website" / "pl_mobile_template.html").read_text(encoding="utf-8")
    self.assertIn("build_laliga_catalog", source)
    self.assertIn("LALIGA_SEASON_SPECS", source)
    self.assertIn("/*__DATA_LL_SEASONS__*/", template)
    self.assertIn("/*__GUESSES_LL_SEASONS__*/", template)
    self.assertIn('live_data["ll_seasons"]', source)
    self.assertIn('ll_current_pack = ll_seasons_data[ll_catalog["current"]]', source)
```

Add an integration test around `run_league_learning` proving only `2026-27` fixtures are passed while history metadata exposes both seasons.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m unittest tests.test_publish_contract tests.test_learning_integration -v`

Expected: failures for missing LL catalog placeholders and current-pack variables.

- [ ] **Step 3: Replace the one-season LL block**

Import the Task 1 API in both direct and relative import branches. Replace date-derived `ll_season_key` with iteration over `LALIGA_SEASON_SPECS`:

```python
ll_seasons_data = {}
today_str = datetime.now(ISRAEL_TZ).strftime("%Y%m%d")
for spec in LALIGA_SEASON_SPECS:
    season = spec["key"]
    events = requests.get(
        ESPN_SCOREBOARD,
        params={"dates": laliga_date_range(season), "limit": "1000"},
        headers=hdr,
        timeout=30,
    ).json().get("events", [])
    if not spec["archive"]:
        today_events = requests.get(
            ESPN_SCOREBOARD, params={"dates": today_str}, headers=hdr, timeout=15
        ).json().get("events", [])
        events = merge_events_by_id(events, today_events)
    standings = requests.get(ESPN_STANDINGS, headers=hdr, timeout=20).json()
    ll_seasons_data[season] = build_laliga_season_pack(
        events, standings, season, archive=spec["archive"]
    )

ll_catalog = build_laliga_catalog(ll_seasons_data)
ll_current_pack = ll_seasons_data[ll_catalog["current"]]
ll_fixtures = ll_current_pack["fix"]
ll_teams = ll_current_pack["teams"]
ll_gws = ll_current_pack["gws"]
ll_data = json.dumps(ll_current_pack, ensure_ascii=False, separators=(",", ":"))
ll_seasons_json = json.dumps(ll_catalog, ensure_ascii=False, separators=(",", ":"))
```

Do not catch a required-season error and continue with `ll_data = ""`; propagate it so the transactional generated-write phase never runs.

- [ ] **Step 4: Embed catalog and historical guesses**

Add template placeholders immediately after current LL data/guesses placeholders and replace them in the builder:

```python
ll_guesses_by_season = {"2025-26": ll_guesses, "2026-27": {}}
html = html.replace(
    "/*__DATA_LL_SEASONS__*/",
    "var EMBEDDED_LL_SEASONS=" + ll_seasons_json + ";",
)
html = html.replace(
    "/*__GUESSES_LL_SEASONS__*/",
    "var EMBEDDED_GUESSES_LL_SEASONS="
    + json.dumps(ll_guesses_by_season, ensure_ascii=False, separators=(",", ":"))
    + ";",
)
```

Keep `EMBEDDED_LL` as the current pack for one release of compatibility, but browser code added later must prefer the catalog.

- [ ] **Step 5: Emit season-aware live data and learning metadata**

```python
live_data["ll_seasons"] = {
    key: {"gws": pack["gws"], "fix": pack["fix"], "archive": pack["archive"]}
    for key, pack in ll_seasons_data.items()
}
live_data["fix_ll"] = ll_current_pack["fix"]

learning_history["laliga"]["current_season"] = ll_catalog["current"]
learning_history["laliga"]["available_seasons"] = [
    item["key"] for item in ll_catalog["items"]
]
```

Call `run_league_learning` with only `ll_current_pack["fix"]`; never replay completed archive fixtures into the lifecycle.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
python -m unittest tests.test_laliga_seasons tests.test_publish_contract tests.test_learning_integration -v
```

Expected: all focused Python tests PASS.

Commit:

```bash
git add website/update_pl_mobile.py website/pl_mobile_template.html tests/test_publish_contract.py tests/test_learning_integration.py
git commit -m "feat: embed La Liga season archives"
```

---

### Task 3: Add A Pure Browser Season Runtime

**Files:**
- Create: `website/season_runtime.js`
- Create: `tests/test_season_runtime.js`
- Modify: `website/update_pl_mobile.py`
- Modify: `website/pl_mobile_template.html`
- Modify: `tests/test_publish_contract.py`

**Interfaces:**
- Produces global `SEASON_RUNTIME` with `catalogFor`, `resolveKey`, `packFor`, `guessKey`, and `migrateLegacyLaligaGuesses`.
- Consumes: embedded PL/LL catalogs and the browser `Storage` API.

- [ ] **Step 1: Write failing Node tests**

Load `website/season_runtime.js` in a `vm` context and assert:

```javascript
assert.strictEqual(runtime.resolveKey(llCatalog, '2025-26'), '2025-26');
assert.strictEqual(runtime.resolveKey(llCatalog, 'missing'), '2026-27');
assert.strictEqual(runtime.guessKey('laliga', '2025-26'), 'llg_2025-26');
assert.strictEqual(runtime.guessKey('laliga', '2026-27'), 'llg_2026-27');
assert.strictEqual(runtime.guessKey('pl', '2026-27'), 'plg_2026-27');
assert.strictEqual(runtime.guessKey('wc', ''), 'wcg');

storage.setItem('llg', JSON.stringify({29:{748424:{w:'draw'}}}));
assert.strictEqual(runtime.migrateLegacyLaligaGuesses(storage, '2025-26'), true);
assert.deepStrictEqual(JSON.parse(storage.getItem('llg_2025-26')), {29:{748424:{w:'draw'}}});
assert.ok(storage.getItem('llg'));
```

Also assert that an existing qualified key is never overwritten, an invalid JSON legacy value is ignored, and `2026-27` never receives legacy `llg`.

- [ ] **Step 2: Run Node test and confirm RED**

Run: `node tests/test_season_runtime.js`

Expected: `ENOENT` for missing `website/season_runtime.js`.

- [ ] **Step 3: Implement the runtime**

```javascript
(function(root){
  'use strict';
  function validCatalog(c){return !!(c&&c.data&&c.items&&c.items.length)}
  function catalogFor(league,pl,ll){
    if(league==='pl'&&validCatalog(pl))return pl;
    if(league==='laliga'&&validCatalog(ll))return ll;
    return null;
  }
  function resolveKey(catalog,requested){
    if(!validCatalog(catalog))return '';
    if(requested&&catalog.data[requested])return requested;
    if(catalog.current&&catalog.data[catalog.current])return catalog.current;
    return catalog.items[0]&&catalog.data[catalog.items[0].key]?catalog.items[0].key:'';
  }
  function packFor(catalog,requested){
    var key=resolveKey(catalog,requested);
    return key?catalog.data[key]:null;
  }
  function guessKey(league,season){
    if(league==='laliga')return 'llg_'+season;
    if(league==='pl')return 'plg_'+season;
    return 'wcg';
  }
  function migrateLegacyLaligaGuesses(storage,season){
    if(season!=='2025-26')return false;
    var target=guessKey('laliga',season);
    if(storage.getItem(target)!==null)return false;
    var raw=storage.getItem('llg');
    if(raw===null)return false;
    try{
      var parsed=JSON.parse(raw);
      if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))return false;
      storage.setItem(target,JSON.stringify(parsed));
      return true;
    }catch(e){return false}
  }
  root.SEASON_RUNTIME={catalogFor:catalogFor,resolveKey:resolveKey,packFor:packFor,guessKey:guessKey,migrateLegacyLaligaGuesses:migrateLegacyLaligaGuesses};
})(typeof globalThis!=='undefined'?globalThis:this);
```

- [ ] **Step 4: Embed the runtime before application code**

Add `/*__SEASON_RUNTIME__*/` after the data placeholders and before functions that call it. Read `website/season_runtime.js` in the builder and replace the marker. Add a publication-contract test asserting the marker is absent in generated HTML and `SEASON_RUNTIME` appears before `function init()`.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
node tests/test_season_runtime.js
python -m unittest tests.test_publish_contract -v
```

Expected: both PASS.

Commit:

```bash
git add website/season_runtime.js website/update_pl_mobile.py website/pl_mobile_template.html tests/test_season_runtime.js tests/test_publish_contract.py
git commit -m "feat: add shared season runtime"
```

---

### Task 4: Generalize The Selector And Protect Archived Guesses/Live Data

**Files:**
- Modify: `website/pl_mobile_template.html`
- Modify: `tests/test_season_runtime.js`
- Modify: `tests/test_publish_contract.py`

**Interfaces:**
- Consumes: `SEASON_RUNTIME` and both embedded catalogs from Tasks 2-3.
- Produces: `D.llSeason`, generic `activeCatalog`, `activeSeasonKey`, `seasonData`, `renderSeasonSel`, and `swSeason` behavior.

- [ ] **Step 1: Extend runtime tests for UI-facing state decisions**

Add tests proving LL has two selectable items, WC has no catalog, invalid saved LL selection resolves to current, and archive pack selection returns `archive=true`. Add static contract assertions that `renderSeasonSel` is not gated by `D.league!=="pl"` and `D` contains `llSeason`.

- [ ] **Step 2: Run tests and confirm RED**

Run:

```bash
node tests/test_season_runtime.js
python -m unittest tests.test_publish_contract -v
```

Expected: selector/static contract failures.

- [ ] **Step 3: Replace PL-only selector helpers with generic adapters**

Use these state rules:

```javascript
var D={tm:{},gw:[],fx:[],st:[],cur:1,sel:1,tab:0,sub:0,ct1:null,ct2:null,ct3:null,ct4:null,cn:5,league:'pl',gwl:'GW',arch:false,plSeason:'',llSeason:''};
var LL_SEASONS={current:'2026-27',items:[],data:{}};

function normSeason(raw){
  if(!raw)return{tm:{},gw:[],fx:[],archive:false,label:''};
  return{tm:raw.teams||{},gw:raw.gws||[],fx:raw.fix||[],archive:!!raw.archive,label:raw.label||''};
}
function activeCatalog(lg){return SEASON_RUNTIME.catalogFor(lg,PL_SEASONS,LL_SEASONS)}
function activeSeasonKey(lg){return lg==='pl'?D.plSeason:lg==='laliga'?D.llSeason:''}
function setActiveSeasonKey(lg,key){if(lg==='pl')D.plSeason=key;else if(lg==='laliga')D.llSeason=key}
function seasonStorageKey(lg){return lg==='pl'?'plSeason':'llSeason'}
function seasonData(lg,key){
  var catalog=activeCatalog(lg),resolved=SEASON_RUNTIME.resolveKey(catalog,key);
  setActiveSeasonKey(lg,resolved);
  var raw=SEASON_RUNTIME.packFor(catalog,resolved);
  return raw?normSeason(raw):LDATA[lg];
}
function activeLD(lg){return(lg==='pl'||lg==='laliga')?seasonData(lg,activeSeasonKey(lg)):LDATA[lg]}
```

`renderSeasonSel` uses the active catalog, hides for fewer than two items, sets the selected active key, and changes its title to `Premier League season` or `La Liga season`. `swSeason` accepts PL or LL, stores the resolved key, clears selected round/date, resets `_gMerged`, loads the pack, renders, and calls `fetchLive()` only when `archive` is false.

During `init`, prefer `EMBEDDED_LL_SEASONS`; fall back to a one-item catalog around `EMBEDDED_LL` only for compatibility. Resolve both saved keys before restoring the saved league.

- [ ] **Step 4: Make guesses season-qualified and archives immutable**

Change `_gKey` to call `SEASON_RUNTIME.guessKey`. Before reading `llg_2025-26`, call the non-destructive legacy migration. Choose embedded LL guesses from `EMBEDDED_GUESSES_LL_SEASONS[D.llSeason]`.

Add `if(D.arch)return` guards to `sG`, `setW`, `setS`, `randomFill`, `aiFill`, and `autoFillDueGuesses`. In `rGF`, set `dis=(m.fin||D.arch)?"disabled":""` and show AI/Random fill controls only when `!D.arch`. Existing saved guesses remain visible and scored.

- [ ] **Step 5: Keep archived live data immutable**

Change `fetchLive` to stop for any `D.arch`. In `_fLiveJson`, select LL data from `d.ll_seasons[D.llSeason]`, use `fix_ll` only when the selected key equals `LL_SEASONS.current`, and never call `_applyData` for an archive. Update `_applyData` so selected LL current-season refreshes update `LL_SEASONS.data[D.llSeason]` rather than replacing all `LDATA.laliga` state.

Derive `_fLL` date range from `D.llSeason` and return the embedded pack when `D.arch`; do not derive the season from the wall-clock year.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
node tests/test_season_runtime.js
node tests/test_learning_runtime.js
python -m unittest tests.test_publish_contract -v
```

Expected: all PASS.

Commit:

```bash
git add website/pl_mobile_template.html tests/test_season_runtime.js tests/test_publish_contract.py
git commit -m "feat: select archived La Liga seasons"
```

---

### Task 5: Make AI Analysis Explicitly La Liga Season-Aware

**Files:**
- Modify: `website/pl_mobile_template.html`
- Modify: `tests/test_learning_runtime.js`
- Modify: `tests/test_learning_integration.py`

**Interfaces:**
- Consumes: `D.llSeason`, `D.arch`, and Task 2 learning metadata.
- Produces: selected-season LL rows and a truthful no-history archive state.

- [ ] **Step 1: Write failing AI season tests**

Add Node assertions that selecting LL `2025-26` filters out `2026-27` rows and that an empty archived cohort renders an explicit truthful state. Extract `aiSeasonEmptyState` from the template into the existing `vm` render context:

```javascript
const llRows = vm.runInContext(
  'learningRowsForSeason([{season:"2025-26",gw:29},{season:"2026-27",gw:1}],"2025-26")',
  context
);
assert.deepStrictEqual(JSON.parse(JSON.stringify(llRows)), [{season:'2025-26',gw:29}]);
const emptySource = template.slice(template.indexOf('function aiSeasonEmptyState'), template.indexOf('function rAI'));
vm.runInContext(emptySource, renderContext);
const archiveHtml = renderContext.aiSeasonEmptyState('La Liga', '2025-26', true, []);
assert.match(archiveHtml, /No verified La Liga 2025-26 AI history/);
assert.doesNotMatch(archiveHtml, /100%/);
```

Add a Python integration assertion that LL history reports both available seasons while total evaluated remains zero until genuine lifecycle rows exist.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
node tests/test_learning_runtime.js
python -m unittest tests.test_learning_integration -v
```

Expected: LL selected-season/no-history assertions fail.

- [ ] **Step 3: Select the active LL season in AI rendering**

Replace PL-only selection with:

```javascript
var selectedSeason=isPL
  ?(D.plSeason||lgData.current_season||'')
  :D.league==='laliga'
    ?(D.llSeason||lgData.current_season||'')
    :(lgData.current_season||'');
```

Only use `localLearningRows()` when `!D.arch` and `selectedSeason === lgData.current_season`. When archived LL has no verified rows, render `No verified La Liga <season> AI history` while leaving model status and factor descriptions visible. Do not calculate accuracy from final fixtures or user guesses.

Add and call this helper from `rAI` before local fallback:

```javascript
function aiSeasonEmptyState(leagueName,season,archived,rows){
  if(rows&&rows.length)return'';
  if(archived&&leagueName==='La Liga')return'<div class="emp">No verified La Liga '+season+' AI history</div>';
  return'';
}
```

- [ ] **Step 4: Verify lifecycle identity remains season-safe**

Extend the existing same-source-ID integration test with LL fixtures for `2025-26` and `2026-27`; assert two distinct keys and that only the current eligible fixture can lock/train through the build adapter.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
node tests/test_learning_runtime.js
python -m unittest tests.test_learning_integration tests.test_final_review_fixes -v
```

Expected: all PASS.

Commit:

```bash
git add website/pl_mobile_template.html tests/test_learning_runtime.js tests/test_learning_integration.py
git commit -m "fix: scope La Liga AI history by season"
```

---

### Task 6: Build, Visually Verify, Review, And Publish

**Files:**
- Modify: `.superpowers/sdd/task7-visual-check.js`
- Generate: `index.html`
- Generate: `website/pl_mobile.html`
- Generate: `live.json`
- Generate: `learning_history.json`
- Generate: the existing six AI prediction/model JSON files only when the controlled build changes them.

**Interfaces:**
- Consumes: all completed source tasks.
- Produces: one reviewed generated commit and a fast-forward update to GitHub `main`.

- [ ] **Step 1: Extend the visual script before the build**

For desktop and mobile, select LL and assert the selector offers exactly `2026/27` and `2025/26`. Capture current and archive screenshots. In archive state assert 380 fixtures, visible matchday 29 historical guesses, disabled/no fill controls, no horizontal overflow, no overlapping metrics, and no page errors. Switch back and assert current season restores.

- [ ] **Step 2: Run all source tests**

Run:

```bash
python -m unittest discover -s tests -q
node tests/test_season_runtime.js
node tests/test_learning_runtime.js
git diff --check
```

Expected: all tests PASS and `git diff --check` is silent.

- [ ] **Step 3: Run one controlled no-publish build**

PowerShell:

```powershell
$env:PUBLISH_TO_GITHUB='0'
Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python website\update_pl_mobile.py
```

Expected output includes 380 fixtures for `2025-26`, 380 fixtures for `2026-27`, current `2026-27`, eight archive guesses, and `GitHub upload disabled`.

- [ ] **Step 4: Validate generated state before staging**

Assert:

- `EMBEDDED_LL_SEASONS` contains both complete packs and the current key is `2026-27`;
- `live.json.ll_seasons` contains both seasons, while `fix_ll` equals only the current pack;
- `learning_history.laliga.available_seasons` contains both keys and existing PL/WC totals do not decrease;
- the eight committed guess IDs appear only under LL `2025-26`;
- no `.pending` files remain;
- `index.html` and `website/pl_mobile.html` are byte-identical;
- all inline scripts parse with `node:vm`;
- the six AI JSON files pass existing state validators and generation IDs align.

- [ ] **Step 5: Run desktop/mobile visual verification**

Start a hidden local server on a free port, run `.superpowers/sdd/task7-visual-check.js`, inspect at least LL archive desktop and LL archive mobile screenshots, then stop the exact server PID and verify the port is free.

Expected: no visual-script failures, browser errors, bad numbers, horizontal overflow, or incoherent overlap.

- [ ] **Step 6: Commit generated outputs once**

Stage exactly the generated paths changed by the controlled build and verify `git diff --cached --name-only` before committing:

```bash
git commit -m "build: publish La Liga season archive"
```

The final generated commit must not contain source or test files.

- [ ] **Step 7: Request final code review and fix all findings**

Review `origin/main..HEAD`, including generated data. Require explicit approval for history preservation, season separation, archive immutability, live behavior, AI honesty, visual output, and atomic publication. Any finding returns to RED/GREEN with a focused test before rebuilding.

- [ ] **Step 8: Fetch, fast-forward, and publish**

Run a final `git fetch origin main`. If `origin/main` advanced, rebase source commits, discard only the obsolete generated commit from the unpublished branch, rerun Tasks 6.2-6.7, and create one fresh generated commit. Then:

```bash
git push origin HEAD:main
git ls-remote origin refs/heads/main
git rev-parse HEAD
```

Expected: remote and local full SHAs match exactly, the worktree is clean, and GitHub `main` contains the archived LL season.
