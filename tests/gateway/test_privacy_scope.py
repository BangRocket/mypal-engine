"""Tests for privacy scope determination in the gateway processor."""
from __future__ import annotations

import pytest

from mypalclara.gateway.processor import _determine_privacy_scope


class TestDeterminePrivacyScope:
    def test_dm_channel_returns_full(self):
        assert _determine_privacy_scope("dm") == "full"

    def test_server_channel_returns_public_only(self):
        assert _determine_privacy_scope("server") == "public_only"

    def test_group_channel_returns_public_only(self):
        assert _determine_privacy_scope("group") == "public_only"

    def test_unknown_defaults_to_public_only(self):
        assert _determine_privacy_scope("unknown") == "public_only"
