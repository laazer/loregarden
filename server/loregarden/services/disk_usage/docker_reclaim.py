"""Machine → Disk: what Docker holds that nothing uses, and freeing it.

Docker keeps images, build cache and volumes inside one disk image, so a folder
scan sees a single opaque file and cleanup can only refuse it. This asks Docker
instead.

What goes, when a person confirms it: images no container uses (running or
stopped), the whole build cache, and the anonymous volumes no container is
attached to. What stays: every container, and every named volume. A stopped
compose stack keeps its database in a named volume, and removing its container
first is how `docker system prune --volumes` deletes that database. So this
never removes a container and never runs `system prune`.

The preview is advice; Docker decides. `volume rm` runs without `-f`, so a
volume something attached since the preview is refused by Docker itself, and a
requested volume no longer unattached is refused here before it is asked.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess

from loregarden.models.domain import (
    DiskCleanupEvent,
    DiskCleanupMethod,
    DiskCleanupOutcome,
    DiskKind,
)
from loregarden.services.docker_subprocess import ANONYMOUS_VOLUME_NAME, run_docker_reclaim
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

READ_TIMEOUT_SECONDS = 60.0
#: A prune of a large build cache can take minutes on a slow disk.
PRUNE_TIMEOUT_SECONDS = 600.0
#: Names per `volume rm`, so one argv stays well under the OS limit.
VOLUME_BATCH = 100

_SIZE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([kKMGTPE]?)i?B\s*$")
_UNIT = {
    "": 1,
    "k": 1000,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
    "P": 1000**5,
    "E": 1000**6,
}
_RECLAIMED = re.compile(r"Total reclaimed space:\s*(\S+)")


class DockerUnavailable(RuntimeError):
    """Docker could not be asked: no binary, or no daemon answering."""


class DockerOutputUnreadable(RuntimeError):
    """Docker answered in a shape this module does not understand."""


def parse_size(text: str) -> int:
    """Docker's human size (`units.HumanSize`, decimal: kB, MB, GB) in bytes."""
    match = _SIZE.match(text)
    if match is None:
        raise DockerOutputUnreadable(f"Docker reported a size this cannot read: {text!r}")
    return round(float(match.group(1)) * _UNIT[match.group(2)])


class _DfImage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    containers: str = Field(alias="Containers")
    unique_size: str = Field(alias="UniqueSize")


