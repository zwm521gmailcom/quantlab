"""Artifact registration constrained to approved local roots."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from quantlab.config import Settings
from quantlab.domain.entities import Artifact
from quantlab.repositories.database import Database


_HASH_CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


class ArtifactRepository:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def register(
        self,
        *,
        run_id: str,
        path: Path | str,
        display_name: str,
        artifact_role: str,
    ) -> Artifact:
        resolved = self.settings.require_artifact_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"artifact file not found: {resolved}")
        artifact = Artifact(
            artifact_id=f"artifact-{uuid.uuid4().hex}",
            run_id=run_id,
            display_name=display_name,
            original_name=resolved.name,
            artifact_role=artifact_role,
            path=str(resolved),
            content_hash=_sha256_file(resolved),
            size_bytes=resolved.stat().st_size,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO artifacts(artifact_id, run_id, display_name, original_name, artifact_role, path, content_hash, size_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact.artifact_id,
                    artifact.run_id,
                    artifact.display_name,
                    artifact.original_name,
                    artifact.artifact_role,
                    artifact.path,
                    artifact.content_hash,
                    artifact.size_bytes,
                ),
            )
        return artifact

    def get(self, artifact_id: str) -> dict[str, object] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT artifact_id, run_id, display_name, original_name, artifact_role, size_bytes, content_hash, created_at "
                "FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_download_path(self, artifact_id: str) -> Path | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT path FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
        if row is None:
            return None
        path = self.settings.require_artifact_path(row["path"])
        return path if path.is_file() else None
