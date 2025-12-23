"""Azure DevOps API tools.

Provides comprehensive Azure DevOps integration via the REST API.
Covers projects, repos, pipelines, work items, wiki, test plans, and more.

Requires:
- AZURE_DEVOPS_ORG: Organization name or full URL
- AZURE_DEVOPS_PAT: Personal Access Token
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from ._base import ToolContext, ToolDef

MODULE_NAME = "azure_devops"
MODULE_VERSION = "1.0.0"

# Configuration
AZURE_DEVOPS_ORG = os.getenv("AZURE_DEVOPS_ORG", "")
AZURE_DEVOPS_PAT = os.getenv("AZURE_DEVOPS_PAT", "")
API_VERSION = "7.1"


def is_configured() -> bool:
    """Check if Azure DevOps is configured."""
    return bool(AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT)


def _get_base_url() -> str:
    """Get the Azure DevOps base URL."""
    org = AZURE_DEVOPS_ORG
    if org.startswith("http"):
        return org.rstrip("/")
    return f"https://dev.azure.com/{org}"


def _get_headers() -> dict[str, str]:
    """Get headers for Azure DevOps API requests."""
    auth = base64.b64encode(f":{AZURE_DEVOPS_PAT}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


async def _ado_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
    json_data: dict | list | None = None,
    api_version: str | None = None,
) -> dict | list | str:
    """Make an Azure DevOps API request."""
    if not is_configured():
        raise ValueError("Azure DevOps not configured (AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT required)")

    base_url = _get_base_url()
    url = f"{base_url}/{endpoint.lstrip('/')}"

    # Add API version
    if params is None:
        params = {}
    params["api-version"] = api_version or API_VERSION

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
            raise ValueError(f"Azure DevOps API error ({response.status_code}): {error_msg}")

        return response.json()


# =============================================================================
# Core Tools
# =============================================================================


async def list_projects(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all projects in the organization."""
    params = {}
    if args.get("top"):
        params["$top"] = args["top"]
    if args.get("skip"):
        params["$skip"] = args["skip"]
    if args.get("stateFilter"):
        params["stateFilter"] = args["stateFilter"]

    try:
        result = await _ado_request("GET", "_apis/projects", params=params)
        projects = [
            {
                "id": p["id"],
                "name": p["name"],
                "state": p.get("state"),
                "visibility": p.get("visibility"),
            }
            for p in result.get("value", [])
        ]
        return json.dumps({"count": result.get("count", len(projects)), "projects": projects}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_project_teams(args: dict[str, Any], ctx: ToolContext) -> str:
    """List teams within a project."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    params = {}
    if args.get("top"):
        params["$top"] = args["top"]
    if args.get("mine"):
        params["$mine"] = args["mine"]

    try:
        result = await _ado_request("GET", f"_apis/projects/{project}/teams", params=params)
        teams = [{"id": t["id"], "name": t["name"]} for t in result.get("value", [])]
        return json.dumps(teams, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Repository Tools
# =============================================================================


async def list_repos(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all repositories in a project."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories")
        repos = [
            {
                "id": r["id"],
                "name": r["name"],
                "defaultBranch": r.get("defaultBranch", "").replace("refs/heads/", ""),
                "size": r.get("size"),
                "webUrl": r.get("webUrl"),
            }
            for r in result.get("value", [])
        ]
        return json.dumps(repos, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_repo(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get repository details by name or ID."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    if not project or not repo:
        return "Error: project and repository are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_branches(args: dict[str, Any], ctx: ToolContext) -> str:
    """List branches in a repository."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    if not project or not repo:
        return "Error: project and repository are required"

    params = {}
    if args.get("filterContains"):
        params["filterContains"] = args["filterContains"]

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/refs", params={"filter": "heads"})
        branches = [
            {
                "name": b["name"].replace("refs/heads/", ""),
                "objectId": b["objectId"][:7],
            }
            for b in result.get("value", [])
        ]
        return json.dumps(branches, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_branch(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new branch."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    branch_name = args.get("branchName", "")
    source_branch = args.get("sourceBranch", "main")

    if not all([project, repo, branch_name]):
        return "Error: project, repository, and branchName are required"

    try:
        # Get source branch ref
        refs = await _ado_request(
            "GET",
            f"{project}/_apis/git/repositories/{repo}/refs",
            params={"filter": f"heads/{source_branch}"},
        )
        if not refs.get("value"):
            return f"Error: Source branch '{source_branch}' not found"

        source_sha = refs["value"][0]["objectId"]

        # Create new branch
        result = await _ado_request(
            "POST",
            f"{project}/_apis/git/repositories/{repo}/refs",
            json_data=[
                {
                    "name": f"refs/heads/{branch_name}",
                    "oldObjectId": "0000000000000000000000000000000000000000",
                    "newObjectId": source_sha,
                }
            ],
        )
        return json.dumps({"created": True, "branch": branch_name}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_commits(args: dict[str, Any], ctx: ToolContext) -> str:
    """List commits in a repository."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    if not project or not repo:
        return "Error: project and repository are required"

    params = {}
    if args.get("top"):
        params["$top"] = args["top"]
    if args.get("branch"):
        params["searchCriteria.itemVersion.version"] = args["branch"]
    if args.get("author"):
        params["searchCriteria.author"] = args["author"]

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/commits", params=params)
        commits = [
            {
                "commitId": c["commitId"][:7],
                "comment": c["comment"].split("\n")[0],
                "author": c["author"]["name"],
                "date": c["author"]["date"],
            }
            for c in result.get("value", [])
        ]
        return json.dumps(commits, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_file_contents(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get file contents from a repository."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    path = args.get("path", "")
    if not all([project, repo]):
        return "Error: project and repository are required"

    params = {"path": path or "/"}
    if args.get("branch"):
        params["versionDescriptor.version"] = args["branch"]
        params["versionDescriptor.versionType"] = "branch"

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/items", params=params)

        # If it's a folder, list contents
        if result.get("isFolder"):
            params["recursionLevel"] = "OneLevel"
            items = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/items", params=params)
            return json.dumps({"type": "directory", "items": items.get("value", [])}, indent=2)
        else:
            # Get file content
            params["includeContent"] = "true"
            file_result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/items", params=params)
            return json.dumps({"type": "file", "path": path, "content": file_result.get("content", "")}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Pull Request Tools
# =============================================================================


async def list_pull_requests(args: dict[str, Any], ctx: ToolContext) -> str:
    """List pull requests."""
    project = args.get("project", "")
    repo = args.get("repository")

    if not project:
        return "Error: project is required"

    endpoint = f"{project}/_apis/git/pullrequests" if not repo else f"{project}/_apis/git/repositories/{repo}/pullrequests"

    params = {}
    if args.get("status"):
        params["searchCriteria.status"] = args["status"]
    if args.get("creatorId"):
        params["searchCriteria.creatorId"] = args["creatorId"]
    if args.get("top"):
        params["$top"] = args["top"]

    try:
        result = await _ado_request("GET", endpoint, params=params)
        prs = [
            {
                "pullRequestId": pr["pullRequestId"],
                "title": pr["title"],
                "status": pr["status"],
                "createdBy": pr["createdBy"]["displayName"],
                "sourceRefName": pr["sourceRefName"].replace("refs/heads/", ""),
                "targetRefName": pr["targetRefName"].replace("refs/heads/", ""),
                "isDraft": pr.get("isDraft", False),
            }
            for pr in result.get("value", [])
        ]
        return json.dumps(prs, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific pull request."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    pr_id = args.get("pullRequestId")
    if not all([project, repo, pr_id]):
        return "Error: project, repository, and pullRequestId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/pullrequests/{pr_id}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new pull request."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    source = args.get("sourceRefName", "")
    target = args.get("targetRefName", "main")
    title = args.get("title", "")

    if not all([project, repo, source, title]):
        return "Error: project, repository, sourceRefName, and title are required"

    data = {
        "sourceRefName": f"refs/heads/{source}" if not source.startswith("refs/") else source,
        "targetRefName": f"refs/heads/{target}" if not target.startswith("refs/") else target,
        "title": title,
    }
    if args.get("description"):
        data["description"] = args["description"]
    if args.get("isDraft"):
        data["isDraft"] = args["isDraft"]

    try:
        result = await _ado_request("POST", f"{project}/_apis/git/repositories/{repo}/pullrequests", json_data=data)
        return json.dumps(
            {
                "created": True,
                "pullRequestId": result["pullRequestId"],
                "url": result.get("url"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update a pull request."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    pr_id = args.get("pullRequestId")
    if not all([project, repo, pr_id]):
        return "Error: project, repository, and pullRequestId are required"

    data = {}
    if args.get("title"):
        data["title"] = args["title"]
    if args.get("description"):
        data["description"] = args["description"]
    if args.get("status"):
        data["status"] = args["status"]
    if args.get("isDraft") is not None:
        data["isDraft"] = args["isDraft"]

    if not data:
        return "Error: at least one field to update is required"

    try:
        result = await _ado_request("PATCH", f"{project}/_apis/git/repositories/{repo}/pullrequests/{pr_id}", json_data=data)
        return json.dumps({"updated": True, "pullRequestId": result["pullRequestId"]}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_pr_threads(args: dict[str, Any], ctx: ToolContext) -> str:
    """List comment threads on a pull request."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    pr_id = args.get("pullRequestId")
    if not all([project, repo, pr_id]):
        return "Error: project, repository, and pullRequestId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/git/repositories/{repo}/pullrequests/{pr_id}/threads")
        threads = [
            {
                "id": t["id"],
                "status": t.get("status"),
                "comments": len(t.get("comments", [])),
                "isDeleted": t.get("isDeleted", False),
            }
            for t in result.get("value", [])
        ]
        return json.dumps(threads, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_pr_comment(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a comment on a pull request."""
    project = args.get("project", "")
    repo = args.get("repository", "")
    pr_id = args.get("pullRequestId")
    content = args.get("content", "")
    if not all([project, repo, pr_id, content]):
        return "Error: project, repository, pullRequestId, and content are required"

    data = {
        "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
        "status": args.get("status", "active"),
    }

    try:
        result = await _ado_request("POST", f"{project}/_apis/git/repositories/{repo}/pullrequests/{pr_id}/threads", json_data=data)
        return json.dumps({"created": True, "threadId": result["id"]}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Pipeline Tools
# =============================================================================


async def list_pipelines(args: dict[str, Any], ctx: ToolContext) -> str:
    """List pipelines/build definitions in a project."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    params = {}
    if args.get("name"):
        params["name"] = args["name"]
    if args.get("top"):
        params["$top"] = args["top"]

    try:
        result = await _ado_request("GET", f"{project}/_apis/build/definitions", params=params)
        pipelines = [
            {
                "id": p["id"],
                "name": p["name"],
                "path": p.get("path", "\\"),
                "queueStatus": p.get("queueStatus"),
            }
            for p in result.get("value", [])
        ]
        return json.dumps(pipelines, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_builds(args: dict[str, Any], ctx: ToolContext) -> str:
    """List builds in a project."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    params = {}
    if args.get("definitions"):
        params["definitions"] = args["definitions"]
    if args.get("statusFilter"):
        params["statusFilter"] = args["statusFilter"]
    if args.get("resultFilter"):
        params["resultFilter"] = args["resultFilter"]
    if args.get("top"):
        params["$top"] = args["top"]
    if args.get("branchName"):
        params["branchName"] = args["branchName"]

    try:
        result = await _ado_request("GET", f"{project}/_apis/build/builds", params=params)
        builds = [
            {
                "id": b["id"],
                "buildNumber": b["buildNumber"],
                "status": b["status"],
                "result": b.get("result"),
                "sourceBranch": b.get("sourceBranch", "").replace("refs/heads/", ""),
                "definition": b["definition"]["name"],
                "queueTime": b.get("queueTime"),
            }
            for b in result.get("value", [])
        ]
        return json.dumps(builds, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_build(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific build."""
    project = args.get("project", "")
    build_id = args.get("buildId")
    if not project or not build_id:
        return "Error: project and buildId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/build/builds/{build_id}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_build_logs(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get logs for a build."""
    project = args.get("project", "")
    build_id = args.get("buildId")
    if not project or not build_id:
        return "Error: project and buildId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/build/builds/{build_id}/logs")
        logs = [{"id": l["id"], "type": l["type"], "lineCount": l.get("lineCount")} for l in result.get("value", [])]
        return json.dumps(logs, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def run_pipeline(args: dict[str, Any], ctx: ToolContext) -> str:
    """Trigger a pipeline run."""
    project = args.get("project", "")
    pipeline_id = args.get("pipelineId")
    if not project or not pipeline_id:
        return "Error: project and pipelineId are required"

    data = {}
    if args.get("branch"):
        data["resources"] = {"repositories": {"self": {"refName": f"refs/heads/{args['branch']}"}}}
    if args.get("variables"):
        data["variables"] = args["variables"]
    if args.get("templateParameters"):
        data["templateParameters"] = args["templateParameters"]

    try:
        result = await _ado_request("POST", f"{project}/_apis/pipelines/{pipeline_id}/runs", json_data=data)
        return json.dumps(
            {
                "triggered": True,
                "runId": result["id"],
                "state": result["state"],
                "url": result.get("_links", {}).get("web", {}).get("href"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_pipeline_run(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a pipeline run."""
    project = args.get("project", "")
    pipeline_id = args.get("pipelineId")
    run_id = args.get("runId")
    if not all([project, pipeline_id, run_id]):
        return "Error: project, pipelineId, and runId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/pipelines/{pipeline_id}/runs/{run_id}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_pipeline_runs(args: dict[str, Any], ctx: ToolContext) -> str:
    """List runs for a pipeline."""
    project = args.get("project", "")
    pipeline_id = args.get("pipelineId")
    if not project or not pipeline_id:
        return "Error: project and pipelineId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/pipelines/{pipeline_id}/runs")
        runs = [
            {
                "id": r["id"],
                "name": r.get("name"),
                "state": r["state"],
                "result": r.get("result"),
                "createdDate": r.get("createdDate"),
            }
            for r in result.get("value", [])
        ]
        return json.dumps(runs, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Work Item Tools
# =============================================================================


async def get_work_item(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get a work item by ID."""
    project = args.get("project", "")
    work_item_id = args.get("id")
    if not project or not work_item_id:
        return "Error: project and id are required"

    params = {}
    if args.get("fields"):
        params["fields"] = args["fields"]
    if args.get("expand"):
        params["$expand"] = args["expand"]

    try:
        result = await _ado_request("GET", f"{project}/_apis/wit/workitems/{work_item_id}", params=params)
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_work_items(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get multiple work items by IDs."""
    project = args.get("project", "")
    ids = args.get("ids", [])
    if not project or not ids:
        return "Error: project and ids are required"

    ids_str = ",".join(str(i) for i in ids)

    try:
        result = await _ado_request("GET", f"{project}/_apis/wit/workitems", params={"ids": ids_str})
        items = [
            {
                "id": w["id"],
                "type": w["fields"].get("System.WorkItemType"),
                "title": w["fields"].get("System.Title"),
                "state": w["fields"].get("System.State"),
                "assignedTo": w["fields"].get("System.AssignedTo", {}).get("displayName"),
            }
            for w in result.get("value", [])
        ]
        return json.dumps(items, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_work_item(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new work item."""
    project = args.get("project", "")
    work_item_type = args.get("workItemType", "")
    title = args.get("title", "")
    if not all([project, work_item_type, title]):
        return "Error: project, workItemType, and title are required"

    # Build patch document
    operations = [{"op": "add", "path": "/fields/System.Title", "value": title}]

    if args.get("description"):
        operations.append({"op": "add", "path": "/fields/System.Description", "value": args["description"]})
    if args.get("assignedTo"):
        operations.append({"op": "add", "path": "/fields/System.AssignedTo", "value": args["assignedTo"]})
    if args.get("areaPath"):
        operations.append({"op": "add", "path": "/fields/System.AreaPath", "value": args["areaPath"]})
    if args.get("iterationPath"):
        operations.append({"op": "add", "path": "/fields/System.IterationPath", "value": args["iterationPath"]})
    if args.get("priority"):
        operations.append({"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": args["priority"]})
    if args.get("tags"):
        operations.append({"op": "add", "path": "/fields/System.Tags", "value": args["tags"]})

    try:
        # Note: Work item creation uses JSON Patch format
        url = f"{_get_base_url()}/{project}/_apis/wit/workitems/${quote(work_item_type)}"
        params = {"api-version": API_VERSION}
        headers = _get_headers()
        headers["Content-Type"] = "application/json-patch+json"

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, params=params, json=operations, timeout=30.0)
            response.raise_for_status()
            result = response.json()

        return json.dumps(
            {
                "created": True,
                "id": result["id"],
                "url": result.get("_links", {}).get("html", {}).get("href"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_work_item(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update a work item."""
    work_item_id = args.get("id")
    if not work_item_id:
        return "Error: id is required"

    operations = []
    field_mapping = {
        "title": "/fields/System.Title",
        "description": "/fields/System.Description",
        "state": "/fields/System.State",
        "assignedTo": "/fields/System.AssignedTo",
        "areaPath": "/fields/System.AreaPath",
        "iterationPath": "/fields/System.IterationPath",
        "priority": "/fields/Microsoft.VSTS.Common.Priority",
        "tags": "/fields/System.Tags",
    }

    for key, path in field_mapping.items():
        if args.get(key):
            operations.append({"op": "replace", "path": path, "value": args[key]})

    if not operations:
        return "Error: at least one field to update is required"

    try:
        url = f"{_get_base_url()}/_apis/wit/workitems/{work_item_id}"
        params = {"api-version": API_VERSION}
        headers = _get_headers()
        headers["Content-Type"] = "application/json-patch+json"

        async with httpx.AsyncClient() as client:
            response = await client.patch(url, headers=headers, params=params, json=operations, timeout=30.0)
            response.raise_for_status()
            result = response.json()

        return json.dumps({"updated": True, "id": result["id"]}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def add_work_item_comment(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add a comment to a work item."""
    project = args.get("project", "")
    work_item_id = args.get("workItemId")
    comment = args.get("comment", "")
    if not all([project, work_item_id, comment]):
        return "Error: project, workItemId, and comment are required"

    try:
        result = await _ado_request(
            "POST",
            f"{project}/_apis/wit/workitems/{work_item_id}/comments",
            json_data={"text": comment},
        )
        return json.dumps({"created": True, "id": result["id"]}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def search_work_items(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for work items."""
    search_text = args.get("searchText", "")
    if not search_text:
        return "Error: searchText is required"

    data = {
        "searchText": search_text,
        "$top": args.get("top", 25),
    }
    if args.get("project"):
        data["filters"] = {"Project": [args["project"]]}

    try:
        # Search API uses a different base URL
        url = f"https://almsearch.dev.azure.com/{AZURE_DEVOPS_ORG}/_apis/search/workitemsearchresults"
        headers = _get_headers()
        params = {"api-version": "7.0"}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, params=params, json=data, timeout=30.0)
            response.raise_for_status()
            result = response.json()

        items = [
            {
                "id": r["fields"]["system.id"],
                "type": r["fields"].get("system.workitemtype"),
                "title": r["fields"].get("system.title"),
                "state": r["fields"].get("system.state"),
                "project": r.get("project", {}).get("name"),
            }
            for r in result.get("results", [])
        ]
        return json.dumps({"count": result.get("count", len(items)), "items": items}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def my_work_items(args: dict[str, Any], ctx: ToolContext) -> str:
    """List work items assigned to current user."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    # Use WIQL query
    wiql = {
        "query": f"""
        SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType]
        FROM WorkItems
        WHERE [System.AssignedTo] = @Me
        AND [System.TeamProject] = '{project}'
        ORDER BY [System.ChangedDate] DESC
        """
    }
    if not args.get("includeCompleted"):
        wiql["query"] = wiql["query"].replace("ORDER BY", "AND [System.State] <> 'Closed' AND [System.State] <> 'Done' ORDER BY")

    try:
        result = await _ado_request("POST", f"{project}/_apis/wit/wiql", json_data=wiql)
        work_item_ids = [str(wi["id"]) for wi in result.get("workItems", [])[:50]]

        if not work_item_ids:
            return json.dumps({"items": []}, indent=2)

        # Get work item details
        items_result = await _ado_request("GET", f"{project}/_apis/wit/workitems", params={"ids": ",".join(work_item_ids)})
        items = [
            {
                "id": w["id"],
                "type": w["fields"].get("System.WorkItemType"),
                "title": w["fields"].get("System.Title"),
                "state": w["fields"].get("System.State"),
            }
            for w in items_result.get("value", [])
        ]
        return json.dumps({"items": items}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Wiki Tools
# =============================================================================


async def list_wikis(args: dict[str, Any], ctx: ToolContext) -> str:
    """List wikis in the organization or project."""
    project = args.get("project")

    endpoint = f"{project}/_apis/wiki/wikis" if project else "_apis/wiki/wikis"

    try:
        result = await _ado_request("GET", endpoint)
        wikis = [
            {"id": w["id"], "name": w["name"], "type": w.get("type"), "projectId": w.get("projectId")}
            for w in result.get("value", [])
        ]
        return json.dumps(wikis, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_wiki_page(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get wiki page content."""
    project = args.get("project", "")
    wiki = args.get("wikiIdentifier", "")
    path = args.get("path", "/")
    if not project or not wiki:
        return "Error: project and wikiIdentifier are required"

    try:
        result = await _ado_request(
            "GET",
            f"{project}/_apis/wiki/wikis/{wiki}/pages",
            params={"path": path, "includeContent": "true"},
        )
        return json.dumps(
            {
                "path": result.get("path"),
                "content": result.get("content", ""),
                "gitItemPath": result.get("gitItemPath"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_or_update_wiki_page(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create or update a wiki page."""
    project = args.get("project", "")
    wiki = args.get("wikiIdentifier", "")
    path = args.get("path", "")
    content = args.get("content", "")
    if not all([project, wiki, path, content]):
        return "Error: project, wikiIdentifier, path, and content are required"

    try:
        url = f"{_get_base_url()}/{project}/_apis/wiki/wikis/{wiki}/pages"
        headers = _get_headers()
        headers["Content-Type"] = "application/json"
        params = {"path": path, "api-version": API_VERSION}

        async with httpx.AsyncClient() as client:
            response = await client.put(url, headers=headers, params=params, json={"content": content}, timeout=30.0)
            response.raise_for_status()
            result = response.json()

        return json.dumps({"success": True, "path": result.get("path")}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_wiki_pages(args: dict[str, Any], ctx: ToolContext) -> str:
    """List pages in a wiki."""
    project = args.get("project", "")
    wiki = args.get("wikiIdentifier", "")
    if not project or not wiki:
        return "Error: project and wikiIdentifier are required"

    try:
        result = await _ado_request(
            "GET",
            f"{project}/_apis/wiki/wikis/{wiki}/pages",
            params={"path": "/", "recursionLevel": "full"},
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Search Tools
# =============================================================================


async def search_code(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for code across repositories."""
    search_text = args.get("searchText", "")
    if not search_text:
        return "Error: searchText is required"

    data = {
        "searchText": search_text,
        "$top": args.get("top", 25),
    }
    filters = {}
    if args.get("project"):
        filters["Project"] = [args["project"]]
    if args.get("repository"):
        filters["Repository"] = [args["repository"]]
    if args.get("path"):
        filters["Path"] = [args["path"]]
    if filters:
        data["filters"] = filters

    try:
        url = f"https://almsearch.dev.azure.com/{AZURE_DEVOPS_ORG}/_apis/search/codesearchresults"
        headers = _get_headers()
        params = {"api-version": "7.0"}

        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, params=params, json=data, timeout=30.0)
            response.raise_for_status()
            result = response.json()

        items = [
            {
                "fileName": r.get("fileName"),
                "path": r.get("path"),
                "repository": r.get("repository", {}).get("name"),
                "project": r.get("project", {}).get("name"),
            }
            for r in result.get("results", [])
        ]
        return json.dumps({"count": result.get("count", len(items)), "items": items}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Iteration/Sprint Tools
# =============================================================================


async def list_iterations(args: dict[str, Any], ctx: ToolContext) -> str:
    """List iterations in a project."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/work/teamsettings/iterations")
        iterations = [
            {
                "id": i["id"],
                "name": i["name"],
                "path": i["path"],
                "startDate": i.get("attributes", {}).get("startDate"),
                "finishDate": i.get("attributes", {}).get("finishDate"),
            }
            for i in result.get("value", [])
        ]
        return json.dumps(iterations, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_team_iterations(args: dict[str, Any], ctx: ToolContext) -> str:
    """List iterations assigned to a team."""
    project = args.get("project", "")
    team = args.get("team", "")
    if not project or not team:
        return "Error: project and team are required"

    params = {}
    if args.get("timeframe"):
        params["$timeframe"] = args["timeframe"]

    try:
        result = await _ado_request("GET", f"{project}/{team}/_apis/work/teamsettings/iterations", params=params)
        iterations = [
            {
                "id": i["id"],
                "name": i["name"],
                "path": i["path"],
                "startDate": i.get("attributes", {}).get("startDate"),
                "finishDate": i.get("attributes", {}).get("finishDate"),
            }
            for i in result.get("value", [])
        ]
        return json.dumps(iterations, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Test Plans Tools
# =============================================================================


async def list_test_plans(args: dict[str, Any], ctx: ToolContext) -> str:
    """List test plans in a project."""
    project = args.get("project", "")
    if not project:
        return "Error: project is required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/testplan/plans")
        plans = [
            {
                "id": p["id"],
                "name": p["name"],
                "state": p.get("state"),
                "iteration": p.get("iteration"),
            }
            for p in result.get("value", [])
        ]
        return json.dumps(plans, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_test_suites(args: dict[str, Any], ctx: ToolContext) -> str:
    """List test suites in a test plan."""
    project = args.get("project", "")
    plan_id = args.get("planId")
    if not project or not plan_id:
        return "Error: project and planId are required"

    try:
        result = await _ado_request("GET", f"{project}/_apis/testplan/plans/{plan_id}/suites")
        suites = [{"id": s["id"], "name": s["name"], "suiteType": s.get("suiteType")} for s in result.get("value", [])]
        return json.dumps(suites, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    # Core
    ToolDef(
        name="ado_list_projects",
        description="List all projects in the Azure DevOps organization.",
        parameters={
            "type": "object",
            "properties": {
                "top": {"type": "integer", "description": "Max results to return"},
                "skip": {"type": "integer", "description": "Results to skip"},
                "stateFilter": {"type": "string", "enum": ["all", "wellFormed", "createPending", "deleting", "new"]},
            },
            "required": [],
        },
        handler=list_projects,
    ),
    ToolDef(
        name="ado_list_project_teams",
        description="List teams within an Azure DevOps project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "top": {"type": "integer", "description": "Max results"},
                "mine": {"type": "boolean", "description": "Only teams I'm a member of"},
            },
            "required": ["project"],
        },
        handler=list_project_teams,
    ),
    # Repositories
    ToolDef(
        name="ado_list_repos",
        description="List all repositories in a project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
            },
            "required": ["project"],
        },
        handler=list_repos,
    ),
    ToolDef(
        name="ado_get_repo",
        description="Get repository details by name or ID.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
            },
            "required": ["project", "repository"],
        },
        handler=get_repo,
    ),
    ToolDef(
        name="ado_list_branches",
        description="List branches in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "filterContains": {"type": "string", "description": "Filter branches containing this text"},
            },
            "required": ["project", "repository"],
        },
        handler=list_branches,
    ),
    ToolDef(
        name="ado_create_branch",
        description="Create a new branch in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "branchName": {"type": "string", "description": "Name for the new branch"},
                "sourceBranch": {"type": "string", "description": "Source branch to branch from (default: main)"},
            },
            "required": ["project", "repository", "branchName"],
        },
        handler=create_branch,
    ),
    ToolDef(
        name="ado_list_commits",
        description="List commits in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "branch": {"type": "string", "description": "Branch name"},
                "author": {"type": "string", "description": "Filter by author"},
                "top": {"type": "integer", "description": "Max results"},
            },
            "required": ["project", "repository"],
        },
        handler=list_commits,
    ),
    ToolDef(
        name="ado_get_file_contents",
        description="Get file or directory contents from a repository.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "path": {"type": "string", "description": "Path to file or directory"},
                "branch": {"type": "string", "description": "Branch name"},
            },
            "required": ["project", "repository"],
        },
        handler=get_file_contents,
    ),
    # Pull Requests
    ToolDef(
        name="ado_list_pull_requests",
        description="List pull requests in a project or repository.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID (optional)"},
                "status": {"type": "string", "enum": ["active", "abandoned", "completed", "all"]},
                "top": {"type": "integer", "description": "Max results"},
            },
            "required": ["project"],
        },
        handler=list_pull_requests,
    ),
    ToolDef(
        name="ado_get_pull_request",
        description="Get details of a specific pull request.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "pullRequestId": {"type": "integer", "description": "Pull request ID"},
            },
            "required": ["project", "repository", "pullRequestId"],
        },
        handler=get_pull_request,
    ),
    ToolDef(
        name="ado_create_pull_request",
        description="Create a new pull request.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "sourceRefName": {"type": "string", "description": "Source branch name"},
                "targetRefName": {"type": "string", "description": "Target branch name (default: main)"},
                "title": {"type": "string", "description": "Pull request title"},
                "description": {"type": "string", "description": "Pull request description"},
                "isDraft": {"type": "boolean", "description": "Create as draft"},
            },
            "required": ["project", "repository", "sourceRefName", "title"],
        },
        handler=create_pull_request,
    ),
    ToolDef(
        name="ado_update_pull_request",
        description="Update a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "pullRequestId": {"type": "integer", "description": "Pull request ID"},
                "title": {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New description"},
                "status": {"type": "string", "enum": ["active", "abandoned", "completed"]},
                "isDraft": {"type": "boolean", "description": "Draft status"},
            },
            "required": ["project", "repository", "pullRequestId"],
        },
        handler=update_pull_request,
    ),
    ToolDef(
        name="ado_list_pr_threads",
        description="List comment threads on a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "pullRequestId": {"type": "integer", "description": "Pull request ID"},
            },
            "required": ["project", "repository", "pullRequestId"],
        },
        handler=list_pr_threads,
    ),
    ToolDef(
        name="ado_create_pr_comment",
        description="Create a comment on a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "repository": {"type": "string", "description": "Repository name or ID"},
                "pullRequestId": {"type": "integer", "description": "Pull request ID"},
                "content": {"type": "string", "description": "Comment content"},
                "status": {"type": "string", "enum": ["active", "fixed", "wontFix", "closed", "byDesign", "pending"]},
            },
            "required": ["project", "repository", "pullRequestId", "content"],
        },
        handler=create_pr_comment,
    ),
    # Pipelines
    ToolDef(
        name="ado_list_pipelines",
        description="List pipelines/build definitions in a project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "name": {"type": "string", "description": "Filter by pipeline name"},
                "top": {"type": "integer", "description": "Max results"},
            },
            "required": ["project"],
        },
        handler=list_pipelines,
    ),
    ToolDef(
        name="ado_list_builds",
        description="List builds in a project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "definitions": {"type": "string", "description": "Comma-separated definition IDs"},
                "statusFilter": {"type": "string", "enum": ["all", "inProgress", "completed", "notStarted", "postponed"]},
                "resultFilter": {"type": "string", "enum": ["succeeded", "partiallySucceeded", "failed", "canceled"]},
                "branchName": {"type": "string", "description": "Filter by branch"},
                "top": {"type": "integer", "description": "Max results"},
            },
            "required": ["project"],
        },
        handler=list_builds,
    ),
    ToolDef(
        name="ado_get_build",
        description="Get details of a specific build.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "buildId": {"type": "integer", "description": "Build ID"},
            },
            "required": ["project", "buildId"],
        },
        handler=get_build,
    ),
    ToolDef(
        name="ado_get_build_logs",
        description="Get logs for a build.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "buildId": {"type": "integer", "description": "Build ID"},
            },
            "required": ["project", "buildId"],
        },
        handler=get_build_logs,
    ),
    ToolDef(
        name="ado_run_pipeline",
        description="Trigger a pipeline run.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "pipelineId": {"type": "integer", "description": "Pipeline ID"},
                "branch": {"type": "string", "description": "Branch to run on"},
                "variables": {"type": "object", "description": "Pipeline variables"},
                "templateParameters": {"type": "object", "description": "Template parameters"},
            },
            "required": ["project", "pipelineId"],
        },
        handler=run_pipeline,
    ),
    ToolDef(
        name="ado_get_pipeline_run",
        description="Get details of a pipeline run.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "pipelineId": {"type": "integer", "description": "Pipeline ID"},
                "runId": {"type": "integer", "description": "Run ID"},
            },
            "required": ["project", "pipelineId", "runId"],
        },
        handler=get_pipeline_run,
    ),
    ToolDef(
        name="ado_list_pipeline_runs",
        description="List runs for a pipeline.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "pipelineId": {"type": "integer", "description": "Pipeline ID"},
            },
            "required": ["project", "pipelineId"],
        },
        handler=list_pipeline_runs,
    ),
    # Work Items
    ToolDef(
        name="ado_get_work_item",
        description="Get a work item by ID.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "id": {"type": "integer", "description": "Work item ID"},
                "fields": {"type": "string", "description": "Comma-separated field names"},
                "expand": {"type": "string", "enum": ["None", "Relations", "Fields", "Links", "All"]},
            },
            "required": ["project", "id"],
        },
        handler=get_work_item,
    ),
    ToolDef(
        name="ado_list_work_items",
        description="Get multiple work items by IDs.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "ids": {"type": "array", "items": {"type": "integer"}, "description": "List of work item IDs"},
            },
            "required": ["project", "ids"],
        },
        handler=list_work_items,
    ),
    ToolDef(
        name="ado_create_work_item",
        description="Create a new work item.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "workItemType": {"type": "string", "description": "Work item type (Bug, Task, User Story, etc.)"},
                "title": {"type": "string", "description": "Work item title"},
                "description": {"type": "string", "description": "Work item description"},
                "assignedTo": {"type": "string", "description": "Assignee email or name"},
                "areaPath": {"type": "string", "description": "Area path"},
                "iterationPath": {"type": "string", "description": "Iteration path"},
                "priority": {"type": "integer", "description": "Priority (1-4)"},
                "tags": {"type": "string", "description": "Semicolon-separated tags"},
            },
            "required": ["project", "workItemType", "title"],
        },
        handler=create_work_item,
    ),
    ToolDef(
        name="ado_update_work_item",
        description="Update a work item.",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Work item ID"},
                "title": {"type": "string", "description": "New title"},
                "description": {"type": "string", "description": "New description"},
                "state": {"type": "string", "description": "New state"},
                "assignedTo": {"type": "string", "description": "New assignee"},
                "areaPath": {"type": "string", "description": "New area path"},
                "iterationPath": {"type": "string", "description": "New iteration path"},
                "priority": {"type": "integer", "description": "New priority"},
                "tags": {"type": "string", "description": "New tags"},
            },
            "required": ["id"],
        },
        handler=update_work_item,
    ),
    ToolDef(
        name="ado_add_work_item_comment",
        description="Add a comment to a work item.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "workItemId": {"type": "integer", "description": "Work item ID"},
                "comment": {"type": "string", "description": "Comment text"},
            },
            "required": ["project", "workItemId", "comment"],
        },
        handler=add_work_item_comment,
    ),
    ToolDef(
        name="ado_search_work_items",
        description="Search for work items.",
        parameters={
            "type": "object",
            "properties": {
                "searchText": {"type": "string", "description": "Search text"},
                "project": {"type": "string", "description": "Filter by project"},
                "top": {"type": "integer", "description": "Max results"},
            },
            "required": ["searchText"],
        },
        handler=search_work_items,
    ),
    ToolDef(
        name="ado_my_work_items",
        description="List work items assigned to current user.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "includeCompleted": {"type": "boolean", "description": "Include completed items"},
            },
            "required": ["project"],
        },
        handler=my_work_items,
    ),
    # Wiki
    ToolDef(
        name="ado_list_wikis",
        description="List wikis in the organization or project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID (optional)"},
            },
            "required": [],
        },
        handler=list_wikis,
    ),
    ToolDef(
        name="ado_get_wiki_page",
        description="Get wiki page content.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "wikiIdentifier": {"type": "string", "description": "Wiki name or ID"},
                "path": {"type": "string", "description": "Page path (e.g., /Home)"},
            },
            "required": ["project", "wikiIdentifier"],
        },
        handler=get_wiki_page,
    ),
    ToolDef(
        name="ado_create_or_update_wiki_page",
        description="Create or update a wiki page.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "wikiIdentifier": {"type": "string", "description": "Wiki name or ID"},
                "path": {"type": "string", "description": "Page path"},
                "content": {"type": "string", "description": "Page content (markdown)"},
            },
            "required": ["project", "wikiIdentifier", "path", "content"],
        },
        handler=create_or_update_wiki_page,
    ),
    ToolDef(
        name="ado_list_wiki_pages",
        description="List pages in a wiki.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "wikiIdentifier": {"type": "string", "description": "Wiki name or ID"},
            },
            "required": ["project", "wikiIdentifier"],
        },
        handler=list_wiki_pages,
    ),
    # Search
    ToolDef(
        name="ado_search_code",
        description="Search for code across repositories.",
        parameters={
            "type": "object",
            "properties": {
                "searchText": {"type": "string", "description": "Search text"},
                "project": {"type": "string", "description": "Filter by project"},
                "repository": {"type": "string", "description": "Filter by repository"},
                "path": {"type": "string", "description": "Filter by path"},
                "top": {"type": "integer", "description": "Max results"},
            },
            "required": ["searchText"],
        },
        handler=search_code,
    ),
    # Iterations
    ToolDef(
        name="ado_list_iterations",
        description="List iterations in a project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
            },
            "required": ["project"],
        },
        handler=list_iterations,
    ),
    ToolDef(
        name="ado_list_team_iterations",
        description="List iterations assigned to a team.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "team": {"type": "string", "description": "Team name or ID"},
                "timeframe": {"type": "string", "enum": ["past", "current", "future"]},
            },
            "required": ["project", "team"],
        },
        handler=list_team_iterations,
    ),
    # Test Plans
    ToolDef(
        name="ado_list_test_plans",
        description="List test plans in a project.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
            },
            "required": ["project"],
        },
        handler=list_test_plans,
    ),
    ToolDef(
        name="ado_list_test_suites",
        description="List test suites in a test plan.",
        parameters={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project name or ID"},
                "planId": {"type": "integer", "description": "Test plan ID"},
            },
            "required": ["project", "planId"],
        },
        handler=list_test_suites,
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize Azure DevOps module."""
    if is_configured():
        print(f"[azure_devops] Azure DevOps configured for org: {AZURE_DEVOPS_ORG}")
    else:
        print("[azure_devops] Not configured - AZURE_DEVOPS_ORG and/or AZURE_DEVOPS_PAT not set")
        global TOOLS
        TOOLS = []


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
