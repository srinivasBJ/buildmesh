from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from pypdf import PdfReader


class IngestionError(ValueError):
    pass


@dataclass(frozen=True)
class StoredAsset:
    id: str
    relative_path: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str


class LocalAssetStore:
    """Project-scoped local evidence storage with safe filenames and content hashes."""

    image_types = {"image/jpeg", "image/png", "image/webp"}
    document_types = {"application/pdf", "text/plain"}

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, project_id: str, stream: BinaryIO, filename: str, media_type: str, category: str) -> StoredAsset:
        allowed = self.image_types if category == "images" else self.document_types
        if media_type not in allowed:
            raise IngestionError(f"unsupported {category} media type: {media_type}")
        suffix = Path(filename).suffix.lower()
        if not suffix:
            raise IngestionError("an uploaded filename needs an extension")
        asset_id = f"asset_{uuid4().hex}"
        directory = self.root / project_id / category
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{asset_id}{suffix}"
        with destination.open("wb") as target:
            shutil.copyfileobj(stream, target)
        size = destination.stat().st_size
        if size == 0:
            destination.unlink(missing_ok=True)
            raise IngestionError("uploaded file is empty")
        if size > 30 * 1024 * 1024:
            destination.unlink(missing_ok=True)
            raise IngestionError("uploaded file exceeds the 30 MB MVP limit")
        try:
            self._validate_content(destination, media_type)
        except IngestionError:
            destination.unlink(missing_ok=True)
            raise
        return StoredAsset(id=asset_id, relative_path=str(destination.relative_to(self.root)), filename=Path(filename).name, media_type=media_type, size_bytes=size, sha256=self._hash(destination))

    def path_for(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents and path != self.root:
            raise IngestionError("asset path escapes the local store")
        if not path.is_file():
            raise IngestionError("asset file no longer exists")
        return path

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _validate_content(path: Path, media_type: str) -> None:
        header = path.read_bytes()[:16]
        signatures = {
            "image/jpeg": (b"\xff\xd8\xff",),
            "image/png": (b"\x89PNG\r\n\x1a\n",),
            "image/webp": (b"RIFF",),
            "application/pdf": (b"%PDF-",),
        }
        expected = signatures.get(media_type)
        if expected and not any(header.startswith(signature) for signature in expected):
            raise IngestionError("uploaded file content does not match its declared media type")
        if media_type == "image/webp" and header[8:12] != b"WEBP":
            raise IngestionError("uploaded file content does not match its declared media type")


class DocumentExtractor:
    max_chars = 50_000

    def extract(self, path: Path, media_type: str) -> str:
        if media_type == "text/plain":
            return path.read_text(encoding="utf-8", errors="replace")[: self.max_chars]
        if media_type == "application/pdf":
            try:
                text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
            except Exception as exc:
                raise IngestionError("unable to extract text from PDF") from exc
            return text[: self.max_chars]
        raise IngestionError("unsupported document type")
