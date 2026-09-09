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


# ---------------------------------------------------------------------- stored keys (relocated
# from plugin/config.py, plan 02-03 task 2) — `plugin/config.py` imports `qt.core` at module
# level, so THIS file (which must stay importable under plain CI Python with no Calibre and no
# GUI, per the module docstring above) cannot import it. The key store therefore lives here, with
# `config.py` importing it back — ONE owner of the stored-key shape, reachable from both a Qt
# settings dialog and a worker job.
_PREFS = None


def _prefs():
    """Lazily-constructed, cached `JSONConfig('plugins/scourgify')` — the import of
    `calibre.utils.config` lives INSIDE this function, like every other non-stdlib import in this
    file, so importing `jobs` costs nothing under plain CI Python."""
    global _PREFS
    if _PREFS is None:
        from calibre.utils.config import JSONConfig
        _PREFS = JSONConfig('plugins/scourgify')
        _PREFS.defaults['keys'] = {}
    return _PREFS


def stored_keys() -> dict:
    """{engine: key} as saved by the settings dialog. The one reader — classify's PLAN/EXECUTE
    jobs resolve through `engines.resolve_keys(stored_keys())` so the GUI and a shell agree about
    which key wins (env beats stored, NLSpec B5.2)."""
    return dict(_prefs()['keys'] or {})


def job_verify(engine, key, abort=None, log=None, notifications=None):
    """One cheap request against a real endpoint, classified per the auth taxonomy (B5.4 / B3.6).

    `key` is passed in rather than read from anywhere: the whole point of the constructor seam is
    that a stored key can reach an engine without the environment being touched. It never reaches
    `log` or the job description.

    Apple is refused, not skipped quietly: constructing it spawns a subprocess pipe, and
    `usable_engines` already answers the only question there is about it (is the afm binary or a
    swift toolchain present)."""
    from scourgify import engines
    if engine not in engines.ENGINE_ENV:
        return {'engine': engine, 'ok': False, 'cls': '', 'detail': 'on-device — nothing to verify'}
    try:
        eng = engines.ENGINES[engine]('', 30, env={engines.ENGINE_ENV[engine][0]: key})
    except Exception as e:                                   # a missing/blank key never gets to fly
        return {'engine': engine, 'ok': False, 'cls': engines.AUTH,
                'detail': engines.redact('%s' % e, key)}
    out, reason = engines.ask_retry(eng, 'Reply with the single word: ok', tries=1)
    return {'engine': engine, 'ok': bool(out and not reason), 'cls': engines.failure_class(reason),
            'detail': reason or (out or '').strip()[:80]}


def _engine_ask(engine_id, model, timeout):
    """The ONE place THIS phase constructs an engine for a classify run — modelled exactly on
    `job_verify`'s existing pattern. Never call `Plan.run()` with `ask=None` from a job: the
    module's own default construction passes no `env=`, so it can only see process-environment
    keys, and a key typed into the settings dialog is invisible to it (the single most likely
    "works for me" bug this phase can ship — see 02-RESEARCH.md Pattern 3 / Pitfall 1)."""
    from scourgify import engines
    env = engines.resolve_keys(stored_keys())
    eng = engines.ENGINES[engine_id](model, timeout, env=env)
    return lambda prompt: engines.ask_retry(eng, prompt)


def _require_column(con, label, verb):
    """The pre-flight that keeps a bare `ValueError` out of the job-failure path: `ops.apply_ops`'s
    column-creation branch raises a plain `ValueError` whenever the legacy DB is absent — which is
    ALWAYS true in-process (the in-process contract deliberately refuses `create_column`, since the
    legacy-DB reopen it needs would desync a live GUI's models) — and a plain `ValueError` is not a
    `GuardrailError`, so it would reach `gui.job_exception` as a raw traceback instead of `_ceremony`'s
    clean refused result.

    Scoped exactly to the column-creation case: only reached on the FIRST classify (or synopsis)
    run on a library, and must never refuse an ordinary run just because some book lacks a value."""
    from scourgify import common
    if common.custom_column_id(con, label) is None:
        raise common.GuardrailError(
            '#%s does not exist in this library yet — run `scourgify setup` with Calibre closed, '
            'then try again.' % label)


