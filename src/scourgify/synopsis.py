#!/usr/bin/env python3
"""Settle every book's synopsis: a spoiler-safe back cover in Calibre's own description field.

    scourgify synopsis                       # dry run: resolve the queue, report it, send NOTHING
    scourgify synopsis --apply               # generate + write (Calibre CLOSED)
    scourgify synopsis --apply --step        # ...reviewing each new synopsis 1-by-1
    scourgify synopsis --apply --batch 100   # chew through the queue a chunk at a time

Two jobs per book, and the cheap one runs first: if the existing description is already an
adequate, spoiler-safe blurb the model says so in ONE word and the book is stamped with its
author's own text UNTOUCHED (never AI-flatten a library of fine descriptions into one beige
voice). Only a bad blurb earns the expensive path — read the book's own prose in slabs, note each,
then fold the notes into a back cover: premise, characters, stakes, hook, and a themes line.
Never plot outcomes, never endings; the description stays safe to browse.

**`#synopsized`** (datetime) is the entire state: "synopsis SETTLED on this date", earned either
way. It is the queue (unstamped = outstanding), the provenance, and the refresh clock (`#updated`
newer than the stamp = the fic grew chapters, so re-settle it). Queue membership itself lives in
`select.pick("unsynopsized")` and is never re-derived here.

The synopsis goes into the BUILT-IN description because improving it is half the point: Calibre
and every reader display that field. FanFicFare would overwrite it on a metadata re-fetch, so the
pass refuses to start unless FFF's Comments -> "New Only" switch is on (`--force` accepts the
degraded self-healing mode instead: a clobbered book simply re-enters the queue and is
re-summarized — wasted free compute, not lost data).

Engine: `apple` — free, on-device, single-threaded and slow, which is fine; this sweep is designed
to run in the background for weeks. A cloud engine is an explicit `--engine` opt-in for a book
that stalls, never a default. Unlike classify, a BARE run here costs nothing at all: there is no
intermediate proposal artifact to build, because resume is off the stamp."""
import argparse
import copy
import os
import re

from scourgify import booktext, select
from scourgify.artifacts import merge_failures, read_rows, syn_fail, write_failures
from scourgify.booktext import strip_html
from scourgify.common import (GuardrailError, custom_column_id, op_create_column, op_set_field,
                              op_stamp_now, ro_connect, run_writer, titles as book_titles)
from scourgify.engines import ENGINES, ask_retry
from scourgify.setup import comments_protected

STAMP = select.SYN_STAMP     # one name for the stamp; select owns the queue that reads it

# Measured 2026-08-23 against afm.swift with real EPUB prose: the on-device model's context is a
# HARD 4096 tokens shared by prompt AND answer, and English prose runs ~4.3 chars/token (18,000
# chars = 4,165 tokens = refused; 17,000 passes). 10k chars is ~2,300 tokens, leaving generous
# room for the template and the reply. Re-measure when the bundled model changes.
CHUNK = 10_000
MAX_CHUNKS = 12          # slabs actually read per book — see chunks()
NOTE_CAP = 500           # chars kept per slab note, so MAX_CHUNKS*NOTE_CAP fits ONE reduce prompt
EXTRACT = 2_000_000      # chars pulled from the file before sampling (bounds memory on a huge fic)
MIN_JUDGE = 120          # below this there is no blurb worth judging — go straight to generation
MIN_SYNOPSIS = 120       # a shorter answer than this is the model failing, not a back cover
JUDGE_CAP = 4_000        # blurb chars sent to the adequacy judge

JUDGE_P = (
    "You are judging whether a fanfiction's existing description works as a back-cover blurb.\n"
    "GOOD: says what the story is about — premise, main characters, what is at stake — in a "
    "sentence or more, and gives away no ending.\n"
    "BAD: author's notes, update schedules, a dump of tags, 'summary inside', one vague line, "
    "cross-posting boilerplate, or nothing about the story at all.\n\n"
    "Title: {title}\nDescription: {blurb}\n\n"
    "Reply with exactly one word: YES if it is good, NO if it is not.")

NOTE_P = (
    'Excerpt {i} of {n} from the fanfiction "{title}".\n'
    "In two sentences, note the characters, the setting, the situation and the tone here. "
    "Plain prose, no preamble.\n\n{chunk}")

BACK_P = (
    'Notes taken while reading the fanfiction "{title}", in order:\n\n{notes}\n\n'
    "Write its back-cover blurb: three to five sentences of flowing prose covering the premise, "
    "the main characters and what is at stake, ending on a hook. Then one final line reading "
    "'Themes: a, b, c' with three short theme or trope words.\n"
    "SPOILER-SAFE: never reveal how the story ends or how its conflicts resolve.\n"
    "No preamble, no title, no headings — the blurb only.")


