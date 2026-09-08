# -*- coding: utf-8 -*-
"""The engine pool is COMPUTED from observation, not written by hand.

WHY. A hand-written engine list needs revising as often as the engines change —
three times in one day is not unusual, each revision against the previous one and
each correct on the data available. The problem is neither the engines nor the
quality of the decisions: a decision freezes, and observation keeps going.

The cost is measurable. A hand-picked pool holds an engine at a 0.19 reference-hit
share, put there as "confirmed clean, 3 out of 3", while at 118-124 probes per
engine the rest stand at

    0.97 · 0.96 · 0.95 · 0.95 · 0.93 · 0.86 · 0.80 · and that one at 0.19

— a fourfold gap, with three engines at 0.76-0.77 sitting unasked in the reserve.
Seven of eight hand picks hold up: the hand decision is mostly RIGHT. Exactly one
is wrong, and only continued measurement shows which.

WHY COMPARISON AND NOT A THRESHOLD. A threshold is a number someone assigns and
which then freezes — the very disease being cured, moved elsewhere. The pool is
simply the BEST N, recomputed continuously. No entry threshold, no exit
threshold, no floor: the pool cannot empty out by construction, because it is
always full.

TWO LIMITERS REMAIN, AND BOTH ARE ABOUT NOISE RATHER THAN QUALITY:
· MIN_OBSERVATIONS — an engine with two lucky probes must not displace one
  proven over a hundred and twenty. That is precisely the mistake that put the
  0.19 engine in;
· DEAD_ZONE — swap only when a candidate is CLEARLY better. Otherwise the pool
  twitches on hundredths. There is no "correct" value for it, only "wide enough
  not to twitch".

FAMILIES. "Best N" is not enough: six of the engines measured SHARE ONE INDEX,
and four of them sat in the pool at once. A pool of the best would fill up with
shopfronts of a single source, and corroboration would become empty while still
looking full — a quarter of the links "confirmed by three engines" were confirmed
by one index counted three times.

Families are COMPUTED too, not declared as a list. A declared list freezes the
same way the pool did: today we are sure two engines share a source, and next
month someone rebuilds their configuration and the declared family becomes a lie
without a sound. The measure is the share of links two engines return in common,
and the data for it is already in the probes — no extra requests outward.

WHO COMPUTES AND WHO READS. The prober computes (it holds the database for
writing), the adapter reads (read-only). One implementation for both — this
file; two would diverge on the first edit.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

# How many engines to keep in the pool. Not "how many good ones exist" but how
# many we are willing to ask: each extra one is an outbound request per search.
POOL_SIZE = int(os.environ.get("POOL_SIZE") or "8")

# Below this many probes an engine takes NO part in the computation — it can
# neither enter nor displace. Twenty comes from the cost of the error, not from
# statistics: the hand-picked mistake that cost a day of half-blind polling was
# made on THREE observations.
MIN_OBSERVATIONS = int(os.environ.get("POOL_MIN_PROBES") or "20")

# How much better a candidate must be for a swap to happen. NOT a quality
# threshold but the width of a dead zone: without it the pool would twitch on
# hundredths at every recomputation.
DEAD_ZONE = float(os.environ.get("POOL_DEADZONE") or "0.10")

# Observation window. A day: shorter and we ride the daily rhythm of engine
# outages, longer and the mechanism stops noticing the decay it exists to catch.
WINDOW_H = int(os.environ.get("POOL_WINDOW_H") or "24")

# Overlap share at which two engines count as sharing an index. Two independent
# routes give the same value: by meaning ("more than half of what A found, B
# already found — as a witness A is worth less than half") and by distribution
# (median overlap 0.038, 95th percentile 0.534).
#
# 0.6 AND NOT 0.5. A real family survives every threshold from 0.40 to 0.80 and
# does not depend on the number. A second group exists at EXACTLY 0.50 and falls
# apart at 0.60 — it is a property of the threshold, not of the engines. At 0.50
# that artefact glues five good engines into one family and hands their pool
# places to engines four times worse.
FAMILY_THRESHOLD = float(os.environ.get("POOL_FAMILY_OVERLAP") or "0.6")

# How many pool slots ONE family may take. One member per family is too greedy:
# it evicts engines at 0.86-0.96 in favour of engines at 0.39-0.40, trading a
# fourfold worse result set for independence there is already enough of.
#
# A cap rather than a ban: one family cannot OWN the pool (which is what the
# mechanism exists to prevent — four shopfronts of one index polled at once), but
# it does not lose a slot when its members are objectively the best.
FAMILY_CAP = int(os.environ.get("POOL_FAMILY_CAP") or "2")

# An engine returning two links is arithmetically "contained" in almost
# anything: that is a property of the denominator, not of a shared index. One
# such engine showed 0.833 overlap with nine different engines at once, including
# ones known to be independent.
FAMILY_MIN_LINKS = 5

# What to poll with until there are enough observations. NOT "the right pool"
# but a seed: where counting starts when the database is empty or new. The
# response marks it in `pool_source` so nobody mistakes it for a computed one.
SEED = ("privacywall", "yandex", "zapmeta", "vuhuv", "abcnyheter")

# Seed for images. The same kind of starting point as the web seed, and just as
# temporary: once probes accumulate, the pool computes itself.
SEED_IMAGES = ("bing images", "duckduckgo images", "brave.images",
                 "google images", "wikicommons.images")

SEEDS = {"general": SEED, "images": SEED_IMAGES}

POOL_SCHEMA = """
CREATE TABLE IF NOT EXISTS pool (
  ts        INTEGER NOT NULL,
  engines   TEXT    NOT NULL,      -- the pool as a JSON list, in poll order
  reason    TEXT,                  -- what changed, and at which shares
  computed  INTEGER NOT NULL,      -- 1 = computed from observation, 0 = seed
  category  TEXT NOT NULL DEFAULT 'general'
);
CREATE INDEX IF NOT EXISTS pool_ts ON pool(ts DESC);
"""

# CATEGORY IS A SEPARATE DIMENSION, NOT A FILTER. An engine excellent on the web
# can be useless on images and the other way round: on one company-name query the
# first image answer came from a museum catalogue — an 1878 self-portrait.
# Treating both as one pool would mix two different capabilities into one number.
#
# The column was added after the schema: the database is already alive, and
# dropping the observation history for the sake of a new field is not an option —
# that history is the value.
POOL_TOPUP = ["ALTER TABLE pool ADD COLUMN category TEXT NOT NULL DEFAULT 'general'"]


def _domain(url: str) -> str:
    import urllib.parse
    try:
        d = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return d.split(":")[0].removeprefix("www.")


def rating_of(conn: sqlite3.Connection, window_h: int = WINDOW_H,
            category: str = "general") -> list[dict]:
    """Engines by reference-hit share, best first.

    The hit share is the PRIMARY measure and it is absolute: either the engine
    found the answer known in advance, or it did not. Spam that echoes the query
    back does not pass it; a genuinely unique index is not punished by it."""
    since = int(time.time()) - window_h * 3600
    rows = conn.execute("""
        SELECT engine, COUNT(*) AS probes, SUM(COALESCE(hit, 0)) AS hits,
               AVG(results) AS links_avg
        FROM probes WHERE ts >= ? AND COALESCE(category, 'general') = ?
        GROUP BY engine
    """, (since, category)).fetchall()
    result = []
    for name, probes, hit_count, link_count in rows:
        if probes < MIN_OBSERVATIONS:
            continue          # excluded: too few observations, noise would displace knowledge
        result.append({"engine": name, "probes": probes, "hits": hit_count or 0,
                     "share": round((hit_count or 0) / probes, 3),
                     "links_avg": round(link_count or 0, 1)})
    result.sort(key=lambda x: (-x["share"], -x["links_avg"], x["engine"]))
    return result


def families_of(conn: sqlite3.Connection, window_h: int = WINDOW_H,
              category: str = "general") -> dict[str, str]:
    """Who shares an index with whom. {engine: family name}.

    The measure is CONTAINMENT, not similarity: "how much of its own output has
    engine A already handed to engine B". Asymmetric, and that is the point. Two
    engines measured at Jaccard 0.29 look different by that metric, yet 95% of
    one engine's URLs lie inside the other's — as an independent witness it adds
    nothing. Jaccard penalises a difference in result-set SIZE, and size is not
    what we are asking about; novelty is.

    A family is named by its lexicographically first member: stable regardless of
    traversal order, and it needs no separate directory to be kept."""
    since = int(time.time()) - window_h * 3600
    out_sets: dict[tuple[str, str], set[str]] = {}
    for name, qry, raw in conn.execute(
            "SELECT engine, query, urls FROM probes WHERE ts >= ? AND urls IS NOT NULL"
            " AND COALESCE(category, 'general') = ?", (since, category)):
        try:
            url_list = {u for u in json.loads(raw or "[]") if u}
        except Exception:  # noqa: BLE001
            continue
        out_sets.setdefault((name, qry), set()).update(url_list)

    engine_names = sorted({ent for ent, _ in out_sets})
    queries = sorted({entry for _, entry in out_sets})
    # Result sets too small are excluded: see FAMILY_MIN_LINKS.
    big = [ent for ent in engine_names
               if sum(len(out_sets.get((ent, entry), ())) for entry in queries) / max(1, len(queries))
               >= FAMILY_MIN_LINKS]

    parent_of = {ent: ent for ent in big}

    def root(x: str) -> str:
        while parent_of[x] != x:
            parent_of[x] = parent_of[parent_of[x]]
            x = parent_of[x]
        return x

    for a in big:
        for b in big:
            if a == b:
                continue
            shares = []
            for entry in queries:
                A, B = out_sets.get((a, entry), set()), out_sets.get((b, entry), set())
                if len(A) < FAMILY_MIN_LINKS or not B:
                    continue
                shares.append(len(A & B) / len(A))
            if len(shares) >= 3 and sum(shares) / len(shares) >= FAMILY_THRESHOLD:
                ra, rb = root(a), root(b)
                if ra != rb:
                    parent_of[ra] = rb
    groups: dict[str, list[str]] = {}
    for ent in big:
        groups.setdefault(root(ent), []).append(ent)
    # A family is named by its alphabetically first member, not by whichever
    # root the union-find happened to end on: the name must be stable between
    # runs, or the same family reads as a different one each time
    result = {}
    for members in groups.values():
        family_name = sorted(members)[0]
        for ent in members:
            result[ent] = family_name
    # Engines left out of the computation (too few links) are each their own
    # family: there is nothing to claim a shared index on. NOT KNOWING, we count
    # them independent, because an error in that direction only UNDERSTATES
    # corroboration — the other direction would overstate it.
    for ent in engine_names:
        result.setdefault(ent, ent)
    return result


def ideal(rating: list[dict], families: dict[str, str],
              size: int = POOL_SIZE) -> list[str]:
    """Best N by hit share, but no more than FAMILY_CAP from one family.

    THE ORDER IS DELIBERATE: quality first, diversity as a limiter. The reverse
    order was tried on live data and gave slots to engines at 0.39-0.59 while
    evicting four at 0.86-0.96, because those turned out to belong to families
    already represented. Independence bought with a fourfold worse result set is
    a bad purchase: there is nothing left to corroborate, because there is
    nothing left to find.

    The cap is nevertheless mandatory. Without it the pool fills with shopfronts
    of one index: four of six members of a single family were once polled at
    once, and "confirmed by three engines" meant one index counted three times in
    a quarter of cases."""
    return breakdown(rating, families, size)["pool"]


def breakdown(rating: list[dict], families: dict[str, str],
           size: int = POOL_SIZE) -> dict:
    """The pool PLUS the reason each engine left out is left out.

    A reason must stand next to its consequence. One engine dropped out at a
    share of 0.86 — not on quality, but because its family had already taken both
    of its slots with better members (0.98 and 0.96). A reader who sees "0.86 —
    excluded" with no reason concludes the mechanism has lost its mind and goes
    to fix something that works."""
    used: dict[str, list[str]] = {}
    pool_state: list[str] = []
    why: dict[str, str] = {}
    for e in rating:
        name = e["engine"]
        fam = families.get(name, name)
        if_full = len(used.get(fam, ())) >= FAMILY_CAP
        if len(pool_state) >= size:
            why[name] = "by share"
            continue
        if if_full:
            why[name] = (f"family cap for {fam!r}: the places are taken by "
                           + ", ".join(used[fam]))
            continue
        pool_state.append(name)
        used.setdefault(fam, []).append(name)
    return {"pool": pool_state, "why_not_included": why,
            "families_in_pool": {c: h for c, h in used.items()}}


def recompute(conn: sqlite3.Connection, category: str = "general") -> dict:
    """Recompute the pool and write it down IF it changed in substance.

    The dead zone applies to EACH swap separately: an incumbent yields only to
    someone better by DEAD_ZONE. Without that the pool would twitch on hundredths
    and "the pool changed" would stop meaning anything."""
    conn.executescript(POOL_SCHEMA)
    for query in POOL_TOPUP:
        try:
            conn.execute(query)
        except sqlite3.OperationalError:
            pass      # column already there — ordinary on restart
    seed = SEEDS.get(category, SEED)
    rating = rating_of(conn, category=category)
    shares = {e["engine"]: e["share"] for e in rating}
    if not rating:
        return {"pool": list(seed), "computed": False, "category": category,
                "reason": "not enough observations: no engine reached "
                           f"{MIN_OBSERVATIONS} probes over {WINDOW_H} h"}

    families = families_of(conn, category=category)
    r = breakdown(rating, families)
    want, why = r["pool"], r["why_not_included"]

    line = conn.execute(
        "SELECT engines, computed FROM pool WHERE COALESCE(category,'general') = ?"
        " ORDER BY ts DESC LIMIT 1", (category,)).fetchone()
    now = json.loads(line[0]) if line else None

    if not now:
        reason = ("the first computed pool; before it the seed was asked. "
                   + ", ".join(f"{ent} {shares.get(ent, 0):.2f}" for ent in want))
        conn.execute("INSERT INTO pool (ts, engines, reason, computed, category)"
                     " VALUES (?,?,?,1,?)",
                     (int(time.time()), json.dumps(want, ensure_ascii=False),
                      reason, category))
        conn.commit()
        return {"pool": want, "computed": True, "reason": reason,
                "category": category,
                "why_not_included": why, "families": r["families_in_pool"]}

    # DEAD ZONE. Candidates are walked best-first; a candidate displaces the
    # WORST incumbent, and only when it is clearly better.
    fresh = list(now)
    swaps = []
    for candidate in want:
        if candidate in fresh:
            continue
        # whom it would have to replace: the worst incumbent not already wanted
        outside = [ent for ent in fresh if ent not in want]
        if not outside:
            break
        worst = min(outside, key=lambda ent: shares.get(ent, -1.0))
        if_better = shares.get(candidate, 0.0) - shares.get(worst, -1.0)
        if if_better >= DEAD_ZONE:
            fresh[fresh.index(worst)] = candidate
            swaps.append(f"{candidate} ({shares.get(candidate, 0):.2f}) displaced "
                          f"{worst} ({shares.get(worst, 0):.2f}) — "
                          f"{why.get(worst, 'by share')}; share difference "
                          f"{if_better:.2f}")
    # Poll order follows the share, best first: a search usually stops before the
    # end of the list, so the worst slots cost less than the good ones.
    fresh.sort(key=lambda ent: -shares.get(ent, 0.0))

    if fresh == now:
        return {"pool": now, "computed": True, "reason": "",
                "category": category,
                "why_not_included": why, "families": r["families_in_pool"]}
    reason = "; ".join(swaps) or "the poll order changed by share"
    conn.execute("INSERT INTO pool (ts, engines, reason, computed, category)"
                 " VALUES (?,?,?,1,?)",
                 (int(time.time()), json.dumps(fresh, ensure_ascii=False),
                  reason, category))
    conn.commit()
    return {"pool": fresh, "computed": True, "reason": reason,
            "category": category,
            "why_not_included": why, "families": r["families_in_pool"]}


def read_pool(db_path: str, category: str = "general") -> dict:
    """The pool, for the adapter. The database is opened read-only; if it is
    absent we return the SEED and SAY SO. Failure direction: "not computed" is
    not the same as "computed"."""
    seed = SEEDS.get(category, SEED)
    base_of = {"pool": list(seed), "source": "seed", "category": category}
    if not os.path.exists(db_path):
        return {**base_of, "reason": "no observation database"}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            line = conn.execute(
                "SELECT engines, reason, ts FROM pool"
                " WHERE COALESCE(category,'general') = ?"
                " ORDER BY ts DESC LIMIT 1", (category,)).fetchone()
        except sqlite3.OperationalError:
            # An older database without the category column: for the web we read
            # as before, and for other categories no pool can exist there by
            # construction.
            line = conn.execute(
                "SELECT engines, reason, ts FROM pool ORDER BY ts DESC LIMIT 1"
            ).fetchone() if category == "general" else None
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return {**base_of, "reason": f"database unavailable: {type(exc).__name__}"}
    if not line:
        return {**base_of, "reason": "the pool has never been computed"}
    return {"pool": json.loads(line[0]), "source": "observation",
            "category": category, "reason": line[1] or "", "when": line[2]}