# ---------------------------------------------------------------------- read jobs (relocated from
# action.py verbatim, task 2 of the D-12 split — action.py now holds ZERO core imports)
def job_inspect(lib_path, lib_uuid, ids, now_uuid=None, abort=None, log=None, notifications=None):
    """"What does scourgify know?" — stamp state, proposal/archive rows, failures, rejects.

    Read-only, off common.ro_connect() (the spike proved a second read-only sqlite handle is fine
    while the GUI holds the library). With no selection this answers at library scope, which is
    the dashboard's header until phase 7 builds it — every number names its source function per
    NLSpec B6.1."""
    from scourgify import artifacts, common, select, setup as setup_mod

    con, err = _open(lib_path, lib_uuid, now_uuid)
    if err:
        return {'title': 'scourgify', 'msg': err, 'det': ''}
    try:
        seen = artifacts.classified_ids()                        # applied archives + proposal + failures
        proposal = {r['book_id']: r for r in artifacts.read_proposal()}
        failures = {int(r['book_id']): r.get('reason', '') for r in artifacts.read_rows(artifacts.fail())
                    if str(r.get('book_id', '')).isdigit()}
        if not ids:
            return _library_scope(con, seen, proposal, failures)

        stamps = common.read_custom_column(con, select.STAMP) or {}
        titles = dict(con.execute('SELECT id, title FROM books'))
        sendable = select.sendable(con)
        with_text = select.sendable(con, text_fallback=True)
        rejects = _rejects_by_book(common)
        archives = _archives_by_book(artifacts)

        lines, never, pending, blocked = [], 0, 0, 0
        for n, b in enumerate(ids):
            if abort is not None and abort.is_set():
                break
            if notifications is not None:
                notifications.put((n / max(len(ids), 1), 'reading %d of %d' % (n + 1, len(ids))))
            lines.append('%s (%s)' % (titles.get(b, '(not in this library)'), b))
            stamp = stamps.get(b)
            lines.append('    classified: %s' % (str(stamp)[:19] if stamp else 'never'))
            if not stamp:
                never += 1
            if b in proposal:
                pending += 1
                r = proposal[b]
                lines.append('    pending proposal: %s' % (artifacts.join_tags(r['added_tags']) or '(no tags)'))
                if r['proposed_new']:
                    lines.append('    proposed new terms: %s' % artifacts.join_tags(r['proposed_new']))
            for arch in archives.get(b, []):
                lines.append('    applied from: %s' % arch)
            if b in failures:
                blocked += 1
                lines.append('    last attempt FAILED: %s' % failures[b])
            for rj in rejects.get(b, []):
                lines.append('    you rejected: %s %s -> %s' % (rj.get('column', ''), rj.get('before', ''), rj.get('after', '')))
            if b not in sendable:
                lines.append('    description too thin to send%s'
                             % ('' if b in with_text else ' — and no file to sample either'))
            lines.append('')

        backlog = [b for b in ids if b not in seen and b in with_text]
        msg = ('<b>%d book%s selected.</b><br>%d never classified · %d with a pending proposal · '
               '%d blocked on the last attempt<br>%d could be classified now.'
               % (len(ids), '' if len(ids) == 1 else 's', never, pending, blocked, len(backlog)))
        return {'title': 'What scourgify knows', 'msg': msg, 'det': '\n'.join(lines)}
    finally:
        con.close()


def _library_scope(con, seen, proposal, failures):
    """The whole-library answer — the dashboard header's numbers, each from its named source.

    Four arguments, not the eight `job_inspect` used to pass (the #72 discretion item): the four
    module arguments existed only because the imports lived in `action.py`'s `job_inspect`; here
    each import lives inside the function that needs it, exactly like every other job body."""
    from scourgify import common, select, setup as setup_mod
    books = common.book_count(con)
    backlog = select.pick(con, 'unclassified')      # bare: seen + text-fallback live in select,
                                                    # so this number matches the wizard header
    changed = select.changed(con)
    have = {'#' + l for (l,) in con.execute('SELECT label FROM custom_columns')} | {'tags'}
    cols = [label for label, _, _, _ in setup_mod.REC if label in have]
    msg = ('<b>%s books</b> in this library.<br>'
           '%s never classified (select.pick "unclassified") · %s new or changed (select.changed)<br>'
           '%s attempted so far (artifacts.classified_ids) · %s pending review · %s failed<br>'
           '%d of %d columns present.'
           % ('{:,}'.format(books), '{:,}'.format(len(backlog)), '{:,}'.format(len(changed)),
              '{:,}'.format(len(seen)), '{:,}'.format(len(proposal)), '{:,}'.format(len(failures)),
              len(cols), len(setup_mod.REC)))
    det = ('columns present: %s\nmissing: %s\n\nnewest never-classified book ids:\n%s'
           % (', '.join(cols) or '(none)',
              ', '.join(l for l, _, _, _ in setup_mod.REC if l not in have) or '(none)',
              ', '.join(str(b) for b in backlog[:50]) or '(none)'))
    return {'title': 'What scourgify knows', 'msg': msg, 'det': det}


