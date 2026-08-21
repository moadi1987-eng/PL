import json


def load_embedded_json_variable(path, variable):
    marker = f"var {variable}="
    with open(path, "r", encoding="utf-8") as handle:
        source = handle.read()
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"missing cached {variable}")
    value, _ = json.JSONDecoder().raw_decode(source[start + len(marker):])
    return value


def load_world_cup_archive(path):
    archive = load_embedded_json_variable(path, "EMBEDDED_WC")
    teams = archive.get("teams") if isinstance(archive, dict) else None
    gameweeks = archive.get("gws") if isinstance(archive, dict) else None
    fixtures = archive.get("fix") if isinstance(archive, dict) else None
    valid = (
        isinstance(teams, dict)
        and len(teams) == 48
        and isinstance(gameweeks, list)
        and len(gameweeks) == 33
        and isinstance(fixtures, list)
        and len(fixtures) == 104
        and archive.get("archive") is True
        and all(isinstance(row, dict) and row.get("fin") is True for row in fixtures)
    )
    if not valid:
        raise ValueError("invalid cached World Cup archive")
    return archive
