# -*- coding: utf-8 -*-
"""Tests of the computed engine pool. Its own in-memory database, no outbound
requests at all.

THE MAIN TEST HERE IS FALSIFICATION, not a check that "the pool was computed". A
mechanism that computes the pool is easy to write so that it always computes the
same thing: such a mechanism passes any check of the form "the pool is non-empty"
and never notices an engine degrading — the very thing it exists for. So what is
checked is BEHAVIOUR: plant a bad run for an engine and it must leave the pool
WITH NO CODE CHANGE; restore the run and it must come back by itself.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "adapter"))
import pool  # noqa: E402

ok_count = 0
fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        print(f"  ok    {name}")
        ok_count += 1
    else:
        print(f"  FAIL  {name}" + (f"  [{detail}]" if detail else ""))
        fail_count += 1


SCHEMA = """
CREATE TABLE probes (
  ts INTEGER, engine TEXT, query TEXT, verdict TEXT, results INTEGER,
  relevant INTEGER, latency_ms INTEGER, reason TEXT, first_title TEXT,
  urls TEXT, hit INTEGER, category TEXT NOT NULL DEFAULT 'general');
"""


def db() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    return c


# There are several reference QUERIES, and that is not fixture decoration. A
# family is computed from result overlap ON SHARED QUERIES and requires at least
# three of them — otherwise one chance coincidence would declare two engines
# related. Pouring every probe through one query finds no families at all, and the
# tests then fail while blaming the code for something it does not do.
# The strings themselves are opaque to the computation — what matters is that
# there are several DIFFERENT ones. Named subjects are deliberately absent: a
# fixture that names a live company or a person publishes it for nothing.
REQUESTS = ("query one", "query two", "query three", "query four")


def fill_probes(c, engine, probes, hit_count, url_list=None, label=None,
             category="general"):
    """A run of observations: `probes` probes, `hit_count` of them hits, spread
    across all the reference queries."""
    ts_val = int(time.time())
    rows = []
    for i in range(probes):
        hit = 1 if i < hit_count else 0
        qry = REQUESTS[i % len(REQUESTS)]
        u = json.dumps(url_list if url_list is not None else [f"https://{engine}.example/{i%7}"])
        rows.append((ts_val - i * 60, engine, qry, "ok" if hit else "miss",
                       8, 8, 300, None, label, u, hit, category))
    c.executemany("INSERT INTO probes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.commit()


def main() -> int:
    print("\n== the minimum number of observations ==")
    c = db()
    fill_probes(c, "veteran", 120, 100)
    fill_probes(c, "newcomer", 2, 2)          # hit rate 1.00, but only two observations
    rr = pool.rating_of(c)
    names = [e["engine"] for e in rr]
    check("an engine with two probes does NOT count, even at a share of 1.00",
             "newcomer" not in names, names)
    check("one proven over a hundred does count", "veteran" in names, names)

    print("\n== order by reference hit share ==")
    c = db()
    for name, hit in (("worst", 30), ("middling", 60), ("best", 95)):
        fill_probes(c, name, 100, hit)
    rr = pool.rating_of(c)
    check("the rating is sorted by share, best first",
             [e["engine"] for e in rr] == ["best", "middling", "worst"],
             [e["engine"] for e in rr])

    print("\n== families are computed, not declared ==")
    c = db()
    common = [f"https://shared.example/{i}" for i in range(8)]
    fill_probes(c, "shopfront-A", 60, 55, url_list=common)
    fill_probes(c, "shopfront-B", 60, 54, url_list=common)       # the same results
    fill_probes(c, "own-index", 60, 53)                   # its own results
    fam = pool.families_of(c)
    check("engines with identical results fell into ONE family",
             fam["shopfront-A"] == fam["shopfront-B"], fam)
    check("an engine with results of its own is a family of its own",
             fam["own-index"] != fam["shopfront-A"], fam)

    print("\n== the per-family cap ==")
    c = db()
    common = [f"https://one-index.example/{i}" for i in range(8)]
    for i in range(4):
        fill_probes(c, f"shopfront{i}", 60, 59 - i, url_list=common)
    for i in range(4):
        fill_probes(c, f"own{i}", 60, 40 - i)
    rr, fam = pool.rating_of(c), pool.families_of(c)
    pool_state = pool.ideal(rr, fam, size=6)
    showcases = sum(1 for ent in pool_state if ent.startswith("shopfront"))
    check(f"one family takes no more than {pool.FAMILY_CAP} slots",
             showcases <= pool.FAMILY_CAP, pool_state)
    check("but the best members of a family do NOT lose their slots (quality comes first)",
             showcases == pool.FAMILY_CAP, pool_state)

    print("\n== the dead zone ==")
    c = db()
    c.executescript(pool.POOL_SCHEMA)
    for i in range(9):
        fill_probes(c, f"e{i}", 100, 90 - i)
    first_one = pool.recompute(c)
    check("the first recomputation computes the pool and says so",
             first_one["computed"] and len(first_one["pool"]) == pool.POOL_SIZE, first_one)
    # FIRST we check that the dead zone HOLDS. Without this check the zone could
    # be set to zero and every test would stay green. Raising a candidate by 0.08
    # against a zone of 0.10 must NOT cause a replacement — a check that "the zone
    # did not fire" is worth more than a check that "the replacement happened".
    fill_probes(c, "e8", 100, 100)      # e8 ~0.91 against e7 ~0.83, a difference below 0.10
    in_zone = pool.recompute(c)
    check("a difference INSIDE the dead zone causes NO swap",
             "e8" not in in_zone["pool"], in_zone)

    # Now push the worst engine in the pool far down — the gap becomes visible.
    fill_probes(c, "e7", 300, 0, label="slump")
    second_one = pool.recompute(c)
    check("a clearly better candidate DISPLACES an incumbent",
             "e8" in second_one["pool"], second_one)
    check("the swap is NAMED: who displaced whom, and at which shares",
             "displaced" in (second_one["reason"] or ""), second_one["reason"])

    print("\n== FALSIFICATION: accepting the mechanism ==")
    c = db()
    c.executescript(pool.POOL_SCHEMA)
    for i in range(9):
        fill_probes(c, f"e{i}", 120, 115 - i * 3)
    before = pool.recompute(c)["pool"]
    check("the initially good engine e0 is in the pool", "e0" in before, before)
    # PLANT A BAD RUN. The code is not touched.
    fill_probes(c, "e0", 400, 0, label="planted")
    after = pool.recompute(c)["pool"]
    check("a bad run TOOK the engine out of the pool with no code change",
             "e0" not in after, after)
    # RESTORE THE RUN
    c.execute("DELETE FROM probes WHERE engine='e0' AND first_title='planted'")
    c.commit()
    back = pool.recompute(c)["pool"]
    check("restoring the run BROUGHT the engine back by itself", "e0" in back, back)

    print("\n== categories are computed separately ==")
    c = db()
    c.executescript(pool.POOL_SCHEMA)
    # One and the same engine: excellent on the web, useless on images. That is
    # how it really goes — asked for a company name in the images category, one
    # engine answers with a museum catalogue.
    fill_probes(c, "web-master", 100, 95, category="general")
    fill_probes(c, "web-master", 100, 5,  category="images")
    fill_probes(c, "image-master", 100, 90, category="images")
    fill_probes(c, "image-master", 100, 10, category="general")
    web = {e["engine"]: e["share"] for e in pool.rating_of(c, category="general")}
    img = {e["engine"]: e["share"] for e in pool.rating_of(c, category="images")}
    check("shares are computed within their own category, not heaped into one",
             web["web-master"] > 0.9 and img["web-master"] < 0.1, (web, img))
    wave = pool.recompute(c, "general")["pool"]
    ent = pool.recompute(c, "images")["pool"]
    check("the web pool and the image pool are DIFFERENT",
             wave[0] == "web-master" and ent[0] == "image-master", (wave, ent))
    check("one category\u0027s pool does not overwrite another\u0027s",
             pool.recompute(c, "general")["pool"][0] == "web-master", wave)
    check("images have a seed of their own, not a shared one",
             pool.SEEDS["images"] != pool.SEEDS["general"], pool.SEEDS)

    print("\n== failure direction ==")
    result = pool.read_pool("/no/such/file.db")
    check("no database — the seed is returned, and that is NAMED",
             result["pool"] == list(pool.SEED) and result["source"] == "seed", result)
    check("the reason is named in words", bool(result["reason"]), result)
    c = db()
    check("not enough observations — the pool is not invented, the seed is returned",
             pool.recompute(c)["computed"] is False, pool.recompute(c))

    print(f"\npool suite: ok {ok_count}, failed {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
