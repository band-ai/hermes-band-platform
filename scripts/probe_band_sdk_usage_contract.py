"""Prove the declared band-sdk floor really carries the usage contract.

Deliberately imports nothing from this package: the question is whether the SDK
at the pinned floor supplies ``TurnUsage`` and the two usage constants, and the
answer must not depend on our own code (or on hermes-agent being installed).
The plugin-side binding is covered by the unit suite.
"""

from importlib.metadata import version

from band.client.rest import ChatEventRequest
from band.core.types import USAGE_EVENT_TYPE, USAGE_METADATA_KEY, TurnUsage


def main() -> None:
    assert version("band-sdk") == "1.3.0"
    usage = TurnUsage.from_mapping(
        {"input": 7, "output": 3}, input="input", output="output"
    )
    assert (usage + usage).to_dict() == {
        "input_tokens": 14,
        "output_tokens": 6,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    event = ChatEventRequest(
        content="usage contract probe",
        message_type=USAGE_EVENT_TYPE,
        metadata={USAGE_METADATA_KEY: usage.to_dict()},
    )
    assert event.message_type == USAGE_EVENT_TYPE
    assert event.metadata[USAGE_METADATA_KEY] == usage.to_dict()
    print("band-sdk 1.3.0 usage contract: OK")


if __name__ == "__main__":
    main()