def _rejects_by_book(common):
    """{book: [reject row]} from data/rejects.csv — "what you rejected", per the interaction spec."""
    import csv, os
    out = {}
    path = common.rejects_path()
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for r in csv.DictReader(f):
            if str(r.get('book', '')).isdigit():
                out.setdefault(int(r['book']), []).append(r)
    return out


def _archives_by_book(artifacts):
    """{book: [archive filename]} — which applied proposal each book's tags came from."""
    import os
    out = {}
    for path in artifacts.applied_proposals():
        name = os.path.basename(path)
        for row in artifacts.read_proposal(path):
            out.setdefault(row['book_id'], []).append(name)
    return out


def job_db_smoke(lib_path, lib_uuid, api, now_uuid=None, abort=None, log=None, notifications=None):
    """NLSpec B3.3 — prove the db-from-worker boundary instead of assuming it.

    Reads through `new_api` AND performs a scratch write (a tag added and removed again) from
    inside a ThreadedJob worker. Two locks, because this is the one thing in phase 4 that writes:
    $SCOURGIFY_SMOKE must be set, and the library must be small enough to be a throwaway. Both
    fail closed."""
    import os
    from scourgify import common
    if not os.environ.get('SCOURGIFY_SMOKE'):
        return {'title': 'scourgify smoke', 'msg': 'Set SCOURGIFY_SMOKE=1 and restart Calibre.', 'det': ''}
    con, err = _open(lib_path, lib_uuid, now_uuid)
    if err:
        return {'title': 'scourgify smoke', 'msg': err, 'det': ''}
    try:
        n = common.book_count(con)
    finally:
        con.close()
    if n > 50:
        return {'title': 'scourgify smoke', 'msg': 'Refusing: %d books is not a throwaway library.' % n, 'det': ''}

    out = ['library_id: %s' % api.library_id, 'book count via new_api: %d' % len(api.all_book_ids())]
    book = sorted(api.all_book_ids())[0]
    before = set(api.field_for('tags', book) or ())
    out.append('book %d tags before: %s' % (book, sorted(before)))
    api.set_field('tags', {book: sorted(before | {'scourgify-smoke'})})
    out.append('after write:  %s' % sorted(api.field_for('tags', book) or ()))
    api.set_field('tags', {book: sorted(before)})
    out.append('after revert: %s' % sorted(api.field_for('tags', book) or ()))
    ok = set(api.field_for('tags', book) or ()) == before
    return {'title': 'scourgify smoke',
            'msg': 'new_api read + scratch write from a background job worker: <b>%s</b>'
                   % ('ok' if ok else 'MISMATCH'),
            'det': '\n'.join(out)}


# ---------------------------------------------------------------------- the write transport
class _Writer:
    """The in-process transport every tool module's `write=` parameter binds to (D-05). Constructed
    once per EXECUTE job and handed to the compute function in place of the CLI's `run_writer`.

    This is the ONLY place `engine`/`model` reach the edit log — the reason no producer function
    (`staleness.write`, and every later verb's write function) needs an `engine=` parameter of its
    own; the transport carries it instead.

    `outcome=` (D-08) names the footer outcome on the success path only, threaded straight through
    to `common.write_ops`'s own `outcome=` — this is what lets classify's EXECUTE job close a
    cancelled run's footer as "cancelled" instead of "ok" while its already-applied ops stay
    logged and undoable exactly like an uninterrupted run's."""

    def __init__(self, api, engine=None, model=None, outcome=None):
        self.api = api
        self.engine = engine
        self.model = model
        self.outcome = outcome
        self.result = None

    def __call__(self, ops, force=False, tool='plugin', scope=None):
        from scourgify import common
        self.result = common.write_ops(self.api, ops, force=force, tool=tool, scope=scope,
                                       engine=self.engine, model=self.model, outcome=self.outcome)
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


