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


# ---- B3 tests ----

async def test_list_dir_appends_trailing_slash(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/Projects/",
        json={"files": ["a.md", "sub/"]},
    )
    client = ObsidianClient("h.example", "t")
    files = await client.list_dir("Projects")
    assert files == ["a.md", "sub/"]


async def test_list_dir_handles_already_trailing_slash(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/Projects/",
        json={"files": []},
    )
    client = ObsidianClient("h.example", "t")
    await client.list_dir("Projects/")


async def test_list_dir_strips_leading_slash(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/Projects/",
        json={"files": []},
    )
    client = ObsidianClient("h.example", "t")
    await client.list_dir("/Projects")


async def test_append_file_uses_post_with_content(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/journal.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "t")
    await client.append_file("journal.md", "\n- new line\n")

    request = httpx_mock.get_request()
    assert request.method == "POST"
    assert request.content == b"\n- new line\n"
    assert "text/markdown" in request.headers.get("Content-Type", "")


async def test_patch_file_sends_target_headers(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/note.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "t")
    await client.patch_file(
        "note.md",
        target_type="heading",
        target="## Daily Log",
        content="- more",
        operation="append",
    )

    request = httpx_mock.get_request()
    assert request.method == "PATCH"
    assert request.headers["Target-Type"] == "heading"
    assert request.headers["Target"] == "## Daily Log"
    assert request.headers["Operation"] == "append"
    assert request.content == b"- more"


async def test_patch_file_defaults_operation_to_append(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/note.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "t")
    await client.patch_file(
        "note.md", target_type="block", target="abc123", content="x"
    )

    request = httpx_mock.get_request()
    assert request.headers["Operation"] == "append"


async def test_patch_file_preserves_auth_header(httpx_mock):
    """Per-call headers must NOT clobber the Authorization header."""
    httpx_mock.add_response(
        url="https://h.example/vault/note.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "secret-token")
    await client.patch_file(
        "note.md", target_type="heading", target="H", content="c"
    )

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer secret-token"


async def test_delete_file_uses_delete_method(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/trash.md",
        status_code=204,
    )
    client = ObsidianClient("h.example", "t")
    await client.delete_file("trash.md")

    request = httpx_mock.get_request()
    assert request.method == "DELETE"


async def test_delete_file_404_raises(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/missing.md",
        status_code=404,
    )
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianNotFoundError):
        await client.delete_file("missing.md")
