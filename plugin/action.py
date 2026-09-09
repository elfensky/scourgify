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

from qt.core import QMenu, QToolButton, Qt

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

# verb -> the EXECUTE job it dispatches, for every verb whose EXECUTE job takes the SAME
# (lib, uuid, ids, carry, api, now_uuid) argument tuple as `_start_execute` builds. Classify's
# EXECUTE job needs `engine_id`/`model` between `carry` and `api` (the engine picker's answer), so
# it dispatches through its own `_start_classify_execute` instead of this generic table. Wrangle's
# EXECUTE job needs `ticks` between `carry` and `api` (the reviewer's per-book replay, D-02) for
# the same reason — `_start_execute` special-cases it below rather than adding it here.
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
        # Cached exactly like `_write_running`: the FanFicFare Comments reason from the last
        # completed synopsis PLAN job (T-02-21). Set on a degraded-mode-eligible refusal, cleared
        # on any later PLAN that DIDN'T hit that refusal — so fixing the FanFicFare setting
        # un-greys 'Settle descriptions' the next time the menu opens, no Calibre restart needed.
        self._comments_reason = ''
        # Cached exactly the same way: the empty-backfill reason from the last completed backfill
        # PLAN job — cleared automatically the next time that PLAN reports real work, so
        # promoting a new candidate un-greys 'Backfill promoted tags' with no Calibre restart.
        self._backfill_reason = ''
        # The 'Retry on another engine' slot's own cache (D-10, plan 02-08): refreshed by
        # dispatching `jobs.job_retry_targets` on menu open and after every completed job
        # (`_refresh_retry_targets`) — `build_menu` cannot call the core, so the menu's greying
        # and the chooser it opens both read this cache, never a live read.
        self._retry_groups = []
        self._retry_reason = 'nothing to retry'

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

        write_reason = ('%s is running' % self._write_running) if self._write_running else None
        status_reason = 'nothing selected' if not n else write_reason
        self._verb(m, 'Classify %s' % _these(n), COSTS, status_reason,
                   None if not n else lambda: self.classify(scope),
                   'Choose a scope, then an engine — the price on the button IS the confirmation.')
        self._verb(m, 'Normalize fields', WRITES, status_reason, lambda: self.wrangle(scope),
                   'Deterministic normalization (alias folds, junk drop, mis-filed values) — '
                   'reviewable per book, with the same data-loss guards `apply` has.')
        self._verb(m, 'Re-derive status', WRITES, status_reason, lambda: self.staleness(scope),
                   "Re-derive #status from #updated age for the selection's activity-family books.")
        synopsis_reason = status_reason or self._comments_reason or None
        self._verb(m, 'Settle descriptions', COSTS, synopsis_reason, lambda: self.synopsis(scope),
                   'Judge each existing blurb — keep the good ones, generate a spoiler-safe back '
                   'cover for the rest — reviewed per book before anything is written.')
        # Adjudicate/backfill are library-scope verbs (classify's OUTPUT, not a book selection),
        # so — unlike the four verbs above — they are never disabled just because nothing is
        # selected; only a running write (or, for backfill, an empty plan) greys them.
        self._verb(m, 'Adjudicate new tags', COSTS, write_reason, lambda: self.promote(scope),
                   "Advocate + skeptic adjudication of classify's new-tag candidates on a "
                   "judge-capable engine — reviewed 1-by-1 before anything is folded into "
                   "overrides/.")
        self._verb(m, 'Backfill promoted tags', WRITES,
                   write_reason or self._backfill_reason or None, lambda: self.backfill(scope),
                   'Apply promoted/aliased tags onto the books that first proposed them — '
                   'deterministic, no LLM.')
        m.addSeparator()
        self._verb(m, 'What does scourgify know?', FREE, None,
                   lambda: self.inspect(scope),
                   'Last classified when, from which proposal, and what you rejected.' if n == 1
                   else 'Classification state across the selection, and what is outstanding.')
        self._verb(m, 'Classify the never-classified here', COSTS, write_reason,
                   lambda: self.classify_backlog(scope),
                   'Skip the scope dialog: never-classified books, restricted to the selection '
                   'when one exists — a chunk at a time (the scope that advances).')
        retry_reason = write_reason or self._retry_reason or None
        self._verb(m, 'Retry on another engine', COSTS, retry_reason,
                   None if retry_reason else lambda: self._open_retry_chooser(),
                   "Retry the refusal-class classify failures for %s on a different engine — "
                   "the same recovery a result dialog's own retry buttons offer."
                   % ('the selection' if n else 'the library'))
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
        self._refresh_retry_targets(scope)

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

    def wrangle(self, scope):
        """PLAN: which of the selected books' fields would change under the deterministic
        normalization pass? Same completion path as `staleness` — `_plan_done` opens the picker,
        and its Run (or a finished 1-by-1 review) dispatches `job_execute_wrangle` via
        `_start_execute`."""
        lib, uuid, ids = scope
        self._run('scourgify: wrangle plan for %s' % _these(len(ids)),
                  jobs.job_plan_wrangle, (lib, uuid, ids, self.current_uuid),
                  done=self._plan_done('wrangle'))

    # ---- synopsis: PLAN -> engine picker -> EXECUTE (harvest) -> review -> EXECUTE (write) ----
    def synopsis(self, scope, force=False):
        """'Settle descriptions' — PLAN: `jobs.job_plan_synopsis` resolves the selection and
        prices every usable engine over it (sends nothing — see that job's own docstring).
        `force=True` is the degraded self-healing mode's own re-dispatch (see
        `_synopsis_plan_done`) — never the menu's own default click, which always passes False."""
        lib, uuid, ids = scope
        self._run('scourgify: synopsis plan for %s' % _these(len(ids)),
                  jobs.job_plan_synopsis, (lib, uuid, ids, {'force': force, 'batch': None}, self.current_uuid),
                  done=self._synopsis_plan_done(scope))

    def _synopsis_plan_done(self, scope):
        """The FanFicFare guard renders as an explicit, visible choice (T-02-21): a refusal
        carrying `degraded_available` shows the guard's own sentence plus ONE control offering
        the degraded mode (`picker.show_refusal`), and caches the reason on the action
        (`self._comments_reason`) so the menu greys the slot without a second job round-trip.
        Any OTHER PLAN outcome clears that cache — fixing the FanFicFare setting un-greys the
        verb the next time the menu opens, no Calibre restart needed."""
        def done(job):
            if job.failed:
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                if r.get('degraded_available'):
                    self._comments_reason = 'FanFicFare Comments is not set to New Only'
                    from calibre_plugins.scourgify.picker import show_refusal
                    return show_refusal(self.gui, r, lambda: self.synopsis(scope, force=True))
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            self._comments_reason = ''
            if r.get('empty'):
                return info_dialog(self.gui, 'scourgify', 'Nothing to settle for these books.', show=True)
            from calibre_plugins.scourgify.picker import show_engine_picker
            show_engine_picker(self.gui, r,
                               lambda engine_id: self._start_synopsis_execute(scope, r['carry'], engine_id))
        return done

    def _start_synopsis_execute(self, scope, carry, engine_id):
        """Dispatched from the engine button (D-07) — the FIRST of two `job_execute_synopsis`
        dispatches (`ticks=None`): the engine pass runs for real, but the write= transport is a
        no-op (`jobs._NullWriter`) — nothing is written until the reviewer has seen every
        generated description (D-13). No `model=` override in this phase's UI, matching
        classify's own engine picker."""
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._write_running = 'synopsis'
        self._run('scourgify: settling descriptions for %s' % _these(len(ids)),
                  jobs.job_execute_synopsis,
                  (lib, uuid, ids, carry, engine_id, '', db.new_api, None, self.current_uuid),
                  done=self._synopsis_review_done(carry, engine_id))

    def _synopsis_review_done(self, carry, engine_id):
        """The harvest dispatch's completion: its `items` (each generated description paired
        against the blurb it would replace) feed the SAME `Picker`/Review-1-by-1 table every
        other verb uses (D-01) — no second review widget for this verb. Run (or a finished
        1-by-1 review) fires the SECOND, real dispatch (`_finish_synopsis`)."""
        def done(job):
            if job.failed:
                self._write_running = ''
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                self._write_running = ''
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            from calibre_plugins.scourgify.picker import show_picker
            dlg = show_picker(self.gui, r,
                              lambda run_carry: self._finish_synopsis(run_carry, engine_id, dlg))
        return done

    def _finish_synopsis(self, carry, engine_id, dlg):
        """The SECOND `job_execute_synopsis` dispatch — `ticks` replays exactly what the
        reviewer left (`_synopsis_ticks`, reusing `picker.checklist_decide` unchanged, D-04):
        `[]` (Run clicked with no review) accepts every generated description, matching D-02's
        one-click-Run contract. Clears `_write_running` via the shared `_execute_done`."""
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        ticks = _synopsis_ticks(dlg)
        self._run('scourgify: writing settled descriptions', jobs.job_execute_synopsis,
                  (lib, uuid, ids, carry, engine_id, '', db.new_api, ticks, self.current_uuid),
                  done=self._execute_done)

    # ---- promote: PLAN -> engine picker -> EXECUTE (harvest) -> review -> EXECUTE (apply ticks) ----
    def promote(self, scope):
        """'Adjudicate new tags' — PLAN: `jobs.job_plan_promote` reads the undecided candidates
        and prices every judge-capable engine over them (sends nothing). Library-scope: `ids` is
        always `[]`, never the selection — promote works over classify's OUTPUT, not a book pick."""
        lib, uuid, _ids = scope
        self._run('scourgify: promote plan', jobs.job_plan_promote,
                  (lib, uuid, [], {}, self.current_uuid), done=self._promote_plan_done())

    def _promote_plan_done(self):
        def done(job):
            if job.failed:
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            if r.get('empty'):
                return info_dialog(self.gui, 'scourgify', 'No undecided candidates to adjudicate.', show=True)
            from calibre_plugins.scourgify.picker import show_engine_picker
            show_engine_picker(self.gui, r,
                               lambda engine_id: self._start_promote_execute(r['carry'], engine_id))
        return done

    def _start_promote_execute(self, carry, engine_id):
        """Dispatched from the engine button — re-reads the live library at click time (like
        `_start_classify_execute`/`_start_synopsis_execute`; a multi-step round trip must not
        carry a stale `lib_path` from when the menu was built). The FIRST of two
        `job_execute_promote` dispatches (`ticks=None`): the adjudication runs for real, but
        nothing is folded into the overrides directory or the ledger until the reviewer has seen
        every verdict (T-02-27). Promote writes no book field, so no `_write_running` here — that
        flag names a book WRITE, and this dispatch is not one (see `_start_backfill_execute`, the
        only one of the four promote/backfill dispatches that is)."""
        db = self.gui.current_db
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._run('scourgify: adjudicating new tags', jobs.job_execute_promote,
                  (lib, uuid, [], carry, engine_id, '', None, None, self.current_uuid),
                  done=self._promote_review_done(carry, engine_id))

    def _promote_review_done(self, carry, engine_id):
        """The harvest dispatch's completion: its `items` (each candidate's advocate/skeptic
        verdict) feed the SAME `Picker`/Review-1-by-1 table every other verb uses (D-01) — no
        second review widget for this verb."""
        def done(job):
            if job.failed:
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            if r.get('empty'):
                return info_dialog(self.gui, 'scourgify', 'Nothing to review — no applicable verdicts.', show=True)
            from calibre_plugins.scourgify.picker import show_picker
            dlg = show_picker(self.gui, r, lambda run_carry: self._finish_promote(run_carry, engine_id, dlg))
        return done

    def _finish_promote(self, carry, engine_id, dlg):
        """The SECOND `job_execute_promote` dispatch — `ticks` replays exactly what the reviewer
        left (`_promote_ticks`, reusing `picker.checklist_decide` unchanged, D-04): `[]` (Run
        clicked with no review) accepts every verdict, matching D-02's one-click-Run contract."""
        db = self.gui.current_db
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        ticks = _promote_ticks(dlg)
        self._run('scourgify: applying adjudicated verdicts', jobs.job_execute_promote,
                  (lib, uuid, [], carry, engine_id, '', None, ticks, self.current_uuid),
                  done=self._promote_apply_done)

    def _promote_apply_done(self, job):
        """Promote writes no book field, so unlike `_execute_done` this never refreshes the
        library view — there is nothing in it to refresh."""
        if job.failed:
            return self.gui.job_exception(job, dialog_title='scourgify failed')
        r = job.result or {}
        from calibre_plugins.scourgify.result_dialog import show_result
        show_result(self.gui, r)

    # ---- backfill: PLAN -> Picker -> EXECUTE (deterministic, no LLM, no engine picker) ----
    def backfill(self, scope):
        """'Backfill promoted tags' — PLAN: `jobs.job_plan_backfill` previews which books would
        gain which promoted/aliased tags. Library-scope, like `promote` above: `ids` is always
        `[]`."""
        lib, uuid, _ids = scope
        self._run('scourgify: backfill plan', jobs.job_plan_backfill,
                  (lib, uuid, [], self.current_uuid), done=self._backfill_plan_done())

    def _backfill_plan_done(self):
        """Caches the empty-backfill reason on the action (`self._backfill_reason`), mirroring
        `self._comments_reason`'s own cache — cleared the next time this PLAN reports real work,
        so promoting a new candidate un-greys the slot with no Calibre restart needed."""
        def done(job):
            if job.failed:
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            if r.get('empty'):
                self._backfill_reason = 'nothing to backfill'
                return info_dialog(self.gui, 'scourgify',
                                   'Nothing to backfill — every source book already carries its '
                                   'promoted tags.', show=True)
            self._backfill_reason = ''
            from calibre_plugins.scourgify.picker import show_picker
            dlg = show_picker(self.gui, r, lambda carry: self._start_backfill_execute(carry, dlg))
        return done

    def _start_backfill_execute(self, carry, dlg):
        """Dispatched from the picker's Run button (or a finished 1-by-1 review). `decide` is
        `picker.backfill_decide(dlg)` when the reviewer actually reviewed (`dlg.reviewed`) — the
        backfill-shaped adapter (`decide(chg, adds) -> chg_to_write`, falsy aborts, T-02-15) — or
        the identity function when the plain Run button was clicked (nothing reviewed, D-02's
        one-click-accepts-everything contract). `_write_running` is set HERE, and only here: this
        is the only one of the four promote/backfill dispatches that writes a book field."""
        from calibre_plugins.scourgify.picker import backfill_decide
        decide = backfill_decide(dlg) if getattr(dlg, 'reviewed', False) else (lambda chg, adds: chg)
        db = self.gui.current_db
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._write_running = 'backfill'
        self._run('scourgify: backfilling promoted tags', jobs.job_execute_backfill,
                  (lib, uuid, [], carry, decide, db.new_api, self.current_uuid),
                  done=self._execute_done)

    def _plan_done(self, verb):
        """A Dispatcher-safe callback factory: a real job failure still reaches
        `gui.job_exception`; a refused PLAN (identity mismatch, a GuardrailError) shows its
        message plainly; an empty PLAN (D-04) shows a one-line notice and opens no dialog;
        otherwise the picker opens, and its Run button dispatches the matching EXECUTE job.

        The picker object itself (`dlg`) is threaded into `_start_execute`'s `on_run` callback —
        every verb still dispatches through the SAME `_start_execute`, so this stays the ONE
        completion path (no new one added for wrangle); only wrangle's own branch inside
        `_start_execute` actually reads `dlg` (see `_wrangle_ticks` — `_step_walk` calls its
        `decide=` once per book, so a flat review table's ticks must be regrouped per book, unlike
        every other verb's single-call review)."""
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
            dlg = show_picker(self.gui, r, lambda carry: self._start_execute(verb, carry, dlg))
        return done

    # ---- classify: scope dialog -> PLAN job -> engine picker -> EXECUTE job (D-06/D-07) ----
    def classify(self, scope):
        """'Classify <these N books>' — step 1: a read job for `classify.scope_options`' rows
        (library reads that must never run on the GUI thread), then `ScopeDialog`."""
        lib, uuid, ids = scope
        self._run('scourgify: classify scope for %s' % _these(len(ids)),
                  jobs.job_scope_rows, (lib, uuid, ids, self.current_uuid),
                  done=self._scope_rows_done(scope))

    def classify_backlog(self, scope):
        """'Classify the never-classified here' — skips the scope dialog entirely and goes
        straight to the PLAN job with the never-classified scope, restricted to the selection
        when one exists (the shortcut for a mixed selection); the batch size is the job's own
        default (`None` here)."""
        self._classify_plan(scope, {'mode': 'unclassified', 'batch': None,
                                    'restrict_to_selection': True})

    def _scope_rows_done(self, scope):
        def done(job):
            if job.failed:
                return self.gui.job_exception(job, dialog_title='scourgify failed')
            r = job.result or {}
            if r.get('refused'):
                return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
            _lib, _uuid, ids = scope
            from calibre_plugins.scourgify.picker import show_scope_dialog
            show_scope_dialog(self.gui, r, len(ids),
                              lambda scope_spec: self._classify_plan(scope, scope_spec))
        return done

    def _classify_plan(self, scope, scope_spec):
        """Step 2: the PLAN job resolves the todo set ONCE through `classify.plan()` and prices
        every usable engine over it."""
        lib, uuid, ids = scope
        self._run('scourgify: classify plan for %s' % _these(len(ids)),
                  jobs.job_plan_classify, (lib, uuid, ids, scope_spec, self.current_uuid),
                  done=self._classify_plan_done)

    def _classify_plan_done(self, job):
        """Step 3: the engine picker opens over the PLAN result — clicking a button IS the run
        (D-07), dispatching the EXECUTE job with no further dialog in between."""
        if job.failed:
            return self.gui.job_exception(job, dialog_title='scourgify failed')
        r = job.result or {}
        if r.get('refused'):
            return info_dialog(self.gui, 'scourgify', r.get('msg', ''), show=True)
        if r.get('empty'):
            return info_dialog(self.gui, 'scourgify', 'Nothing to classify for these books.', show=True)
        from calibre_plugins.scourgify.picker import show_engine_picker
        show_engine_picker(self.gui, r, lambda engine_id: self._start_classify_execute(r['carry'], engine_id))

    def _start_classify_execute(self, carry, engine_id):
        """Dispatched from the engine button — re-reads the selection at click time and passes the
        GUI's live `new_api` handle, exactly as `_start_execute` does for every other write verb.
        No `model=` override in this phase's UI: the engine's own default model is used."""
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._write_running = 'classify'
        self._run('scourgify: classifying %s with %s' % (_these(len(ids)), engine_id),
                  jobs.job_execute_classify, (lib, uuid, ids, carry, engine_id, '', db.new_api, self.current_uuid),
                  done=self._execute_done)

    def _start_execute(self, verb, carry, dlg=None):
        """Dispatched from the picker's Run button (or a finished 1-by-1 review) — re-reads the
        selection at click time (it may have changed since the PLAN job ran) and passes the GUI's
        live `new_api` handle exactly as `db_smoke` already does, so the EXECUTE job writes
        in-process, never through `run_writer`.

        `dlg` is the closed-but-not-destroyed Picker (see `_plan_done`); only wrangle reads it."""
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._write_running = verb
        if verb == 'wrangle':
            ticks = _wrangle_ticks(dlg)
            self._run('scourgify: writing %s' % verb, jobs.job_execute_wrangle,
                      (lib, uuid, ids, carry, ticks, db.new_api, self.current_uuid),
                      done=self._execute_done)
            return
        self._run('scourgify: writing %s' % verb, _EXECUTE_JOBS[verb],
                  (lib, uuid, ids, carry, db.new_api, self.current_uuid), done=self._execute_done)

    def _execute_done(self, job):
        """Dispatcher-wrapped: clears the write-run mirror, shows the diff-after result dialog
        (a refused result renders there too — D-11), refreshes exactly the touched rows so the
        library view reflects the write without a restart, and refreshes the retry-targets cache
        (D-10) — a fresh failure may have just appeared, or a retried one may have just cleared."""
        self._write_running = ''
        if job.failed:
            return self.gui.job_exception(job, dialog_title='scourgify failed')
        r = job.result or {}
        from calibre_plugins.scourgify.result_dialog import show_result
        on_retry = self._retry_classify if r.get('verb') == 'classify' else None
        show_result(self.gui, r, on_retry=on_retry)
        touched = r.get('touched') or []
        if touched:
            self.gui.library_view.model().refresh_ids(list(touched))
        db = self.gui.current_db
        ids = list(self.gui.library_view.get_selected_ids())
        self._refresh_retry_targets((db.library_path, getattr(db.new_api, 'library_id', None), ids))

    # ---- retry (D-10, plan 02-08): the ONE `jobs.job_retry_classify` entry point, reached from
    # both the result dialog's own retry buttons AND this menu's 'Retry on another engine' slot ----
    def _refresh_retry_targets(self, scope):
        """Fire-and-forget: refreshes `self._retry_groups`/`self._retry_reason` for `scope`'s
        selection (or the library, `ids=[]`) so the menu's greying — and the chooser it opens —
        stay live without a synchronous core read on the GUI thread. Dispatched on menu open and
        after every completed write."""
        lib, uuid, ids = scope
        self._run('scourgify: retry targets for %s' % _these(len(ids)), jobs.job_retry_targets,
                  (lib, uuid, ids, self.current_uuid), done=self._retry_targets_done)

    def _retry_targets_done(self, job):
        """A failed/refused read leaves the slot greyed (fail closed, never a stale 'live' state);
        only REFUSAL-class groups that actually carry a target are worth surfacing — auth/
        permission groups (no target ever) and an empty groups list both read as nothing to do."""
        if job.failed or (job.result or {}).get('refused'):
            self._retry_groups, self._retry_reason = [], 'nothing to retry'
            return
        groups = (job.result or {}).get('groups') or []
        live = [g for g in groups if g.get('cls') == 'refusal' and g.get('targets')]
        self._retry_groups = live
        self._retry_reason = '' if live else 'nothing to retry'

    def _open_retry_chooser(self):
        """The menu slot's own dispatch — a small chooser listing the SAME targets a result
        dialog's own retry buttons would render (`self._retry_groups`, refreshed by
        `_refresh_retry_targets`), reused verbatim rather than re-derived."""
        from calibre_plugins.scourgify.picker import show_retry_chooser
        show_retry_chooser(self.gui, self._retry_groups, self._retry_classify)

    def _retry_classify(self, engine_id, book_ids):
        """The ONE retry dispatch — called from a result dialog's retry button AND the menu's
        chooser, through the ONE `self._run` site, exactly like every other write verb."""
        db = self.gui.current_db
        lib, uuid = db.library_path, getattr(db.new_api, 'library_id', None)
        self._write_running = 'classify'
        self._run('scourgify: retrying %s with %s' % (_these(len(book_ids)), engine_id),
                  jobs.job_retry_classify,
                  (lib, uuid, book_ids, engine_id, '', db.new_api, self.current_uuid),
                  done=self._execute_done)

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


