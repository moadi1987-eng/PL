import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from website import embedded_cache
except ImportError:
    embedded_cache = None


def world_cup_archive():
    teams = {str(team_id): {"id": team_id, "n": f"Team {team_id}"} for team_id in range(1, 49)}
    fixtures = [
        {
            "id": 760000 + index,
            "e": index % 33 + 1,
            "h": index % 48 + 1,
            "a": (index + 1) % 48 + 1,
            "hs": 1,
            "as": 0,
            "fin": True,
            "st": True,
        }
        for index in range(104)
    ]
    return {
        "teams": teams,
        "gws": [{"id": day, "fin": True, "cur": day == 33} for day in range(1, 34)],
        "fix": fixtures,
        "archive": True,
    }


class EmbeddedCacheTests(unittest.TestCase):
    def test_world_cup_fallback_loads_only_complete_archived_tournament(self):
        self.assertIsNotNone(embedded_cache, "embedded dashboard cache loader is missing")
        archive = world_cup_archive()
        with TemporaryDirectory() as root:
            path = Path(root) / "dashboard.html"
            path.write_text(
                "<script>var EMBEDDED_WC="
                + json.dumps(archive, separators=(",", ":"))
                + ";var NEXT={};</script>",
                encoding="utf-8",
            )
            loaded = embedded_cache.load_world_cup_archive(path)

        self.assertEqual(48, len(loaded["teams"]))
        self.assertEqual(33, len(loaded["gws"]))
        self.assertEqual(104, len(loaded["fix"]))
        self.assertTrue(all(row["fin"] for row in loaded["fix"]))

    def test_world_cup_fallback_rejects_incomplete_cache(self):
        self.assertIsNotNone(embedded_cache, "embedded dashboard cache loader is missing")
        archive = world_cup_archive()
        archive["fix"][0]["fin"] = False
        with TemporaryDirectory() as root:
            path = Path(root) / "dashboard.html"
            path.write_text(
                "var EMBEDDED_WC=" + json.dumps(archive, separators=(",", ":")) + ";",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "invalid cached World Cup archive"):
                embedded_cache.load_world_cup_archive(path)


if __name__ == "__main__":
    unittest.main()
