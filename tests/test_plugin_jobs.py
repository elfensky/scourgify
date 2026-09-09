#!/usr/bin/env python3
"""Qt-free unit coverage of the PLAN/EXECUTE job bodies in plugin/jobs.py (D-12). No framework:
  uv run tests/test_plugin_jobs.py   (also pytest-collectable). No Calibre, no GUI, no network.

What breaks in the real world if these fail: the plugin's write path (PLAN job -> picker -> Run ->
EXECUTE job -> common.write_ops -> result dialog) is only ever exercised by hand, in a real
Calibre, and a regression ships silently.
"""
import json
import os
import sys
import tempfile
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# jobs.py is Qt-free (D-12) and importable as a plain top-level module — inserting plugin/ onto
# sys.path (rather than the repo root) avoids ever executing plugin/__init__.py, which is the
# real InterfaceActionBase wrapper Calibre loads and has no business running under plain CI Python.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin"))

import jobs                                         # noqa: E402 — plugin/jobs.py as top-level `jobs`
import fixture_db                                    # noqa: E402
from scourgify import common                          # noqa: E402
import test_write_path as _test_write_path             # noqa: E402 — the shared FakeApi shape


FakeApi = _test_write_path.FakeApi
DESC = "A long enough description for the classifier to consider this book usable. " * 2


