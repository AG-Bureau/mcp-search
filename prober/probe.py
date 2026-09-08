# -*- coding: utf-8 -*-
"""Engine prober: every N seconds it asks ONE engine and writes a verdict to the DB.

WHY IT EXISTS. Point measurements are not enough: between two of them a pool goes
stale silently — an engine can be the best of the set and return zero three weeks
later with nothing said about it. A hand-maintained list of engines goes out of
date exactly the way settings do, and just as invisibly.

WHY ONE AT A TIME AND RARELY. Asking them all at once is the fan-out that gets us
blocked in the first place: at forty concurrent searches three engines out of five
refused. Here it is the opposite — one engine every INTERVAL seconds, round-robin.
With 60 engines and 10 seconds a full lap takes 10 minutes, and EVERY engine sees
one request from us per 10 minutes, a load impossible to mistake for a bot raid.

WHY A MEANINGFUL QUERY AND NOT A PING. An engine returning ten off-topic links
looks alive by result count alone. So we ask with real queries whose answer is
known, and judge the hit with the SAME check that runs in production (`_relevant`
from server.py): one implementation, because a second would diverge from the first
at the first edit.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
from server import _relevant  # the same check that judges results in production
import reader                    # the same reader that runs in production
import pool                      # the same pool implementation the adapter reads
import refs                      # the references: a single source

SEARXNG_URL = (os.environ.get("SEARXNG_URL") or "http://searxng:8080").rstrip("/")
API_BASE = os.environ.get("PROBE_DB") or "/data/engines.db"
PROBE_INTERVAL_S = float(os.environ.get("PROBE_INTERVAL_S") or "10")

# Who is asked RARELY rather than every lap. To an engine that has thrown us out
# on address reputation, every probe is confirmation that we keep knocking: one
# engine returned 82 refusals out of 82 probes in 14 hours, all with the same
# reason, and changing transport did not bring it back. That is not a test one can
# pass but somebody else's decision about our address.
#
# Full exclusion was the obvious answer and it is too crude: the prober earns its
# keep by noticing engines COME BACK with nobody watching, and an excluded engine
# would have to be checked by hand — that is, not checked. Once a day is 1 request
# instead of 144, and a return is still noticed on its own.
RARE_EVERY = {e.strip() for e in
         (os.environ.get("PROBE_RARE") or "google cse").split(",") if e.strip()}
RARE_INTERVAL_S = float(os.environ.get("PROBE_RARE_INTERVAL_S") or "86400")
TIMEOUT_S_PROBE = float(os.environ.get("PROBE_TIMEOUT_S") or "30")

# REFERENCES: queries WHOSE CORRECT ANSWER IS KNOWN IN ADVANCE.
#
# This is the main check, and the two obvious alternatives each lie in their own
# way. Judging by the words of the query is fooled by spam: a page of word salad
# quoting the query scores 10 out of 10 "on topic". Judging by overlap with other
# engines is relative: it punishes an engine with its OWN index for finding
# something unique, and means nothing at all when every engine is wrong at once.
#
# A reference is absolute: we know which site MUST show up, and simply look at
# whether it did. Spam will not fool it, and a unique index is not punished for
# being unique.
#
# THE REFERENCES COME FROM ONE PLACE and are not declared here. The same set used
# to exist in two copies — a list here and a tuple of strings in the adapter; the
# copies would drift apart silently, and the dashboard would then honestly say
# "not measured" for months without naming a reason. See `adapter/refs.py`.
REFERENCES = refs.REFERENCES


def _domain(url: str) -> str:
    try:
        d = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return d.split(":")[0].removeprefix("www.")


def _hit_ref(urls: list[str], expect_domains: set[str]) -> bool:
    """Whether the expected site showed up. Subdomains count: docs.python.org is
    python.org, and demanding an exact match would punish an engine for taking us
    to a more precise page."""
    for u in urls:
        d = _domain(u)
        if any(d == j or d.endswith("." + j) for j in expect_domains):
            return True
    return False



# --- READING references ------------------------------------------------------
# TWO DIFFERENT KINDS OF REFERENCE IN ONE MODULE IS A DECISION, NOT AN OVERSIGHT.
# The asymmetry looks like a defect and is not one: collapsing both to one
# attribute buys a symmetry that checks nothing. The argument is below.
#
# THE REFERENCE ATTRIBUTE FOR READING DIFFERS FROM THE ONE FOR SEARCH. For search
# the reference is a canonical domain in the results (`_hit_ref` above). For
# reading that signal does not work: it asks whether we ARRIVED at the right
# place, whereas for reading the address is named in advance and the question is
# different — WAS WHAT WE WERE GIVEN THE PAGE. The four obvious signals all lie:
#
#   status code  a deliberately non-existent article path returned 200 five times
#                out of six (404 once). Not even stable.
#   title        the same non-existent page carried a <title> echoing the request.
#                A check of the form "the title resembles what we asked for" waves
#                such a stub through with distinction.
#   canonical    a 404 page declared itself the canonical address of itself.
#   length       a legitimate page can be 167 characters, while a stub can be 2990
#                characters of coherent text. A threshold cuts the wrong way.
#
# One thing remains that a stub does not forge by accident: KNOWLEDGE OF WHAT
# SPECIFICALLY must be on the page. A marker in the text is the reference
# attribute.
#
# THE NEGATIVE REFERENCE IS THE MAIN DIFFERENCE FROM SEARCH REFERENCES. A search
# reference only counts hits. Reading must have a NEGATIVE probe, because what we
# defend against here is precisely "looks like a result": a stub detector nobody
# checks degrades invisibly, since it always says "clean".
#
# Not theory: on the very first run these references caught two defects of OUR
# OWN. Cyrillic in a URL crashed us (a UnicodeEncodeError that looked like the
# site blocking us), and the word `captcha` in a wiki page's own chrome made us
# reject a live 33 905-character article.
READ_REFERENCES = [
    {"url": "https://example.com",
     "marker": "Example Domain", "expect": "read", "path": "plain",
     "checks": "the reader is alive at all; a short page is not a stub"},
    {"url": "https://ru.wikipedia.org/wiki/Кириллица",
     "marker": "кириллиц", "expect": "read", "path": "plain",
     "checks": "encoding, and non-ASCII in the URL (the mojibake defect class)"},
    {"url": "https://www.rfc-editor.org/rfc/rfc9110.txt",
     "marker": "Hypertext Transfer Protocol", "expect": "read", "path": "plain",
     "checks": "a non-HTML type, truncation and continuation by offset"},
    {"url": "https://www.rbc.ru/",
     "marker": "РБК", "expect": "read", "path": "browser",
     "checks": ("IS THE BROWSER PATH ALIVE: a plain request gets 401 behind "
                   "an anti-bot shield. While the path is not wired up this "
                   "reference must be red — that is a statement about a missing "
                   "capability, not a breakage. Without it a dead browser is "
                   "indistinguishable from a page with no text")},
    {"url": "https://www.tadviser.ru/index.php/Компания:Заведомо_Нет_Такой_12345",
     "marker": None, "expect": "stub", "path": "plain",
     "checks": "NEGATIVE: that rejection works and does not always stay silent"},
    # PDF — FOUR REFERENCES FOR DIFFERENT GENRES. One catches a blunt parse
    # failure and misses degradation on layout, and layout is exactly what
    # destroys reading order.
    {"url": "https://www.rfc-editor.org/rfc/rfc9110.pdf",
     "marker": "Hypertext Transfer Protocol", "expect": "read", "path": "plain",
     "near": ("RFC 9110", "HTTP Semantics", 40),
     "checks": ("PDF, solid prose in a single column: parsing and the "
                   "relation \"document <-> its subtitle\"")},
    {"url": "https://arxiv.org/pdf/1706.03762",
     "marker": "Attention Is All You Need", "expect": "read", "path": "plain",
     # TWO relations in one reference: a document has many of them, and an extra
     # address in the lap costs outbound requests. BOTH are checked — one relation
     # coming apart is enough.
     "near": [("Attention Is All You Need", "Ashish Vaswani", 60),
               # A DENSE RESULTS TABLE is the decisive genre: the characters come
               # out right while the property drifts away from its object. This
               # pair is a real one — a method name and its number on the same
               # line ("Transformer (big) 28.4 41.8 ...").
               ("Transformer (big)", "28.4", 30)],
     "checks": ("A TWO-COLUMN PDF plus A DENSE RESULTS TABLE: the relation "
                   "\"title <-> first author\" and the relation "
                   "\"method <-> its number\"")},
    {"url": "https://files.stroyinf.ru/Data/852/85280.pdf",
     "marker": "ГОСТ Р 51511", "expect": "read", "path": "plain",
     "near": ("ГОСТ Р 51511", "Печати с воспроизведением", 40),
     "checks": ("A CYRILLIC PDF with font subsets — the case where a "
                   "stock-library parser yielded 41 characters instead of "
                   "52 858. The relation is a REAL one: a standard\u0027s number "
                   "and its title from the reference list, that same "
                   "\"object <-> its property\" which collapses when reading "
                   "order is lost. A letterhead makes a weaker anchor: two of "
                   "its lines stay adjacent under almost any parse")},
    {"url": "https://www.rfc-editor.org/rfc/rfc99999.pdf",
     "marker": None, "expect": "stub", "path": "plain",
     "checks": ("NEGATIVE FOR PDF: an address ending in .pdf that does not "
                   "exist. The server returns an HTML error page, and it must NOT "
                   "pass as a document that was read")},
]

# WHICH GENRES THE REFERENCES COVER AND WHICH THEY DO NOT. Stated plainly,
# because "there are references" and "the genres are covered" are different
# claims, and the second is easy to mistake for the first.
#
# Covered: solid single-column prose; two-column layout; Cyrillic with font
# subsets and the "object <-> its property" relation; a missing document behind a
# .pdf extension; a dense results table. A LARGE FILE is covered as well: a ~3 MB
# specification is cut by a 2 MB blanket ceiling, after which the parser declares
# it broken. That reference stands guard so truncation does not start looking like
# somebody else's document being broken.
#
# NOT COVERED: A SCAN WITH NO TEXT LAYER. The behaviour is implemented and covered
# by a UNIT TEST over a hand-built PDF (`tests/test_reader.py`): pages present, no
# text, an "empty" answer whose reason names recognition. There is no live
# reference: no stable public address with a scan was found, and putting an
# address into the prober that will disappear tomorrow means introducing a source
# of false red.
#
# The gap is work, not a disclaimer. Keeping it named here is cheaper than
# discovering in a month that "there are PDF references" meant "there are five out
# of six".

# WHY A PDF REFERENCE IS BUILT DIFFERENTLY FROM A PAGE REFERENCE.
#
# For a page we check a marker in the text: present means we were given that page.
# For a PDF that is NOT ENOUGH.
#
# On a product catalogue a parser can extract every character correctly, with a
# clean encoding and no rubbish, and yet spread one specification across three
# lines so that the labels come away from their values:
#
#     lamella size / pack / pack area        <- three labels in a row
#     1382x195 mm / 8 pcs / 2.156 m2         <- three values in a row
#     Deck Oak                               <- the object all of it belongs to
#
# Recognition shows the same shape: 44 pages out of 48 clean by characters, while
# on dense pages the reading order collapses and an article number drifts away
# from its specification.
#
# CHARACTER ACCURACY AND STRUCTURAL ACCURACY ARE DIFFERENT QUANTITIES, and they
# diverge sharply. A check of the form "text was extracted" misses this by
# construction: plenty of characters, correct letters. Hence the `near` field on
# a PDF reference: (what, what, how many characters) — two fragments that must
# stand close TO EACH OTHER. Once they come apart the structure is lost, however
# many characters were extracted.
#
# And a second rule from the same place: a reference must contain ONLY WHAT IS
# PHYSICALLY OBSERVABLE IN THE SOURCE. An expected list holding article numbers
# synthesised from a database rather than printed on the page drags the score from
# an honest 85-90% down to 69-76%: the parser "fails to find" what was never
# there. A reference not taken from the source measures the wrong thing, and
# measures it downwards — which makes you repair what works.

# HOW OFTEN THE READING REFERENCES ARE READ. The prober ticks once per INTERVAL
# seconds over engines; a read happens on every READ_EVERY-th tick, round-robin.
# A full lap is len(READ_REFERENCES) x READ_EVERY x PROBE_INTERVAL_S. With nine
# references, 30 ticks and 10 seconds that is 45 minutes per address — under one
# request per source per hour.
#
# THE ARITHMETIC MOVES WITH THE NUMBER OF REFERENCES, and a value chosen for a
# smaller set becomes wrong without saying so: at five references the same 30
# ticks gave 25 minutes. Recompute the lap when adding a reference, or read the
# real figure from the line the prober prints at start-up.
#
# The lap gets GENTLER on its own as references are added: a longer lap means
# fewer requests per source. If the set grows much further the lap will stretch
# far enough that a breakage is learned about half a day late — and then the right
# answer is not "knock more often" but to split the laps: cheap references often,
# heavy PDFs rarely.
#
# The rate is not chosen to be "as often as possible". Some thirty requests to one
# site within half an hour walk it down the ladder 200 -> 429 -> total silence,
# and it stays silent for that address while answering another machine in 0.22 s.
# Our address is the scarcest resource this module has, and a prober that burns
# through its own reference sources lies exactly where it is supposed to tell the
# truth.
READ_EVERY = int(os.environ.get("READ_PROBE_EVERY") or "30")

# How often the POOL is recomputed. The prober computes it because it is the one
# with the database open for writing; the adapter only reads the result. Every 200
# ticks is about once per half hour at a 10 s interval, and more often is
# pointless: over half an hour the ratios on a sample of a hundred probes barely
# move, while every recomputation reads the whole probe table.
POOL_EVERY = int(os.environ.get("POOL_RECALC_EVERY") or "200")


def _POSITIONS(text: str, what: str) -> list[int]:
    outside, c = [], 0
    while True:
        i = text.find(what, c)
        if i < 0:
            return outside
        outside.append(i)
        c = i + 1


def _near_each_other(text: str, a: str, b: str, window: int) -> bool:
    """Whether `a` and `b` occur anywhere closer together than `window` characters.

    ANY PAIR OF OCCURRENCES, NOT THE FIRST. Taking `find` — the first occurrence
    of each fragment — fails on a live reference: in one paper the number "28.4"
    appears both in the abstract (offset 1183) and in the results table beside its
    method (offset 21998). The check compared the abstract with the table and
    declared the relation lost.

    A value repeated across a document is the norm, not the exception — that is
    precisely why it reaches the abstract. The check must ask "do they occur near
    each other ANYWHERE", not "did their first occurrences coincide".
    """
    pa, pb = _POSITIONS(text, a), _POSITIONS(text, b)
    if not pa or not pb:
        return False
    return any(abs(j - i) <= window for i in pa for j in pb)


# THROUGH WHOSE HANDS THE PROBE GOES. The prober is a separate container, so
# calling `reader.read()` IN ITS OWN PROCESS would break the rule: CHECK FROM
# WHERE THE CONSUMER CALLS FROM.
#
# The divergence is observable. If the prober's deployment has no
# `BROWSER_WS_URL` while a reference is declared for the browser path, one
# snapshot says both "browser — alive" (about the adapter) and "browser path not
# wired up" (about the prober); and if the adapter's browser dies, nothing turns
# red, because the prober watches ITS OWN process while the contract names that
# reference the only honest check of the browser.
#
# So the probe goes through the adapter's door — the same door the caller uses.
# It thereby checks the door as well: if the adapter is down the references go
# red, and that is TRUE, because reading really is impossible at that moment.
ADAPTER_URL = (os.environ.get("ADAPTER_URL") or "http://ag-search:8081").rstrip("/")
# Its own timeout: reading through the door adds up the adapter's own budget
# (90 s) and the browser stage. The general 30 s timeout would cut the browser
# reference short and keep it permanently red — lying about exactly the capability
# it exists to check.
READ_TIMEOUT_S = float(os.environ.get("PROBE_READ_TIMEOUT_S") or "150")


def _read_via_door(ref_item: dict) -> dict:
    """Call the adapter's `GET /ag/read`. Never raises."""
    params = {"url": ref_item["url"], "max_chars": str(reader.MAX_CHARS),
                 "mode": ref_item["path"], "fresh": "1"}
    if ref_item.get("marker"):
        params["expect"] = ref_item["marker"]
    url = ADAPTER_URL + "/ag/read?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=READ_TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        # The door did not answer, and that is NOT "the page failed to read". We
        # return the shape of a reply with the reason named, so that the verdict
        # does not become a silent zero.
        return {"ok": False, "results": [{
            "status": "not_reached", "reason": f"adapter door: "
                                            f"{type(exc).__name__}: {exc}"[:160],
            "expected_found": None, "content": "", "total_chars": 0,
            "via": "", "http_status": None}]}


