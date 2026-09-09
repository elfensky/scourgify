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
from qt.core import QDialog, QHBoxLayout, QLabel, QPushButton, Qt, QVBoxLayout


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
