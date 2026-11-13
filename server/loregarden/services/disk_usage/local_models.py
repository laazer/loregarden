"""Machine → Disk: the local LLM models on this machine, and deleting one.

A model is tens of gigabytes and its app can download it again, so this one
deletes outright: moving 21 GB to the Trash frees nothing until the Trash is
emptied, which on a full disk is the opposite of the point.

Two layouts are read, each the app's own:

- **LM Studio**: `<downloads folder>/<publisher>/<model>/`, the downloads folder
  from `~/.lmstudio/settings.json` (`downloadsFolder`), else `~/.lmstudio/models`.
- **Ollama**: `<models>/manifests/<registry>/<namespace>/<model>/<tag>` naming
  content-addressed `blobs/sha256-<hex>`, the models folder from
  `OLLAMA_MODELS`, else `~/.ollama/models`. Models share blobs, so a model's
  size is everything it references and the space deleting it frees is only
  the blobs no other manifest references. Deleting removes the manifest, then
  those blobs: what `ollama rm` does, without needing its server running.

LM Studio can "import" an Ollama model as a folder of symlinks into Ollama's
blobs. Such a folder is listed as a link (`links_to`): deleting it frees
nothing, and deleting the Ollama model it points at breaks it. So deleting an
Ollama model also removes the LM Studio link folders left pointing at nothing,
and the listing names them (`linked_by`) before anyone confirms.

Only a model in a fresh listing can be deleted, named by the id the listing
gave it, so no request can name an arbitrary path.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from loregarden.models.domain import (
    DiskCleanupEvent,
    DiskCleanupMethod,
    DiskCleanupOutcome,
    DiskKind,
)
from loregarden.services.disk_usage.store import remove_from_result
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session

logger = logging.getLogger(__name__)

OLLAMA_DEFAULT_REGISTRY = ("registry.ollama.ai", "library")


class ModelSource(StrEnum):
    LM_STUDIO = "lm_studio"
    OLLAMA = "ollama"


class ModelLocation(BaseModel):
    source: ModelSource
    path: str
    found: bool
    #: Why the folder could not be listed, when it exists but could not be read.
    error: str = ""


class LocalModel(BaseModel):
    id: str
    source: ModelSource
    name: str
    path: str
    size_bytes: int
    #: Space deleting it gives back. Less than `size_bytes` when an Ollama model
    #: shares blobs with another.
    frees_bytes: int
    modified_at: datetime
    #: An LM Studio folder made only of symlinks into another app's store.
    links_to: ModelSource | None = None
    #: Its links point at files that no longer exist, so the app cannot load it.
    broken: bool = False
    #: LM Studio link folders that stop working when this Ollama model goes.
    linked_by: list[str] = Field(default_factory=list)


class LocalModelList(BaseModel):
    locations: list[ModelLocation]
    models: list[LocalModel]


class ModelDeleteResult(BaseModel):
    id: str
    name: str
    outcome: DiskCleanupOutcome
    freed_bytes: int = 0
    detail: str = ""


class ModelNotFound(LookupError):
    """The id names no model in the current listing."""


@dataclass(frozen=True)
class ModelRoots:
    lm_studio: Path
    ollama: Path


class _LmStudioSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    downloads_folder: str | None = Field(default=None, alias="downloadsFolder")


class _OllamaLayer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    digest: str


class _OllamaManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    config: _OllamaLayer | None = None
    layers: list[_OllamaLayer] = Field(default_factory=list)

    def digests(self) -> set[str]:
        found = {layer.digest for layer in self.layers}
        if self.config is not None:
            found.add(self.config.digest)
        return found


def default_roots(home: Path | None = None) -> ModelRoots:
    home = home or Path.home()
    lm_studio = home / ".lmstudio" / "models"
    settings_file = home / ".lmstudio" / "settings.json"
    try:
        configured = _LmStudioSettings.model_validate_json(
            settings_file.read_text()
        ).downloads_folder
    except FileNotFoundError:  # silent-ok: no LM Studio settings means its default folder
        configured = None
    except (OSError, ValidationError) as exc:
        logger.warning("Could not read %s; using %s: %s", settings_file, lm_studio, exc)
        configured = None
    if configured:
        lm_studio = Path(configured).expanduser()
    ollama_env = os.environ.get("OLLAMA_MODELS")
    ollama = Path(ollama_env).expanduser() if ollama_env else home / ".ollama" / "models"
    return ModelRoots(lm_studio=lm_studio, ollama=ollama)


def _tree_size(path: Path) -> tuple[int, float]:
    """Bytes under `path` (symlinks not followed) and the newest mtime."""
    total = 0
    newest = path.lstat().st_mtime
    for dirpath, _dirs, files in os.walk(path, onerror=_raise):
        for name in files:
            st = os.lstat(os.path.join(dirpath, name))
            total += st.st_size
            newest = max(newest, st.st_mtime)
    return total, newest


def _raise(error: OSError) -> None:
    raise error


def _stamp(mtime: float) -> datetime:
    return datetime.fromtimestamp(mtime, tz=UTC).replace(tzinfo=None)


def _link_targets(folder: Path) -> list[Path] | None:
    """Where each file in `folder` links to, or None when any file is a real one."""
    targets: list[Path] = []
    for dirpath, _dirs, files in os.walk(folder, onerror=_raise):
        for name in files:
            entry = Path(dirpath) / name
            if not entry.is_symlink():
                return None
            targets.append(Path(os.path.normpath(entry.parent / os.readlink(entry))))
    return targets or None


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _lm_studio_models(root: Path, ollama_root: Path) -> list[LocalModel]:
    models: list[LocalModel] = []
    blobs = ollama_root / "blobs"
    for publisher in sorted(root.iterdir()):
        if publisher.is_symlink() or not publisher.is_dir():
            continue
        for folder in sorted(publisher.iterdir()):
            if folder.is_symlink() or not folder.is_dir():
                continue
            size, newest = _tree_size(folder)
            targets = _link_targets(folder)
            links_ollama = targets is not None and all(_is_inside(t, blobs) for t in targets)
            models.append(
                LocalModel(
                    id=f"{ModelSource.LM_STUDIO}:{publisher.name}/{folder.name}",
                    source=ModelSource.LM_STUDIO,
                    name=f"{publisher.name}/{folder.name}",
                    path=str(folder),
                    size_bytes=size,
                    frees_bytes=size,
                    modified_at=_stamp(newest),
                    links_to=ModelSource.OLLAMA if links_ollama else None,
                    broken=bool(targets) and not all(t.exists() for t in targets or []),
                )
            )
    return models


def _blob_path(root: Path, digest: str) -> Path:
    return root / "blobs" / digest.replace(":", "-")


def _ollama_manifests(root: Path) -> dict[Path, _OllamaManifest]:
    manifests: dict[Path, _OllamaManifest] = {}
    base = root / "manifests"
    if not base.is_dir():
        return manifests
    for path in sorted(base.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            manifests[path] = _OllamaManifest.model_validate_json(path.read_text())
        except (OSError, ValidationError) as exc:
            # Not a model we can size or safely delete; leaving it out is
            # visible (it is not listed) and the warning says why.
            logger.warning("Skipping unreadable Ollama manifest %s: %s", path, exc)
    return manifests


def _ollama_name(rel: Path) -> str:
    parts = rel.parts
    if len(parts) != 4:
        return "/".join(parts)
    registry, namespace, model, tag = parts
    if (registry, namespace) == OLLAMA_DEFAULT_REGISTRY:
        return f"{model}:{tag}"
    return f"{registry}/{namespace}/{model}:{tag}"


def _blob_size(root: Path, digest: str) -> int:
    try:
        return _blob_path(root, digest).stat().st_size
    except FileNotFoundError:  # silent-ok: a missing blob takes no space; the size says so
        return 0


def _linked_by(blobs: set[Path], links: list[LocalModel]) -> list[str]:
    """LM Studio link folders with a link into any of `blobs`."""
    names: list[str] = []
    for link in links:
        targets = _link_targets(Path(link.path)) or []
        if any(t in blobs for t in targets):
            names.append(link.name)
    return names


def _ollama_models(root: Path, links: list[LocalModel]) -> list[LocalModel]:
    manifests = _ollama_manifests(root)
    refcount: dict[str, int] = {}
    for manifest in manifests.values():
        for digest in manifest.digests():
            refcount[digest] = refcount.get(digest, 0) + 1
    base = root / "manifests"
    models: list[LocalModel] = []
    for path, manifest in manifests.items():
        rel = path.relative_to(base)
        digests = manifest.digests()
        sizes = {d: _blob_size(root, d) for d in digests}
        freed = {d for d in digests if refcount[d] == 1}
        models.append(
            LocalModel(
                id=f"{ModelSource.OLLAMA}:{rel.as_posix()}",
                source=ModelSource.OLLAMA,
                name=_ollama_name(rel),
                path=str(path),
                size_bytes=sum(sizes.values()),
                frees_bytes=sum(sizes[d] for d in freed),
                modified_at=_stamp(path.stat().st_mtime),
                linked_by=_linked_by({_blob_path(root, d) for d in freed}, links),
            )
        )
    return models


def _location(
    source: ModelSource, root: Path, reader: Callable[[], list[LocalModel]]
) -> tuple[ModelLocation, list[LocalModel]]:
    if not root.is_dir():
        return ModelLocation(source=source, path=str(root), found=False), []
    try:
        models = reader()
    except OSError as exc:
        logger.warning("Could not list %s models in %s: %s", source, root, exc)
        return ModelLocation(source=source, path=str(root), found=True, error=str(exc)), []
    return ModelLocation(source=source, path=str(root), found=True), models


def list_models(roots: ModelRoots | None = None) -> LocalModelList:
    roots = roots or default_roots()
    lm_location, lm_models = _location(
        ModelSource.LM_STUDIO,
        roots.lm_studio,
        lambda: _lm_studio_models(roots.lm_studio, roots.ollama),
    )
    links = [m for m in lm_models if m.links_to is ModelSource.OLLAMA]
    ol_location, ol_models = _location(
        ModelSource.OLLAMA, roots.ollama, lambda: _ollama_models(roots.ollama, links)
    )
    return LocalModelList(
        locations=[lm_location, ol_location],
        models=sorted(lm_models + ol_models, key=lambda m: -m.size_bytes),
    )


def _delete_ollama(
    roots: ModelRoots, model: LocalModel, links: list[LocalModel]
) -> tuple[int, str]:
    """Delete the manifest, the blobs only it used, and LM Studio links left dangling."""
    root = roots.ollama
    manifest_path = Path(model.path)
    manifests = _ollama_manifests(root)
    target = manifests.get(manifest_path)
    if target is None:
        raise ModelNotFound(f"{model.name} is no longer installed.")
    still_used: set[str] = set()
    for path, manifest in manifests.items():
        if path != manifest_path:
            still_used |= manifest.digests()
    manifest_path.unlink()
    base = root / "manifests"
    parent = manifest_path.parent
    while parent != base and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    freed = 0
    gone: set[Path] = set()
    for digest in target.digests() - still_used:
        blob = _blob_path(root, digest)
        gone.add(blob)
        try:
            size = blob.stat().st_size
            blob.unlink()
        except FileNotFoundError:  # silent-ok: already gone is the state we want
            continue
        freed += size
    removed: list[str] = []
    for link in links:
        folder = Path(link.path)
        targets = _link_targets(folder) or []
        # Only a folder of links every one of which now points at nothing.
        if targets and any(t in gone for t in targets) and not any(t.exists() for t in targets):
            shutil.rmtree(folder)
            removed.append(link.name)
    detail = f"Also removed the LM Studio link(s) to it: {', '.join(removed)}." if removed else ""
    return freed, detail


def delete_model(
    session: Session, model_id: str, roots: ModelRoots | None = None
) -> ModelDeleteResult:
    """Delete one listed model for good, and record what happened."""
    roots = roots or default_roots()
    listing = list_models(roots)
    model = next((m for m in listing.models if m.id == model_id), None)
    if model is None:
        raise ModelNotFound(f"No installed model has the id {model_id!r}. Refresh the list.")
    detail = ""
    try:
        if model.source is ModelSource.LM_STUDIO:
            folder = Path(model.path)
            if folder.is_symlink() or folder.parent.parent != roots.lm_studio:
                raise ModelNotFound(f"{model.path} is not a model folder of LM Studio.")
            # rmtree removes a symlink, never what it points at.
            shutil.rmtree(folder)
            freed = model.frees_bytes
            remove_from_result([model.path])
        else:
            links = [m for m in listing.models if m.links_to is ModelSource.OLLAMA]
            freed, detail = _delete_ollama(roots, model, links)
    except OSError as exc:
        logger.exception("Deleting model %s failed", model.path)
        result = ModelDeleteResult(
            id=model.id,
            name=model.name,
            outcome=DiskCleanupOutcome.FAILED,
            detail=f"Could not delete it: {exc}. Part of it may already be gone.",
        )
    else:
        result = ModelDeleteResult(
            id=model.id,
            name=model.name,
            outcome=DiskCleanupOutcome.DELETED,
            freed_bytes=freed,
            detail=detail,
        )
    session.add(
        DiskCleanupEvent(
            path=model.path,
            size_bytes=result.freed_bytes,
            kind=DiskKind.MODEL,
            method=DiskCleanupMethod.DELETE,
            outcome=result.outcome,
            detail=result.detail,
        )
    )
    session.commit()
    return result