def probe_read(ref_item: dict) -> dict:
    """One read of a reference page. The verdict is a MATCH AGAINST EXPECTATION.

    For most references the expectation is "read, with the marker in place"; for
    the negative ones it is "stub". Judging both by the same yardstick is
    mandatory: a detector that only looks for the good never notices that it has
    stopped catching the bad.
    """
    t0 = time.perf_counter()
    answer = _read_via_door(ref_item)
    ms = int((time.perf_counter() - t0) * 1000)
    r = (answer.get("results") or [{}])[0]
    status = r.get("status", "no answer")
    hit = status == ref_item["expect"] and (
        ref_item["marker"] is None or r.get("expected_found") is True)
    # THE RELATION CHECK. Two fragments must stand close to each other; once they
    # come apart the structure is lost, and the hit is not counted however many
    # characters were extracted.
    structure = ""
    pairs = ref_item.get("near")
    if hit and pairs:
        # One pair or a list of pairs: a document has many relations, and an
        # extra address in the lap costs outbound requests. One relation coming
        # apart is enough to fail the probe.
        if isinstance(pairs, tuple):
            pairs = [pairs]
        text = r.get("content") or ""
        for a, b, window in pairs:
            if not _near_each_other(text, a, b, window):
                hit = False
                structure = (f"structure: {a!r} and {b!r} never occurred near "
                             f"each other (window {window} characters)")
                break
    return {"ts": int(time.time()), "url": ref_item["url"], "path": ref_item["path"],
            "expected": ref_item["marker"] or f"status={ref_item['expect']}",
            "status": status, "via": r.get("via", ""),
            "http_status": r.get("http_status"), "chars": r.get("total_chars") or 0,
            "latency_ms": ms, "hit": int(hit),
            "reason": (structure or r.get("reason") or r.get("stub_reason") or "")[:200],
            "stub_check": r.get("stub_check", "")}


