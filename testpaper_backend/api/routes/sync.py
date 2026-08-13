from __future__ import annotations

from fastapi import APIRouter, Request

from testpaper_backend.api.dependencies import CurrentSyncDeviceDep, CurrentUserDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import Envelope, SyncPushRequest, SyncPushResponse
from testpaper_backend.services.sync_push import push_mutations

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


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
