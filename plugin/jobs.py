#!/usr/bin/env python3
"""The Qt-free job layer (D-12) — the PLAN/EXECUTE body of every write verb.

Deliberately Qt-free, and kept that way BY ENFORCEMENT (`tests/test_plugin_source.py`, once this
file joins its module list): no `qt.core` and no `calibre.gui2` import anywhere here, at module
level or otherwise. That is what makes this whole file importable and every job function callable
under plain CI Python, against `tests/fixture_db.py`, with no Calibre and no GUI — which is the
only way WRITE-01..07's job-level behaviour (ops assembly, the diff-after result shape, the
`GuardrailError`-to-refused-result conversion) can be pinned by CI at all; `plugin/selftest.py`
under a real Calibre is the only other place any of this ever ran.

Module-level imports are stdlib only. Every scourgify import lives inside a function — exactly
the discipline `plugin/action.py` already holds itself to, and for the same reason: a module-level
core import here would run the instant `action.py`'s own module-level `import jobs` executes,
which is on the GUI thread at plugin load.

Every job function returns a plain dict: no dataclass, no Qt object, no live Calibre handle. That
is what lets a result cross the Dispatcher boundary (worker thread -> GUI thread) safely and
survive `json.dumps` — see `plan_result`/`execute_result`.
"""
import collections


# ---------------------------------------------------------------------- the library bind
def _open(lib_path, lib_uuid, now_uuid=None):
    """Bind the core to the library the click was made against, and prove the GUI still has that
    one open. Returns (con, None) or (None, message).

    `now_uuid` is a callable the action supplies; it reads `gui.current_db` — deliberately, and it
    is the only version of this check that can ever fire. Re-reading the uuid out of the db at
    `lib_path` compares the captured library to ITSELF: after a switch that path still exists and
    still holds the same uuid, so the guard would pass while the user is looking at another
    library (NLSpec B1.1). It is a plain attribute read, not a Qt call."""
    from scourgify import common
    common.set_library(lib_path)                     # the one seam; os.environ is never touched
    if lib_uuid and now_uuid is not None:
        try:
            current = now_uuid()
        except Exception:
            current = None
        if current and current != lib_uuid:
            return None, ('The library changed since you clicked, so nothing was read.\n'
                          'Open the scourgify menu again.')
    return common.ro_connect(), None


# ---------------------------------------------------------------------- the write transport
class _Writer:
    """The in-process transport every tool module's `write=` parameter binds to (D-05). Constructed
    once per EXECUTE job and handed to the compute function in place of the CLI's `run_writer`.

    This is the ONLY place `engine`/`model` reach the edit log — the reason no producer function
    (`staleness.write`, and every later verb's write function) needs an `engine=` parameter of its
    own; the transport carries it instead."""

    def __init__(self, api, engine=None, model=None):
        self.api = api
        self.engine = engine
        self.model = model
        self.result = None

    def __call__(self, ops, force=False, tool='plugin', scope=None):
        from scourgify import common
        self.result = common.write_ops(self.api, ops, force=force, tool=tool, scope=scope,
                                       engine=self.engine, model=self.model)
        return self.result


# ---------------------------------------------------------------------- the per-job ceremony (#72)
def _ceremony(verb, body, lib_path, lib_uuid, ids, now_uuid=None, api=None, **kw):
    """The per-job ceremony every PLAN/EXECUTE job shares: bind the library through `_open`, run
    `body(con, ids, ctx)`, and convert a `GuardrailError` into a refused result (D-11) instead of
    letting it propagate — a guard trip is a normal outcome, not a job failure. Every OTHER
    exception propagates untouched, so a real bug still reaches `gui.job_exception`.

    `ctx` carries `api`, `abort`, `log`, `notifications` (whatever the caller passed through `kw`)
    and the verb name, so `body` never needs its own closure over them."""
    con, err = _open(lib_path, lib_uuid, now_uuid)
    if err:
        return {'verb': verb, 'refused': True, 'msg': err}
    try:
        from scourgify.common import GuardrailError
        ctx = dict(kw)
        ctx['api'] = api
        ctx['verb'] = verb
        return body(con, ids, ctx)
    except GuardrailError as e:
        return {'verb': verb, 'refused': True, 'msg': str(e)}
    finally:
        con.close()


