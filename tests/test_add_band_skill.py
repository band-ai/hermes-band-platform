"""Tests for the add-band setup skill."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
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


def _stub_candidates(module, monkeypatch, banner=(), sibling=(), shebang=None, procs=()):
    """Pin every candidate source so a test controls the whole chain."""
    for var in module.OVERRIDE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(module, "_from_launcher_sibling", lambda: list(sibling))
    monkeypatch.setattr(module, "_from_version_banner", lambda: list(banner))
    monkeypatch.setattr(module, "_from_launcher_shebang", lambda: shebang)
    monkeypatch.setattr(module, "_from_running_process", lambda: list(procs))


def _usable(module, version=(3, 12, 1)):
    return module.Probe(version=version, has_hermes_cli=True, prefix="/gw/.venv", base_prefix="/usr")


def _unusable(module, version=(3, 13, 0), import_error="ModuleNotFoundError: No module named 'yaml'"):
    return module.Probe(version=version, has_hermes_cli=False, import_error=import_error)


def _system(module, version=(3, 12, 1)):
    """A usable interpreter that is not a virtualenv (prefix == base_prefix)."""
    return module.Probe(version=version, has_hermes_cli=True, prefix="/usr", base_prefix="/usr")


def _fake_hermes_cli(root: Path) -> Path:
    """A `hermes_cli` package that is importable only via a path leak."""
    package = root / "hermes_cli"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "config.py").write_text("def save_env_value(name, value):\n    pass\n")
    return package


def test_gateway_python_accepts_supported_interpreter(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/gw/.venv/bin/python")
    # First candidate imports hermes_cli at a supported version → it wins.
    monkeypatch.setattr(module, "_probe", lambda path: _usable(module))

    result = module.resolve()

    assert result["ok"] is True
    assert result["python"] == "/gw/.venv/bin/python"
    assert result["method"] == "launcher-shebang"
    assert result["error"] is None
    assert result["is_venv"] is True
    assert result["warnings"] == []


def test_gateway_python_rejects_unsupported_version(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/gw/.venv/bin/python")
    monkeypatch.setattr(module, "_probe", lambda path: _usable(module, version=(3, 14, 0)))

    result = module.resolve()

    assert result["ok"] is False
    assert result["python"] == "/gw/.venv/bin/python"  # found, but version-gated
    assert "3.14" in result["error"]


def test_gateway_python_fails_when_no_candidate_has_hermes_cli(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/some/python")
    # No candidate can import hermes_cli (incl. the self fallback).
    monkeypatch.setattr(module, "_probe", lambda path: _unusable(module))

    result = module.resolve()

    assert result["ok"] is False
    assert result["python"] is None
    assert "hermes_cli" in result["error"]


def test_gateway_python_reports_the_missing_dependency(monkeypatch):
    """A source tree without its deps must name the real cause, not 'not found'.

    This is the `ModuleNotFoundError: yaml` that used to surface only later,
    inside register_agent.py, under an interpreter already declared good.
    """
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/usr/bin/python3.13")
    monkeypatch.setattr(module, "_probe", lambda path: _unusable(module))

    result = module.resolve()

    assert result["ok"] is False
    assert "yaml" in result["error"]
    assert any(entry.get("import_error") for entry in result["candidates"])


@pytest.mark.parametrize("var", ["HERMES_PY", "HERMES_PYTHON"])
def test_gateway_python_honors_the_documented_override(monkeypatch, var):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/wrong/python")
    monkeypatch.setenv(var, "/opt/hermes/.venv/bin/python3")
    monkeypatch.setattr(module, "_probe", lambda path: _usable(module))

    result = module.resolve()

    assert result["ok"] is True
    assert result["python"] == "/opt/hermes/.venv/bin/python3"
    assert result["method"] == "env"


def test_gateway_python_override_failure_does_not_fall_through(monkeypatch):
    """An override the operator got wrong must be loud, never quietly replaced."""
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/usr/bin/python3.13")
    monkeypatch.setenv("HERMES_PY", "/opt/hermes/bin/python3")
    probes = {"/opt/hermes/bin/python3": _unusable(module)}
    monkeypatch.setattr(module, "_probe", lambda path: probes.get(path, _usable(module)))

    result = module.resolve()

    assert result["ok"] is False
    assert result["python"] is None  # the usable fallback is NOT substituted
    assert "HERMES_PY" in result["error"]
    assert "yaml" in result["error"]


def test_gateway_python_finds_a_dot_venv_project_layout(monkeypatch, tmp_path):
    """`.venv` is the layout the resolver used to miss — it only tried `venv/`."""
    module = _load_script("gateway_python.py")
    interpreter = tmp_path / ".venv" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    monkeypatch.setattr(module, "_hermes_launcher", lambda: "/usr/local/bin/hermes")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout=f"hermes 0.18.0\nProject: {tmp_path}\n", stderr=""
        ),
    )
    for var in module.OVERRIDE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(module, "_from_launcher_sibling", list)
    monkeypatch.setattr(module, "_from_launcher_shebang", lambda: None)
    monkeypatch.setattr(module, "_from_running_process", list)
    monkeypatch.setattr(module, "_probe", lambda path: _usable(module))

    result = module.resolve()

    assert result["ok"] is True
    assert result["python"] == str(interpreter)
    assert result["method"] == "version-banner"


def test_gateway_python_banner_covers_known_venv_layouts(monkeypatch):
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(module, "_hermes_launcher", lambda: "/usr/local/bin/hermes")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="Project: /opt/hermes\n", stderr=""),
    )

    paths = module._from_version_banner()

    assert "/opt/hermes/.venv/bin/python3" in paths
    assert "/opt/hermes/venv/bin/python" in paths
    assert any(path.endswith("Scripts/python.exe") for path in paths)


def test_gateway_python_warns_when_the_winner_is_a_system_interpreter(monkeypatch):
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/usr/bin/python3")
    monkeypatch.setattr(module, "_probe", lambda path: _system(module))

    result = module.resolve()

    assert result["ok"] is True  # a system-wide Hermes is legitimate, never a failure
    assert result["is_venv"] is False
    assert len(result["warnings"]) == 1
    assert "/usr/bin/python3" in result["warnings"][0]
    assert result["error"] is None


def test_gateway_python_does_not_warn_when_the_version_is_rejected(monkeypatch):
    """One problem at a time — don't stack a shape nudge on a hard rejection."""
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/usr/bin/python3.14")
    monkeypatch.setattr(module, "_probe", lambda path: _system(module, version=(3, 14, 0)))

    result = module.resolve()

    assert result["ok"] is False
    assert result["warnings"] == []


