# Cross-machine agent install prompt

> **Most installs should use the Band web app's "Add to Hermes" flow** (generated
> from the bootstrapper source at
> [band-ai/add-band](https://github.com/band-ai/add-band)) — on the gateway host it
> installs the `add-band` skill from this repo and hands off to `hermes chat -s add-band`.

Use the prompt below only when you **can't run that on the target host** — e.g.
you're driving setup from a **different machine or a non-Hermes agent** (Claude
Code, etc.), or Hermes isn't yet configured as a working agent.
Hand it to any shell-capable agent with access to the gateway host. It fetches the
official `add-band` setup skill from GitHub and follows it end to end — install,
enable, credentials, restart, and verification all live in the skill, so this
prompt stays thin and never goes stale.

The agent pauses at two points that need you: providing Band credentials, and the
final in-Band @mention test.

---

```
You're connecting this machine's Hermes install to Band for me. Work in the shell.

1. Clone the repo and open the setup skill — it is the source of truth. Read it fully, then
   follow its Procedure end to end:
     git clone --depth 1 https://github.com/band-ai/hermes-band-platform /tmp/hbp
     cat /tmp/hbp/hermes_band_platform/skills/add-band/SKILL.md
   Run the skill's helper scripts (gateway_python.py, verify_install.py, verify_gateway.py,
   verify_roundtrip.py) from /tmp/hbp/hermes_band_platform/skills/add-band with the gateway's
   own Python (the skill shows how to derive it as HERMES_PY). Registration uses the SDK's
   `band-register-agent` command (`band.cli.register_agent`), installed with `band-sdk`.

2. Ground rules — honor these even where a step is ambiguous:
   • Install the plugin into the SAME Python that runs `hermes`, never another venv.
   • Use the Git-ref package install path until `hermes-band-platform` is published on
     PyPI. The later PR that switches to pinned PyPI install is blocked until publication
     is verified.
   • If you choose `hermes plugins install ... --enable` directory mode, explicitly prompt
     before installing `band-sdk>=1.0.0,<2.0.0` into the gateway Python and fail with a
     clear message if `"$HERMES_PY" -c "import band"` still fails.
   • Never edit or patch Hermes's own source. If the CLI can't enable the plugin, use the
     plugins.enabled config fallback the skill describes.
   • Never make me paste a Band *user* API key into a command — I'll set BAND_USER_API_KEY
     for the one registration step, then remove it.

3. Stop and ask me at the two human gates:
   • Credentials — I either create the Band agent at app.band.ai/agents/new and give you
     BAND_AGENT_ID + BAND_API_KEY, or I set BAND_USER_API_KEY for `band-register-agent`.
   • The live test — I @mention the agent in the "Hermes Agent Hub" room and confirm a reply.

4. When done, report: plugin version, how you enabled it (CLI vs config), the BAND_HUB_ROOM id,
   and the verify_gateway.py result.
```

---

## Notes

- **Reachability:** the agent must be able to install from
  `git+https://github.com/band-ai/hermes-band-platform.git@<ref>` for now. Switch
  to `pip install hermes-band-platform==...` only after the package is published
  and verified on PyPI.
- **Already installed?** Once the plugin is installed and the gateway restarted,
  the skill is also available natively as `hermes /add-band` — the clone step is
  only needed for the first install on a fresh box.
- **Directory install alternative:** on Hermes builds whose CLI doesn't list
  entry-point plugins, `hermes plugins install band-ai/hermes-band-platform
  --enable` avoids the config fallback and preserves the `plugin.yaml` manifest,
  but it still requires a separate prompted `band-sdk` install into the gateway
  Python plus an explicit `import band` check. The skill covers both paths.
