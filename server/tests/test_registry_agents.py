"""Registry agents must have role files with memory protocol guidance."""

from loregarden.agents.registry import AGENTS
from loregarden.config import settings

MEMORY_MARKER = "memory_protocol_v1.md"


def test_all_registry_agents_have_role_files():
    missing: list[str] = []
    for agent_id, cfg in AGENTS.items():
        role_file = str(cfg.get("role_file", "")).strip()
        if not role_file:
            missing.append(f"{agent_id}: no role_file")
            continue
        path = settings.agent_context_dir / role_file
        if not path.is_file():
            missing.append(f"{agent_id}: missing {role_file}")
    assert not missing, "Registry agents missing role files:\n" + "\n".join(missing)


def test_all_registry_agents_reference_memory_protocol():
    gaps: list[str] = []
    for agent_id, cfg in AGENTS.items():
        role_file = str(cfg.get("role_file", "")).strip()
        if not role_file:
            gaps.append(agent_id)
            continue
        path = settings.agent_context_dir / role_file
        if not path.is_file():
            gaps.append(agent_id)
            continue
        text = path.read_text(encoding="utf-8")
        if MEMORY_MARKER not in text and "Memory protocol" not in text:
            gaps.append(f"{agent_id} ({role_file})")
    assert not gaps, "Registry agents missing memory protocol reference:\n" + "\n".join(gaps)


def test_registry_role_paths_under_agent_context():
    root = settings.agent_context_dir.resolve()
    for agent_id, cfg in AGENTS.items():
        role_file = str(cfg.get("role_file", "")).strip()
        if not role_file:
            continue
        path = (settings.agent_context_dir / role_file).resolve()
        assert path.is_file(), f"{agent_id}: {role_file}"
        assert str(path).startswith(str(root)), f"{agent_id}: role file outside agent_context"


def test_no_role_file_repeats_its_own_prompt():
    """A role file that contains its prompt more than once bills every run for it.

    The gatekeeper role shipped with three byte-identical copies of its prompt from
    the initial commit — 275 lines where 101 say the same thing. Nothing generated
    it (the studio's save path is idempotent); it was pasted that way, so only a
    check like this catches the next one.
    """
    offenders: list[str] = []
    for path in sorted(settings.agent_context_dir.rglob("*.md")):
        headings: dict[str, int] = {}
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("# "):
                headings[line] = headings.get(line, 0) + 1
        for heading, count in headings.items():
            if count > 1:
                rel = path.relative_to(settings.agent_context_dir)
                offenders.append(f"{rel}: {count}x {heading.strip()}")
    assert not offenders, "Role files repeating their own prompt:\n" + "\n".join(offenders)
