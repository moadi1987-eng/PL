import re


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
        try:
            fixture_id = int(event.get("id", 0) or 0)
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
