#!/usr/bin/env python3
"""The ONE verb-parameterised picker `QDialog` (D-01/D-02) — every write verb's PLAN result opens
this same dialog with different plain data.

Default view is summary + Run, with the consequence label ON THE BUTTON (`writes 37 books`, or a
price) instead of a confirmation dialog on top of it (PROJECT.md Out of Scope: "no confirmation
dialogs for spend or destructive actions" — the click IS the confirmation). The "Review 1-by-1"
control (plan 02-04, D-02) is the picker's SECOND control: it expands an all-pre-ticked tick-list
of the PLAN result's `items` — `(label, payload)` pairs (D-03) — mirroring `ui.checklist`'s own
semantics (apply ticked / all / skip), so a reviewer's options are the same in both front doors.
The one-click path stays one click; the review path costs one expand.

Makes NO core call and imports nothing from `scourgify` — everything it renders was already
computed by the PLAN job. Every label carrying library text is set to plain text
(`Qt.TextFormat.PlainText` / a read-only `QTableWidgetItem`) because FanFicFare-imported titles
and description text are untrusted input (T-02-03/T-02-16)."""
from qt.core import (QDialog, QHBoxLayout, QLabel, QPushButton, Qt, QSpinBox, QTableWidget,
                     QTableWidgetItem, QVBoxLayout)

BOOK_ROLE = Qt.ItemDataRole.UserRole   # the review row's book id, stashed off the checkbox cell


class Picker(QDialog):
    """`result` is a PLAN job's plain dict (see `plugin/jobs.py::plan_result`). `on_run(carry)` is
    called once, after the dialog closes, when the user clicks Run OR finishes a 1-by-1 review —
    both paths converge on the SAME callback (D-02); a later verb plan tells the two apart by
    calling `checklist_decide(dlg)` (or `backfill_decide(dlg)`) on the closed dialog, which reads
    `dlg.reviewed` / `dlg._table` for the ticks the reviewer actually left."""

    def __init__(self, gui, result, on_run):
        QDialog.__init__(self, gui)
        self.on_run = on_run
        self._carry = result.get('carry', {})
        self._items = list(result.get('items', ()))
        self.reviewed = False          # True once a 1-by-1 review action closed the dialog
        self.review_action = None      # 'apply' | 'all' | 'skip' — set only when reviewed
        self._table = None
        self.setWindowTitle('scourgify — %s' % result.get('verb', ''))

        self._outer = QVBoxLayout(self)
        for line in result.get('summary', []):
            self._outer.addWidget(self._plain_label(line))

        safety = result.get('safety', '')
        if safety:
            lab = self._plain_label(safety)
            lab.setStyleSheet('color: #b35900;')
            self._outer.addWidget(lab)

        btns = QHBoxLayout()
        run = QPushButton(result.get('consequence') or 'Run')
        run.setDefault(True)
        run.clicked.connect(self._run)
        btns.addWidget(run)
        review = QPushButton('Review 1-by-1')
        review.setEnabled(bool(self._items))
        review.clicked.connect(self._expand_review)
        btns.addWidget(review)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        self._outer.addLayout(btns)

    def _plain_label(self, text):
        lab = QLabel(text)
        lab.setTextFormat(Qt.TextFormat.PlainText)
        lab.setWordWrap(True)
        return lab

    def _run(self):
        self.accept()
        self.on_run(self._carry)

    # ---------------------------------------------------------------- Review 1-by-1 (D-02)
    def _expand_review(self):
        """The picker's second control: a `QTableWidget` of every `(label, payload)` item, ALL
        PRE-TICKED (the checkbox state below is set from a CONSTANT — `Qt.CheckState.Checked` —
        never from a filter over `self._items`, so the review always starts from "everything
        applies" exactly like `ui.checklist`). Idempotent: a second click does nothing once the
        table exists."""
        if self._table is not None:
            return
        cols = ('', 'title', 'field', 'before', 'after')
        table = QTableWidget(len(self._items), len(cols), self)
        table.setHorizontalHeaderLabels(cols)
        for row, item in enumerate(self._items):
            label, payload = item if isinstance(item, tuple) else (item, {})
            payload = payload or {}
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked)              # a constant — every row starts ticked
            check.setData(BOOK_ROLE, payload.get('book'))
            table.setItem(row, 0, check)
            values = (payload.get('title', label), payload.get('field', ''),
                     payload.get('before', ''), payload.get('after', ''))
            for col, val in enumerate(values, start=1):
                cell = QTableWidgetItem(str(val))                     # QTableWidgetItem text is plain by
                cell.setFlags(Qt.ItemFlag.ItemIsEnabled)              # construction — read-only (T-02-03/T-02-16)
                table.setItem(row, col, cell)
        self._table = table
        self._outer.addWidget(table)

        controls = QHBoxLayout()
        apply_btn = QPushButton('apply ticked')
        apply_btn.clicked.connect(lambda: self._finish_review('apply'))
        controls.addWidget(apply_btn)
        all_btn = QPushButton('all')
        all_btn.clicked.connect(lambda: self._finish_review('all'))
        controls.addWidget(all_btn)
        skip_btn = QPushButton('skip')
        skip_btn.clicked.connect(lambda: self._finish_review('skip'))
        controls.addWidget(skip_btn)
        self._outer.addLayout(controls)

    def _finish_review(self, action):
        """`action` mirrors `ui.checklist`'s own three outcomes (apply/all/skip) — the reviewer's
        options are the same in both front doors. Ticks are read HERE, on the GUI thread, and
        stay captured on `self` for `checklist_decide`/`backfill_decide` to read afterward; the
        dialog is not destroyed on close (no `WA_DeleteOnClose`), so its table survives long
        enough for a later verb plan's `on_run` to build the decide= callable from it."""
        if action == 'all':
            for row in range(self._table.rowCount()):
                self._table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        self.reviewed = True
        self.review_action = action
        self.accept()
        self.on_run(self._carry)


