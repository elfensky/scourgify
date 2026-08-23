#!/usr/bin/env python3
"""The synopsis pass — the FanFicFare clobber guard, the pure prompt/parse helpers, and
settle() driven by a fake engine. No Calibre, no network, no on-device model.
No framework:  uv run tests/test_synopsis.py   (also pytest-collectable)."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scourgify import common, setup as setup_mod, synopsis
from fixture_db import build

FFF_KEY = "namespaced:FanFicFarePlugin:settings"


def _con(prefs=None):
    d = tempfile.mkdtemp()
    con = build(os.path.join(d, "metadata.db"), [dict(id=1, added="2026-01-01 10:00:00")])
    if prefs is not None:
        con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, json.dumps(prefs)))
        con.commit()
    return con


def test_comments_protected_reads_the_real_fff_pref_shape():
    """Verified 2026-08-23 against the author's library: FFF stores per-library prefs as JSON in
    the preferences table under namespaced:FanFicFarePlugin:settings, and std_cols_newonly is a
    dict of standard-column -> bool. Readable through an ordinary read-only connection, which is
    what makes this guard automatic instead of an interactive confirm."""
    on = {"std_cols_newonly": {"comments": True, "title": True}, "custom_cols": {}}
    off = {"std_cols_newonly": {"comments": False, "title": True}, "custom_cols": {}}
    assert setup_mod.comments_protected(_con(on)) is True
    assert setup_mod.comments_protected(_con(off)) is False
    assert setup_mod.comments_protected(_con({"custom_cols": {}})) is False   # key absent = unprotected
    assert setup_mod.comments_protected(_con(None)) is None                   # FFF unconfigured here
    assert setup_mod.fff_settings(_con(None)) == {}


def test_comments_protected_survives_a_corrupt_prefs_blob():
    con = _con()
    con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, "{not json"))
    con.commit()
    assert setup_mod.fff_settings(con) == {}
    assert setup_mod.comments_protected(con) is None


def test_synopsized_is_a_recommended_column():
    assert ("#synopsized", "Synopsized", "datetime", False) in setup_mod.REC


# ---- chunking: sized for the apple engine's measured 4096-token ceiling ----
def test_chunks_fit_the_measured_context_window():
    """Measured 2026-08-23 against afm.swift with real EPUB prose: 17,000 chars pass, 18,000
    fail at 4,165 tokens against a hard limit of 4,096 (prompt AND answer share it). CHUNK must
    stay well under that — the prompt template and the model's own reply also have to fit."""
    assert synopsis.CHUNK <= 12_000
    parts = synopsis.chunks("x" * 25_000)
    assert [len(p) for p in parts] == [synopsis.CHUNK, synopsis.CHUNK, 25_000 - 2 * synopsis.CHUNK]
    assert "".join(parts) == "x" * 25_000                 # nothing lost when under the cap


def test_chunks_sample_evenly_once_past_the_cap_but_always_keep_the_opening():
    """A 500k-word fic is ~300 slabs; reading every one on-device costs ~10 minutes for ONE book.
    Past the cap we spread across the whole story — but slabs 0 and 1 are always in, because the
    premise, the cast and the hook (the back cover's whole job) live in the opening."""
    text = "".join(f"{i:05d}".ljust(synopsis.CHUNK, "y") for i in range(100))
    got = synopsis.chunks(text)
    assert len(got) == synopsis.MAX_CHUNKS
    idx = [int(p[:5]) for p in got]
    assert idx[:2] == [0, 1]                              # the opening, always
    assert idx == sorted(idx) and len(set(idx)) == len(idx)
    assert idx[-1] > 80                                   # ...and it reaches the end of the book


def test_chunks_of_nothing_is_nothing():
    assert synopsis.chunks("") == []


# ---- parsing ----
def test_clean_strips_the_lead_in_and_refuses_an_error_line():
    assert synopsis.clean("Here is the blurb: A boy meets a wolf.") == "A boy meets a wolf."
    assert synopsis.clean("Sure! Here's a back-cover summary:  A boy meets a wolf. ") == "A boy meets a wolf."
    assert synopsis.clean("A boy meets a wolf.") == "A boy meets a wolf."
    assert synopsis.clean('"A boy meets a wolf."') == "A boy meets a wolf."
    assert synopsis.clean("ERR: exceededContextWindowSize(...)") == ""
    assert synopsis.clean("") == "" and synopsis.clean(None) == ""


