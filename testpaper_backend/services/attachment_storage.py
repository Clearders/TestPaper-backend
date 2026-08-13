from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from testpaper_backend.config import get_data_dir

_STORAGE_KEY = re.compile(r"^(?:blobs/[0-9a-f]{2}/[0-9a-f]{64}|uploads/[0-9a-f-]{36}/[0-9]+\.part)$")


class AttachmentStorageError(RuntimeError):
    pass


class FilesystemAttachmentStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or (get_data_dir() / "sync-attachments")).resolve()

    @staticmethod
    def blob_key(content_hash: str) -> str:
        return f"blobs/{content_hash[:2]}/{content_hash}"

    @staticmethod
    def chunk_key(upload_id: str, ordinal: int) -> str:
        return f"uploads/{upload_id}/{ordinal}.part"

    def _path(self, key: str) -> Path:
        if not _STORAGE_KEY.fullmatch(key):
            raise AttachmentStorageError("invalid attachment storage key")
        path = (self.root / Path(*key.split("/"))).resolve()
        if self.root not in path.parents:
            raise AttachmentStorageError("attachment storage path escaped its root")
        return path

    def write_chunk(self, key: str, data: bytes, expected_hash: str) -> None:
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise AttachmentStorageError("chunk hash mismatch")
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() == data:
                return
            raise AttachmentStorageError("stored chunk differs from replay")
        handle, temporary_name = tempfile.mkstemp(prefix=".chunk-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def assemble(self, chunk_keys: list[str], *, content_hash: str, byte_size: int) -> str:
        final_key = self.blob_key(content_hash)
        final_path = self._path(final_key)
        if final_path.exists() and self.verify(final_key, content_hash=content_hash, byte_size=byte_size):
            return final_key
        final_path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".blob-", dir=final_path.parent)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(handle, "wb") as output:
                for key in chunk_keys:
                    with self._path(key).open("rb") as chunk:
                        while block := chunk.read(1024 * 1024):
                            output.write(block)
                            digest.update(block)
                            total += len(block)
                output.flush()
                os.fsync(output.fileno())
            if total != byte_size or digest.hexdigest() != content_hash:
                raise AttachmentStorageError("assembled attachment hash or size mismatch")
            os.replace(temporary, final_path)
            return final_key
        finally:
            temporary.unlink(missing_ok=True)

    def verify(self, key: str, *, content_hash: str, byte_size: int) -> bool:
        path = self._path(key)
        if not path.is_file() or path.stat().st_size != byte_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest() == content_hash

    def read_verified(self, key: str, *, content_hash: str, byte_size: int) -> bytes:
        if not self.verify(key, content_hash=content_hash, byte_size=byte_size):
            raise AttachmentStorageError("attachment hash or size mismatch")
        return self._path(key).read_bytes()