def _wrangle_ticks(dlg):
    """Convert the picker's flat, all-books review table (D-02) into wrangle's per-book `ticks` —
    `wrangle.Plan.step` (via `_step_walk`) calls its `decide=` ONCE PER BOOK, so a single global
    tick state has to be regrouped into one `(accepted_idx, rejected_idx, action)` triple per
    book, with indices LOCAL to that book's own run of items — matching the call order
    `jobs.job_plan_wrangle`'s `_record_decide()` recorded and `jobs._replay_decide` expects to
    replay. This is why wrangle cannot reuse `picker.checklist_decide` (D-04's own adapter):
    that one returns GLOBAL table-row indices for every call, which is correct only for a
    single-call review (staleness, promote, classify, overrides) and wrong from the second book
    onward here.

    Not reviewed (the plain Run button) -> `[]`, which `_replay_decide` treats as accept
    everything (D-02's one-click path — no Qt call needed to answer that). A book with every item
    left unticked -> `'skip'` (deferred, no reject row — `_step_walk`'s own semantics). A book
    with a partial untick -> `'apply'` with the unticked LOCAL indices as declared rejects. The
    picker's own global "skip" button (abandon the whole review) forces every book to `'skip'`
    rather than falling through to `_replay_decide`'s empty-ticks fallback, which means the
    opposite (accept everything)."""
    if dlg is None or not getattr(dlg, 'reviewed', False) or dlg._table is None:
        return []
    from calibre_plugins.scourgify.picker import BOOK_ROLE
    table = dlg._table
    n = table.rowCount()
    books = [table.item(row, 0).data(BOOK_ROLE) for row in range(n)]
    kept = [table.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(n)]
    global_skip = dlg.review_action in ('skip', 'quit')
    ticks, start = [], 0
    while start < n:
        end = start + 1
        while end < n and books[end] == books[start]:
            end += 1
        if global_skip:
            ticks.append(([], [], 'skip'))
        else:
            run = kept[start:end]
            acc = [i for i, k in enumerate(run) if k]
            rej = [i for i, k in enumerate(run) if not k]
            ticks.append((acc, [] if not acc else rej, 'skip' if not acc else 'apply'))
        start = end
    return ticks


