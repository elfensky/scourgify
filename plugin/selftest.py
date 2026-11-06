#!/usr/bin/env python3
"""Phase 4's GUI acceptance, DRIVEN instead of clicked — a test hook, not a user feature.

    SCOURGIFY_SMOKE=1 calibre --with-library /path/to/throwaway

Inert unless $SCOURGIFY_SMOKE is set (action.initialization_complete's only statement), so nothing
here runs in a normal Calibre. It then does exactly what a hand test does, and records numbers a
pair of eyes cannot: builds the menu at 0 / 1 / N selected, runs "What does scourgify know?" and
the db-from-worker smoke, and — the thing "the window never stops repainting" actually means —
runs a 16 ms heartbeat on the GUI thread throughout and reports the LONGEST gap between beats. A
frozen GUI thread shows up as a gap the size of the job.

Same shape as $SCOURGIFY_SCRIPT for the wizard: canned input, real code path, no human.
Transcript goes to $SCOURGIFY_SMOKE_OUT (default /tmp/scourgify-selftest.txt); Calibre quits when
it finishes unless $SCOURGIFY_SMOKE_STAY is set.

ponytail: a linear list of steps driven by one QTimer, no framework. It runs once, by hand,
per phase.
"""
import os
import time

from qt.core import QTimer

OUT = os.environ.get('SCOURGIFY_SMOKE_OUT', '/tmp/scourgify-selftest.txt')


def run(action):
    Harness(action).start()


