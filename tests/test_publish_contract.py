import ast
import re
import shlex
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
UPDATE_SOURCE = ROOT / "website" / "update_pl_mobile.py"
WORKFLOW = ROOT / ".github" / "workflows" / "update-dashboard.yml"
UPLOAD_METHODS = frozenset({"PUT", "POST", "PATCH", "DELETE"})
FORBIDDEN_BUILD_ENV = frozenset({"GITHUB_TOKEN", "GH_TOKEN", "PUBLISH_TO_GITHUB"})


def _constant_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _request_method(call):
    if call.args:
        return _constant_string(call.args[0])
    for keyword in call.keywords:
        if keyword.arg == "method":
            return _constant_string(keyword.value)
    return None


def _is_upload_sink(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr.upper() in UPLOAD_METHODS:
        return True
    if node.func.attr != "request":
        return False
    method = _request_method(node)
    return method is None or method.upper() in UPLOAD_METHODS


def _is_function(node):
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))


def _runtime_descendants(node):
    for child in ast.iter_child_nodes(node):
        if _is_function(child) or isinstance(child, (ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _runtime_descendants(child)


def _guard_contains_publish_and_not_ci(node):
    terms = []

    def flatten(value):
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.And):
            for item in value.values:
                flatten(item)
        else:
            terms.append(value)

    flatten(node)
    has_publish = any(
        isinstance(term, ast.Name) and term.id == "PUBLISH_TO_GITHUB" for term in terms
    )
    has_not_ci = any(
        isinstance(term, ast.UnaryOp)
        and isinstance(term.op, ast.Not)
        and isinstance(term.operand, ast.Name)
        and term.operand.id == "IS_CI"
        for term in terms
    )
    return has_publish and has_not_ci


def _upload_bypass_reasons(source):
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if _is_function(node)
    }
    direct_sinks = {}
    calls = {}
    for name, function in functions.items():
        direct_sinks[name] = False
        calls[name] = set()
        for node in _runtime_descendants(function):
            if not isinstance(node, ast.Call):
                continue
            if _is_upload_sink(node):
                direct_sinks[name] = True
            elif isinstance(node.func, ast.Name) and node.func.id in functions:
                calls[name].add(node.func.id)

    reachable_cache = {}

    def reaches_upload(name, active=()):
        if name in reachable_cache:
            return reachable_cache[name]
        if name in active:
            return False
        result = direct_sinks[name] or any(
            reaches_upload(child, active + (name,)) for child in calls[name]
        )
        reachable_cache[name] = result
        return result

    reasons = []

    def scan(node, guarded, location="module"):
        if _is_function(node) or isinstance(node, (ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.If):
            body_guarded = guarded or _guard_contains_publish_and_not_ci(node.test)
            for child in node.body:
                scan(child, body_guarded, location)
            for child in node.orelse:
                scan(child, guarded, location)
            return
        if isinstance(node, ast.Call):
            if _is_upload_sink(node) and not guarded:
                reasons.append(f"unguarded upload sink at line {node.lineno}")
            elif (
                isinstance(node.func, ast.Name)
                and node.func.id in functions
                and reaches_upload(node.func.id)
                and not guarded
            ):
                reasons.append(f"unguarded upload helper {node.func.id} at line {node.lineno}")
        for child in ast.iter_child_nodes(node):
            scan(child, guarded, location)

    scan(tree, False)
    return reasons


def _extract_workflow_steps(workflow):
    lines = workflow.splitlines()
    headers = []
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>[ \t]*)-\s+name:\s*(?P<name>.+?)\s*$", line)
        if match:
            name = match.group("name").strip().strip("'\"")
            headers.append((index, len(match.group("indent")), name))
    steps = []
    for position, (start, indent, name) in enumerate(headers):
        end = len(lines)
        for next_start, next_indent, _ in headers[position + 1 :]:
            if next_indent == indent:
                end = next_start
                break
        steps.append((name, "\n".join(lines[start:end])))
    return steps


def _unique_workflow_step(steps, name):
    matches = [step for step_name, step in steps if step_name == name]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one workflow step named {name!r}")
    return matches[0]


