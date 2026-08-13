from __future__ import annotations

import hashlib

import pytest

from testpaper_backend.services.attachment_storage import AttachmentStorageError, FilesystemAttachmentStorage


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def test_chunks_are_idempotent_and_cannot_escape_storage_root(tmp_path) -> None:
    storage = FilesystemAttachmentStorage(tmp_path)
    upload_id = "11111111-1111-4111-8111-111111111111"
    key = storage.chunk_key(upload_id, 0)
    content = b"chunk"
    storage.write_chunk(key, content, _digest(content))
    storage.write_chunk(key, content, _digest(content))

    with pytest.raises(AttachmentStorageError, match="differs from replay"):
        storage.write_chunk(key, b"other", _digest(b"other"))
    with pytest.raises(AttachmentStorageError, match="invalid attachment storage key"):
        storage.read_verified("../secret", content_hash=_digest(content), byte_size=len(content))


def test_assemble_verifies_hash_and_never_replaces_a_valid_blob_on_failure(tmp_path) -> None:
    storage = FilesystemAttachmentStorage(tmp_path)
    upload_id = "22222222-2222-4222-8222-222222222222"
    chunks = [b"first", b"second"]
    keys = []
    for ordinal, content in enumerate(chunks):
        key = storage.chunk_key(upload_id, ordinal)
        storage.write_chunk(key, content, _digest(content))
        keys.append(key)

    complete = b"".join(chunks)
    key = storage.assemble(keys, content_hash=_digest(complete), byte_size=len(complete))
    assert storage.read_verified(key, content_hash=_digest(complete), byte_size=len(complete)) == complete

    with pytest.raises(AttachmentStorageError, match="assembled attachment"):
        storage.assemble(keys, content_hash="f" * 64, byte_size=len(complete))
    assert storage.read_verified(key, content_hash=_digest(complete), byte_size=len(complete)) == complete


def test_read_rejects_altered_bytes(tmp_path) -> None:
    storage = FilesystemAttachmentStorage(tmp_path)
    content = b"verified"
    upload_id = "33333333-3333-4333-8333-333333333333"
    chunk_key = storage.chunk_key(upload_id, 0)
    storage.write_chunk(chunk_key, content, _digest(content))
    blob_key = storage.assemble([chunk_key], content_hash=_digest(content), byte_size=len(content))
    storage._path(blob_key).write_bytes(b"tampered")

    with pytest.raises(AttachmentStorageError, match="hash or size mismatch"):
        storage.read_verified(blob_key, content_hash=_digest(content), byte_size=len(content))


def test_delete_is_idempotent_and_never_removes_sibling_blobs(tmp_path) -> None:
    storage = FilesystemAttachmentStorage(tmp_path)
    contents = [b"first", b"second"]
    keys = []
    for ordinal, content in enumerate(contents):
        upload_id = f"44444444-4444-4444-8444-44444444444{ordinal}"
        chunk = storage.chunk_key(upload_id, 0)
        storage.write_chunk(chunk, content, _digest(content))
        keys.append(storage.assemble([chunk], content_hash=_digest(content), byte_size=len(content)))

    assert storage.delete(keys[0]) is True
    assert storage.delete(keys[0]) is False
    assert storage.read_verified(keys[1], content_hash=_digest(contents[1]), byte_size=len(contents[1])) == contents[1]
