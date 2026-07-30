#!/usr/bin/env python3
"""Characterization tests for the classify run pipeline — plan → run → apply — through the
module-attribute seams (a fake engine in ENGINES, a recording run_writer) against the throwaway
fixture db, with every artifact under a $SCOURGIFY_HOME temp. Written to pin behavior BEFORE the
Plan-object refactor: whatever these assert must survive it.
No framework:  uv run tests/test_classify_run.py   (also pytest-collectable)."""
import contextlib, os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from fixture_db import build
from scourgify import artifacts, classify, common
from scourgify.engines import ENGINES


@contextlib.contextmanager
def env(**kv):
    saved = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


@contextlib.contextmanager
def harness(books, custom=(("wrangled", {}),)):
    """Fixture library + redirected home; vocab caches cleared both ways."""
    with tempfile.TemporaryDirectory() as td:
        lib = os.path.join(td, "library"); os.makedirs(lib)
        build(os.path.join(lib, "metadata.db"), books, custom=custom).close()
        with env(SCOURGIFY_HOME=os.path.join(td, "home"), CALIBRE_LIBRARY=lib):
            os.makedirs(common.data_dir())
            classify.clear_caches()
            try:
                yield td
            finally:
                classify.clear_caches()


DESC = "A long enough description for the classifier to consider this book usable. " * 2


class FakeEngine:
    """Answers by marker planted in each book's description (the prompt embeds it)."""
    def __init__(self, model, timeout): pass

    def ask(self, prompt):
        if "MARKER-ERR" in prompt: raise RuntimeError("blocked:TEST")
        if "MARKER-HIT" in prompt:
            return '{"tags": ["%s"], "new": ["Sentient Toaster Romance"]}' % classify.load_vocab()[0]
        return "no json here"                       # parses to a no-match ([], [])


def test_plan_resolves_scope_and_resume_once():
    """plan(): targets from the scope, todo = targets minus books already in the proposal
    (the resume rule) — resolved once, priced once."""
    books = [{"id": i, "added": f"2026-01-0{i} 10:00:00", "desc": DESC, "tags": ["t"]} for i in (1, 2, 3)]
    with harness(books):
        artifacts.write_proposal([{"book_id": 2, "title": "b2", "added_tags": ["X"], "proposed_new": []}])
        p = classify.plan(classify.default_opts())            # sparse default: no scope flag
        assert {b for b, _ in p.targets} == {1, 2, 3}
        assert {b for b, _ in p.todo} == {1, 3}               # book 2 rides the existing proposal
        assert p.proposal[2] == (["X"], []) and 2 in p.done


def test_plan_is_isolated_from_later_caller_mutation():
    """The old bare-dict plan kept the caller's Namespace by identity — mutating it after
    planning silently steered the run. The Plan owns a copy: callers steer through p.opts."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00", "desc": DESC}]
    with harness(books):
        a = classify.default_opts()
        p = classify.plan(a)
        a.engine = "gemini"                                   # caller keeps mutating its own namespace…
        assert p.opts.engine == "apple"                       # …the resolved plan doesn't move
        p.opts.engine = "claude"                              # the sanctioned way: through the plan
        assert p.opts.engine == "claude" and a.engine == "gemini"


def test_run_records_hits_no_matches_and_failures():
    """classify_run: proposal rows for every non-errored book (a no-match row still stamps later),
    failures CSV for errored books (they retry), ranked candidates from proposed_new. The engine
    chosen AFTER planning (the wizard's flow) is the one used."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00", "desc": DESC + " MARKER-HIT"},
             {"id": 2, "added": "2026-01-02 10:00:00", "desc": DESC + " MARKER-MISS"},
             {"id": 3, "added": "2026-01-03 10:00:00", "desc": DESC + " MARKER-ERR"}]
    with harness(books):
        ENGINES["fake"] = FakeEngine
        try:
            p = classify.plan(classify.default_opts())
            p.opts.engine = "fake"                            # chosen after planning, like the wizard
            classify.classify_run(p)                          # accepts a Plan (or a bare Namespace)
        finally:
            del ENGINES["fake"]
        rows = {r["book_id"]: r for r in artifacts.read_proposal()}
        assert rows[1]["added_tags"] == [classify.load_vocab()[0]]
        assert rows[1]["proposed_new"] == ["Sentient Toaster Romance"]
        assert rows[2]["added_tags"] == [] and rows[2]["proposed_new"] == []   # recorded, not dropped
        assert 3 not in rows                                  # errored: excluded so a retry re-sends it
        fails = artifacts.read_rows(artifacts.fail())
        assert [r["book_id"] for r in fails] == ["3"] and "blocked:TEST" in fails[0]["reason"]
        ranked = artifacts.read_ranked()
        assert [(r["proposed_tag"], r["count"]) for r in ranked if r["verdict"] == "new"] \
               == [("Sentient Toaster Romance", 1)]