# ---------------------------------------------------------------------- the two generic decide=
# helpers (D-02): every review-checklist site in the core takes a `decide(title, items,
# subtitle='') -> (accepted_idx, rejected_idx, action)` callback (D-11). A PLAN job needs to
# HARVEST what a review would show without deciding anything (the Plan/rows object it walks is
# discarded at the end of the job anyway); the matching EXECUTE job needs to REPLAY a reviewer's
# actual ticks against a freshly (re)computed object. These two functions are the ONLY places the
# plugin ever fabricates a decide= answer — neither one narrows a change-set itself, the tool
# module's own `_step_walk`/`step` does that, which is what keeps the reject log and the
# overrides flow working for the plugin exactly as they do for the terminal's `--step`.
#
# Some sites (`staleness.step`, and every other `decide=` producer outside `_step_walk`) call
# decide() exactly ONCE with every candidate item; `wrangle.Plan.step` (via `_step_walk`) calls it
# ONCE PER BOOK. Both helpers are written sequence-aware so they work for either shape: `calls`
# grows one entry per invocation, and `_replay_decide` pops one tuple per invocation, in the same
# order.
def _record_decide():
    """The PLAN-side helper: returns `(callback, calls)`. `calls` grows one
    `{'title':, 'subtitle':, 'items':}` entry per call — the PLAN job flattens it into the
    result's `items`, in call order, so a later replay lines up call-for-call. The callback itself
    always answers `([], list(range(len(items))), 'skip')` — nothing accepted, so whatever object
    is being walked is left untouched (or, for `_step_walk`, deferred) — it is being read for its
    items only, and the object it mutates is thrown away at the end of the PLAN job regardless."""
    calls = []

    def decide(title, items, subtitle=''):
        items = list(items)
        calls.append({'title': title, 'subtitle': subtitle, 'items': items})
        return [], list(range(len(items))), 'skip'
    return decide, calls


def _replay_decide(ticks):
    """The EXECUTE-side helper: returns a callback that pops the next `(accepted_idx,
    rejected_idx, action)` triple off `ticks`, in call order, one per invocation. Once `ticks` is
    exhausted it falls back to accept-everything (`(range(len(items)), [], 'apply')`) — the
    one-click Run path passes `ticks=()` and therefore accepts every item unchanged, which is
    D-02's 'all ticked on the one-click path'."""
    ticks = list(ticks)
    i = 0

    def decide(title, items, subtitle=''):
        nonlocal i
        if i < len(ticks):
            acc, rej, action = ticks[i]
            i += 1
            return list(acc), list(rej), action
        return list(range(len(items))), [], 'apply'
    return decide


# ---------------------------------------------------------------------- staleness (the tracer verb)
def job_plan_staleness(lib_path, lib_uuid, ids, now_uuid=None, abort=None, log=None, notifications=None):
    """PLAN: which of the selected books' #status would change, per `staleness.compute()`?

    Empty selection (or a selection where nothing changes) is the D-04 empty result — no
    library read beyond `_open`'s identity check (`compute()` is never called).

    Items are harvested by driving `staleness.step` itself with `_record_decide()`'s recorder
    (D-02), rather than re-deriving the same `status_line`/payload shape here a second time —
    `staleness.step` is called exactly ONCE (one entry in `calls`) since it reviews the whole
    change-set as a single list, not per book."""
    def body(con, ids, ctx):
        from scourgify import staleness
        if not ids:
            return plan_result('staleness', [], [], '', {})
        status_label, rows = staleness.compute(books=ids)
        if not rows:
            return plan_result('staleness', [], [], '', {})
        decide, calls = _record_decide()
        staleness.step(status_label, rows, decide=decide)
        items = calls[0]['items'] if calls else []
        trans = collections.Counter('%s -> %s' % (o, n) for _, o, n, _ in rows)
        summary = ['%s: %d' % (k, c) for k, c in trans.most_common()]
        consequence = 'Re-derive status on %d book%s' % (len(rows), '' if len(rows) == 1 else 's')
        carry = {'status_label': status_label, 'rows': rows}
        return plan_result('staleness', summary, items, consequence, carry)

    return _ceremony('staleness', body, lib_path, lib_uuid, ids, now_uuid,
                     abort=abort, log=log, notifications=notifications)


