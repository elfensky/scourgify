#!/usr/bin/env python3
"""Customize scourgify — API keys and what each engine will do to you (NLSpec B5).

Keys live in Calibre's ordinary plugin JSONConfig, the same place FanFicFare keeps site logins.
That is a settled decision, not an oversight, and the banner says so in words rather than behind a
modal nobody reads. Two rules the rest of this file exists to keep:

  1. **The environment WINS over a stored key** (B5.2) — the opposite of the library path's rule
     (`common.set_library` beats $CALIBRE_LIBRARY), deliberately: a key is user config, so an
     exported one must keep a scripted run working. The row SAYS so rather than silently ignoring
     what was typed. Nothing here mutates os.environ; the resolved mapping is passed to `engines`
     explicitly, so two jobs and a CLI alongside can never observe each other's keys.
  2. **No key reaches a log, a job description, or an error dialog** (B5 postcondition). Display
     goes through `engines.mask`, recorded failures through `engines.redact`.

Every row is DERIVED from engines.TRAITS / PRICING / ENGINE_ENV (B4.2): a capability claim this
dialog makes is a trait row first, so a newly registered engine appears here by existing.

The verification probe is a network call, so it is a ThreadedJob like everything else (B5.4) —
never inline in the dialog, which runs on the GUI thread.

ponytail: plain QWidget + a grid, no model/view, no per-engine subclass. Five rows.
"""
import os