def _synopsis_ticks(dlg):
    """Build the single-call `ticks` list `job_execute_synopsis`'s second dispatch replays.
    Reuses `picker.checklist_decide` (D-04) rather than re-deriving the tick-reading logic a
    second time — `synopsis.step` (unlike `wrangle.Plan.step`'s own `_step_walk`) calls its
    `decide=` exactly ONCE for the whole batch of generated descriptions, the same single-call
    shape `checklist_decide` already covers (staleness, promote, classify all share it); only the
    OUTPUT here is plain data (a list), not a callable, because `ticks` travels across a SECOND
    job dispatch (D-13's own two-dispatch shape), not a same-call closure.

    Not reviewed (the plain Run button) -> `[]` — `_replay_decide`'s own accept-everything
    fallback, no Qt call needed to answer that (mirrors `_wrangle_ticks`'s own empty case)."""
    if dlg is None or not getattr(dlg, 'reviewed', False) or dlg._table is None:
        return []
    from calibre_plugins.scourgify.picker import checklist_decide
    decide = checklist_decide(dlg)
    acc, rej, action = decide('', dlg._items)
    return [(list(acc), list(rej), action)]


def _promote_ticks(dlg):
    """Build the single-call `ticks` list `job_execute_promote`'s second dispatch replays — the
    same shape `_synopsis_ticks` builds, reusing `picker.checklist_decide` (D-04) since promote's
    review (like synopsis's, unlike wrangle's per-book one) calls its `decide=` exactly once for
    the whole batch of verdicts.

    Not reviewed (the plain Run button) -> `[]` — `_replay_decide`'s own accept-everything
    fallback, no Qt call needed to answer that (mirrors `_synopsis_ticks`'s own empty case)."""
    if dlg is None or not getattr(dlg, 'reviewed', False) or dlg._table is None:
        return []
    from calibre_plugins.scourgify.picker import checklist_decide
    decide = checklist_decide(dlg)
    acc, rej, action = decide('', dlg._items)
    return [(list(acc), list(rej), action)]


def _these(n):
    return 'this book' if n == 1 else 'these %d books' % n if n else 'the library'
