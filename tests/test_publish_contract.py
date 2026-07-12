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
    functions = {}
    classes = {}

    def collect(node, scope=()):
        for child in ast.iter_child_nodes(node):
            if _is_function(child):
                qualified = ".".join(scope + (child.name,))
                functions[qualified] = child
                collect(child, scope + (child.name,))
            elif isinstance(child, ast.ClassDef):
                qualified = ".".join(scope + (child.name,))
                classes[qualified] = child
                collect(child, scope + (child.name,))
            else:
                collect(child, scope)

    collect(tree)

    def resolve_bare(name, scope):
        for end in range(len(scope), -1, -1):
            qualified = ".".join(scope[:end] + (name,))
            if qualified in functions:
                return {qualified}
        return set()

    def resolve_class(name, scope):
        for end in range(len(scope), -1, -1):
            qualified = ".".join(scope[:end] + (name,))
            if qualified in classes:
                return {qualified}
        return set()

    def resolve_call_targets(call, scope):
        if isinstance(call.func, ast.Name):
            return resolve_bare(call.func.id, scope)
        if not isinstance(call.func, ast.Attribute):
            return set()
        method = call.func.attr
        targets = {name for name in functions if name.endswith("." + method)}
        value = call.func.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            targets.update(
                f"{class_name}.{method}" for class_name in resolve_class(value.func.id, scope)
            )
        return targets

    direct_sinks = {}
    calls = {}
    for qualified, function in functions.items():
        scope = tuple(qualified.split("."))
        direct_sinks[qualified] = False
        calls[qualified] = set()
        for node in _runtime_descendants(function):
            if not isinstance(node, ast.Call):
                continue
            if _is_upload_sink(node):
                direct_sinks[qualified] = True
            else:
                calls[qualified].update(resolve_call_targets(node, scope))

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

    def scan(node, guarded, scope=()):
        if _is_function(node) or isinstance(node, (ast.ClassDef, ast.Lambda)):
            return
        if isinstance(node, ast.If):
            body_guarded = guarded or _guard_contains_publish_and_not_ci(node.test)
            for child in node.body:
                scan(child, body_guarded, scope)
            for child in node.orelse:
                scan(child, guarded, scope)
            return
        if isinstance(node, ast.Call):
            if _is_upload_sink(node) and not guarded:
                reasons.append(f"unguarded upload sink at line {node.lineno}")
            elif any(reaches_upload(target) for target in resolve_call_targets(node, scope)) and not guarded:
                reasons.append(f"unguarded upload helper at line {node.lineno}")
        for child in ast.iter_child_nodes(node):
            scan(child, guarded, scope)

    scan(tree, False)
    return reasons


def _has_executable_substitution(command):
    single_quote = False
    double_quote = False
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\" and not single_quote:
            escaped = True
            index += 1
            continue
        if char == "#" and not single_quote and not double_quote:
            break
        if char == "'" and not double_quote:
            single_quote = not single_quote
        elif char == '"' and not single_quote:
            double_quote = not double_quote
        elif not single_quote and (char == "`" or command.startswith("$(", index)):
            return True
        index += 1
    return False


def _wrapper_bypass_reasons(workflow):
    reasons = []
    shell_names = {"bash", "sh", "zsh"}
    powershell_names = {"pwsh", "powershell"}
    wrapper_names = {"env", "command", "xargs", "eval", "exec", "source", "."}

    def basename(token):
        return token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()

    for step_name, step in _extract_workflow_steps(workflow):
        for command in _shell_commands(_extract_run_script(step)):
            if _has_executable_substitution(command):
                reasons.append(f"{step_name}: command substitution")
                continue
            for tokens in _normalized_shell_segments(command):
                first = basename(tokens[0])
                lowered = [token.lower() for token in tokens]
                if first in {"echo", "printf"}:
                    continue
                if first in wrapper_names:
                    reasons.append(f"{step_name}: shell wrapper {first}")
                    continue
                if first in shell_names and any(
                    token in {"-c", "-lc", "-ic"} for token in lowered[1:]
                ):
                    reasons.append(f"{step_name}: shell interpreter wrapper {first}")
                    continue
                if first in powershell_names and any(
                    token in {"-c", "-command", "/command", "-encodedcommand"}
                    for token in lowered[1:]
                ):
                    reasons.append(f"{step_name}: PowerShell wrapper {first}")
                    continue
                if first in {"cmd", "cmd.exe"} and any(
                    token in {"/c", "/k"} for token in lowered[1:]
                ):
                    reasons.append(f"{step_name}: cmd wrapper")
    return reasons


