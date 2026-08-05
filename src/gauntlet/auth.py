"""Clerk session verification.

**Auth is off when `CLERK_SECRET_KEY` is unset**, and that is a deliberate mode
rather than a fallback. It keeps local development and the public demo runnable
without a Clerk account, and it makes "is this protected?" one explicit condition
in one file instead of a property of however many route decorators.

**What is protected, and why only that.** Reads are public: the universe, the
demo mandates, and completed runs are the demo — putting a sign-up wall in front
of them would mean nobody could see the system without creating an account.
Starting a run is not, because a run spends real money on Anthropic API calls.
The line is drawn at cost, not at sensitivity.

Verification is local arithmetic when `CLERK_JWT_KEY` is set. Without it the SDK
fetches JWKS on first use and caches per key, so neither path round-trips to
Clerk on every request.
"""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from gauntlet.config import settings

logger = logging.getLogger(__name__)


class AuthenticatedUser:
    """The verified caller. `user_id` is Clerk's `sub` claim."""

    def __init__(self, user_id: str | None) -> None:
        self.user_id = user_id

    @property
    def is_anonymous(self) -> bool:
        return self.user_id is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"AuthenticatedUser({self.user_id or 'anonymous'})"


ANONYMOUS = AuthenticatedUser(None)


def _authenticate(request: Request) -> AuthenticatedUser:
    from clerk_backend_api import Clerk
    from clerk_backend_api.security.types import AuthenticateRequestOptions

    # A FastAPI Request already satisfies Clerk's `Requestish` protocol, which
    # asks only for `.headers` — no adapter needed.
    with Clerk(bearer_auth=settings.clerk_secret_key) as clerk:
        state = clerk.authenticate_request(
            request,
            AuthenticateRequestOptions(
                secret_key=settings.clerk_secret_key,
                jwt_key=settings.clerk_jwt_key,
                # Pinning the origins that may present a token blocks a token
                # minted for another site being replayed against this API.
                authorized_parties=settings.cors_origins,
            ),
        )

    if not state.is_signed_in:
        # `reason` is logged but not returned: it distinguishes expired from
        # malformed from wrong-audience, which is useful to an operator and a
        # probing aid to anyone else.
        logger.info("Rejected request to %s: %s", request.url.path, state.reason)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to start a run.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = state.payload or {}
    return AuthenticatedUser(payload.get("sub"))


def require_user(request: Request) -> AuthenticatedUser:
    """Dependency for routes that must not run for an anonymous caller.

    Returns the anonymous user unchanged when auth is disabled, so the demo and
    local development behave identically to a signed-in session.
    """
    if not settings.auth_enabled:
        return ANONYMOUS
    return _authenticate(request)


def optional_user(request: Request) -> AuthenticatedUser:
    """Identify the caller if they are signed in, without requiring it.

    For read routes that stay public but should still attribute a request when a
    session happens to be present.
    """
    if not settings.auth_enabled:
        return ANONYMOUS
    try:
        return _authenticate(request)
    except HTTPException:
        return ANONYMOUS


CurrentUser = Annotated[AuthenticatedUser, Depends(require_user)]
OptionalUser = Annotated[AuthenticatedUser, Depends(optional_user)]