# --- IMAGE references --------------------------------------------------------
# THE REFERENCE ATTRIBUTE IS THE DOMAIN OF THE IMAGE SOURCE, not of the page. An
# image has two addresses, and a check against the PAGE address would pass in the
# very case where the picture itself comes from somebody else's stock warehouse.
# We ask "where is the file from", not "where is it mentioned".
#
# Why that is needed: on a company-name query an image engine backed by a museum
# catalogue answers with a self-portrait from 1878. By result count it looks
# excellent.
#
# ALL FOUR REFERENCES ARE CONFIRMED BY OBSERVATION, not assigned. Expectations
# chosen "by meaning" — a transport operator's own domain for "metro",
# `python.org` for "Python logo" — are NOT IN THE RESULTS AT ALL: for general
# concepts image engines return stock warehouses and aggregators, not the
# canonical source. Such a reference is permanently red for the wrong reason,
# which is the CONTAMINATED REFERENCE this module has a rule against: a reference
# must contain ONLY WHAT IS PHYSICALLY OBSERVABLE IN THE SOURCE.
#
# Hence the selection property: a canonical domain is found by the NAME OF AN
# ENTITY (a company, a publication, a project), not by a general concept. "Habr
# logo" leads to habrastorage.org; "metro map" leads anywhere at all.
IMAGE_REFERENCES = [
    {"q": "Рикор Электроникс", "expect": {"rikor-electronics.ru", "tadviser.ru"}},
    {"q": "Хабр логотип", "expect": {"habr.com", "habrastorage.org"}},
    {"q": "Википедия логотип", "expect": {"wikimedia.org"}},
    {"q": "Python programming language logo site:python.org", "expect": {"python.org"}},
]

