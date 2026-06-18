"""Tests for the add-band setup skill."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "hermes_band_platform" / "skills" / "add-band"


def _load_script(name: str):
    path = SKILL_DIR / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_skill_frontmatter_description_is_concise():
    text = (SKILL_DIR / "SKILL.md").read_text()
    match = re.search(r"^description:\s*(.*)$", text, re.MULTILINE)
    assert match is not None
    description = match.group(1).strip().strip('"')
    assert len(description) <= 60
    assert description.endswith(".")


def test_register_agent_extracts_supported_response_shapes():
    module = _load_script("register_agent.py")

    assert module.extract_agent_credentials(
        {"agent": {"id": "agent_1"}, "api_key": "band_agent_key"}
    ) == ("agent_1", "band_agent_key")
    assert module.extract_agent_credentials(
        {"id": "agent_2", "key": "band_agent_key_2"}
    ) == ("agent_2", "band_agent_key_2")
    assert module.extract_agent_credentials(
        {"agent_id": "agent_3", "token": "band_agent_key_3"}
    ) == ("agent_3", "band_agent_key_3")


def test_register_agent_posts_user_key_without_printing_secret(monkeypatch, capsys):
    module = _load_script("register_agent.py")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self):
            return json.dumps(
                {"agent": {"id": "agent_http"}, "api_key": "band_agent_http_key"}
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["user_key"] = request.get_header("X-api-key")
        captured["body"] = request.data.decode()
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    agent_id, agent_key = module.register_agent(
        "band_user_secret",
        base_url="https://band.example",
        name="Hermes Test",
        timeout=7.0,
    )

    out, err = capsys.readouterr()
    assert agent_id == "agent_http"
    assert agent_key == "band_agent_http_key"
    assert captured["url"] == "https://band.example/api/v1/me/agents/register"
    assert captured["method"] == "POST"
    assert captured["user_key"] == "band_user_secret"
    assert json.loads(captured["body"]) == {
        "agent": {
            "name": "Hermes Test",
            "description": module.DEFAULT_DESCRIPTION,
        }
    }
    assert captured["timeout"] == 7.0
    assert "band_user_secret" not in out
    assert "band_user_secret" not in err


def test_verify_gateway_detects_successful_band_start(monkeypatch, tmp_path):
    module = _load_script("verify_gateway.py")
    monkeypatch.setattr(module, "_env_value", lambda name: "room_123")
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "[band] Connected as agent agent_123\n[band] Hub ready: room room_123\n"
    )

    result = module.verify_gateway(log_path=log_path)

    assert result["success"] is True
    assert result["band_hub_room_present"] is True
    assert result["success_signals"]
    assert result["failure_signals"] == []


def test_verify_gateway_accepts_home_room_without_hub(monkeypatch, tmp_path):
    """A pinned BAND_HOME_ROOM (no BAND_HUB_ROOM) is a valid main channel."""
    module = _load_script("verify_gateway.py")
    env = {"BAND_HOME_ROOM": "room_456"}
    monkeypatch.setattr(module, "_env_value", lambda name: env.get(name, ""))
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "[band] Connected as agent agent_123\n[band] Hub ready: room room_456\n"
    )

    result = module.verify_gateway(log_path=log_path)

    assert result["success"] is True
    assert result["band_hub_room_present"] is False
    assert result["band_home_room_present"] is True


def test_verify_install_reports_missing_requirements(monkeypatch):
    module = _load_script("verify_install.py")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(module, "_has_band_entry_point", lambda: False)
    monkeypatch.setattr(module, "_has_directory_manifest", lambda: False)
    monkeypatch.setattr(module, "_plugin_enabled", lambda: False)
    monkeypatch.setattr(module, "_env_value", lambda name: "")

    result = module.verify_install()

    assert result["success"] is False
    assert "package_importable" in result["missing"]
    assert "sdk_importable" in result["missing"]
    assert "entry_point" in result["missing"]
    assert "band_api_key_present" in result["missing"]