def _classify_lib(n, wrangled=True, stamped=None):
    """A throwaway library of n sendable (long-description) books, optionally missing the
    #wrangled column (to drive the missing-column pre-flight refusal). `stamped` (a
    {book_id: datetime-string} dict) pre-stamps specific books as already classified."""
    d = tempfile.mkdtemp()
    books = [{"id": i, "added": "2026-01-01 %02d:%02d:%02d" % ((i // 3600) % 24, (i // 60) % 60, i % 60),
             "desc": DESC} for i in range(1, n + 1)]
    custom = [("wrangled", dict(stamped or {}))] if wrangled else []
    con = fixture_db.build(os.path.join(d, "metadata.db"), books, custom=custom)
    con.commit()
    con.close()
    return d


def _lib(n=2):
    """A throwaway library: book 1 In-Progress and stale (re-derives to Abandoned), book 2
    Completed (never in the activity family, never changes)."""
    old = (datetime.date.today() - datetime.timedelta(days=365 * 6)).isoformat()
    d = tempfile.mkdtemp()
    con = fixture_db.build(os.path.join(d, "metadata.db"),
                           [{"id": 1, "title": "Book One"}, {"id": 2, "title": "Book Two"}][:n],
                           custom=[("status", {1: "In-Progress", 2: "Completed"}),
                                   ("updated", {1: old, 2: old})])
    con.commit()
    con.close()
    return d


def _pointed_at(lib):
    """Context manager: CALIBRE_LIBRARY + SCOURGIFY_HOME point at a throwaway library/home for the
    duration, restored on exit — same shape as tests/test_write_path.py's own helper."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        home = tempfile.mkdtemp()
        old = {k: os.environ.get(k) for k in ("CALIBRE_LIBRARY", "SCOURGIFY_HOME")}
        os.environ["CALIBRE_LIBRARY"], os.environ["SCOURGIFY_HOME"] = lib, home
        try:
            yield home
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    return _cm()


# ---------------- PLAN ----------------
def test_job_plan_staleness_returns_the_expected_items_and_consequence():
    lib = _lib()
    with _pointed_at(lib):
        r = jobs.job_plan_staleness(lib, None, [1, 2])
    assert r["refused"] is False and r["empty"] is False
    assert r["consequence"] == "Re-derive status on 1 book"
    assert len(r["items"]) == 1
    label, payload = r["items"][0]
    assert payload["book"] == 1 and payload["before"] == "In-Progress" and payload["after"] == "Abandoned"
    assert payload["field"] == "#status"
    carry_row = r["carry"]["rows"][0]
    assert carry_row[:3] == (1, "In-Progress", "Abandoned") and carry_row[3] > 5.0   # age in years
    assert r["carry"]["status_label"] == "#status"


def test_an_empty_id_list_returns_the_d04_empty_result_with_no_library_read():
    lib = _lib()
    with _pointed_at(lib):
        r = jobs.job_plan_staleness(lib, None, [])
    assert r["empty"] is True and r["items"] == [] and r["refused"] is False


def test_a_selection_with_nothing_to_change_is_also_the_empty_result():
    lib = _lib()
    with _pointed_at(lib):
        r = jobs.job_plan_staleness(lib, None, [2])   # book 2: Completed, never in the activity family
    assert r["empty"] is True and r["items"] == []


# ---------------- EXECUTE ----------------
def test_job_execute_staleness_writes_through_write_ops_and_the_fake_apis_state_changes():
    lib = _lib()
    with _pointed_at(lib):
        plan = jobs.job_plan_staleness(lib, None, [1, 2])
        api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
        api.set_field("#status", {1: "In-Progress"})
        result = jobs.job_execute_staleness(lib, None, [1, 2], plan["carry"], api)
    assert result["refused"] is False
    assert result["written"] == 1
    assert result["skipped"] == []
    assert result["touched"] == [1]
    assert api.fields["#status"][1] == "Abandoned"
    row = result["rows"][0]
    assert row == {"book": 1, "title": "Book One", "field": "#status",
                   "before": "In-Progress", "after": "Abandoned", "state": "written"}
    assert result["outcome"] == "ok" and result["run_id"]


def test_a_second_identical_execute_reports_every_row_skipped_since_the_plan():
    lib = _lib()
    with _pointed_at(lib):
        plan = jobs.job_plan_staleness(lib, None, [1, 2])
        api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
        api.set_field("#status", {1: "In-Progress"})
        first = jobs.job_execute_staleness(lib, None, [1, 2], plan["carry"], api)
        second = jobs.job_execute_staleness(lib, None, [1, 2], plan["carry"], api)
    assert first["written"] == 1
    assert second["written"] == 0
    assert second["touched"] == []
    assert all(r["state"] == "skipped: changed since the plan" for r in second["rows"])
    assert second["skipped"] == [[1, "#status"]]


def test_a_guardrail_error_from_the_writer_becomes_a_refused_result_with_zero_rows():
    lib = _lib()
    saved = common.write_ops

    def _boom(*a, **k):
        raise common.GuardrailError("ABORT: a wrangle run started at ... is already writing this library")
    common.write_ops = _boom
    try:
        with _pointed_at(lib):
            plan = jobs.job_plan_staleness(lib, None, [1, 2])
            api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
            result = jobs.job_execute_staleness(lib, None, [1, 2], plan["carry"], api)
    finally:
        common.write_ops = saved
    assert result["refused"] is True
    assert "already writing this library" in result["msg"]
    assert "rows" not in result or result.get("rows", []) == []


# ---------------- the result contract ----------------
def test_the_plan_and_execute_results_survive_json_dumps():
    lib = _lib()
    with _pointed_at(lib):
        plan = jobs.job_plan_staleness(lib, None, [1, 2])
        api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
        api.set_field("#status", {1: "In-Progress"})
        result = jobs.job_execute_staleness(lib, None, [1, 2], plan["carry"], api)
    json.dumps(plan)
    json.dumps(result)


def test_result_rows_are_ordered_by_book_then_field():
    lib = _lib()
    with _pointed_at(lib):
        plan = jobs.job_plan_staleness(lib, None, [1, 2])
        api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
        api.set_field("#status", {1: "In-Progress"})
        result = jobs.job_execute_staleness(lib, None, [1, 2], plan["carry"], api)
    pairs = [(r["book"], r["field"]) for r in result["rows"]]
    assert pairs == sorted(pairs)


# ---------------- editlog.conflict is set-wise (multi) / string-wise-with-None==""  (single) ----
def test_a_reordered_multi_value_is_applied_not_skipped():
    """editlog.conflict compares multi-value fields as SETS — a tags reorder (Calibre's own
    doing, not a user edit) must not read as a conflict. Drives jobs._Writer directly, the same
    transport every EXECUTE job (job_execute_staleness included) binds `write=` to."""
    lib = _lib()
    with _pointed_at(lib):
        api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
        api.set_field("tags", {1: ("Isekai", "Harem")})    # same members, different order
        writer = jobs._Writer(api)
        writer([common.op_set_field("tags", {1: ["New"]}, expected={1: ["Harem", "Isekai"]})])
    assert writer.result.skipped == []
    assert api.fields["tags"][1] == ("New",)


def test_single_value_none_and_empty_string_compare_equal():
    """editlog.conflict's single-value side: `None` (never set) and `""` (planned against an
    empty value) are the same absence, not a conflict."""
    lib = _lib()
    with _pointed_at(lib):
        api = FakeApi(books=(1, 2), fields=("tags", "#status"), multi=("tags",))
        writer = jobs._Writer(api)
        writer([common.op_set_field("#status", {1: "Hiatus"}, expected={1: ""})])
    assert writer.result.skipped == []
    assert api.fields["#status"][1] == "Hiatus"


# ---------------- classify: the highest-stakes verb (plan 02-03) ----------------
def test_job_plan_classify_prices_over_the_resolved_todo_set_not_the_selection():
    """The engine picker's price must be computed over len(todo), never the selection size that
    triggered the menu — a 1-book selection choosing the whole-library scope must price 5 books."""
    lib = _classify_lib(5)
    saved_sk = jobs.stored_keys
    jobs.stored_keys = lambda: {}      # PLAN prices every USABLE engine — never reach real JSONConfig
    try:
        with _pointed_at(lib):
            r = jobs.job_plan_classify(lib, None, [1], {"mode": "all"})
    finally:
        jobs.stored_keys = saved_sk
    assert r["refused"] is False and r["empty"] is False
    assert r["carry"]["n_todo"] == 5
    assert any("for 5 books" in row[3] for row in r["engines"]), r["engines"]
    assert r["default_engine"]
    # the engine picker (Qt-only, no core import) needs BOTH handed to it as plain data:
    # usability (engine_options' own tuple carries none) and the TRAITS 'limits' failure-mode text.
    assert set(r["engine_limits"]) == {eid for _key, eid, _eid2, _label in r["engines"]}
    assert all(r["engine_limits"].values())            # every engine states how it will let you down
    assert isinstance(r["usable"], list)


def test_job_plan_classify_unclassified_shortcut_restricts_to_the_selection():
    """'Classify the never-classified here' on a mixed selection: only the SELECTED books that
    are actually never-classified enter the todo set — never the whole-library backlog, and
    never a book outside the selection."""
    from scourgify import artifacts
    lib = _classify_lib(5)
    saved_sk = jobs.stored_keys
    jobs.stored_keys = lambda: {}
    try:
        with _pointed_at(lib):
            common.set_library(lib)      # _LIBRARY may still point at a prior test's library
            os.makedirs(common.data_dir(), exist_ok=True)
            # book 1 already attempted (a pending proposal row) — the other four are still backlog
            artifacts.write_proposal([{"book_id": 1, "title": "", "added_tags": [], "proposed_new": []}])
            r = jobs.job_plan_classify(lib, None, [1, 2, 3], {"mode": "unclassified", "batch": None,
                                                              "restrict_to_selection": True})
    finally:
        jobs.stored_keys = saved_sk
    assert r["refused"] is False and r["empty"] is False
    # book 1 (already attempted) and books 4/5 (outside the selection) are both excluded; order
    # is newest-added-first (select.pick's own ordering), not the selection's own order.
    assert set(r["carry"]["todo_ids"]) == {2, 3}


def test_job_plan_classify_unclassified_without_a_selection_is_the_whole_backlog():
    """The ScopeDialog's OWN 'never classified' row never sets restrict_to_selection — it means
    the whole-library backlog, matching its label's count, regardless of what is selected."""
    lib = _classify_lib(3)
    saved_sk = jobs.stored_keys
    jobs.stored_keys = lambda: {}
    try:
        with _pointed_at(lib):
            r = jobs.job_plan_classify(lib, None, [], {"mode": "unclassified", "batch": None})
    finally:
        jobs.stored_keys = saved_sk
    assert r["carry"]["n_todo"] == 3


def test_job_plan_classify_returns_the_d04_empty_result_when_nothing_is_outstanding():
    lib = _classify_lib(0, wrangled=True)
    with _pointed_at(lib):
        r = jobs.job_plan_classify(lib, None, [], {"mode": "all"})
    assert r["empty"] is True and r["refused"] is False


def test_job_plan_classify_refuses_cleanly_without_the_wrangled_column():
    """Pitfall 3/5/6 (02-RESEARCH.md): a library that has never run `scourgify setup` must refuse
    with a clean GuardrailError-derived result, never a bare ValueError from ops.apply_ops."""
    from scourgify import artifacts
    lib = _classify_lib(3, wrangled=False)
    with _pointed_at(lib):
        r = jobs.job_plan_classify(lib, None, [], {"mode": "all"})
        assert r["refused"] is True
        assert "#wrangled" in r["msg"] and "scourgify setup" in r["msg"]
        assert not os.path.exists(artifacts.prop()), "a refused PLAN must write nothing"


def test_engine_ask_reaches_a_stored_only_key_never_touching_the_environment():
    """The single most likely 'works for me' bug this phase can ship (02-RESEARCH.md Pattern 3 /
    Pitfall 1): a key typed into the settings dialog and never exported must reach the engine
    _engine_ask constructs, and os.environ must stay untouched."""
    from scourgify import engines
    saved_engine = engines.ENGINES.get("openai")
    saved_env = os.environ.pop("OPENAI_API_KEY", None)
    seen = {}

    class Capture:
        def __init__(self, model, timeout, env=None):
            seen["env"] = dict(env or {})

        def ask(self, prompt):
            return "ok"

    engines.ENGINES["openai"] = Capture
    saved_sk = jobs.stored_keys
    jobs.stored_keys = lambda: {"openai": "sk-from-the-settings-dialog"}
    try:
        ask = jobs._engine_ask("openai", "", 30)
        out, err = ask("ping")
        env_during = os.environ.get("OPENAI_API_KEY")     # captured BEFORE the finally restores it
    finally:
        engines.ENGINES["openai"] = saved_engine
        jobs.stored_keys = saved_sk
        if saved_env is not None:
            os.environ["OPENAI_API_KEY"] = saved_env
    assert out == "ok" and err == ""
    assert seen["env"].get("OPENAI_API_KEY") == "sk-from-the-settings-dialog"
    assert env_during is None, "the environment must be left alone"


class _FastNoMatchEngine:
    """A trivial engine: no vocab hits, no proposed-new — fast, deterministic, no network."""
    def __init__(self, model, timeout, env=None):
        pass

    def ask(self, prompt):
        return '{"tags": [], "new": []}'


def test_job_execute_classify_sets_yes_true_so_the_spend_gate_is_never_reached():
    """The scope step already answered classify.spend_gate — a 201-book todo (above SPEND_GATE)
    on a non-free engine must start with no prompt and no GuardrailError."""
    from scourgify import classify, engines
    lib = _classify_lib(201)
    saved_engine = engines.ENGINES.get("openai")
    saved_sk = jobs.stored_keys
    engines.ENGINES["openai"] = _FastNoMatchEngine
    jobs.stored_keys = lambda: {"openai": "sk-test"}     # _engine_ask must never reach real JSONConfig
    try:
        with _pointed_at(lib), _test_write_path._fake_calibre_utils_date():
            assert classify.SPEND_GATE < 201
            plan = jobs.job_plan_classify(lib, None, [], {"mode": "all"})
            assert plan["refused"] is False and plan["carry"]["n_todo"] == 201
            api = FakeApi(books=tuple(range(1, 202)), fields=("tags", "#wrangled"), multi=("tags",))
            result = jobs.job_execute_classify(lib, None, [], plan["carry"], "openai", "", api)
    finally:
        engines.ENGINES["openai"] = saved_engine
        jobs.stored_keys = saved_sk
    assert result["refused"] is False
    assert result["outcome"] == "ok"


class _AbortAfter:
    """A Calibre-shaped abort stub: `.is_set()` flips True once called more than `after` times —
    mirrors the counting shape Plan.run's own `stop=` seam is pinned against."""
    def __init__(self, after):
        self.n = 0
        self.after = after

    def is_set(self):
        self.n += 1
        return self.n > self.after


def test_job_execute_classify_closes_a_cancelled_run_as_cancelled_and_still_applies_it():
    from scourgify import engines
    lib = _classify_lib(5)
    saved_engine = engines.ENGINES.get("openai")
    saved_sk = jobs.stored_keys
    engines.ENGINES["openai"] = _FastNoMatchEngine
    jobs.stored_keys = lambda: {"openai": "sk-test"}
    try:
        with _pointed_at(lib), _test_write_path._fake_calibre_utils_date():
            plan = jobs.job_plan_classify(lib, None, [], {"mode": "all"})
            assert plan["carry"]["n_todo"] == 5
            api = FakeApi(books=(1, 2, 3, 4, 5), fields=("tags", "#wrangled"), multi=("tags",))
            result = jobs.job_execute_classify(lib, None, [], plan["carry"], "openai", "", api,
                                               abort=_AbortAfter(2))
    finally:
        engines.ENGINES["openai"] = saved_engine
        jobs.stored_keys = saved_sk
    assert result["refused"] is False
    assert result["outcome"] == "cancelled"
    assert result["run_id"], "the applied ops must stay logged and undoable"


def test_a_missing_wrangled_column_refuses_execute_too():
    from scourgify import engines
    lib = _classify_lib(2, wrangled=False)
    saved_engine = engines.ENGINES.get("openai")
    engines.ENGINES["openai"] = _FastNoMatchEngine
    try:
        with _pointed_at(lib):
            api = FakeApi(books=(1, 2), fields=("tags", "#wrangled"), multi=("tags",))
            result = jobs.job_execute_classify(lib, None, [], {"todo_ids": [1, 2]}, "openai", "", api)
    finally:
        engines.ENGINES["openai"] = saved_engine
    assert result["refused"] is True
    assert "#wrangled" in result["msg"]


# ---------------- the generic decide= helpers (D-02, plan 02-05) ----------------
def test_record_decide_grows_one_call_per_invocation_in_order():
    decide, calls = jobs._record_decide()
    acc1, rej1, action1 = decide("book B", [("a", {"x": 1}), ("b", {"x": 2})], subtitle="1/2")
    acc2, rej2, action2 = decide("book A", [("c", {"x": 3})])
    assert acc1 == [] and rej1 == [0, 1] and action1 == "skip"
    assert acc2 == [] and rej2 == [0] and action2 == "skip"
    assert [c["title"] for c in calls] == ["book B", "book A"]
    assert calls[0]["items"] == [("a", {"x": 1}), ("b", {"x": 2})]
    assert calls[0]["subtitle"] == "1/2"


def test_replay_decide_pops_ticks_in_call_order_and_falls_back_to_accept_everything():
    decide = jobs._replay_decide([([], [0, 1], "skip"), ([0], [1], "apply")])
    assert decide("t1", [1, 2]) == ([], [0, 1], "skip")
    assert decide("t2", [1, 2]) == ([0], [1], "apply")
    # exhausted -> accept-everything fallback, whatever the items list looks like
    assert decide("t3", ["x", "y", "z"]) == ([0, 1, 2], [], "apply")


def test_replay_decide_of_an_empty_ticks_list_always_accepts_everything():
    decide = jobs._replay_decide([])
    acc, rej, action = decide("title", [("a", {}), ("b", {})])
    assert acc == [0, 1] and rej == [] and action == "apply"


# ---------------- wrangle (plan 02-05) ----------------
def _wrangle_lib(books):
    """A throwaway library for wrangle tests. `books`: [{"id":, "tags":[...], "fandoms": [...]}]."""
    d = tempfile.mkdtemp()
    fandoms = {b["id"]: b["fandoms"] for b in books if b.get("fandoms")}
    custom = [("fandoms", fandoms)] if fandoms else []
    con = fixture_db.build(os.path.join(d, "metadata.db"),
                           [{"id": b["id"], "title": b.get("title", "book %d" % b["id"]),
                             "tags": b.get("tags", [])} for b in books],
                           custom=custom)
    con.commit(); con.close()
    return d


def _write_override(home, name, lines):
    path = os.path.join(home, "overrides", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def test_job_plan_wrangle_returns_the_d04_empty_result_for_an_already_normalized_library():
    lib = _wrangle_lib([{"id": 1, "title": "Plain Book"}])
    with _pointed_at(lib):
        r = jobs.job_plan_wrangle(lib, None, [1])
    assert r["refused"] is False and r["empty"] is True and r["items"] == []


def test_job_plan_wrangle_produces_per_book_reviewable_items_in_step_walks_own_order():
    """Two books, each with the SAME single junk-drop edit — MASS_MIN is 3, so at 2 books this
    stays a per-book 'unique' edit and is reviewable, in `_step_walk`'s own newest-id-first order."""
    lib = _wrangle_lib([{"id": 1, "tags": ["ZWrangleJunk"]}, {"id": 2, "tags": ["ZWrangleJunk"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "junk.txt", ["zwranglejunk"])
        r = jobs.job_plan_wrangle(lib, None, [1, 2])
    assert r["refused"] is False and r["empty"] is False
    assert r["consequence"] == "Normalize fields on 2 books"
    assert "SAFETY" in r["safety"]
    assert len(r["items"]) == 2
    books = [payload["book"] for _label, payload in r["items"]]
    assert books == [2, 1]                                  # newest id first
    for _label, payload in r["items"]:
        assert payload["kind"] == "drop" and payload["before"] == "ZWrangleJunk"
        assert payload["field"] == "tags"
    assert r["carry"] == {"ids": [1, 2], "n_books": 2}


def test_job_plan_wrangle_a_fandom_emptying_change_set_is_refused_with_no_dialog():
    lib = _wrangle_lib([{"id": 1, "fandoms": ["ZGhostFandom"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "fandoms.csv", ["alias,canonical", "ZGhostFandom,"])
        r = jobs.job_plan_wrangle(lib, None, [1])
    assert r["refused"] is True
    assert "last fandom" in r["msg"]
    assert r.get("items", []) == []


def test_job_execute_wrangle_writes_through_write_ops_and_the_fake_apis_state_changes():
    lib = _wrangle_lib([{"id": 1, "tags": ["ZWrangleJunk"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "junk.txt", ["zwranglejunk"])
        plan = jobs.job_plan_wrangle(lib, None, [1])
        api = FakeApi(books=(1,), fields=("tags",), multi=("tags",))
        api.set_field("tags", {1: ("ZWrangleJunk",)})
        result = jobs.job_execute_wrangle(lib, None, [1], plan["carry"], [], api)
    assert result["refused"] is False
    assert result["written"] == 1
    assert result["touched"] == [1]
    assert api.fields["tags"][1] == ()


def test_job_execute_wrangle_a_skipped_book_is_deferred_and_writes_no_reject_row():
    lib = _wrangle_lib([{"id": 1, "tags": ["ZWrangleJunkOne"]}, {"id": 2, "tags": ["ZWrangleJunkTwo"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "junk.txt", ["zwranglejunkone", "zwranglejunktwo"])
        plan = jobs.job_plan_wrangle(lib, None, [1, 2])
        api = FakeApi(books=(1, 2), fields=("tags",), multi=("tags",))
        api.set_field("tags", {1: ("ZWrangleJunkOne",), 2: ("ZWrangleJunkTwo",)})
        # call order is newest-id-first (_step_walk): book 2's call replays first, book 1's second.
        ticks = [([], [0], "skip"), ([0], [], "apply")]
        result = jobs.job_execute_wrangle(lib, None, [1, 2], plan["carry"], ticks, api)
        assert not os.path.exists(common.rejects_path())
    assert result["refused"] is False
    assert api.fields["tags"][2] == ("ZWrangleJunkTwo",)      # book 2 deferred: untouched
    assert api.fields["tags"][1] == ()                         # book 1 written (accepted)
    assert result["touched"] == [1]


def test_job_execute_wrangle_an_explicit_untick_writes_a_declared_reject_row():
    lib = _wrangle_lib([{"id": 1, "tags": ["ZWrangleJunkThree"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "junk.txt", ["zwranglejunkthree"])
        plan = jobs.job_plan_wrangle(lib, None, [1])
        api = FakeApi(books=(1,), fields=("tags",), multi=("tags",))
        api.set_field("tags", {1: ("ZWrangleJunkThree",)})
        ticks = [([], [0], "apply")]                     # apply, with the one edit rejected
        result = jobs.job_execute_wrangle(lib, None, [1], plan["carry"], ticks, api)
        from scourgify import artifacts
        rows = artifacts.read_rows(common.rejects_path())
    assert result["refused"] is False
    assert len(rows) == 1
    assert rows[0]["book"] == "1" and rows[0]["kind"] == "drop" and rows[0]["column"] == "tags"
    assert api.fields["tags"][1] == ("ZWrangleJunkThree",)     # rejected -> kept unchanged


def test_a_second_plan_after_a_successful_wrangle_write_returns_the_d04_empty_result():
    lib = _wrangle_lib([{"id": 1, "tags": ["ZWrangleJunkFour"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "junk.txt", ["zwranglejunkfour"])
        plan = jobs.job_plan_wrangle(lib, None, [1])
        api = FakeApi(books=(1,), fields=("tags",), multi=("tags",))
        api.set_field("tags", {1: ("ZWrangleJunkFour",)})
        result = jobs.job_execute_wrangle(lib, None, [1], plan["carry"], [], api)
        assert result["written"] == 1
        # This job's own transport is a fixture FakeApi (never persisted to metadata.db) —
        # test_write_path.py pins that both transports emit the identical ops for the same
        # producer, so applying the same op to the real sqlite file simulates a real write.
        import sqlite3
        con = sqlite3.connect(os.path.join(lib, "metadata.db"))
        con.execute("DELETE FROM books_tags_link WHERE book=1")
        con.commit(); con.close()
        second = jobs.job_plan_wrangle(lib, None, [1])
    assert second["empty"] is True


def test_job_execute_wrangle_refuses_cleanly_on_a_guard_trip():
    lib = _wrangle_lib([{"id": 1, "fandoms": ["ZGhostFandomTwo"]}])
    with _pointed_at(lib) as home:
        _write_override(home, "fandoms.csv", ["alias,canonical", "ZGhostFandomTwo,"])
        api = FakeApi(books=(1,), fields=("#fandoms",))
        result = jobs.job_execute_wrangle(lib, None, [1], {"ids": [1], "n_books": 1}, [], api)
    assert result["refused"] is True
    assert "last fandom" in result["msg"]


# ---------------- synopsis (plan 02-06) ----------------
import test_synopsis as _test_synopsis   # noqa: E402 — reuses its EPUB-building `lib()` fixture


def _synopsis_lib_ctx(books=None, prefs=_test_synopsis.PREFS_ON, custom=None):
    """`test_synopsis.lib()`, but yielding the resolved CALIBRE_LIBRARY path this file's
    job_plan_synopsis/job_execute_synopsis calls need — that module's own `lib()` context manager
    yields nothing (its callers read the env var directly).

    Resets `common.set_library(None)` FIRST: unlike every other test in this file,
    `jobs.job_plan_synopsis`/`job_execute_synopsis` call `common.set_library()` (via `_open`),
    which WINS over `$CALIBRE_LIBRARY` and is process-global — a prior synopsis test's call would
    otherwise still be in effect when `test_synopsis.lib()`'s own `os.makedirs(common.data_dir())`
    setup runs, pointing it at the WRONG (stale) library's data dir (same class of gap
    `test_job_plan_classify_unclassified_shortcut_restricts_to_the_selection` already works
    around for classify's own `set_library` calls)."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        common.set_library(None)
        kw = {"prefs": prefs}
        if books is not None:
            kw["books"] = books
        if custom is not None:
            kw["custom"] = custom
        with _test_synopsis.lib(**kw):
            yield os.environ["CALIBRE_LIBRARY"]
    return _cm()


def _no_real_prefs():
    """`job_plan_synopsis` reaches `engines.resolve_keys(stored_keys())` unconditionally (to
    price every USABLE engine) — never let it touch the real `JSONConfig` (no Calibre here)."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        saved = jobs.stored_keys
        jobs.stored_keys = lambda: {}
        try:
            yield
        finally:
            jobs.stored_keys = saved
    return _cm()


def test_job_plan_synopsis_refuses_when_fanficfare_would_clobber_the_synopsis():
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    with _no_real_prefs(), _synopsis_lib_ctx(prefs=off) as lib:
        r = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
    assert r["refused"] is True
    assert r["degraded_available"] is True
    assert "New Only" in r["msg"]


def test_job_plan_synopsis_proceeds_with_force_true():
    off = {"std_cols_newonly": {"comments": False}, "custom_cols": {}}
    with _no_real_prefs(), _synopsis_lib_ctx(prefs=off) as lib:
        r = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": True, "batch": None})
    assert r["refused"] is False


def test_job_plan_synopsis_refuses_cleanly_without_the_synopsized_column():
    """Pitfall 3/5/6 (02-RESEARCH.md), reused for synopsis: a library that has never run
    `scourgify setup` must refuse cleanly, never a bare exception from deep in the pass."""
    with _synopsis_lib_ctx(custom=[]) as lib:
        r = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
    assert r["refused"] is True
    assert "#synopsized" in r["msg"] and "scourgify setup" in r["msg"]
    assert "degraded_available" not in r


def test_job_plan_synopsis_returns_the_d04_empty_result_for_an_empty_selection():
    with _synopsis_lib_ctx() as lib:
        r = jobs.job_plan_synopsis(lib, None, [], {"force": False, "batch": None})
    assert r["empty"] is True and r["refused"] is False


def test_job_plan_synopsis_prices_every_usable_engine_over_the_resolved_todo_set():
    with _no_real_prefs(), _synopsis_lib_ctx() as lib:
        r = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
    assert r["refused"] is False and r["empty"] is False
    assert sorted(r["carry"]["todo_ids"]) == [1, 2]
    assert r["consequence"] == "Settle 2 descriptions"
    assert r["default_engine"]
    assert isinstance(r["usable"], list)
    assert set(r["engine_limits"]) == {eid for _key, eid, _eid2, _label in r["engines"]}
    apple_row = next(row for row in r["engines"] if row[1] == "apple")
    assert "free" in apple_row[3]                       # apple's list price is always (0, 0)


def _no_engine_call(engine_id, model, timeout):
    raise AssertionError("job_plan_synopsis must reach no engine — it sends nothing")


def test_job_plan_synopsis_sends_nothing_to_an_engine():
    saved = jobs._engine_ask
    jobs._engine_ask = _no_engine_call
    try:
        with _no_real_prefs(), _synopsis_lib_ctx() as lib:
            jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
    finally:
        jobs._engine_ask = saved


def test_job_execute_synopsis_first_dispatch_harvests_review_items_and_writes_nothing():
    """The harvest dispatch (`ticks=None`) runs the real engine pass but its write= transport
    touches nothing — the generated descriptions come back as review items instead."""
    ask = _test_synopsis.FakeAsk(judge="NO", back=_test_synopsis.BACK)   # both books -> generation
    saved = jobs._engine_ask
    jobs._engine_ask = lambda engine_id, model, timeout: ask
    try:
        with _no_real_prefs(), _synopsis_lib_ctx() as lib:
            plan = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
            api = FakeApi(books=(1, 2), fields=("comments", "#synopsized"), multi=())
            result = jobs.job_execute_synopsis(lib, None, [1, 2], plan["carry"], "apple", "", api, None)
    finally:
        jobs._engine_ask = saved
    assert result["refused"] is False
    assert result["items"], "the generated descriptions must come back as review items"
    for label, payload in result["items"]:
        assert payload["field"] == "comments" and payload["after"]
    assert api.fields == {}, "the harvest dispatch must write nothing"


def test_job_execute_synopsis_second_dispatch_writes_only_the_ticked_books():
    """An unticked book (rejected in the review) receives neither its generated description nor
    its #synopsized stamp — it stays in the queue."""
    ask = _test_synopsis.FakeAsk(judge="NO", back=_test_synopsis.BACK)   # both books -> generation
    saved = jobs._engine_ask
    jobs._engine_ask = lambda engine_id, model, timeout: ask
    try:
        with _no_real_prefs(), _synopsis_lib_ctx() as lib:
            plan = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
            api = FakeApi(books=(1, 2), fields=("comments", "#synopsized"), multi=())
            api.set_field("comments", {1: _test_synopsis.FAT, 2: _test_synopsis.THIN})
            harvest = jobs.job_execute_synopsis(lib, None, [1, 2], plan["carry"], "apple", "", api, None)
            book_order = [payload["book"] for _label, payload in harvest["items"]]
            assert sorted(book_order) == [1, 2]
            accept = [i for i, b in enumerate(book_order) if b == 1]     # keep book 1
            reject = [i for i, b in enumerate(book_order) if b == 2]     # reject book 2
            ticks = [(accept, reject, "apply")]
            with _test_write_path._fake_calibre_utils_date():
                result = jobs.job_execute_synopsis(lib, None, [1, 2], plan["carry"], "apple", "", api, ticks)
    finally:
        jobs._engine_ask = saved
    assert result["refused"] is False
    assert api.fields["comments"][1].startswith("A generated back cover")
    assert 1 in api.fields.get("#synopsized", {})
    assert api.fields["comments"][2] == _test_synopsis.THIN, \
        "an unticked book must not receive its generated description"
    assert 2 not in api.fields.get("#synopsized", {}), "an unticked book must not receive its stamp either"


def test_job_execute_synopsis_a_book_with_an_adequate_blurb_is_stamped_and_kept_untouched():
    """book 1 (a good, long blurb) is judged adequate and KEPT — comments untouched, but stamped
    #synopsized alongside book 2 (thin blurb -> generated and accepted)."""
    ask = _test_synopsis.FakeAsk(judge="YES", back=_test_synopsis.BACK)  # book1's blurb judged adequate
    saved = jobs._engine_ask
    jobs._engine_ask = lambda engine_id, model, timeout: ask
    try:
        with _no_real_prefs(), _synopsis_lib_ctx() as lib:      # default BOOKS2: book1 FAT blurb, book2 THIN blurb
            plan = jobs.job_plan_synopsis(lib, None, [1, 2], {"force": False, "batch": None})
            api = FakeApi(books=(1, 2), fields=("comments", "#synopsized"), multi=())
            api.set_field("comments", {1: _test_synopsis.FAT, 2: _test_synopsis.THIN})
            jobs.job_execute_synopsis(lib, None, [1, 2], plan["carry"], "apple", "", api, None)   # harvest
            with _test_write_path._fake_calibre_utils_date():
                result = jobs.job_execute_synopsis(lib, None, [1, 2], plan["carry"], "apple", "", api, [])
    finally:
        jobs._engine_ask = saved
    assert result["refused"] is False
    assert api.fields["comments"][1] == _test_synopsis.FAT, "an adequate blurb is kept — comments untouched"
    assert 1 in api.fields.get("#synopsized", {}), "a kept book is still stamped"
    assert api.fields["comments"][2].startswith("A generated back cover")
    assert 2 in api.fields.get("#synopsized", {})


def test_a_missing_synopsized_column_refuses_execute_too():
    with _synopsis_lib_ctx(custom=[]) as lib:
        api = FakeApi(books=(1, 2), fields=("comments", "#synopsized"), multi=())
        result = jobs.job_execute_synopsis(lib, None, [1, 2], {"todo_ids": [1, 2], "force": False},
                                           "apple", "", api, None)
    assert result["refused"] is True
    assert "#synopsized" in result["msg"]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