from qt.core import (QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from calibre.utils.config import JSONConfig

# The same path FanFicFare uses: ~/Library/Preferences/calibre/plugins/scourgify.json
prefs = JSONConfig('plugins/scourgify')
prefs.defaults['keys'] = {}

BANNER = (
    '<b>Keys are saved in plain text</b>, in Calibre’s own plugin settings file — the same '
    'place every Calibre plugin keeps its logins. Anyone with your config folder can read them, so '
    'don’t sync it somewhere public, and rotate a key if you ever share it. '
    '<code>OPENAI_API_KEY</code> and friends still win when set, so scripts keep working.')


def stored_keys() -> dict:
    """{engine: key} as saved by this dialog. The one reader — phase 6's classify verb resolves
    through `engines.resolve_keys(stored_keys())` so the GUI and a shell agree about which key wins."""
    return dict(prefs['keys'] or {})


# ---------------------------------------------------------------- job function (worker thread)

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


VERDICT = {'refusal': 'refused the probe — the key works',   # a refusal proves auth succeeded
           'auth': 'rejected — wrong or revoked key',
           'permission': 'no permission for this model',
           'quota': 'out of quota or rate-limited',
           'timeout': 'timed out',
           'parse': 'answered in a shape scourgify could not read',
           'error': 'failed'}


# ------------------------------------------------------------------ Qt layer (GUI thread)

def engine_order():
    """Cloud engines cheapest first, then the ones needing no key. Derived from PRICING so a new
    engine sorts itself instead of being appended to a hand-kept list."""
    from scourgify import engines
    return sorted(engines.ENGINES, key=lambda e: (e not in engines.ENGINE_ENV,
                                                  sum(engines.PRICING.get(e, (0, 0)))))


class ConfigWidget(QWidget):
    """Preferences → Plugins → scourgify → Customize."""

    def __init__(self, action):
        QWidget.__init__(self)
        from scourgify import engines
        self.action = action                    # for gui.job_manager; None outside a loaded plugin
        self.engines = engines
        self.rows = {}                          # engine -> (field, state label, verify button)

        outer = QVBoxLayout(self)
        banner = QLabel('\U0001F511  ' + BANNER)
        banner.setWordWrap(True)
        banner.setStyleSheet('padding:8px; border:1px solid palette(mid); border-radius:4px;')
        outer.addWidget(banner)

        box = QGroupBox('API keys')
        grid = QGridLayout(box)
        stored = stored_keys()
        usable = set(engines.usable_engines(env=engines.resolve_keys(stored)))
        for r, e in enumerate(engine_order()):
            self._row(grid, r, e, stored, usable)
        outer.addWidget(box)

        self.note = QLabel('')
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        outer.addStretch(1)
        self._version_label(outer)

    def _row(self, grid, r, e, stored, usable):
        engines = self.engines
        src = engines.key_source(e, stored)
        who = QLabel('<b>%s</b><br><span style="color:palette(mid)">%s</span>'
                     % (e, engines.trait(e, 'role')))
        who.setToolTip(engines.trait(e, 'limits'))          # how this engine will let you down
        grid.addWidget(who, r, 0)

        field = QLineEdit()
        field.setToolTip(engines.trait(e, 'limits'))
        if e not in engines.ENGINE_ENV:                     # on-device: there is no key to hold
            field.setText('no key needed — uses Apple Intelligence')
            field.setEnabled(False)
        elif src == 'env':
            field.setText('from $%s — the environment wins' % engines.ENGINE_ENV[e][0])
            field.setEnabled(False)                         # editing it here would change nothing
        else:
            field.setText(engines.mask(stored.get(e, '')))
            field.setPlaceholderText('not set — paste a key')
        grid.addWidget(field, r, 1)

        state = QLabel(self._state_text(e, src, e in usable))
        grid.addWidget(state, r, 2)

        verify = QPushButton('Verify')
        verify.setEnabled(e in engines.ENGINE_ENV and e in usable)
        verify.setToolTip('One cheap request to the real endpoint, as a background job.')
        verify.clicked.connect(lambda checked=False, eng=e: self.verify(eng))
        grid.addWidget(verify, r, 3)
        self.rows[e] = (field, state, verify)

    def _state_text(self, e, src, usable):
        if e not in self.engines.ENGINE_ENV:
            return ('✓ available' if usable else 'not available — %s'
                    % self.engines.trait(e, 'unusable'))
        if src == 'env': return '✓ from environment'
        if src == 'stored': return 'saved — not verified'
        return 'not configured'

    def _version_label(self, outer):
        """Version coherence (NLSpec Constraints): the zip and the wheel are cut from one tree, and
        this is scourgify's own surface saying which one is loaded."""
        try:
            from scourgify._plugin_version import VERSION
        except Exception:
            VERSION = 'unknown (not built by build_plugin.py)'
        outer.addWidget(QLabel('<span style="color:palette(mid)">scourgify core %s</span>' % VERSION))

    # ---- the probe: a job, never inline (B5.4) ----
    def verify(self, e):
        field, state, _ = self.rows[e]
        key = self._key_for(e)
        if not key:
            return state.setText('not configured')
        state.setText('verifying…')
        self.action._run('scourgify: verify the %s key' % e,      # NB: the key is never in this string
                         job_verify, (e, key), done=self._verified)

    def _key_for(self, e):
        """The key a probe should use: whatever would actually be used at run time — the environment
        first, then what is in the field right now (typed or still masked)."""
        engines, stored = self.engines, stored_keys()
        if engines.key_source(e, stored) == 'env':
            return engines.resolve_keys(stored).get(engines.ENGINE_ENV[e][0], '')
        return engines.unmask(self.rows[e][0].text(), stored.get(e, ''))

    def _verified(self, job):
        """Dispatcher-wrapped by `action._run` — ThreadedJob calls back from the worker thread."""
        if job.failed:
            return self.note.setText('the verification job failed — see Calibre’s job list.')
        r = job.result or {}
        e = r.get('engine', '')
        if e not in self.rows: return
        _, state, _ = self.rows[e]
        if r.get('ok'):
            state.setText('✓ verified')
            return self.note.setText('%s answered.' % e)
        state.setText('✗ ' + VERDICT.get(r.get('cls'), 'failed'))
        self.note.setText('%s: %s' % (e, r.get('detail') or VERDICT.get(r.get('cls'), 'failed')))

    # ---- save ----
    def save_settings(self):
        """Called from ScourgifyPlugin.save_settings. An untouched masked field keeps its key; an
        emptied one clears it (engines.unmask). chmod 0600 because the banner just promised the file
        is the only thing protecting them."""
        stored = stored_keys()
        out = {}
        for e, (field, _, _) in self.rows.items():
            if e not in self.engines.ENGINE_ENV or not field.isEnabled():
                if stored.get(e): out[e] = stored[e]       # env is winning; don't drop what's saved
                continue
            key = self.engines.unmask(field.text(), stored.get(e, ''))
            if key: out[e] = key
        prefs['keys'] = out
        try:
            os.chmod(prefs.file_path, 0o600)
        except OSError:
            pass                                           # a config dir we cannot chmod is not a
        return out                                         # reason to lose the keys
