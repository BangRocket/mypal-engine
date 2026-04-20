"""Typed exception hierarchy for the Obsidian client."""

import pytest

from mypalclara.core.obsidian.exceptions import (
    ObsidianAuthError,
    ObsidianConnectionError,
    ObsidianError,
    ObsidianNotFoundError,
    ObsidianRateLimitError,
    ObsidianServerError,
)


def test_all_subclasses_inherit_from_obsidian_error():
    for cls in (
        ObsidianAuthError,
        ObsidianNotFoundError,
        ObsidianRateLimitError,
        ObsidianConnectionError,
        ObsidianServerError,
    ):
        assert issubclass(cls, ObsidianError)


def test_obsidian_error_inherits_from_exception():
    assert issubclass(ObsidianError, Exception)


def test_exceptions_carry_messages():
    err = ObsidianAuthError("401 Unauthorized")
    assert str(err) == "401 Unauthorized"
