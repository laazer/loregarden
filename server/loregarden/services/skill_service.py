"""DB-backed skill storage, parsing, seeding, and version history."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml
from loregarden.config import settings
from loregarden.models.domain import (
    Skill,
    SkillVersion,
    StudioSkillCreate,
    StudioSkillRestore,
    StudioSkillUpdate,
    StudioSkillVersionView,
    StudioSkillView,
)
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

_SKILL_SNAPSHOT_FIELDS = (
    "slug",
    "name",
    "description",
    "body",
    "required_capabilities_json",
    "pack_id",
    "pack_commit",
    "upstream_name",
    "version",
    "built_in",
)


@dataclass(frozen=True)
class ParsedSkillMarkdown:
    name: str
    description: str
    body: str


def validate_skill_slug(slug: str) -> str:
    value = slug.strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"Invalid skill slug: {slug}")
    return value


def _resolve_parse_args(
    slug_or_markdown: str, markdown: str | None, slug: str | None
) -> tuple[str, str]:
    """Resolve the (slug, markdown) pair from this function's three call forms."""
    if slug is not None:
        return slug, slug_or_markdown
    if markdown is None:
        return "", slug_or_markdown
    return slug_or_markdown, markdown


def _split_frontmatter(markdown: str) -> tuple[str | None, str]:
    """Split a leading YAML fence off the body. Frontmatter is None when there is none."""
    if not markdown.startswith("---"):
        return None, markdown
    lines = markdown.splitlines(keepends=True)
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    return None, markdown


def _frontmatter_fields(frontmatter: str, slug_value: str) -> tuple[str, str]:
    """Return (name, description) from frontmatter; empty strings when absent or invalid."""
    try:
        loaded = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        logger.warning("Invalid skill frontmatter for %r: %s", slug_value, exc)
        return "", ""
    if not isinstance(loaded, dict):
        logger.warning("Skill frontmatter for %r is not a mapping", slug_value)
        return "", ""
    raw_name = loaded.get("name")
    raw_description = loaded.get("description")
    return (
        raw_name.strip() if isinstance(raw_name, str) else "",
        raw_description.strip() if isinstance(raw_description, str) else "",
    )


def parse_skill_markdown(
    slug_or_markdown: str, markdown: str | None = None, *, slug: str | None = None
) -> ParsedSkillMarkdown:
    """Parse only a leading YAML frontmatter fence; all other fences are body."""
    slug_value, markdown_value = _resolve_parse_args(slug_or_markdown, markdown, slug)
    frontmatter, body = _split_frontmatter(markdown_value)
    if frontmatter is None:
        return ParsedSkillMarkdown(name=slug_value, description="", body=body)
    name, description = _frontmatter_fields(frontmatter, slug_value)
    return ParsedSkillMarkdown(name=name or slug_value, description=description, body=body)


def _skill_snapshot(skill: Skill) -> dict:
    return skill.model_dump(include=set(_SKILL_SNAPSHOT_FIELDS))


def _write_skill_version(
    session: Session, skill: Skill, *, created_by: str, change_note: str = ""
) -> None:
    session.add(
        SkillVersion(
            id=str(uuid4()),
            skill_id=skill.id,
            version=skill.version,
            snapshot_json=json.dumps(_skill_snapshot(skill)),
            created_by=created_by,
            change_note=change_note or "",
        )
    )


def _required_capabilities(raw: str) -> list[str]:
    try:
        loaded = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


