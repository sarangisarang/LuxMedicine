"""Identity (#30).

Everything here is about forgery. The mechanism is a signature check and forty lines; its
worth is entirely in what it refuses, so that is what these test — each one is an attack
that works against a plausible implementation of the same feature.

The network is faked (MockTransport) and nothing else is. Discovery parsing, JWKS
parsing, caching, rotation, and every claim check run for real; only the socket is
replaced, and the socket is not what is under test.
"""

import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core import auth as auth_module
from app.core.auth import Clinician, current_clinician, verify_token
from app.core.config import Settings

ISSUER = "https://idp.example.invalid/realms/luxmedicine"
AUDIENCE = "luxmedicine-api"


def make_key(kid: str) -> tuple[rsa.RSAPrivateKey, dict]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private.public_key().public_numbers()

    def b64(value: int) -> str:
        import base64

        length = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()

    return private, {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": b64(public_numbers.n),
        "e": b64(public_numbers.e),
    }


@pytest.fixture
def settings() -> Settings:
    return Settings(oidc_issuer=ISSUER, oidc_audience=AUDIENCE, environment="local")


@pytest.fixture
def idp(monkeypatch):
    """A fake issuer: one signing key, a discovery document, and a JWKS endpoint.

    Returns a handle that can mint tokens, rotate keys, and count how often its JWKS was
    fetched — the last one matters because refetch behaviour is itself a vulnerability
    (see the rotation tests).
    """

    class Idp:
        def __init__(self) -> None:
            self.private, self.jwk = make_key("key-1")
            self.keys = [self.jwk]
            self.jwks_fetches = 0
            self.discovery_issuer = ISSUER

        def mint(self, *, kid: str = "key-1", key=None, **overrides) -> str:
            claims = {
                "sub": "dr-ada-smith",
                "iss": ISSUER,
                "aud": AUDIENCE,
                "exp": int(time.time()) + 300,
                "iat": int(time.time()),
                "email": "dr.smith@example.invalid",
                "name": "Ada Smith",
            }
            claims.update(overrides)
            claims = {k: v for k, v in claims.items() if v is not None}
            return jwt.encode(claims, key or self.private, algorithm="RS256", headers={"kid": kid})

        def rotate(self) -> None:
            self.private, self.jwk = make_key("key-2")
            self.keys = [self.jwk]

        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("openid-configuration"):
                return httpx.Response(
                    200,
                    json={
                        "issuer": self.discovery_issuer,
                        "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                    },
                )
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": self.keys})

    server = Idp()

    # Bound before patching. `auth_module.httpx` *is* the httpx module — the same object,
    # not a copy — so patching through it patches httpx globally, and a factory that then
    # calls httpx.AsyncClient calls itself. It recursed 14 tests deep before saying so.
    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(server.handle))

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", client_factory)
    # Both caches are module state; a test that inherits another's keys proves nothing.
    monkeypatch.setattr(auth_module, "_jwks", auth_module._JwksCache())
    monkeypatch.setattr(auth_module, "_discovery", {})
    return server


# --- the control this issue exists for ---------------------------------------------


async def test_a_valid_token_yields_its_subject_as_the_actor(idp, settings):
    clinician = await verify_token(idp.mint(), settings)

    assert clinician.actor_id == "dr-ada-smith"
    assert clinician.email == "dr.smith@example.invalid"


async def test_the_request_body_cannot_name_the_actor(engine, idp, settings, monkeypatch):
    """The headline test.

    A body carrying `actor_id` must not decide who the audit says asked. Pydantic ignores
    unknown keys, so this does not raise — the value simply has nowhere to go, because
    QueryRequest has no such field. That absence is the control; this test is what keeps
    someone from helpfully adding the field back.
    """
    from app.api.queries import QueryRequest

    assert "actor_id" not in QueryRequest.model_fields, (
        "a client-settable actor turns the audit trail back into a log"
    )

    request = QueryRequest.model_validate(
        {"question": "enalapril dose", "actor_id": "dr-someone-else"}
    )
    assert not hasattr(request, "actor_id")


async def test_no_token_is_rejected(engine, idp, settings):
    from app.db.session import get_session
    from app.main import app

    async def override():
        yield None

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/queries", json={"question": "enalapril dose"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# --- forgeries ---------------------------------------------------------------------