REQUIRED_GENERATED_PATHS = [
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


def _workflow_contract_reasons(workflow):
    layout_reasons = _workflow_step_layout_reasons(workflow)
    if layout_reasons:
        return layout_reasons
    wrapper_reasons = _wrapper_bypass_reasons(workflow)
    if wrapper_reasons:
        return wrapper_reasons
    reasons = []
    steps = _extract_workflow_steps(workflow)
    try:
        publish_step = _unique_workflow_step(steps, "Commit and push if changed")
        build_step = _unique_workflow_step(steps, "Build dashboard")
    except AssertionError as error:
        return [str(error)]
    reasons.extend(_git_global_option_reasons(workflow))
    reasons.extend(_workflow_index_integrity_reasons(workflow))
    if _count_executable_git_commands(workflow, "add") != 1:
        reasons.append("workflow must have exactly one git add")
    if _git_add_paths(publish_step) != REQUIRED_GENERATED_PATHS:
        reasons.append("workflow git add paths are not exact")
    if _count_executable_git_commands(workflow, "commit") != 1:
        reasons.append("workflow must have exactly one git commit")
    if not _has_global_rebase_push_retry(workflow):
        reasons.append("workflow publish retry structure is invalid")
    if _alternate_upload_paths(workflow):
        reasons.append("workflow has an alternate upload path")
    if _forbidden_workflow_env(workflow):
        reasons.append("workflow has sensitive environment state")
    if _forbidden_build_env(build_step):
        reasons.append("build step has sensitive environment state")
    return reasons


def _extract_workflow_steps(workflow):
    lines = workflow.splitlines()
    step_scopes = []
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>[ \t]*)steps:\s*$", line)
        if not match:
            continue
        scope_indent = len(match.group("indent"))
        end = len(lines)
        for next_index, next_line in enumerate(lines[index + 1 :], index + 1):
            if next_line.strip() and len(next_line) - len(next_line.lstrip(" \t")) <= scope_indent:
                end = next_index
                break
        step_scopes.append((index + 1, end, scope_indent))

    if not step_scopes:
        step_scopes = [(0, len(lines), -1)]

    entries = []
    unnamed_index = 0
    for scope_start, scope_end, scope_indent in step_scopes:
        candidates = []
        for index in range(scope_start, scope_end):
            match = re.match(r"^(?P<indent>[ \t]*)-\s+", lines[index])
            if not match:
                continue
            item_indent = len(match.group("indent"))
            if scope_indent >= 0 and item_indent <= scope_indent:
                continue
            candidates.append((index, item_indent))

        if not candidates:
            continue
        step_indent = min(item_indent for _, item_indent in candidates)
        headers = [candidate for candidate in candidates if candidate[1] == step_indent]
        for position, (start, indent) in enumerate(headers):
            end = scope_end
            for next_start, next_indent in headers[position + 1 :]:
                if next_indent == indent:
                    end = next_start
                    break
            block = "\n".join(lines[start:end])
            name_fields = _top_level_step_fields(block).get("name", [])
            name = name_fields[0]["value"].strip().strip("'\"") if name_fields else None
            if name is None:
                name = f"<unnamed-step-{unnamed_index}>"
                unnamed_index += 1
            entries.append((start, name, block))

    entries.sort(key=lambda entry: entry[0])
    return [(name, block) for _, name, block in entries]


def _unique_workflow_step(steps, name):
    matches = [step for step_name, step in steps if step_name == name]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one workflow step named {name!r}")
    return matches[0]


def _top_level_step_fields(step):
    lines = step.splitlines()
    first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_index is None:
        return {}
    item_match = re.match(r"^(?P<indent>[ \t]*)-\s+", lines[first_index])
    if not item_match:
        return {}
    item_indent = len(item_match.group("indent"))
    field_indent = item_indent + 2
    fields = {}

    for index, line in enumerate(lines):
        match = None
        line_indent = None
        if index == first_index:
            match = re.match(
                r"^[ \t]*-\s+(?P<key>[A-Za-z_][A-Za-z0-9_-]*):\s*(?P<value>.*?)\s*$",
                line,
            )
            line_indent = item_indent
        else:
            match = re.match(
                r"^(?P<indent>[ \t]+)(?P<key>[A-Za-z_][A-Za-z0-9_-]*):\s*(?P<value>.*?)\s*$",
                line,
            )
            if match:
                line_indent = len(match.group("indent"))
                if line_indent != field_indent:
                    match = None
        if match:
            fields.setdefault(match.group("key"), []).append(
                {
                    "line": index,
                    "indent": line_indent,
                    "value": match.group("value"),
                }
            )
    return fields


def _is_literal_block_scalar(value):
    return bool(re.fullmatch(r"\|\s*(?:[+-]\s*)?(?:#.*)?", value.strip()))