# How often the image engines are asked. Their own lap, not shared with the web
# one: the category holds 49 engines, and running them at the same rate would
# double our outbound traffic for a capability that no caller uses yet.
IMAGES_EVERY = int(os.environ.get("IMAGE_PROBE_EVERY") or "40")


def probe_images(engine: str, ref_item: dict) -> dict:
    """One image-engine probe. The verdict is by the domain of the image SOURCE."""
    t0 = time.perf_counter()
    params = urllib.parse.urlencode({
        "q": ref_item["q"], "format": "json", "pageno": 1,
        "language": "ru" if any("а" <= c <= "я" for c in ref_item["q"].lower()) else "en",
        "safesearch": "0", "engines": engine})
    try:
        with urllib.request.urlopen(SEARXNG_URL + "/search?" + params, timeout=TIMEOUT_S_PROBE) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "error", "results": 0, "relevant": 0,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "reason": f"{type(exc).__name__}: {exc}"[:200],
                "first_title": None, "urls": None, "hit": 0}
    ms = int((time.perf_counter() - t0) * 1000)
    own = [x for x in (d.get("results") or []) if engine in (x.get("engines") or [])]
    # We count FILE addresses specifically: they are what the verdict is made on,
    # and what families are later computed from. A page address does not go here —
    # otherwise the family "they share an image warehouse" gets confused with
    # "they link to the same article".
    links = [str(x.get("img_src") or "") for x in own if x.get("img_src")]
    refusal = d.get("unresponsive_engines") or []
    hit = _hit_ref(links, ref_item["expect"])
    if refusal and not own:
        verdict = "refused"
    elif not own:
        verdict = "empty"
    elif not hit:
        verdict = "miss"
    else:
        verdict = "ok"
    return {"verdict": verdict, "results": len(own), "relevant": len(links),
            "latency_ms": ms, "reason": (refusal[0][1] if refusal else None),
            "first_title": (own[0].get("title") or "")[:200] if own else None,
            "urls": json.dumps(links[:8]), "hit": int(hit)}


