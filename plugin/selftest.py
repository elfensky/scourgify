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
        self.steps = [self.step_menu_0, self.step_inspect,        # library scope (the heavy read)
                      self.step_menu_1, self.step_menu_n, self.step_inspect,
                      self.step_identity, self.step_smoke, self.finish]
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

    def finish(self):
        self.heart.stop()
        open(OUT, 'w').write('\n'.join(self.lines) + '\n')
        self.say('wrote %s' % OUT)
        if not os.environ.get('SCOURGIFY_SMOKE_STAY'):
            QTimer.singleShot(500, self.gui.quit)
