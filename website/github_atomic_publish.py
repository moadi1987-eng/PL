"""Atomic GitHub publication for the locally opted-in dashboard workflow."""

import base64
import re
import subprocess


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _repository_from_remote(value):
    remote = str(value or "").strip()
    patterns = (
        r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?$",
        r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, remote)
        if match:
            return match.group(1)
    return remote if _REPOSITORY.fullmatch(remote) else None


def resolve_target_repository(explicit=None, *, cwd=None, runner=subprocess.run):
    """Resolve an owner/repository target without assuming a project identity."""
    if explicit:
        repository = _repository_from_remote(explicit)
        if repository:
            return repository
        raise RuntimeError("invalid explicit GitHub repository")
    result = runner(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    repository = _repository_from_remote(result.stdout) if result.returncode == 0 else None
    if not repository:
        raise RuntimeError("GitHub repository is not configured and origin is not a GitHub checkout")
    return repository


def _require_success(response, action, expected_statuses):
    if response.status_code not in expected_statuses:
        raise RuntimeError(f"{action} failed: HTTP {response.status_code}")
    return response.json()


def publish_generated_outputs(repo, token, files, requester, branch="main"):
    """Publish all generated outputs by advancing the branch ref once."""
    contents = []
    for path, local_path in files.items():
        with open(local_path, "rb") as output_file:
            contents.append((path, output_file.read()))
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    base_url = f"https://api.github.com/repos/{repo}/git"

    ref = _require_success(
        requester("GET", f"{base_url}/ref/heads/{branch}", headers=headers, json=None, timeout=15),
        "read branch ref",
        {200},
    )
    expected_head = ref["object"]["sha"]
    commit = _require_success(
        requester("GET", f"{base_url}/commits/{expected_head}", headers=headers, json=None, timeout=15),
        "read branch commit",
        {200},
    )
    base_tree = commit["tree"]["sha"]

    tree_entries = []
    for path, content in contents:
        blob = _require_success(
            requester(
                "POST",
                f"{base_url}/blobs",
                headers=headers,
                json={"content": base64.b64encode(content).decode("ascii"), "encoding": "base64"},
                timeout=15,
            ),
            "create blob",
            {201},
        )
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    tree = _require_success(
        requester(
            "POST",
            f"{base_url}/trees",
            headers=headers,
            json={"base_tree": base_tree, "tree": tree_entries},
            timeout=15,
        ),
        "create tree",
        {201},
    )
    new_commit = _require_success(
        requester(
            "POST",
            f"{base_url}/commits",
            headers=headers,
            json={
                "message": "Update PL Dashboard data",
                "tree": tree["sha"],
                "parents": [expected_head],
            },
            timeout=15,
        ),
        "create commit",
        {201},
    )
    _require_success(
        requester(
            "PATCH",
            f"{base_url}/refs/heads/{branch}",
            headers=headers,
            json={"sha": new_commit["sha"], "force": False},
            timeout=15,
        ),
        "advance branch ref",
        {200},
    )
