"""GitHub pull request creation for AgentAutopsy auto-fixes."""

from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import requests


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_github_remote(url: str) -> tuple[str, str]:
    url = url.strip()
    patterns = [
        r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)",
        r"git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group("owner"), match.group("repo").removesuffix(".git")
    raise ValueError("Could not determine GitHub owner/repo from git remote")


def _git_remote_repo() -> tuple[str, str]:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        cwd=Path.cwd(),
    )
    if result.returncode != 0:
        raise ValueError("No git origin remote configured")
    return _parse_github_remote(result.stdout)


def _default_branch(token: str, owner: str, repo: str) -> str:
    response = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers=_github_headers(token),
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("default_branch", "main")


def _branch_sha(token: str, owner: str, repo: str, branch: str) -> str:
    response = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{branch}",
        headers=_github_headers(token),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["object"]["sha"]


def _create_branch(token: str, owner: str, repo: str, branch: str, sha: str) -> None:
    response = requests.post(
        f"https://api.github.com/repos/{owner}/{repo}/git/refs",
        headers=_github_headers(token),
        json={"ref": f"refs/heads/{branch}", "sha": sha},
        timeout=30,
    )
    if response.status_code == 422 and "Reference already exists" in response.text:
        return
    response.raise_for_status()


def _file_sha(token: str, owner: str, repo: str, path: str, branch: str) -> str | None:
    response = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        headers=_github_headers(token),
        params={"ref": branch},
        timeout=30,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json().get("sha")


def _commit_file(
    token: str,
    owner: str,
    repo: str,
    branch: str,
    path: str,
    message: str,
    content: str,
) -> None:
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    existing_sha = _file_sha(token, owner, repo, path, branch)
    if existing_sha:
        payload["sha"] = existing_sha

    response = requests.put(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        headers=_github_headers(token),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def create_pr(
    run_id: str,
    fix_description: str,
    *,
    error_type: str = "agent failure",
    root_cause: str = "",
    fix_applied: str = "",
    test_results: str = "",
    file_path: str | None = None,
) -> dict[str, Any]:
    """Create a GitHub branch, commit the fix, and open a pull request."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is required")

    owner, repo = _git_remote_repo()
    base_branch = _default_branch(token, owner, repo)
    branch = f"agentautopsy-fix-{run_id[:8]}"
    base_sha = _branch_sha(token, owner, repo, base_branch)
    _create_branch(token, owner, repo, branch, base_sha)

    if file_path:
        path = Path(file_path)
        if path.exists():
            rel_path = str(path.relative_to(Path.cwd())).replace("\\", "/")
            _commit_file(
                token,
                owner,
                repo,
                branch,
                rel_path,
                f"AgentAutopsy auto-fix for {error_type}",
                path.read_text(encoding="utf-8"),
            )

    title = f"AgentAutopsy: Auto-fix for {error_type}"
    body = (
        "## AgentAutopsy Auto-fix\n\n"
        f"**Run ID:** `{run_id}`\n\n"
        f"### Root Cause\n{root_cause or 'Unknown'}\n\n"
        f"### Fix Applied\n{fix_applied or fix_description}\n\n"
        f"### Test Results\n```\n{test_results or 'No test output'}\n```\n"
    )

    response = requests.post(
        f"https://api.github.com/repos/{owner}/{repo}/pulls",
        headers=_github_headers(token),
        json={
            "title": title,
            "head": branch,
            "base": base_branch,
            "body": body,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "pr_url": data.get("html_url"),
        "pr_number": data.get("number"),
        "branch": branch,
    }
