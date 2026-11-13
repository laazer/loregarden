"""Machine → Disk's local models: listing LM Studio and Ollama, and deleting one.

Every model here is built in `tmp_path` in the app's own layout; nothing reads
or deletes the real `~/.lmstudio` or `~/.ollama`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    DiskCleanupEvent,
    DiskCleanupMethod,
    DiskCleanupOutcome,
    DiskKind,
)
from loregarden.services.disk_usage import local_models
from loregarden.services.disk_usage.local_models import (
    ModelNotFound,
    ModelRoots,
    ModelSource,
    default_roots,
    delete_model,
    list_models,
)
from sqlmodel import Session, select


def _blob(root: Path, hexdigest: str, size: int) -> str:
    path = root / "blobs" / f"sha256-{hexdigest}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return f"sha256:{hexdigest}"


def _manifest(root: Path, name: str, tag: str, digests: list[str]) -> Path:
    path = root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    path.parent.mkdir(parents=True, exist_ok=True)
    config, *layers = digests
    path.write_text(
        json.dumps({"config": {"digest": config}, "layers": [{"digest": d} for d in layers]})
    )
    return path


def _lm_model(root: Path, publisher: str, name: str, size: int) -> Path:
    folder = root / publisher / name
    folder.mkdir(parents=True)
    (folder / "model.gguf").write_bytes(b"x" * size)
    return folder


def _link(lm_root: Path, name: str, target: Path) -> Path:
    folder = lm_root / "unknown" / name
    folder.mkdir(parents=True)
    os.symlink(target, folder / f"{name}.gguf")
    return folder


@pytest.fixture(name="roots")
def roots_fixture(tmp_path) -> ModelRoots:
    roots = ModelRoots(lm_studio=tmp_path / "lmstudio", ollama=tmp_path / "ollama")
    roots.lm_studio.mkdir()
    roots.ollama.mkdir()
    # Nothing here touches the real stored disk scan.
    with mock.patch.object(local_models, "remove_from_result"):
        yield roots


@pytest.fixture(name="shared")
def shared_fixture(roots):
    """Two Ollama models sharing one 300-byte blob; each has its own 100/200 bytes."""
    common = _blob(roots.ollama, "c" * 64, 300)
    one = _manifest(roots.ollama, "one", "7b", [_blob(roots.ollama, "1" * 64, 100), common])
    two = _manifest(roots.ollama, "two", "latest", [_blob(roots.ollama, "2" * 64, 200), common])
    return one, two


def _by_name(roots: ModelRoots):
    return {m.name: m for m in list_models(roots).models}


def test_lm_studio_models_are_listed_by_publisher_and_size(roots) -> None:
    _lm_model(roots.lm_studio, "lmstudio-community", "Big", 500)
    _lm_model(roots.lm_studio, "lmstudio-community", "Small", 50)
    models = list_models(roots).models
    assert [(m.name, m.size_bytes, m.frees_bytes) for m in models] == [
        ("lmstudio-community/Big", 500, 500),
        ("lmstudio-community/Small", 50, 50),
    ]
    assert {m.source for m in models} == {ModelSource.LM_STUDIO}


def test_an_ollama_model_frees_only_the_blobs_no_other_model_uses(shared, roots) -> None:
    models = _by_name(roots)
    assert (models["one:7b"].size_bytes, models["one:7b"].frees_bytes) == (400, 100)
    assert (models["two:latest"].size_bytes, models["two:latest"].frees_bytes) == (500, 200)


def test_a_missing_folder_is_reported_not_found(tmp_path) -> None:
    listing = list_models(ModelRoots(lm_studio=tmp_path / "no", ollama=tmp_path / "nope"))
    assert listing.models == []
    assert [loc.found for loc in listing.locations] == [False, False]


def test_deleting_an_ollama_model_keeps_shared_blobs(shared, roots, isolated_db) -> None:
    one, two = shared
    with Session(isolated_db) as session:
        result = delete_model(session, _by_name(roots)["one:7b"].id, roots)
        (event,) = session.exec(select(DiskCleanupEvent)).all()

    assert result.outcome is DiskCleanupOutcome.DELETED
    assert result.freed_bytes == 100
    assert not one.exists()
    assert not one.parent.exists()  # its empty model folder went with it
    assert two.exists()
    assert (roots.ollama / "blobs" / f"sha256-{'c' * 64}").exists()
    assert not (roots.ollama / "blobs" / f"sha256-{'1' * 64}").exists()
    assert (event.kind, event.method, event.size_bytes) == (
        DiskKind.MODEL,
        DiskCleanupMethod.DELETE,
        100,
    )


def test_an_lm_studio_link_into_ollama_is_named_and_removed_with_it(
    shared, roots, isolated_db
) -> None:
    own_blob = roots.ollama / "blobs" / f"sha256-{'1' * 64}"
    shared_blob = roots.ollama / "blobs" / f"sha256-{'c' * 64}"
    dangling = _link(roots.lm_studio, "one-GGUF", own_blob)
    still_good = _link(roots.lm_studio, "common-GGUF", shared_blob)

    models = _by_name(roots)
    assert models["unknown/one-GGUF"].links_to is ModelSource.OLLAMA
    assert models["one:7b"].linked_by == ["unknown/one-GGUF"]

    with Session(isolated_db) as session:
        result = delete_model(session, models["one:7b"].id, roots)

    assert "unknown/one-GGUF" in result.detail
    assert not dangling.exists()
    assert still_good.exists()


def test_a_broken_link_is_listed_as_broken_and_deleting_it_frees_its_link_only(
    roots, isolated_db, tmp_path
) -> None:
    target = tmp_path / "elsewhere.gguf"
    target.write_bytes(b"keep me")
    folder = _link(roots.lm_studio, "kept-GGUF", target)
    _link(roots.lm_studio, "broken-GGUF", roots.ollama / "blobs" / "sha256-gone")
    models = _by_name(roots)
    assert models["unknown/broken-GGUF"].broken is True
    assert models["unknown/kept-GGUF"].broken is False

    with Session(isolated_db) as session:
        delete_model(session, models["unknown/kept-GGUF"].id, roots)
    assert not folder.exists()
    assert target.read_bytes() == b"keep me"


def test_deleting_an_lm_studio_model_removes_its_folder(roots, isolated_db) -> None:
    folder = _lm_model(roots.lm_studio, "pub", "Model", 42)
    with Session(isolated_db) as session:
        result = delete_model(session, f"{ModelSource.LM_STUDIO}:pub/Model", roots)
    assert (result.outcome, result.freed_bytes) == (DiskCleanupOutcome.DELETED, 42)
    assert not folder.exists()


@pytest.mark.parametrize(
    "model_id",
    ["lm_studio:../../etc", "lm_studio:pub/Missing", "ollama:../../x", "/Users/me"],
)
def test_an_id_not_in_the_listing_deletes_nothing(roots, isolated_db, model_id) -> None:
    folder = _lm_model(roots.lm_studio, "pub", "Model", 1)
    with Session(isolated_db) as session, pytest.raises(ModelNotFound):
        delete_model(session, model_id, roots)
    assert folder.exists()


def test_a_failed_delete_is_recorded_as_failed(roots, isolated_db) -> None:
    _lm_model(roots.lm_studio, "pub", "Model", 1)
    with (
        mock.patch.object(local_models.shutil, "rmtree", side_effect=PermissionError("locked")),
        Session(isolated_db) as session,
    ):
        result = delete_model(session, f"{ModelSource.LM_STUDIO}:pub/Model", roots)
        (event,) = session.exec(select(DiskCleanupEvent)).all()
    assert result.outcome is DiskCleanupOutcome.FAILED
    assert "locked" in result.detail
    assert event.outcome is DiskCleanupOutcome.FAILED


def test_default_roots_follow_lm_studio_settings_and_ollama_env(tmp_path, monkeypatch) -> None:
    (tmp_path / ".lmstudio").mkdir()
    (tmp_path / ".lmstudio" / "settings.json").write_text(
        json.dumps({"downloadsFolder": str(tmp_path / "models-here")})
    )
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "ollama-here"))
    roots = default_roots(tmp_path)
    assert roots == ModelRoots(lm_studio=tmp_path / "models-here", ollama=tmp_path / "ollama-here")


def test_default_roots_without_settings_use_the_app_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    roots = default_roots(tmp_path)
    assert roots == ModelRoots(
        lm_studio=tmp_path / ".lmstudio" / "models", ollama=tmp_path / ".ollama" / "models"
    )
