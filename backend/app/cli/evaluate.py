"""Run the question set through the real pipeline and report what actually happened.

    python -m app.cli.evaluate --questions eval/questions.yaml --out eval/runs/

**This does not produce "the rejection rate".** It produces four numbers that a single rate
would destroy, because a no-answer means opposite things depending on what was asked:

    answered   when answerable    -> working
    declined   when answerable    -> the failure worth finding. Invisible from outside: an
                                     honest "I don't know" and a wrongly withheld answer
                                     look identical to a clinician.
    declined   when not_covered   -> CORRECT. The corpus does not cover it and the system
                                     said so.
    answered   when not_covered   -> the dangerous one.

Collapse those into one percentage and the number cannot be interpreted at all — which is
what `luxmedicine-blockers` means by "do not run 500 questions and call the number the
model's hallucination rate".

**Every rejected quote is diagnosed, not counted.** `services/quote_diagnosis.py` says why a
quote failed #19 — typography, boundary, mis-cited, paraphrase, synthesis, parametric leak —
and only the last two are the model inventing. The first real #19 rejection this project ever
saw looked like a hallucination and was our own glued-spaces bug; the second looked like
synthesis and was the table-scrambling regression. A run that reports a count reproduces
exactly that mistake at scale.

**It runs against the real pipeline**, not a reimplementation of it: `answer_query` embeds,
retrieves, extracts, validates and writes a hash-chained audit row. Rows land under
`clinic-eval` so an eval never mixes into a clinic's chain. There is no unaudited path and
this does not invent one.

**A run is reproducible, which took a measurement to establish.** Six questions, three
identical runs with the cache off, eighteen live calls: 0/6 flipped (2026-07-17). At
temperature 0 this model returns the same outcome and the same citation count every time, so a
difference between two runs is a difference in the corpus, the retrieval, the prompt or the
model — never noise. That is what makes a prompt change measurable: 3/3 answering before and
0/3 after is a result, not a coincidence. Before that was measured, every surprising outcome
had "the model is just like that" available as an excuse, and it was the wrong one.

Paced for the free tier: `gemini-3.1-flash-lite` allows 15 RPM / 500 RPD, so the default
pacing is conservative and the whole set fits in a day. Set LLM_CACHE_DIR and a re-run of
unchanged questions costs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services.pipeline import answer_query
from app.services.quote_diagnosis import diagnose

EVAL_CLINIC = "clinic-eval"
EVAL_ACTOR = "eval-harness"


@dataclass
class Question:
    id: str
    text: str
    expect: str
    language: str = "English"
    source: str | None = None
    note: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    id: str
    expect: str
    tags: list[str]
    # What the clinician would have seen.
    no_answer_reason: str | None
    groups: int
    citations: int
    orgs: list[str]
    pages: list[str]
    # What #19 threw away, and WHY — the part a count would hide.
    rejected: int
    diagnoses: list[dict]
    elapsed_s: float
    error: str | None = None

    @property
    def answered(self) -> bool:
        return self.groups > 0

    @property
    def verdict(self) -> str:
        """The only classification that means anything: outcome against expectation."""
        if self.error:
            return "error"
        if self.expect == "answerable":
            return "answered" if self.answered else "wrongly_declined"
        return "correctly_declined" if not self.answered else "answered_uncovered"


def load_questions(path: Path) -> list[Question]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Question(**q) for q in raw["questions"]]


async def _corpus_contains(session: AsyncSession, phrase: str) -> bool:
    return (
        await session.execute(
            sql_text("SELECT EXISTS(SELECT 1 FROM chunks WHERE lower(content) LIKE :p)"),
            {"p": f"%{phrase.lower()}%"},
        )
    ).scalar_one()


async def _diagnose_rejections(session: AsyncSession, answered) -> list[dict]:
    """Why each rejected quote failed — never just how many.

    `corpus_contains` is what separates a sentence assembled from real fragments (synthesis,
    our chunking problem) from one the model brought with it (a parametric leak, a model
    problem). Without it the two collapse and the run cannot tell whose fault it is.
    """
    out: list[dict] = []
    chunks = {h.chunk_id: h.content for h in answered.hits}
    for rej in answered.rejected:
        cited = chunks.get(rej.chunk_id, "")
        others = [c for cid, c in chunks.items() if cid != rej.chunk_id]

        async def contains(phrase: str) -> bool:
            return await _corpus_contains(session, phrase)

        # diagnose() takes a sync predicate; resolve the corpus lookups it needs up front.
        words = rej.quote.lower().split()
        grams = {" ".join(words[i : i + 4]) for i in range(max(0, len(words) - 3))}
        found = {g: await contains(g) for g in list(grams)[:12]}

        d = diagnose(
            rej.quote,
            cited,
            other_passages=others,
            corpus_contains=lambda g: found.get(g, False),
        )
        out.append(
            {
                "verdict": d.verdict.value,
                "is_invention": d.is_invention,
                "detail": d.detail,
                "quote": rej.quote[:160],
            }
        )
    return out


async def run_one(session: AsyncSession, q: Question, embedder, extractor) -> Outcome:
    started = time.monotonic()
    try:
        answered = await answer_query(
            session,
            question=q.text,
            actor_id=EVAL_ACTOR,
            clinic_id=EVAL_CLINIC,
            embedder=embedder,
            extractor=extractor,
            language=q.language,
        )
    except Exception as exc:  # noqa: BLE001 - a run must survive one bad question
        return Outcome(
            id=q.id, expect=q.expect, tags=q.tags, no_answer_reason=None, groups=0,
            citations=0, orgs=[], pages=[], rejected=0, diagnoses=[],
            elapsed_s=time.monotonic() - started, error=f"{type(exc).__name__}: {exc}",
        )

    payload = answered.payload
    groups = payload.groups or []
    return Outcome(
        id=q.id,
        expect=q.expect,
        tags=q.tags,
        no_answer_reason=payload.no_answer_reason.value if payload.no_answer_reason else None,
        groups=len(groups),
        citations=sum(len(g.citations) for g in groups),
        orgs=sorted({g.issuing_org for g in groups}),
        pages=[f"{c.page_start}" for g in groups for c in g.citations],
        rejected=payload.rejected_citations,
        diagnoses=await _diagnose_rejections(session, answered),
        elapsed_s=round(time.monotonic() - started, 2),
    )


def report(outcomes: list[Outcome]) -> dict:
    """Four numbers, kept apart. See the module docstring for why one would be a lie."""
    buckets: dict[str, list[str]] = {}
    for o in outcomes:
        buckets.setdefault(o.verdict, []).append(o.id)

    answerable = [o for o in outcomes if o.expect == "answerable" and not o.error]
    uncovered = [o for o in outcomes if o.expect == "not_covered" and not o.error]
    glyph = [o for o in answerable if "glyph-45" in o.tags]
    clean = [o for o in answerable if "glyph-45" not in o.tags]

    verdicts: dict[str, int] = {}
    inventions = 0
    for o in outcomes:
        for d in o.diagnoses:
            verdicts[d["verdict"]] = verdicts.get(d["verdict"], 0) + 1
            inventions += bool(d["is_invention"])

    def rate(xs, pred):
        return f"{sum(1 for x in xs if pred(x))}/{len(xs)}" if xs else "0/0"

    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "model": get_settings().llm_cache_dir and "cached-or-live" or "live",
        "counts": {k: len(v) for k, v in sorted(buckets.items())},
        "by_id": buckets,
        "answerable": {
            "answered": rate(answerable, lambda o: o.answered),
            "clean_prose_answered": rate(clean, lambda o: o.answered),
            # Kept apart on purpose: #45 corrupts the characters in tables and thresholds,
            # so folding these in would launder a known extraction bug into a model score.
            "glyph_45_answered": rate(glyph, lambda o: o.answered),
        },
        "not_covered": {
            "correctly_declined": rate(uncovered, lambda o: not o.answered),
        },
        "rejected_quotes": {
            "total": sum(o.rejected for o in outcomes),
            "by_verdict": verdicts,
            # The only number that is about the MODEL. Everything else is about us.
            "inventions": inventions,
        },
    }


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    questions = load_questions(args.questions)
    if args.only:
        questions = [q for q in questions if q.id in set(args.only)]

    from app.services.embedding import E5Embedder
    from app.services.extractor_gemini import GeminiExtractor

    print(f"{len(questions)} questions | loading the embedder (local, no quota)…")
    embedder = E5Embedder()
    extractor = GeminiExtractor()
    model = extractor.model
    if settings.llm_cache_dir is not None:
        from app.services.llm_cache import CachingExtractor, DiskCache

        extractor = CachingExtractor(
            extractor, DiskCache(root=settings.llm_cache_dir), model=model
        )
        print(f"cache: {settings.llm_cache_dir} (a re-run of unchanged questions is free)")
    print(f"model: {model} | pacing: {args.rpm} requests/minute\n")

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    interval = 60.0 / args.rpm
    outcomes: list[Outcome] = []
    try:
        for i, q in enumerate(questions, 1):
            tick = time.monotonic()
            async with maker() as session:
                o = await run_one(session, q, embedder, extractor)
            outcomes.append(o)
            flag = "!!" if o.verdict in {"wrongly_declined", "answered_uncovered", "error"} else "  "
            print(
                f"{flag} [{i:>3}/{len(questions)}] {o.id:<28} {o.verdict:<19} "
                f"cites={o.citations} rej={o.rejected} {o.elapsed_s}s"
            )
            # Pace against RPM, not against wall time: a cache hit costs no request, so it
            # must not cost a sleep either.
            if i < len(questions) and o.elapsed_s > 0.5:
                await asyncio.sleep(max(0.0, interval - (time.monotonic() - tick)))
    finally:
        await engine.dispose()

    summary = report(outcomes)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.out / f"{stamp}-{model}.json"
    path.write_text(
        json.dumps({"summary": summary, "outcomes": [asdict(o) for o in outcomes]}, indent=2),
        encoding="utf-8",
    )

    print("\n" + json.dumps(summary, indent=2))
    print(f"\nwritten: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--questions", type=Path, default=Path("eval/questions.yaml"))
    p.add_argument("--out", type=Path, default=Path("eval/runs"))
    p.add_argument("--only", nargs="*", help="run only these question ids")
    # 15 RPM is gemini-3.1-flash-lite's free-tier ceiling; 12 leaves room for a retry.
    p.add_argument("--rpm", type=float, default=12.0)
    return asyncio.run(main_async(p.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
