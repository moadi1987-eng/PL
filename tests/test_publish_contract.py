import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
UPDATE_SOURCE = ROOT / "website" / "update_pl_mobile.py"
WORKFLOW = ROOT / ".github" / "workflows" / "update-dashboard.yml"


class PublishContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = UPDATE_SOURCE.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_contents_api_is_opt_in_and_local_only(self):
        self.assertIn(
            'PUBLISH_TO_GITHUB = os.environ.get("PUBLISH_TO_GITHUB", "") == "1"',
            self.source,
        )
        self.assertIn("if PUBLISH_TO_GITHUB and not IS_CI:", self.source)

        tree = ast.parse(self.source)
        upload_calls = []
        guarded_calls = []

        def walk(node, guards=()):
            if isinstance(node, list):
                for child in node:
                    walk(child, guards)
                return
            if isinstance(node, ast.If):
                condition = ast.unparse(node.test).replace("(", "").replace(")", "")
                walk(node.body, guards + (condition,))
                for child in node.orelse:
                    walk(child, guards)
                return
            if isinstance(node, ast.Call):
                function = node.func
                is_put = (
                    isinstance(function, ast.Attribute)
                    and function.attr == "put"
                    and isinstance(function.value, ast.Name)
                    and function.value.id == "requests"
                )
                if is_put:
                    upload_calls.append(node)
                    if "PUBLISH_TO_GITHUB and not IS_CI" in guards:
                        guarded_calls.append(node)
            for child in ast.iter_child_nodes(node):
                walk(child, guards)

        walk(tree)
        self.assertGreater(len(upload_calls), 0)
        self.assertEqual(
            len(upload_calls),
            len(guarded_calls),
            "every Contents API upload call must be under the local-only guard",
        )

    def test_build_step_does_not_receive_a_github_token_or_publish_flag(self):
        build_step = self._step_named("Build dashboard")
        self.assertNotRegex(build_step, r"(?im)^\s*GITHUB_TOKEN\s*:")
        self.assertNotRegex(build_step, r"(?im)^\s*PUBLISH_TO_GITHUB\s*:")

    def test_workflow_stages_every_generated_file_once(self):
        required = [
            "index.html",
            "website/pl_mobile.html",
            "live.json",
            "learning_history.json",
            "ai_predictions.json",
            "ai_weights.json",
            "ai_predictions_laliga.json",
            "ai_weights_laliga.json",
            "ai_predictions_wc.json",
            "ai_weights_wc.json",
        ]
        add_commands = re.findall(
            r"(?m)^\s*git add\b(?:[^\n\\]|\\\s*\n\s*)*",
            self.workflow,
        )
        self.assertEqual(1, len(add_commands), "workflow must have one coherent git add")
        staged = re.sub(r"\\\s*\n\s*", " ", add_commands[0])
        for name in required:
            self.assertIn(name, staged)

    def test_workflow_has_one_commit_and_preserves_atomic_push_retry(self):
        self.assertEqual(1, len(re.findall(r"(?m)^\s*git commit\b", self.workflow)))
        self.assertIn("group: update-dashboard", self.workflow)
        self.assertIn("cancel-in-progress: true", self.workflow)
        self.assertGreaterEqual(
            self.workflow.count("git pull --rebase --autostash origin main"), 2
        )
        self.assertRegex(
            self.workflow,
            r"git push origin main\s*\|\|\s*\(git pull --rebase --autostash origin main",
        )

    def test_workflow_has_no_alternate_upload_path(self):
        self.assertNotIn("api.github.com/repos", self.workflow)
        self.assertNotRegex(self.workflow, r"(?im)^\s*uses:\s*actions/upload-artifact@")
        self.assertNotRegex(self.workflow, r"(?im)^\s*(?:gh\s+api|curl\b).*contents/")

    def _step_named(self, name):
        match = re.search(
            rf"(?ms)^\s*- name:\s*{re.escape(name)}\s*$\n(?P<body>.*?)(?=^\s*- name:|\Z)",
            self.workflow,
        )
        self.assertIsNotNone(match, f"workflow step not found: {name}")
        return match.group("body")


if __name__ == "__main__":
    unittest.main()
