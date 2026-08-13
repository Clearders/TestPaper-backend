from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Body, Header, Query, Request, Response, status

from testpaper_backend.api.dependencies import CurrentSyncDeviceDep, CurrentUserDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import (
    AttachmentChunkReceipt,
    AttachmentUploadCompleteRequest,
    AttachmentUploadInitiateRequest,
    AttachmentUploadStatus,
    Envelope,
    SyncAckRequest,
    SyncAckResponse,
    SyncConflictRecord,
    SyncConflictResolutionRecord,
    SyncConflictResolutionRequest,
    SyncEntityType,
    SyncEntityVersionRecord,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncSnapshotResponse,
    SyncVersionRestoreRecord,
    SyncVersionRestoreRequest,
)
from testpaper_backend.services.attachment_transfers import (
    complete_attachment_upload,
    download_attachment,
    get_attachment_upload_status,
    initiate_attachment_upload,
    upload_attachment_chunk,
)
from testpaper_backend.services.sync_conflicts import (
    get_conflict,
    list_conflict_resolutions,
    list_versions,
    resolve_conflict,
    restore_version,
)
from testpaper_backend.services.sync_push import push_mutations
from testpaper_backend.services.sync_read import acknowledge_cursor, pull_changes, snapshot_entities

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.get("/conflicts/{conflict_id}", response_model=Envelope[SyncConflictRecord])
def get_sync_conflict(request: Request, conflict_id: str, current_user: CurrentUserDep):
    return envelope(get_conflict(conflict_id, user=current_user), request)


@router.get(
    "/conflicts/{conflict_id}/resolutions",
    response_model=Envelope[list[SyncConflictResolutionRecord]],
)
def list_sync_conflict_resolutions(request: Request, conflict_id: str, current_user: CurrentUserDep):
    return envelope(list_conflict_resolutions(conflict_id, user=current_user), request)


@router.post("/conflicts/{conflict_id}/resolve", response_model=Envelope[SyncConflictResolutionRecord])
def resolve_sync_conflict(
    request: Request,
    conflict_id: str,
    payload: SyncConflictResolutionRequest,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
):
    return envelope(resolve_conflict(conflict_id, payload, user=current_user, device_id=device_id), request)


@router.get(
    "/entities/{entity_type}/{entity_id}/versions",
    response_model=Envelope[list[SyncEntityVersionRecord]],
)
def list_sync_entity_versions(
    request: Request,
    entity_type: SyncEntityType,
    entity_id: str,
    current_user: CurrentUserDep,
):
    return envelope(list_versions(entity_type, entity_id, user=current_user), request)


@router.post(
    "/entities/{entity_type}/{entity_id}/versions/{version}/restore",
    response_model=Envelope[SyncVersionRestoreRecord],
)
def restore_sync_entity_version(
    request: Request,
    entity_type: SyncEntityType,
    entity_id: str,
    version: int,
    payload: SyncVersionRestoreRequest,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
):
    result = restore_version(
        entity_type,
        entity_id,
        version,
        payload,
        user=current_user,
        device_id=device_id,
    )
    return envelope(result, request)


@router.post(
    "/attachments/uploads",
    response_model=Envelope[AttachmentUploadStatus],
    status_code=status.HTTP_201_CREATED,
)
def initiate_sync_attachment_upload(
    request: Request,
    payload: AttachmentUploadInitiateRequest,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
):
    result = initiate_attachment_upload(payload, user=current_user, device_id=device_id)
    return envelope(result, request)


@router.get("/attachments/uploads/{upload_id}", response_model=Envelope[AttachmentUploadStatus])
def get_sync_attachment_upload(
    request: Request,
    upload_id: str,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
):
    result = get_attachment_upload_status(upload_id=upload_id, user=current_user, device_id=device_id)
    return envelope(result, request)


@router.put("/attachments/uploads/{upload_id}/chunks/{ordinal}", response_model=Envelope[AttachmentChunkReceipt])
def put_sync_attachment_chunk(
    request: Request,
    upload_id: str,
    ordinal: int,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
    chunk_sha256: str = Header(alias="X-Chunk-SHA256"),
    data: bytes = Body(media_type="application/octet-stream"),
):
    result = upload_attachment_chunk(
        upload_id=upload_id,
        ordinal=ordinal,
        data=data,
        content_hash=chunk_sha256,
        user=current_user,
        device_id=device_id,
    )
    return envelope(result, request)


@router.post("/attachments/uploads/{upload_id}/complete", response_model=Envelope[AttachmentUploadStatus])
def complete_sync_attachment_upload(
    request: Request,
    upload_id: str,
    payload: AttachmentUploadCompleteRequest,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
):
    result = complete_attachment_upload(
        upload_id=upload_id,
        protocol_version=payload.protocolVersion,
        user=current_user,
        device_id=device_id,
    )
    return envelope(result, request)


@router.get(
    "/attachments/{attachment_id}/content",
    response_class=Response,
    responses={
        200: {
            "description": "Verified attachment bytes.",
            "headers": {
                "Content-Disposition": {"schema": {"type": "string"}},
                "ETag": {"schema": {"type": "string"}},
                "X-Content-SHA256": {"schema": {"type": "string"}},
            },
            "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def download_sync_attachment(
    attachment_id: str,
    current_user: CurrentUserDep,
    _device_id: CurrentSyncDeviceDep,
) -> Response:
    download = download_attachment(attachment_id=attachment_id, user=current_user)
    ascii_name = download.file_name.encode("ascii", "ignore").decode() or "attachment"
    ascii_name = ascii_name.replace('"', "'")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(download.file_name)}"
    return Response(
        content=download.content,
        media_type=download.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": disposition,
            "ETag": f'"{download.content_hash}"',
            "X-Content-SHA256": download.content_hash,
        },
    )


@router.get("/pull", response_model=Envelope[SyncPullResponse])
def pull_sync_changes(
    request: Request,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=100, alias="pageSize", ge=1, le=500),
):
    return envelope(
        pull_changes(user=current_user, device_id=device_id, cursor=cursor, page_size=page_size),
        request,
    )


@router.post("/ack", response_model=Envelope[SyncAckResponse])
def ack_sync_cursor(
    request: Request,
    payload: SyncAckRequest,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
):
    return envelope(acknowledge_cursor(payload, user=current_user, device_id=device_id), request)


@router.get("/snapshot", response_model=Envelope[SyncSnapshotResponse])
def get_sync_snapshot(
    request: Request,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    cursor: str | None = Query(default=None),
    page_size: int = Query(default=100, alias="pageSize", ge=1, le=500),
):
    return envelope(
        snapshot_entities(user=current_user, device_id=device_id, cursor=cursor, page_size=page_size),
        request,
    )


@router.post("/push", response_model=Envelope[SyncPushResponse])
def push_sync_mutations(
    request: Request,
    payload: SyncPushRequest,
    current_user: CurrentUserDep,
    device_id: CurrentSyncDeviceDep,
    _: RateLimitWriteDep,
):
    response = push_mutations(
        payload,
        user=current_user,
        authenticated_device_id=device_id,
        request_id=request.state.request_id,
    )
    return envelope(response, request)
