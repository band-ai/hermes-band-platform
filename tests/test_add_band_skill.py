"""Tests for the add-band setup skill."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest


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


def test_verify_gateway_reports_owner_presence(monkeypatch, tmp_path):
    module = _load_script("verify_gateway.py")
    env = {"BAND_HUB_ROOM": "room_123", "BAND_OWNER_ID": "owner-uuid"}
    monkeypatch.setattr(module, "_env_value", lambda name: env.get(name, ""))
    log_path = tmp_path / "gateway.log"
    log_path.write_text("[band] Connected as agent agent_123\n[band] Hub ready: room room_123\n")

    result = module.verify_gateway(log_path=log_path)

    assert result["band_owner_present"] is True

    env.pop("BAND_OWNER_ID")
    assert module.verify_gateway(log_path=log_path)["band_owner_present"] is False


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
    monkeypatch.setattr(module, "_access_policy_allowlist", lambda: False)

    result = module.verify_install()

    assert result["success"] is False
    assert "package_importable" in result["missing"]
    assert "sdk_importable" in result["missing"]
    assert "entry_point" in result["missing"]
    assert "band_api_key_present" in result["missing"]
    assert "access_policy_allowlist" in result["missing"]


def _install_fake_hermes_config(monkeypatch, store: dict):
    """Inject an in-memory ``hermes_cli.config`` so scripts run in any interpreter."""
    import sys
    import types

    pkg = sys.modules.get("hermes_cli") or types.ModuleType("hermes_cli")
    mod = types.ModuleType("hermes_cli.config")

    def load_config():
        import copy

        return copy.deepcopy(store.get("config", {}))

    def save_config(config):
        import copy

        store["config"] = copy.deepcopy(config)

    def get_env_value(name):
        return store.get("env", {}).get(name, "")

    def save_env_value(key, value):
        store.setdefault("saved", {})[key] = value
        store.setdefault("env", {})[key] = value

    mod.load_config = load_config
    mod.save_config = save_config
    mod.get_env_value = get_env_value
    mod.save_env_value = save_env_value
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", mod)


def test_ensure_access_policy_writes_and_is_idempotent(monkeypatch):
    module = _load_script("ensure_access_policy.py")
    store: dict = {"config": {}}
    _install_fake_hermes_config(monkeypatch, store)

    first = module.ensure_access_policy()
    assert first["success"] is True
    assert first["changed"] is True
    extra = store["config"]["platforms"]["band"]["extra"]
    assert extra["group_policy"] == "allowlist"
    assert extra["dm_policy"] == "allowlist"

    # Re-running is a no-op once both keys are set.
    second = module.ensure_access_policy()
    assert second["success"] is True
    assert second["changed"] is False


def test_ensure_access_policy_preserves_existing_extra(monkeypatch):
    module = _load_script("ensure_access_policy.py")
    store = {"config": {"platforms": {"band": {"extra": {"agent_id": "keep-me"}}}}}
    _install_fake_hermes_config(monkeypatch, store)

    result = module.ensure_access_policy()

    assert result["changed"] is True
    extra = store["config"]["platforms"]["band"]["extra"]
    assert extra["agent_id"] == "keep-me"  # untouched
    assert extra["group_policy"] == "allowlist"


def test_ensure_home_channel_sets_home_to_hub(monkeypatch):
    module = _load_script("ensure_home_channel.py")
    store = {"config": {}, "env": {"BAND_HUB_ROOM": "hub-1", "BAND_HOME_ROOM": ""}}
    _install_fake_hermes_config(monkeypatch, store)

    result = module.ensure_home_channel()

    assert result["success"] is True
    assert result["changed"] is True
    assert store["saved"]["BAND_HOME_ROOM"] == "hub-1"


def test_ensure_home_channel_respects_existing_home(monkeypatch):
    module = _load_script("ensure_home_channel.py")
    store = {"config": {}, "env": {"BAND_HUB_ROOM": "hub-1", "BAND_HOME_ROOM": "operator-room"}}
    _install_fake_hermes_config(monkeypatch, store)

    result = module.ensure_home_channel()

    assert result["success"] is True
    assert result["changed"] is False
    assert "saved" not in store  # existing home (incl. operator override) untouched


def test_ensure_home_channel_reports_when_no_hub(monkeypatch):
    module = _load_script("ensure_home_channel.py")
    _install_fake_hermes_config(monkeypatch, {"config": {}, "env": {}})

    result = module.ensure_home_channel()

    assert result["success"] is False
    assert result["changed"] is False
    assert "action" in result


def test_verify_install_access_policy_check_reads_config(monkeypatch):
    module = _load_script("verify_install.py")
    store = {
        "config": {"platforms": {"band": {"extra": {"group_policy": "allowlist"}}}},
        "env": {},
    }
    _install_fake_hermes_config(monkeypatch, store)

    assert module._access_policy_allowlist() is True

    store["config"] = {}  # no policy anywhere, BAND_ALLOW_ALL unset
    assert module._access_policy_allowlist() is False

    store["env"] = {"BAND_ALLOW_ALL": "true"}  # the env override authorizes too
    assert module._access_policy_allowlist() is True


def test_verify_install_detects_bundled_conversations_skill():
    # The band-conversations runtime skill ships in the repo, so the check sees
    # it via the directory-manifest fallback.
    module = _load_script("verify_install.py")
    assert module._conversations_skill_present() is True


def test_verify_install_reports_missing_conversations_skill(monkeypatch, tmp_path):
    # When neither the importable package nor the on-disk fallback carries the
    # skill, it is reported missing with a remediation action.
    module = _load_script("verify_install.py")
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda name: None)
    # Point the fallback resolution at a script path with no skills/ tree.
    fake_script = tmp_path / "a" / "b" / "c" / "d" / "verify_install.py"
    fake_script.parent.mkdir(parents=True)
    fake_script.write_text("")
    monkeypatch.setattr(module, "__file__", str(fake_script))

    assert module._conversations_skill_present() is False
    result = module.verify_install()
    assert "conversations_skill_present" in result["missing"]
    assert any("band-conversations" in a for a in result["actions"])

def test_register_agent_extracts_supported_response_shapes():
    module = _load_script("register_agent.py")

    agent_id, api_key = module._extract_credentials(
        {"data": {"agent": {"id": "agent_123"}, "credentials": {"api_key": "key_123"}}}
    )

    assert agent_id == "agent_123"
    assert api_key == "key_123"


def test_register_agent_requires_user_key(monkeypatch):
    module = _load_script("register_agent.py")
    monkeypatch.delenv("BAND_USER_API_KEY", raising=False)
    monkeypatch.delenv("BAND_API_KEY", raising=False)
    monkeypatch.delenv("BAND_AGENT_ID", raising=False)

    with pytest.raises(RuntimeError, match="Band API key is required"):
        module.register_agent()


def test_register_agent_reads_user_key_from_band_api_key(monkeypatch):
    """The web app's onboarding snippet exports the user key as BAND_API_KEY."""
    module = _load_script("register_agent.py")
    monkeypatch.delenv("BAND_USER_API_KEY", raising=False)
    monkeypatch.delenv("BAND_AGENT_ID", raising=False)
    monkeypatch.setenv("BAND_API_KEY", "user-key-from-snippet")

    captured = {}
    monkeypatch.setattr(module, "_registration_headers", lambda k: captured.update(key=k) or {})
    monkeypatch.setattr(
        module, "_save_credentials", lambda agent_id, api_key: captured.update(saved=(agent_id, api_key))
    )

    class _Resp:
        status = 200

        def read(self):
            return b'{"agent": {"id": "a1"}, "credentials": {"api_key": "agent-key"}}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: _Resp())

    result = module.register_agent()

    assert captured["key"] == "user-key-from-snippet"
    assert captured["saved"] == ("a1", "agent-key")
    assert result["success"] is True