def _is_folded_block_scalar(value):
    return bool(re.fullmatch(r">\s*(?:[+-]\s*)?(?:#.*)?", value.strip()))


def _workflow_step_layout_reasons(workflow):
    reasons = []
    for step_name, step in _extract_workflow_steps(workflow):
        fields = _top_level_step_fields(step)
        for field_name, matches in fields.items():
            if len(matches) > 1:
                reasons.append(f"{step_name}: duplicate top-level {field_name} field")
        for field in fields.get("run", []):
            value = field["value"].strip()
            if not value:
                reasons.append(f"{step_name}: run field is empty")
            elif _is_folded_block_scalar(value):
                reasons.append(f"{step_name}: folded run scalar")
        for field in fields.get("uses", []):
            value = field["value"].strip()
            if not value or _is_literal_block_scalar(value) or _is_folded_block_scalar(value):
                reasons.append(f"{step_name}: uses field must be inline")
    return reasons


def _extract_run_script(step):
    fields = _top_level_step_fields(step).get("run", [])
    if len(fields) != 1:
        return ""
    field = fields[0]
    lines = step.splitlines()
    value = field["value"].strip()
    if not _is_literal_block_scalar(value) and not _is_folded_block_scalar(value):
        return value
    script = []
    for body_line in lines[field["line"] + 1 :]:
        if body_line.strip() and len(body_line) - len(body_line.lstrip(" \t")) <= field["indent"]:
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


SHELL_CONTROLS = frozenset({";", "&&", "||", "(", ")", "|", "{", "}"})
SHELL_CONTROL_PREFIXES = frozenset(
    {"if", "then", "else", "elif", "while", "until", "do", "!", "{", "}", "fi", "done"}
)
SHELL_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _shell_invocations(script):
    for _, _, segment, _, _ in _shell_invocation_records(script):
        yield segment


def _split_shell_tokens(tokens):
    current = []
    start = 0
    for index, token in enumerate(tokens):
        if token in SHELL_CONTROLS:
            if current:
                yield current, start, index
            current = []
            start = index + 1
        else:
            current.append(token)
    if current:
        yield current, start, len(tokens)


def _shell_invocation_records(script):
    for command_index, command in enumerate(_shell_commands(script)):
        command_tokens = _shell_control_tokens(command)
        for segment, start, end in _split_shell_tokens(command_tokens):
            yield command_index, command_tokens, segment, start, end


def _normalize_shell_segment(tokens):
    index = 0
    while index < len(tokens) and tokens[index].lower() in SHELL_CONTROL_PREFIXES:
        index += 1
    while index < len(tokens) and SHELL_ASSIGNMENT.match(tokens[index]):
        index += 1
    return tokens[index:]


def _normalized_shell_segments(script):
    for tokens in _shell_invocations(script):
        normalized = _normalize_shell_segment(tokens)
        if normalized:
            yield normalized


