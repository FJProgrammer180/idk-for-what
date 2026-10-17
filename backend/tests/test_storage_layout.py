"""Storage-root consistency: relocating the data dir must move uploads and artifacts with it.

This started as a live bug: with ``PLAB_DATA_DIR`` pointed at another volume, uploads still wrote
into the packaged default directory and then died in ``Path.relative_to`` - and the resulting 409
handed the client an absolute server path. The invariant is now enforced where it can be seen:
at settings construction.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides) -> Settings:
    """Fresh settings that ignore the environment the test session exported.

    ``conftest`` points PLAB_UPLOADS_DIR / PLAB_ARTIFACTS_DIR at the session-wide data dir, so a
    test that relocates only ``data_dir`` would otherwise inherit a child directory outside it -
    which the validator (correctly) refuses.
    """
    for key in ("PLAB_DATA_DIR", "PLAB_UPLOADS_DIR", "PLAB_ARTIFACTS_DIR"):
        monkeypatch.delenv(key, raising=False)
    return Settings(_env_file=None, environment="test", **overrides)


def test_relocating_the_data_dir_moves_uploads_and_artifacts_too(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "storage"
    settings = build(tmp_path, monkeypatch, data_dir=root)

    assert Path(settings.uploads_dir) == root / "uploads"
    assert Path(settings.artifacts_dir) == root / "artifacts"

    settings.ensure_dirs()
    assert Path(settings.uploads_dir).is_dir()
    assert Path(settings.artifacts_dir).is_dir()


def test_explicit_children_inside_the_data_dir_are_respected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "storage"
    (root / "uploads").mkdir(parents=True)
    settings = build(tmp_path, monkeypatch, data_dir=root, uploads_dir=root / "uploads")

    assert Path(settings.uploads_dir) == root / "uploads"


def test_a_child_dir_outside_the_data_dir_is_refused_at_startup(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(ValidationError) as excinfo:
        build(
            tmp_path, monkeypatch, data_dir=tmp_path / "storage", uploads_dir=tmp_path / "elsewhere"
        )

    message = str(excinfo.value)
    assert "must live inside" in message
    # the refusal explains the reason (relative path bookkeeping) rather than only naming a field
    assert "recorded relative to the data directory" in message


def test_relative_storage_path_records_uploads_prefixed_names(tmp_path: Path, monkeypatch) -> None:
    settings = build(tmp_path, monkeypatch, data_dir=tmp_path / "storage")
    settings.ensure_dirs()
    stored = Path(settings.uploads_dir) / "owner_abc.csv"
    stored.write_bytes(b"a,b\n1,2\n")

    recorded = settings.relative_storage_path(stored)

    assert recorded == f"uploads/{stored.name}"
    # and that is exactly what the download handler resolves back
    assert (Path(settings.data_dir) / recorded).read_bytes() == b"a,b\n1,2\n"


def test_relative_storage_path_refuses_rather_than_inventing_a_relative_name(tmp_path: Path, monkeypatch) -> None:
    settings = build(tmp_path, monkeypatch, data_dir=tmp_path / "storage")
    settings.ensure_dirs()

    with pytest.raises(RuntimeError, match="outside the configured storage root"):
        settings.relative_storage_path(tmp_path / "somewhere-else.csv")


def test_a_real_upload_lands_under_the_data_dir_and_is_returned_by_download(authed) -> None:
    """End-to-end for the path that broke: store the file, describe it, read the bytes back."""
    payload = "t,v\n0.0,0.0\n0.5,4.9\n1.0,9.8\n1.5,14.7\n2.0,19.7\n"

    response = authed.post("/api/v1/datasets", files={"file": ("falling.csv", payload.encode(), "text/csv")})
    assert response.status_code == 201, response.text
    body = response.json()
    dataset_id = body["dataset_id"]
    assert body["row_count"] == 5
    assert body["size_bytes"] == len(payload)
    assert body["sha256"], "the digest is what makes a re-upload deduplicable and the artifact auditable"

    detail = authed.get(f"/api/v1/datasets/{dataset_id}")
    assert detail.status_code == 200, detail.text
    # the export URL the client is handed is relative: no server directory structure in a response
    assert detail.json()["export_url"].startswith("/api/v1/datasets/")

    exported = authed.get(f"/api/v1/datasets/{dataset_id}/export")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"].startswith("text/csv")
    assert "19.7" in exported.text, "the cleaned export must still contain the measurements"
    assert exported.headers.get("x-content-provenance", "").startswith("derived from dataset")

    assert authed.delete(f"/api/v1/datasets/{dataset_id}").status_code == 204
    assert authed.get(f"/api/v1/datasets/{dataset_id}").status_code == 404


def test_a_rejected_upload_explains_itself_without_disclosing_paths(authed) -> None:
    """A rejected file is a user-facing error: it must not say where the app stores things."""
    response = authed.post("/api/v1/datasets", files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert response.status_code in (409, 415, 422), response.text
    message = response.json()["error"]["message"]
    assert not re.search(r"(?:^|\s)/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]*", message), message
    assert "uploads" not in message
    # the reason is still actionable: which types are accepted
    assert "csv" in message.lower(), message
