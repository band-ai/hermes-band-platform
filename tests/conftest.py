"""Shared test fixtures for the Band platform plugin.

The band SDK is NOT assumed installed in the test environment. A minimal but
*faithful* stub is injected into ``sys.modules`` at collection time — BEFORE
``hermes_band_platform.adapter`` (or ``.tools``) is imported — so the adapter's
top-level ``try: from band ...`` binds the stub and ``BAND_AVAILABLE`` stays
True, and so ``tools.py`` constructs real request-type objects (not auto-attr
MagicMocks).

If the real ``band-sdk`` is installed, we leave it in place.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock


def _install_band_mock() -> MagicMock:
    """Register stub band sub-packages into sys.modules (idempotent).

    Returns the root ``band`` mock so callers can attach extra attrs.
    """
    if "band" in sys.modules:
        # Already present (real SDK or a prior install) — reuse it.
        return sys.modules["band"]

    # A class that, when instantiated, returns an AsyncMock-backed link object.
    class _FakeLinkClass:
        def __init__(self, agent_id, api_key, ws_url, rest_url):
            self._agent_id = agent_id
            self._api_key = api_key
            self._ws_url = ws_url
            self._rest_url = rest_url
            self.connect = AsyncMock()
            self.disconnect = AsyncMock()
            self.subscribe_agent_rooms = AsyncMock()
            self.subscribe_room = AsyncMock()
            self.unsubscribe_room = AsyncMock()
            self.rest = MagicMock()
            self._events = []

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    # SDK request types used by the adapter *and* the tools module. The stub
    # must be a faithful stand-in for every request type either module
    # constructs, with the real keyword signatures.
    class _FakeChatMessageRequest:
        def __init__(self, content, mentions):
            self.content = content
            self.mentions = mentions

    class _FakeChatMessageRequestMentionsItem:
        def __init__(self, id, handle=None, name=None):
            self.id = id
            self.handle = handle
            self.name = name

    class _FakeParticipantRequest:
        def __init__(self, participant_id, role=None):
            self.participant_id = participant_id
            self.role = role

    class _FakeChatRoomRequest:
        def __init__(self, task_id=None):
            self.task_id = task_id

    band_mod = MagicMock()
    band_platform_mod = MagicMock()
    band_platform_link_mod = MagicMock()
    band_platform_link_mod.BandLink = _FakeLinkClass
    band_platform_event_mod = MagicMock()
    band_client_mod = MagicMock()
    band_client_rest_mod = MagicMock()
    band_client_rest_mod.ChatMessageRequest = _FakeChatMessageRequest
    band_client_rest_mod.ChatMessageRequestMentionsItem = _FakeChatMessageRequestMentionsItem
    band_client_rest_mod.ParticipantRequest = _FakeParticipantRequest
    band_client_rest_mod.ChatRoomRequest = _FakeChatRoomRequest
    band_client_rest_mod.DEFAULT_REQUEST_OPTIONS = {"max_retries": 3}

    sys.modules["band"] = band_mod
    sys.modules["band.platform"] = band_platform_mod
    sys.modules["band.platform.link"] = band_platform_link_mod
    sys.modules["band.platform.event"] = band_platform_event_mod
    sys.modules["band.client"] = band_client_mod
    sys.modules["band.client.rest"] = band_client_rest_mod

    return band_mod


# Install the stub at collection time, before any test module imports the
# adapter / tools package.
_install_band_mock()
