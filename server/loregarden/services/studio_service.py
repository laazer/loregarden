"""Agent & Workflow Studio — custom agents and workflow definitions."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from loregarden.agents.registry import AGENTS
from loregarden.agents.registry import list_agents as list_builtin_agents
from loregarden.config import settings
from loregarden.core.workflow_loader import write_template_version
from loregarden.mcp.tool_ids import (
    MEMORY_DEFAULT_MCP_TOOLS,
    mcp_tool_values,
)
from loregarden.models.domain import (
    ArtifactKind,
    StudioAgent,
    StudioAgentCreate,
    StudioAgentPreview,
    StudioAgentPreviewRequest,
    StudioAgentUpdate,
    StudioAgentVersion,
    StudioAgentVersionView,
    StudioAgentView,
    StudioGateCheck,
    StudioGeneratedAgent,
    StudioGeneratedWorkflow,
    StudioHandoffCheck,
    StudioMcpToolGuide,
    StudioSkillCreate,
    StudioSkillRestore,
    StudioSkillUpdate,
    StudioSkillVersionView,
    StudioSkillView,
    StudioWorkflow,
    StudioWorkflowCreate,
    StudioWorkflowStage,
    StudioWorkflowUpdate,
    StudioWorkflowVersionView,
    StudioWorkflowView,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from loregarden.services.exit_actions import validate_against_catalog
from loregarden.services.mcp_registry import registered_mcp_server_names
from loregarden.services.skill_service import SkillService
from loregarden.services.studio_agent_config import (
    default_mcp_tools,
    ensure_studio_role_preamble,
    load_role_body,
)
from loregarden.services.studio_agent_prompt import preview_agent_markdown
from loregarden.services.studio_agent_references import (
    refuse_agent_delete,
    scan_agent_references,
)
from loregarden.services.studio_agent_views import (
    agent_snapshot_view,
    agent_view,
    builtin_agent_view,
)
from loregarden.services.studio_generation import (
    build_agent_generate_prompt,
    build_workflow_generate_prompt,
    invoke_studio_generate_model,
    parse_agent_generate_payload,
    parse_workflow_generate_payload,
    slugify,
    tool_names,
)
from loregarden.services.studio_mcp_tool_guides import MCP_TOOL_GUIDES
from loregarden.services.studio_routing import SKIP_CONDITIONS
from loregarden.services.studio_workflow_validation import (
    available_agent_ids,
    available_skills,
    refuse_unconfirmed_stage_removal,
    validate_alternative_groups,
    validate_classify_routes_are_selectable,
    validate_default_skill,
    validate_has_terminal_stage,
    validate_stage_agent_ids,
    validate_stage_route_targets,
    validate_stage_skill_names,
)
from loregarden.services.workflow_service import WorkflowService
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

DEFAULT_HANDOFF_CHECKS = [
    StudioHandoffCheck(
        kind=ArtifactKind.MCP_COMPLETE,
        prompt="When stage deliverables are ready, ensure tests pass and call loregarden_complete_stage if you are the orchestrator; otherwise finish your role output clearly.",
    ),
    StudioHandoffCheck(
        kind=ArtifactKind.BLOCKING_CLEAR,
        prompt="Do not hand off with unresolved blocking_issues — document failures or request approval via loregarden_request_approval.",
    ),
]

DEFAULT_GATE_CHECKS = [
    StudioGateCheck(
        kind=ArtifactKind.WORKFLOW_GATE,
        title="Stage sign-off",
        impact="Human review required before the workflow advances.",
    ),
]

# Fields captured verbatim in each StudioAgentVersion snapshot. Matches the
# migration backfill (0022) for the columns that existed then; later columns are
# snapshotted going forward only, and a restore of an older snapshot resets them
# to their default rather than carrying the current value forward (see
# `restore_agent_version`).
#
# Together with _SNAPSHOT_EXCLUDED_FIELDS this must cover every column on
# StudioAgent — test_studio_agent_snapshot asserts the partition, so a new
# column fails the build until someone decides which side it belongs on. Left to
# memory, an omission here loses that column on every restore with no error.
_AGENT_SNAPSHOT_FIELDS = (
    "slug",
    "name",
    "description",
    "role_body",
    "adapter",
    "default_model",
    "timeout",
    "default_skill",
    "mcp_enabled",
    "mcp_tools_json",
    "gate_checks_json",
    "handoff_checks_json",
    "tool_grants_json",
    "built_in",
)

# Identity and bookkeeping — deliberately NOT snapshotted. `id` identifies the
# row a restore writes back to; `version` / `created_at` / `updated_at` are the
# history's own metadata, and restoring them would rewrite the history instead
# of appending to it.
_SNAPSHOT_EXCLUDED_FIELDS = ("id", "version", "created_at", "updated_at")


def _agent_snapshot(agent: StudioAgent) -> dict:
    return agent.model_dump(include=set(_AGENT_SNAPSHOT_FIELDS))


#: ``StudioAgentUpdate`` field -> ``StudioAgent`` column. A table rather than a
#: chain of ``if`` statements: each new editable field was another branch, and
#: the branch count is what the complexity gate measures. Adding a field is one
#: row here, not one branch in the updater.
_AGENT_UPDATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("name", "name"),
    ("description", "description"),
    ("role_body", "role_body"),
    ("adapter", "adapter"),
    ("default_model", "default_model"),
    ("timeout", "timeout"),
    ("default_skill", "default_skill"),
    ("mcp_enabled", "mcp_enabled"),
    ("mcp_tools", "mcp_tools_json"),
    ("gate_checks", "gate_checks_json"),
    ("handoff_checks", "handoff_checks_json"),
    ("tool_grants", "tool_grants_json"),
)

#: Columns stored as a JSON blob. ``model_dump`` has already turned any nested
#: model into plain data by the time these are written, so one dumps() covers
#: lists of checks and the grants object alike.
_AGENT_JSON_COLUMNS = frozenset(
    {"mcp_tools_json", "gate_checks_json", "handoff_checks_json", "tool_grants_json"}
)

_AGENT_STRIPPED_COLUMNS = frozenset({"name", "description"})


class AgentSlugImmutable(ValueError):
    """A PATCH asked to rename an agent, which is not a thing that can happen.

    A `ValueError` subclass so the route can catch it ahead of the generic
    `except ValueError` that maps every other `update_agent` failure to 404 —
    which would answer "agent not found" to a rename attempt. The precedent is
    `StageRemovalNeedsConfirmation`, caught the same way for the same reason.
    """


def _agent_update_values(body: StudioAgentUpdate) -> dict[str, object]:
    """Column -> value for the fields this update actually supplied.

    ``exclude_none`` is the "field omitted" test: a PATCH that leaves a field out
    must not overwrite it. ``change_note`` is absent from the table on purpose —
    it belongs to the version entry, not the agent row.
    """
    provided = body.model_dump(exclude_none=True)
    values: dict[str, object] = {}
    for field, column in _AGENT_UPDATE_COLUMNS:
        if field not in provided:
            continue
        value = provided[field]
        if column in _AGENT_JSON_COLUMNS:
            values[column] = json.dumps(value)
        elif column in _AGENT_STRIPPED_COLUMNS:
            values[column] = str(value).strip()
        elif column == "role_body":
            values[column] = ensure_studio_role_preamble(str(value))
        else:
            values[column] = value
    return values


def _write_agent_version(
    session: Session, agent: StudioAgent, *, created_by: str, change_note: str = ""
) -> None:
    session.add(
        StudioAgentVersion(
            id=str(uuid4()),
            agent_id=agent.id,
            version=agent.version,
            snapshot_json=json.dumps(_agent_snapshot(agent)),
            created_by=created_by,
            change_note=change_note or "",
        )
    )


def _seeded_mcp_tools(session: Session, agent: StudioAgent) -> list[str] | None:
    """The tool list this row was seeded with, or ``None`` if that is unknowable.

    Version 1 is always the seed entry — `seed_builtin_agents` writes it in the
    same flush as the row — and its snapshot carries the `mcp_tools_json` column
    verbatim, as a JSON *string*, because `_agent_snapshot` dumps columns rather
    than fields.

    ``None`` for a row whose seed entry is missing or unreadable: unknowable is
    not the same as unchanged, and the caller must leave such a row alone.
    """
    entry = session.exec(
        select(StudioAgentVersion)
        .where(StudioAgentVersion.agent_id == agent.id)
        .where(StudioAgentVersion.version == 1)
    ).first()
    if entry is None:
        return None
    try:
        snapshot = json.loads(entry.snapshot_json or "{}")
        tools = json.loads(snapshot.get("mcp_tools_json") or "null")
    except json.JSONDecodeError:
        logger.warning(
            "reconcile_builtin_mcp_tools: agent %r has an unreadable seed snapshot; "
            "leaving its tool list alone",
            agent.slug,
        )
        return None
    if not isinstance(tools, list):  # py-org: allow-isinstance
        return None
    return [str(name) for name in tools]


def reconcile_builtin_mcp_tools(session: Session) -> list[str]:
    """Offer built-in agents the MCP tools added since they were seeded.

    `seed_builtin_agents` writes `tool_names()` into the row once, when it first
    sees the slug, and no later tool ever reaches it. On this install that left
    every built-in agent holding 21 of 42 tools — so `TRIAGE_OPS_MCP_TOOLS` was
    documented as "offered to the ticket rail" while no rail could offer it, and
    the reference cache and docker ledger that the shared prompt asset describes
    to *every* agent were ungrantable. A tool an agent is told to call and cannot
    is the failure `mcp_context.resolve_control_plane_transport` exists to
    prevent, and `m_require_verify_evidence` already patched one instance of it
    by hand, one tool at a time.

    Runs on every boot rather than as a migration, so a tool added tomorrow needs
    no data change. Idempotent: it writes only when tools are actually missing,
    and each write records a version entry, because an operator who opens Studio
    and finds new tools ticked deserves to see why.

    **Appends; never removes.** The test for "an operator narrowed this agent" is
    that a tool the seed snapshot had is gone from the row — the only shape a
    narrowing can take, since `StudioAgentToolGrants` holds `CliTool` members and
    cannot express a per-MCP-tool exclusion. Extra tools *added* since the seed
    are not a narrowing: `m_require_verify_evidence` appended `attach_evidence`
    to every row, so requiring an exact match against the seed would skip the
    entire fleet — which is what the first version of this did.

    Custom agents are out of scope entirely: their tool list is their author's
    design, not a snapshot that went stale.

    Returns the slugs it changed.
    """
    current = tool_names()
    changed: list[str] = []
    narrowed: list[str] = []
    unknown: list[str] = []
    for agent in session.exec(select(StudioAgent).where(StudioAgent.built_in)).all():
        stored = [str(name) for name in json.loads(agent.mcp_tools_json or "[]")]
        missing = [name for name in current if name not in stored]
        if not missing:
            continue
        seeded = _seeded_mcp_tools(session, agent)
        if seeded is None:
            unknown.append(agent.slug)
            continue
        if set(seeded) - set(stored):
            narrowed.append(agent.slug)
            continue
        agent.mcp_tools_json = json.dumps([*stored, *missing])
        agent.version += 1
        agent.updated_at = datetime.now(timezone.utc)
        session.add(agent)
        session.flush()
        _write_agent_version(
            session,
            agent,
            created_by="reconcile",
            change_note=(
                f"Offered the {len(missing)} MCP tools added since this agent was seeded: "
                f"{', '.join(missing)}. Restore an earlier version to undo."
            ),
        )
        changed.append(agent.slug)
    if changed:
        session.commit()
        logger.info(
            "reconcile_builtin_mcp_tools: re-offered the current tool set to %s", sorted(changed)
        )
    # Both skips are correct and invisible from the schema. Said out loud so
    # "Baxter still cannot call X" has an answer that does not require reading
    # this function.
    if narrowed:
        logger.info(
            "reconcile_builtin_mcp_tools: left %s alone — each is missing a tool its seed "
            "snapshot had, so its list is an operator narrowing, not staleness",
            sorted(narrowed),
        )
    if unknown:
        logger.warning(
            "reconcile_builtin_mcp_tools: left %s alone — no readable seed snapshot, so "
            "there is no evidence of what they were seeded with",
            sorted(unknown),
        )
    return changed


def seed_builtin_agents(session: Session) -> list[str]:
    """Idempotently seed the registry built-ins into ``studio_agents`` so the DB is
    the single source of truth. Seed-WHEN-MISSING by slug — an existing row (edited
    or not) is never overwritten, preserving user edits and version history. Returns
    the slugs newly seeded.
    """
    existing = {a.slug for a in session.exec(select(StudioAgent)).all()}
    # Distinguishes "this root has no agent assets" from "an asset it declares
    # is missing" — see the role-body guard below.
    has_agent_context = settings.agent_context_dir.is_dir()
    seeded: list[str] = []
    for slug, cfg in AGENTS.items():
        if slug in existing:
            continue
        role_file = str(cfg.get("role_file", ""))
        role_body, excerpt = load_role_body(role_file)
        if role_file and not role_body:
            # The role body is what a chat rail renders as its identity, so an
            # empty seed produces an agent that answers with no character and no
            # rules — indistinguishable, from the outside, from a working one.
            #
            # A *missing agent_context tree* is a different situation and an
            # expected one: initialising a database for a root that has no
            # agent assets yet (`init_db` does this, and says so for skills
            # too). There is nothing to read, so seed the row and warn.
            # A tree that exists but lacks a role file its own registry
            # declares is a broken checkout — fail rather than ship the
            # lobotomised agent.
            if has_agent_context:
                raise ValueError(
                    f"Built-in agent {slug!r} declares role_file {role_file!r}, but it is "
                    f"missing or empty under {settings.agent_context_dir}. Seeding it would "
                    "create an agent with no role instructions."
                )
            logger.warning(
                "seed_builtin_agents: no agent_context tree at %s; seeding %r with an empty "
                "role body. Chat rails will render no role for it until the assets exist.",
                settings.agent_context_dir,
                slug,
            )
        agent = StudioAgent(
            id=str(uuid4()),
            slug=slug,
            name=str(cfg.get("name", slug)),
            description=excerpt or "",
            role_body=role_body,
            adapter=str(cfg.get("adapter", "claude")),
            # Registry pins model under `claude_model` (e.g. triage→haiku); wire it
            # into default_model, which is the key the executor actually reads.
            default_model=str(cfg.get("claude_model", "") or cfg.get("default_model", "")),
            timeout=int(cfg.get("timeout", 600)),
            default_skill="",
            mcp_enabled=True,
            # Preserve the prior built-in behavior of listing all MCP tools.
            mcp_tools_json=json.dumps(tool_names()),
            gate_checks_json="[]",
            handoff_checks_json="[]",
            version=1,
            built_in=True,
        )
        session.add(agent)
        session.flush()
        _write_agent_version(session, agent, created_by="seed")
        seeded.append(slug)
    if seeded:
        session.commit()
    return seeded


def _workflow_view(session: Session, workflow: StudioWorkflow) -> StudioWorkflowView:
    template_slug = ""
    template_version = 1
    if workflow.published_template_id:
        tpl = session.get(WorkflowTemplate, workflow.published_template_id)
        if tpl:
            template_slug = tpl.slug
            template_version = tpl.version
    return StudioWorkflowView(
        id=workflow.id,
        slug=workflow.slug,
        name=workflow.name,
        description=workflow.description,
        stages=[
            StudioWorkflowStage.model_validate(item)
            for item in json.loads(workflow.stages_json or "[]")
        ],
        transitions=json.loads(workflow.transitions_json or "[]"),
        published_template_id=workflow.published_template_id,
        published_template_slug=template_slug,
        built_in=False,
        source_path=f"studio:{workflow.slug}",
        read_only=False,
        version=template_version,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


def _studio_stage_from(item: dict) -> StudioWorkflowStage:
    """One stored stage as a `StudioWorkflowStage`, defaults filled.

    The five `setdefault` calls this replaces appeared identically in two view
    builders, and every field added to the model meant editing both — the same
    hand-maintained-list problem as the publish path, one layer down
    (lg-workflow-integrity-559).

    They supply defaults for stages written before those fields existed. The
    model already declares the same defaults, so this only covers keys stored as
    an explicit `null`, which `model_validate` would otherwise reject.
    """
    payload = dict(item)
    for field, default in _STORED_STAGE_DEFAULTS.items():
        if payload.get(field) is None:
            payload[field] = default() if callable(default) else default
    return StudioWorkflowStage.model_validate(payload)


#: Defaults for stage rows stored before a field existed. Callables where the
#: default is mutable, so two stages never share one list.
_STORED_STAGE_DEFAULTS: dict[str, object] = {
    "stage_type": "agent",
    "classify_routes": list,
    "parallel_agents": list,
    "gate_commands": list,
    "exit_actions_enabled": False,
    "exit_actions": list,
}


def _template_workflow_view(template: WorkflowTemplate) -> StudioWorkflowView:
    stages_raw = json.loads(template.stages_json or "[]")
    stages: list[StudioWorkflowStage] = []
    for item in stages_raw:
        stages.append(_studio_stage_from(item))
    return StudioWorkflowView(
        id=template.id,
        slug=template.slug,
        name=template.name,
        description=template.description,
        stages=stages,
        transitions=json.loads(template.transitions_json or "[]"),
        published_template_id=template.id,
        published_template_slug=template.slug,
        built_in=not template.source_path.startswith("studio:"),
        source_path=template.source_path,
        read_only=True,
        version=template.version,
        created_at=template.created_at,
        updated_at=template.created_at,
    )


def _template_snapshot_view(template: WorkflowTemplate, snap: dict) -> StudioWorkflowView:
    """Render a historical template snapshot (read-only) for the version-detail view."""
    stages: list[StudioWorkflowStage] = []
    for item in json.loads(snap.get("stages_json") or "[]"):
        stages.append(_studio_stage_from(item))
    source_path = snap.get("source_path", "")
    return StudioWorkflowView(
        id=template.id,
        slug=snap.get("slug", template.slug),
        name=snap.get("name", ""),
        description=snap.get("description", ""),
        stages=stages,
        transitions=json.loads(snap.get("transitions_json") or "[]"),
        published_template_id=template.id,
        published_template_slug=template.slug,
        built_in=bool(snap.get("built_in", not source_path.startswith("studio:"))),
        source_path=source_path,
        read_only=True,
        created_at=template.created_at,
        updated_at=template.created_at,
    )


# Generic vocabulary that implies a specialty even when the ticket text doesn't
# use the route's literal keyword (e.g. "modal button" implies frontend work).


class StudioService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_mcp_tools(self) -> list[str]:
        return tool_names()

    def list_mcp_tool_guides(self) -> list[StudioMcpToolGuide]:
        known = {item.name for item in MCP_TOOL_GUIDES}
        guides = list(MCP_TOOL_GUIDES)
        for name in tool_names():
            if name not in known:
                guides.append(
                    StudioMcpToolGuide(
                        name=name,
                        description="Loregarden MCP tool",
                        when_to_use="See loregarden_mcp_v1.md for usage.",
                        example=f"tools/call {name} {{}}",
                    )
                )
        return guides

    def agent_defaults(self) -> dict:
        return {
            "mcp_tools": default_mcp_tools(),
            "memory_mcp_tools": mcp_tool_values(MEMORY_DEFAULT_MCP_TOOLS),
            "handoff_checks": [item.model_dump() for item in DEFAULT_HANDOFF_CHECKS],
            "gate_checks": [item.model_dump() for item in DEFAULT_GATE_CHECKS],
            # Served rather than mirrored in the client so the vocabulary has one
            # source of truth; a hardcoded TS copy would drift from the resolver.
            "skip_conditions": list(SKIP_CONDITIONS),
        }

    def preview_agent(self, body: StudioAgentPreviewRequest) -> StudioAgentPreview:
        return preview_agent_markdown(body)

    def generate_agent(self, description: str) -> StudioGeneratedAgent:
        trimmed = (description or "").strip()
        if not trimmed:
            raise ValueError("Description is required")
        prompt = build_agent_generate_prompt(
            trimmed,
            agent_ids=available_agent_ids(self.session),
            skills=available_skills(),
            mcp_tools=tool_names(),
        )
        reply = invoke_studio_generate_model(self.session, prompt)
        generated = parse_agent_generate_payload(reply, skills=available_skills())
        if not generated:
            raise ValueError("Could not parse agent draft from assistant response")
        if generated.slug in AGENTS:
            generated.slug = slugify(f"{generated.slug}-custom")
        return generated

    def generate_workflow(self, description: str) -> StudioGeneratedWorkflow:
        trimmed = (description or "").strip()
        if not trimmed:
            raise ValueError("Description is required")
        agent_ids = available_agent_ids(self.session)
        skills = available_skills()
        prompt = build_workflow_generate_prompt(trimmed, agent_ids=agent_ids, skills=skills)
        reply = invoke_studio_generate_model(self.session, prompt)
        generated = parse_workflow_generate_payload(reply, agent_ids=agent_ids, skills=skills)
        if not generated:
            raise ValueError("Could not parse workflow draft from assistant response")
        return generated

    def _registered_servers(self) -> frozenset[str]:
        """The registry read every agent view needs, done once per operation."""
        return registered_mcp_server_names(self.session)

    def _agent_view_with_warnings(self, agent: StudioAgent) -> StudioAgentView:
        return agent_view(agent, registered_servers=self._registered_servers())

    def list_agents(self, *, include_builtin: bool = True) -> list[StudioAgentView]:
        # Resolved once for the whole list rather than per row — the warnings
        # need it, and a per-agent lookup would turn one query into N.
        registered = self._registered_servers()
        custom = [
            agent_view(agent, registered_servers=registered)
            for agent in self.session.exec(select(StudioAgent).order_by(StudioAgent.name)).all()
        ]
        if not include_builtin:
            return custom
        builtin_ids = {item.slug for item in custom}
        merged = list(custom)
        for item in list_builtin_agents():
            if item["id"] in builtin_ids:
                continue
            merged.append(builtin_agent_view(item["id"], item, registered_servers=registered))
        return sorted(merged, key=lambda item: (not item.built_in, item.name.lower()))

    def get_agent(self, slug: str) -> StudioAgentView | None:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if agent:
            return self._agent_view_with_warnings(agent)
        cfg = AGENTS.get(slug)
        if cfg:
            return builtin_agent_view(slug, cfg, registered_servers=self._registered_servers())
        return None

    def create_agent(self, body: StudioAgentCreate) -> StudioAgentView:
        slug = slugify(body.slug)
        if self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first():
            raise ValueError(f"Studio agent already exists: {slug}")
        if slug in AGENTS:
            raise ValueError(f"Slug conflicts with built-in agent: {slug}")
        validate_default_skill(body.default_skill)
        now = datetime.now(timezone.utc)
        mcp_tools = body.mcp_tools if body.mcp_tools else default_mcp_tools()
        handoffs = body.handoff_checks if body.handoff_checks else DEFAULT_HANDOFF_CHECKS
        agent = StudioAgent(
            slug=slug,
            name=body.name.strip(),
            description=body.description.strip(),
            role_body=ensure_studio_role_preamble(body.role_body),
            adapter=body.adapter or "claude",
            default_model=body.default_model,
            timeout=body.timeout,
            default_skill=body.default_skill,
            mcp_enabled=body.mcp_enabled,
            mcp_tools_json=json.dumps(mcp_tools),
            gate_checks_json=json.dumps([item.model_dump() for item in body.gate_checks]),
            handoff_checks_json=json.dumps([item.model_dump() for item in handoffs]),
            tool_grants_json=body.tool_grants.model_dump_json(),
            version=1,
            built_in=False,
            created_at=now,
            updated_at=now,
        )
        self.session.add(agent)
        self.session.flush()
        _write_agent_version(self.session, agent, created_by="studio-ui")
        self.session.commit()
        self.session.refresh(agent)
        return self._agent_view_with_warnings(agent)

    def update_agent(self, slug: str, body: StudioAgentUpdate) -> StudioAgentView:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            # Before the slug check: the sentence that refusal carries
            # ("duplicate the agent under a new slug instead") is false for an
            # agent that does not exist, and the delete path answers 404 first
            # for the same reason.
            raise ValueError(f"Studio agent not found: {slug}")
        # An unchanged *literal* echo is a no-op too, not only a slugify-equal
        # one: built-in agents are seeded with the registry id verbatim
        # (`ac_gatekeeper`), and slugify maps `_` to `-`, so comparing only the
        # slugified echo against the raw stored slug would refuse an agent's own
        # slug for all 29 of them. Slugifying both sides instead would collapse a
        # genuinely different stored slug into a silent no-op (A5/AC2).
        if body.slug is not None and body.slug != agent.slug and slugify(body.slug) != agent.slug:
            raise AgentSlugImmutable(
                f"slug cannot be changed after creation: '{agent.slug}' is a by-string key "
                "that workflow templates, drafts, ticket pins and the run ledger all name. "
                "Duplicate the agent under a new slug instead."
            )
        if body.default_skill is not None:
            validate_default_skill(body.default_skill)
        agent.sqlmodel_update(_agent_update_values(body))
        agent.updated_at = datetime.now(timezone.utc)
        agent.version += 1
        self.session.add(agent)
        self.session.flush()
        _write_agent_version(
            self.session, agent, created_by="studio-ui", change_note=body.change_note or ""
        )
        self.session.commit()
        self.session.refresh(agent)
        return self._agent_view_with_warnings(agent)

    def delete_agent(self, slug: str) -> None:
        """Delete the agent and its version history, or refuse and name why.

        The scan runs *first*, so a refusal leaves the session untouched and the
        agent writable afterwards. The 2xx arm is an ordering fix and nothing
        more: `StudioAgentVersion.agent_id` is a bare foreign key with no ORM
        relationship, so the unit of work has no delete-order dependency to
        honour and emits `DELETE FROM studio_agents` first, which SQLite's
        foreign-key check rejects. The `flush()` between the two sends the
        version deletes to the database before the agent row goes.
        """
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        references = scan_agent_references(self.session, slug)
        if references.blocking:
            raise refuse_agent_delete(slug, references)
        for version in self.session.exec(
            select(StudioAgentVersion).where(StudioAgentVersion.agent_id == agent.id)
        ).all():
            self.session.delete(version)
        self.session.flush()
        self.session.delete(agent)
        self.session.commit()

    def list_skills(self) -> list[StudioSkillView]:
        return SkillService(self.session).list_skills()

    def get_skill(self, slug: str) -> StudioSkillView | None:
        return SkillService(self.session).get_skill(slug)

    def create_skill(self, body: StudioSkillCreate) -> StudioSkillView:
        return SkillService(self.session).create_skill(body)

    def update_skill(self, slug: str, body: StudioSkillUpdate) -> StudioSkillView:
        return SkillService(self.session).update_skill(slug, body)

    def list_skill_versions(self, slug: str) -> list[StudioSkillVersionView]:
        return SkillService(self.session).list_skill_versions(slug)

    def get_skill_version(self, slug: str, version: int) -> StudioSkillVersionView:
        return SkillService(self.session).get_skill_version(slug, version)

    def restore_skill_version(
        self, slug: str, version: int, body: StudioSkillRestore | None = None
    ) -> StudioSkillView:
        return SkillService(self.session).restore_skill_version(slug, version, body)

    def list_agent_versions(self, slug: str) -> list[StudioAgentVersionView]:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        rows = self.session.exec(
            select(StudioAgentVersion)
            .where(StudioAgentVersion.agent_id == agent.id)
            .order_by(StudioAgentVersion.version.desc())
        ).all()
        return [
            StudioAgentVersionView(
                version=row.version,
                created_by=row.created_by,
                change_note=row.change_note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_agent_version(self, slug: str, version: int) -> StudioAgentVersionView:
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        row = self.session.exec(
            select(StudioAgentVersion).where(
                StudioAgentVersion.agent_id == agent.id, StudioAgentVersion.version == version
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for agent {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        return StudioAgentVersionView(
            version=row.version,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            snapshot=agent_snapshot_view(
                agent, snap, registered_servers=self._registered_servers()
            ),
        )

    def restore_agent_version(self, slug: str, version: int) -> StudioAgentView:
        """Apply an old snapshot as a NEW head version. History is never mutated."""
        agent = self.session.exec(select(StudioAgent).where(StudioAgent.slug == slug)).first()
        if not agent:
            raise ValueError(f"Studio agent not found: {slug}")
        row = self.session.exec(
            select(StudioAgentVersion).where(
                StudioAgentVersion.agent_id == agent.id, StudioAgentVersion.version == version
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for agent {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        # A snapshot older than a column simply lacks its key. Carrying the
        # current value forward would leave a "restored" agent silently holding
        # settings that version never had — for tool grants that means a policy
        # outliving the restore that was supposed to undo it. Reset to the
        # column default instead, and report which fields that touched.
        # Read defaults off the field definitions rather than an instance: a
        # required column (``name``) is simply unset on a bare StudioAgent, so
        # instantiating one and dumping it silently omits those keys.
        defaults = {
            field: StudioAgent.model_fields[field].get_default(call_default_factory=True)
            for field in _AGENT_SNAPSHOT_FIELDS
        }
        current = agent.model_dump()
        reset_fields = [
            field
            for field in _AGENT_SNAPSHOT_FIELDS
            if field != "slug" and field not in snap and current[field] != defaults[field]
        ]
        restored = {
            field: snap.get(field, defaults[field])
            for field in _AGENT_SNAPSHOT_FIELDS
            if field != "slug"
        }
        if reset_fields:
            logger.info(
                "restore of agent %r to v%s predates %s; reset to defaults",
                slug,
                version,
                ", ".join(reset_fields),
            )
        agent.sqlmodel_update(restored)
        agent.version += 1
        agent.updated_at = datetime.now(timezone.utc)
        self.session.add(agent)
        self.session.flush()
        note = f"Restored from v{version}"
        if reset_fields:
            # Carried in the change note rather than a log line alone: the
            # version history is where an operator looks to understand what a
            # restore did, and a settings reset they cannot see is the kind of
            # quiet change that gets discovered weeks later.
            note += f" (v{version} predates {', '.join(reset_fields)}; reset to defaults)"
        _write_agent_version(self.session, agent, created_by="studio-ui", change_note=note)
        self.session.commit()
        self.session.refresh(agent)
        return self._agent_view_with_warnings(agent)

    def list_workflows(self) -> list[StudioWorkflowView]:
        custom = [
            _workflow_view(self.session, item)
            for item in self.session.exec(
                select(StudioWorkflow).order_by(StudioWorkflow.name)
            ).all()
        ]
        custom_slugs = {item.slug for item in custom}
        published_slugs = {
            item.published_template_slug for item in custom if item.published_template_slug
        }
        merged = list(custom)
        for template in WorkflowService(self.session).list_templates():
            if template.slug in published_slugs:
                continue
            if template.source_path.startswith("studio:"):
                studio_slug = template.source_path.removeprefix("studio:")
                if studio_slug in custom_slugs:
                    continue
            merged.append(_template_workflow_view(template))
        return sorted(merged, key=lambda item: (not item.built_in, item.name.lower()))

    def get_workflow(self, slug: str) -> StudioWorkflowView | None:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if workflow:
            return _workflow_view(self.session, workflow)
        template = WorkflowService(self.session).get_template_by_slug(slug)
        if template:
            return _template_workflow_view(template)
        return None

    def create_workflow(self, body: StudioWorkflowCreate) -> StudioWorkflowView:
        slug = slugify(body.slug)
        if self.session.exec(select(StudioWorkflow).where(StudioWorkflow.slug == slug)).first():
            raise ValueError(f"Studio workflow already exists: {slug}")
        stages = sorted(body.stages, key=lambda stage: stage.order)
        validate_stage_agent_ids(self.session, stages)
        validate_stage_route_targets(stages)
        validate_classify_routes_are_selectable(stages)
        validate_has_terminal_stage(stages)
        validate_stage_skill_names(stages)
        validate_alternative_groups(stages)
        for stage in stages:
            validate_against_catalog(stage.exit_actions)
        transitions = body.transitions or _auto_transitions(stages)
        now = datetime.now(timezone.utc)
        workflow = StudioWorkflow(
            slug=slug,
            name=body.name.strip(),
            description=body.description.strip(),
            stages_json=json.dumps([stage.model_dump() for stage in stages]),
            transitions_json=json.dumps(transitions),
            created_at=now,
            updated_at=now,
        )
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)

    def update_workflow(self, slug: str, body: StudioWorkflowUpdate) -> StudioWorkflowView:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if not workflow:
            raise ValueError(f"Studio workflow not found: {slug}")
        if body.name is not None:
            workflow.name = body.name.strip()
        if body.description is not None:
            workflow.description = body.description.strip()
        if body.stages is not None:
            stages = sorted(body.stages, key=lambda stage: stage.order)
            validate_stage_agent_ids(self.session, stages)
            validate_stage_route_targets(stages)
            validate_classify_routes_are_selectable(stages)
            validate_has_terminal_stage(stages)
            validate_stage_skill_names(stages)
            validate_alternative_groups(stages)
            for stage in stages:
                validate_against_catalog(stage.exit_actions)
            workflow.stages_json = json.dumps([stage.model_dump() for stage in stages])
            if body.transitions is None:
                # Editing a stage must not destroy hand-authored routes. This used to
                # regenerate a bare linear chain on every stage edit, silently dropping
                # every `when: reject` edge — the only thing that routes rework to the
                # stage a rejecting agent actually asked for. Seed a linear chain only
                # when there is nothing to preserve; otherwise keep the existing routes,
                # minus any that now point at a stage that no longer exists.
                existing = json.loads(workflow.transitions_json or "[]")
                workflow.transitions_json = json.dumps(
                    _prune_transitions(existing, stages) if existing else _auto_transitions(stages)
                )
        if body.transitions is not None:
            workflow.transitions_json = json.dumps(body.transitions)
        workflow.updated_at = datetime.now(timezone.utc)
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)

    def delete_workflow(self, slug: str) -> None:
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if not workflow:
            raise ValueError(f"Studio workflow not found: {slug}")
        self.session.delete(workflow)
        self.session.commit()

    def publish_workflow(
        self, slug: str, *, confirm_stage_removal: bool = False
    ) -> StudioWorkflowView:
        """Overwrite the published template from the draft.

        `confirm_stage_removal` gates the one irreversible case: a publish that
        drops a stage key some live ticket is currently sitting on. Those tickets
        read the LIVE template — `get_template_stages_at_version` falls back to it
        whenever `template_version` is NULL, which 152 of 790 instances were when
        this guard was written — so the publish would leave their cursor pointing
        at a stage that no longer exists. It is a confirmation rather than a
        refusal because removing a stage is a legitimate edit; doing it without
        being told what it costs is the part worth stopping.
        """
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if not workflow:
            raise ValueError(f"Studio workflow not found: {slug}")
        if not confirm_stage_removal:
            refuse_unconfirmed_stage_removal(self.session, workflow)
        stages = [
            StudioWorkflowStage.model_validate(item)
            for item in json.loads(workflow.stages_json or "[]")
        ]
        if not stages:
            raise ValueError("Workflow must have at least one stage")
        validate_stage_agent_ids(self.session, stages)
        validate_stage_route_targets(stages)
        validate_classify_routes_are_selectable(stages)
        validate_has_terminal_stage(stages)
        validate_stage_skill_names(stages)
        validate_alternative_groups(stages)
        for stage in stages:
            validate_against_catalog(stage.exit_actions)

        published_slug = f"studio-{workflow.slug}"
        stage_defs: list[dict] = []
        for stage in sorted(stages, key=lambda item: item.order):
            # Derived from the model, not enumerated. The literal this replaces
            # listed 17 keys by hand, and anything a caller added to
            # `StudioWorkflowStage` but forgot here was dropped on first publish
            # — silently, and visible later only as a gate that stopped firing.
            # It happened twice: `terminal` and `skip_when` first, then
            # `required_evidence`, `checklist` and `stage_brief`, each fixed by
            # adding more entries to the same list. Starting from `model_dump()`
            # makes the drift structurally impossible instead of test-detected
            # (lg-workflow-integrity-559).
            payload = stage.model_dump()
            # The one thing publish genuinely computes: a classify stage with no
            # agent of its own adopts its default route's, so the published
            # template names an agent even though the draft left it to routing.
            if stage.stage_type == "classify" and stage.classify_routes:
                default = next(
                    (route for route in stage.classify_routes if route.default),
                    stage.classify_routes[0],
                )
                payload["agent_id"] = stage.agent_id or default.agent_id
                payload["skill_name"] = stage.skill_name or default.skill_name
            stage_defs.append(payload)

        transitions = json.loads(workflow.transitions_json or "[]")
        if not transitions:
            transitions = _auto_transitions(stages)

        template = self.session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == published_slug)
        ).first()
        if template:
            template.name = workflow.name
            template.description = workflow.description or f"Studio workflow · {workflow.slug}"
            template.stages_json = json.dumps(stage_defs)
            template.transitions_json = json.dumps(transitions)
            template.source_path = f"studio:{workflow.slug}"
            template.built_in = False
            template.version += 1
        else:
            template = WorkflowTemplate(
                slug=published_slug,
                name=workflow.name,
                description=workflow.description or f"Studio workflow · {workflow.slug}",
                stages_json=json.dumps(stage_defs),
                transitions_json=json.dumps(transitions),
                source_path=f"studio:{workflow.slug}",
                version=1,
                built_in=False,
            )
            self.session.add(template)
            self.session.flush()
        write_template_version(self.session, template, created_by="studio-ui")

        workflow.published_template_id = template.id
        workflow.updated_at = datetime.now(timezone.utc)
        self.session.add(workflow)
        self.session.commit()
        self.session.refresh(workflow)
        return _workflow_view(self.session, workflow)

    def _resolve_workflow_template(self, slug: str) -> WorkflowTemplate | None:
        """Resolve a workflow slug to its versioned template: a studio draft via its
        published template, else a template slug directly."""
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if workflow and workflow.published_template_id:
            return self.session.get(WorkflowTemplate, workflow.published_template_id)
        return self.session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)
        ).first()

    def list_workflow_versions(self, slug: str) -> list[StudioWorkflowVersionView]:
        template = self._resolve_workflow_template(slug)
        if not template:
            raise ValueError(f"Workflow not found or unpublished: {slug}")
        rows = self.session.exec(
            select(WorkflowTemplateVersion)
            .where(WorkflowTemplateVersion.template_id == template.id)
            .order_by(WorkflowTemplateVersion.version.desc())
        ).all()
        return [
            StudioWorkflowVersionView(
                version=row.version,
                created_by=row.created_by,
                change_note=row.change_note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_workflow_version(self, slug: str, version: int) -> StudioWorkflowVersionView:
        template = self._resolve_workflow_template(slug)
        if not template:
            raise ValueError(f"Workflow not found or unpublished: {slug}")
        row = self.session.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == template.id,
                WorkflowTemplateVersion.version == version,
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for workflow {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        return StudioWorkflowVersionView(
            version=row.version,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            snapshot=_template_snapshot_view(template, snap),
        )

    def restore_workflow_version(self, slug: str, version: int) -> StudioWorkflowView:
        """Apply an old template snapshot as a NEW head version. History is never
        mutated. Slug/source_path/built_in are identity fields and are preserved."""
        template = self._resolve_workflow_template(slug)
        if not template:
            raise ValueError(f"Workflow not found or unpublished: {slug}")
        row = self.session.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == template.id,
                WorkflowTemplateVersion.version == version,
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for workflow {slug}")
        snap = json.loads(row.snapshot_json or "{}")
        template.sqlmodel_update(
            {
                field: snap[field]
                for field in ("name", "description", "stages_json", "transitions_json")
                if field in snap
            }
        )
        template.version += 1
        self.session.add(template)
        self.session.flush()
        write_template_version(
            self.session, template, created_by="studio-ui", change_note=f"Restored from v{version}"
        )
        self.session.commit()
        self.session.refresh(template)
        workflow = self.session.exec(
            select(StudioWorkflow).where(StudioWorkflow.slug == slug)
        ).first()
        if workflow:
            workflow.name = template.name
            workflow.description = template.description
            workflow.stages_json = template.stages_json
            workflow.transitions_json = template.transitions_json
            workflow.updated_at = datetime.now(timezone.utc)
            self.session.add(workflow)
            self.session.commit()
            self.session.refresh(workflow)
            return _workflow_view(self.session, workflow)
        return _template_workflow_view(template)


def _auto_transitions(stages: list[StudioWorkflowStage]) -> list[dict[str, str]]:
    """Seed a linear forward chain for a workflow that has no routes of its own.

    Forward-only by construction: it cannot express `when: reject`. Use it to
    bootstrap, never to rewrite — see _prune_transitions and update_workflow.
    """
    ordered = sorted(stages, key=lambda stage: stage.order)
    transitions: list[dict[str, str]] = []
    for idx in range(len(ordered) - 1):
        transitions.append({"from": ordered[idx].key, "to": ordered[idx + 1].key})
    return transitions


def _prune_transitions(
    transitions: list[dict[str, str]],
    stages: list[StudioWorkflowStage],
) -> list[dict[str, str]]:
    """Drop routes whose endpoints no longer exist, preserving everything else.

    A route to a deleted stage is a phantom target: apply_stage_route can't resolve
    it, so it degrades to the previous-stage fallback rather than erroring.
    """
    keys = {stage.key for stage in stages}
    return [item for item in transitions if item.get("from") in keys and item.get("to") in keys]