# ---------------------------------------------------------------------- the plain-dict result shapes
def plan_result(verb, summary, items, consequence, carry, safety=''):
    """A PLAN job's result. `items` is `[(label, payload), ...]` (D-03) — `payload` is what the
    picker's review list and, later, the result dialog render; `carry` is handed back verbatim to
    the matching EXECUTE job so it never recomputes what the PLAN job already read (the apply-time
    conflict filter is the staleness net, not a second compute)."""
    return {
        'verb': verb,
        'empty': not items,
        'summary': list(summary),
        'safety': safety,
        'consequence': consequence,
        'items': list(items),
        'carry': carry,
        'refused': False,
        'msg': '',
    }


def execute_result(verb, rows, write_result, engine_failures=(), touched=()):
    """An EXECUTE job's result, built from a `_Writer`'s `common.WriteResult` — flattened into
    scalar keys so no dataclass, no Qt object and no live handle ever crosses the Dispatcher
    boundary, and the whole dict is JSON-serializable. `rows` are sorted by (book, field) so two
    rows that compare equal on book id never swap order between runs."""
    rows = sorted(rows, key=lambda r: (r['book'], r['field']))
    written = sum(1 for r in rows if r.get('state') == 'written')
    return {
        'verb': verb,
        'refused': False,
        'msg': '',
        'written': written,
        'skipped': [list(pair) for pair in write_result.skipped],
        'run_id': write_result.run_id,
        'backup': write_result.backup,
        'outcome': write_result.outcome,
        'rows': rows,
        'engine_failures': [list(f) for f in engine_failures],
        'touched': list(touched),
    }


# ---------------------------------------------------------------------- staleness (the tracer verb)
def job_plan_staleness(lib_path, lib_uuid, ids, now_uuid=None, abort=None, log=None, notifications=None):
    """PLAN: which of the selected books' #status would change, per `staleness.compute()`?

    Empty selection (or a selection where nothing changes) is the D-04 empty result — no
    library read beyond `_open`'s identity check (`compute()` is never called)."""
    def body(con, ids, ctx):
        from scourgify import staleness
        from scourgify.common import titles as book_titles
        if not ids:
            return plan_result('staleness', [], [], '', {})
        status_label, rows = staleness.compute(books=ids)
        if not rows:
            return plan_result('staleness', [], [], '', {})
        titles = book_titles(con, [r[0] for r in rows])
        items = [(staleness.status_line(r, str(titles.get(r[0], ''))),
                 {'book': r[0], 'title': titles.get(r[0], ''), 'field': status_label,
                  'before': r[1], 'after': r[2]}) for r in rows]
        trans = collections.Counter('%s -> %s' % (o, n) for _, o, n, _ in rows)
        summary = ['%s: %d' % (k, c) for k, c in trans.most_common()]
        consequence = 'Re-derive status on %d book%s' % (len(rows), '' if len(rows) == 1 else 's')
        carry = {'status_label': status_label, 'rows': rows}
        return plan_result('staleness', summary, items, consequence, carry)

    return _ceremony('staleness', body, lib_path, lib_uuid, ids, now_uuid,
                     abort=abort, log=log, notifications=notifications)


def job_execute_staleness(lib_path, lib_uuid, ids, carry, api, now_uuid=None, abort=None, log=None,
                          notifications=None):
    """EXECUTE: write exactly the change-set the PLAN job carried forward — no recompute, so the
    apply-time conflict filter (not a second `staleness.compute()`) is what decides whether a
    book's since-changed status is skipped."""
    def body(con, ids, ctx):
        from scourgify import staleness
        writer = _Writer(ctx['api'])
        status_label, rows = carry['status_label'], carry['rows']
        titles = {}
        try:
            from scourgify.common import titles as book_titles
            titles = book_titles(con, [r[0] for r in rows])
        except Exception:
            pass
        staleness.write(status_label, rows, write=writer)
        wr = writer.result
        skipped_pairs = {(b, f) for b, f in wr.skipped}
        out_rows, touched = [], []
        for b, old, new, age in rows:
            skipped = (b, status_label) in skipped_pairs
            state = 'skipped: changed since the plan' if skipped else 'written'
            out_rows.append({'book': b, 'title': titles.get(b, ''), 'field': status_label,
                             'before': old, 'after': new, 'state': state})
            if not skipped:
                touched.append(b)
        return execute_result('staleness', out_rows, wr, touched=touched)

    return _ceremony('staleness', body, lib_path, lib_uuid, ids, now_uuid, api=api,
                     abort=abort, log=log, notifications=notifications)