SCHEMA = """
CREATE TABLE IF NOT EXISTS probes (
  ts        INTEGER NOT NULL,      -- unix time of the probe
  engine    TEXT    NOT NULL,
  query     TEXT    NOT NULL,
  verdict   TEXT    NOT NULL,      -- ok | empty | refused | irrelevant | error
  results   INTEGER NOT NULL,      -- how many links it returned
  relevant  INTEGER NOT NULL,      -- how many of them were on topic
  latency_ms INTEGER NOT NULL,
  reason    TEXT,                  -- refusal reason, as the metasearch named it
  first_title TEXT,                -- so a verdict can be re-checked by eye
  urls        TEXT,                -- up to 8 result addresses, as a JSON list
  hit         INTEGER              -- 1 = found the reference answer, 0 = did not
);
CREATE INDEX IF NOT EXISTS probes_engine_ts ON probes(engine, ts DESC);
CREATE INDEX IF NOT EXISTS probes_ts ON probes(ts DESC);

-- Observation of READING. A separate table rather than rows in `probes`: an
-- engine probe is keyed by "engine + query", a read probe by "address + fetch
-- path", and merging them would leave half the columns empty and make every
-- query guess which kind of row it is looking at.
CREATE TABLE IF NOT EXISTS reads (
  ts        INTEGER NOT NULL,
  url       TEXT    NOT NULL,
  path      TEXT    NOT NULL,      -- plain | browser: whose health this is
  expected  TEXT    NOT NULL,      -- a marker in the text, or the expected status
  status    TEXT    NOT NULL,      -- the ag.read/1 outcome, seven values
  via       TEXT,                  -- what actually fetched it
  http_status INTEGER,
  chars     INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  hit       INTEGER NOT NULL,      -- 1 = the outcome matched the expectation
  reason    TEXT,
  stub_check TEXT
);
CREATE INDEX IF NOT EXISTS reads_url_ts ON reads(url, ts DESC);
CREATE INDEX IF NOT EXISTS reads_ts ON reads(ts DESC);
"""

