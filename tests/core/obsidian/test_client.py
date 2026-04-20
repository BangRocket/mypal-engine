"""Tests for ObsidianClient — HTTP wrapper over obsidian-local-rest-api."""

import httpx
import pytest

from mypalclara.core.obsidian.client import ObsidianClient
from mypalclara.core.obsidian.exceptions import (
    ObsidianAuthError,
    ObsidianConnectionError,
    ObsidianNotFoundError,
    ObsidianRateLimitError,
    ObsidianServerError,
)

pytestmark = pytest.mark.asyncio


async def test_list_vault_returns_file_list(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/",
        json={"files": ["a.md", "b/"]},
    )
    client = ObsidianClient("h.example", "my-token")
    files = await client.list_vault()
    assert files == ["a.md", "b/"]


async def test_list_vault_sends_bearer_auth(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/",
        json={"files": []},
    )
    client = ObsidianClient("h.example", "my-token")
    await client.list_vault()

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer my-token"


async def test_client_accepts_full_url_host(httpx_mock):
    """Host with scheme should not be double-prefixed."""
    httpx_mock.add_response(
        url="http://localhost:27124/vault/",
        json={"files": []},
    )
    client = ObsidianClient("http://localhost:27124", "t")
    await client.list_vault()


async def test_get_file_returns_text_content(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/note.md",
        text="# Hello\n\nBody.",
    )
    client = ObsidianClient("h.example", "t")
    content = await client.get_file("note.md")
    assert content == "# Hello\n\nBody."


async def test_get_file_404_raises_not_found(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/missing.md",
        status_code=404,
    )
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianNotFoundError):
        await client.get_file("missing.md")


async def test_401_raises_auth_error(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/", status_code=401)
    client = ObsidianClient("h.example", "bad-token")
    with pytest.raises(ObsidianAuthError):
        await client.list_vault()


async def test_403_raises_auth_error(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/", status_code=403)
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianAuthError):
        await client.list_vault()


async def test_429_raises_rate_limit(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/", status_code=429)
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianRateLimitError):
        await client.list_vault()


async def test_500_raises_server_error(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/", status_code=500)
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianServerError):
        await client.list_vault()


async def test_put_file_sends_put_with_content(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/new.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "t")
    await client.put_file("new.md", "some content")

    request = httpx_mock.get_request()
    assert request.method == "PUT"
    assert request.content == b"some content"


async def test_put_file_sets_text_markdown_content_type(httpx_mock):
    """The Obsidian REST API expects a text Content-Type for markdown bodies."""
    httpx_mock.add_response(
        url="https://h.example/vault/new.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "t")
    await client.put_file("new.md", "content")

    request = httpx_mock.get_request()
    ct = request.headers.get("Content-Type", "")
    assert "text/markdown" in ct or "text/plain" in ct


async def test_connection_error_wraps_httpx_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("dns fail"))
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianConnectionError):
        await client.list_vault()


async def test_timeout_wraps_httpx_timeout(httpx_mock):
    httpx_mock.add_exception(httpx.TimeoutException("too slow"))
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianConnectionError):
        await client.list_vault()


async def test_verify_tls_defaults_true():
    """TLS verification should be on by default."""
    client = ObsidianClient("h.example", "t")
    assert client.verify_tls is True


async def test_verify_tls_can_be_disabled():
    client = ObsidianClient("h.example", "t", verify_tls=False)
    assert client.verify_tls is False
