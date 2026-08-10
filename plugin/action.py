#!/usr/bin/env python3
"""The Qt layer: a toolbar button whose menu IS the current selection.

Deliberately thin, and kept thin BY ENFORCEMENT (tests/test_plugin_source.py, the same mechanism
tests/test_cli.py uses on wizard.py). Four invariants that file will fail on:

  1. `run_writer(` appears nowhere, and neither does subprocess / multiprocessing /
     ThreadPoolExecutor — the plugin never spawns a second writer against a library the GUI holds
     open (NLSpec B2.1), and never runs work off Calibre's job system (B3.2).
  2. NOTHING imports scourgify at module level. Every core import lives inside a `job_*` function,
     so the GUI thread cannot reach a library read even by accident — the failure that froze
     Calibre during the spike (briefing footgun 1). Measured: the cheapest core read is 6 ms and
     wrangle.load_maps() is 870 ms, so B3's <100 ms budget is a *dispatch* budget; no read qualifies.
  3. Every ThreadedJob callback is Dispatcher-wrapped — ThreadedJob.start_work calls
     `self.callback(self)` from the WORKER thread (disassembled, Calibre 9.11), so an unwrapped
     callback touches Qt off the GUI thread.
  4. Job functions take abort/log/notifications.

Phase 4 is read-only. Every verb that writes is on its fixed slot, greyed with its reason — a slot
never changes meaning (B1 edge case, mirroring the wizard's fixed-slot rule).
"""
import os
import time

from qt.core import QMenu, QToolButton

from calibre import prints
from calibre.gui2 import Dispatcher, info_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.threaded_jobs import ThreadedJob

FREE, COSTS, WRITES = 'free', 'costs', 'writes'
SOON = 'not built yet'                    # the honest reason a phase-6/7 slot is grey


# ---------------------------------------------------------------- job functions (worker thread)
# Everything below this line runs OFF the GUI thread. It may import scourgify; it may not touch a
# Qt object. Results are plain data handed back to a Dispatcher-wrapped callback.

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
            return _library_scope(con, seen, proposal, failures, artifacts, common, select, setup_mod)

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


def _library_scope(con, seen, proposal, failures, artifacts, common, select, setup_mod):
    """The whole-library answer — the dashboard header's numbers, each from its named source."""
    books = common.book_count(con)
    backlog = select.pick(con, 'unclassified', seen=seen)
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
    import csv
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
            'msg': 'new_api read + scratch write from a ThreadedJob worker: <b>%s</b>' % ('ok' if ok else 'MISMATCH'),
            'det': '\n'.join(out)}


# ---------------------------------------------------------------------- Qt layer (GUI thread)

