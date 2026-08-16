"""Focused outbound mention policy tests; no live Band credentials are used."""

from hermes_band_platform.adapter import _mention_items, strip_attached_handles


def test_auto_selection_includes_peer_agents_but_excludes_self():
    items = _mention_items(
        [
            {"id": "self", "type": "Agent", "handle": "bot"},
            {"id": "peer", "type": "Agent", "handle": "peer-bot"},
            {"id": "human", "type": "User", "handle": "alice"},
        ],
        agent_id="self",
    )
    assert [item.id for item in items] == ["peer", "human"]


def test_strip_only_attached_handle_and_preserve_similar_text():
    mentions = _mention_items(
        [{"id": "u", "type": "User", "handle": "twins-owner/dumpty"}],
        agent_id="self",
    )
    text = (
        "@twins-owner/dumpty please reply; email @twins-owner/dumpty@example.com; "
        "URL https://x/@twins-owner/dumpty and @other/person stay."
    )
    assert strip_attached_handles(text, mentions) == (
        "please reply; email @twins-owner/dumpty@example.com; "
        "URL https://x/@twins-owner/dumpty and @other/person stay."
    )