def show_picker(gui, result, on_run):
    """Non-modal, parented to `gui`, so Calibre stays usable while it's open — the EXECUTE job's
    own identity check (`_open`) is what catches a library switch in the meantime, not modality."""
    dlg = Picker(gui, result, on_run)
    dlg.setModal(False)
    dlg.show()
    return dlg


# ---------------------------------------------------------------------- the two decide= adapters
# (D-02): `dialog` is a `Picker` that has been through `_expand_review` — its `_table` carries the
# ticks a reviewer actually left. Neither adapter imports `scourgify` or touches Qt after the
# closure it returns is built, so the returned callable is safe to invoke later from a worker
# thread (the tool function's decide= seam is called from inside a job body, never the GUI thread).
def checklist_decide(dialog):
    """The checklist-shape adapter: returns `decide(title, items, subtitle='') ->
    (accepted_idx, rejected_idx, action)` — EXACTLY `ui.checklist`'s own contract (D-11), so a
    tool function (`staleness.step`, `wrangle.Plan.step`, `classify.apply_proposal_step`,
    `synopsis.Plan.run`'s internal review, `promote.apply_decisions_step`) cannot tell the two
    front doors apart. The two index lists are built by ONE pass over the table rows, so they
    partition `range(n)` by construction — every row lands in exactly one list, never both,
    never neither."""
    table = dialog._table
    action = dialog.review_action or 'apply'
    n = table.rowCount() if table is not None else 0
    accepted, rejected = [], []
    for row in range(n):
        cell = table.item(row, 0)
        (accepted if cell is not None and cell.checkState() == Qt.CheckState.Checked
         else rejected).append(row)

    def decide(title, items, subtitle=""):
        if action in ('skip', 'quit'):
            return [], list(range(len(items))), action
        return accepted, rejected, 'apply'
    return decide


def backfill_decide(dialog):
    """The backfill-shape adapter: `promote.backfill`'s callback is a DIFFERENT shape —
    `decide(chg, adds) -> chg_to_write`, where a falsy return aborts — so it cannot go through
    `checklist_decide`; conflating the two would let an aborted review read as an empty-but-
    successful write (T-02-15). Reads the SAME table `checklist_decide` reads, keyed by row ->
    book id (`payload['book']`, stashed on the checkbox cell at `_expand_review` time), and
    narrows `chg` to exactly the ticked books."""
    table = dialog._table
    action = dialog.review_action or 'apply'
    n = table.rowCount() if table is not None else 0
    kept = set()
    for row in range(n):
        cell = table.item(row, 0)
        if cell is not None and cell.checkState() == Qt.CheckState.Checked:
            kept.add(cell.data(BOOK_ROLE))

    def decide(chg, adds):
        if action in ('skip', 'quit'):
            return None
        return {b: v for b, v in chg.items() if b in kept}
    return decide


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
