from __future__ import annotations

import argparse
import getpass
import os

from sqlalchemy import delete, select

from testpaper_backend.db import AuthTokenRow, SessionLocal, UserRow
from testpaper_backend.schemas import UserCreate, UserRole
from testpaper_backend.security import password_hash
from testpaper_backend.time_utils import now_utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or reset the initial TestPapers administrator.")
    parser.add_argument("--username", default=os.getenv("TESTPAPER_ADMIN_USERNAME", "admin"))
    parser.add_argument("--display-name", default=os.getenv("TESTPAPER_ADMIN_DISPLAY_NAME", "System Admin"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    password = os.getenv("TESTPAPER_ADMIN_PASSWORD") or getpass.getpass("Administrator password: ")
    payload = UserCreate(
        username=args.username,
        displayName=args.display_name,
        password=password,
        role=UserRole.admin,
        isActive=True,
    )

    now = now_utc()
    with SessionLocal() as session:
        user = session.scalars(select(UserRow).where(UserRow.username == payload.username)).first()
        if user is None:
            user = UserRow(
                username=payload.username,
                display_name=payload.displayName,
                password_hash=password_hash(payload.password),
                role=UserRole.admin.value,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(user)
        else:
            user.display_name = payload.displayName
            user.password_hash = password_hash(payload.password)
            user.role = UserRole.admin.value
            user.is_active = True
            user.updated_at = now
        session.flush()
        session.execute(delete(AuthTokenRow).where(AuthTokenRow.user_id == user.id))
        session.commit()
        session.refresh(user)

    print(f"Administrator ready: {user.username} ({user.public_id})")


if __name__ == "__main__":
    main()
