#!/usr/bin/env python3
"""Pins booktext.py — the text extractor behind `classify --text-fallback`. A fixture EPUB is
just a zip of XHTML, so the whole EPUB strategy is testable with no Calibre, library, or LLM.
No framework:  uv run tests/test_booktext.py   (also pytest-collectable)."""
import os, sys, tempfile, zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from scourgify import booktext
from fixture_db import build


def _epub(path, chapters, nav="toc"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("nav.xhtml", f"<html><body><p>{nav}</p></body></html>")   # short -> skipped as a nav page
        for i, text in enumerate(chapters):
            z.writestr(f"ch{i}.xhtml", f"<html><body><p>{text}</p></body></html>")
        z.writestr("cover.jpg", b"\xff\xd8 not xhtml")                        # non-XHTML member ignored


def test_extract_reads_chapters_and_skips_nav_pages():
    p = os.path.join(tempfile.mkdtemp(), "b.epub")
    body = "chapterword " * 40                       # > MIN_PAGE chars -> a real chapter
    _epub(p, [body], nav="short toc page")
    out = booktext.extract(p)
    assert "chapterword" in out
    assert "short toc page" not in out               # nav/title heuristics keep the sample prose-only


def test_extract_respects_limit_and_absence():
    p = os.path.join(tempfile.mkdtemp(), "b.epub")
    _epub(p, ["x" * 5000])
    assert len(booktext.extract(p, limit=100)) <= 100
    assert booktext.extract(None) == ""
    assert booktext.extract("/nonexistent/b.epub") == ""


def test_extract_garbage_epub_degrades_to_empty():
    p = os.path.join(tempfile.mkdtemp(), "b.epub")
    open(p, "wb").write(b"this is not a zip archive")
    assert booktext.extract(p) == ""                 # never raises into a classify run


def test_paths_prefers_epub_over_other_formats():
    d = tempfile.mkdtemp()
    con = build(os.path.join(d, "metadata.db"), [dict(id=1, added="2026-01-01"), dict(id=2, added="2026-01-02")])
    con.execute("INSERT INTO data VALUES(1, 'MOBI', 'story')")
    con.execute("INSERT INTO data VALUES(1, 'EPUB', 'story')")
    con.execute("INSERT INTO data VALUES(2, 'PDF', 'other')")
    con.commit()
    old = os.environ.get("CALIBRE_LIBRARY"); os.environ["CALIBRE_LIBRARY"] = d
    try:
        paths = booktext.paths(con)
        assert paths[1].endswith("story.epub")       # EPUB preferred
        assert paths[2].endswith("other.pdf")        # else any available format
    finally:
        os.environ.pop("CALIBRE_LIBRARY", None) if old is None else os.environ.__setitem__("CALIBRE_LIBRARY", old)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
