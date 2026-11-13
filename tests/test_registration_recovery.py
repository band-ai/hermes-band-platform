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
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(self.status)
            self.end_headers()
            self.wfile.write(self.body)

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