# Columns added after the schema: the database is already live, and dropping the
# history of measurements for the sake of a new field is not an option — that
# history is the whole value.
TOPUP = ["ALTER TABLE probes ADD COLUMN urls TEXT",
          "ALTER TABLE probes ADD COLUMN hit INTEGER",
          "ALTER TABLE probes ADD COLUMN category TEXT NOT NULL DEFAULT 'general'"]


def attach() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(API_BASE), exist_ok=True)
    conn = sqlite3.connect(API_BASE, timeout=30)
    # WAL: the prober writes while the adapter reads — without it the reader
    # would hit locks.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for query in TOPUP:
        try:
            conn.execute(query)
        except sqlite3.OperationalError:
            pass      # the column is already there — normal on a restart
    conn.commit()
    return conn


def disabled_in_image() -> set[str]:
    """Engines the metasearch ships switched off (`disabled: true`).

    WHAT THE FLAG MEANS — AND WHAT IT DOES NOT. It is tempting to read it as "the
    vendor considers this engine not ready". MEASURED ON A LIVE INSTANCE, that
    reading DOES NOT HOLD: FORTY-SEVEN engines ship disabled in the general
    category, and among them are engines with reference hit rates of 0.98 and 0.91
    — both in our computed pool and both excellent.

    So `disabled: true` means "not enabled by default", and the reasons vary:
    traffic, regional scope, terms of use, caution on the maintainers' part. It is
    a FACT, not a verdict, and it must be presented as one.

    WHY SHOW IT AT ALL. The flag is cheap and read straight from the settings, and
    beside a hit rate it raises a question worth asking out loud: "this engine is
    in the pool, yet the vendor does not enable it by default — do we know why?"
    For one engine the answer turned out to be heavy, and it was found NOT from
    the flag but in that engine's own docstring: it must first scrape a parameter
    out of the front page, because the parameter cannot be constructed, and only
    then call the API; break the first link and everything is empty. At a hit rate
    of 0.11 that explains everything — but the explanation came from reading, not
    from the flag.

    In short: the flag is a reason to look, never grounds to exclude. Only
    observation excludes.
    """
    with urllib.request.urlopen(SEARXNG_URL + "/config", timeout=30) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return {str(e["name"]) for e in d.get("engines") or []
            if e.get("name") and e.get("enabled") is False}


def image_engines() -> list[str]:
    """Image-category engines from the metasearch registry, not from a file of ours."""
    with urllib.request.urlopen(SEARXNG_URL + "/config", timeout=30) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    all_ = sorted(str(e["name"]) for e in d.get("engines") or []
                 if "images" in (e.get("categories") or []) and e.get("name"))
    return [e for e in all_ if e not in RARE_EVERY and "google cse" not in e]


def engine_names() -> list[str]:
    """The list from /config, not from a file of ours: engines appear and vanish
    with an image update, and a hard-coded list goes stale exactly like everything
    else. The general category is taken — it alone is about web search."""
    with urllib.request.urlopen(SEARXNG_URL + "/config", timeout=30) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    all_ = sorted(str(e["name"]) for e in d.get("engines") or []
                 if "general" in (e.get("categories") or []) and e.get("name"))
    return [e for e in all_ if e not in RARE_EVERY]


