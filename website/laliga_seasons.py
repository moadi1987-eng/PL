import re

try:
    from embedded_cache import load_embedded_json_variable
except ImportError:
    from .embedded_cache import load_embedded_json_variable


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


def _api_football_round(value):
    match = re.search(r"(?:^|\s-\s)(\d{1,2})$", str(value or ""))
    round_id = int(match.group(1)) if match else 0
    if not 1 <= round_id <= 38:
        raise ValueError("invalid API-Football La Liga round")
    return round_id


def _api_football_team(raw):
    raw = raw if isinstance(raw, dict) else {}
    team_id = raw.get("id")
    if not _is_positive_int(team_id):
        raise ValueError("invalid API-Football La Liga team")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("invalid API-Football La Liga team")
    abbreviation = "".join(part[:1] for part in name.split()).upper()[:3] or name[:3].upper()
    return team_id, {
        "id": team_id,
        "n": name,
        "s": abbreviation,
        "c": team_id,
        "b": str(raw.get("logo") or ""),
        "sah": 1100,
        "sdh": 1100,
        "saa": 1050,
        "sda": 1050,
    }


def build_api_football_season_pack(fixtures, season, archive):
    laliga_date_range(season)
    rows = []
    teams = {}
    seen = set()
    finished_statuses = {"FT", "AET", "PEN"}
    scheduled_statuses = {"TBD", "NS", "PST", "CANC", "ABD", "AWD", "WO"}
    for item in fixtures if isinstance(fixtures, list) else ():
        fixture = item.get("fixture") if isinstance(item, dict) else None
        league = item.get("league") if isinstance(item, dict) else None
        raw_teams = item.get("teams") if isinstance(item, dict) else None
        goals = item.get("goals") if isinstance(item, dict) else None
        if not all(isinstance(value, dict) for value in (fixture, league, raw_teams, goals)):
            raise ValueError("invalid API-Football La Liga fixture")
        fixture_id = fixture.get("id")
        if not _is_positive_int(fixture_id) or fixture_id in seen:
            raise ValueError("invalid API-Football La Liga fixture")
        seen.add(fixture_id)
        round_id = _api_football_round(league.get("round"))
        home_id, home_team = _api_football_team(raw_teams.get("home"))
        away_id, away_team = _api_football_team(raw_teams.get("away"))
        if home_id == away_id:
            raise ValueError("invalid API-Football La Liga fixture")
        teams.setdefault(home_id, home_team)
        teams.setdefault(away_id, away_team)
        status = fixture.get("status") if isinstance(fixture.get("status"), dict) else {}
        short = str(status.get("short") or "").upper()
        finished = short in finished_statuses
        started = finished or short not in scheduled_statuses
        home_score = _score(goals.get("home")) if started else None
        away_score = _score(goals.get("away")) if started else None
        if started and (home_score is None or away_score is None):
            raise ValueError("invalid API-Football La Liga score")
        elapsed = _score(status.get("elapsed")) or (90 if finished else 0)
        rows.append({
            "id": fixture_id,
            "source_fixture_id": fixture_id,
            "season": season,
            "e": round_id,
            "h": home_id,
            "a": away_id,
            "hs": home_score,
            "as": away_score,
            "fin": finished,
            "st": started,
            "ko": str(fixture.get("date") or ""),
            "mn": elapsed,
            "sx": short,
        })
    if not rows or not teams:
        raise ValueError(f"empty La Liga season {season}")
    rows.sort(key=lambda row: (row["e"], row["ko"], row["id"]))
    max_matchday = max(row["e"] for row in rows)
    gws = []
    for matchday in range(1, max_matchday + 1):
        matchday_fixtures = [row for row in rows if row["e"] == matchday]
        finished = bool(matchday_fixtures) and all(row["fin"] for row in matchday_fixtures)
        gws.append({"id": matchday, "fin": finished, "cur": False})
    live_matchdays = [row["id"] for row in gws if any(
        fixture["st"] and not fixture["fin"] for fixture in rows if fixture["e"] == row["id"]
    )]
    unfinished_matchdays = [row["id"] for row in gws if not row["fin"]]
    current_id = (live_matchdays or unfinished_matchdays or [gws[-1]["id"]])[0]
    for row in gws:
        row["cur"] = row["id"] == current_id
    return {
        "teams": teams,
        "gws": gws,
        "fix": rows,
        "season": season,
        "label": season.replace("-", "/"),
        "archive": bool(archive),
    }


