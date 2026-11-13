"""Exercise registration diagnostics through its CLI and a real HTTP peer."""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "hermes_band_platform/skills/add-band/scripts/register_agent.py"
SECRET = "user-key-must-not-appear"


@pytest.fixture
def registration_peer():
    class Handler(BaseHTTPRequestHandler):
        status = 401
        body = b""
        requests: ClassVar[list] = []

        def do_POST(self):
            self.requests.append((self.path, self.headers.get("X-API-Key")))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(self.status)
            self.end_headers()
            self.wfile.write(self.body)

        do_GET = do_POST

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", Handler
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def helper_env(tmp_path):
    # The gateway env writer is not installed in this standalone helper test.
    # A file-backed substitute preserves writes across the real CLI subprocess.
    package = tmp_path / "hermes_cli"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "config.py").write_text(
        "import os, json\nfrom pathlib import Path\n"
        "def save_env_value(key, value):\n"
        " p = Path(os.environ['SAVED_ENV'])\n"
        " data = json.loads(p.read_text())\n"
        " data[key] = value\n"
        " p.write_text(json.dumps(data))\n"
    )
    saved = tmp_path / "saved.json"
    saved.write_text(json.dumps({"BAND_BASE_URL": "https://stale.example"}))
    env = {key: value for key, value in os.environ.items() if not key.startswith("BAND_")}
    env.update(PYTHONPATH=str(tmp_path), SAVED_ENV=str(saved), BAND_USER_API_KEY=SECRET)
    return env, saved


@pytest.mark.parametrize("status", [401, 403, 422, 500])
@pytest.mark.parametrize("body", [
    json.dumps({"error": SECRET}).encode(),
    json.dumps({"unexpected": SECRET}).encode(),
    f"<html>{SECRET}</html>".encode(),
])
def test_rejection_is_safe_and_not_retried(registration_peer, helper_env, status, body):
    destination, peer = registration_peer
    env, saved = helper_env
    peer.status, peer.body = status, body
    env["BAND_BASE_URL"] = destination + "/?private=" + SECRET
    result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert SECRET not in output
    assert f"HTTP {status}" in output
    assert destination in output
    assert ("authentication recovery" in output) == (status in {401, 403})
    assert len(peer.requests) == 1
    assert peer.requests[0][1] == SECRET
    assert json.loads(saved.read_text()) == {"BAND_BASE_URL": "https://stale.example"}


def test_success_persists_identity_and_destination(registration_peer, helper_env):
    destination, peer = registration_peer
    env, saved = helper_env
    peer.status = 201
    peer.body = json.dumps({"data": {"agent": {"id": "a1"}, "credentials": {"api_key": "agent-secret"}}}).encode()
    env["BAND_BASE_URL"] = destination
    result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert SECRET not in result.stdout + result.stderr
    assert "agent-secret" not in result.stdout + result.stderr
    assert json.loads(saved.read_text()) == {"BAND_BASE_URL": destination, "BAND_AGENT_ID": "a1", "BAND_API_KEY": "agent-secret"}
    assert len(peer.requests) == 1


@pytest.mark.parametrize("error_kind,status,recovery", [
    ("api", 401, True), ("api", 403, True), ("parsing", 401, True),
    ("parsing", 403, True), ("api", 500, False), ("network", None, False),
])
def test_roundtrip_cli_redacts_errors(monkeypatch, capsys, error_kind, status, recovery):
    import importlib.util
    from types import SimpleNamespace

    from band_rest.core import ParsingError
    from band_rest.core.api_error import ApiError

    script = SCRIPT.with_name("verify_roundtrip.py")
    spec = importlib.util.spec_from_file_location("recovery_roundtrip", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module._plugin_env, "resolve_layout", lambda **kwargs: SimpleNamespace(error=None, as_dict=dict))
    monkeypatch.setattr(module, "_env_value", lambda name: "room" if name == "BAND_HUB_ROOM" else "test-agent-key")
    errors = {
        "api": ApiError(status_code=status, body={"message": SECRET}, headers={"secret": SECRET}),
        "parsing": ParsingError(status_code=status, body={"message": SECRET}, headers={"secret": SECRET}),
        "network": ConnectionError(SECRET),
    }

    async def rejected(*args):
        raise errors[error_kind]

    monkeypatch.setattr(module, "_run", rejected)
    assert module.main([]) == 1
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    payload = json.loads(output.out)
    assert payload["success"] is False
    assert ("authentication recovery" in payload["error"]) == recovery
    if status is not None:
        assert f"HTTP {status}" in payload["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", ["_context_ids", "_new_human_message"])
@pytest.mark.parametrize("status", [401, 403, 500])
async def test_roundtrip_polling_exposes_auth_failure_only(registration_peer, helper, status):
    import importlib.util

    from band_rest import AsyncRestClient
    from band_rest.core.api_error import ApiError

    destination, peer = registration_peer
    peer.status = status
    peer.body = b"not-json"
    spec = importlib.util.spec_from_file_location("polling_roundtrip", SCRIPT.with_name("verify_roundtrip.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rest = AsyncRestClient(api_key=SECRET, base_url=destination)
    args = [rest, "room"] if helper == "_context_ids" else [rest, "room", set()]
    try:
        if status in {401, 403}:
            with pytest.raises(ApiError) as caught:
                await getattr(module, helper)(*args)
            assert caught.value.status_code == status
        else:
            result = await getattr(module, helper)(*args)
            assert result == (set() if helper == "_context_ids" else None)
    finally:
        await rest._client_wrapper.httpx_client.httpx_client.aclose()