def test_gateway_python_override_suppresses_the_venv_warning(monkeypatch):
    """An explicit override is the operator's statement; don't second-guess it."""
    module = _load_script("gateway_python.py")
    _stub_candidates(module, monkeypatch, shebang="/gw/.venv/bin/python")
    monkeypatch.setenv("HERMES_PY", "/usr/bin/python3")
    monkeypatch.setattr(module, "_probe", lambda path: _system(module))

    result = module.resolve()

    assert result["ok"] is True
    assert result["method"] == "env"
    assert result["warnings"] == []


def test_gateway_python_prefers_the_earlier_method_over_venv_shape(monkeypatch, tmp_path):
    """Venv preference is a warning, never a reordering.

    A stronger-confidence method pointing at a system Python still wins over a
    weaker-confidence candidate that merely *looks* like a venv — shape is not
    evidence about which interpreter runs the gateway.
    """
    module = _load_script("gateway_python.py")
    system_py = tmp_path / "usr" / "bin" / "python3"
    venv_py = tmp_path / "gw" / ".venv" / "bin" / "python3"
    for path in (system_py, venv_py):
        path.parent.mkdir(parents=True)
        path.touch()
    _stub_candidates(module, monkeypatch, sibling=[str(system_py)], banner=[str(venv_py)])
    probes = {str(system_py): _system(module), str(venv_py): _usable(module)}
    monkeypatch.setattr(module, "_probe", lambda path: probes[path])

    result = module.resolve()

    assert result["python"] == str(system_py)
    assert result["method"] == "launcher-sibling"
    assert result["is_venv"] is False
    assert len(result["warnings"]) == 1  # nudged, not overridden


