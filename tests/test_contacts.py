"""Tests for the Band contact-management tools (``hermes_band_platform/contacts.py``).

These wrap band-sdk's own ``ContactTools`` helper (``band.runtime.contact_tools``),
so tests patch ``contacts.ContactTools`` directly (mirroring how
``tests/test_tools.py`` patches ``tools._rest``) rather than needing a
``sys.modules`` stub for ``band.runtime.contact_tools`` -- that submodule isn't
registered by ``tests/conftest.py``, so ``contacts.py``'s own lazy import of it
fails closed to ``None`` in this test environment, exactly as it would with an
older band-sdk missing this module.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes_band_platform import contacts as band_contacts


def _make_contact_tools(**overrides) -> MagicMock:
    """Fake ContactTools instance with sensible AsyncMock defaults."""
    fake = MagicMock()
    fake.list_contacts = AsyncMock(return_value={"contacts": [], "metadata": {}})
    fake.add_contact = AsyncMock(return_value={"id": "req-1", "status": "pending"})
    fake.remove_contact = AsyncMock(return_value={"status": "removed"})
    fake.list_contact_requests = AsyncMock(
        return_value={"received": [], "sent": [], "metadata": {}}
    )
    fake.respond_contact_request = AsyncMock(
        return_value={"id": "req-1", "status": "approved"}
    )
    for key, value in overrides.items():
        setattr(fake, key, value)
    return fake


def _patch_contact_tools(fake):
    return patch.object(band_contacts, "ContactTools", MagicMock(return_value=fake))


def _patch_rest():
    return patch.object(band_contacts, "_rest", AsyncMock(return_value=MagicMock()))


def _parse(result: str) -> dict:
    assert isinstance(result, str)
    return json.loads(result)


class TestAddContact:

    @pytest.mark.asyncio
    async def test_sends_request(self):
        fake = _make_contact_tools(
            add_contact=AsyncMock(return_value={"id": "req-9", "status": "pending"})
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(
                await band_contacts._handle_add_contact(
                    {"handle": "@alice/hermes", "message": "hi"}
                )
            )
        assert out["success"] is True
        assert out["id"] == "req-9"
        assert out["status"] == "pending"
        fake.add_contact.assert_awaited_once_with(handle="@alice/hermes", message="hi")

    @pytest.mark.asyncio
    async def test_requires_handle(self):
        out = _parse(await band_contacts._handle_add_contact({}))
        assert "error" in out


class TestListContacts:

    @pytest.mark.asyncio
    async def test_lists(self):
        fake = _make_contact_tools(
            list_contacts=AsyncMock(
                return_value={
                    "contacts": [
                        {"id": "c1", "handle": "bob/hermes", "name": "Bob", "type": "Agent"}
                    ],
                    "metadata": {},
                }
            )
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(await band_contacts._handle_list_contacts({}))
        assert out["success"] is True
        assert out["contacts"] == [
            {"id": "c1", "handle": "bob/hermes", "name": "Bob", "type": "Agent"}
        ]


class TestListContactRequests:

    @pytest.mark.asyncio
    async def test_lists_received_and_sent(self):
        fake = _make_contact_tools(
            list_contact_requests=AsyncMock(
                return_value={
                    "received": [{"id": "r1", "from_handle": "carol"}],
                    "sent": [],
                    "metadata": {},
                }
            )
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(await band_contacts._handle_list_contact_requests({}))
        assert out["success"] is True
        assert out["received"] == [{"id": "r1", "from_handle": "carol"}]
        assert out["sent"] == []


class TestRespondContactRequest:

    @pytest.mark.asyncio
    async def test_approves(self):
        fake = _make_contact_tools(
            respond_contact_request=AsyncMock(
                return_value={"id": "req-1", "status": "approved"}
            )
        )
        with _patch_rest(), _patch_contact_tools(fake):
            out = _parse(
                await band_contacts._handle_respond_contact_request(
                    {"action": "approve", "request_id": "req-1"}
                )
            )
        assert out["success"] is True
        assert out["status"] == "approved"
        fake.respond_contact_request.assert_awaited_once_with(
            action="approve", request_id="req-1"
        )

    @pytest.mark.asyncio
    async def test_rejects_invalid_action(self):
        out = _parse(
            await band_contacts._handle_respond_contact_request(
                {"action": "smash", "request_id": "req-1"}
            )
        )
        assert "error" in out

    @pytest.mark.asyncio
    async def test_requires_request_id(self):
        out = _parse(
            await band_contacts._handle_respond_contact_request({"action": "approve"})
        )
        assert "error" in out


class TestContactToolsUnavailable:

    @pytest.mark.asyncio
    async def test_add_contact_reports_unavailable_when_sdk_missing(self, monkeypatch):
        monkeypatch.setattr(band_contacts, "ContactTools", None)
        monkeypatch.setattr(
            band_contacts, "_load_contact_tools", lambda: False
        )
        out = _parse(await band_contacts._handle_add_contact({"handle": "@x/y"}))
        assert "error" in out