def probe(engine: str, ref_item: dict) -> dict:
    query = ref_item["q"]
    t0 = time.perf_counter()
    params = urllib.parse.urlencode({
        "q": query, "format": "json", "pageno": 1,
        "language": "ru" if any("а" <= c <= "я" for c in query.lower()) else "en",
        "safesearch": "0", "engines": engine})
    try:
        with urllib.request.urlopen(SEARXNG_URL + "/search?" + params, timeout=TIMEOUT_S_PROBE) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        return {"verdict": "error", "results": 0, "relevant": 0,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "reason": f"{type(exc).__name__}: {exc}"[:200],
                "first_title": None, "urls": None, "hit": 0}
    ms = int((time.perf_counter() - t0) * 1000)
    # Only THIS engine's results: the metasearch sometimes mixes neighbours in.
    own = [x for x in (d.get("results") or []) if engine in (x.get("engines") or [])]
    normalised = [{"title": x.get("title", ""), "snippet": x.get("content", ""),
                    "url": x.get("url", "")} for x in own]
    refusal = d.get("unresponsive_engines") or []
    reason = refusal[0][1] if refusal else None
    if refusal and not own:
        verdict = "refused"
    elif not own:
        verdict = "empty"
    elif not _relevant(query, normalised):
        verdict = "irrelevant"
    else:
        verdict = "ok"
    on_topic_flag = sum(1 for x in normalised if _relevant(query, [x]))
    links = [x.get("url", "") for x in own]
    hit = _hit_ref(links, ref_item["expect"])
    # The REFERENCE verdict outranks the word verdict: an engine that answers
    # briskly and misses the reference is no more useful for it. "miss" is not a
    # refusal but "looked in the wrong place"; keeping it apart from refused and
    # empty is mandatory, or a report merges "we were thrown out" with "it found
    # rubbish".
    if verdict == "ok" and not hit:
        verdict = "miss"
    return {"verdict": verdict, "results": len(own), "relevant": on_topic_flag,
            "latency_ms": ms, "reason": reason,
            "first_title": (own[0].get("title") or "")[:200] if own else None,
            # The addresses are kept for a signal the text check cannot give:
            # OVERLAP with other engines. A spam page that pasted our query
            # verbatim passes the word check, but nobody else finds its address.
            # A link found independently by two engines is almost certainly real.
            "urls": json.dumps(links[:8]), "hit": int(hit)}