def _extract_run_script(step):
    lines = step.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>[ \t]*)run:\s*(?P<value>.*)$", line)
        if not match:
            continue
        value = match.group("value").strip()
        if value not in {"|", ">", "|-", ">-", "|+", ">+"}:
            return value
        run_indent = len(match.group("indent"))
        script = []
        for body_line in lines[index + 1 :]:
            if body_line.strip() and len(body_line) - len(body_line.lstrip(" \t")) <= run_indent:
                break
            script.append(body_line.lstrip())
        return "\n".join(script)
    return ""


def _shell_commands(script):
    commands = []
    pending = ""
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pending:
            stripped = pending + " " + stripped
        if stripped.endswith("\\"):
            pending = stripped[:-1].rstrip()
        else:
            commands.append(stripped)
            pending = ""
    if pending:
        commands.append(pending)
    return commands


def _shell_invocations(script):
    controls = {";", "&&", "||", "(", ")", "|"}
    for command in _shell_commands(script):
        tokens = _shell_control_tokens(command)
        current = []
        for token in tokens:
            if token in controls:
                if current:
                    yield current
                current = []
            else:
                current.append(token)
        if current:
            yield current


def _shell_control_tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _executable_git_commands(script, verb):
    for tokens in _shell_invocations(script):
        for index in range(len(tokens) - 1):
            if tokens[index : index + 2] != ["git", verb]:
                continue
            prefix = tokens[:index]
            if prefix and prefix[-1] in {"echo", "printf"}:
                continue
            yield tokens[index + 2 :]
            break


def _git_add_paths(step):
    commands = list(_executable_git_commands(_extract_run_script(step), "add"))
    if len(commands) != 1:
        return None
    arguments = commands[0]
    if "--" in arguments:
        arguments = arguments[arguments.index("--") + 1 :]
    return {argument for argument in arguments if not argument.startswith("-")}


def _count_executable_git_commands(workflow, verb):
    return sum(
        1
        for _, step in _extract_workflow_steps(workflow)
        for _ in _executable_git_commands(_extract_run_script(step), verb)
    )


def _forbidden_build_env(step):
    found = set()
    lines = step.splitlines()
    for index, line in enumerate(lines):
        env_match = re.match(r"^(?P<indent>[ \t]*)env:\s*$", line)
        if not env_match:
            continue
        env_indent = len(env_match.group("indent"))
        for env_line in lines[index + 1 :]:
            if env_line.strip() and len(env_line) - len(env_line.lstrip(" \t")) <= env_indent:
                break
            key_match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", env_line)
            if key_match and key_match.group(1) in FORBIDDEN_BUILD_ENV:
                found.add(key_match.group(1))

    def assignment_name(token):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", token)
        return match.group(1) if match else None

    for tokens in _shell_invocations(_extract_run_script(step)):
        if not tokens or tokens[0] in {"echo", "printf"}:
            continue
        if tokens[0] == "export":
            assignment_tokens = tokens[1:]
        elif tokens[0] == "env":
            assignment_tokens = tokens[1:]
        else:
            assignment_tokens = tokens
        for token in assignment_tokens:
            name = assignment_name(token)
            if name is None:
                break
            if name in FORBIDDEN_BUILD_ENV:
                found.add(name)
    return found


def _forbidden_workflow_env(workflow):
    found = set()
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        inline = re.match(r"^(?P<indent>[ \t]*)env:\s*\{(?P<body>.*)\}\s*$", line)
        if inline:
            for key in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:", inline.group("body")):
                if key in FORBIDDEN_BUILD_ENV:
                    found.add(key)
            continue
        env_match = re.match(r"^(?P<indent>[ \t]*)env:\s*$", line)
        if not env_match:
            continue
        env_indent = len(env_match.group("indent"))
        for env_line in lines[index + 1 :]:
            if env_line.strip() and len(env_line) - len(env_line.lstrip(" \t")) <= env_indent:
                break
            key_match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", env_line)
            if key_match and key_match.group(1) in FORBIDDEN_BUILD_ENV:
                found.add(key_match.group(1))

    for _, step in _extract_workflow_steps(workflow):
        found.update(_forbidden_build_env(step))
    return found


