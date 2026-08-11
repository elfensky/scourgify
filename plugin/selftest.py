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
        from calibre_plugins.scourgify import action as mod
        self.mod = mod
        mod.info_dialog = self._capture          # the result dialog is modal; capture it instead

    # ---- plumbing ----
    def say(self, s):
        self.lines.append(s)
        print('SELFTEST %s' % s)

    def _capture(self, parent, title, msg, det_msg='', show=False, **kw):
        self.captured = (title, msg, det_msg)

    def _beat(self):
        self.beats.append(time.monotonic())

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
                      self.step_no_key_leaked, self.finish]
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
        mode = oct(os.stat(config.prefs.file_path).st_mode & 0o777)
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

    def finish(self):
        if getattr(self, 'cfg', None) is not None:
            self.cfg.prefs['keys'] = self.saved_keys      # put the user's own keys back
            self.say('restored stored keys: %s' % sorted(self.saved_keys))
        self.heart.stop()
        open(OUT, 'w').write('\n'.join(self.lines) + '\n')
        self.say('wrote %s' % OUT)
        if not os.environ.get('SCOURGIFY_SMOKE_STAY'):
            QTimer.singleShot(500, self.gui.quit)
