#!/usr/bin/env python3
"""The ONE verb-parameterised picker `QDialog` (D-01/D-02) — every write verb's PLAN result opens
this same dialog with different plain data.

Default view is summary + Run, with the consequence label ON THE BUTTON (`writes 37 books`, or a
price) instead of a confirmation dialog on top of it (PROJECT.md Out of Scope: "no confirmation
dialogs for spend or destructive actions" — the click IS the confirmation). The "Review 1-by-1"
tick-list is plan 02-04's addition, not built here.

Makes NO core call and imports nothing from `scourgify` — everything it renders was already
computed by the PLAN job. Every label carrying library text is set to plain text
(`Qt.TextFormat.PlainText`) because FanFicFare-imported titles are untrusted input (T-02-03)."""
from qt.core import (QDialog, QHBoxLayout, QLabel, QPushButton, Qt, QSpinBox, QVBoxLayout)


class Picker(QDialog):
    """`result` is a PLAN job's plain dict (see `plugin/jobs.py::plan_result`). `on_run(carry)` is
    called once, after the dialog closes, when the user clicks Run."""

    def __init__(self, gui, result, on_run):
        QDialog.__init__(self, gui)
        self.on_run = on_run
        self._carry = result.get('carry', {})
        self.setWindowTitle('scourgify — %s' % result.get('verb', ''))

        outer = QVBoxLayout(self)
        for line in result.get('summary', []):
            outer.addWidget(self._plain_label(line))

        safety = result.get('safety', '')
        if safety:
            lab = self._plain_label(safety)
            lab.setStyleSheet('color: #b35900;')
            outer.addWidget(lab)

        btns = QHBoxLayout()
        run = QPushButton(result.get('consequence') or 'Run')
        run.setDefault(True)
        run.clicked.connect(self._run)
        btns.addWidget(run)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        outer.addLayout(btns)

    def _plain_label(self, text):
        lab = QLabel(text)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setWordWrap(True)
        return lab

    def _run(self):
        self.accept()
        self.on_run(self._carry)


def show_picker(gui, result, on_run):
    """Non-modal, parented to `gui`, so Calibre stays usable while it's open — the EXECUTE job's
    own identity check (`_open`) is what catches a library switch in the meantime, not modality."""
    dlg = Picker(gui, result, on_run)
    dlg.setModal(False)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------- classify: step 1 of D-06
class ScopeDialog(QDialog):
    """The classify scope dialog — one control per FIXED slot from `classify.scope_options`,
    handed in verbatim by a job's read (`jobs.job_scope_rows`) and rendered in slot order. A row
    whose id is `None` shows DISABLED with its own "none" label rather than vanishing, so a slot
    never comes to mean something else (the wizard's own fixed-slot rule, B1 edge case).

    Makes NO core call and imports nothing from `scourgify` — `scope_result` is plain data a job
    already computed. `on_choose(scope_spec)` is called once, after the dialog closes, with the
    plain dict `{'mode', 'batch', 'last'}` — never for the 'skip' slot, which just closes."""

    def __init__(self, gui, scope_result, current_selection, on_choose):
        QDialog.__init__(self, gui)
        self.on_choose = on_choose
        self.setWindowTitle('scourgify — classify: choose a scope')
        outer = QVBoxLayout(self)
        outer.addWidget(self._plain_label(
            'nothing selected — library scope' if not current_selection else
            '1 book selected' if current_selection == 1 else '%d books selected' % current_selection))

        opts = scope_result.get('opts', [])
        default = scope_result.get('default')
        batch_default = scope_result.get('batch_default', 200)

        self._batch = QSpinBox()
        self._batch.setRange(1, 1000000)
        self._batch.setValue(batch_default)
        self._last = QSpinBox()
        self._last.setRange(1, 1000000)
        self._last.setValue(min(1000000, max(1, current_selection or 50)))

        for _key, ident, label, hint in opts:
            row = QHBoxLayout()
            btn = QPushButton(label)
            btn.setToolTip(hint)
            if ident is None:
                btn.setEnabled(False)
            else:
                btn.clicked.connect(lambda checked=False, mode=ident: self._choose(mode))
                if ident == default:
                    btn.setDefault(True)
            row.addWidget(btn)
            if ident == 'unclassified':
                row.addWidget(self._batch)
            elif ident == 'last':
                row.addWidget(self._last)
            outer.addLayout(row)

        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        outer.addWidget(cancel)

    def _plain_label(self, text):
        lab = QLabel(text)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setWordWrap(True)
        return lab

    def _choose(self, mode):
        self.accept()
        if mode == 'skip':
            return
        self.on_choose({'mode': mode,
                        'batch': self._batch.value() if mode == 'unclassified' else None,
                        'last': self._last.value() if mode == 'last' else None})


def show_scope_dialog(gui, scope_result, current_selection, on_choose):
    dlg = ScopeDialog(gui, scope_result, current_selection, on_choose)
    dlg.setModal(False)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------- classify: step 3 of D-06,
# all of D-07 — the engine picker whose buttons ARE the run
class EnginePicker(QDialog):
    """ONE `QPushButton` per row of a classify PLAN result's `engines` list (`jobs.job_plan_classify`),
    rendered VERBATIM: the engine name, the price fragment computed over the resolved todo set
    (`~$0.42 for 37 books`, `free`, or the sub-cent label), and the TRAITS-derived failure-mode
    text (`engine_limits`, handed in by the job — this file may import nothing from `scourgify`,
    so it cannot derive that text itself).

    **Clicking a button IS the run** — it calls `on_engine(engine_id)` and closes. No radio list,
    no separate Run button, no confirmation. An engine absent from `usable` (also handed in by the
    job) is rendered disabled with its own row's hint (already the "unusable" reason for that
    engine, e.g. "no key" or "needs the afm binary or a swift toolchain"). No engine is ever
    compared by name — usability and defaulting are both plain data from the PLAN result, so an
    engine registered later needs no change here."""

    def __init__(self, gui, plan_result, on_engine):
        QDialog.__init__(self, gui)
        self.on_engine = on_engine
        self.setWindowTitle('scourgify — %s' % (plan_result.get('consequence') or 'classify'))
        outer = QVBoxLayout(self)
        outer.addWidget(self._plain_label(plan_result.get('consequence') or ''))

        usable = set(plan_result.get('usable') or ())
        limits = plan_result.get('engine_limits') or {}
        default_engine = plan_result.get('default_engine')

        for _key, engine_id, _engine_id2, label in plan_result.get('engines', []):
            text = '%s  —  %s' % (engine_id, label)
            limit = limits.get(engine_id, '')
            if limit:
                text += '\n%s' % limit
            btn = QPushButton(text)
            btn.setToolTip(limit)
            if engine_id not in usable:
                btn.setEnabled(False)
            else:
                btn.clicked.connect(lambda checked=False, eid=engine_id: self._choose(eid))
                if engine_id == default_engine:
                    btn.setDefault(True)
                    btn.setFocus()
            outer.addWidget(btn)

        close = QPushButton('Cancel')
        close.clicked.connect(self.reject)
        outer.addWidget(close)

    def _plain_label(self, text):
        lab = QLabel(text)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setWordWrap(True)
        return lab

    def _choose(self, engine_id):
        self.accept()
        self.on_engine(engine_id)


def show_engine_picker(gui, plan_result, on_engine):
    dlg = EnginePicker(gui, plan_result, on_engine)
    dlg.setModal(False)
    dlg.show()
    return dlg
