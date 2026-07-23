"""Say where inference actually runs — read back, never assumed.

    python -m app.cli.check_inference

Prints the endpoint the SDK resolved, whether that endpoint keeps processing in the EU, and
whether the residency refusal is currently waived. Exits non-zero when the configuration
would send clinical text outside the EU.

**Why reading it back is the whole point.** A config saying `europe-west3` and a client
talking to the global endpoint is a real, documented failure (google-genai #27984: the JS SDK
silently drops the location when an API key is present). "I set the region" and "it runs in
the region" are different claims, and only the second one is a promise to a patient. This
asks the client which base URL it built, so the answer comes from the object that will make
the call rather than from the variable that was supposed to configure it.

Makes no model call: it inspects the constructed client, so it costs no quota and can be run
before, during and after a switch.
"""

from __future__ import annotations

import argparse
import os
import sys

from app.core.config import get_settings


def _describe() -> tuple[int, list[str]]:
    from app.services.extractor_gemini import DEFAULT_MODEL, GeminiExtractor

    settings = get_settings()
    lines: list[str] = []

    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
    location = os.environ.get("GOOGLE_CLOUD_LOCATION") or ""
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""

    lines.append(f"  environment          {settings.environment}")
    lines.append(f"  model                {DEFAULT_MODEL}")
    lines.append(f"  GOOGLE_CLOUD_PROJECT {project or '(unset — Developer API, global endpoint)'}")
    lines.append(f"  GOOGLE_CLOUD_LOCATION {location or '(unset)'}")
    if credentials:
        exists = "present" if os.path.isfile(credentials) else "MISSING at that path"
        lines.append(f"  credentials          {credentials}  [{exists}]")
    else:
        lines.append("  credentials          (unset — Vertex needs a service account or ADC)")

    try:
        extractor = GeminiExtractor(model=DEFAULT_MODEL)
    except Exception as exc:  # noqa: BLE001 — a construction failure is the finding
        lines.append("")
        lines.append(f"  CLIENT DID NOT CONSTRUCT: {type(exc).__name__}: {exc}")
        return 2, lines

    endpoint = extractor.endpoint or "(none resolved)"
    lines.append("")
    lines.append(f"  resolved endpoint    {endpoint}")
    lines.append(f"  keeps processing EU  {extractor.processes_in_eu}")
    lines.append(f"  residency waived     {settings.allow_non_eu_inference}")
    lines.append("")

    if extractor.processes_in_eu:
        lines.append("  OK — inference resolves to a European Vertex endpoint.")
        if settings.allow_non_eu_inference:
            lines.append("  NOTE: ALLOW_NON_EU_INFERENCE is still true. It is no longer needed;")
            lines.append("        turn it off so the guardrail is armed rather than bypassed.")
        return 0, lines

    if settings.allow_non_eu_inference:
        lines.append("  WAIVED — this endpoint is NOT EU, and the refusal is switched off.")
        lines.append("  Clinical text leaves the EU. Demo posture only; not for patient data.")
        return 1, lines

    lines.append("  REFUSED — this endpoint is not EU and the guardrail is armed, so")
    lines.append("  extract() will raise rather than send anything. Set GOOGLE_CLOUD_PROJECT")
    lines.append("  and GOOGLE_CLOUD_LOCATION to a European region with real credentials.")
    return 1, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    code, lines = _describe()
    print("\ninference residency check\n")
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