class ScourgifyAction(InterfaceAction):
    name = 'scourgify'
    action_spec = ('scourgify', None, 'Normalize and tag this library', None)
    action_type = 'current'
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup

    def genesis(self):
        """Wire the action and NOTHING else — this runs on the GUI thread during action setup,
        and initialization_complete() is no better (footgun 1: a library scan there froze Calibre
        and read as a crash)."""
        self.menu = QMenu(self.gui)
        self.menu.setToolTipsVisible(True)      # else every item's hint is invisible dead text
        self.menu.aboutToShow.connect(self.build_menu)
        self.qaction.setMenu(self.menu)

    def initialization_complete(self):
        """Where gui.current_db is finally real — and where the spike froze Calibre by reading the
        library. So: nothing, unless the phase-4 GUI test hook is armed (a test hook, not a user
        feature — the same deal as $SCOURGIFY_SCRIPT for the wizard)."""
        if os.environ.get('SCOURGIFY_SMOKE'):
            from calibre_plugins.scourgify.selftest import run
            run(self)

    def build_menu(self):
        """The selection is the command (B1). Pure Qt work over an id list — no core call, so the
        GUI-thread portion is a menu build, not a library read."""
        t0 = time.monotonic()
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        # Captured HERE, at click time, and carried into the job: the selection may change while
        # the menu is open, and the library may be switched before the job starts.
        scope = (db.library_path, getattr(db.new_api, 'library_id', None), ids)
        n = len(ids)

        m = self.menu
        m.clear()
        head = m.addAction('nothing selected — library scope' if not n else
                           '1 book selected' if n == 1 else '%d books selected' % n)
        head.setEnabled(False)
        m.addSeparator()

        self._verb(m, 'Classify %s' % _these(n), COSTS, SOON)
        self._verb(m, 'Normalize fields', WRITES, SOON)
        self._verb(m, 'Re-derive status', WRITES, SOON)
        m.addSeparator()
        self._verb(m, 'What does scourgify know?', FREE, None,
                   lambda: self.inspect(scope),
                   'Last classified when, from which proposal, and what you rejected.' if n == 1
                   else 'Classification state across the selection, and what is outstanding.')
        self._verb(m, 'Classify the never-classified here', COSTS, SOON)
        self._verb(m, 'Retry on another engine', COSTS, SOON)
        m.addSeparator()
        self._verb(m, 'Edit tags…', WRITES, SOON)
        if not n:
            m.addSeparator()
            self._verb(m, 'Open dashboard…', FREE, SOON)
        if os.environ.get('SCOURGIFY_SMOKE'):
            m.addSeparator()
            self._verb(m, 'Smoke: db from a job worker', WRITES, None,
                       lambda: self.db_smoke(scope), 'Throwaway libraries only.')
        prints('scourgify: menu for %d ids built in %.1f ms' % (n, (time.monotonic() - t0) * 1000))

    def _verb(self, menu, label, tag, disabled_reason, slot=None, hint=''):
        """One fixed slot. A verb that does not apply greys out WITH ITS REASON rather than
        vanishing, so a slot never comes to mean something else (B1 edge case)."""
        text = '%s — %s' % (label, tag)
        if disabled_reason:
            text += ' (%s)' % disabled_reason
        a = menu.addAction(text)
        a.setToolTip(hint or disabled_reason or '')
        if slot is None or disabled_reason:
            a.setEnabled(False)
        else:
            a.triggered.connect(lambda checked=False: slot())
        return a

    # ---- dispatch: build a job, hand it to Calibre, return. Nothing waits here. ----
    def current_uuid(self):
        """Which library does the GUI have open RIGHT NOW — called from the job, so it can notice
        a switch that happened after the click. An attribute read, not a Qt call."""
        db = self.gui.current_db
        return getattr(db.new_api, 'library_id', None) if db is not None else None

    def inspect(self, scope):
        lib, uuid, ids = scope
        self._run('scourgify: what do I know about %s' % _these(len(ids)),
                  job_inspect, (lib, uuid, ids, self.current_uuid))

    def db_smoke(self, scope):
        lib, uuid, _ids = scope
        self._run('scourgify: db-from-worker smoke', job_db_smoke,
                  (lib, uuid, self.gui.current_db.new_api, self.current_uuid))

    def _run(self, description, func, args):
        t0 = time.monotonic()
        job = ThreadedJob('scourgify', description, func, args, {}, Dispatcher(self._done))
        self.gui.job_manager.run_threaded_job(job)
        prints('scourgify: dispatched in %.1f ms' % ((time.monotonic() - t0) * 1000))

    def _done(self, job):
        """Dispatcher-wrapped: ThreadedJob calls this from the worker thread, and everything it
        touches is Qt."""
        if job.failed:
            return self.gui.job_exception(job, dialog_title='scourgify failed')
        r = job.result or {}
        info_dialog(self.gui, r.get('title', 'scourgify'), r.get('msg', ''),
                    det_msg=r.get('det', ''), show=True)


def _these(n):
    return 'this book' if n == 1 else 'these %d books' % n if n else 'the library'
