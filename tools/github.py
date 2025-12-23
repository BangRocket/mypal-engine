"""GitHub API tools.

Provides comprehensive GitHub integration via the REST API.
Covers repositories, issues, pull requests, actions, gists, and more.

Requires: GITHUB_TOKEN env var (Personal Access Token)
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from ._base import ToolContext, ToolDef

MODULE_NAME = "github"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## GitHub Integration
You can interact with GitHub repositories, issues, pull requests, and workflows.

**Repository Tools:**
- `github_search_repositories` - Search for repositories
- `github_get_repository` - Get repo details (stats, description, topics)
- `github_list_branches` / `github_list_tags` - List branches and tags
- `github_list_commits` / `github_get_commit` - View commit history

**Issues & PRs:**
- `github_list_issues` / `github_get_issue` / `github_create_issue` - Manage issues
- `github_list_pull_requests` / `github_get_pull_request` / `github_create_pull_request` - Manage PRs
- `github_list_pr_files` / `github_list_pr_commits` - View PR details

**Code & Files:**
- `github_get_file_contents` - Read files from repos
- `github_create_or_update_file` - Create or update files
- `github_search_code` - Search code across GitHub

**Actions & Workflows:**
- `github_list_workflows` / `github_list_workflow_runs` - View workflows
- `github_run_workflow` - Trigger a workflow

**Other:**
- `github_get_me` - Get authenticated user info
- `github_list_gists` / `github_create_gist` - Manage gists
- `github_list_notifications` - View notifications
""".strip()

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_URL = "https://api.github.com"


def is_configured() -> bool:
    """Check if GitHub is configured."""
    return bool(GITHUB_TOKEN)


def _get_headers() -> dict[str, str]:
    """Get headers for GitHub API requests."""
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _github_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
    json_data: dict | None = None,
) -> dict | list | str:
    """Make a GitHub API request."""
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN not configured")

    url = f"{GITHUB_API_URL}{endpoint}"

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            url,
            headers=_get_headers(),
            params=params,
            json=json_data,
            timeout=30.0,
        )

        if response.status_code == 204:
            return {"success": True}

        if response.status_code >= 400:
            error_msg = response.text
            try:
                error_data = response.json()
                error_msg = error_data.get("message", response.text)
            except Exception:
                pass
            raise ValueError(f"GitHub API error ({response.status_code}): {error_msg}")

        return response.json()


# =============================================================================
# Context / User Tools
# =============================================================================