def job_execute_staleness(lib_path, lib_uuid, ids, carry, api, now_uuid=None, ticks=(), abort=None,
                          log=None, notifications=None):
    """EXECUTE: replay the reviewer's `ticks` through `staleness.step` (D-02) to narrow the PLAN
    job's carried-forward rows, then write — no re-`compute()`, so the apply-time conflict filter
    (not a second `staleness.compute()`) is still what decides whether a book's since-changed
    status is skipped. `ticks=()` (the default, and every existing call site before this plan)
    replays through `_replay_decide`'s own accept-everything fallback, so omitting `ticks`
    reproduces this job's exact pre-D-02 behaviour — writing every row the PLAN job carried.

    `ticks` deliberately stays AFTER `now_uuid` (not between `carry` and `api`, unlike wrangle's
    own EXECUTE job below) so `plugin/action.py`'s existing generic `_EXECUTE_JOBS` dispatch —
    which builds a fixed `(lib, uuid, ids, carry, api, now_uuid)` positional tuple for every verb
    in that table — keeps working unchanged; `abort`/`log`/`notifications` are always injected by
    Calibre's `ThreadedJob` as keywords, never positionally, so their position here doesn't
    matter."""
    def body(con, ids, ctx):
        from scourgify import staleness
        writer = _Writer(ctx['api'])
        status_label, carried_rows = carry['status_label'], carry['rows']
        titles = {}
        try:
            from scourgify.common import titles as book_titles
            titles = book_titles(con, [r[0] for r in carried_rows])
        except Exception:
            pass
        rows = staleness.step(status_label, carried_rows, decide=_replay_decide(ticks))
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


# ---------------------------------------------------------------------- wrangle (the verb this
# phase is named for — the deterministic pass with real data-loss guards, D-01..D-03)
def job_plan_wrangle(lib_path, lib_uuid, ids, now_uuid=None, abort=None, log=None, notifications=None):
    """PLAN: `wrangle.plan(cfg, m).restrict(ids)` — see `wrangle.Plan.restrict`'s own docstring
    for why the COMPUTE stays library-wide (tagcanon majority spelling, known_chars) while only
    the WRITE set narrows to the selection.

    Order matters, and matches the plan's own contract:
      1. Read the SAFETY numbers (and `n_books`) off the plan BEFORE any walk.
      2. `p.guard(force=False)` BEFORE opening a dialog for a run that cannot proceed — a
         data-loss-shaped change-set surfaces as a refused result (via `_ceremony`) carrying the
         guard's own sentence, never a job failure.
      3. Harvest the per-book review items with `p.step(decide=_record_decide()[0])`. The
         recorder always answers 'skip' (see its own docstring), which as a SIDE EFFECT empties
         `p.changes` for every book it walks — harmless here: the Plan itself is discarded at the
         end of this job (never crosses the Dispatcher boundary), and every number this result
         reports was already read in step 1, before step() ran.
      4. When `p.changes` was empty to begin with, return the D-04 empty result — the same reason
         a second identical run opens no dialog: the first run's write leaves nothing to change."""
    def body(con, ids, ctx):
        from scourgify import wrangle
        from scourgify.common import load_config
        if not ids:
            return plan_result('wrangle', [], [], '', {})
        cfg = load_config()
        m = wrangle.load_maps(cfg)
        p = wrangle.plan(cfg, m).restrict(ids)
        if not p.changes:
            return plan_result('wrangle', [], [], '', {})

        summary = ['%s: %d book(s)' % (lab, len(ch)) for lab, ch in sorted(p.changes.items())]
        safety = ('SAFETY  losing last fandom: %d | character: %d | tag assignments: %d -> %d'
                 % (p.lostF, p.lostC, p.tagsB, p.tagsA))
        n_books = p.n_books

        p.guard(force=False)          # a GuardrailError here -> _ceremony's refused result

        decide, calls = _record_decide()
        p.step(decide=decide)                          # harvest only — see docstring above
        items = [item for call in calls for item in call['items']]

        consequence = 'Normalize fields on %d book%s' % (n_books, '' if n_books == 1 else 's')
        carry = {'ids': list(ids), 'n_books': n_books}
        result = plan_result('wrangle', summary, items, consequence, carry, safety=safety)
        result['empty'] = False       # n_books > 0 here — `plan_result`'s own `not items` test
                                      # would be wrong for wrangle: a mass-only change-set (no
                                      # per-book unique edits to review) has real work to write
                                      # and an empty `items` list at the same time.
        return result

    return _ceremony('wrangle', body, lib_path, lib_uuid, ids, now_uuid,
                     abort=abort, log=log, notifications=notifications)