def test_gateway_python_print_mode_keeps_stdout_path_only(monkeypatch, capsys):
    """The `$(...)` contract: warnings go to stderr, stdout stays parseable."""
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(
        module,
        "resolve",
        lambda: {
            "ok": True,
            "python": "/usr/bin/python3",
            "warnings": ["/usr/bin/python3 looks like a system Python"],
        },
    )

    rc = module.main(["--print"])
    captured = capsys.readouterr()

    assert rc == 0
    assert captured.out == "/usr/bin/python3\n"
    assert "warning: /usr/bin/python3 looks like a system Python" in captured.err


@pytest.mark.parametrize(
    "cmdline",
    [
        "/opt/hermes/.venv/bin/python -m hermes_cli.main gateway run --replace",
        "/opt/hermes/.venv/bin/python -m hermes_cli.main --profile coder gateway run",
        "/opt/hermes/.venv/bin/python /opt/hermes/.venv/bin/hermes gateway run",
        "hermes gateway run",
    ],
)
def test_gateway_cmdline_regex_matches_real_launch_shapes(cmdline):
    module = _load_script("gateway_python.py")
    assert module._GATEWAY_CMDLINE_RE.search(cmdline)


@pytest.mark.parametrize(
    "cmdline",
    [
        # Every one of these matched the old bare-"hermes" pattern.
        "python3 /root/.hermes/plugins/band/skills/add-band/scripts/gateway_python.py",
        "tail -f /root/.hermes/logs/gateway.log",
        "/usr/bin/ssh -N -T -o BatchMode=yes hermes-server",
        "bash /home/nirs/band/add-band/hermes/bootstrap.sh",
        "/root/.hermes/hermes-agent/venv/bin/python /root/.hermes/hermes-agent/tools/"
        "mcp_stdio_watchdog.py --ppid 7385",
        "hermes chat -s band:add-band",
    ],
)
def test_gateway_cmdline_regex_rejects_incidental_hermes_mentions(cmdline):
    module = _load_script("gateway_python.py")
    assert not module._GATEWAY_CMDLINE_RE.search(cmdline)


def test_pgrep_gateway_pids_parses_the_pid_list(monkeypatch):
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout="111\n222\n", stderr=""),
    )

    assert module._pgrep_gateway_pids() == [111, 222]


def test_pgrep_gateway_pids_survives_a_missing_pgrep(monkeypatch):
    module = _load_script("gateway_python.py")

    def _boom(*a, **kw):
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(module.subprocess, "run", _boom)

    assert module._pgrep_gateway_pids() == []


def test_argv0_from_proc_reads_the_nul_separated_cmdline(monkeypatch, tmp_path):
    module = _load_script("gateway_python.py")
    cmdline = tmp_path / "cmdline"
    cmdline.write_bytes(b"/opt/hermes/.venv/bin/python3\x00-m\x00hermes_cli.main\x00gateway\x00run\x00")
    monkeypatch.setattr(module, "_proc_cmdline_path", lambda pid: cmdline)

    assert module._argv0_from_proc(1) == "/opt/hermes/.venv/bin/python3"


def test_argv0_from_ps_takes_the_first_argument(monkeypatch):
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="/opt/hermes/.venv/bin/python3 -m hermes_cli.main gateway run\n", stderr=""
        ),
    )

    assert module._argv0_from_ps(1) == "/opt/hermes/.venv/bin/python3"


