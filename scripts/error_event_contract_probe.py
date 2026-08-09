"""Probe the minimum real band-sdk error-event contract without test stubs."""

from importlib.metadata import version

from band.client.rest import ChatEventRequest, DEFAULT_REQUEST_OPTIONS
from band.core.types import MessageType


EXPECTED_SDK_VERSION = "1.1.0"


def main() -> None:
    installed = version("band-sdk")
    assert installed == EXPECTED_SDK_VERSION, (
        f"probe requires band-sdk=={EXPECTED_SDK_VERSION}, found {installed}"
    )
    assert MessageType.ERROR.value == "error"

    event = ChatEventRequest(
        content="failure contract probe",
        message_type=MessageType.ERROR,
        metadata={"error_code": "turn_failed", "message_id": "probe-message"},
    )
    assert event.content == "failure contract probe"
    assert event.message_type == "error"
    assert event.metadata == {
        "error_code": "turn_failed",
        "message_id": "probe-message",
    }
    assert not hasattr(event, "mentions")
    assert DEFAULT_REQUEST_OPTIONS == {"max_retries": 3}
    print("band-sdk 1.1.0 error-event contract: OK")


if __name__ == "__main__":
    main()
