"""Tests for the MCP server's host allowlist (app/mcp_server.py).

The MCP SDK rejects any request whose Host header is not allowlisted (DNS-rebinding
protection). The local defaults cover dev and tests; a real deploy (Render) adds its public
hostname through the AQUASENTINEL_ALLOWED_HOSTS environment variable. The protection stays ON
- it is extended, never disabled.
"""

from __future__ import annotations

import logging

from app.mcp_server import build_transport_security

LOCAL_HOSTS = ["testserver", "localhost", "localhost:8000", "127.0.0.1:8000"]
LOCAL_ORIGINS = ["http://testserver", "http://localhost:8000", "http://127.0.0.1:8000"]


def test_with_no_env_value_only_the_local_defaults_are_allowed():
    settings = build_transport_security(None)

    assert settings.allowed_hosts == LOCAL_HOSTS
    assert settings.allowed_origins == LOCAL_ORIGINS


def test_a_configured_host_is_added_with_its_https_origin_and_defaults_stay():
    settings = build_transport_security("aquasentinel.onrender.com")

    assert "aquasentinel.onrender.com" in settings.allowed_hosts
    assert "https://aquasentinel.onrender.com" in settings.allowed_origins
    assert set(LOCAL_HOSTS) <= set(settings.allowed_hosts)
    assert set(LOCAL_ORIGINS) <= set(settings.allowed_origins)


def test_several_comma_separated_hosts_with_spaces_are_all_added():
    settings = build_transport_security(" a.onrender.com , b.example.org ,")

    assert "a.onrender.com" in settings.allowed_hosts
    assert "b.example.org" in settings.allowed_hosts
    assert "https://b.example.org" in settings.allowed_origins


def test_a_blank_value_changes_nothing():
    assert build_transport_security("   ").allowed_hosts == LOCAL_HOSTS
    assert build_transport_security("").allowed_hosts == LOCAL_HOSTS


def test_a_host_that_was_not_configured_is_not_allowed():
    settings = build_transport_security("aquasentinel.onrender.com")

    assert "evil.example" not in settings.allowed_hosts
    assert "https://evil.example" not in settings.allowed_origins


def test_dns_rebinding_protection_stays_on():
    assert build_transport_security("aquasentinel.onrender.com").enable_dns_rebinding_protection


def test_a_malformed_entry_is_skipped_with_a_warning_not_a_crash(caplog):
    """A pasted full URL ("https://x.onrender.com") is the likely mistake. It must not be
    silently accepted as a weird host string, and must not take down the server at import."""
    with caplog.at_level(logging.WARNING):
        settings = build_transport_security("https://wrong.onrender.com/, good.onrender.com")

    assert "good.onrender.com" in settings.allowed_hosts
    assert not any("wrong.onrender.com" in host for host in settings.allowed_hosts)
    assert "wrong.onrender.com" in caplog.text