def test_verdict_is_three_state_because_guessing_is_wrong_in_both_directions():
    """Guessing 'keep' stamps a bad blurb as settled forever; guessing 'generate' AI-rewrites a
    healthy author-written one. An unreadable answer settles nothing — it becomes a failure row,
    which is self-clearing when the book is retried on another engine."""
    assert synopsis.verdict("YES") == "keep"
    assert synopsis.verdict("  yes.") == "keep"
    assert synopsis.verdict("NO") == "generate"
    assert synopsis.verdict("No, the description is just an author's note.") == "generate"
    assert synopsis.verdict("It depends on what you mean by adequate.") == ""
    assert synopsis.verdict("") == "" and synopsis.verdict(None) == ""


# ---- the pre-flight guard ----
def test_guard_refuses_when_fanficfare_would_clobber_the_synopsis():
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    try:
        synopsis.guard_comments(_con(off))
        assert False, "expected a GuardrailError"
    except common.GuardrailError as e:
        assert "New Only" in str(e) and "--force" in str(e)


def test_guard_passes_when_protected_unconfigured_or_forced():
    on = {"std_cols_newonly": {"comments": True}, "custom_cols": {}}
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    synopsis.guard_comments(_con(on))               # protected
    synopsis.guard_comments(_con(None))             # FFF unconfigured: nothing to clobber
    synopsis.guard_comments(_con(off), force=True)  # degraded self-healing mode, opted into


def test_the_guard_is_not_a_systemexit():
    """Job-reachable code may never raise SystemExit — Calibre's ThreadedJob catches only
    Exception, so it would kill the worker thread silently (CLAUDE.md, test_plugin_safety)."""
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    try:
        synopsis.guard_comments(_con(off))
    except common.GuardrailError:
        pass
    except SystemExit:
        assert False, "guard_comments must raise GuardrailError, never SystemExit"


# ---- settle(): one book, one fake engine ----
class FakeAsk:
    """prompt -> (text, err), keyed by what the prompt is asking for. Records every call so a
    test can assert the pass did NOT read the book when the blurb was already good."""
    def __init__(self, judge="YES", note="Notes about this excerpt.", back="A real back cover.", err=""):
        self.judge, self.note, self.back, self.err = judge, note, back, err
        self.calls = []

    def __call__(self, prompt):
        self.calls.append(prompt)
        if self.err: return "", self.err
        if "exactly one word" in prompt: return self.judge, ""
        if "Excerpt" in prompt: return self.note, ""
        return self.back, ""


GOOD_BLURB = "A sheriff who used to be a monster keeps the peace in a town of exiled fairy tales. " * 2


def _epub(text="the story went on and on. " * 900):
    """A tiny real EPUB (a zip of one XHTML member) so booktext.extract has something to read."""
    import zipfile
    p = os.path.join(tempfile.mkdtemp(), "b.epub")
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ch1.xhtml", f"<html><body><p>{text}</p></body></html>")
    return p


def test_an_adequate_blurb_is_kept_and_the_book_is_never_read():
    """Decision Q4: author voice is preserved on the ~7.5k healthy books. ('', '') means SETTLED
    with nothing to write — not a failure, and not an empty synopsis."""
    ask = FakeAsk(judge="YES")
    assert synopsis.settle("Fables", GOOD_BLURB, _epub(), ask) == ("", "")
    assert len(ask.calls) == 1                     # the judge only: no excerpts, no generation


def test_a_bad_blurb_triggers_whole_book_generation():
    ask = FakeAsk(judge="NO", back="Bigby Wolf keeps the peace in a town of exiled fairy tales, and "
                                   "hates every minute of it. Themes: redemption, noir, exile.")
    out, err = synopsis.settle("Fables", GOOD_BLURB, _epub(), ask)
    assert err == "" and out.startswith("Bigby Wolf keeps the peace")
    assert sum("Excerpt" in c for c in ask.calls) >= 1