def test_apply_proposal_ops_union_stamp_archive():
    """apply_proposal: ONE set_field op unioning proposed tags with the book's current tags,
    ONE stamp of every processed book (tagged or not), then the proposal archives so stale rows
    can never re-apply."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00", "tags": ["Old"]},
             {"id": 2, "added": "2026-01-02 10:00:00"}]
    with harness(books):
        artifacts.write_proposal([{"book_id": 1, "title": "b1", "added_tags": ["New"], "proposed_new": []},
                                  {"book_id": 2, "title": "b2", "added_tags": [], "proposed_new": []}])
        recorded = []
        saved = classify.run_writer
        classify.run_writer = lambda ops, force=False: recorded.append(ops)
        try:
            classify.apply_proposal()
        finally:
            classify.run_writer = saved
        (ops,) = recorded
        assert common.op_set_field("tags", {1: ["New", "Old"]}) in ops       # union, sorted
        assert common.op_stamp_now("#wrangled", [1, 2]) in ops               # BOTH books stamped
        assert not any(o["op"] == "create_column" for o in ops)              # column already exists
        assert not os.path.exists(artifacts.prop()) and len(artifacts.applied_proposals()) == 1


def test_step_review_archives_only_the_books_it_applied():
    """An *_applied_* archive is the durable record of "this book was classified" — scopes read it
    back to decide what is still outstanding. So a book the user SKIPPED (or everything after a
    quit) must never appear in one: filing it there retires the book while nothing reached the
    library. Quitting on book 2 of 200 used to archive all 200 as applied."""
    from scourgify import ui
    books = [{"id": i, "added": f"2026-01-0{i} 10:00:00", "desc": DESC, "tags": []} for i in (1, 2, 3)]
    with harness(books, custom=(("wrangled", {}),)):
        artifacts.write_proposal([{"book_id": 1, "title": "b1", "added_tags": ["Keep"], "proposed_new": []},
                                  {"book_id": 2, "title": "b2", "added_tags": ["Skipped"], "proposed_new": []},
                                  {"book_id": 3, "title": "b3", "added_tags": ["AfterQuit"], "proposed_new": []}])
        saved_w, saved_c, saved_i = classify.run_writer, ui.checklist, ui.interactive
        # book 1 accepted, book 2 skipped, book 3 never reached (quit)
        answers = iter([([0], [], "apply"), ([], [], "skip"), ([], [], "quit")])
        classify.run_writer = lambda ops, force=False: None
        ui.checklist = lambda *a, **k: next(answers)
        ui.interactive = lambda: True
        try:
            classify.apply_proposal_step()
        finally:
            classify.run_writer, ui.checklist, ui.interactive = saved_w, saved_c, saved_i

        (arch,) = artifacts.applied_proposals()
        applied = {r["book_id"] for r in artifacts.read_proposal(arch)}
        assert applied == {1}, applied                       # ONLY the book that was written
        pending = {r["book_id"] for r in artifacts.read_proposal()}
        assert pending == {2, 3}, pending                    # skipped + post-quit stay pending


def test_run_accepts_injected_ask():
    """Plan.run(ask=) — the injected prompt->(text, err) seam, like promote.run's ask=."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00", "desc": DESC}]
    with harness(books):
        p = classify.plan(classify.default_opts())
        p.run(ask=lambda prompt: ('{"tags": [], "new": ["Injected Tag"]}', ""))
        (row,) = artifacts.read_proposal()
        assert row["book_id"] == 1 and row["proposed_new"] == ["Injected Tag"]


def test_apply_proposal_skips_rows_for_deleted_books():
    """A proposal row can outlive its book (deleted / re-imported with a new id since the run).
    Shipping the dead id to Calibre dies with a foreign-key violation mid-write — stale rows are
    skipped (visibly), never sent."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00"}]
    with harness(books):
        artifacts.write_proposal([{"book_id": 1, "title": "b1", "added_tags": ["New"], "proposed_new": []},
                                  {"book_id": 99, "title": "gone", "added_tags": ["X"], "proposed_new": []}])
        recorded = []
        saved = classify.run_writer
        classify.run_writer = lambda ops, force=False: recorded.append(ops)
        try:
            classify.apply_proposal()
        finally:
            classify.run_writer = saved
        (ops,) = recorded
        assert common.op_set_field("tags", {1: ["New"]}) in ops              # live book applies
        assert common.op_stamp_now("#wrangled", [1]) in ops                  # stale id 99 not stamped
        assert not any("99" in str(o.get("values", {})) or 99 in (o.get("books") or []) for o in ops)


def test_books_scope_selects_exactly_those_books():
    """--books names books directly — no stamp state, no tag-count heuristic. Ids not in the
    library are dropped (and counted), never fatal."""
    books = [{"id": i, "added": f"2026-01-0{i} 10:00:00", "desc": DESC, "tags": ["t", "u"]}
             for i in (1, 2, 3)]
    with harness(books):
        p = classify.plan(classify.default_opts(books="3,1,99"))
        assert {b for b, _ in p.targets} == {1, 3}            # book 2 untouched, 99 dropped
        assert [b for b, _ in p.targets] == [3, 1]            # newest-added-first


def test_books_scope_counts_as_explicit_so_resume_reprocesses():
    """A --books book already sitting in the proposal is re-processed, like every other explicit
    scope — the user asked for it by id."""
    books = [{"id": 1, "added": "2026-01-01 10:00:00", "desc": DESC}]
    with harness(books):
        artifacts.write_proposal([{"book_id": 1, "title": "b1", "added_tags": ["X"], "proposed_new": []}])
        p = classify.plan(classify.default_opts(books="1"))
        assert [b for b, _ in p.todo] == [1] and not p.done


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