def _shell_control_tokens(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|(){}")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return list(lexer)


def _executable_git_commands(script, verb):
    for tokens in _normalized_shell_segments(script):
        if tokens[0].lower() in {"echo", "printf"}:
            continue
        if tokens[:2] == ["git", verb]:
            yield tokens[2:]


def _git_global_option_reasons(workflow):
    reasons = []
    for step_name, step in _extract_workflow_steps(workflow):
        for tokens in _normalized_shell_segments(_extract_run_script(step)):
            if len(tokens) > 1 and tokens[0].lower() == "git" and tokens[1].startswith("-"):
                reasons.append(f"{step_name}: git global option before verb")
    return reasons


def _workflow_normalized_commands(workflow):
    for step_name, step in _extract_workflow_steps(workflow):
        for tokens in _normalized_shell_segments(_extract_run_script(step)):
            yield step_name, tokens


def _workflow_index_integrity_reasons(workflow):
    commands = list(_workflow_normalized_commands(workflow))
    executable = [
        (index, step_name, tokens)
        for index, (step_name, tokens) in enumerate(commands)
        if tokens and tokens[0].lower() not in {"echo", "printf"}
    ]
    add_commands = [
        record for record in executable if record[2][:2] == ["git", "add"]
    ]
    commit_commands = [
        record for record in executable if record[2][:2] == ["git", "commit"]
    ]
    expected_add = ["git", "add", *REQUIRED_GENERATED_PATHS]
    expected_commit = ["git", "commit", "-m", "Auto-update dashboard data"]
    reasons = []

    if len(add_commands) != 1 or add_commands[0][2] != expected_add:
        reasons.append("workflow git add command is not the sole exact approved add")
    if len(commit_commands) != 1 or commit_commands[0][2] != expected_commit:
        reasons.append("workflow commit command is not the sole exact pathless commit")

    index_mutating_verbs = {
        "restore",
        "reset",
        "rm",
        "update-index",
        "read-tree",
        "checkout",
        "checkout-index",
    }
    for _, step_name, tokens in executable:
        if len(tokens) > 1 and tokens[0].lower() == "git" and tokens[1].lower() in index_mutating_verbs:
            reasons.append(f"{step_name}: executable git index mutation")
        if tokens[:2] == ["git", "apply"] and "--cached" in tokens:
            reasons.append(f"{step_name}: cached git apply mutates the index")

    if len(add_commands) == 1 and len(commit_commands) == 1:
        add_index = add_commands[0][0]
        commit_index = commit_commands[0][0]
        if add_index >= commit_index:
            reasons.append("workflow commit must occur after git add")
        else:
            for step_name, tokens in commands[add_index + 1 : commit_index]:
                if tokens[0].lower() in {"echo", "printf"}:
                    continue
                if tokens != ["git", "diff", "--staged", "--quiet"]:
                    reasons.append(f"{step_name}: unexpected command between add and commit")
                    break
        for index, step_name, tokens in executable:
            if tokens[:2] in (["git", "pull"], ["git", "push"]) and index <= commit_index:
                reasons.append(f"{step_name}: pull or push must follow commit")
    return reasons


def _git_add_paths(step):
    commands = list(_executable_git_commands(_extract_run_script(step), "add"))
    if len(commands) != 1:
        return None
    arguments = commands[0]
    if not arguments or any(argument.startswith("-") for argument in arguments):
        return None
    return arguments


def _count_executable_git_commands(workflow, verb):
    return sum(
        1
        for _, step in _extract_workflow_steps(workflow)
        for _ in _executable_git_commands(_extract_run_script(step), verb)
    )


def _forbidden_build_env(step):
    found = set()
    lines = step.splitlines()
    for field in _top_level_step_fields(step).get("env", []):
        value = field["value"].strip()
        if value.startswith("{") and value.endswith("}"):
            keys = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:", value)
            found.update(key for key in keys if key in FORBIDDEN_BUILD_ENV)
            continue
        env_indent = field["indent"]
        for env_line in lines[field["line"] + 1 :]:
            if env_line.strip() and len(env_line) - len(env_line.lstrip(" \t")) <= env_indent:
                break
            key_indent = len(env_line) - len(env_line.lstrip(" \t"))
            if key_indent != env_indent + 2:
                continue
            key_match = re.match(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)\s*:", env_line)
            if key_match and key_match.group(1) in FORBIDDEN_BUILD_ENV:
                found.add(key_match.group(1))

    def assignment_name(token):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", token)
        return match.group(1) if match else None

    for raw_tokens in _shell_invocations(_extract_run_script(step)):
        if not raw_tokens:
            continue
        index = 0
        while index < len(raw_tokens) and raw_tokens[index].lower() in SHELL_CONTROL_PREFIXES:
            index += 1
        while index < len(raw_tokens):
            name = assignment_name(raw_tokens[index])
            if name is None:
                break
            if name in FORBIDDEN_BUILD_ENV:
                found.add(name)
            index += 1

        tokens = _normalize_shell_segment(raw_tokens)
        if not tokens or tokens[0].lower() in {"echo", "printf"}:
            continue
        if tokens[0].lower() in {"export", "env"}:
            for token in tokens[1:]:
                name = assignment_name(token)
                if name is None:
                    break
                if name in FORBIDDEN_BUILD_ENV:
                    found.add(name)
    return found


def _forbidden_persistent_env(script):
    found = set()
    target_pattern = re.compile(r"\$(?:\{)?(?:GITHUB_ENV|GITHUB_OUTPUT)(?:\})?", re.IGNORECASE)
    assignment_pattern = re.compile(
        r"\b(GITHUB_TOKEN|GH_TOKEN|PUBLISH_TO_GITHUB)\s*=", re.IGNORECASE
    )

    for command in _shell_commands(script):
        if not target_pattern.search(command):
            continue
        if not re.search(r">>?|\btee\b|\bcat\b", command):
            continue
        found.update(match.group(1).upper() for match in assignment_pattern.finditer(command))

    lines = script.splitlines()
    for index, line in enumerate(lines):
        if not target_pattern.search(line) or "<<" not in line:
            continue
        delimiter_match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if not delimiter_match:
            continue
        delimiter = delimiter_match.group(1)
        body = []
        for body_line in lines[index + 1 :]:
            if body_line.strip() == delimiter:
                break
            body.append(body_line)
        found.update(
            match.group(1).upper()
            for match in assignment_pattern.finditer("\n".join(body))
        )
    return found


def _inside_steps_scope(lines, index):
    current_line = lines[index]
    current_indent = len(current_line) - len(current_line.lstrip(" \t"))
    for previous_index in range(index - 1, -1, -1):
        previous = lines[previous_index]
        steps_match = re.match(r"^(?P<indent>[ \t]*)steps:\s*$", previous)
        if not steps_match:
            continue
        steps_indent = len(steps_match.group("indent"))
        if steps_indent >= current_indent:
            continue
        if all(
            not between.strip()
            or len(between) - len(between.lstrip(" \t")) > steps_indent
            for between in lines[previous_index + 1 : index]
        ):
            return True
        return False
    return False


def _forbidden_workflow_env(workflow):
    found = set()
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if _inside_steps_scope(lines, index):
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
        found.update(_forbidden_persistent_env(_extract_run_script(step)))
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
    command_records = list(_shell_invocation_records(_extract_run_script(step)))
    push_records = []
    for command_index, command_tokens, segment, start, end in command_records:
        normalized = _normalize_shell_segment(segment)
        if normalized[:2] == ["git", "push"]:
            push_records.append(
                (command_index, command_tokens, normalized, start, end)
            )
    if len(push_records) != 2:
        return False

    initial_command, initial_tokens, initial_segment, initial_index, initial_end = push_records[0]
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
    branch_records = list(_split_shell_tokens(branch_tokens))
    if len(branch_records) != 2:
        return False

    pull_segment, pull_start, pull_end = branch_records[0]
    retry_segment, retry_start, retry_end = branch_records[1]
    if pull_end >= len(branch_tokens) or branch_tokens[pull_end] != "&&":
        return False
    if retry_start != pull_end + 1:
        return False

    normalized_pull = _normalize_shell_segment(pull_segment)
    normalized_retry = _normalize_shell_segment(retry_segment)
    if normalized_pull != ["git", "pull", "--rebase", "--autostash", "origin", "main"]:
        return False
    if normalized_retry != ["git", "push", "origin", "main"]:
        return False
    if retry_end != len(branch_tokens):
        return False

    retry_command, retry_tokens, retry_args, retry_absolute_start, retry_absolute_end = push_records[1]
    expected_retry_index = initial_end + 2 + retry_start
    if retry_command != initial_command or retry_tokens is not initial_tokens:
        return False
    if retry_absolute_start != expected_retry_index:
        return False
    return initial_segment == ["git", "push", "origin", "main"] and retry_args == [
        "git",
        "push",
        "origin",
        "main",
    ]


def _has_global_rebase_push_retry(workflow):
    steps = _extract_workflow_steps(workflow)
    try:
        publish_step = _unique_workflow_step(steps, "Commit and push if changed")
    except AssertionError:
        return False
    if _count_executable_git_commands(workflow, "push") != 2:
        return False
    return _has_rebase_push_retry(publish_step)


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
        for field in _top_level_step_fields(step).get("uses", []):
            uses_value = field["value"].split("#", 1)[0].strip()
            if uses_value and "upload" in uses_value.lower():
                findings.append(f"{step_name}: alternate action")
        for tokens in _normalized_shell_segments(_extract_run_script(step)):
            lowered = [token.lower() for token in tokens]
            method_upload = has_upload_method(tokens)
            if not lowered or lowered[0] in {"echo", "printf"}:
                continue
            gh_write = has_write_flag(tokens, gh_write_flags)
            curl_write = has_write_flag(tokens, curl_write_flags)
            if lowered[0] == "gh" and len(lowered) > 1 and lowered[1] == "api" and (
                method_upload or gh_write
            ):
                findings.append(f"{step_name}: gh api upload")
            if lowered[0] == "curl" and (method_upload or curl_write):
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
            (
                "class Uploader:\n"
                "    def upload(self):\n"
                "        requests.put('/file')\n"
                "Uploader().upload()"
            ),
            (
                "def first():\n"
                "    def upload():\n"
                "        requests.put('/first')\n"
                "    upload()\n"
                "def second():\n"
                "    def upload():\n"
                "        return None\n"
                "    upload()\n"
                "first()"
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
            (
                "class Uploader:\n"
                "    def upload(self):\n"
                "        requests.put('/file')\n"
                "if PUBLISH_TO_GITHUB and not IS_CI:\n"
                "    Uploader().upload()"
            ),
            "def outer():\n    def never_called():\n        requests.put('/file')",
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

        bad_adds = [
            "git add -A " + " ".join(required),
            "git add " + " ".join(required + ["index.html"]),
            "git add " + " ".join(required + ["README.md"]),
            "git add " + " ".join(required[:-1] + [":(exclude)ai_weights_wc.json"]),
            "git add -- " + " ".join(required),
        ]
        for command in bad_adds:
            with self.subTest(command=command):
                fixture = f"""
      - name: Commit and push if changed
        run: {command}
"""
                self.assertNotEqual(
                    required,
                    _git_add_paths(
                        _unique_workflow_step(
                            _extract_workflow_steps(fixture), "Commit and push if changed"
                        )
                    ),
                )

    def test_publish_preserves_index_and_uses_exact_pathless_commit(self):
        add_command = "git add " + " ".join(REQUIRED_GENERATED_PATHS)
        commit_command = 'git commit -m "Auto-update dashboard data"'

        def fixture(middle, commit=commit_command, before_add=""):
            return f"""
steps:
  - name: Commit and push if changed
    run: |
      {before_add}
      {add_command}
      {middle}
      {commit}
"""

        self.assertEqual([], _workflow_index_integrity_reasons(fixture("")))
        self.assertEqual(
            [],
            _workflow_index_integrity_reasons(
                fixture(
                    """if git diff --staged --quiet; then
        echo \"No changes to deploy\"
      else"""
                )
            ),
        )

        index_mutations = [
            "git restore --staged index.html",
            "git reset HEAD index.html",
            "git rm --cached index.html",
            "git update-index --assume-unchanged index.html",
            "git read-tree HEAD",
            "git checkout -- index.html",
            "git checkout-index --all",
            add_command,
        ]
        for mutation in index_mutations:
            with self.subTest(mutation=mutation):
                self.assertTrue(_workflow_index_integrity_reasons(fixture(mutation)))
                self.assertTrue(
                    _workflow_index_integrity_reasons(fixture("", before_add=mutation))
                )

        self.assertTrue(
            _workflow_index_integrity_reasons(
                fixture("", commit='git commit --only -m "Auto-update dashboard data"')
            )
        )
        self.assertTrue(
            _workflow_index_integrity_reasons(
                fixture("", commit='git commit -m "Auto-update dashboard data" index.html')
            )
        )
        self.assertTrue(
            _workflow_index_integrity_reasons(
                fixture("", before_add=commit_command)
            )
        )

    def test_workflow_has_one_commit_and_preserves_atomic_push_retry(self):
        steps = _extract_workflow_steps(self.workflow)
        self.assertEqual(1, _count_executable_git_commands(self.workflow, "commit"))
        self.assertTrue(_has_concurrency_group(self.workflow))
        self.assertTrue(
            _has_rebase_push_retry(_unique_workflow_step(steps, "Commit and push if changed"))
        )
        self.assertTrue(_has_global_rebase_push_retry(self.workflow))

        hidden_other_push = self.workflow + """
      - name: Hidden duplicate publish
        run: git push origin main
"""
        self.assertFalse(_has_global_rebase_push_retry(hidden_other_push))

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

        trailing_control_fixture = """
      - name: Commit and push if changed
        run: |
          git push origin main || (git pull --rebase --autostash origin main && git push origin main | )
"""
        self.assertFalse(
            _has_rebase_push_retry(
                _unique_workflow_step(
                    _extract_workflow_steps(trailing_control_fixture),
                    "Commit and push if changed",
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

    def test_github_env_persistence_is_rejected_globally(self):
        persistence_fixture = """
jobs:
  update:
    steps:
      - name: Earlier step
        run: echo "GITHUB_TOKEN=secret" >> "$GITHUB_ENV"
      - name: Current step
        run: |
          printf 'GH_TOKEN=secret\\n' >> ${GITHUB_OUTPUT}
          cat >> "$GITHUB_ENV" <<'EOF'
          PUBLISH_TO_GITHUB=1
          EOF
"""
        self.assertEqual(
            {"GITHUB_TOKEN", "GH_TOKEN", "PUBLISH_TO_GITHUB"},
            _forbidden_workflow_env(persistence_fixture),
        )

        safe_persistence_fixture = """
jobs:
  update:
    steps:
      - name: Safe text
        run: |
          # echo GITHUB_TOKEN=secret >> "$GITHUB_ENV"
          echo GITHUB_TOKEN=quoted
          printf 'GH_TOKEN=quoted'
"""
        self.assertEqual(set(), _forbidden_workflow_env(safe_persistence_fixture))

    def test_workflow_rejects_shell_wrappers_before_global_scans(self):
        wrapper_fixtures = [
            """
      - name: Hidden git
        run: bash -c 'git add index.html; git commit -m hidden; git push origin main'
""",
            """
      - name: Hidden curl
        run: env curl -X PUT remote/endpoint
""",
            """
      - name: Hidden curl
        run: command curl -X PUT remote/endpoint
""",
            """
      - name: Hidden env
        run: env -i GITHUB_TOKEN=secret python build.py
""",
            """
      - name: Hidden shell
        run: sh -c "git commit -m hidden"
""",
            """
      - name: Hidden PowerShell
        run: pwsh -Command "git push origin main"
""",
            """
      - name: Hidden cmd
        run: cmd /c "git push origin main"
""",
            """
      - name: Hidden substitution
        run: echo $(git push origin main)
""",
            """
      - name: Hidden backtick
        run: echo `git commit -m hidden`
""",
            """
      - name: Hidden chained wrapper
        run: echo ok; env curl -X PUT remote/endpoint
""",
            """
      - name: Hidden conditional wrapper
        run: if true; then ! bash -c "git push origin main"; fi
""",
        ]
        for fixture in wrapper_fixtures:
            with self.subTest(fixture=fixture):
                self.assertTrue(_wrapper_bypass_reasons(fixture))

        safe_fixture = """
      - name: Safe direct commands
        run: |
          # bash -c 'git push origin main'
          echo 'command curl -X PUT remote/endpoint'
          printf 'env GITHUB_TOKEN=quoted'
          git status --short
"""
        self.assertEqual([], _wrapper_bypass_reasons(safe_fixture))
        self.assertEqual([], _workflow_contract_reasons(self.workflow))
        hidden_global = self.workflow + """
      - name: Hidden global push
        run: git push origin main
"""
        self.assertTrue(_workflow_contract_reasons(hidden_global))

    def test_shell_segments_ignore_quoted_text_but_scan_each_command(self):
        safe_fixture = """
      - name: Safe shell segments
        run: |
          echo "quoted; env curl -X PUT remote/endpoint"
          printf 'if true; then bash -c "git push"; fi'
          echo ok; printf 'still safe'
"""
        self.assertEqual([], _wrapper_bypass_reasons(safe_fixture))

        chained_fixture = """
      - name: Chained hidden command
        run: echo ok && bash -c "git add index.html"
"""
        self.assertTrue(_wrapper_bypass_reasons(chained_fixture))

    def test_unnamed_workflow_steps_are_ordered_and_scanned(self):
        malicious_fixture = """
jobs:
  update:
    steps:
      - run: bash -c "git commit -m hidden"
      - uses: actions/upload-artifact@v4
      - run: |
          git push origin main
"""
        steps = _extract_workflow_steps(malicious_fixture)
        self.assertEqual(3, len(steps))
        self.assertTrue(all(step_name.startswith("<unnamed-step-") for step_name, _ in steps))
        self.assertTrue(_wrapper_bypass_reasons(malicious_fixture))
        self.assertTrue(_alternate_upload_paths(malicious_fixture))
        self.assertEqual(1, _count_executable_git_commands(malicious_fixture, "push"))

        safe_fixture = """
steps:
  - run: echo "safe; text"
  - uses: actions/checkout@v4
"""
        self.assertEqual(2, len(_extract_workflow_steps(safe_fixture)))
        self.assertEqual([], _wrapper_bypass_reasons(safe_fixture))
        self.assertEqual([], _alternate_upload_paths(safe_fixture))

    def test_steps_with_arbitrary_first_keys_are_ordered_and_scanned(self):
        malicious_fixture = """
jobs:
  update:
    steps:
      - if: always()
        run: bash -c "git commit -m hidden"
      - if: always()
        uses: actions/upload-artifact@v4
        env:
          SAFE: value
"""
        steps = _extract_workflow_steps(malicious_fixture)
        self.assertEqual(2, len(steps))
        self.assertTrue(all(step_name.startswith("<unnamed-step-") for step_name, _ in steps))
        self.assertTrue(_wrapper_bypass_reasons(malicious_fixture))
        self.assertTrue(_alternate_upload_paths(malicious_fixture))

        named_later_fixture = """
steps:
  - if: always()
    name: Conditional build
    run: echo safe
"""
        self.assertEqual(
            ["Conditional build"],
            [step_name for step_name, _ in _extract_workflow_steps(named_later_fixture)],
        )
        self.assertEqual([], _wrapper_bypass_reasons(named_later_fixture))
        self.assertEqual(set(), _forbidden_workflow_env(named_later_fixture))

    def test_step_fields_are_scoped_to_the_top_level(self):
        malicious_fixture = """
steps:
  - name: Real publish content
    env:
      run: echo fake
    run: |
      git push origin main
      curl -X PUT remote/endpoint
"""
        step = _unique_workflow_step(
            _extract_workflow_steps(malicious_fixture), "Real publish content"
        )
        self.assertIn("git push origin main", _extract_run_script(step))
        self.assertEqual(1, _count_executable_git_commands(malicious_fixture, "push"))
        self.assertTrue(_alternate_upload_paths(malicious_fixture))

        safe_nested_fixture = """
steps:
  - name: Safe nested data
    env:
      run: curl -X PUT fake/endpoint
      uses: actions/upload-artifact@v4
    with:
      run: bash -c "git push origin main"
      env:
        GITHUB_TOKEN: nested fake
    run: echo safe
"""
        safe_step = _unique_workflow_step(
            _extract_workflow_steps(safe_nested_fixture), "Safe nested data"
        )
        self.assertEqual("echo safe", _extract_run_script(safe_step))
        self.assertEqual([], _alternate_upload_paths(safe_nested_fixture))
        self.assertEqual(0, _count_executable_git_commands(safe_nested_fixture, "push"))
        self.assertEqual(set(), _forbidden_workflow_env(safe_nested_fixture))

    def test_duplicate_top_level_run_fields_reject_the_workflow_contract(self):
        duplicate_fixture = """
steps:
  - name: Duplicate run
    run: echo first
    run: echo second
"""
        self.assertTrue(_workflow_step_layout_reasons(duplicate_fixture))
        self.assertTrue(_workflow_contract_reasons(duplicate_fixture))

    def test_git_global_options_are_rejected_but_direct_git_is_safe(self):
        for command in (
            "git -C . add index.html",
            "git -c user.name=hidden commit -m hidden",
            "git --git-dir=.git push origin main",
            "git --work-tree=. push origin main",
        ):
            fixture = f"""
      - name: Hidden git global option
        run: {command}
"""
            with self.subTest(command=command):
                self.assertTrue(_git_global_option_reasons(fixture))

        safe_fixture = """
      - name: Direct git
        run: git commit -m direct
"""
        self.assertEqual([], _git_global_option_reasons(safe_fixture))

    def test_run_and_uses_scalars_reject_folded_forms(self):
        folded_fixtures = [
            """
      - name: Folded wrapper
        run: >
          bash -c "git commit -m hidden"
""",
            """
      - name: Folded curl
        run: >-
          curl -X PUT remote/endpoint
""",
            """
      - name: Folded upload action
        uses: >
          actions/upload-artifact@v4
""",
        ]
        for fixture in folded_fixtures:
            with self.subTest(fixture=fixture):
                self.assertTrue(_workflow_step_layout_reasons(fixture))

        self.assertEqual([], _workflow_step_layout_reasons(self.workflow))

    def test_duplicate_top_level_security_fields_reject_the_workflow_contract(self):
        duplicate_fixture = """
steps:
  - name: First name
    name: Second name
    uses: actions/checkout@v4
    uses: actions/setup-python@v5
    env:
      SAFE: one
    env:
      SAFE: two
    if: always()
    if: success()
    shell: bash
    shell: sh
    with:
      first: value
    with:
      second: value
"""
        reasons = _workflow_step_layout_reasons(duplicate_fixture)
        self.assertGreaterEqual(len(reasons), 5)
        self.assertTrue(_workflow_contract_reasons(duplicate_fixture))

    def test_shell_assignments_are_normalized_before_command_scans(self):
        wrapper_fixture = """
      - name: Hidden assignment wrapper
        run: FOO=1 BAR=2 bash -c "git commit -m hidden"
"""
        self.assertTrue(_wrapper_bypass_reasons(wrapper_fixture))

        upload_fixture = """
      - name: Hidden assignment upload
        run: FOO=1 BAR=2 curl --request=PUT remote/endpoint
"""
        self.assertTrue(_alternate_upload_paths(upload_fixture))

        sensitive_fixture = """
      - name: Sensitive assignment
        run: FOO=1 GITHUB_TOKEN=secret python build.py
"""
        self.assertEqual(
            {"GITHUB_TOKEN"},
            _forbidden_workflow_env(sensitive_fixture),
        )

        safe_fixture = """
      - name: Safe assignment
        run: FOO=1 BAR=2 python build.py
"""
        self.assertEqual([], _wrapper_bypass_reasons(safe_fixture))
        self.assertEqual([], _alternate_upload_paths(safe_fixture))

    def test_echo_segments_are_output_data_not_git_or_upload_commands(self):
        payload_fixture = """
      - name: Echo payload
        run: printf '%s' 'git' push origin main
"""
        self.assertEqual(0, _count_executable_git_commands(payload_fixture, "push"))
        self.assertEqual([], _alternate_upload_paths(payload_fixture))

        chained_fixture = """
      - name: Real second segment
        run: printf '%s' 'git' push origin main; git push origin main
"""
        self.assertEqual(1, _count_executable_git_commands(chained_fixture, "push"))

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