def test_a_blurb_too_thin_to_judge_goes_straight_to_generation():
    ask = FakeAsk(back="A generated back cover, long enough to be worth storing in a library, with "
                       "a premise and a hook. Themes: exile, duty, noir.")
    out, err = synopsis.settle("Fables", "see inside", _epub(), ask)
    assert err == "" and out.startswith("A generated back cover")
    assert not any("exactly one word" in c for c in ask.calls)     # nothing to judge


def test_an_unreadable_file_is_a_failure_not_a_silent_skip():
    """Bucket B stays finite only if a book that cannot be read leaves the queue via the log."""
    out, err = synopsis.settle("Fables", "see inside", "/nonexistent/book.epub", FakeAsk())
    assert out == "" and "no readable text" in err


def test_an_engine_error_is_reported_verbatim_enough_to_act_on():
    out, err = synopsis.settle("Fables", "see inside", _epub(), FakeAsk(err="quota: 429 Too Many Requests"))
    assert out == "" and err.startswith("quota")


def test_an_unparseable_verdict_settles_nothing():
    ask = FakeAsk(judge="Well, it depends.")
    out, err = synopsis.settle("Fables", GOOD_BLURB, _epub(), ask)
    assert out == "" and "verdict" in err
    assert len(ask.calls) == 1                     # it did NOT fall through and rewrite a healthy blurb


def test_a_model_that_returns_nothing_usable_is_a_failure_not_an_empty_description():
    """The write path must never be handed '' for comments — that is a wipe, not a synopsis."""
    out, err = synopsis.settle("Fables", "see inside", _epub(), FakeAsk(back="ok"))
    assert out == "" and "no usable synopsis" in err



# ---- Plan: scope, the write contract, the failure log ----
import contextlib, io


@contextlib.contextmanager
def _env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try: yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


THIN = "see inside"
FAT = "A sheriff who used to be a monster keeps the peace in a town of exiled fairy tales. " * 2
PREFS_ON = {"std_cols_newonly": {"comments": True}, "custom_cols": {}}
BOOKS2 = [dict(id=1, added="2026-01-01 10:00:00", title="Good Blurb", desc=FAT),
          dict(id=2, added="2026-01-02 10:00:00", title="Thin Blurb", desc=THIN)]
BACK = ("A generated back cover, long enough to be worth storing in a library, with a premise "
        "and a hook. Themes: exile, duty, noir.")


@contextlib.contextmanager
def lib(books=BOOKS2, custom=None, prefs=PREFS_ON):
    """A throwaway library with real EPUB files on disk, so booktext.paths() resolves and
    extract() actually reads. NEVER the user's library — CALIBRE_LIBRARY points into a tempdir."""
    import zipfile
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "library"); os.makedirs(root)
        con = build(os.path.join(root, "metadata.db"), books,
                    custom=custom if custom is not None else [("updated", {}), ("synopsized", {})])
        if prefs is not None:
            con.execute("INSERT INTO preferences VALUES(?,?)", (FFF_KEY, json.dumps(prefs)))
        for b in books:
            d = os.path.join(root, f"b{b['id']}"); os.makedirs(d, exist_ok=True)
            con.execute("UPDATE books SET path=? WHERE id=?", (f"b{b['id']}", b["id"]))
            con.execute("INSERT INTO data VALUES(?,?,?)", (b["id"], "EPUB", "f"))
            with zipfile.ZipFile(os.path.join(d, "f.epub"), "w") as z:
                z.writestr("ch1.xhtml", "<html><body><p>%s</p></body></html>"
                           % ("the story went on and on. " * 900))
        con.commit(); con.close()
        with _env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=root):
            os.makedirs(common.data_dir(), exist_ok=True)
            yield


@contextlib.contextmanager
def recording_writer():
    """synopsis.run_writer replaced by a recorder — the write is asserted, never performed."""
    recorded = []
    saved = synopsis.run_writer
    synopsis.run_writer = lambda ops, force=False, **kw: recorded.append(ops)
    try: yield recorded
    finally: synopsis.run_writer = saved


def _syn_failures():
    from scourgify import artifacts
    return artifacts.read_rows(artifacts.syn_fail())


