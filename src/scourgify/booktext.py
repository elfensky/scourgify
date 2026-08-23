#!/usr/bin/env python3
"""Book-text extraction for the synopsis pass — one deep interface over two strategies.

`paths(con)` discovers each book's best format file (EPUB preferred); `extract(path, limit=)`
pulls readable text from it: EPUBs are read directly as zips of XHTML (with a zip-bomb guard and
a nav-page heuristic); every other format shells out to Calibre's `ebook-convert` with a timeout.
No LLM, no library state — testable against a fixture EPUB.

(It began as `classify --text-fallback`'s prose sampler. That flag is retired: synopsis.py needs
extraction more than classify ever did, and reads whole books rather than a slice.)"""
import os
import re
import subprocess

from scourgify.common import library

MAX_MEMBER = 2_000_000     # untrusted download: skip zip members larger than this (zip bomb)
MIN_PAGE = 200             # chapters are long; shorter XHTML members are nav/title pages
CONVERT_TIMEOUT = 180      # seconds before a wedged ebook-convert is abandoned


def strip_html(s: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def paths(con) -> dict:
    """{book_id: absolute path of the preferred format file} — EPUB if present, else any."""
    bp = {b: p for b, p in con.execute("SELECT id, path FROM books")}
    byb = {}
    for b, fmt, name in con.execute("SELECT book, format, name FROM data"):
        byb.setdefault(b, {})[fmt.upper()] = os.path.join(library(), bp[b], name + "." + fmt.lower())
    return {b: fm.get("EPUB") or next(iter(fm.values())) for b, fm in byb.items()}


def extract(path: str | None, limit: int = 6000) -> str:
    """Readable prose from an ebook file, capped at `limit` chars; '' when nothing usable."""
    if not path or not os.path.exists(path): return ""
    if path.lower().endswith(".epub"):                  # fast path: epub is a zip of XHTML
        import zipfile
        try:
            z = zipfile.ZipFile(path); out = []
            for n in z.namelist():
                if not n.lower().endswith((".xhtml", ".html", ".htm")): continue
                if z.getinfo(n).file_size > MAX_MEMBER: continue
                t = re.sub(r"\s+", " ", strip_html(z.read(n).decode("utf-8", "ignore"))).strip()
                if len(t) > MIN_PAGE: out.append(t)
                if sum(len(x) for x in out) > limit: break
            return " ".join(out)[:limit]
        except Exception: return ""
    import tempfile                                      # other formats (MOBI/PDF/DOCX/…): let calibre extract
    try:
        with tempfile.TemporaryDirectory() as td:
            o = os.path.join(td, "o.txt")
            subprocess.run(["ebook-convert", path, o], capture_output=True, timeout=CONVERT_TIMEOUT)
            return re.sub(r"\s+", " ", open(o, errors="ignore").read()).strip()[:limit] if os.path.exists(o) else ""
    except Exception: return ""
