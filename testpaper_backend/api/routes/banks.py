from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request, Response, status

from testpaper_backend.api.dependencies import CurrentUserDep, RateLimitWriteDep
from testpaper_backend.core.responses import envelope
from testpaper_backend.schemas import (
    BankCreate,
    BankForkRequest,
    BankItemAdd,
    BankListScope,
    BankMemberCreate,
    BankMemberUpdate,
    BankPublicationEntity,
    BankSubscriptionEntity,
    BankSubscriptionUpdate,
    BankUpdate,
    BankVersionSummary,
    BankVisibility,
    Envelope,
    PublicBankDetail,
    PublicBankSummary,
    QuestionBankEntity,
    QuestionBankSummary,
    QuestionEntity,
)
from testpaper_backend.services.banks import (
    add_bank_items,
    add_bank_member,
    create_bank,
    delete_bank,
    fork_bank,
    get_bank_detail,
    get_bank_version,
    get_public_bank,
    list_bank_questions,
    list_bank_versions,
    list_public_banks,
    list_visible_banks,
    publish_bank,
    remove_bank_item,
    remove_bank_member,
    subscribe_bank,
    unsubscribe_bank,
    update_bank,
    update_bank_member_role,
    update_subscription,
    withdraw_bank,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/banks", tags=["banks"])
public_router = APIRouter(prefix="/api/v1/public/banks", tags=["public-banks"])


@public_router.get("", response_model=Envelope[list[PublicBankSummary]])
def list_public_bank_snapshots(request: Request, q: str | None = Query(default=None, max_length=120)):
    banks = list_public_banks(q=q)
    return envelope([bank.model_dump(mode="json") for bank in banks], request)


@public_router.get("/{bank_public_id}", response_model=Envelope[PublicBankDetail])
def get_public_bank_snapshot(request: Request, bank_public_id: str):
    detail = get_public_bank(bank_public_id)
    return envelope(detail.model_dump(mode="json"), request)


@router.get("", response_model=Envelope[list[QuestionBankSummary]])
def list_banks(
    request: Request,
    current_user: CurrentUserDep,
    q: str | None = Query(default=None, max_length=120),
    visibility: BankVisibility | None = None,
    scope: BankListScope = BankListScope.visible,
):
    banks = list_visible_banks(current_user, q=q, visibility=visibility, scope=scope)
    return envelope([bank.model_dump(mode="json") for bank in banks], request)


@router.post("", response_model=Envelope[QuestionBankEntity], status_code=status.HTTP_201_CREATED)
def create_bank_route(request: Request, payload: BankCreate, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = create_bank(payload, current_user)
    logger.info("Question bank created: %s", detail.publicId)
    return envelope(detail.model_dump(mode="json"), request)


@router.get("/{bank_public_id}", response_model=Envelope[QuestionBankEntity])
def get_bank(request: Request, bank_public_id: str, current_user: CurrentUserDep):
    detail = get_bank_detail(bank_public_id, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.patch("/{bank_public_id}", response_model=Envelope[QuestionBankEntity])
def patch_bank(request: Request, bank_public_id: str, payload: BankUpdate, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = update_bank(bank_public_id, payload, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.delete("/{bank_public_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bank_route(bank_public_id: str, current_user: CurrentUserDep, _: RateLimitWriteDep):
    delete_bank(bank_public_id, current_user)
    logger.info("Question bank deleted: %s", bank_public_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{bank_public_id}/questions", response_model=Envelope[list[QuestionEntity]])
def get_bank_questions(request: Request, bank_public_id: str, current_user: CurrentUserDep):
    questions = list_bank_questions(bank_public_id, current_user)
    return envelope([question.model_dump(mode="json") for question in questions], request)


@router.post("/{bank_public_id}/items", response_model=Envelope[QuestionBankEntity])
def add_items(request: Request, bank_public_id: str, payload: BankItemAdd, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = add_bank_items(bank_public_id, payload, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.delete("/{bank_public_id}/items/{question_public_id}", response_model=Envelope[QuestionBankEntity])
def remove_item(
    request: Request,
    bank_public_id: str,
    question_public_id: str,
    current_user: CurrentUserDep,
    _: RateLimitWriteDep,
):
    detail = remove_bank_item(bank_public_id, question_public_id, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.post("/{bank_public_id}/members", response_model=Envelope[QuestionBankEntity])
def create_member(request: Request, bank_public_id: str, payload: BankMemberCreate, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = add_bank_member(bank_public_id, payload, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.patch("/{bank_public_id}/members/{user_public_id}", response_model=Envelope[QuestionBankEntity])
def patch_member(
    request: Request,
    bank_public_id: str,
    user_public_id: str,
    payload: BankMemberUpdate,
    current_user: CurrentUserDep,
    _: RateLimitWriteDep,
):
    detail = update_bank_member_role(bank_public_id, user_public_id, payload, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.delete("/{bank_public_id}/members/{user_public_id}", response_model=Envelope[QuestionBankEntity])
def remove_member(
    request: Request,
    bank_public_id: str,
    user_public_id: str,
    current_user: CurrentUserDep,
    _: RateLimitWriteDep,
):
    detail = remove_bank_member(bank_public_id, user_public_id, current_user)
    return envelope(detail.model_dump(mode="json"), request)


@router.post("/{bank_public_id}/publish", response_model=Envelope[QuestionBankEntity])
def publish(request: Request, bank_public_id: str, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = publish_bank(bank_public_id, current_user)
    logger.info("Question bank published: %s", bank_public_id)
    return envelope(detail.model_dump(mode="json"), request)


@router.post("/{bank_public_id}/withdraw", response_model=Envelope[QuestionBankEntity])
def withdraw(request: Request, bank_public_id: str, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = withdraw_bank(bank_public_id, current_user)
    logger.info("Question bank withdrawn: %s", bank_public_id)
    return envelope(detail.model_dump(mode="json"), request)


@router.get("/{bank_public_id}/versions", response_model=Envelope[list[BankVersionSummary]])
def versions(request: Request, bank_public_id: str, current_user: CurrentUserDep):
    items = list_bank_versions(bank_public_id, current_user)
    return envelope([item.model_dump(mode="json") for item in items], request)


@router.get("/{bank_public_id}/versions/{version}", response_model=Envelope[BankPublicationEntity])
def get_version(request: Request, bank_public_id: str, version: int, current_user: CurrentUserDep):
    item = get_bank_version(bank_public_id, version, current_user)
    return envelope(item.model_dump(mode="json"), request)


@router.post("/{bank_public_id}/fork", response_model=Envelope[QuestionBankEntity], status_code=status.HTTP_201_CREATED)
def fork(request: Request, bank_public_id: str, payload: BankForkRequest, current_user: CurrentUserDep, _: RateLimitWriteDep):
    detail = fork_bank(bank_public_id, payload, current_user)
    logger.info("Question bank forked: %s -> %s", bank_public_id, detail.publicId)
    return envelope(detail.model_dump(mode="json"), request)


@router.post("/{bank_public_id}/subscribe", response_model=Envelope[BankSubscriptionEntity])
def subscribe(request: Request, bank_public_id: str, current_user: CurrentUserDep, _: RateLimitWriteDep):
    item = subscribe_bank(bank_public_id, current_user)
    return envelope(item.model_dump(mode="json"), request)


@router.patch("/{bank_public_id}/subscribe", response_model=Envelope[BankSubscriptionEntity])
def patch_subscription(
    request: Request,
    bank_public_id: str,
    payload: BankSubscriptionUpdate,
    current_user: CurrentUserDep,
    _: RateLimitWriteDep,
):
    item = update_subscription(bank_public_id, payload, current_user)
    return envelope(item.model_dump(mode="json"), request)


@router.delete("/{bank_public_id}/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(bank_public_id: str, current_user: CurrentUserDep, _: RateLimitWriteDep):
    unsubscribe_bank(bank_public_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
