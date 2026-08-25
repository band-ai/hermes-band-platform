"""Token-safe outbound mention rendering."""

from __future__ import annotations

from hermes_band_platform import adapter as band_adapter


def _render_like_band(content, mentions):
    rendered = content
    for mention in mentions:
        replacement = f"@[[{mention.id}]]"
        for field in ("handle", "name"):
            token = getattr(mention, field, None)
            if token and f"@{token}" in rendered:
                rendered = rendered.replace(f"@{token}", replacement)
                break
        else:
            rendered = f"{replacement} {rendered}"
    return rendered


def test_prefix_handle_is_withheld_instead_of_corrupting_longer_token():
    plan = band_adapter._resolve_mentions(
        [{"id": "owner", "handle": "ageofascension", "name": "Ed"}],
        ["@ageofascension"],
    )
    content = "@ageofascension/ted please take a look"

    aligned = band_adapter.align_mentions_to_content(content, plan.items)

    assert aligned[0].handle is None
    assert _render_like_band(content, aligned) == (
        "@[[owner]] @ageofascension/ted please take a look"
    )


def test_whole_handle_token_is_retained_for_in_place_rendering():
    plan = band_adapter._resolve_mentions(
        [{"id": "agent", "handle": "owner/agent", "name": "Agent"}],
        ["@owner/agent"],
    )
    content = "ask @owner/agent please"

    aligned = band_adapter.align_mentions_to_content(content, plan.items)

    assert aligned[0].handle == "owner/agent"
    assert _render_like_band(content, aligned) == "ask @[[agent]] please"


def test_reference_kind_survives_an_unsafe_field_rebuild():
    plan = band_adapter._resolve_mentions(
        [{"id": "self", "handle": "owner", "name": "Owner"}],
        ["@owner"],
        agent_id="self",
    )

    aligned = band_adapter.align_mentions_to_content("@owner/agent text", plan.items)

    assert aligned[0].handle is None
    assert aligned[0].kind == "reference"


def test_content_without_recipient_token_keeps_original_item_identity():
    plan = band_adapter._resolve_mentions(
        [{"id": "alice", "handle": "alice", "name": "Alice"}],
        ["@alice"],
    )

    aligned = band_adapter.align_mentions_to_content("hello there", plan.items)

    assert aligned is plan.items
