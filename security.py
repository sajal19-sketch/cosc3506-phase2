"""Phase 4 identity layer: password hashing, JWTs, BOLA/RBAC checks, rate limiting."""

import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Optional

import bcrypt
import jwt
from fastapi import HTTPException, Request, status

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin"
ADMIN_ROLE = "admin"
STUDENT_ROLE = "student"

JWT_ALGORITHM = "HS256"
# Set JWT_SECRET in the deployment environment; a random per-process key is the fallback.
JWT_SECRET = os.environ.get("JWT_SECRET") or secrets.token_urlsafe(48)
TOKEN_TTL_MINUTES = int(os.environ.get("TOKEN_TTL_MINUTES", "60"))


# --- Passwords ---------------------------------------------------------------
def hash_password(password: str) -> str:
    """bcrypt hash with a fresh random salt. bcrypt only reads the first 72 bytes."""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


# --- Users (in memory; only hashes are stored) -------------------------------
users_db: Dict[str, Dict[str, str]] = {}


def seed_admin() -> None:
    if ADMIN_USERNAME not in users_db:
        users_db[ADMIN_USERNAME] = {
            "password_hash": hash_password(ADMIN_PASSWORD),
            "role": ADMIN_ROLE,
        }


def register_user(username: str, password: str) -> bool:
    """Create a student account. Returns False when the username is taken."""
    if username in users_db:
        return False
    users_db[username] = {"password_hash": hash_password(password), "role": STUDENT_ROLE}
    return True


def authenticate(username: str, password: str) -> Optional[Dict[str, str]]:
    user = users_db.get(username)
    if user and verify_password(password, user["password_hash"]):
        return user
    return None


# --- JWT ---------------------------------------------------------------------
def create_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_TTL_MINUTES),
    }
    return jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def decode_token(token: str) -> Dict:
    try:
        claims = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "role", "exp"]},
        )
    except jwt.PyJWTError:
        raise _unauthorized("Invalid or expired token")
    return claims


def current_claims(request: Request) -> Dict:
    """FastAPI dependency: a valid token is required, otherwise 401."""
    token = bearer_token(request)
    if token is None:
        raise _unauthorized("Not authenticated")
    return decode_token(token)


def optional_claims(request: Request) -> Optional[Dict]:
    token = bearer_token(request)
    if token is None:
        return None
    try:
        return decode_token(token)
    except HTTPException:
        return None


def require_owner(claims: Dict, student_id: str) -> None:
    """BOLA: the token subject must be the student in the URL."""
    if claims.get("sub") != student_id:
        raise _unauthorized("Token does not grant access to this student")


def require_owner_or_admin(claims: Dict, student_id: str) -> None:
    """RBAC: the owning student, or any admin."""
    if claims.get("role") == ADMIN_ROLE:
        return
    require_owner(claims, student_id)


# --- Rate limiting -----------------------------------------------------------
class SlidingWindowLimiter:
    """Allow `limit` requests per key in any rolling `window` seconds."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def retry_after(self, key: str) -> int:
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, int(self.window - (self.clock() - hits[0])) + 1)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_ip(request: Request) -> str:
    # Render sits behind a proxy; the first X-Forwarded-For hop is the real client.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_key(request: Request) -> str:
    claims = optional_claims(request)
    if claims:
        return f"sub:{claims['sub']}"
    return f"ip:{client_ip(request)}"
