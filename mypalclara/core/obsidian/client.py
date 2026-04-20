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