def test_a_bare_run_sends_nothing_and_writes_nothing():
    """The cost pin, and the lesson CLAUDE.md records the hard way about classify: a read-only
    check must be genuinely free. Here there is no proposal artifact to build, so a dry run is
    the queue report and nothing else — no engine call, no library write."""
    with lib():
        p = synopsis.plan(synopsis.default_opts())
        assert sorted(p.todo) == [1, 2]
        def boom(prompt): raise AssertionError("a dry run must reach no engine")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            p.run(ask=boom)
        assert "--apply" in buf.getvalue()


def test_apply_keeps_a_good_blurb_writes_a_bad_one_and_stamps_both():
    """The write contract in one assertion: comments is set ONLY for the generated book, while
    #synopsized stamps every book the run settled — a kept blurb is settled too, and an unstamped
    one would be re-read and re-judged on every future sweep."""
    with lib(), recording_writer() as recorded:
        p = synopsis.plan(synopsis.default_opts(apply=True))
        with contextlib.redirect_stdout(io.StringIO()):
            p.run(ask=FakeAsk(judge="YES", back=BACK))
    (ops,) = recorded
    # book ids stringify through op_set_field (ops.coerce puts both write paths back on int)
    sets = {o["field"]: {int(b): v for b, v in o["values"].items()}
            for o in ops if o["op"] == "set_field"}
    assert list(sets["comments"]) == [2]                    # only the thin-blurb book is rewritten
    assert sets["comments"][2].startswith("A generated back cover")
    (stamp,) = [o for o in ops if o["op"] == "stamp_now"]
    assert stamp["field"] == "#synopsized" and sorted(stamp["books"]) == [1, 2]
    assert not [o for o in ops if o["op"] == "create_column"]      # the column already exists


def test_the_stamp_column_is_created_on_first_run():
    with lib(custom=[("updated", {})]), recording_writer() as recorded:
        p = synopsis.plan(synopsis.default_opts(apply=True))
        with contextlib.redirect_stdout(io.StringIO()):
            p.run(ask=FakeAsk(judge="YES", back=BACK))
    (ops,) = recorded
    assert ops[0]["op"] == "create_column" and ops[0]["label"] == "synopsized"


def test_a_failed_book_lands_in_the_failure_log_and_is_not_stamped():
    """Not stamped, because a blocked book must stay retryable; in the log, because otherwise it
    re-occupies the head of every batch forever."""
    with lib([BOOKS2[1]]), recording_writer() as recorded:
        p = synopsis.plan(synopsis.default_opts(apply=True))
        with contextlib.redirect_stdout(io.StringIO()):
            p.run(ask=FakeAsk(err="quota: 429"))
        rows = _syn_failures()
        assert [int(r["book_id"]) for r in rows] == [2]
        assert "quota" in rows[0]["reason"]
    assert recorded == []                                   # nothing settled -> nothing written


def test_the_failure_log_clears_when_a_book_later_succeeds():
    """Self-clearing, like classify's: a book recovered on another engine must LEAVE the list or
    it reads as blocked forever — and would stay out of the queue forever with it."""
    with lib([BOOKS2[1]]), recording_writer():
        with contextlib.redirect_stdout(io.StringIO()):
            synopsis.plan(synopsis.default_opts(apply=True)).run(ask=FakeAsk(err="quota: 429"))
            assert len(_syn_failures()) == 1
            synopsis.plan(synopsis.default_opts(apply=True, books="2")).run(ask=FakeAsk(back=BACK))
        assert _syn_failures() == []


def test_batch_caps_the_run_newest_first():
    with lib():
        p = synopsis.plan(synopsis.default_opts(batch=1))
        assert p.todo == [2]


def test_named_books_are_re_settled_whatever_the_stamp_says():
    """--books is the targeted redo: it bypasses the queue, so a settled book can be re-done."""
    with lib(custom=[("updated", {}), ("synopsized", {1: "2030-01-01 00:00:00+00:00"})]):
        assert 1 not in synopsis.plan(synopsis.default_opts()).todo      # settled
        assert synopsis.plan(synopsis.default_opts(books="1")).todo == [1]


def test_the_guard_fires_before_any_work():
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    with lib(prefs=off):
        try:
            synopsis.plan(synopsis.default_opts(apply=True))
            assert False, "expected the pre-flight guard to refuse"
        except common.GuardrailError as e:
            assert "New Only" in str(e)
    with lib(prefs=off):
        synopsis.plan(synopsis.default_opts(apply=True, force=True))   # degraded mode, opted into


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
