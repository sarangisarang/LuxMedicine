"""Verified clinician identity (#30).

`actor_id` comes from a signed token's `sub` and from nowhere else. An audit trail whose
author field the client sets is not evidence of anything — it is a log again, which is
what #4 through #29 were spent not building.

**OIDC, not Keycloak.** Everything here speaks the standard: discovery document, JWKS,
RS256. Keycloak is what `docker-compose` happens to run, and the reason it runs it is
data residency — clinician identity is personal data, and this project self-hosts its
embedding model inside the EU rather than post clinical text to a third-party API
(`services/embedding.py`). Sending the *identity* to a US processor while keeping the
question in the EU would be an architectural contradiction nobody had agreed to. That
said, none of this file knows what Keycloak is: swapping it for an EU-hosted managed
provider is `OIDC_ISSUER`, not a rewrite. The choice is meant to stay cheap to reverse.

**What is actually checked, and why each one is load-bearing:**

- *Signature*, against the issuer's published keys. Obviously.
- *Algorithm allowlist.* `jwt.decode` is given `RS256` explicitly and never the header's
  own `alg`. Trusting the header is the classic forgery: `alg: none` verifies anything,
  and `alg: HS256` against an RS256 key lets an attacker sign a token using the *public*
  key as the HMAC secret — a public key being, by construction, public.
- *Audience.* The one people skip. Without it, any token from the same realm is accepted
  — the monitoring dashboard's service account, another app's user, anything the issuer
  ever signed. It is not a check that the token is well-formed; it is the check that the
  token was meant for *us*.
- *Issuer*, exactly. A valid signature from the wrong issuer is a valid signature.
- *`exp` / `nbf`*, by pyjwt.
- *`sub`, non-empty.* It becomes `actor_id`, and an audit row attributed to `""` is an
  audit row attributed to nobody.
- *`clinic_id`, non-empty.* It is the tenant boundary that row-level security enforces
  (#31). A token without one is refused rather than defaulted: a default tenant is a
  tenant every misconfigured token silently joins.

**No bypass flag.** A `AUTH_DISABLED=true` for convenient local dev is how production
ends up unauthenticated — the flag ships, and the failure is silent because everything
works. Tests use `dependency_overrides`; local dev runs `docker compose up keycloak`.
The friction is the point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKSet

from app.core.config import Settings, get_settings

# Never the token header's `alg`. See the module docstring.
ALLOWED_ALGORITHMS = ["RS256"]

# The claim carrying the tenant. A private claim, so it needs a mapper in the realm —
# see keycloak/realm-luxmedicine.json. Named here rather than inline because it is the
# single point where the tenant boundary enters the system.
CLINIC_CLAIM = "clinic_id"


@dataclass(frozen=True)
class Clinician:
    """A verified identity. Constructed only from a validated token."""

    actor_id: str

    # The tenant boundary (#31). Row-level security filters on this value, so a
    # client-settable clinic_id would be a client-settable tenant boundary — every
    # isolation guarantee in the system reduces to this claim being the issuer's word and
    # not the caller's.
    clinic_id: str

    email: str | None = None
    name: str | None = None

    # Realm roles the token carries (Keycloak's realm_access.roles). Authorisation, not
    # identity: which clinics a clinician may act in is clinic_id; what they may DO is here.
    # #51 gates minting invites on "clinic-admin", because an invite opens the tenant boundary
    # and that is an administrative act, not a thing any logged-in clinician should do.
    roles: frozenset[str] = frozenset()

    def has_role(self, role: str) -> bool:
        return role in self.roles


class _JwksCache:
    """The issuer's public keys, cached, with rotation handled.

    Two failure modes this exists to avoid, pulling in opposite directions:

    *Never refetching* means key rotation is a total outage until someone restarts the
    API — the issuer rotates, every token is signed by a key we do not have, and every
    clinician is locked out at once.

    *Always refetching on an unknown `kid`* turns that fix into an amplifier: unauthenticated
    requests carrying random `kid` values each trigger an outbound fetch, and the issuer
    gets hammered by way of us.

    The rate limit is on *refetch attempts*, not on cache age, and the difference is a bug
    the rotation test caught. Keying it to cache age meant an unknown kid arriving shortly
    after any successful fetch was refused without a refetch — so a rotation locked every
    clinician out for the length of the interval. Tracking attempts separately gives both
    properties at once: the first unknown kid after a rotation refetches immediately, and
    a flood of forged kids still costs the issuer at most one request per interval. The
    two goals were never actually in conflict; conflating the two clocks invented the
    conflict.
    """

    def __init__(self, ttl_seconds: int = 600, min_refetch_interval: int = 30) -> None:
        self._keys: PyJWKSet | None = None
        self._fetched_at: float = 0.0
        # Distinct from _fetched_at: see the docstring. Never attempted, not "at time 0".
        self._last_refetch_attempt: float | None = None
        self._ttl = ttl_seconds
        self._min_refetch_interval = min_refetch_interval

    async def _fetch(self, jwks_uri: str) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(jwks_uri)
            response.raise_for_status()
            self._keys = PyJWKSet.from_dict(response.json())
            self._fetched_at = time.monotonic()

    async def signing_key(self, jwks_uri: str, kid: str):
        age = time.monotonic() - self._fetched_at

        if self._keys is None or age > self._ttl:
            await self._fetch(jwks_uri)

        key = self._find(kid)
        if key is not None:
            return key

        # Unknown kid: either a rotation we have not seen, or a forged header. Refetch,
        # but not more often than the interval allows.
        since_attempt = (
            None
            if self._last_refetch_attempt is None
            else time.monotonic() - self._last_refetch_attempt
        )
        if since_attempt is None or since_attempt > self._min_refetch_interval:
            self._last_refetch_attempt = time.monotonic()
            await self._fetch(jwks_uri)
            key = self._find(kid)
            if key is not None:
                return key

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token signing key is not known to the issuer",
        )

    def _find(self, kid: str):
        for key in self._keys.keys if self._keys else []:
            if key.key_id == kid:
                return key
        return None


_jwks = _JwksCache()
_discovery: dict[str, str] = {}


async def jwks_uri_for(settings: Settings) -> str:
    """Resolve the JWKS endpoint from the issuer's discovery document.

    Discovered rather than configured: hardcoding the path is a bet that every provider
    lays out its URLs the way Keycloak does, and that bet is exactly what makes the
    issuer expensive to change later.
    """
    issuer = settings.oidc_issuer
    if issuer in _discovery:
        return _discovery[issuer]

    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
        response.raise_for_status()
        document = response.json()

    if document.get("issuer") != issuer:
        # A discovery document that names a different issuer than the one we asked is
        # either a misconfiguration or a redirect somewhere unintended. Neither is a
        # thing to shrug at when the answer decides which keys we trust.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="issuer mismatch in the OIDC discovery document",
        )

    uri = document["jwks_uri"]
    _discovery[issuer] = uri
    return uri


async def verify_token(token: str, settings: Settings) -> Clinician:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="malformed token"
        ) from None

    kid = header.get("kid")
    if not kid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token has no key id"
        )

    key = await _jwks.signing_key(await jwks_uri_for(settings), kid)

    try:
        claims = jwt.decode(
            token,
            key.key,
            # Not header["alg"] — see the module docstring. This single argument is what
            # stops `alg: none` and RS256/HS256 confusion.
            algorithms=ALLOWED_ALGORITHMS,
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # The reason goes to the caller because it is theirs to fix (expired, wrong
        # audience). It says nothing about keys or internals.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=f"token rejected: {exc}"
        ) from None

    subject = (claims.get("sub") or "").strip()
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token has no subject; an audit row attributed to nobody is not an audit row",
        )

    clinic = (claims.get(CLINIC_CLAIM) or "").strip()
    if not clinic:
        # No fallback, no default tenant. A token the issuer did not place in a clinic is
        # a token whose isolation nobody decided, and picking one here would be this code
        # deciding it. The realm's mapper is what says which clinic a clinician is in.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"token has no {CLINIC_CLAIM} claim: the tenant boundary must come from "
                "the issuer, and there is no default clinic to fall back to"
            ),
        )

    # realm_access.roles, defaulted to empty — a token with no roles is a clinician who can
    # read but not administer, never an error. Keycloak places realm_access in the access
    # token by default, so no mapper is needed for this the way clinic_id needed one.
    realm_access = claims.get("realm_access") or {}
    roles = frozenset(r for r in (realm_access.get("roles") or []) if isinstance(r, str))

    return Clinician(
        actor_id=subject,
        clinic_id=clinic,
        email=claims.get("email"),
        name=claims.get("name"),
        roles=roles,
    )


_bearer = HTTPBearer(auto_error=False)


async def current_clinician(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> Clinician:
    """The only supplier of `actor_id` in this system."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await verify_token(credentials.credentials, settings)


CLINIC_ADMIN_ROLE = "clinic-admin"


async def require_clinic_admin(
    clinician: Clinician = Depends(current_clinician),
) -> Clinician:
    """A verified clinician who also holds `clinic-admin` (#51). 403, not 401: they ARE
    authenticated — they are just not allowed to open the tenant boundary by minting an invite.
    Authorisation on top of authentication, kept a separate dependency so an endpoint asks for
    exactly the level it needs."""
    if not clinician.has_role(CLINIC_ADMIN_ROLE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"this action requires the {CLINIC_ADMIN_ROLE} role",
        )
    return clinician
