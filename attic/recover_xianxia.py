import sys, sqlite3
APPLY = "--apply" in sys.argv
LIB = os.path.expanduser(os.environ.get("CALIBRE_LIBRARY", ""))
if not LIB:
    raise SystemExit("Set CALIBRE_LIBRARY to your Calibre library folder (the one containing metadata.db).")
BK = os.path.expanduser(os.environ.get("CALIBRE_BACKUP_DB", ""))
if not BK:
    raise SystemExit("Set CALIBRE_BACKUP_DB to the pre-cleanup metadata.db backup path.")   # pre-fandom-consolidation backup (has Xianxia fandoms)
TERMS = ("xianxia", "cultivation", "wuxia")
# read backup: which cultivation-fandoms each book had
cb = sqlite3.connect("file:%s?mode=ro" % BK, uri=True)
fi = cb.execute("SELECT id FROM custom_columns WHERE label='fandoms'").fetchone()[0]
had = {}
for book, val in cb.execute("SELECT l.book,v.value FROM custom_column_%d v JOIN books_custom_column_%d_link l ON l.value=v.id" % (fi, fi)):
    if any(t in val.lower() for t in TERMS): had.setdefault(book, []).append(val)

from calibre.library import db as DB
api = DB(LIB).new_api
ids = api.all_book_ids()
addg = {}; addf = {}; addt = {}
for b, vals in had.items():
    if b not in ids: continue
    low = " ".join(vals).lower()
    genres = set()
    if "xianxia" in low or "cultivation" in low: genres.add("Xianxia")
    if "wuxia" in low: genres.add("Wuxia")
    if "litrpg" in low: genres.add("LitRPG")
    tags = set()
    if "si" in low.split() or " si" in low or low.endswith("si"): tags.add("Self-Insert")
    if "hentai" in low or "lewd" in low: tags.add("NSFW")
    curG = set(api.field_for('#genres', b) or ())
    curF = set(api.field_for('#fandoms', b) or ())
    curT = set(api.field_for('tags', b) or ())
    nG = curG | genres
    nF = curF | ({"Original Work"} if not curF else set())
    nT = curT | tags
    if nG != curG: addg[b] = tuple(sorted(nG))
    if nF != curF: addf[b] = tuple(sorted(nF))
    if nT != curT: addt[b] = tuple(sorted(nT))

print("APPLY" if APPLY else "PRE-APPLY (no write)")
print("  books in backup w/ xianxia-cluster fandom:", len(had))
print("  +genre:", len(addg), "| +Original Work fandom:", len(addf), "| +tag:", len(addt))
from collections import Counter
gc = Counter(g for t in addg.values() for g in t if g in ("Xianxia","Wuxia","LitRPG"))
print("  genre adds:", dict(gc))
if APPLY:
    api.set_field('#genres', addg); api.set_field('#fandoms', addf); api.set_field('tags', addt)
    print("\nWROTE genres/fandoms/tags.")
else:
    print("\nRe-run with -- --apply to write.")