def _load_cached_laliga_catalog(path):
    catalog = load_embedded_json_variable(path, "EMBEDDED_LL_SEASONS")
    if not isinstance(catalog, dict) or not isinstance(catalog.get("data"), dict):
        raise ValueError("invalid cached La Liga catalog")
    return build_laliga_catalog(catalog["data"], current=catalog.get("current", "2026-27"))


def fetch_laliga_catalog(request_get, *, api_base, api_key, cache_path):
    provider_error = ValueError("API-Football key unavailable")
    if api_key:
        try:
            packs = {}
            for spec in LALIGA_SEASON_SPECS:
                response = request_get(
                    f"{api_base}/fixtures",
                    params={"league": 140, "season": int(spec["key"][:4])},
                    headers={"x-apisports-key": api_key},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("errors"):
                    raise ValueError(f"API-Football errors: {payload['errors']}")
                if not isinstance(payload, dict) or not isinstance(payload.get("response"), list):
                    raise ValueError("invalid API-Football La Liga response")
                packs[spec["key"]] = build_api_football_season_pack(
                    payload["response"], spec["key"], archive=spec["archive"]
                )
            return build_laliga_catalog(packs), "api-football"
        except Exception as exc:
            provider_error = exc
    try:
        reason = re.sub(r"\s+", " ", str(provider_error)).strip() or type(provider_error).__name__
        if api_key:
            reason = reason.replace(api_key, "***")
        return _load_cached_laliga_catalog(cache_path), f"cache ({reason[:240]})"
    except Exception as cache_error:
        raise RuntimeError(
            f"La Liga providers unavailable ({provider_error}); cache invalid ({cache_error})"
        ) from provider_error


def _score(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _is_positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _event_fixture_id(event):
    raw_id = event.get("id") if isinstance(event, dict) else None
    if isinstance(raw_id, bool):
        raise ValueError("invalid La Liga fixture id")
    if isinstance(raw_id, int):
        fixture_id = raw_id
    elif isinstance(raw_id, str) and re.fullmatch(r"[1-9]\d*", raw_id):
        fixture_id = int(raw_id)
    else:
        raise ValueError("invalid La Liga fixture id")
    if fixture_id <= 0:
        raise ValueError("invalid La Liga fixture id")
    return fixture_id


def _add_team(teams, raw):
    team_id = int(raw.get("id", 0) or 0)
    if not team_id:
        return
    logos = raw.get("logos", [])
    first_logo = logos[0].get("href", "") if logos and isinstance(logos[0], dict) else ""
    teams.setdefault(team_id, {
        "id": team_id,
        "n": raw.get("displayName", raw.get("name", "")),
        "s": raw.get("abbreviation", "???"),
        "c": team_id,
        "b": raw.get("logo", "") or first_logo,
        "sah": 1100,
        "sdh": 1100,
        "saa": 1050,
        "sda": 1050,
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
    for event in sorted(
            events or [],
            key=lambda row: (row.get("date", ""), _event_fixture_id(row))):
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
        try:
            fixture_id = _event_fixture_id(event)
            home_id = int(home["team"]["id"])
            away_id = int(away["team"]["id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid La Liga fixture")
        if not fixture_id or fixture_id in seen:
            raise ValueError("invalid or duplicate La Liga fixture id")
        seen.add(fixture_id)
        status = competitions[0].get("status", {}).get("type", {})
        state = status.get("state", "pre")
        started = state in {"in", "post"}
        finished = bool(status.get("completed", False))
        rows.append({
            "id": fixture_id,
            "source_fixture_id": fixture_id,
            "season": season,
            "e": 0,
            "h": home_id,
            "a": away_id,
            "hs": _score(home.get("score")) if started else None,
            "as": _score(away.get("score")) if started else None,
            "fin": finished,
            "st": started,
            "ko": event.get("date", ""),
            "mn": 90 if finished else 0,
            "sx": status.get("name", ""),
        })
    if not rows or not teams:
        raise ValueError(f"empty La Liga season {season}")
    for index, row in enumerate(rows):
        row["e"] = index // 10 + 1
    max_matchday = max(row["e"] for row in rows)
    gws = []
    for matchday in range(1, max_matchday + 1):
        fixtures = [row for row in rows if row["e"] == matchday]
        finished = bool(fixtures) and all(row["fin"] for row in fixtures)
        gws.append({"id": matchday, "fin": finished, "cur": False})
    live_matchdays = [row["id"] for row in gws if any(
        fixture["st"] and not fixture["fin"]
        for fixture in rows
        if fixture["e"] == row["id"]
    )]
    unfinished_matchdays = [row["id"] for row in gws if not row["fin"]]
    current_id = (live_matchdays or unfinished_matchdays or [gws[-1]["id"]])[0]
    for row in gws:
        row["cur"] = row["id"] == current_id
    return {
        "teams": teams,
        "gws": gws,
        "fix": rows,
        "season": season,
        "label": season.replace("-", "/"),
        "archive": bool(archive),
    }


def build_laliga_catalog(packs, current="2026-27", strict=True):
    specs = {row["key"]: row for row in LALIGA_SEASON_SPECS}
    packs = packs or {}
    for season in specs:
        if season not in packs:
            raise ValueError(f"missing La Liga season {season}")
        pack = packs[season]
        if (pack.get("season") != season or type(pack.get("archive")) is not bool
                or pack.get("archive") is not specs[season]["archive"]):
            raise ValueError(f"invalid La Liga season metadata {season}")
        if strict and (len(pack.get("teams", {})) != 20 or len(pack.get("gws", [])) != 38 or len(pack.get("fix", [])) != 380):
            raise ValueError(f"incomplete La Liga season {season}")
        if strict:
            gws = pack.get("gws", [])
            if (any(not isinstance(row, dict) or type(row.get("cur")) is not bool for row in gws)
                    or sum(row["cur"] for row in gws) != 1):
                raise ValueError(f"invalid La Liga current matchdays {season}")
            team_ids = set()
            for key, team in pack.get("teams", {}).items():
                if not isinstance(team, dict) or not _is_positive_int(team.get("id")):
                    raise ValueError(f"invalid La Liga teams {season}")
                try:
                    key_id = int(key)
                except (TypeError, ValueError):
                    raise ValueError(f"invalid La Liga teams {season}")
                if key_id != team["id"] or team["id"] in team_ids:
                    raise ValueError(f"invalid La Liga teams {season}")
                team_ids.add(team["id"])
            fixture_ids = set()
            for fixture in pack.get("fix", []):
                fixture_id = fixture.get("id") if isinstance(fixture, dict) else None
                source_id = fixture.get("source_fixture_id") if isinstance(fixture, dict) else None
                home_id = fixture.get("h") if isinstance(fixture, dict) else None
                away_id = fixture.get("a") if isinstance(fixture, dict) else None
                if (not _is_positive_int(fixture_id) or not _is_positive_int(source_id)
                        or fixture_id != source_id or fixture_id in fixture_ids
                        or not _is_positive_int(home_id) or not _is_positive_int(away_id)
                        or home_id == away_id or home_id not in team_ids or away_id not in team_ids
                        or fixture.get("season") != season):
                    raise ValueError(f"invalid La Liga fixtures {season}")
                fixture_ids.add(fixture_id)
        else:
            ids = [row.get("id") for row in pack.get("fix", [])]
            if len(ids) != len(set(ids)) or any(row.get("season") != season for row in pack.get("fix", [])):
                raise ValueError(f"invalid La Liga fixtures {season}")
    if current not in packs or current not in specs or packs[current].get("archive"):
        raise ValueError("invalid current La Liga season")
    return {
        "current": current,
        "items": [{"key": row["key"], "label": row["label"]} for row in LALIGA_SEASON_SPECS],
        "data": {row["key"]: packs[row["key"]] for row in LALIGA_SEASON_SPECS},
    }
