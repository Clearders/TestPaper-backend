from __future__ import annotations

from testpaper_backend.schemas import UserEntity
from testpaper_backend.security import has_permission


def can_manage_owned_resource(owner_id: int | None, current_user: UserEntity) -> bool:
    return owner_id in (None, current_user.id) or has_permission(current_user, "users:manage")
