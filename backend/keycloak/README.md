# Development realm

`realm-luxmedicine.json` is imported on `docker compose up keycloak`. It is **not** a
production configuration: the passwords are in the file, the container runs `start-dev` over
plain HTTP with an in-memory database, and every user is fictional. Everything resets on
container restart.

> The realm JSON carries **no comments.** Keycloak's realm import (Quarkus/Jackson) rejects
> any unrecognised field — a top-level `_comment` fails the whole import with
> `Unrecognized field "_comment" ... not marked as ignorable`, and the server does not
> start. So the documentation lives here instead.

## The two load-bearing mappers

**`luxmedicine-audience`.** Keycloak does not put the client id in `aud` by default — it
puts `account`, and the client id goes to `azp`. Without this mapper every token is rejected
by `app/core/auth.py`, which checks the audience. That check is not ceremony: without it,
any token this realm ever signed is accepted, including other clients' users and service
accounts. The mapper is how a token says who it was addressed to.

**`luxmedicine-clinic-id`.** `app/core/auth.py` refuses any token with no `clinic_id` claim
— it is the tenant boundary row-level security filters on (#31), and there is no default
clinic to fall back to. This mapper copies `dr.smith`'s `clinic_id` attribute (`clinic-demo`)
into the access token as the `clinic_id` claim. Without both the mapper and the attribute,
every dev token is rejected 401.

## Getting a token (dev only)

`directAccessGrantsEnabled` turns on the password grant, so a token can be fetched directly
while developing. This belongs in a dev realm and nowhere else — it hands the user's password
to the client, the flow OAuth 2.1 removed for that reason.

```bash
curl -d grant_type=password -d client_id=luxmedicine-api \
     -d username=dr.smith -d password=dev-password \
     http://localhost:8081/realms/luxmedicine/protocol/openid-connect/token
```

Or use `python scripts/dev_token.py` (`--header` for a full `Authorization:` header).