async def test_alg_none_is_rejected(idp, settings):
    """The oldest JWT forgery there is. A library that reads `alg` from the header will
    happily verify a token with no signature at all."""
    forged = jwt.encode(
        {"sub": "dr-attacker", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300},
        key="",
        algorithm="none",
        headers={"kid": "key-1"},
    )

    with pytest.raises(HTTPException) as exc:
        await verify_token(forged, settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_hs256_signed_with_the_public_key_is_rejected(idp, settings):
    """Algorithm confusion — the subtle one.

    The verifier expects RS256 and holds a *public* key. An attacker signs HS256 using
    that public key as the HMAC secret. If the code trusts the header's `alg`, the
    verifier computes HMAC with the same public bytes and the signature matches. The
    secret was published; that was the point of it.
    """
    public_pem = idp.private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    # Hand-rolled, because pyjwt refuses to *encode* this: it sees a PEM being passed as
    # an HMAC secret and raises. That guardrail protects the signer, and an attacker is
    # not using our signer — so minting it through pyjwt would have tested pyjwt's
    # politeness rather than our verifier. Three lines of base64 is what they would write.
    import base64
    import hashlib
    import hmac
    import json

    def segment(data: dict) -> bytes:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=")

    signing_input = (
        segment({"alg": "HS256", "typ": "JWT", "kid": "key-1"})
        + b"."
        + segment(
            {"sub": "dr-attacker", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 300}
        )
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    ).rstrip(b"=")
    forged = (signing_input + b"." + signature).decode()

    with pytest.raises(HTTPException) as exc:
        await verify_token(forged, settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_signed_by_another_key_is_rejected(idp, settings):
    other, _ = make_key("key-1")  # same kid, different key: the signature is what decides

    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(key=other), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_for_another_audience_is_rejected(idp, settings):
    """The check people skip, and the reason it is not ceremony.

    This token is real: correctly signed, unexpired, from the right issuer. It was issued
    to a different client in the same realm — a monitoring service account, another app's
    user. Without the audience check it is accepted here, and every one of those becomes
    a clinician.
    """
    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(aud="some-other-app"), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_with_no_audience_is_rejected(idp, settings):
    """Keycloak's default. Without the realm's audience mapper the claim is absent, and
    absent must fail rather than skip the check."""
    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(aud=None), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_from_another_issuer_is_rejected(idp, settings):
    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(iss="https://evil.example.invalid"), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_an_expired_token_is_rejected(idp, settings):
    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(exp=int(time.time()) - 1), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_with_no_expiry_is_rejected(idp, settings):
    """A token that never expires cannot be revoked by waiting, which is the only
    revocation this design has."""
    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(exp=None), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_token_with_an_empty_subject_is_rejected(idp, settings):
    """`sub` becomes actor_id. An audit row attributed to "" is attributed to nobody."""
    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(sub="   "), settings)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def test_a_discovery_document_naming_a_different_issuer_is_refused(idp, settings):
    """The document decides which keys we trust. One that names an issuer other than the
    one we asked for is a redirect somewhere unintended or a misconfiguration."""
    idp.discovery_issuer = "https://evil.example.invalid"

    with pytest.raises(HTTPException) as exc:
        await verify_token(idp.mint(), settings)
    assert exc.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR


# --- key rotation, and the DoS the obvious fix creates -----------------------------


async def test_rotation_does_not_lock_everyone_out(idp, settings):
    """Caching forever means the issuer rotating its keys locks out every clinician at
    once, until someone restarts the API. An unknown kid must trigger a refetch."""
    await verify_token(idp.mint(), settings)

    idp.rotate()
    clinician = await verify_token(idp.mint(kid="key-2"), settings)

    assert clinician.actor_id == "dr-ada-smith"


async def test_unknown_kids_do_not_let_a_stranger_hammer_the_issuer(idp, settings):
    """The fix above, weaponised.

    If every unknown kid refetches, unauthenticated requests carrying random kids each
    cost the issuer a round trip — we become the amplifier. Ten forged tokens must not
    be ten fetches.
    """
    await verify_token(idp.mint(), settings)
    baseline = idp.jwks_fetches

    for _ in range(10):
        with pytest.raises(HTTPException):
            await verify_token(idp.mint(kid=f"forged-{uuid.uuid4().hex}"), settings)

    assert idp.jwks_fetches - baseline <= 1, "each forged kid must not cost the issuer a fetch"


async def test_keys_are_cached_across_requests(idp, settings):
    for _ in range(5):
        await verify_token(idp.mint(), settings)

    assert idp.jwks_fetches == 1, "fetching per request makes the issuer a hard dependency"


# --- no bypass ---------------------------------------------------------------------


def test_a_deployed_environment_cannot_start_without_an_issuer():
    """Fails at boot, not at 2am. There is deliberately no flag that disables auth — the
    only way to have no identity is to not be deployed."""
    with pytest.raises(ValueError, match="OIDC_ISSUER"):
        Settings(environment="production", oidc_issuer="", oidc_audience="")


def test_a_deployed_environment_cannot_start_without_an_audience():
    with pytest.raises(ValueError, match="OIDC_AUDIENCE"):
        Settings(environment="production", oidc_issuer=ISSUER, oidc_audience="")


def test_there_is_no_flag_that_turns_authentication_off():
    """A regression guard aimed at a future good intention. `AUTH_DISABLED` for local dev
    is how production ends up open: the flag ships, and nothing looks broken."""
    fields = set(Settings.model_fields)
    for name in ("auth_disabled", "disable_auth", "skip_auth", "auth_enabled"):
        assert name not in fields


def test_only_rs256_is_accepted():
    assert auth_module.ALLOWED_ALGORITHMS == ["RS256"]
    assert "none" not in auth_module.ALLOWED_ALGORITHMS


# --- the dependency ----------------------------------------------------------------


async def test_current_clinician_is_the_only_supplier_of_actor_id(idp, settings, monkeypatch):
    """Not a mechanism test — a test of the claim. If some other call site can produce an
    actor_id, the token is decoration."""
    import inspect

    from app.api import queries

    source = inspect.getsource(queries.post_query)
    assert "actor_id=clinician.actor_id" in source
    assert "request.actor_id" not in source


def test_clinician_cannot_be_mutated_after_verification():
    clinician = Clinician(actor_id="dr-ada-smith")
    with pytest.raises(Exception):
        clinician.actor_id = "dr-someone-else"  # type: ignore[misc]


def test_the_dependency_is_wired_into_the_query_endpoint():
    import inspect

    from app.api import queries

    signature = inspect.signature(queries.post_query)
    assert signature.parameters["clinician"].default.dependency is current_clinician