def test_register_agent_short_circuits_when_already_registered(monkeypatch):
    """A re-run must not re-register (or misread the persisted agent key)."""
    module = _load_script("register_agent.py")
    monkeypatch.setenv("BAND_AGENT_ID", "existing-agent")
    monkeypatch.setenv("BAND_API_KEY", "persisted-agent-key")
    monkeypatch.delenv("BAND_USER_API_KEY", raising=False)

    def _fail(*a, **k):  # registration must not be attempted
        raise AssertionError("register_agent attempted a network call on a re-run")

    monkeypatch.setattr(module.urllib.request, "urlopen", _fail)

    result = module.register_agent()

    assert result == {
        "success": True,
        "already_registered": True,
        "agent_id": "existing-agent",
        "saved": [],
    }

def test_register_agent_headers_use_browser_like_fingerprint(monkeypatch):
    module = _load_script("register_agent.py")
    monkeypatch.delenv("BAND_USER_AGENT", raising=False)

    headers = module._registration_headers("user-key")

    assert headers["User-Agent"].startswith("Mozilla/5.0")
    assert headers["Accept"] == "application/json, text/plain, */*"
    assert headers["Accept-Language"] == "en-US,en;q=0.9"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-API-Key"] == "user-key"


def test_register_agent_headers_allow_user_agent_override(monkeypatch):
    module = _load_script("register_agent.py")
    monkeypatch.setenv("BAND_USER_AGENT", "BandTest/1.0")

    headers = module._registration_headers("user-key")

    assert headers["User-Agent"] == "BandTest/1.0"


def _stub_candidates(module, monkeypatch, banner=None, shebang=None, procs=()):
    monkeypatch.setattr(module, "_from_version_banner", lambda: banner)
    monkeypatch.setattr(module, "_from_launcher_shebang", lambda: shebang)
    monkeypatch.setattr(module, "_from_running_process", lambda: list(procs))


def test_gateway_python_accepts_supported_interpreter(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, banner="/gw/venv/bin/python")
    # First candidate imports hermes_cli at a supported version → it wins.
    monkeypatch.setattr(module, "_probe", lambda path: ((3, 12, 1), True))

    result = module.resolve()

    assert result["ok"] is True
    assert result["python"] == "/gw/venv/bin/python"
    assert result["method"] == "version-banner"
    assert result["error"] is None


def test_gateway_python_rejects_unsupported_version(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, banner="/gw/venv/bin/python")
    monkeypatch.setattr(module, "_probe", lambda path: ((3, 14, 0), True))

    result = module.resolve()

    assert result["ok"] is False
    assert result["python"] == "/gw/venv/bin/python"  # found, but version-gated
    assert "3.14" in result["error"]


def test_gateway_python_fails_when_no_candidate_has_hermes_cli(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, banner="/some/python")
    # No candidate can import hermes_cli (incl. the self fallback).
    monkeypatch.setattr(module, "_probe", lambda path: ((3, 12, 0), False))

    result = module.resolve()

    assert result["ok"] is False
    assert result["python"] is None
    assert "hermes_cli" in result["error"]


def test_verify_roundtrip_requires_a_hub_room(monkeypatch, capsys):
    module = _load_script("verify_roundtrip.py")
    monkeypatch.setattr(module, "_env_value", lambda name: "")

    rc = module.main([])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["success"] is False
    assert "HUB_ROOM" in payload["error"]
