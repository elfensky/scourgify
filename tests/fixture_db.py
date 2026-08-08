"""Throwaway Calibre metadata.db builder — just the schema subset scourgify reads.

Not a test file; imported by test_selection.py. Real Calibre DBs have far more,
but everything the code touches is here: books, comments, tags(+link), data,
custom_columns and both custom-column storage shapes (inline `book` column and
the books_custom_column_N_link table)."""
import sqlite3


def build(path, books, custom=(), link_labels=(), multi_labels=(), uuid=None):
    """books: [{id, title?, added?, last_modified?, desc?, tags?: [...]}]
    custom:  [(label, {book_id: value}), ...] — label without '#'; a value may be a list
    link_labels: labels from `custom` stored via the link-table shape instead of inline.
    multi_labels: labels flagged is_multiple (Calibre's own answer to "is this column
    multi-valued" — the storage shape does not tell you, a single-value column may use a link
    table too). uuid: fills library_id, the edit log's per-library key."""
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT, path TEXT DEFAULT '',"
        " timestamp TEXT, last_modified TEXT);"
        "CREATE TABLE comments(book INTEGER, text TEXT);"
        "CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE books_tags_link(book INTEGER, tag INTEGER);"
        "CREATE TABLE data(book INTEGER, format TEXT, name TEXT);"
        "CREATE TABLE custom_columns(id INTEGER PRIMARY KEY, label TEXT, is_multiple INTEGER DEFAULT 0);"
        "CREATE TABLE library_id(id INTEGER PRIMARY KEY, uuid TEXT);"
        "CREATE TABLE preferences(key TEXT, val TEXT);")
    if uuid:
        con.execute("INSERT INTO library_id VALUES(1,?)", (uuid,))
    tag_ids = {}
    for b in books:
        con.execute("INSERT INTO books VALUES(?,?,?,?,?)",
                    (b["id"], b.get("title", f"book {b['id']}"), "",
                     b.get("added"), b.get("last_modified", b.get("added"))))
        if b.get("desc"):
            con.execute("INSERT INTO comments VALUES(?,?)", (b["id"], b["desc"]))
        for t in b.get("tags", ()):
            if t not in tag_ids:
                tag_ids[t] = len(tag_ids) + 1
                con.execute("INSERT INTO tags VALUES(?,?)", (tag_ids[t], t))
            con.execute("INSERT INTO books_tags_link VALUES(?,?)", (b["id"], tag_ids[t]))
    for i, (label, values) in enumerate(custom, 1):
        con.execute("INSERT INTO custom_columns VALUES(?,?,?)", (i, label, int(label in multi_labels)))
        vals = {bk: (v if isinstance(v, list) else [v]) for bk, v in values.items()}
        if label in link_labels:
            con.execute(f"CREATE TABLE custom_column_{i}(id INTEGER PRIMARY KEY, value TEXT)")
            con.execute(f"CREATE TABLE books_custom_column_{i}_link(book INTEGER, value INTEGER)")
            vid = {}
            for bk, vs in vals.items():
                for v in vs:
                    if v not in vid:
                        vid[v] = len(vid) + 1
                        con.execute(f"INSERT INTO custom_column_{i} VALUES(?,?)", (vid[v], v))
                    con.execute(f"INSERT INTO books_custom_column_{i}_link VALUES(?,?)", (bk, vid[v]))
        else:
            con.execute(f"CREATE TABLE custom_column_{i}(id INTEGER PRIMARY KEY, book INTEGER, value TEXT)")
            for bk, vs in vals.items():
                for v in vs:
                    con.execute(f"INSERT INTO custom_column_{i}(book, value) VALUES(?,?)", (bk, v))
    con.commit()
    return con