# ---------------------------------------------------------------------- classify (the highest-
# stakes verb this phase builds — the price on the engine button IS the confirmation, D-06/D-07)
_CLASSIFY_BATCH_DEFAULT = 200   # a chunk a user can finish in one sitting — the CLI's own --unclassified default


def job_scope_rows(lib_path, lib_uuid, ids, now_uuid=None, abort=None, log=None, notifications=None):
    """Read job: `classify.scope_options(...)`'s rows plus the never-classified batch default.

    `scope_options` needs `select.changed` and `select.pick('unclassified')`, both library reads —
    they must never run on the GUI thread, which is the entire reason this is a job and not a
    method on the scope dialog itself."""
    con, err = _open(lib_path, lib_uuid, now_uuid)
    if err:
        return {'refused': True, 'msg': err}
    try:
        from scourgify import classify, common, select
        ch = select.changed(con)
        outstanding = len(select.pick(con, 'unclassified'))
        total = common.book_count(con)
        opts, default = classify.scope_options(ch, total, outstanding)
        return {'refused': False, 'msg': '', 'opts': opts, 'default': default,
                'batch_default': _CLASSIFY_BATCH_DEFAULT}
    finally:
        con.close()


def job_plan_classify(lib_path, lib_uuid, ids, scope_spec, now_uuid=None, abort=None, log=None,
                      notifications=None):
    """PLAN: resolve the classify scope ONCE via `classify.plan()` — the expensive text extraction
    never runs twice, and the price the engine picker shows is over the exact resolved `todo` set
    the EXECUTE job will bill.

    `scope_spec` is the plain dict the scope dialog (or the never-classified shortcut) produced:
    `{'mode': 'ids'|'changed'|'unclassified'|'last'|'all', 'batch': int|None, 'last': int|None}`.
    `mode='ids'` means "exactly the books in `ids`" — the plugin's own selection-only scope, never
    one of `scope_options`' own rows (those never emit an id of `'ids'`); every other mode maps
    onto the SAME scope flag `classify`'s CLI parser already exposes, so the plugin and the CLI
    can never disagree about what a scope means."""
    def body(con, ids, ctx):
        from scourgify import classify
        _require_column(con, 'wrangled', 'classify')
        a = classify.default_opts()
        mode = (scope_spec or {}).get('mode')
        if mode == 'ids':
            if not ids:
                return plan_result('classify', [], [], '', {})
            a.books = ','.join(str(i) for i in ids)
        elif mode == 'changed':
            a.incremental = True
        elif mode == 'unclassified':
            if (scope_spec or {}).get('restrict_to_selection') and ids:
                # the "Classify the never-classified here" shortcut on a mixed selection: never
                # the ScopeDialog's own row (that one means the WHOLE-library backlog, matching
                # its label's count regardless of what happens to be selected) — only the
                # dedicated shortcut sets this flag.
                from scourgify import select as select_mod
                backlog = set(select_mod.pick(con, 'unclassified'))
                restricted = [b for b in ids if b in backlog]
                if not restricted:
                    return plan_result('classify', [], [], '', {})
                a.books = ','.join(str(i) for i in restricted)
            else:
                a.unclassified = True
                a.batch = (scope_spec or {}).get('batch') or _CLASSIFY_BATCH_DEFAULT
        elif mode == 'last':
            a.last = (scope_spec or {}).get('last') or 0
        elif mode == 'all':
            a.all = True
        else:                                    # 'skip', or an unrecognised mode — nothing to plan
            return plan_result('classify', [], [], '', {})

        p = classify.plan(a)
        if not p.todo:
            return plan_result('classify', [], [], '', {})

        from scourgify import engines
        keys = engines.resolve_keys(stored_keys())
        engs = engines.engine_rows(env=keys)
        usable = engines.usable_engines(env=keys)
        opts = engines.engine_options(engs, len(p.todo), classify.est_cost)
        default_engine = engines.default_engine_id(opts, usable=usable)
        # `engine_options` deliberately carries no usability flag (its 4-tuple is (key, id, id,
        # label) only) and no TRAITS 'limits' text — picker.py may import NOTHING from scourgify
        # (D-01's own contract), so the engine picker needs both handed to it as plain data here,
        # rather than deriving them itself.
        limits = {e: engines.trait(e, 'limits') for e, _ok, _hint in engs}

        items = [('#%d  %s' % (b, str(p.titles.get(b, ''))[:64]), {'book': b, 'title': p.titles.get(b, '')})
                for b, _d in p.todo]
        summary = ['%d book(s) to send this run' % len(p.todo),
                  '%d candidate(s) in scope' % len(p.targets)]
        consequence = 'Classify %d book%s' % (len(p.todo), '' if len(p.todo) == 1 else 's')
        carry = {'todo_ids': [b for b, _d in p.todo], 'n_todo': len(p.todo),
                'n_targets': len(p.targets), 'scope_spec': scope_spec}
        result = plan_result('classify', summary, items, consequence, carry)
        result['engines'] = opts
        result['default_engine'] = default_engine
        result['usable'] = usable
        result['engine_limits'] = limits
        return result

    return _ceremony('classify', body, lib_path, lib_uuid, ids, now_uuid,
                     abort=abort, log=log, notifications=notifications)


