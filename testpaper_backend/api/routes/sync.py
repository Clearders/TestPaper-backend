from __future__ import annotations

from fastapi import APIRouter, Query, Request

from testpaper_backend.api.dependencies import CurrentSyncDeviceDep, CurrentUserDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import (
    Envelope,
    SyncAckRequest,
    SyncAckResponse,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncSnapshotResponse,
)
from testpaper_backend.services.sync_push import push_mutations
from testpaper_backend.services.sync_read import acknowledge_cursor, pull_changes, snapshot_entities

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


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
