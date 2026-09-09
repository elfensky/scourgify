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

# The job bodies live in the Qt-free plugin/jobs.py (D-12) — this import costs the GUI thread
# nothing, because jobs.py holds no module-level core import of its own (every scourgify import
# in it lives inside a function, same discipline this file holds itself to).
from calibre_plugins.scourgify import jobs

FREE, COSTS, WRITES = 'free', 'costs', 'writes'
SOON = 'not built yet'                    # the honest reason a phase-6/7 slot is grey

# verb -> the EXECUTE job it dispatches. One row per write verb this phase adds; every job takes
# the same (lib, uuid, ids, carry, api, now_uuid) argument tuple.
_EXECUTE_JOBS = {'staleness': jobs.job_execute_staleness}


# ---------------------------------------------------------------------- Qt layer (GUI thread)
# Every job body lives in the Qt-free plugin/jobs.py (D-12) — this file holds ZERO core imports,
# anywhere, not even inside a job_* function, because it dispatches jobs but never defines one.

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
        # Action-side mirror of "a write-run is live", read (never written) by build_menu. Plain
        # instance state, no core call — the core write-run lock (common._acquire_write_lock)
        # stays the authoritative refusal, reached in the EXECUTE job and rendered via the result
        # dialog (D-11); this is advisory-only, so the menu greys instantly without a core read.
        self._write_running = ''

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
        if not n:
            status_reason = 'nothing selected'
        elif self._write_running:
            status_reason = '%s is running' % self._write_running
        else:
            status_reason = None
        self._verb(m, 'Re-derive status', WRITES, status_reason, lambda: self.staleness(scope),
                   "Re-derive #status from #updated age for the selection's activity-family books.")
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
                  jobs.job_inspect, (lib, uuid, ids, self.current_uuid))

    def db_smoke(self, scope):
        lib, uuid, _ids = scope
        self._run('scourgify: db-from-worker smoke', jobs.job_db_smoke,
                  (lib, uuid, self.gui.current_db.new_api, self.current_uuid))

    def staleness(self, scope):
        """PLAN: which of the selected books' #status would change? Dispatches the picker on
        completion (`_plan_done`), which in turn dispatches the EXECUTE job on Run."""
        lib, uuid, ids = scope
        self._run('scourgify: status for %s' % _these(len(ids)),
                  jobs.job_plan_staleness, (lib, uuid, ids, self.current_uuid),
                  done=self._plan_done('staleness'))

    def _plan_done(self, verb):
        """A Dispatcher-safe callback factory: a real job failure still reaches
        `gui.job_exception`; a refused PLAN (identity mismatch, a GuardrailError) shows its
        message plainly; an empty PLAN (D-04) shows a one-line notice and opens no dialog;
        otherwise the picker opens, and its Run button dispatches the matching EXECUTE job."""
        def done(job):
            if job.failed:
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            if r.get('empty'):
                return info_dialog(self.gui, 'scourgify',
                                   'Nothing to change for these books.', show=True)
            from calibre_plugins.scourgify.picker import show_picker
            show_picker(self.gui, r, lambda carry: self._start_execute(verb, carry))
        return done

    def _start_execute(self, verb, carry):
        """Dispatched from the picker's Run button — re-reads the selection at click time (it may
        have changed since the PLAN job ran) and passes the GUI's live `new_api` handle exactly as
        `db_smoke` already does, so the EXECUTE job writes in-process, never through `run_writer`."""
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._write_running = verb
        self._run('scourgify: writing %s' % verb, _EXECUTE_JOBS[verb],
                  (lib, uuid, ids, carry, db.new_api, self.current_uuid), done=self._execute_done)

    def _execute_done(self, job):
        """Dispatcher-wrapped: clears the write-run mirror, shows the diff-after result dialog
        (a refused result renders there too — D-11), and refreshes exactly the touched rows so the
        library view reflects the write without a restart."""
        self._write_running = ''
        if job.failed:
            return self.gui.job_exception(job, dialog_title='scourgify failed')
        r = job.result or {}
        from calibre_plugins.scourgify.result_dialog import show_result
        show_result(self.gui, r)
        touched = r.get('touched') or []
        if touched:
            self.gui.library_view.model().refresh_ids(list(touched))

    def _run(self, description, func, args, done=None):
        """`done` lets a caller own its own completion (the settings dialog shows a probe result
        inline instead of in a dialog). It is wrapped here, so a caller cannot forget to — an
        unwrapped callback runs on the WORKER thread and touches Qt from it.

        `description` shows in Calibre's job list: never put a key in it."""
        t0 = time.monotonic()
        job = ThreadedJob('scourgify', description, func, args, {},
                          Dispatcher(done or self._done))
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
