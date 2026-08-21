import unittest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import website.laliga_seasons as laliga

from website.laliga_seasons import (
    build_laliga_catalog,
    build_laliga_season_pack,
    laliga_date_range,
    merge_events_by_id,
)


def espn_event(event_id, index, completed, started=False):
    kickoff = datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(days=index)
    home_id = index % 20 + 1
    away_id = (index + 1) % 20 + 1
    state = "post" if completed else "in" if started else "pre"
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
            "status": {"type": {"state": state, "completed": completed,
                                  "name": "STATUS_FULL_TIME" if completed else "STATUS_SCHEDULED"}},
        }],
    }


def make_pack(season, archive, matches=380, teams=20):
    return {
        "season": season,
        "label": season.replace("-", "/"),
        "archive": archive,
        "teams": {team_id: {"id": team_id, "n": f"Team {team_id}"} for team_id in range(1, teams + 1)},
        "gws": [{"id": matchday, "fin": archive,
                 "cur": matchday == (38 if archive else 1)} for matchday in range(1, 39)],
        "fix": [
            {"id": 700000 + index, "source_fixture_id": 700000 + index, "season": season,
             "e": index // 10 + 1, "h": index % teams + 1, "a": (index + 1) % teams + 1,
             "hs": 2 if archive else None, "as": 1 if archive else None,
             "fin": archive, "st": archive, "ko": "2026-05-01T18:00:00Z", "mn": 90 if archive else 0, "sx": ""}
            for index in range(matches)
        ],
    }


def api_football_fixture(fixture_id, round_id, status="NS", home_score=None, away_score=None):
    return {
        "fixture": {
            "id": fixture_id,
            "date": f"2026-08-{14 + round_id:02d}T19:00:00+00:00",
            "status": {"short": status, "elapsed": 90 if status == "FT" else None},
        },
        "league": {"id": 140, "season": 2026, "round": f"Regular Season - {round_id}"},
        "teams": {
            "home": {"id": 1, "name": "Home FC", "logo": "home.png"},
            "away": {"id": 2, "name": "Away FC", "logo": "away.png"},
        },
        "goals": {"home": home_score, "away": away_score},
    }


def official_page(round_id, statuses=None):
    statuses = statuses or ["PreMatch"] * 10
    matches = []
    for offset, status in enumerate(statuses):
        home_id = offset * 2 + 1
        away_id = offset * 2 + 2
        matches.append({
            "id": 100000 + round_id * 100 + offset,
            "date": f"2026-08-{min(28, 10 + round_id):02d}T19:00:00+00:00",
            "home_score": 2 if status != "PreMatch" else None,
            "away_score": 1 if status != "PreMatch" else None,
            "status": status,
            "home_team": {
                "id": home_id, "nickname": f"Team {home_id}", "shortname": f"T{home_id}",
                "shield": {"url": f"https://assets.laliga.com/{home_id}.png"},
            },
            "away_team": {
                "id": away_id, "nickname": f"Team {away_id}", "shortname": f"T{away_id}",
                "shield": {"url": f"https://assets.laliga.com/{away_id}.png"},
            },
        })
    return {
        "statusCode": 200,
        "season": "2026",
        "gameweek": {"id": 10000 + round_id, "week": round_id},
        "currentGameweek": {"id": 10002, "week": 2},
        "gameweekList": [{"id": 10000 + value, "week": value} for value in range(1, 39)],
        "matches": matches,
    }


def official_response(page):
    payload = {"props": {"pageProps": page}}

    class Response:
        text = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"

        def raise_for_status(self):
            return None

        def json(self):
            raise AssertionError("official La Liga pages must not be parsed as API JSON")

    return Response()


