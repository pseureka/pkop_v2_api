"""Create or update a user for login.

Usage:
    python scripts/create_user.py --username alice --password secret123 --role Admin
    python scripts/create_user.py --username bob --password secret123 --role Reader

Run from the api/ directory so database.py and models.py import cleanly.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Make sibling modules importable when running this as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from database import AsyncSessionLocal
from models import User
from auth.security import hash_password


VALID_ROLES = ("Admin", "Reader")
MIN_PASSWORD_LEN = 8


async def upsert_user(username: str, password: str, role: str) -> None:
    if role not in VALID_ROLES:
        raise SystemExit(f"role must be one of {VALID_ROLES}, got {role!r}")
    if len(password) < MIN_PASSWORD_LEN:
        raise SystemExit(f"password must be at least {MIN_PASSWORD_LEN} characters")

    username = username.strip().lower()
    if not username:
        raise SystemExit("username is required")

    password_hash = hash_password(password)

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if existing is None:
            existing = User(username=username, password_hash=password_hash, role=role, is_active=True)
            db.add(existing)
            action = "created"
        else:
            existing.password_hash = password_hash
            existing.role = role
            existing.is_active = True
            action = "updated"
        await db.commit()
        print(f"{action} user {username!r} (role={role})")


def main() -> None:
    p = argparse.ArgumentParser(description="Create or update a user.")
    p.add_argument("--username", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--role", required=True, choices=VALID_ROLES)
    args = p.parse_args()
    asyncio.run(upsert_user(args.username, args.password, args.role))


if __name__ == "__main__":
    main()