class _DfVolume(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(alias="Name")
    links: str = Field(alias="Links")
    size: str = Field(alias="Size")


class _DfCache(BaseModel):
    model_config = ConfigDict(extra="ignore")
    in_use: str = Field(alias="InUse")
    size: str = Field(alias="Size")


class _DiskFree(BaseModel):
    """`docker system df -v --format '{{json .}}'`, as far as cleanup reads it."""

    model_config = ConfigDict(extra="ignore")
    images: list[_DfImage] | None = Field(default=None, alias="Images")
    volumes: list[_DfVolume] | None = Field(default=None, alias="Volumes")
    build_cache: list[_DfCache] | None = Field(default=None, alias="BuildCache")


class DockerVolume(BaseModel):
    name: str
    size_bytes: int


class DockerReclaimPreview(BaseModel):
    #: Images no container (running or stopped) uses, and about what they free.
    unused_images: int
    unused_images_bytes: int
    build_cache_entries: int
    build_cache_bytes: int
    #: Anonymous volumes no container is attached to: what the cleanup removes.
    volumes: list[DockerVolume]
    volumes_bytes: int
    #: Named volumes no container is attached to: kept, because a name means
    #: someone meant to keep it. Listed so the person can see what stays.
    kept_named_volumes: list[DockerVolume]
    total_bytes: int


class DockerReclaimRequest(BaseModel):
    images: bool = False
    build_cache: bool = False
    #: Anonymous volume names from the preview the person confirmed.
    volumes: list[str] = Field(default_factory=list, max_length=5000)


class DockerReclaimStep(BaseModel):
    label: str
    outcome: DiskCleanupOutcome
    reclaimed_bytes: int = 0
    detail: str = ""


class DockerReclaimResult(BaseModel):
    steps: list[DockerReclaimStep]
    reclaimed_bytes: int
    failed_count: int


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return run_docker_reclaim(args, timeout=timeout)
    except FileNotFoundError as exc:
        raise DockerUnavailable("Docker is not installed (no `docker` command found).") from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerUnavailable(
            f"`docker {' '.join(args[:2])}` did not answer in {int(timeout)}s."
        ) from exc


def _disk_free() -> _DiskFree:
    proc = _run(["system", "df", "-v", "--format", "{{json .}}"], READ_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise DockerUnavailable(f"Docker is not answering: {reason}")
    try:
        return _DiskFree.model_validate(json.loads(proc.stdout))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DockerOutputUnreadable(
            f"`docker system df` output was not understood: {exc}"
        ) from exc


def _unattached(volume: _DfVolume) -> bool:
    # "N/A" or anything else unreadable counts as attached: keep what is unclear.
    return volume.links == "0"  # py-org: allow-string — Docker's own df output


def preview() -> DockerReclaimPreview:
    df = _disk_free()
    # Docker's own df vocabulary; anything but an exact "0"/"false" is kept.
    images = [i for i in df.images or [] if i.containers == "0"]  # py-org: allow-string
    cache = [c for c in df.build_cache or [] if c.in_use == "false"]  # py-org: allow-string
    loose = [v for v in df.volumes or [] if _unattached(v)]
    anonymous = [
        DockerVolume(name=v.name, size_bytes=parse_size(v.size))
        for v in loose
        if ANONYMOUS_VOLUME_NAME.fullmatch(v.name)
    ]
    named = [
        DockerVolume(name=v.name, size_bytes=parse_size(v.size))
        for v in loose
        if not ANONYMOUS_VOLUME_NAME.fullmatch(v.name)
    ]
    images_bytes = sum(parse_size(i.unique_size) for i in images)
    cache_bytes = sum(parse_size(c.size) for c in cache)
    volumes_bytes = sum(v.size_bytes for v in anonymous)
    return DockerReclaimPreview(
        unused_images=len(images),
        unused_images_bytes=images_bytes,
        build_cache_entries=len(cache),
        build_cache_bytes=cache_bytes,
        volumes=sorted(anonymous, key=lambda v: -v.size_bytes),
        volumes_bytes=volumes_bytes,
        kept_named_volumes=sorted(named, key=lambda v: -v.size_bytes),
        total_bytes=images_bytes + cache_bytes + volumes_bytes,
    )


def _prune(label: str, args: list[str]) -> DockerReclaimStep:
    proc = _run(args, PRUNE_TIMEOUT_SECONDS)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        logger.warning("docker %s failed: %s", " ".join(args), detail)
        return DockerReclaimStep(label=label, outcome=DiskCleanupOutcome.FAILED, detail=detail)
    match = _RECLAIMED.search(proc.stdout or "")
    if match is None:
        # It ran and exited 0; only the figure is missing. Say so rather than claim 0.
        logger.warning("docker %s printed no reclaimed total: %r", " ".join(args), proc.stdout)
        return DockerReclaimStep(
            label=label,
            outcome=DiskCleanupOutcome.DELETED,
            detail="Docker did not report how much it freed.",
        )
    return DockerReclaimStep(
        label=label, outcome=DiskCleanupOutcome.DELETED, reclaimed_bytes=parse_size(match.group(1))
    )


def _remove_volumes(names: list[str], sizes: dict[str, int]) -> DockerReclaimStep:
    removed: set[str] = set()
    errors: list[str] = []
    for start in range(0, len(names), VOLUME_BATCH):
        batch = names[start : start + VOLUME_BATCH]
        proc = _run(["volume", "rm", *batch], PRUNE_TIMEOUT_SECONDS)
        # Docker removes what it can, prints each removed name, and exits 1 if any failed.
        removed.update(line.strip() for line in (proc.stdout or "").splitlines())
        if proc.returncode != 0:
            errors.append((proc.stderr or "").strip() or f"exit {proc.returncode}")
    removed &= set(names)
    kept = len(names) - len(removed)
    freed = sum(sizes.get(name, 0) for name in removed)
    label = f"{len(removed)} of {len(names)} unattached volumes"
    if kept:
        detail = f"{kept} not removed: {'; '.join(errors)[:600]}"
        logger.warning("docker volume rm left %d volume(s): %s", kept, detail)
        outcome = DiskCleanupOutcome.DELETED if removed else DiskCleanupOutcome.FAILED
        return DockerReclaimStep(label=label, outcome=outcome, reclaimed_bytes=freed, detail=detail)
    return DockerReclaimStep(label=label, outcome=DiskCleanupOutcome.DELETED, reclaimed_bytes=freed)


def reclaim(session: Session, request: DockerReclaimRequest) -> DockerReclaimResult:
    """Free what the person confirmed, re-reading Docker first. Records every step."""
    if not (request.images or request.build_cache or request.volumes):
        raise ValueError("Choose at least one thing to clean up.")
    steps: list[DockerReclaimStep] = []
    if request.volumes:
        current = {v.name: v.size_bytes for v in preview().volumes}
        wanted = list(dict.fromkeys(request.volumes))
        stale = [name for name in wanted if name not in current]
        if stale:
            steps.append(
                DockerReclaimStep(
                    label=f"{len(stale)} volume(s) skipped",
                    outcome=DiskCleanupOutcome.REFUSED,
                    detail="Attached to a container, renamed or already gone since the preview.",
                )
            )
        ready = [name for name in wanted if name in current]
        if ready:
            steps.append(_remove_volumes(ready, current))
    if request.images:
        steps.append(_prune("Unused images", ["image", "prune", "-a", "-f"]))
    if request.build_cache:
        steps.append(_prune("Build cache", ["builder", "prune", "-a", "-f"]))
    for step in steps:
        session.add(
            DiskCleanupEvent(
                path=f"Docker: {step.label}",
                size_bytes=step.reclaimed_bytes,
                kind=DiskKind.DOCKER,
                method=DiskCleanupMethod.DOCKER_PRUNE,
                outcome=step.outcome,
                detail=step.detail,
            )
        )
    session.commit()
    return DockerReclaimResult(
        steps=steps,
        reclaimed_bytes=sum(s.reclaimed_bytes for s in steps),
        failed_count=sum(1 for s in steps if s.outcome is DiskCleanupOutcome.FAILED),
    )
