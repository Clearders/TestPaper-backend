from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from testpaper_backend.schemas import UserEntity
from testpaper_backend.security import get_current_user, require_permission

CurrentUserDep = Annotated[UserEntity, Depends(get_current_user)]
QuestionsReadDep = Annotated[UserEntity, Depends(require_permission("questions:read"))]
QuestionsWriteDep = Annotated[UserEntity, Depends(require_permission("questions:write"))]
QuestionsDeleteDep = Annotated[UserEntity, Depends(require_permission("questions:delete"))]
PapersReadDep = Annotated[UserEntity, Depends(require_permission("papers:read"))]
PapersWriteDep = Annotated[UserEntity, Depends(require_permission("papers:write"))]
UsersManageDep = Annotated[UserEntity, Depends(require_permission("users:manage"))]