def _skill_view(skill: Skill, *, read_only: bool = False) -> StudioSkillView:
    return StudioSkillView(
        id=skill.id,
        slug=skill.slug,
        name=skill.name,
        description=skill.description,
        body=skill.body,
        required_capabilities=_required_capabilities(skill.required_capabilities_json),
        pack_id=skill.pack_id,
        pack_commit=skill.pack_commit,
        upstream_name=skill.upstream_name,
        built_in=skill.built_in,
        read_only=read_only,
        version=skill.version,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


def _skill_snapshot_view(skill: Skill, snap: dict) -> StudioSkillView:
    return StudioSkillView(
        id=skill.id,
        slug=snap.get("slug", skill.slug),
        name=snap.get("name", ""),
        description=snap.get("description", ""),
        body=snap.get("body", ""),
        required_capabilities=_required_capabilities(snap.get("required_capabilities_json", "[]")),
        pack_id=snap.get("pack_id"),
        pack_commit=snap.get("pack_commit"),
        upstream_name=snap.get("upstream_name"),
        built_in=bool(snap.get("built_in", skill.built_in)),
        read_only=True,
        version=int(snap.get("version", skill.version) or skill.version),
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


def _apply_parsed_markdown(skill: Skill, raw: str, body: StudioSkillUpdate) -> None:
    """Apply markdown to `skill`; an explicit name/description in the payload wins."""
    parsed = parse_skill_markdown(skill.slug, raw)
    skill.body = parsed.body
    if body.name is None:
        skill.name = parsed.name
    if body.description is None:
        skill.description = parsed.description


def _apply_skill_update(skill: Skill, body: StudioSkillUpdate) -> None:
    """Mutate `skill` in place from an update payload. Persistence is the caller's job."""
    if body.body is not None:
        _apply_parsed_markdown(skill, body.body, body)
    if body.markdown is not None:
        _apply_parsed_markdown(skill, body.markdown, body)
    if body.name is not None:
        skill.name = body.name.strip() or skill.slug
    if body.description is not None:
        skill.description = body.description.strip()
    if body.required_capabilities is not None:
        skill.required_capabilities_json = json.dumps(body.required_capabilities)
    if body.pack_id is not None:
        skill.pack_id = body.pack_id
    if body.pack_commit is not None:
        skill.pack_commit = body.pack_commit
    if body.upstream_name is not None:
        skill.upstream_name = body.upstream_name


def skill_seed_root() -> Path:
    """The directory builtin skills are seeded from.

    Public because it is also half the answer to "is this skill name
    registered?": `get_skill` seeds from here on a miss, so a name that has a
    directory here resolves even when the `skills` table has not been seeded
    yet. Callers that must judge a name without opening a session — migrations,
    notably — read this alongside the table (see `db.migrations_templates`).
    """
    return settings.agent_context_dir / "skills"


def seed_builtin_skills(session: Session, *, skills_dir: Path | None = None) -> list[str]:
    root = skills_dir or skill_seed_root()
    if not root.is_dir():
        logger.warning("seed_builtin_skills: seed directory missing: %s", root)
        return []
    existing = {s.slug for s in session.exec(select(Skill)).all()}
    seeded: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        try:
            slug = validate_skill_slug(child.name)
        except ValueError as exc:
            logger.warning("seed_builtin_skills: skipping %s: %s", child, exc)
            continue
        if slug in existing:
            continue
        path = child / "SKILL.md"
        try:
            markdown = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("seed_builtin_skills: skipping unreadable skill %s: %s", path, exc)
            continue
        parsed = parse_skill_markdown(slug, markdown)
        if not parsed.body.strip():
            logger.warning("seed_builtin_skills: skipping empty skill body for %r", slug)
            continue
        now = datetime.now(timezone.utc)
        skill = Skill(
            id=str(uuid4()),
            slug=slug,
            name=parsed.name,
            description=parsed.description,
            body=parsed.body,
            required_capabilities_json="[]",
            pack_id=None,
            pack_commit=None,
            upstream_name=None,
            version=1,
            built_in=True,
            created_at=now,
            updated_at=now,
        )
        session.add(skill)
        session.flush()
        _write_skill_version(
            session,
            skill,
            created_by="migration",
            change_note="Seeded built-in skill from agent_context/skills",
        )
        existing.add(slug)
        seeded.append(slug)
    if seeded:
        session.commit()
    return seeded


class SkillService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_skills(self) -> list[StudioSkillView]:
        return [
            _skill_view(skill)
            for skill in self.session.exec(select(Skill).order_by(Skill.slug)).all()
        ]

    def list_skill_slugs(self) -> list[str]:
        return [skill.slug for skill in self.session.exec(select(Skill).order_by(Skill.slug)).all()]

    def get_skill(self, slug: str) -> StudioSkillView | None:
        try:
            valid_slug = validate_skill_slug(slug)
        except ValueError:
            return None
        skill = self.session.exec(select(Skill).where(Skill.slug == valid_slug)).first()
        if not skill or not skill.body.strip():
            return None
        return _skill_view(skill)

    def create_skill(self, body: StudioSkillCreate) -> StudioSkillView:
        slug = validate_skill_slug(body.slug)
        if self.session.exec(select(Skill).where(Skill.slug == slug)).first():
            raise ValueError(f"Skill already exists: {slug}")
        markdown = body.markdown if body.markdown is not None else body.body
        parsed = parse_skill_markdown(slug, markdown)
        now = datetime.now(timezone.utc)
        skill = Skill(
            id=str(uuid4()),
            slug=slug,
            name=(body.name if body.name is not None else parsed.name).strip() or slug,
            description=(
                body.description if body.description is not None else parsed.description
            ).strip(),
            body=parsed.body,
            required_capabilities_json=json.dumps(body.required_capabilities),
            pack_id=body.pack_id,
            pack_commit=body.pack_commit,
            upstream_name=body.upstream_name,
            version=1,
            built_in=False,
            created_at=now,
            updated_at=now,
        )
        if not skill.body.strip():
            raise ValueError("Skill body must not be empty")
        self.session.add(skill)
        self.session.flush()
        _write_skill_version(
            self.session,
            skill,
            created_by=body.created_by or "studio-ui",
            change_note=body.change_note,
        )
        self.session.commit()
        self.session.refresh(skill)
        return _skill_view(skill)

    def update_skill(self, slug: str, body: StudioSkillUpdate) -> StudioSkillView:
        skill = self.session.exec(
            select(Skill).where(Skill.slug == validate_skill_slug(slug))
        ).first()
        if not skill:
            raise ValueError(f"Skill not found: {slug}")
        _apply_skill_update(skill, body)
        if not skill.body.strip():
            raise ValueError("Skill body must not be empty")
        skill.version += 1
        skill.updated_at = datetime.now(timezone.utc)
        self.session.add(skill)
        self.session.flush()
        _write_skill_version(
            self.session,
            skill,
            created_by=body.created_by or "studio-ui",
            change_note=body.change_note,
        )
        self.session.commit()
        self.session.refresh(skill)
        return _skill_view(skill)

    def list_skill_versions(self, slug: str) -> list[StudioSkillVersionView]:
        skill = self.session.exec(
            select(Skill).where(Skill.slug == validate_skill_slug(slug))
        ).first()
        if not skill:
            raise ValueError(f"Skill not found: {slug}")
        rows = self.session.exec(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.version.desc())
        ).all()
        return [
            StudioSkillVersionView(
                version=row.version,
                created_by=row.created_by,
                change_note=row.change_note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_skill_version(self, slug: str, version: int) -> StudioSkillVersionView:
        skill, row = self._version_row(slug, version)
        snap = json.loads(row.snapshot_json or "{}")
        return StudioSkillVersionView(
            version=row.version,
            created_by=row.created_by,
            change_note=row.change_note,
            created_at=row.created_at,
            snapshot=_skill_snapshot_view(skill, snap),
        )

    def restore_skill_version(
        self, slug: str, version: int, body: StudioSkillRestore | None = None
    ) -> StudioSkillView:
        skill, row = self._version_row(slug, version)
        snap = json.loads(row.snapshot_json or "{}")
        restored = {
            field: snap[field]
            for field in _SKILL_SNAPSHOT_FIELDS
            if field not in {"slug", "version", "built_in"} and field in snap
        }
        skill.sqlmodel_update(restored)
        if not skill.body.strip():
            raise ValueError("Cannot restore an empty skill body")
        skill.version += 1
        skill.updated_at = datetime.now(timezone.utc)
        self.session.add(skill)
        self.session.flush()
        _write_skill_version(
            self.session,
            skill,
            created_by=(body.created_by if body else "studio-ui") or "studio-ui",
            change_note=body.change_note if body else f"Restored from v{version}",
        )
        self.session.commit()
        self.session.refresh(skill)
        return _skill_view(skill)

    def _version_row(self, slug: str, version: int) -> tuple[Skill, SkillVersion]:
        skill = self.session.exec(
            select(Skill).where(Skill.slug == validate_skill_slug(slug))
        ).first()
        if not skill:
            raise ValueError(f"Skill not found: {slug}")
        row = self.session.exec(
            select(SkillVersion).where(
                SkillVersion.skill_id == skill.id, SkillVersion.version == version
            )
        ).first()
        if not row:
            raise ValueError(f"Version {version} not found for skill {slug}")
        return skill, row