def _has_concurrency_group(workflow):
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        header = re.match(r"^(?P<indent>[ \t]*)concurrency:\s*$", line)
        if not header:
            continue
        indent = len(header.group("indent"))
        body = []
        for body_line in lines[index + 1 :]:
            if body_line.strip() and len(body_line) - len(body_line.lstrip(" \t")) <= indent:
                break
            body.append(body_line)
        body_text = "\n".join(body)
        return bool(
            re.search(r"(?m)^\s*group:\s*update-dashboard\s*$", body_text)
            and re.search(r"(?m)^\s*cancel-in-progress:\s*true\s*$", body_text)
        )
    return False


def _has_rebase_push_retry(step):
    controls = {";", "&&", "||", "(", ")", "|"}

    def command_end(tokens, start):
        end = start + 2
        while end < len(tokens) and tokens[end] not in controls:
            end += 1
        return end

    def git_commands(tokens):
        commands = []
        index = 0
        while index < len(tokens) - 1:
            if tokens[index] != "git" or tokens[index + 1] not in {"pull", "push"}:
                index += 1
                continue
            if index > 0 and tokens[index - 1] in {"echo", "printf"}:
                index += 1
                continue
            end = command_end(tokens, index)
            commands.append((tokens[index + 1], tokens[index + 2 : end], index))
            index = end
        return commands

    command_tokens = [
        (command_index, _shell_control_tokens(command))
        for command_index, command in enumerate(_shell_commands(_extract_run_script(step)))
    ]
    push_records = []
    for command_index, tokens in command_tokens:
        for verb, args, index in git_commands(tokens):
            if verb == "push":
                push_records.append((command_index, tokens, args, index))
    if len(push_records) != 2:
        return False

    initial_command, initial_tokens, initial_args, initial_index = push_records[0]
    initial_end = command_end(initial_tokens, initial_index)
    if initial_end >= len(initial_tokens) or initial_tokens[initial_end] != "||":
        return False
    if initial_end + 1 >= len(initial_tokens) or initial_tokens[initial_end + 1] != "(":
        return False

    depth = 0
    closing = None
    for index in range(initial_end + 1, len(initial_tokens)):
        token = initial_tokens[index]
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth == 0:
                closing = index
                break
    if closing is None or closing != len(initial_tokens) - 1:
        return False

    branch_tokens = initial_tokens[initial_end + 2 : closing]
    pull_start = 0
    if branch_tokens[0:2] != ["git", "pull"]:
        return False
    pull_end = command_end(branch_tokens, pull_start)
    expected_pull = ["--rebase", "--autostash", "origin", "main"]
    if branch_tokens[2:pull_end] != expected_pull:
        return False
    if pull_end >= len(branch_tokens) or branch_tokens[pull_end] != "&&":
        return False

    retry_start = pull_end + 1
    if branch_tokens[retry_start : retry_start + 2] != ["git", "push"]:
        return False
    retry_end = command_end(branch_tokens, retry_start)
    if retry_end != len(branch_tokens):
        return False

    retry_command, retry_tokens, retry_args, retry_index = push_records[1]
    expected_retry_index = initial_end + 2 + retry_start
    if retry_command != initial_command or retry_tokens is not initial_tokens:
        return False
    if retry_index != expected_retry_index:
        return False

    def push_target(args):
        positional = [argument for argument in args if not argument.startswith("-")]
        return tuple(positional[:2]) if len(positional) >= 2 else None

    if initial_args != ["origin", "main"] or retry_args != ["origin", "main"]:
        return False
    return push_target(initial_args) == push_target(retry_args)


