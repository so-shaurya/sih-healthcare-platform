from enum import StrEnum
from fastapi import Depends, Header, HTTPException, status


class Role(StrEnum):
    ASHA = "ASHA"
    DOCTOR = "DOCTOR"
    ADMIN = "ADMIN"


def current_role(
    x_user_role: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Role:
    """Development-only auth seam; replace with Supabase JWT verification."""
    raw = x_user_role or (authorization.removeprefix("Bearer demo-") if authorization else None)
    try:
        return Role((raw or "").upper())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required") from exc


def require(*roles: Role):
    def check(role: Role = Depends(current_role)) -> Role:
        if role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return role
    return check