class LaligaSeasonPackTests(unittest.TestCase):
    def test_official_pack_preserves_matchday_scores_status_and_team_assets(self):
        pack = laliga.build_laliga_official_season_pack([
            official_page(1, ["FullTime"] * 10),
            official_page(2, ["FirstHalf"] + ["PreMatch"] * 9),
        ], "2026-27", archive=False)

        self.assertEqual([1, 2], [row["id"] for row in pack["gws"]])
        self.assertEqual((2, 1, True, True), tuple(pack["fix"][0][key] for key in ("hs", "as", "fin", "st")))
        self.assertEqual((2, 1, False, True, "FirstHalf"), tuple(pack["fix"][10][key] for key in ("hs", "as", "fin", "st", "sx")))
        self.assertEqual("https://assets.laliga.com/1.png", pack["teams"][1]["b"])
        self.assertEqual("laliga.com", pack["provider"])

    def test_fetch_catalog_bootstraps_current_season_from_official_laliga(self):
        cached = build_laliga_catalog({
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False),
        })
        requested = []

        def get(url, **kwargs):
            requested.append(url)
            if "laliga.com" not in url:
                raise AssertionError("API-Football should not be called after an official success")
            match = __import__("re").search(r"gameweek-(\d+)$", url)
            return official_response(official_page(int(match.group(1))))

        with TemporaryDirectory() as root:
            cache_path = Path(root) / "dashboard.html"
            cache_path.write_text(
                "<script>\nvar EMBEDDED_LL_SEASONS=" + json.dumps(cached, separators=(",", ":"))
                + ";\nvar NEXT_VALUE={};\n</script>", encoding="utf-8",
            )
            catalog, source = laliga.fetch_laliga_catalog(
                get, api_base="https://api.invalid", api_key="", cache_path=str(cache_path)
            )

        self.assertEqual("laliga.com", source)
        self.assertEqual(38, len(requested))
        self.assertEqual(380, len(catalog["data"]["2026-27"]["fix"]))
        self.assertEqual("laliga.com", catalog["data"]["2026-27"]["provider"])
        self.assertEqual(380, len(catalog["data"]["2025-26"]["fix"]))

    def test_fetch_catalog_refreshes_only_nearby_rounds_after_official_bootstrap(self):
        current = laliga.build_laliga_official_season_pack(
            [official_page(round_id) for round_id in range(1, 39)], "2026-27", archive=False
        )
        cached = build_laliga_catalog({
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": current,
        })
        requested = []

        def get(url, **kwargs):
            requested.append(url)
            round_match = __import__("re").search(r"gameweek-(\d+)$", url)
            round_id = int(round_match.group(1)) if round_match else 2
            statuses = ["FullTime"] * 10 if round_id == 1 else None
            return official_response(official_page(round_id, statuses))

        with TemporaryDirectory() as root:
            cache_path = Path(root) / "dashboard.html"
            cache_path.write_text(
                "<script>\nvar EMBEDDED_LL_SEASONS=" + json.dumps(cached, separators=(",", ":"))
                + ";\nvar NEXT_VALUE={};\n</script>", encoding="utf-8",
            )
            catalog, source = laliga.fetch_laliga_catalog(
                get, api_base="https://api.invalid", api_key="", cache_path=str(cache_path)
            )

        self.assertEqual("laliga.com", source)
        self.assertEqual(3, len(requested))
        self.assertTrue(all(row["fin"] for row in catalog["data"]["2026-27"]["fix"] if row["e"] == 1))

    def test_api_football_pack_preserves_round_live_status_and_score(self):
        builder = getattr(laliga, "build_api_football_season_pack", None)
        self.assertTrue(callable(builder), "API-Football La Liga adapter is missing")

        pack = builder(
            [
                api_football_fixture(900001, 1, status="FT", home_score=2, away_score=1),
                api_football_fixture(900002, 2, status="1H", home_score=1, away_score=0),
            ],
            "2026-27",
            archive=False,
        )

        self.assertEqual([1, 2], [row["e"] for row in pack["fix"]])
        self.assertEqual((2, 1, True, True, 90), tuple(pack["fix"][0][key] for key in ("hs", "as", "fin", "st", "mn")))
        self.assertEqual((1, 0, False, True), tuple(pack["fix"][1][key] for key in ("hs", "as", "fin", "st")))
        self.assertEqual([False, True], [row["cur"] for row in pack["gws"]])

    def test_fetch_catalog_uses_last_valid_cache_when_provider_returns_non_json(self):
        fetcher = getattr(laliga, "fetch_laliga_catalog", None)
        self.assertTrue(callable(fetcher), "resilient La Liga catalog fetcher is missing")
        catalog = build_laliga_catalog({
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False),
        })

        class NonJsonResponse:
            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("not JSON")

        with TemporaryDirectory() as root:
            cache_path = Path(root) / "dashboard.html"
            cache_path.write_text(
                "<script>\nvar EMBEDDED_LL_SEASONS="
                + __import__("json").dumps(catalog, separators=(",", ":"))
                + ";\nvar NEXT_VALUE={};\n</script>",
                encoding="utf-8",
            )
            loaded, source = fetcher(
                lambda *args, **kwargs: NonJsonResponse(),
                api_base="https://example.invalid",
                api_key="available",
                cache_path=str(cache_path),
            )

        self.assertTrue(source.startswith("cache ("))
        self.assertIn("not JSON", source)
        self.assertEqual(380, len(loaded["data"]["2026-27"]["fix"]))
        self.assertEqual("2026-27", loaded["current"])

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

    def test_pack_normalizes_scores_and_deterministic_current_matchday(self):
        events = [espn_event(700000 + index, index, completed=index < 10) for index in range(20)]
        pack = build_laliga_season_pack(events, {}, "2026-27", archive=False)
        self.assertEqual([1, 2], [row["id"] for row in pack["gws"]])
        self.assertEqual([True, False], [row["fin"] for row in pack["gws"]])
        self.assertEqual([False, True], [row["cur"] for row in pack["gws"]])
        self.assertEqual((2, 1), (pack["fix"][0]["hs"], pack["fix"][0]["as"]))
        self.assertEqual((None, None), (pack["fix"][10]["hs"], pack["fix"][10]["as"]))
        self.assertEqual(20, len(pack["teams"]))

    def test_pack_marks_only_earliest_live_matchday_current(self):
        events = [espn_event(700000 + index, index, completed=index < 10) for index in range(30)]
        for event in events[10:]:
            event["competitions"][0]["status"]["type"]["state"] = "in"
        pack = build_laliga_season_pack(events, {}, "2026-27", archive=False)
        self.assertEqual([False, True, False], [row["cur"] for row in pack["gws"]])

    def test_pack_falls_back_to_earliest_unfinished_or_final_matchday(self):
        unfinished = [espn_event(700000 + index, index, completed=index < 10) for index in range(20)]
        unfinished_pack = build_laliga_season_pack(unfinished, {}, "2026-27", archive=False)
        self.assertEqual([False, True], [row["cur"] for row in unfinished_pack["gws"]])

        completed = [espn_event(700000 + index, index, completed=True) for index in range(20)]
        completed_pack = build_laliga_season_pack(completed, {}, "2025-26", archive=True)
        self.assertEqual([False, True], [row["cur"] for row in completed_pack["gws"]])

    def test_equal_kickoff_order_is_stable_across_shuffled_input(self):
        events = [espn_event(700000 + index, index, completed=index < 10) for index in range(20)]
        for event in events:
            event["date"] = "2026-05-01T18:00:00Z"
        shuffled = events[10:15] + events[:5] + events[15:] + events[5:10]

        expected = build_laliga_season_pack(events, {}, "2026-27", archive=False)
        actual = build_laliga_season_pack(shuffled, {}, "2026-27", archive=False)

        self.assertEqual(
            [(row["id"], row["e"]) for row in expected["fix"]],
            [(row["id"], row["e"]) for row in actual["fix"]],
        )
        self.assertEqual(expected["gws"], actual["gws"])

    def test_equal_kickoff_sort_fails_closed_for_malformed_fixture_id(self):
        events = [espn_event(700001, 0, completed=True), espn_event("invalid", 1, completed=True)]
        for event in events:
            event["date"] = "2026-05-01T18:00:00Z"
        with self.assertRaisesRegex(ValueError, "invalid La Liga fixture"):
            build_laliga_season_pack(events, {}, "2025-26", archive=True)

    def test_pack_accepts_both_espn_logo_shapes(self):
        standings = {
            "children": [{"standings": {"entries": [
                {"team": {"id": "1", "displayName": "Team 1", "logo": "direct.png"}},
                {"team": {"id": "2", "displayName": "Team 2", "logos": [{"href": "nested.png"}]}},
            ]}}]
        }
        pack = build_laliga_season_pack([espn_event(700001, 0, completed=True)], standings, "2025-26", archive=True)
        self.assertEqual("direct.png", pack["teams"][1]["b"])
        self.assertEqual("nested.png", pack["teams"][2]["b"])

    def test_merge_events_replaces_by_id_and_preserves_order(self):
        base = [{"id": "1", "value": "old"}, {"id": "2", "value": "keep"}]
        overlay = [{"id": "1", "value": "new"}, {"id": "3", "value": "append"}]
        self.assertEqual(
            [{"id": "1", "value": "new"}, {"id": "2", "value": "keep"}, {"id": "3", "value": "append"}],
            merge_events_by_id(base, overlay),
        )

    def test_catalog_requires_both_supported_seasons(self):
        current = make_pack("2026-27", archive=False)
        with self.assertRaisesRegex(ValueError, "missing La Liga season 2025-26"):
            build_laliga_catalog({"2026-27": current})

    def test_catalog_orders_current_before_archive(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False),
        }
        catalog = build_laliga_catalog(packs)
        self.assertEqual("2026-27", catalog["current"])
        self.assertEqual(["2026-27", "2025-26"], [row["key"] for row in catalog["items"]])

    def test_catalog_strict_mode_rejects_incomplete_pack(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False, matches=379),
        }
        with self.assertRaisesRegex(ValueError, "incomplete La Liga season 2026-27"):
            build_laliga_catalog(packs)

    def test_catalog_rejects_wrong_archive_metadata_and_fixture_season(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=False),
            "2026-27": make_pack("2026-27", archive=False),
        }
        with self.assertRaisesRegex(ValueError, "invalid La Liga season metadata 2025-26"):
            build_laliga_catalog(packs)
        packs["2025-26"] = make_pack("2025-26", archive=True)
        packs["2025-26"]["fix"][0]["season"] = "2026-27"
        with self.assertRaisesRegex(ValueError, "invalid La Liga fixtures 2025-26"):
            build_laliga_catalog(packs)

    def test_catalog_strict_mode_rejects_invalid_team_identity(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False),
        }
        packs["2026-27"]["teams"][1]["id"] = -9
        with self.assertRaisesRegex(ValueError, "invalid La Liga teams 2026-27"):
            build_laliga_catalog(packs)

    def test_catalog_strict_mode_rejects_dangling_or_same_fixture_teams(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False),
        }
        packs["2026-27"]["fix"][0]["h"] = 999999
        with self.assertRaisesRegex(ValueError, "invalid La Liga fixtures 2026-27"):
            build_laliga_catalog(packs)
        packs["2026-27"] = make_pack("2026-27", archive=False)
        packs["2026-27"]["fix"][0]["a"] = packs["2026-27"]["fix"][0]["h"]
        with self.assertRaisesRegex(ValueError, "invalid La Liga fixtures 2026-27"):
            build_laliga_catalog(packs)

    def test_catalog_strict_mode_requires_stable_positive_fixture_ids(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=True),
            "2026-27": make_pack("2026-27", archive=False),
        }
        packs["2026-27"]["fix"][0]["id"] = -1
        with self.assertRaisesRegex(ValueError, "invalid La Liga fixtures 2026-27"):
            build_laliga_catalog(packs)

    def test_catalog_rejects_non_boolean_archive_metadata(self):
        for season, expected_archive in (("2025-26", True), ("2026-27", False)):
            for impostor in (1, 0, "false", None):
                with self.subTest(season=season, impostor=impostor):
                    packs = {
                        "2025-26": make_pack("2025-26", archive=True),
                        "2026-27": make_pack("2026-27", archive=False),
                    }
                    packs[season]["archive"] = impostor
                    if impostor is expected_archive:
                        continue
                    with self.assertRaisesRegex(ValueError, f"invalid La Liga season metadata {season}"):
                        build_laliga_catalog(packs)

    def test_catalog_strict_mode_requires_exactly_one_boolean_current_matchday(self):
        cases = {
            "missing": lambda pack: [row.update(cur=False) for row in pack["gws"]],
            "multiple": lambda pack: pack["gws"][1].update(cur=True),
            "non_boolean": lambda pack: pack["gws"][0].update(cur=1),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                packs = {
                    "2025-26": make_pack("2025-26", archive=True),
                    "2026-27": make_pack("2026-27", archive=False),
                }
                mutate(packs["2026-27"])
                with self.assertRaisesRegex(ValueError, "invalid La Liga current matchdays 2026-27"):
                    build_laliga_catalog(packs)

    def test_catalog_non_strict_mode_preserves_small_pack_behavior(self):
        packs = {
            "2025-26": make_pack("2025-26", archive=True, matches=2, teams=2),
            "2026-27": make_pack("2026-27", archive=False, matches=2, teams=2),
        }
        packs["2026-27"]["gws"][0]["cur"] = 1
        packs["2026-27"]["gws"][1]["cur"] = True
        catalog = build_laliga_catalog(packs, strict=False)
        self.assertEqual("2026-27", catalog["current"])
        packs["2026-27"] = make_pack("2026-27", archive=False)
        packs["2026-27"]["fix"][0]["source_fixture_id"] = 999999
        with self.assertRaisesRegex(ValueError, "invalid La Liga fixtures 2026-27"):
            build_laliga_catalog(packs)


if __name__ == "__main__":
    unittest.main()
