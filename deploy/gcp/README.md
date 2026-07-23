# Vertex service-account key goes here

This directory is mounted read-only into the api container at `/run/gcp`, and it is mounted
whether or not it holds anything — so switching to Vertex never requires editing
`docker-compose.cohost.yml` on the day of the switch.

Put the service-account JSON here and point the env at it:

    GOOGLE_APPLICATION_CREDENTIALS=/run/gcp/vertex-sa.json

The path is the one INSIDE the container. `deploy/gcp/vertex-sa.json` on the host is
`/run/gcp/vertex-sa.json` in the api.

**Nothing in here is committed.** `.gitignore` excludes every file but this README, because a
service-account key in git history is a key that has to be rotated. Keep the file at `600`.
