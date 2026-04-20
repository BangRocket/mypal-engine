"""Async HTTP client for the obsidian-local-rest-api plugin.

Wraps bearer-auth, dialect-less JSON/text responses, and maps HTTP
errors into the typed exceptions defined in exceptions.py.
"""

from __future__ import annotations

from datetime import date as _date

import httpx

from mypalclara.core.obsidian.exceptions import (
    ObsidianAuthError,
    ObsidianConnectionError,
    ObsidianNotFoundError,
    ObsidianRateLimitError,
    ObsidianServerError,
)


class ObsidianClient:
    """Async client for the Obsidian Local REST API.

    Parameters
    ----------
    api_host:
        Host (e.g. "obsidian.shmp.app") or full base URL
        ("https://localhost:27124"). Plain hosts default to https://.
    api_token:
        Bearer token from the Obsidian plugin.
    verify_tls:
        Verify the server's TLS certificate. Keep True for hosted
        instances; disable only for localhost self-signed certs.
    timeout:
        Seconds for each request. Applies to connect + read.
    """

    def __init__(
        self,
        api_host: str,
        api_token: str,
        verify_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self.api_host = api_host
        self.api_token = api_token
        self.verify_tls = verify_tls
        self.timeout = timeout

    # ---- internals ----

    @property
    def _base_url(self) -> str:
        host = self.api_host.rstrip("/")
        if host.startswith(("http://", "https://")):
            return host
        return f"https://{host}"

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    def _raise_for_status(self, resp: httpx.Response) -> None:
        code = resp.status_code
        if code < 400:
            return
        if code in (401, 403):
            raise ObsidianAuthError(f"Obsidian auth failed: HTTP {code}")
        if code == 404:
            raise ObsidianNotFoundError(f"Not found: {resp.url}")
        if code == 429:
            raise ObsidianRateLimitError("Obsidian rate-limited")
        if code >= 500:
            raise ObsidianServerError(f"Obsidian server error: HTTP {code}")
        resp.raise_for_status()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        merged_headers = {**self._auth_headers, **(headers or {})}
        try:
            async with httpx.AsyncClient(verify=self.verify_tls, timeout=self.timeout) as http:
                resp = await http.request(method, url, headers=merged_headers, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
            raise ObsidianConnectionError(str(e)) from e
        self._raise_for_status(resp)
        return resp

    # ---- vault endpoints ----

    async def list_vault(self) -> list[str]:
        """List files and directories at the vault root."""
        resp = await self._request("GET", "/vault/")
        return resp.json().get("files", [])

    async def get_file(self, path: str) -> str:
        """Read the full text content of a note."""
        resp = await self._request("GET", f"/vault/{path}")
        return resp.text

    async def put_file(self, path: str, content: str) -> None:
        """Create or replace a note."""
        await self._request(
            "PUT",
            f"/vault/{path}",
            content=content.encode("utf-8"),
            headers={"Content-Type": "text/markdown; charset=utf-8"},
        )

    async def list_dir(self, path: str) -> list[str]:
        """List files and directories in a vault sub-directory."""
        normalized = path.strip("/") + "/"
        resp = await self._request("GET", f"/vault/{normalized}")
        return resp.json().get("files", [])

    async def append_file(self, path: str, content: str) -> None:
        """Append content to an existing note (or create it if missing)."""
        await self._request(
            "POST",
            f"/vault/{path}",
            content=content.encode("utf-8"),
            headers={"Content-Type": "text/markdown; charset=utf-8"},
        )

    async def patch_file(
        self,
        path: str,
        target_type: str,
        target: str,
        content: str,
        operation: str = "append",
    ) -> None:
        """Insert content relative to a heading, block reference, or frontmatter field.

        Parameters
        ----------
        target_type:
            One of "heading", "block", "frontmatter".
        target:
            The heading path ("H1::H2"), block ID, or frontmatter field name.
        operation:
            One of "append", "prepend", "replace".
        """
        await self._request(
            "PATCH",
            f"/vault/{path}",
            content=content.encode("utf-8"),
            headers={
                "Content-Type": "text/markdown; charset=utf-8",
                "Target-Type": target_type,
                "Target": target,
                "Operation": operation,
            },
        )

    async def delete_file(self, path: str) -> None:
        """Delete a note from the vault."""
        await self._request("DELETE", f"/vault/{path}")

    # ---- active file ----

    async def get_active(self) -> str:
        """Return the text content of the currently-open note in Obsidian."""
        resp = await self._request("GET", "/active/")
        return resp.text

    async def put_active(self, content: str) -> None:
        """Replace the content of the currently-open note."""
        await self._request(
            "PUT",
            "/active/",
            content=content.encode("utf-8"),
            headers={"Content-Type": "text/markdown; charset=utf-8"},
        )

    # ---- periodic notes ----

    @staticmethod
    def _periodic_path(period: str, d: _date | None) -> str:
        if d is None:
            return f"/periodic/{period}/"
        return f"/periodic/{period}/{d.year}/{d.month:02d}/{d.day:02d}/"

    async def get_periodic(self, period: str, date: _date | None = None) -> str:
        """Read today's (or a specific date's) daily/weekly/etc. note."""
        resp = await self._request("GET", self._periodic_path(period, date))
        return resp.text

    async def append_periodic(
        self, period: str, content: str, date: _date | None = None
    ) -> None:
        """Append content to today's (or a specific date's) periodic note."""
        await self._request(
            "POST",
            self._periodic_path(period, date),
            content=content.encode("utf-8"),
            headers={"Content-Type": "text/markdown; charset=utf-8"},
        )

    # ---- search ----

    async def search_simple(
        self, query: str, context_length: int | None = None
    ) -> list[dict]:
        """Full-text search across the vault.

        Returns a list of hit dicts; each hit has at least `filename` and `matches`.
        """
        body: dict[str, object] = {"query": query}
        if context_length is not None:
            body["contextLength"] = context_length
        resp = await self._request("POST", "/search/simple/", json=body)
        return resp.json()

    async def search_dql(self, query: str) -> list[dict]:
        """Run a Dataview DQL query over the vault.

        Only works if the Dataview plugin is installed and enabled.
        """
        resp = await self._request(
            "POST",
            "/search/",
            content=query.encode("utf-8"),
            headers={"Content-Type": "application/vnd.olrapi.dataview.dql+txt"},
        )
        return resp.json()

    async def search_jsonlogic(self, query: dict) -> list[dict]:
        """Run a JsonLogic query over the vault."""
        import json as _json  # local alias; don't clutter top-level imports

        resp = await self._request(
            "POST",
            "/search/",
            content=_json.dumps(query).encode("utf-8"),
            headers={"Content-Type": "application/vnd.olrapi.jsonlogic+json"},
        )
        return resp.json()

    # ---- tags, commands, open ----

    async def list_tags(self) -> list[tuple[str, int]]:
        """List all tags in the vault with usage counts, sorted by count desc.

        Returns a list of (tag_name, count) tuples. The leading '#' is stripped.
        """
        resp = await self._request("GET", "/tags/")
        data = resp.json()
        items = data if isinstance(data, list) else data.get("tags", [])
        result: list[tuple[str, int]] = []
        for item in items:
            name = item.get("tag") or item.get("name") or ""
            if name.startswith("#"):
                name = name[1:]
            count = int(item.get("count", 0))
            if name:
                result.append((name, count))
        result.sort(key=lambda t: t[1], reverse=True)
        return result

    async def list_commands(self) -> list[dict]:
        """List available Obsidian commands (id + name)."""
        resp = await self._request("GET", "/commands/")
        return resp.json().get("commands", [])

    async def execute_command(self, command_id: str) -> None:
        """Execute an Obsidian command by its ID."""
        await self._request("POST", f"/commands/{command_id}/")

    async def open_file(self, path: str) -> None:
        """Surface a note in the Obsidian UI."""
        await self._request("POST", f"/open/{path}")