def test_argv0_for_pid_falls_back_to_ps_without_proc(monkeypatch, tmp_path):
    """No /proc (macOS/BSD) is the common case, not an error path."""
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(module, "_proc_cmdline_path", lambda pid: tmp_path / "absent" / "cmdline")
    monkeypatch.setattr(module, "_argv0_from_ps", lambda pid: "/opt/hermes/.venv/bin/python3")

    assert module._argv0_for_pid(1) == "/opt/hermes/.venv/bin/python3"


def test_argv0_for_pid_never_invents_a_proc_path(monkeypatch, tmp_path):
    """The regression: `/proc/<pid>/exe` used to be returned verbatim on macOS,
    because non-strict `resolve()` normalizes a nonexistent path instead of failing."""
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(module, "_proc_cmdline_path", lambda pid: tmp_path / "absent" / "cmdline")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=""),
    )

    assert module._argv0_for_pid(7385) is None


def test_from_running_process_drops_self_parent_and_non_python(monkeypatch):
    module = _load_script("gateway_python.py")
    monkeypatch.setattr(module.os, "getpid", lambda: 10)
    monkeypatch.setattr(module.os, "getppid", lambda: 11)
    monkeypatch.setattr(module, "_pgrep_gateway_pids", lambda: [10, 11, 333, 444])
    argv0 = {10: "/self/python3", 11: "/parent/python3", 333: "/bin/bash", 444: "/opt/hermes/.venv/bin/python3"}
    monkeypatch.setattr(module, "_argv0_for_pid", lambda pid: argv0[pid])

    assert module._from_running_process() == ["/opt/hermes/.venv/bin/python3"]


def test_probe_ignores_a_hermes_cli_in_the_callers_cwd(monkeypatch, tmp_path):
    """The regression: cwd is on `sys.path` for `python -c`, so *any* interpreter
    looked like the gateway's when run from a Hermes source tree."""
    module = _load_script("gateway_python.py")
    _fake_hermes_cli(tmp_path)
    # The fake is genuinely importable from that directory — the test is not vacuous.
    sanity = subprocess.run(
        [sys.executable, "-c", "import hermes_cli.config"], cwd=tmp_path, capture_output=True
    )
    assert sanity.returncode == 0
    monkeypatch.chdir(tmp_path)

    probed = module._probe(sys.executable)

    assert probed is not None
    assert str(tmp_path) not in (probed.origin or "")
    if not probed.has_hermes_cli:
        assert probed.import_error  # rejection always carries a reason


def test_probe_ignores_a_hermes_cli_on_pythonpath(monkeypatch, tmp_path):
    module = _load_script("gateway_python.py")
    _fake_hermes_cli(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    sanity = subprocess.run(
        [sys.executable, "-c", "import hermes_cli.config"], env=env, capture_output=True
    )
    assert sanity.returncode == 0
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    probed = module._probe(sys.executable)

    assert probed is not None
    assert str(tmp_path) not in (probed.origin or "")


def test_probe_survives_a_chatty_import(monkeypatch):
    """Anything printed during import must not corrupt the probe's JSON report."""
    module = _load_script("gateway_python.py")
    report = json.dumps(
        {
            "version": [3, 12, 4],
            "prefix": "/opt/hermes/.venv",
            "base_prefix": "/usr",
            "hermes_cli": True,
            "origin": "/opt/hermes/.venv/lib/python3.12/site-packages/hermes_cli/config.py",
            "origin_in_prefix": True,
        }
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="loading config...\n" + module._MARKER + report, stderr=""
        ),
    )

    probed = module._probe("/opt/hermes/.venv/bin/python3")

    assert probed is not None
    assert probed.version == (3, 12, 4)
    assert probed.has_hermes_cli is True
    assert probed.is_venv is True


def test_verify_roundtrip_requires_a_hub_room(monkeypatch, capsys):
    module = _load_script("verify_roundtrip.py")
    monkeypatch.setattr(module, "_env_value", lambda name: "")

    rc = module.main([])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["success"] is False
    assert "HUB_ROOM" in payload["error"]
