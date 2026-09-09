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


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for n, f in fns:
        f(); print(f"  ok {n}")
    print(f"{len(fns)} tests passed")