class Harness(object):
    def __init__(self, action):
        self.action = action
        self.gui = action.gui
        self.lines = []
        self.beats = []
        self.captured = None
        # Task 3 (plan 02-08): the PLAN/EXECUTE round trip a real click drives (picker, engine
        # picker, scope dialog, result dialog, refusal dialog, retry chooser) is captured exactly
        # like info_dialog already is below — `self.chain` collects every capture this run, in
        # order; `self.chain_terminal` is set only by a TERMINAL one (result/refusal/info_dialog),
        # which is what `_run_verb_chain`'s poll waits for.
        self.chain = []
        self.chain_terminal = None
        # The TRUE whole-run worst gap (task 3a): `self.beats` itself is reset per-verb by the
        # pre-existing `_run_verb`/`_menu` helpers (and now `_run_verb_chain`), so a max() over it
        # only ever covers ONE step. Tracked independently, incrementally, from every heartbeat
        # tick regardless of any reset — `finish()` reports it as the phase's own measured number
        # for "GUI-thread portion of any dispatch < 100 ms", not a per-step approximation of it.
        self._last_beat_time = None
        self.worst_gap_ms = -1.0
        from calibre_plugins.scourgify import action as mod
        from calibre_plugins.scourgify import picker as picker_mod
        from calibre_plugins.scourgify import result_dialog as result_mod
        self.mod = mod
        self._orig = {
            'info_dialog': mod.info_dialog,
            'show_picker': picker_mod.show_picker,
            'show_scope_dialog': picker_mod.show_scope_dialog,
            'show_engine_picker': picker_mod.show_engine_picker,
            'show_refusal': picker_mod.show_refusal,
            'show_retry_chooser': picker_mod.show_retry_chooser,
            'show_result': result_mod.show_result,
        }
        mod.info_dialog = self._capture          # the result dialog is modal; capture it instead
        picker_mod.show_picker = self._capture_picker
        picker_mod.show_scope_dialog = self._capture_scope_dialog
        picker_mod.show_engine_picker = self._capture_engine_picker
        picker_mod.show_refusal = self._capture_refusal
        picker_mod.show_retry_chooser = self._capture_retry_chooser
        result_mod.show_result = self._capture_result

    # ---- plumbing ----
    def say(self, s):
        self.lines.append(s)
        print('SELFTEST %s' % s)

    def _capture(self, parent, title, msg, det_msg='', show=False, **kw):
        self.captured = (title, msg, det_msg)
        self._chain_record('info_dialog', {'title': title, 'msg': msg}, terminal=True)

    # ---- Task 3 (plan 02-08): the write-verb chain capture — see __init__'s docstring note ----
    def _chain_record(self, kind, data, terminal=False):
        self.chain.append((kind, data))
        if terminal:
            self.chain_terminal = (kind, data)

    def _capture_picker(self, gui, result, on_run):
        """The Review-1-by-1 picker: captured, then Run is simulated by calling `on_run` with the
        PLAN result's own `carry` — deferred one event-loop tick (`QTimer.singleShot(0, ...)`)
        because the real caller assigns its own `dlg = show_picker(...)` return value INTO the
        very closure `on_run` is; calling it synchronously here (before that assignment completes)
        would read `dlg` before it exists."""
        self._chain_record('picker', {'verb': result.get('verb'), 'consequence': result.get('consequence'),
                                      'items': len(result.get('items', ()))})
        carry = result.get('carry', {})
        QTimer.singleShot(0, lambda: on_run(carry))
        return None

    def _capture_scope_dialog(self, gui, scope_result, current_selection, on_choose):
        mode = scope_result.get('default')
        self._chain_record('scope_dialog', {'mode': mode})
        QTimer.singleShot(0, lambda: on_choose({'mode': mode, 'batch': None, 'last': None}))
        return None

    def _capture_engine_picker(self, gui, result, on_engine):
        """NEVER dispatches to a cloud engine (CLAUDE.md: no casual full cloud runs while testing)
        — only ever chooses `apple`, and only when the PLAN result's own `usable` list actually
        offers it (promote's judge-aware picker never does — apple cannot judge — so this always
        skips there, which is correct: there is no on-device judge to test)."""
        usable = set(result.get('usable') or ())
        if 'apple' not in usable:
            self._chain_record('engine_picker', {'skipped': 'no usable on-device engine'}, terminal=True)
            return None
        self._chain_record('engine_picker', {'engine': 'apple'})
        QTimer.singleShot(0, lambda: on_engine('apple'))
        return None

    def _capture_refusal(self, gui, result, on_degraded=None):
        """Terminal, and NEVER auto-clicks the degraded-mode control — that is a genuine decision
        (synopsis's FanFicFare guard, T-02-21), not a one-click Run path."""
        self._chain_record('refusal', {'msg': result.get('msg', '')}, terminal=True)
        return None

    def _capture_result(self, gui, result, on_retry=None):
        self._chain_record('result', {'verb': result.get('verb'), 'written': result.get('written'),
                                      'skipped': len(result.get('skipped', [])),
                                      'failed': len(result.get('engine_failures', []))}, terminal=True)
        return None

    def _capture_retry_chooser(self, gui, groups, on_retry):
        if not groups or not groups[0].get('targets'):
            self._chain_record('retry_chooser', {'groups': len(groups)}, terminal=True)
            return None
        target = groups[0]['targets'][0]
        self._chain_record('retry_chooser', {'groups': len(groups), 'engine': target.get('engine')})
        engine_id, book_ids = target.get('engine'), list(groups[0].get('books', []))
        QTimer.singleShot(0, lambda: on_retry(engine_id, book_ids))
        return None

    def _no_usable_engine(self):
        """The lock the engine-spending verbs (classify/synopsis/promote/retry) share: apple is
        the ONLY engine this harness will ever dispatch to (see `_capture_engine_picker`), so a
        machine with no usable apple has nothing safe to drive — skip the whole step rather than
        risk a cloud call via some other path."""
        from scourgify.engines import usable_engines
        return 'apple' not in usable_engines()

    def _run_verb_chain(self, text, then):
        """Like `_run_verb` (below), but for a verb whose flow is a CHAIN of dialogs (PLAN ->
        picker/engine-picker/scope-dialog -> EXECUTE -> result), not one shot. Waits for
        `self.chain_terminal`, which only a result/refusal/info_dialog capture ever sets — an
        intermediate picker/engine-picker/scope-dialog capture keeps the poll running."""
        self.chain = []
        self.chain_terminal = None
        hit = [a for a in self.action.menu.actions() if a.text().startswith(text)]
        if not hit:
            self.say('MISSING VERB: %s' % text)
            return QTimer.singleShot(10, self.next)
        self.beats = []
        t0 = time.monotonic()
        hit[0].trigger()
        self.say('dispatch of %r returned in %.1f ms' % (text, (time.monotonic() - t0) * 1000))

        def poll(n=[0]):
            n[0] += 1
            if self.chain_terminal is None and n[0] < 300:
                return QTimer.singleShot(100, poll)
            then(time.monotonic() - t0)
        QTimer.singleShot(100, poll)

    def _beat(self):
        now = time.monotonic()
        self.beats.append(now)
        if self._last_beat_time is not None:
            gap_ms = (now - self._last_beat_time) * 1000
            if gap_ms > self.worst_gap_ms:
                self.worst_gap_ms = gap_ms
        self._last_beat_time = now

    def gap(self):
        """Longest interval between GUI-thread heartbeats, ms — the freeze detector."""
        b = self.beats
        return max((b[i + 1] - b[i]) for i in range(len(b) - 1)) * 1000 if len(b) > 1 else -1

    def start(self):
        self.heart = QTimer(self.gui)
        self.heart.timeout.connect(self._beat)
        self.heart.start(16)
        self.steps = [self.step_popup,
                      self.step_menu_0, self.step_inspect,        # library scope (the heavy read)
                      self.step_menu_1, self.step_menu_n, self.step_inspect,
                      self.step_identity, self.step_smoke,
                      self.step_settings, self.step_probe_guards, self.step_verify,
                      self.step_no_key_leaked,
                      # Task 3 (plan 02-08): one driven round trip per write verb this phase built.
                      self.step_staleness, self.step_wrangle, self.step_classify,
                      self.step_synopsis, self.step_promote, self.step_backfill,
                      self.step_retry,
                      self.finish]
        QTimer.singleShot(2000, self.next)

    def next(self):
        step = self.steps.pop(0)
        try:
            step()
        except Exception as e:
            import traceback
            self.say('EXCEPTION in %s: %s\n%s' % (step.__name__, e, traceback.format_exc()))
            self.steps = [self.finish]
            QTimer.singleShot(10, self.next)

    def ids(self, n):
        all_ids = sorted(self.gui.current_db.new_api.all_book_ids())
        return all_ids[:n]

    # ---- steps ----
    def _menu(self, n, label):
        self.gui.library_view.select_rows(self.ids(n), using_ids=True)
        t0 = time.monotonic()
        self.action.build_menu()
        ms = (time.monotonic() - t0) * 1000
        self.say('%s: menu built in %.1f ms' % (label, ms))
        for a in self.action.menu.actions():
            self.say('    %s%s' % ('[ ] ' if not a.isEnabled() else '[x] ',
                                   a.text() or '---- separator ----'))
        QTimer.singleShot(200, self.next)

    def step_popup(self):
        """The wiring a click actually uses: the toolbar action's menu emits aboutToShow, which is
        what builds it. Every other step calls build_menu() directly and would pass with that
        connection missing. popup() rather than a real click — QToolButton.showMenu() spins a
        nested event loop and does not return until the menu closes."""
        from qt.core import QPoint
        self.action.menu.clear()
        self.action.menu.popup(QPoint(0, 0))
        n = len(self.action.menu.actions())
        self.action.menu.close()
        self.say('menu.aboutToShow built %d items (0 means the toolbar click is dead)' % n)
        QTimer.singleShot(200, self.next)

    def step_menu_0(self):
        self.gui.library_view.clearSelection()
        self._menu(0, '0 selected')

    def step_menu_1(self):
        self._menu(1, '1 selected')

    def step_menu_n(self):
        self._menu(5, 'N selected')

    def _run_verb(self, text, then):
        self.captured = None
        hit = [a for a in self.action.menu.actions() if a.text().startswith(text)]
        if not hit:
            self.say('MISSING VERB: %s' % text)
            return QTimer.singleShot(10, self.next)
        self.beats = []
        t0 = time.monotonic()
        hit[0].trigger()
        self.say('dispatch of %r returned in %.1f ms' % (text, (time.monotonic() - t0) * 1000))

        def poll(n=[0]):
            n[0] += 1
            if self.captured is None and n[0] < 300:
                return QTimer.singleShot(100, poll)
            then(time.monotonic() - t0)
        QTimer.singleShot(100, poll)

    def step_inspect(self):
        def done(elapsed):
            self.say('inspect finished in %.2f s; longest GUI-thread gap %.1f ms (%d beats)'
                     % (elapsed, self.gap(), len(self.beats)))
            self.say('result: %r' % (self.captured,))
            QTimer.singleShot(10, self.next)
        self._run_verb('What does scourgify know?', done)

    def step_identity(self):
        """B1.1: a job whose captured library is no longer the open one must abort cleanly. Faked
        by dispatching with a uuid that was never this library's — the same branch a real switch
        between click and job takes, and the only way to exercise it without two libraries."""
        self.captured = None
        self.action.inspect((self.gui.current_db.library_path, 'not-this-library', self.ids(1)))

        def poll(n=[0]):
            n[0] += 1
            if self.captured is None and n[0] < 100:
                return QTimer.singleShot(100, poll)
            ok = self.captured and 'library changed' in (self.captured[1] or '')
            self.say('identity mismatch aborts cleanly: %s -> %r' % (bool(ok), self.captured))
            QTimer.singleShot(10, self.next)
        QTimer.singleShot(100, poll)

    def step_smoke(self):
        def done(elapsed):
            self.say('db-from-worker smoke finished in %.2f s; longest GUI-thread gap %.1f ms'
                     % (elapsed, self.gap()))
            self.say('result: %r' % (self.captured,))
            QTimer.singleShot(10, self.next)
        self._run_verb('Smoke: db from a job worker', done)

    # ---- phase 5: settings and engines (NLSpec B5) ----
    FAKE = 'sk-fake-selftest-key-do-not-use-0000'

    def step_settings(self):
        """Build the real config widget, save a key through it, and prove the three claims B5 makes
        that cannot be checked by reading code: env beats stored, the display is masked, and the
        file is 0600. The user's own stored keys are snapshotted and put back in finish()."""
        from calibre_plugins.scourgify import config
        from scourgify import engines
        self.cfg = config
        self.saved_keys = config.stored_keys()

        w = config.ConfigWidget(self.action)
        rows = sorted(w.rows)
        self.say('settings: rows for %s (order %s)' % (rows, config.engine_order()))
        for e in engines.ENGINES:
            field, state, verify = w.rows[e]
            self.say('    %-8s %-34s %-28s verify=%s'
                     % (e, field.text(), state.text(), verify.isEnabled()))
        assert set(rows) == set(engines.ENGINES), 'every engine must have a row, keyless ones too'

        # save a fake key through the real widget, exactly as clicking OK does
        w.rows['openai'][0].setText(self.FAKE)
        out = w.save_settings()
        mode = oct(os.stat(config._prefs().file_path).st_mode & 0o777)
        self.say('saved: engines with a stored key = %s; config file mode %s' % (sorted(out), mode))

        stored = config.stored_keys()
        env_wins = engines.resolve_keys(stored, env={'OPENAI_API_KEY': 'sk-from-the-environment'})
        stored_wins = engines.resolve_keys(stored, env={})
        self.say('env wins: %s' % (env_wins['OPENAI_API_KEY'] == 'sk-from-the-environment'))
        self.say('stored fills in when env is silent: %s'
                 % (stored_wins.get('OPENAI_API_KEY') == self.FAKE))
        self.say('key_source with env set: %s / without: %s'
                 % (engines.key_source('openai', stored, env={'OPENAI_API_KEY': 'x'}),
                    engines.key_source('openai', stored, env={})))
        self.say('stored key makes the engine usable: %s'
                 % ('openai' in engines.usable_engines(env=stored_wins)))

        # a REBUILT widget must show the mask, never the key
        w2 = config.ConfigWidget(self.action)
        shown = w2.rows['openai'][0].text()
        self.say('field after reopen shows %r (masked: %s)' % (shown, self.FAKE not in shown))
        self.widget = w2
        QTimer.singleShot(200, self.next)

    def step_probe_guards(self):
        """Force the two branches the UI is supposed to make unreachable. A guard nobody can trip is
        worse than none — it reads as covered (the phase-4 lesson)."""
        r = self.cfg.job_verify('apple', 'irrelevant')
        self.say('job_verify refuses apple without constructing it: %s -> %r'
                 % (not r['ok'], r['detail']))
        assert not r['ok'] and 'on-device' in r['detail']
        blank = self.cfg.job_verify('openai', '')
        self.say('job_verify on a blank key never flies: %s -> %r'
                 % (not blank['ok'], blank['detail'][:60]))
        assert not blank['ok'] and blank['cls'] == 'auth'
        QTimer.singleShot(10, self.next)

    def step_verify(self):
        """B5.4: the probe is a job. With a fake key this takes the AUTH branch of the new taxonomy —
        the branch that has to work, forced rather than hoped for. Set $SCOURGIFY_SMOKE_PROBE to an
        engine name to fire ONE real probe instead (it spends; ask before arming it)."""
        w = self.widget
        real = os.environ.get('SCOURGIFY_SMOKE_PROBE')
        eng = real or 'openai'
        _, state, _ = w.rows[eng]
        self.say('verify %s (%s key) — state before: %r'
                 % (eng, 'REAL, from the environment' if real else 'fake', state.text()))
        # Fail CLOSED rather than spend: unarmed, the probe must be flying the fake key. If the
        # shell that launched Calibre exported a real one it wins (B5.2), and this would bill it.
        resolved = w._key_for(eng)
        if not real and resolved != self.FAKE:
            self.say('REFUSING to probe: %s resolves to a key that is not the fake one — rerun with '
                     'the key env vars unset (env -u OPENAI_API_KEY …).' % eng)
            return QTimer.singleShot(10, self.next)
        t0 = time.monotonic()
        w.rows[eng][2].setEnabled(True)          # a fake key leaves the row "not configured"
        w.verify(eng)

        def poll(n=[0]):
            n[0] += 1
            if state.text() == 'verifying…' and n[0] < 300:
                return QTimer.singleShot(100, poll)
            self.say('verify finished in %.2f s; state %r; note %r; longest GUI-thread gap %.1f ms'
                     % (time.monotonic() - t0, state.text(), w.note.text(), self.gap()))
            QTimer.singleShot(10, self.next)
        self.beats = []
        QTimer.singleShot(100, poll)

    def step_no_key_leaked(self):
        """B5 postcondition, asserted against the transcript itself rather than by reading code."""
        blob = '\n'.join(self.lines)
        self.say('the fake key appears in the transcript: %s (must be False)' % (self.FAKE in blob))
        assert self.FAKE not in blob, 'a key reached the transcript'
        QTimer.singleShot(10, self.next)

    # ---- Task 3 (plan 02-08): one driven round trip per write verb, under a real Calibre ----
    def _prep(self, n):
        """Select `n` books and rebuild the menu so it reflects that selection before a chain
        step reads `self.action.menu.actions()` — mirrors `_menu()`'s own setup above."""
        self.gui.library_view.select_rows(self.ids(n), using_ids=True)
        self.action.build_menu()

    def _chain_done(self, label):
        def done(elapsed):
            self.say('%s: %.2fs, %d capture(s), terminal=%r, longest GUI-thread gap %.1fms'
                     % (label, elapsed, len(self.chain), self.chain_terminal, self.gap()))
            QTimer.singleShot(10, self.next)
        return done

    def step_staleness(self):
        self._prep(3)
        self._run_verb_chain('Re-derive status', self._chain_done('staleness'))

    def step_wrangle(self):
        self._prep(3)
        self._run_verb_chain('Normalize fields', self._chain_done('wrangle'))

    def step_classify(self):
        if self._no_usable_engine():
            self.say('classify: skipped: no usable engine')
            return QTimer.singleShot(10, self.next)
        self._prep(2)
        self._run_verb_chain('Classify the never-classified here', self._chain_done('classify'))

    def step_synopsis(self):
        if self._no_usable_engine():
            self.say('synopsis: skipped: no usable engine')
            return QTimer.singleShot(10, self.next)
        self._prep(2)
        self._run_verb_chain('Settle descriptions', self._chain_done('synopsis'))

    def step_promote(self):
        # apple can never judge (TRAITS['judge'] is False) — job_plan_promote's own `usable`
        # therefore never includes it, so this always ends up skipped by _capture_engine_picker;
        # dispatched anyway (PLAN is a read-only, engine-free job) so the read path is exercised.
        self._run_verb_chain('Adjudicate new tags', self._chain_done('promote'))

    def step_backfill(self):
        self._run_verb_chain('Backfill promoted tags', self._chain_done('backfill'))

    def step_retry(self):
        if self._no_usable_engine():
            self.say('retry: skipped: no usable engine')
            return QTimer.singleShot(10, self.next)
        self._prep(2)

        def check(n=[0]):
            n[0] += 1
            self.action.build_menu()
            hit = [a for a in self.action.menu.actions() if a.text().startswith('Retry on another engine')]
            if hit and hit[0].isEnabled():
                return self._run_verb_chain('Retry on another engine', self._chain_done('retry'))
            if n[0] >= 30:
                self.say('retry: skipped: nothing to retry')
                return QTimer.singleShot(10, self.next)
            QTimer.singleShot(200, check)
        QTimer.singleShot(200, check)

    def finish(self):
        if getattr(self, 'cfg', None) is not None:
            self.cfg._prefs()['keys'] = self.saved_keys  # put the user's own keys back
            self.say('restored stored keys: %s' % sorted(self.saved_keys))
        self.mod.info_dialog = self._orig['info_dialog']
        from calibre_plugins.scourgify import picker as picker_mod
        from calibre_plugins.scourgify import result_dialog as result_mod
        picker_mod.show_picker = self._orig['show_picker']
        picker_mod.show_scope_dialog = self._orig['show_scope_dialog']
        picker_mod.show_engine_picker = self._orig['show_engine_picker']
        picker_mod.show_refusal = self._orig['show_refusal']
        picker_mod.show_retry_chooser = self._orig['show_retry_chooser']
        result_mod.show_result = self._orig['show_result']
        self.heart.stop()
        self.say('longest GUI-thread heartbeat gap for the WHOLE run: %.1f ms' % self.worst_gap_ms)
        open(OUT, 'w').write('\n'.join(self.lines) + '\n')
        self.say('wrote %s' % OUT)
        if not os.environ.get('SCOURGIFY_SMOKE_STAY'):
            QTimer.singleShot(500, self.gui.quit)