def chunks(text: str, size: int = CHUNK, cap: int = MAX_CHUNKS) -> list:
    """`text` in <=size-char slabs; past `cap` slabs, an even spread that always keeps the first two.

    ponytail: even sampling rather than every chapter. The back cover needs the opening (premise,
    cast, hook — always slabs 0 and 1) plus a sense of the whole, and reading all ~300 slabs of a
    500k-word fic on-device costs ~10 minutes for ONE book against a 7,949-book library. Raise
    `cap` if generated synopses read thin; the ceiling here is wall-clock, not quality by design."""
    parts = [text[i:i + size] for i in range(0, len(text), size)]
    if len(parts) <= cap: return parts
    step = len(parts) / cap
    keep = sorted({0, 1} | {int(i * step) for i in range(cap)})[:cap]
    return [parts[i] for i in keep]


def clean(s) -> str:
    """One engine answer as storable prose: whitespace collapsed, a chatty lead-in dropped,
    surrounding quotes stripped. '' for an error line or nothing usable — and '' is exactly what
    the callers test, because handing '' to a set_field on `comments` is a WIPE, not a synopsis."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or s.startswith("ERR"): return ""
    s = re.sub(r"^(sure[,!.]?\s*)?here(?:'s| is)[^:]{0,60}:\s*", "", s, flags=re.I)
    return s.strip().strip('"').strip()


def verdict(resp) -> str:
    """The adequacy answer as 'keep' | 'generate' | '' (unreadable). Pure.

    Three states, not two, because a guess is wrong in BOTH directions: guessing 'keep' stamps a
    bad blurb as settled forever, and guessing 'generate' AI-rewrites an author's healthy prose.
    '' settles nothing — the book earns a failure row and is retried later, on another engine if
    need be. Only a leading YES/NO counts; the prompt asks for exactly one word."""
    s = str(resp or "").strip()
    if re.match(r"\W*yes\b", s, re.I): return "keep"
    if re.match(r"\W*no\b", s, re.I): return "generate"
    return ""


def guard_comments(con, force: bool = False) -> None:
    """Pre-flight: refuse to start unless FanFicFare cannot overwrite what we are about to write.

    GuardrailError, never SystemExit — this is reachable from a Calibre job, where a SystemExit
    would kill the worker thread silently. It runs before any GENERATION, not before the write:
    the point is not to spend hours of on-device compute on synopses a re-fetch will erase."""
    if force: return
    if comments_protected(con) is False:
        raise GuardrailError(
            "FanFicFare would overwrite every synopsis this pass writes.\n"
            "  The synopsis lives in the built-in description (Comments), and FFF re-fetches it.\n"
            "  Fix: Calibre -> Preferences -> Plugins -> FanFicFare -> Customize -> Standard Columns\n"
            "       -> tick 'New Only' beside Comments.   (`scourgify setup` offers the same fix.)\n"
            "  --force accepts the degraded mode instead: a clobbered book re-enters the queue and "
            "is re-summarized — wasted free compute, not lost data.")


def settle(title: str, blurb: str, path, ask) -> tuple:
    """One book -> (synopsis, error).

    ('', '')   the existing blurb is adequate — SETTLED, nothing to write, author voice kept.
    (text, '') a generated back cover to write into the description.
    ('', err)  attempted and blocked; the caller files a failure row so the queue stays finite.

    `ask` is prompt -> (text, err) — the injected seam (engines.ask_retry in production, a fake in
    tests), the same shape as promote.run(ask=) and classify.Plan.run(ask=)."""
    if len(blurb) >= MIN_JUDGE:
        v, err = ask(JUDGE_P.format(title=title, blurb=blurb[:JUDGE_CAP]))
        if err: return "", err
        d = verdict(v)
        if d == "keep": return "", ""
        if not d: return "", f"parse: no yes/no in the adequacy verdict ({clean(v)[:80]!r})"
    text = booktext.extract(path, limit=EXTRACT)
    if not text: return "", "no readable text (file missing, DRM'd, or empty)"
    slabs = chunks(text)
    notes = []
    for i, c in enumerate(slabs, 1):
        n, err = ask(NOTE_P.format(i=i, n=len(slabs), title=title, chunk=c))
        if err: return "", err
        n = clean(n)
        if n: notes.append(n[:NOTE_CAP])
    if not notes: return "", "engine returned nothing usable for any excerpt"
    out, err = ask(BACK_P.format(title=title, notes="\n".join(f"- {n}" for n in notes)))
    if err: return "", err
    out = clean(out)
    return (out, "") if len(out) >= MIN_SYNOPSIS else ("", "engine returned no usable synopsis")
