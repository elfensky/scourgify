#!/usr/bin/env python3
"""The non-modal diff-after result `QDialog` (D-09/D-11) — every write verb's EXECUTE result opens
this same dialog.

A refused result (`refused=True` — a `GuardrailError` the job wrapper caught, or an identity
mismatch from `_open`) renders the message VERBATIM as the whole body, zero rows, no error
styling: a guard refusal is a normal outcome, not a crash. Otherwise: a one-line summary
(written / skipped / failed) and a table of `(book, field)` rows — before -> after, or a state
naming why not. `QTableWidgetItem` never interprets its text as HTML, so no explicit plain-text
flag is needed there; the summary/refusal `QLabel`s still set it explicitly (T-02-03).

**The grouped diff-after (D-09/D-10, plan 02-08, WRITE-07).** A result's own `groups` key — built
by the JOB, never by this file (`plugin/jobs.py::failure_groups`, so no core call ever happens on
the GUI thread) — is rendered as one section per failure class present, naming the class, its
book count, and one retry button per target the taxonomy actually supports (a group with no
targets renders its books and class with an explanatory line, no control). Clicking a button
dispatches straight through `on_retry(engine_id, book_ids)` — no engine picker in between, since
the engine and its price are already on the button. This is what keeps a run from ever reporting a
single collapsed success number while books were skipped or failed."""
from qt.core import (QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                     Qt, QVBoxLayout)

COLUMNS = ('book', 'title', 'field', 'before', 'after', 'state')


class ResultDialog(QDialog):
    """`result` is an EXECUTE job's plain dict (see `plugin/jobs.py::execute_result`), or a
    refused result (`{'verb', 'refused': True, 'msg'}`) from the same job wrapper.

    `on_retry(engine_id, book_ids)` — when given, wired to every group's target buttons; `None`
    (every non-classify verb today) renders the group sections with no controls at all, since the
    only retry job this phase builds (`jobs.job_retry_classify`) is classify-specific. The dialog
    stays non-modal and per-run: it holds a plain copy of its own `result` and nothing mutates it
    after construction, so two completed runs render independently in either order."""

    def __init__(self, gui, result, on_retry=None):
        QDialog.__init__(self, gui)
        self.on_retry = on_retry
        self.setWindowTitle('scourgify — %s' % result.get('verb', ''))
        outer = QVBoxLayout(self)

        if result.get('refused'):
            msg = QLabel(result.get('msg', ''))
            msg.setTextFormat(Qt.TextFormat.PlainText)
            msg.setWordWrap(True)
            outer.addWidget(msg)
        else:
            summary = QLabel('written %d · skipped %d · failed %d'
                             % (result.get('written', 0), len(result.get('skipped', [])),
                                len(result.get('engine_failures', []))))
            summary.setTextFormat(Qt.TextFormat.PlainText)
            outer.addWidget(summary)
            for group in result.get('groups', ()):
                outer.addLayout(self._group_row(group))
            outer.addWidget(self._rows_table(result.get('rows', [])))

        close = QPushButton('Close')
        close.clicked.connect(self.accept)
        outer.addWidget(close)

    def _group_row(self, group):
        """One failure-class section (D-09): `group['cls']` is one of `engines.failure_class`'s
        own CLASSES (`refusal`/`auth`/`permission`/`quota`/`timeout`/`parse`/`error`) — computed
        by the job, rendered here as plain text, never re-derived from a raw reason string. The
        class, its book count and (a plain-text truncated) sample of titles, plus one button per
        target this group's own `targets` list carries — the label (price included) is already
        built by the job."""
        row = QHBoxLayout()
        books = group.get('books', [])
        titles = group.get('titles', {})
        sample = ', '.join(str(titles.get(b, '') or b) for b in books[:5])
        if len(books) > 5:
            sample += ', …'
        label = QLabel('%s (%d book%s): %s' % (group.get('cls', ''), len(books),
                                                '' if len(books) == 1 else 's', sample))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        row.addWidget(label)
        targets = group.get('targets', ())
        if not targets:
            note = QLabel('(nothing to retry)')
            note.setTextFormat(Qt.TextFormat.PlainText)
            row.addWidget(note)
        for target in targets:
            btn = QPushButton(target.get('label', target.get('engine', '')))
            btn.setEnabled(self.on_retry is not None)
            btn.clicked.connect(
                lambda checked=False, e=target.get('engine'), b=list(books): self._retry(e, b))
            row.addWidget(btn)
        return row

    def _retry(self, engine_id, book_ids):
        if self.on_retry is not None:
            self.on_retry(engine_id, book_ids)
        self.accept()

    def _rows_table(self, rows):
        t = QTableWidget(len(rows), len(COLUMNS))
        t.setHorizontalHeaderLabels(list(COLUMNS))
        for r, row in enumerate(rows):
            for c, key in enumerate(COLUMNS):
                item = QTableWidgetItem(str(row.get(key, '')))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, c, item)
        t.resizeColumnsToContents()
        return t


def show_result(gui, result, on_retry=None):
    """Non-modal, parented to `gui`."""
    dlg = ResultDialog(gui, result, on_retry=on_retry)
    dlg.setModal(False)
    dlg.show()
    return dlg