def _alternate_upload_paths(workflow):
    findings = []

    def has_upload_method(tokens):
        lowered = [token.lower() for token in tokens]
        upload_values = {method.lower() for method in UPLOAD_METHODS}
        for index, token in enumerate(lowered):
            if token in {"--request", "--method"} and index + 1 < len(lowered):
                if lowered[index + 1] in upload_values:
                    return True
            for prefix in ("--request=", "--method="):
                if token.startswith(prefix) and token[len(prefix) :] in upload_values:
                    return True
            original = tokens[index]
            if original == "-X" and index + 1 < len(tokens):
                if lowered[index + 1] in upload_values:
                    return True
            if original.startswith("-X") and len(original) > 2:
                if original[2:].lower() in upload_values:
                    return True
        return False

    def has_write_flag(tokens, flags):
        long_flags = {flag.lower() for flag in flags if flag.startswith("--")}
        short_flags = {flag for flag in flags if flag.startswith("-") and not flag.startswith("--")}
        for raw_token in tokens:
            token = raw_token.lower()
            if token in long_flags:
                return True
            if any(token.startswith(flag + "=") for flag in long_flags):
                return True
            if any(
                raw_token == flag
                or (len(raw_token) > len(flag) and raw_token.startswith(flag))
                for flag in short_flags
            ):
                return True
        return False

    curl_write_flags = {
        "-d",
        "--data",
        "--data-raw",
        "--data-binary",
        "--data-urlencode",
        "-T",
        "--upload-file",
        "-F",
        "--form",
    }
    gh_write_flags = {
        "-f",
        "--raw-field",
        "-F",
        "--field",
        "--input",
        "-d",
        "--data",
    }

    for step_name, step in _extract_workflow_steps(workflow):
        for line in step.splitlines():
            uses_match = re.match(r"^\s*uses:\s*([^\s#]+)", line)
            if uses_match and "upload" in uses_match.group(1).lower():
                findings.append(f"{step_name}: alternate action")
        for tokens in _shell_invocations(_extract_run_script(step)):
            lowered = [token.lower() for token in tokens]
            method_upload = has_upload_method(tokens)
            if lowered and lowered[0] in {"echo", "printf"}:
                continue
            gh_write = has_write_flag(tokens, gh_write_flags)
            curl_write = has_write_flag(tokens, curl_write_flags)
            if "gh" in lowered and "api" in lowered and (method_upload or gh_write):
                findings.append(f"{step_name}: gh api upload")
            if lowered and lowered[0] == "curl" and (
                method_upload or curl_write
            ):
                findings.append(f"{step_name}: curl upload")
    return findings


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
        self.assertEqual([], _upload_bypass_reasons(self.source))

    def test_upload_detector_rejects_direct_and_indirect_bypasses(self):
        rejected = [
            "requests.put('/contents/file')",
            "requests.post('/contents/file')",
            "requests.delete('/contents/file')",
            "requests.request('PUT', '/contents/file')",
            "requests.request(method='DELETE', url='/contents/file')",
            "session.request('PUT', '/file')",
            "requests.Session().request(method='PATCH', url='/file')",
            "session_alias.patch('/contents/file')",
            "requests.Session().put('/contents/file')",
            "def upload():\n    requests.put('/contents/file')\nupload()",
            "def upload():\n    session.request('PUT', '/file')\nupload()",
            (
                "def inner():\n"
                "    requests.request(method='POST', url='/contents/file')\n"
                "def outer():\n"
                "    inner()\n"
                "outer()"
            ),
            (
                "def inner():\n"
                "    requests.Session().request(method='PATCH', url='/file')\n"
                "def outer():\n"
                "    inner()\n"
                "outer()"
            ),
        ]
        for snippet in rejected:
            with self.subTest(snippet=snippet):
                self.assertTrue(_upload_bypass_reasons(snippet))

    def test_upload_detector_accepts_exact_guard_at_executable_root(self):
        accepted = [
            "if PUBLISH_TO_GITHUB and not IS_CI:\n    requests.put('/contents/file')",
            (
                "def upload():\n"
                "    requests.Session().put('/contents/file')\n"
                "if PUBLISH_TO_GITHUB and not IS_CI:\n"
                "    upload()"
            ),
            (
                "def inner():\n"
                "    requests.request('PATCH', '/contents/file')\n"
                "def outer():\n"
                "    inner()\n"
                "if PUBLISH_TO_GITHUB and not IS_CI:\n"
                "    outer()"
            ),
            "session.request('GET', '/safe')",
            "requests.Session().request(method='GET', url='/safe')",
        ]
        for snippet in accepted:
            with self.subTest(snippet=snippet):
                self.assertEqual([], _upload_bypass_reasons(snippet))

    def test_build_step_does_not_receive_a_github_token_or_publish_flag(self):
        steps = _extract_workflow_steps(self.workflow)
        self.assertEqual(set(), _forbidden_workflow_env(self.workflow))
        self.assertEqual(set(), _forbidden_build_env(_unique_workflow_step(steps, "Build dashboard")))

        inline_fixture = """
      - name: Build dashboard
        run: GITHUB_TOKEN=secret python build.py
        env:
          SAFE: value
"""
        self.assertEqual(
            {"GITHUB_TOKEN"},
            _forbidden_build_env(
                _unique_workflow_step(_extract_workflow_steps(inline_fixture), "Build dashboard")
            ),
        )

        export_fixture = """
      - name: Build dashboard
        run: |
          export GH_TOKEN=secret
          python build.py
"""
        self.assertEqual(
            {"GH_TOKEN"},
            _forbidden_build_env(
                _unique_workflow_step(_extract_workflow_steps(export_fixture), "Build dashboard")
            ),
        )

        publish_fixture = """
      - name: Build dashboard
        env:
          PUBLISH_TO_GITHUB: '1'
        run: python build.py
"""
        self.assertEqual(
            {"PUBLISH_TO_GITHUB"},
            _forbidden_build_env(
                _unique_workflow_step(_extract_workflow_steps(publish_fixture), "Build dashboard")
            ),
        )

        safe_text_fixture = """
      - name: Build dashboard
        run: |
          # GITHUB_TOKEN=secret
          echo GITHUB_TOKEN=secret
          printf 'GH_TOKEN=secret'
          python build.py
"""
        self.assertEqual(
            set(),
            _forbidden_build_env(
                _unique_workflow_step(_extract_workflow_steps(safe_text_fixture), "Build dashboard")
            ),
        )

        env_command_fixture = """
      - name: Build dashboard
        run: env PUBLISH_TO_GITHUB=1 python build.py
"""
        self.assertEqual(
            {"PUBLISH_TO_GITHUB"},
            _forbidden_build_env(
                _unique_workflow_step(
                    _extract_workflow_steps(env_command_fixture), "Build dashboard"
                )
            ),
        )

    def test_workflow_stages_every_generated_file_once(self):
        required = {
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
        }
        steps = _extract_workflow_steps(self.workflow)
        self.assertEqual(1, _count_executable_git_commands(self.workflow, "add"))
        self.assertEqual(
            required,
            _git_add_paths(_unique_workflow_step(steps, "Commit and push if changed")),
        )

        substring_fixture = """
      - name: Commit and push if changed
        run: |
          git add index.html website/pl_mobile.html.bak live.json
"""
        self.assertNotEqual(
            required,
            _git_add_paths(
                _unique_workflow_step(
                    _extract_workflow_steps(substring_fixture), "Commit and push if changed"
                )
            ),
        )

    def test_workflow_has_one_commit_and_preserves_atomic_push_retry(self):
        steps = _extract_workflow_steps(self.workflow)
        self.assertEqual(1, _count_executable_git_commands(self.workflow, "commit"))
        self.assertTrue(_has_concurrency_group(self.workflow))
        self.assertTrue(
            _has_rebase_push_retry(_unique_workflow_step(steps, "Commit and push if changed"))
        )

        echo_push_fixture = """
      - name: Commit and push if changed
        run: |
          echo git push origin main || (git pull --rebase --autostash origin main && git push origin main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(echo_push_fixture), "Commit and push if changed"
                )
            )
        )

        comment_fixture = """
      - name: Commit and push if changed
        run: |
          # git commit -m fake
          echo git commit -m fake
          git commit -m real
