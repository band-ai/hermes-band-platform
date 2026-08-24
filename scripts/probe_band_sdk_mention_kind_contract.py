"""Prove the declared band-sdk floor really puts mention ``kind`` on the wire.

Self-recipient demotion depends on an undeclared ``kind`` extra surviving the
SDK model and JSON serialization. An omitted kind must remain omitted because
Band defaults it to the normal delivery mention.
"""

from importlib.metadata import version

from band.client.rest import ChatMessageRequestMentionsItem
from band_rest.core.jsonable_encoder import jsonable_encoder


def main() -> None:
    assert version("band-sdk") == "1.3.0"

    reference = ChatMessageRequestMentionsItem(
        id="00000000-0000-0000-0000-000000000000",
        handle="owner/agent",
        kind="reference",
    )
    assert reference.kind == "reference"
    encoded = jsonable_encoder(reference)
    assert encoded["kind"] == "reference", (
        f"band-sdk {version('band-sdk')} dropped mention kind: {encoded!r}"
    )

    plain = jsonable_encoder(
        ChatMessageRequestMentionsItem(
            id="00000000-0000-0000-0000-000000000001"
        )
    )
    assert "kind" not in plain, f"unexpected kind on plain mention: {plain!r}"

    print("band-sdk 1.3.0 mention-kind contract: OK")


if __name__ == "__main__":
    main()
