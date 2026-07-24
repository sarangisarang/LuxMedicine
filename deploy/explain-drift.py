"""Given a drifted service, say WHICH field moved — the hash alone cannot.

check-drift.sh finds that a container's config-hash no longer matches the file. That is enough
to know something changed, but not what, and the two faults this guard exists for both live in
one specific field each: Keycloak's was `command` (a stray --optimized), the compose-swap's was
a dropped `environment` key (GEMINI_API_KEY). So on drift, diff exactly those two.

Reads the file's resolved config as JSON on stdin (`docker compose config --format json`), and
takes the service name and the running container id as argv. Environment is compared by KEY
only — a value diff would print secrets, and the faults that matter are a key that appears or
vanishes, not a rotated password.
"""

import json
import subprocess
import sys


def main() -> int:
    service, cid = sys.argv[1], sys.argv[2]
    want = json.load(sys.stdin).get("services", {}).get(service, {})
    run = json.loads(subprocess.check_output(["docker", "inspect", cid]))[0]["Config"]

    # command: the file's `command` (a list, or a string to split) vs the container's Cmd.
    want_cmd = want.get("command")
    if isinstance(want_cmd, str):
        want_cmd = want_cmd.split()
    run_cmd = run.get("Cmd")
    if want_cmd is not None and want_cmd != run_cmd:
        print(f"    command: file={want_cmd} running={run_cmd}")

    # environment: key sets only. The file may express it as a map or a "KEY=value" list.
    want_env = want.get("environment", {})
    if isinstance(want_env, dict):
        want_keys = set(want_env.keys())
    else:
        want_keys = {e.split("=", 1)[0] for e in want_env}
    run_keys = {e.split("=", 1)[0] for e in (run.get("Env") or [])}

    # Only the file-has / container-lacks direction is actionable: the reverse is dominated by
    # the image's own baked-in defaults (PATH, LANG, …), which are noise, not drift.
    only_file = sorted(want_keys - run_keys)
    if only_file:
        print(f"    env declared in file but absent from container: {', '.join(only_file)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
