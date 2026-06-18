# Cross-machine agent install prompt

> **Most installs should use the Band web app's "Add to Hermes" flow** (generated
> from the bootstrapper source at
> [band-ai/add-band](https://github.com/band-ai/add-band)) — on the gateway host it
> fetches the `add-band` skill from this repo and hands off to `hermes /add-band`.

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
   Run the skill's helper scripts (verify_install.py, register_agent.py, verify_gateway.py)
   from /tmp/hbp/hermes_band_platform/skills/add-band with the gateway's own Python (the
   skill shows how to derive it as HERMES_PY).

2. Ground rules — honor these even where a step is ambiguous:
   • Install the plugin into the SAME Python that runs `hermes`, never another venv.
   • Never edit or patch Hermes's own source. If the CLI can't enable the plugin, use the
     plugins.enabled config fallback the skill describes.
   • Never make me paste a Band *user* API key into a command — I'll set BAND_USER_API_KEY
     for the one registration step, then remove it.

3. Stop and ask me at the two human gates:
   • Credentials — I either create the Band agent at app.band.ai/agents/new and give you
     BAND_AGENT_ID + BAND_API_KEY, or I set BAND_USER_API_KEY for register_agent.py.
   • The live test — I @mention the agent in the "Hermes Agent Hub" room and confirm a reply.

4. When done, report: plugin version, how you enabled it (CLI vs config), the BAND_HUB_ROOM id,
   and the verify_gateway.py result.
```

---

## Notes

- **Reachability:** the agent must be able to clone `band-ai/hermes-band-platform`
  (or `pip install hermes-band-platform`). Confirm the repo/package is published
  and public before sharing this prompt.
- **Already installed?** Once the plugin is installed and the gateway restarted,
  the skill is also available natively as `hermes /add-band` — the clone step is
  only needed for the first install on a fresh box.
- **Directory install alternative:** on Hermes builds whose CLI doesn't list
  entry-point plugins, `hermes plugins install band-ai/hermes-band-platform
  --enable` (plus a separate `band-sdk` install) avoids the config fallback
  entirely and preserves the `plugin.yaml` manifest. The skill covers both paths.
