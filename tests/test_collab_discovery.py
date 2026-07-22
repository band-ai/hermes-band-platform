"""Discoverability of Band as the agent-collaboration fabric.

Plugin skills never enter the system prompt's skill index (host behavior), so
outside a Band room the only ambient surfaces are (a) the flat-tree
``band-collaborate`` discovery skill the installer publishes into
``$HERMES_HOME/skills/`` and (b) the band toolset's own descriptions. These
tests pin both surfaces so a refactor can't silently strip the framing again.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from hermes_band_platform import tools

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "hermes_band_platform" / "skills" / "band-collaborate" / "SKILL.md"


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_discovery_skill_ships_with_indexable_description():
    meta = _frontmatter(SKILL)
    assert meta["name"] == "band-collaborate"
    # The description is what the flat skill index shows the model — it must
    # sell the capability ("collaborate ... agents ... Band"), not the plumbing.
    desc = meta["description"].lower()
    for needle in ("collaborate", "agents", "band"):
        assert needle in desc, f"description must mention '{needle}': {desc}"


def test_discovery_skill_points_at_tools_and_playbook():
    body = SKILL.read_text()
    for ref in (
        "band_find_contact",
        "band_create_room",
        "band_send_message",
        "band:band-conversations",
    ):
        assert ref in body, f"band-collaborate skill must reference {ref}"


def test_tool_descriptions_carry_collaboration_framing():
    """Tool descriptions are the only ambient Band surface in sessions where
    the toolset is wired but no skill was loaded — they must frame Band as
    where agents/collaborators are reachable, not just room plumbing."""
    by_name = {name: schema for name, schema, _, _ in tools.BAND_TOOLS}
    assert "collaborate" in by_name["band_create_room"]["description"].lower()
    assert "agents" in by_name["band_find_contact"]["description"].lower()
    assert "agents" in by_name["band_send_message"]["description"].lower()