def main() -> None:
    conn = attach()
    items: list[str] = []
    image_items: list[str] = []
    pool_counted = False
    i = 0
    print(f"prober: database {API_BASE}, interval {PROBE_INTERVAL_S} s", flush=True)
    if READ_EVERY > 0:
        cycle = len(READ_REFERENCES) * READ_EVERY * PROBE_INTERVAL_S / 60
        print(f"prober: {len(READ_REFERENCES)} reading references, each read "
              f"once per {cycle:.0f} min", flush=True)
    if RARE_EVERY:
        print(f"prober: asked rarely (every {RARE_INTERVAL_S/3600:.0f} h): "
              f"{sorted(RARE_EVERY)}", flush=True)
    while True:
        if i % 200 == 0 or not items:
            try:
                fresh = engine_names()
                if fresh != items:
                    print(f"prober: {len(fresh)} engines in the lap "
                          f"(full lap ~{len(fresh) * PROBE_INTERVAL_S / 60:.0f} min)", flush=True)
                items = fresh
            except Exception as exc:  # noqa: BLE001
                print(f"prober: could not read /config: {exc}", flush=True)
                time.sleep(PROBE_INTERVAL_S)
                continue
        # The rare ones jump the queue: when one is due this tick goes to it and
        # the ordinary lap shifts by one. The time of the last probe is taken FROM
        # THE DATABASE, not from process memory: otherwise every restart of the
        # prober would poke again exactly the engine we are trying not to touch.
        due = None
        for name in sorted(RARE_EVERY):
            line = conn.execute(
                "SELECT MAX(ts) FROM probes WHERE engine = ?", (name,)).fetchone()
            when = (line[0] if line else None) or 0
            if time.time() - when >= RARE_INTERVAL_S:
                due = name
                break
        engine = due or items[i % len(items)]
        if due:
            print(f"prober: rare check of {due} "
                  f"(once per {RARE_INTERVAL_S/3600:.0f} h)", flush=True)
        # The reference changes ONCE PER LAP, not once per probe: that way every
        # engine in a lap is asked the same question and their results are
        # comparable. Different questions within one lap would make the comparison
        # meaningless.
        ref_item = REFERENCES[(i // len(items)) % len(REFERENCES)]
        r = probe(engine, ref_item)
        conn.execute(
            "INSERT INTO probes (ts, engine, query, verdict, results, relevant,"
            " latency_ms, reason, first_title, urls, hit)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), engine, ref_item["q"], r["verdict"], r["results"],
             r["relevant"], r["latency_ms"], r["reason"], r["first_title"],
             r.get("urls"), r.get("hit", 0)))
        conn.commit()
        if r["verdict"] != "ok":
            print(f"prober: {engine} [{ref_item['q'][:24]}] -> {r['verdict']}"
                  f"{' (' + str(r['reason'])[:60] + ')' if r['reason'] else ''}", flush=True)
        if not due:          # a rare probe does not advance the lap
            i += 1

        # IMAGES. Their own lap and their own rate: the category holds 49
        # engines, and running them at the shared rate would double our outbound
        # traffic for a capability no caller uses yet.
        if IMAGES_EVERY > 0 and i % IMAGES_EVERY == 0:
            try:
                if not image_items:
                    image_items = image_engines()
                    print(f"prober: {len(image_items)} image engines", flush=True)
                eng = image_items[(i // IMAGES_EVERY) % len(image_items)]
                ref = IMAGE_REFERENCES[(i // IMAGES_EVERY) % len(IMAGE_REFERENCES)]
                rk = probe_images(eng, ref)
                conn.execute(
                    "INSERT INTO probes (ts, engine, query, verdict, results, relevant,"
                    " latency_ms, reason, first_title, urls, hit, category)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,'images')",
                    (int(time.time()), eng, ref["q"], rk["verdict"], rk["results"],
                     rk["relevant"], rk["latency_ms"], rk["reason"],
                     rk["first_title"], rk.get("urls"), rk.get("hit", 0)))
                conn.commit()
                if rk["verdict"] != "ok":
                    print(f"prober/images: {eng} [{ref['q'][:22]}] -> {rk['verdict']}",
                          flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"prober/images: probe failed: "
                      f"{type(exc).__name__}: {exc}", flush=True)
            # The image pool is recomputed on its own rate, in its own category.
            if i % (IMAGES_EVERY * 20) == 0:
                try:
                    ic = pool.recompute(conn, "images")
                    if ic.get("reason"):
                        print(f"prober/image-pool: {', '.join(ic['pool'])}", flush=True)
                        print(f"prober/image-pool: {ic['reason']}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"prober/image-pool: {type(exc).__name__}: {exc}", flush=True)

        # THE POOL. Recomputed HERE and not in the adapter: the prober is the one
        # with the database open for writing, and "whom to ask" must be computed
        # in a single place.
        #
        # THE FIRST RECOMPUTATION HAPPENS AT ONCE, not half an hour in. A plain
        # "every N-th tick" condition first fires on tick N, and all that time the
        # adapter would be serving the SEED while observations already exist. The
        # seed is an honest answer over an empty database, not over a full one.
        if POOL_EVERY > 0 and (not pool_counted or i % POOL_EVERY == 0):
            pool_counted = True
            try:
                result = pool.recompute(conn)
                if result.get("reason"):
                    print(f"prober/pool: {', '.join(result['pool'])}", flush=True)
                    print(f"prober/pool: {result['reason']}", flush=True)
            except Exception as exc:  # noqa: BLE001
                # A recomputation has no right to bring observation down: without
                # probes there will be no data for the next recomputation either.
                print(f"prober/pool: recomputation failed: "
                      f"{type(exc).__name__}: {exc}", flush=True)

        # READING. It runs on the same tick and the same lap as the engines: a
        # separate process would mean a second rate limiter and a second database,
        # while the module has a single outbound rate — it has a single address.
        if READ_EVERY > 0 and i % READ_EVERY == 0:
            ref = READ_REFERENCES[(i // READ_EVERY) % len(READ_REFERENCES)]
            try:
                h = probe_read(ref)
            except Exception as exc:  # noqa: BLE001
                # The prober has no right to die over one page: it exists to
                # notice failures, not to take part in them.
                h = {"ts": int(time.time()), "url": ref["url"], "path": ref["path"],
                     "expected": ref["marker"] or f"status={ref['expect']}",
                     "status": "error", "via": "", "http_status": None,
                     "chars": 0, "latency_ms": 0, "hit": 0,
                     "reason": f"{type(exc).__name__}: {exc}"[:200],
                     "stub_check": ""}
            conn.execute(
                "INSERT INTO reads (ts, url, path, expected, status, via,"
                " http_status, chars, latency_ms, hit, reason, stub_check)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (h["ts"], h["url"], h["path"], h["expected"], h["status"],
                 h["via"], h["http_status"], h["chars"], h["latency_ms"],
                 h["hit"], h["reason"], h["stub_check"]))
            conn.commit()
            if not h["hit"]:
                # A RED REFERENCE IS THREE DIFFERENT PIECES OF NEWS, and they
                # must not be conflated:
                #  · "read", but the relation did not hold — OUR parsing broke;
                #  · a stub or a refusal where text was expected — the source is
                #    gone or has closed, and the defect is NOT ours;
                #  · "did not get through" — the fetch path is not wired up (the
                #    browser), a statement about a missing capability.
                # The status in the line tells them apart without guesswork, which
                # is why it is printed BESIDE the expectation and not instead of it.
                print(f"prober/read: {h['url'][:60]} [{h['path']}] -> "
                      f"{h['status']} (expected {ref['expect']})"
                      f"{' ' + h['reason'][:60] if h['reason'] else ''}", flush=True)

        time.sleep(PROBE_INTERVAL_S)


if __name__ == "__main__":
    main()