def job_execute_classify(lib_path, lib_uuid, ids, carry, engine_id, model, api, now_uuid=None,
                         abort=None, log=None, notifications=None):
    """EXECUTE: the engine pass and the write, together (D-05) — ONE job, so there is no second
    confirmation between the price the user saw and the spend: `p.opts.yes = True` below is what
    makes the scope step the answer to `classify.spend_gate` — the user already clicked a button
    that named the price, so a second dialog here would be exactly the confirmation this phase
    deletes.

    Rebuilds the plan from `carry['scope_spec']` RESTRICTED to `carry['todo_ids']` (mode `'ids'`)
    — the set sent to the engine is exactly the set that was priced, never a re-resolve that could
    widen it (a book added to the library between PLAN and EXECUTE must not silently join a run
    whose price the user already confirmed)."""
    def body(con, ids, ctx):
        from scourgify import artifacts, classify
        from scourgify.common import current_tags, titles as book_titles
        _require_column(con, 'wrangled', 'classify')
        todo_ids = carry['todo_ids']
        if not todo_ids:
            from scourgify.common import WriteResult
            wr = WriteResult(run_id=None, backup=None, ops=0, books=0, skipped=[], outcome='noop')
            return execute_result('classify', [], wr)

        a = classify.default_opts()
        a.books = ','.join(str(i) for i in todo_ids)
        p = classify.plan(a)
        p.opts.engine = engine_id
        p.opts.model = model
        p.opts.yes = True                        # the scope step already answered classify.spend_gate

        cur = current_tags(con)
        titles = book_titles(con, todo_ids)
        before = {b: sorted(cur.get(b, set())) for b in todo_ids}

        ask = _engine_ask(engine_id, model, p.opts.timeout)
        stop = (lambda: abort is not None and abort.is_set())

        def on_book(done, total, tagged, failed):
            if notifications is not None:
                notifications.put((done / max(total, 1),
                                   'tagged %d · failed %d' % (tagged, failed)))

        p.run(ask=ask, on_book=on_book, stop=stop)

        writer = _Writer(ctx['api'], engine=engine_id, model=model,
                         outcome=('cancelled' if p.cancelled else None))
        classify.apply_proposal(write=writer)          # the whole pending proposal (CLI parity)
        wr = writer.result

        skipped_pairs = {(b, f) for b, f in wr.skipped}
        rows, touched = [], []
        for b, _d in p.todo:
            entry = p.proposal.get(b)
            if entry is None:
                continue                          # errored this run — reported via engine_failures
            vt, _nt = entry
            after = sorted(set(before.get(b, ())) | set(vt))
            skipped = (b, 'tags') in skipped_pairs
            state = ('skipped: changed since the plan' if skipped
                    else 'written' if vt else 'no new tags')
            rows.append({'book': b, 'title': titles.get(b, ''), 'field': 'tags',
                        'before': ', '.join(before.get(b, ())), 'after': ', '.join(after),
                        'state': state})
            if not skipped:
                touched.append(b)

        fails = {int(r['book_id']): r.get('reason', '') for r in artifacts.read_rows(artifacts.fail())
                if str(r.get('book_id', '')).isdigit()}
        engine_failures = [(b, fails[b]) for b in todo_ids if b in fails]

        return execute_result('classify', rows, wr, engine_failures=engine_failures, touched=touched)

    return _ceremony('classify', body, lib_path, lib_uuid, ids, now_uuid, api=api,
                     abort=abort, log=log, notifications=notifications)
