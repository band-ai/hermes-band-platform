"""End-to-end: directory-plugin install on a READ-ONLY gateway venv.

Simulates the hosted-runtime failure mode this install path exists for: the
gateway's site-packages (e.g. ``/opt/hermes/.venv``) is not writable, so the
old ``uv pip install --python <gateway>`` route dies with ``Permission
denied``. The test builds a disposable gateway venv (real ``hermes-agent``, NO
``band-sdk``), chmods its site-packages read-only, and asserts the whole flow
succeeds through user-writable paths only:

    install.sh → hermes plugins enable band → gateway loads hermes_plugins.band
    → import band (from $HERMES_HOME/band-libs) → hermes chat -s band:add-band

Heavy (venv build + network for wheels), so it is opt-in: set ``HERMES_E2E=1``
(CI does). Requires ``uv`` on PATH.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HERMES_E2E", "").strip() not in {"1", "true", "yes"},
    reason="e2e install test is opt-in: set HERMES_E2E=1",
)

REPO = Path(__file__).resolve().parents[1]
PY_SPEC = f"{sys.version_info.major}.{sys.version_info.minor}"


def _run(cmd, *, env=None, timeout=600, **kw):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=env, **kw
    )


def _chmod_tree(root: Path, *, writable: bool) -> None:
    mode_op = (lambda m: m | stat.S_IWUSR) if writable else (lambda m: m & ~0o222)
    for path in [root, *root.rglob("*")]:
        path.chmod(mode_op(path.stat().st_mode))


@pytest.fixture(scope="module")
def gateway_venv(tmp_path_factory):
    """A disposable 'gateway' venv: hermes-agent installed, band-sdk absent,
    site-packages read-only (the /opt/hermes/.venv shape)."""
    if shutil.which("uv") is None:
        pytest.skip("uv is required for the e2e install test")
    root = tmp_path_factory.mktemp("gwenv")
    venv = root / "venv"
    subprocess.run(
        ["uv", "venv", "-p", PY_SPEC, str(venv)], check=True, capture_output=True
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv / "bin" / "python"),
         "hermes-agent>=0,<1"],
        check=True, capture_output=True, timeout=900,
    )
    site = next((venv / "lib").glob("python*/site-packages"))
    _chmod_tree(site, writable=False)
    yield {
        "python": venv / "bin" / "python",
        "hermes": venv / "bin" / "hermes",
        "bin": venv / "bin",
        "site_packages": site,
    }
    _chmod_tree(site, writable=True)  # let pytest clean the tmp dir


@pytest.fixture(scope="module")
def hermes_home(tmp_path_factory):
    return tmp_path_factory.mktemp("hermes-home")


@pytest.fixture(scope="module")
def gw_env(gateway_venv, hermes_home):
    """Environment for driving the gateway venv against the isolated home."""
    env = os.environ.copy()
    env.update(
        HERMES_HOME=str(hermes_home),
        HERMES_PY=str(gateway_venv["python"]),
        PATH=f"{gateway_venv['bin']}:{env['PATH']}",
        # Keep the gateway from reaching a live LLM during the test.
        OPENAI_API_KEY="",
        OPENROUTER_API_KEY="",
        NOUS_API_KEY="",
    )
    # The plugin must come from the DIRECTORY install, not this test env's
    # pip-installed copy (pip entry-points override directory plugins).
    env.pop("PYTHONPATH", None)
    return env


@pytest.fixture(scope="module")
def installed(gateway_venv, hermes_home, gw_env):
    """Run the installer once for the module; assert it succeeded."""
    result = _run(["bash", str(REPO / "install.sh")], env=gw_env, cwd=REPO)
    assert result.returncode == 0, f"install.sh failed:\n{result.stdout}\n{result.stderr}"
    return result


def test_gateway_venv_is_actually_read_only(gateway_venv):
    """Negative control: the OLD install path must fail with Permission denied."""
    result = _run(
        ["uv", "pip", "install", "--python", str(gateway_venv["python"]),
         "band-sdk>=1.0.0,<2.0.0"],
        timeout=300,
    )
    assert result.returncode != 0
    assert "denied" in (result.stdout + result.stderr).lower()


def test_installer_lands_everything_under_hermes_home(installed, hermes_home):
    plugin = hermes_home / "plugins" / "band"
    assert (plugin / "plugin.yaml").is_file()
    assert (plugin / "__init__.py").is_file()
    assert (plugin / "_band_libs.py").is_file()
    assert (plugin / "skills" / "add-band" / "SKILL.md").is_file()
    assert (plugin / "skills" / "add-band" / "scripts" / "register_agent.py").is_file()
    assert (plugin / "skills" / "add-band" / "scripts" / "register-agent.sh").is_file()
    assert (hermes_home / "band-libs" / "band" / "__init__.py").is_file()
    # Discovery skill published into the FLAT tree (the one the prompt indexes).
    assert (hermes_home / "skills" / "band-collaborate" / "SKILL.md").is_file()


def test_installer_never_wrote_to_site_packages(installed, gateway_venv):
    site = gateway_venv["site_packages"]
    assert not (site / "band").exists()
    assert not (site / "hermes_band_platform").exists()


def test_gateway_loads_plugin_and_sdk_from_user_paths(installed, gateway_venv, gw_env):
    """The real host loader: hermes_plugins.band + band from band-libs."""
    probe = textwrap.dedent(
        """
        import json, sys
        from hermes_cli.plugins import get_plugin_manager

        mgr = get_plugin_manager()
        mgr.discover_and_load()
        band = mgr._plugins.get("band")
        assert band is not None, "band plugin not discovered"
        assert band.enabled and not band.error, band.error

        import band as band_sdk
        from gateway.platform_registry import platform_registry

        from hermes_cli.gateway import _all_platforms

        print(json.dumps({
            "module": band.module.__name__,
            "tools": sorted(band.tools_registered),
            "skills": sorted(mgr._plugin_skills),
            "band_origin": band_sdk.__file__,
            "band_libs_on_sys_path": any(p.endswith("band-libs") for p in sys.path),
            "platform_registered": platform_registry.is_registered("band"),
            "band_in_setup_menu": any(p["key"] == "band" for p in _all_platforms()),
        }))
        """
    )
    # -I + neutral cwd: keep this checkout (and its egg-info) off sys.path so
    # the probe sees only what a real gateway host would.
    result = _run(
        [str(gateway_venv["python"]), "-I", "-c", probe],
        env=gw_env, timeout=180, cwd=gw_env["HERMES_HOME"],
    )
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout.splitlines()[-1])
    assert info["module"] == "hermes_plugins.band"  # directory plugin, not pip
    assert "band_send_message" in info["tools"]
    assert "band:add-band" in info["skills"]
    assert str(Path(*Path(info["band_origin"]).parts[-3:])).startswith("band-libs")
    assert info["band_libs_on_sys_path"] is True
    assert info["platform_registered"] is True
    assert info["band_in_setup_menu"] is True


def test_band_stays_an_enumerable_channel_without_sdk(
    installed, gateway_venv, gw_env, hermes_home
):
    """REGRESSION GUARD: with band-libs missing (SDK unresolvable), Band must
    still appear in the gateway's channel enumeration as a degraded channel —
    an import-time abort silently removed it from every channel surface."""
    libs = hermes_home / "band-libs"
    hidden = hermes_home / "band-libs.hidden"
    libs.rename(hidden)
    try:
        probe = textwrap.dedent(
            """
            import json
            from hermes_cli.gateway import _all_platforms
            keys = [p["key"] for p in _all_platforms()]
            print(json.dumps({"band_listed": "band" in keys}))
            """
        )
        result = _run(
            [str(gateway_venv["python"]), "-I", "-c", probe],
            env=gw_env, timeout=180, cwd=gw_env["HERMES_HOME"],
        )
        assert result.returncode == 0, result.stderr
        info = json.loads(result.stdout.splitlines()[-1])
        assert info["band_listed"] is True
    finally:
        hidden.rename(libs)


def test_verify_install_asserts_band_libs_on_gateway_sys_path(
    installed, gateway_venv, gw_env, hermes_home
):
    script = (
        hermes_home / "plugins" / "band" / "skills" / "add-band" / "scripts"
        / "verify_install.py"
    )
    result = _run(
        [str(gateway_venv["python"]), "-I", str(script)],
        env=gw_env, timeout=180, cwd=gw_env["HERMES_HOME"],
    )
    report = json.loads(result.stdout)
    checks = report["checks"]
    assert checks["sdk_importable"] is True
    assert checks["band_libs_on_sys_path"] is True
    assert checks["directory_manifest"] is True
    assert checks["plugin_enabled"] is True
    assert report["band_libs_dir"] == str(hermes_home / "band-libs")


def test_hermes_chat_activates_the_add_band_skill(installed, gw_env):
    """`hermes chat -s band:add-band` must launch and resolve the plugin skill.

    A fresh $HERMES_HOME has no LLM provider, so on a clean box (CI — no
    host-level ~/.hermes auth) `hermes chat` hits the first-run setup gate
    before it activates any skill. Set a DUMMY OPENAI_API_KEY to clear that
    gate; it never reaches the network because stdin is closed — the REPL exits
    on EOF without taking a turn, and skill activation prints before any model
    call, which is all this asserts.
    """
    env = {**gw_env, "OPENAI_API_KEY": "sk-not-a-real-key-e2e-offline"}
    result = _run(
        ["hermes", "chat", "-s", "band:add-band"],
        env=env, timeout=180, stdin=subprocess.DEVNULL,
        cwd=gw_env["HERMES_HOME"],
    )
    assert "Activated skills: band:add-band" in result.stdout, result.stdout[-2000:]


def test_flat_discovery_skill_resolves_by_bare_name(installed, gw_env):
    """The band-collaborate copy in $HERMES_HOME/skills is a flat-tree skill —
    loadable by bare name from any session (and indexed in the system prompt),
    unlike plugin skills which need the band: namespace."""
    env = {**gw_env, "OPENAI_API_KEY": "sk-not-a-real-key-e2e-offline"}
    result = _run(
        ["hermes", "chat", "-s", "band-collaborate"],
        env=env, timeout=180, stdin=subprocess.DEVNULL,
        cwd=gw_env["HERMES_HOME"],
    )
    assert "Activated skills: band-collaborate" in result.stdout, result.stdout[-2000:]


def test_installer_is_idempotent(installed, gw_env):
    result = _run(["bash", str(REPO / "install.sh")], env=gw_env, cwd=REPO)
    assert result.returncode == 0, f"re-run failed:\n{result.stdout}\n{result.stderr}"
    assert "already enabled" in result.stdout