async def get_me(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the authenticated user's profile."""
    try:
        user = await _github_request("GET", "/user")
        return json.dumps(user, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def search_users(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for GitHub users."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await _github_request(
            "GET", "/search/users", params={"q": query, "per_page": per_page}
        )
        users = [
            {"login": u["login"], "url": u["html_url"], "type": u["type"]}
            for u in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "users": users}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Repository Tools
# =============================================================================


async def search_repositories(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for repositories."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 10), 100)
    sort = args.get("sort", "best-match")

    try:
        result = await _github_request(
            "GET",
            "/search/repositories",
            params={"q": query, "per_page": per_page, "sort": sort},
        )
        repos = [
            {
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "url": r["html_url"],
                "stars": r["stargazers_count"],
                "language": r.get("language"),
            }
            for r in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "repositories": repos},
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get repository details."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        result = await _github_request("GET", f"/repos/{owner}/{repo}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new repository."""
    name = args.get("name", "")
    if not name:
        return "Error: name is required"

    data = {
        "name": name,
        "description": args.get("description", ""),
        "private": args.get("private", False),
        "auto_init": args.get("auto_init", False),
    }

    try:
        result = await _github_request("POST", "/user/repos", json_data=data)
        return json.dumps(
            {
                "created": True,
                "full_name": result["full_name"],
                "url": result["html_url"],
                "clone_url": result["clone_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def fork_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Fork a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    data = {}
    if args.get("organization"):
        data["organization"] = args["organization"]

    try:
        result = await _github_request(
            "POST", f"/repos/{owner}/{repo}/forks", json_data=data
        )
        return json.dumps(
            {
                "forked": True,
                "full_name": result["full_name"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_file_contents(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get file or directory contents from a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {}
    if args.get("ref"):
        params["ref"] = args["ref"]

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params=params
        )

        if isinstance(result, list):
            # Directory listing
            items = [
                {"name": item["name"], "type": item["type"], "path": item["path"]}
                for item in result
            ]
            return json.dumps({"type": "directory", "items": items}, indent=2)
        else:
            # File content
            if result.get("encoding") == "base64" and result.get("content"):
                content = base64.b64decode(result["content"]).decode("utf-8")
                return json.dumps(
                    {
                        "type": "file",
                        "path": result["path"],
                        "size": result["size"],
                        "content": content,
                    },
                    indent=2,
                )
            return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_or_update_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create or update a file in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    content = args.get("content", "")
    message = args.get("message", "")

    if not all([owner, repo, path, message]):
        return "Error: owner, repo, path, and message are required"

    data = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }

    if args.get("branch"):
        data["branch"] = args["branch"]

    # Check if file exists to get SHA
    if args.get("sha"):
        data["sha"] = args["sha"]
    else:
        try:
            existing = await _github_request(
                "GET", f"/repos/{owner}/{repo}/contents/{path}"
            )
            if isinstance(existing, dict) and existing.get("sha"):
                data["sha"] = existing["sha"]
        except Exception:
            pass  # File doesn't exist, creating new

    try:
        result = await _github_request(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}", json_data=data
        )
        return json.dumps(
            {
                "success": True,
                "path": result["content"]["path"],
                "sha": result["content"]["sha"],
                "commit_sha": result["commit"]["sha"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def delete_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a file from a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    message = args.get("message", "")
    sha = args.get("sha", "")

    if not all([owner, repo, path, message, sha]):
        return "Error: owner, repo, path, message, and sha are required"

    data = {"message": message, "sha": sha}
    if args.get("branch"):
        data["branch"] = args["branch"]

    try:
        result = await _github_request(
            "DELETE", f"/repos/{owner}/{repo}/contents/{path}", json_data=data
        )
        return json.dumps(
            {"deleted": True, "commit_sha": result["commit"]["sha"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def list_branches(args: dict[str, Any], ctx: ToolContext) -> str:
    """List branches in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    per_page = min(args.get("per_page", 30), 100)

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/branches", params={"per_page": per_page}
        )
        branches = [
            {"name": b["name"], "protected": b.get("protected", False)} for b in result
        ]
        return json.dumps(branches, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_branch(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new branch."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    branch = args.get("branch", "")
    from_branch = args.get("from_branch", "main")

    if not all([owner, repo, branch]):
        return "Error: owner, repo, and branch are required"

    try:
        # Get the SHA of the source branch
        ref_result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/git/refs/heads/{from_branch}"
        )
        sha = ref_result["object"]["sha"]

        # Create the new branch
        result = await _github_request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json_data={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return json.dumps(
            {"created": True, "branch": branch, "sha": result["object"]["sha"]},
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def list_commits(args: dict[str, Any], ctx: ToolContext) -> str:
    """List commits in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {"per_page": min(args.get("per_page", 20), 100)}
    if args.get("sha"):
        params["sha"] = args["sha"]
    if args.get("path"):
        params["path"] = args["path"]

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/commits", params=params
        )
        commits = [
            {
                "sha": c["sha"][:7],
                "message": c["commit"]["message"].split("\n")[0],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
            }
            for c in result
        ]
        return json.dumps(commits, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_commit(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific commit."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    sha = args.get("sha", "")
    if not all([owner, repo, sha]):
        return "Error: owner, repo, and sha are required"

    try:
        result = await _github_request("GET", f"/repos/{owner}/{repo}/commits/{sha}")
        return json.dumps(
            {
                "sha": result["sha"],
                "message": result["commit"]["message"],
                "author": result["commit"]["author"],
                "stats": result.get("stats"),
                "files": [
                    {"filename": f["filename"], "status": f["status"]}
                    for f in result.get("files", [])
                ],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def search_code(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for code across repositories."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await _github_request(
            "GET", "/search/code", params={"q": query, "per_page": per_page}
        )
        items = [
            {
                "name": item["name"],
                "path": item["path"],
                "repository": item["repository"]["full_name"],
                "url": item["html_url"],
            }
            for item in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "items": items}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def get_repository_tree(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the file tree of a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    tree_sha = args.get("tree_sha", "HEAD")
    recursive = args.get("recursive", True)

    try:
        params = {}
        if recursive:
            params["recursive"] = "1"

        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params
        )
        tree = [
            {"path": item["path"], "type": item["type"], "size": item.get("size")}
            for item in result.get("tree", [])
        ]
        return json.dumps({"sha": result["sha"], "tree": tree}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Issues Tools
# =============================================================================


async def list_issues(args: dict[str, Any], ctx: ToolContext) -> str:
    """List issues in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {
        "state": args.get("state", "open"),
        "per_page": min(args.get("per_page", 20), 100),
    }
    if args.get("labels"):
        params["labels"] = args["labels"]
    if args.get("assignee"):
        params["assignee"] = args["assignee"]

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/issues", params=params
        )
        issues = [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "user": i["user"]["login"],
                "labels": [l["name"] for l in i.get("labels", [])],
                "created_at": i["created_at"],
            }
            for i in result
            if "pull_request" not in i  # Exclude PRs
        ]
        return json.dumps(issues, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_issue(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    if not all([owner, repo, issue_number]):
        return "Error: owner, repo, and issue_number are required"

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/issues/{issue_number}"
        )
        return json.dumps(
            {
                "number": result["number"],
                "title": result["title"],
                "state": result["state"],
                "body": result.get("body", ""),
                "user": result["user"]["login"],
                "labels": [l["name"] for l in result.get("labels", [])],
                "assignees": [a["login"] for a in result.get("assignees", [])],
                "created_at": result["created_at"],
                "updated_at": result["updated_at"],
                "comments": result["comments"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_issue(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    title = args.get("title", "")
    if not all([owner, repo, title]):
        return "Error: owner, repo, and title are required"

    data = {"title": title}
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("labels"):
        data["labels"] = args["labels"]
    if args.get("assignees"):
        data["assignees"] = args["assignees"]

    try:
        result = await _github_request(
            "POST", f"/repos/{owner}/{repo}/issues", json_data=data
        )
        return json.dumps(
            {
                "created": True,
                "number": result["number"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_issue(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update an existing issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    if not all([owner, repo, issue_number]):
        return "Error: owner, repo, and issue_number are required"

    data = {}
    if args.get("title"):
        data["title"] = args["title"]
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("state"):
        data["state"] = args["state"]
    if args.get("labels"):
        data["labels"] = args["labels"]
    if args.get("assignees"):
        data["assignees"] = args["assignees"]

    if not data:
        return "Error: at least one field to update is required"

    try:
        result = await _github_request(
            "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json_data=data
        )
        return json.dumps(
            {
                "updated": True,
                "number": result["number"],
                "state": result["state"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def add_issue_comment(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add a comment to an issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    body = args.get("body", "")
    if not all([owner, repo, issue_number, body]):
        return "Error: owner, repo, issue_number, and body are required"

    try:
        result = await _github_request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json_data={"body": body},
        )
        return json.dumps(
            {"created": True, "id": result["id"], "url": result["html_url"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def list_issue_comments(args: dict[str, Any], ctx: ToolContext) -> str:
    """List comments on an issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    if not all([owner, repo, issue_number]):
        return "Error: owner, repo, and issue_number are required"

    per_page = min(args.get("per_page", 20), 100)

    try:
        result = await _github_request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            params={"per_page": per_page},
        )
        comments = [
            {
                "id": c["id"],
                "user": c["user"]["login"],
                "body": c["body"],
                "created_at": c["created_at"],
            }
            for c in result
        ]
        return json.dumps(comments, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def search_issues(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for issues and pull requests."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 20), 100)

    try:
        result = await _github_request(
            "GET", "/search/issues", params={"q": query, "per_page": per_page}
        )
        items = [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "repository": i["repository_url"].split("/")[-2:]
                if "repository_url" in i
                else None,
                "url": i["html_url"],
                "is_pr": "pull_request" in i,
            }
            for i in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "items": items}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Pull Request Tools
# =============================================================================


async def list_pull_requests(args: dict[str, Any], ctx: ToolContext) -> str:
    """List pull requests in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {
        "state": args.get("state", "open"),
        "per_page": min(args.get("per_page", 20), 100),
    }
    if args.get("base"):
        params["base"] = args["base"]
    if args.get("head"):
        params["head"] = args["head"]

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/pulls", params=params
        )
        prs = [
            {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "user": pr["user"]["login"],
                "head": pr["head"]["ref"],
                "base": pr["base"]["ref"],
                "draft": pr.get("draft", False),
                "created_at": pr["created_at"],
            }
            for pr in result
        ]
        return json.dumps(prs, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}"
        )
        return json.dumps(
            {
                "number": result["number"],
                "title": result["title"],
                "state": result["state"],
                "body": result.get("body", ""),
                "user": result["user"]["login"],
                "head": {"ref": result["head"]["ref"], "sha": result["head"]["sha"]},
                "base": {"ref": result["base"]["ref"]},
                "mergeable": result.get("mergeable"),
                "merged": result.get("merged", False),
                "draft": result.get("draft", False),
                "additions": result.get("additions"),
                "deletions": result.get("deletions"),
                "changed_files": result.get("changed_files"),
                "created_at": result["created_at"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    title = args.get("title", "")
    head = args.get("head", "")
    base = args.get("base", "main")

    if not all([owner, repo, title, head]):
        return "Error: owner, repo, title, and head are required"

    data = {"title": title, "head": head, "base": base}
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("draft"):
        data["draft"] = args["draft"]

    try:
        result = await _github_request(
            "POST", f"/repos/{owner}/{repo}/pulls", json_data=data
        )
        return json.dumps(
            {
                "created": True,
                "number": result["number"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update an existing pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    data = {}
    if args.get("title"):
        data["title"] = args["title"]
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("state"):
        data["state"] = args["state"]
    if args.get("base"):
        data["base"] = args["base"]

    if not data:
        return "Error: at least one field to update is required"

    try:
        result = await _github_request(
            "PATCH", f"/repos/{owner}/{repo}/pulls/{pull_number}", json_data=data
        )
        return json.dumps(
            {
                "updated": True,
                "number": result["number"],
                "state": result["state"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def merge_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Merge a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    data = {}
    if args.get("commit_title"):
        data["commit_title"] = args["commit_title"]
    if args.get("commit_message"):
        data["commit_message"] = args["commit_message"]
    if args.get("merge_method"):
        data["merge_method"] = args["merge_method"]  # merge, squash, rebase

    try:
        result = await _github_request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{pull_number}/merge", json_data=data
        )
        return json.dumps(
            {
                "merged": result.get("merged", True),
                "sha": result.get("sha"),
                "message": result.get("message"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_pull_request_diff(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the diff of a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pull_number}"
        headers = _get_headers()
        headers["Accept"] = "application/vnd.github.diff"

        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=30.0)
            response.raise_for_status()
            return response.text
    except Exception as e:
        return f"Error: {e}"


async def list_pull_request_files(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files changed in a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/files"
        )
        files = [
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
            }
            for f in result
        ]
        return json.dumps(files, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# GitHub Actions Tools
# =============================================================================


async def list_workflows(args: dict[str, Any], ctx: ToolContext) -> str:
    """List workflows in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        result = await _github_request("GET", f"/repos/{owner}/{repo}/actions/workflows")
        workflows = [
            {
                "id": w["id"],
                "name": w["name"],
                "path": w["path"],
                "state": w["state"],
            }
            for w in result.get("workflows", [])
        ]
        return json.dumps(workflows, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_workflow_runs(args: dict[str, Any], ctx: ToolContext) -> str:
    """List workflow runs in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {"per_page": min(args.get("per_page", 10), 100)}
    if args.get("workflow_id"):
        params["workflow_id"] = args["workflow_id"]
    if args.get("branch"):
        params["branch"] = args["branch"]
    if args.get("status"):
        params["status"] = args["status"]

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/actions/runs", params=params
        )
        runs = [
            {
                "id": r["id"],
                "name": r["name"],
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "branch": r["head_branch"],
                "event": r["event"],
                "created_at": r["created_at"],
                "url": r["html_url"],
            }
            for r in result.get("workflow_runs", [])
        ]
        return json.dumps({"total_count": result.get("total_count", 0), "runs": runs}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_workflow_run(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific workflow run."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    run_id = args.get("run_id")
    if not all([owner, repo, run_id]):
        return "Error: owner, repo, and run_id are required"

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}"
        )
        return json.dumps(
            {
                "id": result["id"],
                "name": result["name"],
                "status": result["status"],
                "conclusion": result.get("conclusion"),
                "branch": result["head_branch"],
                "sha": result["head_sha"][:7],
                "event": result["event"],
                "created_at": result["created_at"],
                "updated_at": result["updated_at"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def run_workflow(args: dict[str, Any], ctx: ToolContext) -> str:
    """Trigger a workflow run."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    workflow_id = args.get("workflow_id", "")
    ref = args.get("ref", "main")

    if not all([owner, repo, workflow_id]):
        return "Error: owner, repo, and workflow_id are required"

    data = {"ref": ref}
    if args.get("inputs"):
        data["inputs"] = args["inputs"]

    try:
        await _github_request(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
            json_data=data,
        )
        return json.dumps({"triggered": True, "workflow_id": workflow_id, "ref": ref}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def cancel_workflow_run(args: dict[str, Any], ctx: ToolContext) -> str:
    """Cancel a workflow run."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    run_id = args.get("run_id")
    if not all([owner, repo, run_id]):
        return "Error: owner, repo, and run_id are required"

    try:
        await _github_request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
        )
        return json.dumps({"cancelled": True, "run_id": run_id}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def rerun_workflow(args: dict[str, Any], ctx: ToolContext) -> str:
    """Re-run a workflow."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    run_id = args.get("run_id")
    if not all([owner, repo, run_id]):
        return "Error: owner, repo, and run_id are required"

    try:
        await _github_request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun"
        )
        return json.dumps({"rerun": True, "run_id": run_id}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Gists Tools
# =============================================================================


async def list_gists(args: dict[str, Any], ctx: ToolContext) -> str:
    """List gists for the authenticated user."""
    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await _github_request("GET", "/gists", params={"per_page": per_page})
        gists = [
            {
                "id": g["id"],
                "description": g.get("description", ""),
                "public": g["public"],
                "files": list(g["files"].keys()),
                "url": g["html_url"],
                "created_at": g["created_at"],
            }
            for g in result
        ]
        return json.dumps(gists, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get a specific gist."""
    gist_id = args.get("gist_id", "")
    if not gist_id:
        return "Error: gist_id is required"

    try:
        result = await _github_request("GET", f"/gists/{gist_id}")
        files = {
            name: {"content": f.get("content", ""), "language": f.get("language")}
            for name, f in result.get("files", {}).items()
        }
        return json.dumps(
            {
                "id": result["id"],
                "description": result.get("description", ""),
                "public": result["public"],
                "files": files,
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new gist."""
    files = args.get("files", {})
    if not files:
        return "Error: files is required (dict of filename: content)"

    data = {
        "files": {name: {"content": content} for name, content in files.items()},
        "public": args.get("public", False),
    }
    if args.get("description"):
        data["description"] = args["description"]

    try:
        result = await _github_request("POST", "/gists", json_data=data)
        return json.dumps(
            {
                "created": True,
                "id": result["id"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update an existing gist."""
    gist_id = args.get("gist_id", "")
    if not gist_id:
        return "Error: gist_id is required"

    data = {}
    if args.get("description"):
        data["description"] = args["description"]
    if args.get("files"):
        data["files"] = {
            name: {"content": content} for name, content in args["files"].items()
        }

    if not data:
        return "Error: at least description or files is required"

    try:
        result = await _github_request("PATCH", f"/gists/{gist_id}", json_data=data)
        return json.dumps(
            {"updated": True, "id": result["id"], "url": result["html_url"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def delete_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a gist."""
    gist_id = args.get("gist_id", "")
    if not gist_id:
        return "Error: gist_id is required"

    try:
        await _github_request("DELETE", f"/gists/{gist_id}")
        return json.dumps({"deleted": True, "gist_id": gist_id}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Releases & Tags Tools
# =============================================================================


async def list_releases(args: dict[str, Any], ctx: ToolContext) -> str:
    """List releases in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/releases", params={"per_page": per_page}
        )
        releases = [
            {
                "id": r["id"],
                "tag_name": r["tag_name"],
                "name": r.get("name", ""),
                "draft": r["draft"],
                "prerelease": r["prerelease"],
                "created_at": r["created_at"],
                "url": r["html_url"],
            }
            for r in result
        ]
        return json.dumps(releases, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_latest_release(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the latest release of a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/releases/latest"
        )
        return json.dumps(
            {
                "tag_name": result["tag_name"],
                "name": result.get("name", ""),
                "body": result.get("body", ""),
                "draft": result["draft"],
                "prerelease": result["prerelease"],
                "created_at": result["created_at"],
                "url": result["html_url"],
                "assets": [
                    {"name": a["name"], "download_url": a["browser_download_url"]}
                    for a in result.get("assets", [])
                ],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def list_tags(args: dict[str, Any], ctx: ToolContext) -> str:
    """List tags in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    per_page = min(args.get("per_page", 20), 100)

    try:
        result = await _github_request(
            "GET", f"/repos/{owner}/{repo}/tags", params={"per_page": per_page}
        )
        tags = [{"name": t["name"], "sha": t["commit"]["sha"][:7]} for t in result]
        return json.dumps(tags, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Notifications Tools
# =============================================================================


async def list_notifications(args: dict[str, Any], ctx: ToolContext) -> str:
    """List notifications for the authenticated user."""
    params = {
        "all": args.get("all", False),
        "per_page": min(args.get("per_page", 20), 100),
    }

    try:
        result = await _github_request("GET", "/notifications", params=params)
        notifications = [
            {
                "id": n["id"],
                "reason": n["reason"],
                "unread": n["unread"],
                "subject": {
                    "title": n["subject"]["title"],
                    "type": n["subject"]["type"],
                },
                "repository": n["repository"]["full_name"],
                "updated_at": n["updated_at"],
            }
            for n in result
        ]
        return json.dumps(notifications, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def mark_notifications_read(args: dict[str, Any], ctx: ToolContext) -> str:
    """Mark all notifications as read."""
    try:
        await _github_request("PUT", "/notifications")
        return json.dumps({"marked_read": True}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Stars Tools
# =============================================================================


async def list_starred_repos(args: dict[str, Any], ctx: ToolContext) -> str:
    """List repositories starred by the authenticated user."""
    per_page = min(args.get("per_page", 20), 100)
    sort = args.get("sort", "created")

    try:
        result = await _github_request(
            "GET", "/user/starred", params={"per_page": per_page, "sort": sort}
        )
        repos = [
            {
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "stars": r["stargazers_count"],
                "url": r["html_url"],
            }
            for r in result
        ]
        return json.dumps(repos, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def star_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Star a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        await _github_request("PUT", f"/user/starred/{owner}/{repo}")
        return json.dumps({"starred": True, "repository": f"{owner}/{repo}"}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def unstar_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Unstar a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        await _github_request("DELETE", f"/user/starred/{owner}/{repo}")
        return json.dumps({"unstarred": True, "repository": f"{owner}/{repo}"}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    # Context / User
    ToolDef(
        name="github_get_me",
        description="Get the authenticated GitHub user's profile information.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=get_me,
    ),
    ToolDef(
        name="github_search_users",
        description="Search for GitHub users by username, name, or other criteria.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "per_page": {"type": "integer", "description": "Results per page (max 100)"},
            },
            "required": ["query"],
        },
        handler=search_users,
    ),
    # Repositories
    ToolDef(
        name="github_search_repositories",
        description="Search for GitHub repositories.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g., 'language:python stars:>1000')"},
                "per_page": {"type": "integer", "description": "Results per page (max 100)"},
                "sort": {"type": "string", "enum": ["stars", "forks", "updated", "best-match"]},
            },
            "required": ["query"],
        },
        handler=search_repositories,
    ),
    ToolDef(
        name="github_get_repository",
        description="Get detailed information about a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_repository,
    ),
    ToolDef(
        name="github_create_repository",
        description="Create a new repository for the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repository name"},
                "description": {"type": "string", "description": "Repository description"},
                "private": {"type": "boolean", "description": "Whether the repo is private"},
                "auto_init": {"type": "boolean", "description": "Initialize with README"},
            },
            "required": ["name"],
        },
        handler=create_repository,
    ),
    ToolDef(
        name="github_fork_repository",
        description="Fork a repository to your account or an organization.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "organization": {"type": "string", "description": "Organization to fork to (optional)"},
            },
            "required": ["owner", "repo"],
        },
        handler=fork_repository,
    ),
    ToolDef(
        name="github_get_file_contents",
        description="Get the contents of a file or directory from a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file or directory"},
                "ref": {"type": "string", "description": "Git ref (branch, tag, or SHA)"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_file_contents,
    ),
    ToolDef(
        name="github_create_or_update_file",
        description="Create or update a file in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file"},
                "content": {"type": "string", "description": "File content"},
                "message": {"type": "string", "description": "Commit message"},
                "branch": {"type": "string", "description": "Branch name"},
                "sha": {"type": "string", "description": "SHA of file being replaced (for updates)"},
            },
            "required": ["owner", "repo", "path", "content", "message"],
        },
        handler=create_or_update_file,
    ),
    ToolDef(
        name="github_delete_file",
        description="Delete a file from a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file"},
                "message": {"type": "string", "description": "Commit message"},
                "sha": {"type": "string", "description": "SHA of file to delete"},
                "branch": {"type": "string", "description": "Branch name"},
            },
            "required": ["owner", "repo", "path", "message", "sha"],
        },
        handler=delete_file,
    ),
    ToolDef(
        name="github_list_branches",
        description="List branches in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_branches,
    ),
    ToolDef(
        name="github_create_branch",
        description="Create a new branch in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "New branch name"},
                "from_branch": {"type": "string", "description": "Source branch (default: main)"},
            },
            "required": ["owner", "repo", "branch"],
        },
        handler=create_branch,
    ),
    ToolDef(
        name="github_list_commits",
        description="List commits in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "sha": {"type": "string", "description": "Branch or SHA to list commits from"},
                "path": {"type": "string", "description": "Only commits containing this path"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_commits,
    ),
    ToolDef(
        name="github_get_commit",
        description="Get details of a specific commit.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "sha": {"type": "string", "description": "Commit SHA"},
            },
            "required": ["owner", "repo", "sha"],
        },
        handler=get_commit,
    ),
    ToolDef(
        name="github_search_code",
        description="Search for code across GitHub repositories.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g., 'addClass repo:jquery/jquery')"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["query"],
        },
        handler=search_code,
    ),
    ToolDef(
        name="github_get_repository_tree",
        description="Get the file tree of a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "tree_sha": {"type": "string", "description": "Tree SHA or 'HEAD' (default)"},
                "recursive": {"type": "boolean", "description": "Get full tree recursively"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_repository_tree,
    ),
    # Issues
    ToolDef(
        name="github_list_issues",
        description="List issues in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "labels": {"type": "string", "description": "Comma-separated label names"},
                "assignee": {"type": "string", "description": "Filter by assignee"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_issues,
    ),
    ToolDef(
        name="github_get_issue",
        description="Get details of a specific issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        handler=get_issue,
    ),
    ToolDef(
        name="github_create_issue",
        description="Create a new issue in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to add"},
                "assignees": {"type": "array", "items": {"type": "string"}, "description": "Assignees"},
            },
            "required": ["owner", "repo", "title"],
        },
        handler=create_issue,
    ),
    ToolDef(
        name="github_update_issue",
        description="Update an existing issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
                "title": {"type": "string", "description": "New title"},
                "body": {"type": "string", "description": "New body"},
                "state": {"type": "string", "enum": ["open", "closed"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "assignees": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        handler=update_issue,
    ),
    ToolDef(
        name="github_add_issue_comment",
        description="Add a comment to an issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
                "body": {"type": "string", "description": "Comment body"},
            },
            "required": ["owner", "repo", "issue_number", "body"],
        },
        handler=add_issue_comment,
    ),
    ToolDef(
        name="github_list_issue_comments",
        description="List comments on an issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        handler=list_issue_comments,
    ),
    ToolDef(
        name="github_search_issues",
        description="Search for issues and pull requests across GitHub.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g., 'is:open is:issue repo:owner/repo')"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["query"],
        },
        handler=search_issues,
    ),
    # Pull Requests
    ToolDef(
        name="github_list_pull_requests",
        description="List pull requests in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "base": {"type": "string", "description": "Filter by base branch"},
                "head": {"type": "string", "description": "Filter by head branch"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_pull_requests,
    ),
    ToolDef(
        name="github_get_pull_request",
        description="Get details of a specific pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=get_pull_request,
    ),
    ToolDef(
        name="github_create_pull_request",
        description="Create a new pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "PR title"},
                "head": {"type": "string", "description": "Head branch (source)"},
                "base": {"type": "string", "description": "Base branch (target, default: main)"},
                "body": {"type": "string", "description": "PR description"},
                "draft": {"type": "boolean", "description": "Create as draft PR"},
            },
            "required": ["owner", "repo", "title", "head"],
        },
        handler=create_pull_request,
    ),
    ToolDef(
        name="github_update_pull_request",
        description="Update an existing pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "title": {"type": "string", "description": "New title"},
                "body": {"type": "string", "description": "New body"},
                "state": {"type": "string", "enum": ["open", "closed"]},
                "base": {"type": "string", "description": "New base branch"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=update_pull_request,
    ),
    ToolDef(
        name="github_merge_pull_request",
        description="Merge a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "commit_title": {"type": "string", "description": "Title for merge commit"},
                "commit_message": {"type": "string", "description": "Message for merge commit"},
                "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=merge_pull_request,
    ),
    ToolDef(
        name="github_get_pull_request_diff",
        description="Get the diff of a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=get_pull_request_diff,
    ),
    ToolDef(
        name="github_list_pull_request_files",
        description="List files changed in a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=list_pull_request_files,
    ),
    # GitHub Actions
    ToolDef(
        name="github_list_workflows",
        description="List workflows in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_workflows,
    ),
    ToolDef(
        name="github_list_workflow_runs",
        description="List workflow runs in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "workflow_id": {"type": "string", "description": "Filter by workflow ID or filename"},
                "branch": {"type": "string", "description": "Filter by branch"},
                "status": {"type": "string", "enum": ["queued", "in_progress", "completed"]},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_workflow_runs,
    ),
    ToolDef(
        name="github_get_workflow_run",
        description="Get details of a specific workflow run.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {"type": "integer", "description": "Workflow run ID"},
            },
            "required": ["owner", "repo", "run_id"],
        },
        handler=get_workflow_run,
    ),
    ToolDef(
        name="github_run_workflow",
        description="Trigger a workflow run.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "workflow_id": {"type": "string", "description": "Workflow ID or filename"},
                "ref": {"type": "string", "description": "Git ref to run on (default: main)"},
                "inputs": {"type": "object", "description": "Workflow inputs"},
            },
            "required": ["owner", "repo", "workflow_id"],
        },
        handler=run_workflow,
    ),
    ToolDef(
        name="github_cancel_workflow_run",
        description="Cancel a workflow run.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {"type": "integer", "description": "Workflow run ID"},
            },
            "required": ["owner", "repo", "run_id"],
        },
        handler=cancel_workflow_run,
    ),
    ToolDef(
        name="github_rerun_workflow",
        description="Re-run a workflow.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {"type": "integer", "description": "Workflow run ID"},
            },
            "required": ["owner", "repo", "run_id"],
        },
        handler=rerun_workflow,
    ),
    # Gists
    ToolDef(
        name="github_list_gists",
        description="List gists for the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": [],
        },
        handler=list_gists,
    ),
    ToolDef(
        name="github_get_gist",
        description="Get a specific gist with its contents.",
        parameters={
            "type": "object",
            "properties": {
                "gist_id": {"type": "string", "description": "Gist ID"},
            },
            "required": ["gist_id"],
        },
        handler=get_gist,
    ),
    ToolDef(
        name="github_create_gist",
        description="Create a new gist.",
        parameters={
            "type": "object",
            "properties": {
                "files": {"type": "object", "description": "Object mapping filename to content"},
                "description": {"type": "string", "description": "Gist description"},
                "public": {"type": "boolean", "description": "Whether the gist is public"},
            },
            "required": ["files"],
        },
        handler=create_gist,
    ),
    ToolDef(
        name="github_update_gist",
        description="Update an existing gist.",
        parameters={
            "type": "object",
            "properties": {
                "gist_id": {"type": "string", "description": "Gist ID"},
                "files": {"type": "object", "description": "Object mapping filename to content"},
                "description": {"type": "string", "description": "New description"},
            },
            "required": ["gist_id"],
        },
        handler=update_gist,
    ),
    ToolDef(
        name="github_delete_gist",
        description="Delete a gist.",
        parameters={
            "type": "object",
            "properties": {
                "gist_id": {"type": "string", "description": "Gist ID"},
            },
            "required": ["gist_id"],
        },
        handler=delete_gist,
    ),
    # Releases & Tags
    ToolDef(
        name="github_list_releases",
        description="List releases in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_releases,
    ),
    ToolDef(
        name="github_get_latest_release",
        description="Get the latest release of a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_latest_release,
    ),
    ToolDef(
        name="github_list_tags",
        description="List tags in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_tags,
    ),
    # Notifications
    ToolDef(
        name="github_list_notifications",
        description="List notifications for the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Include read notifications"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": [],
        },
        handler=list_notifications,
    ),
    ToolDef(
        name="github_mark_notifications_read",
        description="Mark all notifications as read.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=mark_notifications_read,
    ),
    # Stars
    ToolDef(
        name="github_list_starred_repos",
        description="List repositories starred by the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "description": "Results per page"},
                "sort": {"type": "string", "enum": ["created", "updated"]},
            },
            "required": [],
        },
        handler=list_starred_repos,
    ),
    ToolDef(
        name="github_star_repository",
        description="Star a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=star_repository,
    ),
    ToolDef(
        name="github_unstar_repository",
        description="Unstar a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=unstar_repository,
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize GitHub module."""
    if is_configured():
        print("[github] GitHub API configured")
    else:
        print("[github] Not configured - GITHUB_TOKEN not set, tools will be disabled")
        global TOOLS
        TOOLS = []


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