"""
        self.assertEqual(1, _count_executable_git_commands(comment_fixture, "commit"))

        unrelated_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main
          git pull --rebase --autostash origin main
          git push origin main
          git pull --rebase --autostash origin main
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(unrelated_fixture), "Commit and push if changed"
                )
            )
        )

        malformed_order_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main || (git push origin main && git pull --rebase --autostash origin main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(malformed_order_fixture), "Commit and push if changed"
                )
            )
        )

        semicolon_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main || (git pull --rebase --autostash origin main; git push origin main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(semicolon_fixture), "Commit and push if changed"
                )
            )
        )

        trailing_push_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main || (git pull --rebase --autostash origin main && git push origin main) || git push origin main
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(trailing_push_fixture), "Commit and push if changed"
                )
            )
        )

        extra_fallback_push_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main || (git pull --rebase --autostash origin main && git push origin main && git push origin main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(extra_fallback_push_fixture),
                    "Commit and push if changed",
                )
            )
        )

        mismatched_target_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main || (git pull --rebase --autostash origin main && git push backup main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(mismatched_target_fixture), "Commit and push if changed"
                )
            )
        )

        extra_ref_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main refs/heads/other || (git pull --rebase --autostash origin main && git push origin main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(extra_ref_fixture), "Commit and push if changed"
                )
            )
        )

        push_flag_fixture = """
      - name: Commit and push if changed
        run: |
          git push --force origin main || (git pull --rebase --autostash origin main && git push --tags origin main)
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(push_flag_fixture), "Commit and push if changed"
                )
            )
        )

    def test_workflow_steps_preserve_duplicates_for_global_checks(self):
        duplicate_fixture = """
env:
  SAFE: value
jobs:
  update:
    steps:
      - name: Commit and push if changed
        run: |
          git commit -m hidden
          gh api --method=PUT remote/hidden
      - name: Commit and push if changed
        run: git commit -m visible
"""
        steps = _extract_workflow_steps(duplicate_fixture)
        self.assertEqual(2, len(steps))
        self.assertEqual(2, _count_executable_git_commands(duplicate_fixture, "commit"))
        self.assertTrue(_alternate_upload_paths(duplicate_fixture))

    def test_sensitive_workflow_env_inheritance_is_rejected(self):
        inherited_fixture = """
env:
  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
jobs:
  update:
    env:
      GH_TOKEN: inherited
    steps:
      - name: Build dashboard
        env:
          PUBLISH_TO_GITHUB: '1'
        run: python build.py
"""
        self.assertEqual(
            {"GITHUB_TOKEN", "GH_TOKEN", "PUBLISH_TO_GITHUB"},
            _forbidden_workflow_env(inherited_fixture),
        )

        safe_fixture = """
env:
  SAFE: value
jobs:
  update:
    env:
      SAFE_JOB: value
    steps:
      - name: Build dashboard
        run: |
          # GITHUB_TOKEN: safe comment
          echo GH_TOKEN=quoted
          printf 'PUBLISH_TO_GITHUB=quoted'
          python build.py
"""
        self.assertEqual(set(), _forbidden_workflow_env(safe_fixture))

    def test_workflow_has_no_alternate_upload_path(self):
        self.assertEqual([], _alternate_upload_paths(self.workflow))
        fixture = """
      - name: Upload
        uses: actions/upload-artifact@v4
      - name: API upload
        run: |
          gh api --method PUT repos/x/contents/live.json
          curl -X POST https://api.github.com/repos/x/contents/live.json
"""
        self.assertTrue(_alternate_upload_paths(fixture))

        method_fixture = """
      - name: Upload methods
        run: |
          gh api --method=PUT remote/endpoint
          gh api --method post remote/endpoint
          curl --request=PATCH remote/endpoint
          curl --request delete remote/endpoint
          curl --method=PUT remote/endpoint
          curl --method post remote/endpoint
          curl -XPUT remote/endpoint
          curl -X DELETE remote/endpoint
          curl -d payload remote/endpoint
          curl --data=payload remote/endpoint
          curl -T file remote/endpoint
          curl --upload-file=file remote/endpoint
          curl -F field=value remote/endpoint
          curl --form=field=value remote/endpoint
"""
        self.assertGreaterEqual(len(_alternate_upload_paths(method_fixture)), 14)

        safe_text_fixture = """
      - name: Safe text
        run: |
          # gh api --method=PUT remote/endpoint
          echo curl -XPUT remote/endpoint
          printf 'gh api --method=POST remote/endpoint'
          curl -x patch remote/endpoint
          curl -f remote/endpoint
          curl -t remote/endpoint
          curl https://example.invalid/read
          gh api repos/x/read
"""
        self.assertEqual([], _alternate_upload_paths(safe_text_fixture))

        gh_data_fixture = """
      - name: API writes
        run: |
          gh api -f key=value remote/endpoint
          gh api --raw-field=key=value remote/endpoint
          gh api -F key=value remote/endpoint
          gh api --field=key=value remote/endpoint
          gh api --input payload.json remote/endpoint
"""
        self.assertGreaterEqual(len(_alternate_upload_paths(gh_data_fixture)), 5)

if __name__ == "__main__":
    unittest.main()
