#!/usr/bin/env python3
"""scourgify promote — adversarially decide whether each novel tag candidate from classify's
proposed_new list should be promoted to the vocab, aliased to an existing tag, or rejected.

  scourgify promote                 # dry run -> data/promote_review.csv (advocate + skeptic)
  scourgify promote --apply         # fold verdicts into overrides/ (vocab, tropes, aliases)
  scourgify promote --verify-with openai   # run the skeptic on a different engine (cross-model)

Reasons each candidate against a difflib shortlist of the master tag list (curated vocab ∪ ao3_vocab)
plus the example books that proposed it. Audit-first: verdicts are a reviewed artifact you apply."""
import argparse, csv, json, os, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib

from scourgify.classify import RANK, PROP, ENGINES, existing_terms, ask_retry
from scourgify.common import DATA, library

LEDGER = f"{DATA}/promote_ledger.csv"
REVIEW = f"{DATA}/promote_review.csv"
ALIASES = os.path.join(os.getcwd(), "overrides", "promote_aliases.csv")
VERDICTS = ("promote", "alias", "reject")


def parse_decision(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m: return None
    try: obj = json.loads(m.group(0))
    except Exception: return None
    v = str(obj.get("verdict", "")).strip().lower()
    if v not in VERDICTS: return None
    target = str(obj.get("target", "")).strip()
    if v == "alias" and not target: return None
    conf = str(obj.get("confidence", "med")).strip().lower()
    if conf not in ("low", "med", "high"): conf = "med"
    return {"verdict": v, "target": target if v == "alias" else "",
            "reason": str(obj.get("reason", "")).strip()[:200], "confidence": conf}
