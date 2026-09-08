# -*- coding: utf-8 -*-
"""The MCP server of the search module: search as a capability, not a sidecar.

WHY IT EXISTS. A metasearch engine can return JSON, but it cannot speak MCP and
can do nothing beyond searching whichever engines it was told about. The policy
of "which engines to ask and how to spread the load" is a property of the
metasearch layer, not of an engine; kept in the caller instead, it silently
duplicates the module's own settings, and once the two lists drift apart the
results grow poorer with no error and no log line. Here that policy sits in one
place, beside the settings it describes.

ONE IMPLEMENTATION BEHIND EVERY DOOR.
  POST /mcp                       MCP (JSON-RPC 2.0) for tool clients
  GET  /ag/search /ag/read        the contracts, over plain HTTP
  GET  /ag/images /ag/deep /ag/screenshot
  GET  /healthz                   the health of the CAPABILITY, not the process
  GET  /engines /pages /stats /tool-spec   the observation views
A second implementation of a capability "for another protocol" would diverge from
the first at the first edit — so there is none: transport outside, search inside.

NO DEPENDENCIES. The standard library only: pulling in a web framework would mean
paying in upgrades and CVEs for what we do not use.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import reader          # page reading: contract ag.read/2, see reader.py
import pool            # the engine pool — computed, never written by hand
import model           # the single place where the module talks to a model
import deep            # deep search: wave orchestration, contract ag.deep/2
import refs            # the references: one source for the prober and the views

# --- Call accounting ---------------------------------------------------------
# THE QUESTION THIS ANSWERS IS "IS ANYTHING CALLING IT AT ALL?" Without counters
# there is nothing to answer with: the request log is deliberately silenced (an
# empty log_message, so a health check every 30 seconds does not drown the
# output), and the answer has to be dug out of interface byte counts. A service
# that cannot say whether it is being called has the same defect we chase in the
# engines: the mechanism exists and nothing shows it.
#
# Counted cheaply and always: counters in memory, served on GET /stats. The state
# lives in the process and resets on restart, and that is honest — it is about
# "is it working now", not about history.
_started_at = time.time()
_counters_lock = threading.Lock()
_counters = {"total": 0, "by_path": {}, "searches": 0, "errors": 0,
         "with_corroboration": 0, "reads": 0, "pages": 0,
         "last_call": None, "last_search": None,
         "last_read": None}


def _count_call(path: str, search: bool = False, error: bool = False,
              corroborated: bool = False, reading: bool = False,
              page_count: int = 0) -> None:
    with _counters_lock:
        _counters["total"] += 1
        _counters["by_path"][path] = _counters["by_path"].get(path, 0) + 1
        _counters["last_call"] = time.time()
        if search:
            _counters["searches"] += 1
            _counters["last_search"] = time.time()
        if reading:
            _counters["reads"] += 1
            _counters["pages"] += page_count
            _counters["last_read"] = time.time()
        if corroborated:
            _counters["with_corroboration"] += 1
        if error:
            _counters["errors"] += 1


def _ago(t: float | None) -> str | None:
    """ISO time plus "how many seconds ago" — the second reads without arithmetic."""
    if t is None:
        return None
    return (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
            + f" ({int(time.time() - t)} s ago)")


CONTRACT = "ag.search/2"
PROTOCOL = "2024-11-05"                      # the same as the caller's MCP server
# THE VERSION IS TAKEN FROM ONE PLACE, not written out here as well. A literal
# would be a second truth about the same thing: the release it names and the one
# a site administrator sees in our `User-Agent` would drift apart, and nothing
# would report it. `1` used to stand here — internal numbering that matched no
# release at all.
SERVER = {"name": "ag-mod-search", "version": reader.VERSION}

SEARXNG_URL = (os.environ.get("SEARXNG_URL") or "http://searxng:8080").strip().rstrip("/")
PORT = int(os.environ.get("PORT") or "8081")
# The prober database, read-only. If it is missing, /engines says honestly that
# the prober is not running rather than pretending the engines are healthy.
PROBE_DB = os.environ.get("PROBE_DB") or "/data/engines.db"
TIMEOUT_S = float(os.environ.get("SEARXNG_TIMEOUT_S") or "45")

# ---------------------------------------------------------------------------
# WHO GETS ASKED, AND WHY IT IS NOT A LIST
# ---------------------------------------------------------------------------
# THE POOL IS NOT A LITERAL. A hand-written tuple of engine names in this place
# needs revising as often as the engines change — three times in one day is not
# unusual, each revision against the previous one and each correct on its own
# data. The problem is neither the engines nor the quality of the decisions: a
# decision is frozen while observation goes on.
#
# So the pool is computed by the prober from its own probes (adapter/pool.py) and
# merely read here. Verified by falsification: a planted bad run takes an engine
# out of the pool WITH NO CODE CHANGE, and restoring the run brings it back by
# itself.
#
# WHAT THE POOL CANNOT BE ALLOWED TO DO, whoever computes it:
#
# · ENGINES ARE ASKED ONE BY ONE, TOP DOWN, stopping as soon as there is enough.
#   A fan-out to all of them at once is wasteful and harmful. The ladder of
#   concurrent searches:
#
#       concurrent   wall    who refused
#         5          1.5 s   nobody
#        10          2.2 s   one engine on 4 of 10
#        20          3.4 s   one engine on 10, another on 6
#        40          6.1 s   one engine on 40 of 40, two more heavily
#
#   Our own machine spends 460 ms of CPU on the adapter and 4.7 s on the
#   metasearch across such a run — about 6% of one core. The limit is not our
#   hardware but other people's willingness to serve us, and a fan-out brings that
#   limit five times closer.
#
# · RECOVERY TAKES MINUTES, NOT SECONDS. After an overload one engine comes back
#   in about four minutes and another stays silent far longer, so an overload
#   costs minutes of blindness per engine.
#
# · AN ENGINE CAN BE LOST FOR GOOD. The best engine of a set (20 hits out of 20)
#   is lost after 82 probes in 14 hours: 82 refusals for too many requests, and
#   changing transport does not bring it back. That is not a test one can pass but
#   the reputation of an address. Keeping such an engine at the bottom of the list
#   looks free and is not: when the first engines do not fill the result set,
#   search reaches it and knocks again, prolonging exactly what is being ended.
#
# · A VERDICT NEEDS A REFERENCE, NOT AN OPINION. By result counts an engine that
#   answers questions about people with stock quotes looks healthy. Only a
#   reference — did it find the known-correct answer — separates the two, and it
#   reverses verdicts in both directions.
#
# · THE SPLIT IS NOT BINARY. On two references engines look strictly either good
#   or hopeless; on three, several hit once out of three. Cutting by pool
#   composition still holds; "always or never" is an artefact of a small sample.
_pool_cache: tuple[float, dict] = (0.0, {})
_pool_lock = threading.Lock()
POOL_CACHE_S = 60.0


_disabled_cache: tuple[float, set] = (0.0, set())


def disabled_in_image() -> list[str]:
    """Which engines the metasearch ships switched off (`disabled: true`).

    A FACT, NOT A VERDICT, and that is verified rather than assumed. A live
    instance ships FORTY-SEVEN engines disabled in the general category, among
    them engines with reference hit rates of 0.98 and 0.91 — both in the computed
    pool and both excellent. So the flag means "not enabled by default", and the
    reasons vary: traffic, regional scope, terms of use.

    It is shown beside the hit rate because together they raise a question for the
    reader: "this engine is in the pool while the vendor does not enable it by
    default — do we know why?" Only observation excludes, never the flag.
    """
    global _disabled_cache
    now = time.time()
    when, what = _disabled_cache
    if what and now - when < 3600:
        return sorted(what)
    try:
        with urllib.request.urlopen(SEARXNG_URL + "/config", timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        what = {str(e["name"]) for e in d.get("engines") or []
               if e.get("name") and e.get("enabled") is False}
        _disabled_cache = (now, what)
    except Exception:  # noqa: BLE001
        return []      # we do not know — we stay silent rather than invent
    return sorted(what)


def pool_now_for(category: str) -> dict:
    """The pool for one category. The cache is shared, keyed by category."""
    global _pool_cache
    now = time.time()
    with _pool_lock:
        when, what = _pool_cache
        if isinstance(what, dict) and category in what and now - when < POOL_CACHE_S:
            return what[category]
    result = pool.read_pool(PROBE_DB, category)
    with _pool_lock:
        when, what = _pool_cache
        map_of = dict(what) if isinstance(what, dict) and now - when < POOL_CACHE_S else {}
        map_of[category] = result
        _pool_cache = (now, map_of)
    return result


def pool_now() -> dict:
    """Whom to ask. Read from observation, cached for a minute.

    Failure direction: if the database is missing or the pool has never been
    computed, we return the SEED and SAY SO in the `pool_source` field. "Not
    computed" is not the same as "computed": substituting the seed silently would
    look like ordinary work.
    """
    return pool_now_for("general")


# The line between core and reserve engines. The first CORE_TIER_N places are the
# ones search almost always reaches; the rest join in when the results fall short.
CORE_TIER_N = 5

# How many engines are asked in corroboration mode. Three is the minimum at which
# agreement means anything.
#
# A MEASURED CAVEAT, STILL OPEN: three NAMES are not three independent witnesses.
# Several engines share one index, and of the links corroborated by three engines
# a quarter are corroborated by ONE index counted three times. The pool now holds
# a per-family cap, which reduces the harm without curing the count: counting by
# family properly belongs to ag.search/2, together with a new field.
CORROB_ENGINES = 3
# HOW MANY RECENT OBSERVATIONS DECIDE THE SUBJECT-SUBSTITUTION VERDICT. A count,
# not a period: a period sees a breakage instantly but sees a repair only once the
# old observations age out. Ten was chosen from the distribution of observations,
# not by eye (the argument sits by the query itself, below).
TRUST_LAST_N = int(os.environ.get("TRUST_LAST_N") or "10")
# The ceiling on reads inside a search. Reading the whole result set is not
# allowed: fifty pages means minutes and tens of megabytes — the price of a deep
# search without its benefit.
SEARCH_READ_MAX = 8


def order() -> tuple[str, ...]:
    """The current engine order."""
    return tuple(pool_now()["pool"])


def core() -> tuple[str, ...]:
    return order()[:CORE_TIER_N]

# At most one request per this interval PER ENGINE. The metasearch has no rate
# regulator at all (verified in its code: neither delays nor a per-unit-time limit
# on the outbound path), so we keep one here. The default is conservative; raise
# it only on a measurement, never on a feeling.
ENGINE_INTERVAL_S = float(os.environ.get("ENGINE_MIN_INTERVAL_S") or "1.0")
# After a refusal an engine is left alone for this long — on top of the
# metasearch's own suspension. The point is not to keep hammering somebody who has
# just thrown us out.
COOLDOWN_S = float(os.environ.get("ENGINE_COOLDOWN_S") or "120.0")
# AN EMPTY RESULT SET COOLS BRIEFLY, NOT AS HARSHLY AS A REFUSAL.
#
# They are different events. A refusal or a substitution says something ABOUT THE
# ENGINE: it is broken or it lies. An empty result set says something ABOUT THE
# QUERY: we asked for something rare and nobody found it. Cooling on emptiness
# punishes the pool for the rarity of the question — and blinds us on exactly the
# questions deep search exists for, since it makes up to eight queries across
# three engines each, and the first rare phrasing would take the pool out for two
# minutes.
#
# Not cooling at all is wrong too: an engine that answers everything with emptiness
# would otherwise be asked forever. Hence a short pause — it lets the engine
# through on the next pass but stops a loop inside one.
COOLDOWN_EMPTY_S = float(os.environ.get("ENGINE_COOLDOWN_EMPTY_S") or "5.0")
# How long a caller waits in the queue before getting an honest refusal. Waiting
# out the whole interval is not allowed: at a 60 s interval the caller would hang
# for a minute, and that is worse than a refusal — a refusal it handles, a hang it
# takes for a broken network. This is a promise rather than a safety margin: at a
# throughput of ~7 searches per second, ten seconds of queue holds about seventy
# requests.
ENGINE_WAIT_MAX_S = float(os.environ.get("ENGINE_MAX_WAIT_S") or "10.0")

_rate_lock = threading.Lock()
_last_asked: dict[str, float] = {}     # when each engine was last asked
_cooling: dict[str, float] = {}      # until when each engine is left alone


def _may_ask(engine: str, now: float) -> bool:
    """Has the interval passed, and is the engine not cooling after a refusal."""
    if _cooling.get(engine, 0.0) > now:
        return False
    return now - _last_asked.get(engine, 0.0) >= ENGINE_INTERVAL_S


def _mark_refusal(engine: str, now: float, cooldown: float = 0.0) -> None:
    """Send an engine to cool down. The caller names the pause: a refusal and an
    empty result set get different ones, and they must not be one number."""
    _cooling[engine] = now + (cooldown or COOLDOWN_S)


def _reset_rate() -> None:
    """Forget the request history. Needed by the tests: otherwise the rate limiter
    makes them order-dependent — a second run in a row skips every engine."""
    with _rate_lock:
        _last_asked.clear()
        _cooling.clear()


def _refusal(reason: str, **extra) -> dict:
    """A refusal answer with the FULL set of engine fields.

    The contract declares three fields mandatory in every answer. Omitting them on
    the ok: false path would force the consumer to branch on the shape of the
    answer, and it would fall over doing that one day. Empty lists are information
    too."""
    return {"contract": CONTRACT, "ok": False, "error": reason,
            "count": 0, "results": [],
            "unresponsive_engines": [], "engines_asked": [],
            "engines_answered": [], "engines_irrelevant": [],
            "engines_skipped": [], "engines_used": 0, "tiers_used": [],
            "engines_trust": {}, "all_engines_clean": False,
            "pool_source": "", "pool_reason": "", "arguments_adjusted": [],
            "search_aborted": "", "engines_unasked": [],
            **extra}

_registry: set[str] | None = None
_registry_lock = threading.Lock()
_registry_at = 0.0
# HOW LONG A FAILED REGISTRY READ IS REMEMBERED. One failed `/config` at startup
# recorded as an empty registry forever would disable name checking for the life
# of the process — and disable it silently, because with an empty registry the
# check passes names through as they are. That is the class this module hunts
# everywhere: a guard that switches itself off.
#
# Success is cached without expiry (the engine set changes only when the
# metasearch container restarts); failure is cached for a minute.
REGISTRY_RETRY_S = float(os.environ.get("REGISTRY_RETRY_S") or "60.0")


def _known_engines() -> set[str]:
    """The engine names the metasearch actually knows. Success is remembered
    forever, failure for a minute.

    THIS IS NOT COSMETIC. Send a name that does not exist in `engines=` and the
    metasearch does NOT answer with an error — it silently falls back to the whole
    general category. One query with a typo returned 63 results from five engines,
    including engines nobody had measured, and neither the answer nor
    `unresponsive_engines` shows it. So names are checked BEFORE being sent.

    It is also the only honest source of truth about the set: `disabled: true` in
    the settings does NOT protect against being asked — a disabled engine asked
    explicitly returned 9 results. The engine list is set by whoever calls, not by
    the settings.
    """
    global _registry, _registry_at
    with _registry_lock:
        # A non-empty registry is kept; an empty one only until the retry expires.
        if _registry:
            return _registry
        if (_registry is not None
                and time.time() - _registry_at < REGISTRY_RETRY_S):
            return _registry
        try:
            with urllib.request.urlopen(SEARXNG_URL + "/config", timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            _registry = {str(e.get("name")) for e in (data.get("engines") or []) if e.get("name")}
        except Exception as exc:  # noqa: BLE001
            print(f"[registry] not read ({type(exc).__name__}: {exc}); "
                  f"retry in {REGISTRY_RETRY_S:.0f} s, until then engine-name "
                  "checking does NOT work", flush=True)
            _registry = set()
        _registry_at = time.time()
        return _registry


def _split_known(engine_names: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split into names the metasearch knows and names it does not. Unknown names
    are NOT sent."""
    known = _known_engines()
    if not known:          # registry unread — we do not invent, we send as is
        return list(engine_names), []
    ok = [e for e in engine_names if e in known]
    missing = [e for e in engine_names if e not in known]
    if missing:
        print(f"[registry] unknown engines NOT sent: {missing} "
              f"(otherwise the metasearch would silently query the whole category)",
              flush=True)
    return ok, missing


import re as _re

_CYR = _re.compile(r"[а-яё]", _re.I)


def _domain(url: str) -> str:
    """Domain without www. Kept here so no consumer has to parse it itself."""
    try:
        d = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return d.split(":")[0].removeprefix("www.")


# Some engines return THEIR OWN REDIRECT ADDRESS instead of the target: the real
# link is hidden in a query parameter, e.g.
#
#   https://go.example.com?id=678&xs=1&url=https%3A%2F%2Fww...
#
# The reader is handed such an address and honestly returns zero characters. The
# failure looks like "the page is empty" while in fact the address is unusable —
# and it came FROM THE ENGINE. Our "canonical domain in the results" reference
# waves this through: a domain is formally present, it is merely the wrong one.
_REDIRECT_PARAMS = ("url", "u", "target", "redirect", "to", "link")


def _unwrap_redirect(url: str) -> str:
    """Extract the target address from a redirect wrapper; pass anything else
    through unchanged.

    We unwrap rather than reject: behind the wrapper is a real page, and throwing
    it away would lose a finding over the shape of a link.
    """
    if not url:
        return url
    try:
        pth = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(pth.query)
    except Exception:  # noqa: BLE001
        return url
    for name in _REDIRECT_PARAMS:
        for value in params.get(name, []):
            target = urllib.parse.unquote(value)
            # The target must be a FULL address on another domain: a relative
            # path or the same domain is an ordinary query parameter, not a
            # redirect.
            if target.startswith(("http://", "https://")) and _domain(target) != _domain(url):
                return target
    return url


def _relevant(query: str, items: list[dict]) -> bool:
    """Does this look like a result set for OUR query specifically.

    Measured repeatedly: an engine asked about a named person returned pages about
    game mechanics in Spanish; asked for a company's tax number it returned "How to
    get help in Windows"; asked about another person it returned a speed-test site.
    The page is laid out as an ordinary result set throughout. This kind of failure
    is more dangerous than a block: a block is visible, this looks like a result.

    The check is deliberately WEAK — one match on a significant word is enough. Its
    job is to tell somebody else's result set from ours, not to judge quality.
    Words are compared by their first five characters, so that inflected forms
    still match. A strict check would cut legitimate results where a company is
    named by an abbreviation — and a good result thrown away silently is no better
    than rubbish let through silently.
    """
    if not items:
        return False
    words = [w for w in _re.split(r"[^\w]+", query.lower()) if len(w) >= 4]
    if not words:
        return True
    stems = {w[:5] for w in words}
    for r in items:
        hay = (r.get("title", "") + " " + r.get("snippet", "") + " "
               + urllib.parse.unquote(r.get("url", ""))).lower()
        if any(st in hay for st in stems):
            return True
    return False


def _drop_hijacked(query: str, items: list[dict]) -> tuple[list[dict], list[str]]:
    """Discard the results of engines that answered a different question.

    THE GRANULARITY HERE MATTERS. Asking providers ONE AT A TIME lets you check
    each answer as a whole. We ask three engines AT ONCE and get their results
    interleaved: a check of the form "is there any match in this batch" passes on
    the strength of the good engine's answers, and the bad engine's rubbish rides
    along. So each engine is judged by ITS OWN results.

    A result is discarded only if EVERY engine that returned it is judged to have
    substituted the subject: a link found by both a good and a bad engine stays —
    the one that answered on topic vouches for it.
    """
    per_engine_rows: dict[str, list[dict]] = {}
    for r in items:
        for e in (x.strip() for x in r.get("via", "").split(",")):
            if e:
                per_engine_rows.setdefault(e, []).append(r)
    hijacked = sorted(e for e, grp_n in per_engine_rows.items() if not _relevant(query, grp_n))
    if not hijacked:
        return items, []
    kept = [r for r in items
                if any(x.strip() and x.strip() not in hijacked
                       for x in r.get("via", "").split(","))]
    return kept, hijacked


def _clean(s: str) -> str:
    return " ".join(str(s or "").split())


def _round(query: str, engine_names: list[str], n: int, page: int,
           category: str = "general") -> dict:
    """One round trip to the metasearch with a given set of engines. Never raises.

    THE CATEGORY IS NOT PUT INTO THE REQUEST, AND THAT IS A MEASUREMENT RATHER
    THAN A PREFERENCE. The same query, three ways:

        categories=images + engines=<one>  -> 213 results, 6 engines
        engines=<one> only                 ->  35 results, 1 engine
        categories=images only             -> 213 results, 6 engines

    With both parameters present THE ENGINE LIST IS IGNORED and the whole category
    is queried. Our "one engine at a time" order then turns into a fan-out across
    forty-nine engines — precisely what this module exists to avoid. And that
    fan-out includes engines of a family we have already lost on address reputation
    and no longer touch.

    It cannot be spotted from the answer: there are MORE results, so from outside
    a fan-out looks like luck. Hence the rule: the category is set by the ENGINE
    NAME and by nothing else. The `category` parameter stays here — it says how to
    parse the result — but it does not go into the request.
    """
    params = urllib.parse.urlencode({
        "q": query, "format": "json", "pageno": page + 1,
        "language": "ru" if _CYR.search(query) else "en",
        "safesearch": "0",
        "engines": ",".join(engine_names),
    })
    try:
        req = urllib.request.Request(
            SEARXNG_URL + "/search?" + params,
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read(4_000_000)
    except Exception as exc:  # noqa: BLE001 — unavailability must not kill us
        return {"error": f"metasearch unavailable: {type(exc).__name__}: {exc}"[:200]}
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {"error": "the metasearch answered with something other than JSON "
                          "(is the json format enabled in its settings?)"}

    out = []
    for it in data.get("results") or []:
        url = _unwrap_redirect(str(it.get("url") or ""))
        if not url.startswith(("http://", "https://")):
            continue
        # Which engines produced a link goes outward: it shows what the instance
        # is still alive on and what it is not. Without it degradation just looks
        # like "things got worse".
        engines = ", ".join(str(x) for x in (it.get("engines") or [])[:3])
        record = {"title": _clean(it.get("title"))[:300], "url": url,
                  "snippet": _clean(it.get("content"))[:300],
                  "via": engines or "searxng"}
        if category == "images":
            # AN IMAGE HAS TWO ADDRESSES AND THEY MUST NOT BE CONFUSED. `url` is
            # the page the image was found on; `img_src` is the file itself. Both
            # go out under distinct names: a consumer that shows the page instead
            # of the image (or the reverse) fails silently, and the difference is
            # visible only in the field name.
            record["image_url"] = str(it.get("img_src") or "")
            record["thumbnail"] = str(it.get("thumbnail") or "")
            record["page_url"] = url
            record["author"] = _clean(it.get("author"))[:200]
            record["published"] = str(it.get("publishedDate") or "")[:64]
            if not record["image_url"]:
                continue     # a result with no image file is useless as an image
        out.append(record)
    return {"results": out, "unresponsive": data.get("unresponsive_engines") or []}


def search(query: str, n: int = 6, page: int = 0, corroborate: bool = False,
           min_engines: int = 0, per_engine: int = 0) -> dict:
    """One search. Never raises: returns {ok, results|error}.

    THE WIDTH OF THE SWEEP IS ONE KNOB, NOT THREE. `min_engines` says how many
    engines to ask REGARDLESS of how many links have already been collected, and
    `corroborate: true` is a special case of it — exactly CORROB_ENGINES. A second
    independent switch beside it was not allowed: two knobs on one property drift
    apart at the first edit. `per_engine` — how many links to take from EACH — is
    part of the same width.

    CORROBORATION is the one case where we do ask several engines: instead of
    stopping at enough results we query several in a row, so that the `via` field
    names more than one. A link found by two engines independently is sounder than
    one found by a single engine. THE PRICE IS HONEST AND HIGH: five times the
    outbound requests. Call it only for a SPECIFIC source carrying a numeric or
    contested claim — two or three per dossier, never for a whole batch. The share
    of such calls is visible in GET /stats: above a tenth, the mode is being called
    as a fan-out, and that is the caller's defect rather than the module's.

    ONE ENGINE AT A TIME, not a fan-out. We ask the first in order, and stop if we
    have enough. If not enough, or it refused, the next one goes. That IS the
    switch to another engine on failure: not a separate mechanism but a property
    of the order.

    WHY NOT A FAN-OUT. The same query sent to five engines costs five requests to
    other people's services for result sets that overlap by ~80%. Measured: at
    forty concurrent searches three engines out of five refused, while our own
    machine was busy on 6% of one core. A fan-out brought somebody else's limit
    five times closer while giving nothing back on an ordinary query.

    The price of dropping the fan-out is honest: cross-corroboration disappears
    (the `via` field with several names) and some rare findings that only one
    engine had are lost. That is what we pay for the engines continuing to serve
    us; when results fall short we go to the next engine anyway.

    RATE. At most one request per ENGINE_INTERVAL_S per ENGINE. The metasearch has
    no regulator of its own, so we keep one here. An engine that may not be asked
    right now is simply skipped — waiting is not allowed, as it would turn a pause
    for the engine into a delay for the caller.

    An empty result set is NOT an error. It is an honest "nothing found", and it
    must be distinguishable from "the module is broken".
    """
    query = _clean(query)
    if not query:
        return _refusal("the q parameter is required")
    # A COERCED ARGUMENT IS NAMED, NOT SILENTLY SUBSTITUTED. Clamping rubbish to a
    # default keeps the call alive, which is right; doing it in silence means the
    # caller asked for one thing, got another, and has nothing in the answer that
    # says so. `n=abc` used to come back as an ordinary answer over six results.
    adjusted: list[str] = []

    def _num(value, low, high, default, name):
        try:
            out = max(low, min(high, int(value)))
        except (TypeError, ValueError):
            adjusted.append(f"{name}={value!r} is not a number, using {default}")
            return default
        if str(value).strip() not in ("", str(out)):
            adjusted.append(f"{name}={value!r} clamped to {out}")
        return out

    n = _num(n, 1, 50, 6, "n")
    page = _num(page, 0, 10_000, 0, "page")
    # WIDTH. `corroborate` IS REDUCED to the number rather than living beside it:
    # otherwise there would be two answers to "how many to ask", and they would
    # drift apart silently.
    min_engines_n = _num(min_engines or 0, 0, 8, 0, "min_engines")
    if not min_engines_n and corroborate:
        min_engines_n = CORROB_ENGINES
    per_engine_n = _num(per_engine or 0, 0, 50, 0, "per_engine")

    pool_state = pool_now()
    known, unknown = _split_known(tuple(pool_state["pool"]))
    trust = _trust()
    asked: list[str] = []
    skipped: list[str] = []
    silent: list = []
    hijacked: list[str] = []
    collected: list[dict] = []
    seen: dict[str, dict] = {}
    # Who led us to this SITE, kept apart from who led us to this page. The
    # distinction is mandatory: engines often return different pages of one site,
    # and corroboration by exact address then reports zero although every engine
    # found the site.
    domain_witnesses: dict[str, set] = {}
    net_error = None

    # THE QUEUE. When no engine may be asked right now we WAIT for one to free up
    # instead of refusing. Under a "check once, sleep, refuse" design, 30
    # concurrent requests give 11 successes and 19 refusals while only 12 requests
    # actually leave for the engines: the engines are protected and the callers
    # punished for nothing, when a throughput of ~7 searches per second would serve
    # all 30 in five seconds.
    #
    # The wait is INSIDE the outer loop, not before it. Waiting once before the
    # sweep lets a thread whose engines were taken by neighbours WHILE it walked
    # the list reach the end with nothing and refuse, although there was still
    # time.
    #
    # An honest caveat: this is NOT a fair queue. There is no service order,
    # waiters compete for whichever engine frees up, and someone may be unlucky
    # several times in a row. A real FIFO becomes worthwhile when a consumer
    # appears that cares about order; there is none today, and the complexity of a
    # priority queue would be paid for always.
    deadline_at = time.time() + ENGINE_WAIT_MAX_S
    while True:
        for engine in known:
            # In the ordinary mode we stop as soon as we have enough. In
            # corroboration mode we ask the given number of engines regardless of
            # how much has been collected: the point is independent witnesses.
            if not min_engines_n and len(collected) >= n:
                break
            # A width was given — we go wide UP TO that number of engines,
            # however many links we already have.
            if min_engines_n and len(asked) >= min_engines_n:
                break
            now = time.time()
            with _rate_lock:
                if not _may_ask(engine, now):
                    skipped.append(engine)
                    continue
                _last_asked[engine] = now
            # How many to ask THIS engine for: the explicit number, or however
            # many are still missing. With a width set, "how many are missing" will
            # not do — having filled up on the first engine we would ask the second
            # for zero.
            how_many = per_engine_n or (n if min_engines_n else n - len(collected))
            r = _round(query, [engine], max(1, how_many), page)
            if "error" in r:
                # The metasearch itself is unavailable — the next engine will not
                # help, it is behind the same door. Telling this apart from an
                # engine refusal is mandatory.
                net_error = r["error"]
                break
            asked.append(engine)
            silent += r["unresponsive"]
            batch, hijacked_here = _drop_hijacked(query, r["results"])
            hijacked += hijacked_here
            # A REFUSAL AND A SUBSTITUTION ARE ABOUT THE ENGINE, EMPTINESS IS
            # ABOUT THE QUERY, and they cool differently. Heaping all three
            # together and cooling for two minutes takes the pool out on the first
            # rare phrasing of a deep search.
            if r["unresponsive"] or hijacked_here:
                with _rate_lock:
                    _mark_refusal(engine, now)
            elif not batch:
                with _rate_lock:
                    _mark_refusal(engine, now, COOLDOWN_EMPTY_S)
            for item in batch:
                # The domain is computed once and returned: consumers need it more
                # often than the full address, and parsing it in every consumer is
                # a way to end up with five different notions of what a domain is.
                item.setdefault("domain", _domain(item["url"]))
                for_domain = domain_witnesses.setdefault(item["domain"], set())
                for_domain.update(e.strip() for e in item["via"].split(",") if e.strip())
                prev = seen.get(item["url"])
                if prev is not None:
                    # The same link from another engine is not a duplicate but
                    # CORROBORATION. The name is appended to `via`, otherwise the
                    # independent agreement is lost exactly where it is the only
                    # thing of value.
                    for e in item["via"].split(","):
                        e = e.strip()
                        if e and e not in prev["via"]:
                            prev["via"] += ", " + e
                    continue
                seen[item["url"]] = item
                collected.append(item)


        if asked or net_error or time.time() >= deadline_at:
            break
        # Nothing was obtained but there is still time — wait and go round again.
        skipped.clear()
        time.sleep(0.05)

    # NOTHING COLLECTED AND THE NETWORK DOWN IS A REFUSAL OUTRIGHT.
    if not collected and net_error:
        return _refusal(net_error)
    # THE PARTIAL CASE MUST NOT VANISH SILENTLY. The metasearch can die MID-SWEEP:
    # two engines answer, the third meets a dead door, the network error is
    # recorded — and then discarded because something was collected.
    #
    # What goes out is `ok: true` with fewer engines and NOT ONE WORD about the
    # cause. The caller sees a thin result set and puts it down to the rarity of
    # the query, while in fact half the search never happened. That is the class
    # this module hunts everywhere: a failure indistinguishable from a success.
    #
    # It stays a success — the links were obtained and must be returned — but the
    # circumstance IS NAMED. `engines_skipped` will not do for it: that means "not
    # asked because of the rate limit", this means "there was nobody left to ask".
    #
    # "Nobody was asked" is A REFUSAL, not an empty result set. The difference is
    # fundamental: an empty result set means we looked and found nothing.
    if not asked:
        return _refusal("no engines left: all of them are cooling after a refusal "
                      f"or did not fit the rate limit ({ENGINE_INTERVAL_S} s per "
                      "engine)",
                      engines_skipped=skipped)
    # CLEANLINESS IS COUNTED OVER THE ENGINES THAT ANSWERED, NOT THOSE THAT WERE
    # ASKED. A guard of the form `bool(asked) and all(... for e in answered)` looks
    # at whether anyone was asked, while `all([])` over an empty answered-list
    # stays true. Ask four engines, have all four stay silent, and "results come
    # from clean engines" comes out TRUE over results that do not exist.
    #
    # That is the defect the whole module is written against: no data means "not
    # checked", never "sound". The guard was standing where the failure was not.

    out = collected[:n]
    for item in out:
        by_url = [e.strip() for e in item["via"].split(",") if e.strip()]
        item["corroborated_by_url"] = len(set(by_url))
        item["corroborated_by_domain"] = len(domain_witnesses.get(item.get("domain", ""), ()))
    silent_seen: set[str] = set()
    silent = [m for m in silent
               if not (m[0] in silent_seen or silent_seen.add(m[0]))]
    answered = sorted({e.strip() for r in collected for e in r["via"].split(",") if e.strip()})
    quietly_empty = [e for e in asked
                  if e not in answered and e not in {m[0] for m in silent}
                  and e not in hijacked]
    print(f"[search] {query[:70]!r} -> {len(out)} results, "
          f"asked {len(asked)} of {len(known)}: {asked}", flush=True)
    if silent or quietly_empty or hijacked or unknown:
        print(f"[engines] query={query[:60]!r} silent={silent} "
              f"quietly_empty={quietly_empty} substituted={hijacked} "
              f"unknown={unknown}", flush=True)
    return {"contract": CONTRACT, "ok": True, "query": query, "page": page,
            "count": len(out), "results": out,
            # WHERE THE POOL CAME FROM, IN THE SEARCH ANSWER ITSELF. On a fresh
            # install the prober has not accumulated enough observations yet, and
            # the pool is the hand-written SEED for the first hours. The views
            # said so and the answer did not, so the module's main property —
            # "the pool is computed from observation" — was silently untrue at the
            # moment a new operator first tried it, with nothing in the answer to
            # show it.
            "pool_source": pool_state.get("source", "?"),
            "pool_reason": pool_state.get("reason", ""),
            # Empty when every argument arrived usable. Never absent: a consumer
            # must not have to branch on the shape of the answer.
            "arguments_adjusted": adjusted,
            # WHY THE SWEEP BROKE OFF, if it did. Empty means we walked the whole
            # pool or collected enough; non-empty means the metasearch stopped
            # answering mid-sweep and the results are INCOMPLETE by no decision of
            # ours.
            "search_aborted": net_error or "",
            # WHO WAS MISSED BECAUSE OF THE BREAK — AND ONLY THEM. "All known
            # minus asked" would sweep in the ones skipped BY THE RATE LIMIT, which
            # would not have been asked even without a break. That is the very
            # merging with `engines_skipped` this field exists to avoid, and the
            # double count turns "did not get to them" into "never asked at all".
            "engines_unasked": ([e for e in known
                                 if e not in asked and e not in skipped]
                                if net_error else []),
            "unresponsive_engines": silent,
            "engines_asked": asked,
            "engines_answered": answered,
            "engines_irrelevant": hijacked,
            # Who was not asked and why: either the rate limit or cooling after a
            # refusal. Without it "we asked one" is indistinguishable from "the
            # rest stayed silent".
            "engines_skipped": skipped,
            # Trust in EVERY participating engine, in the sense of subject
            # substitution: clean (n >= 3 on independently verified references with
            # no misses), candidate (no misses but fewer than three observations),
            # substitutes, unavailable (probes were made and all refused — news
            # about our address rather than about the engine), not checked (no
            # probes). This changes the consumer's behaviour: results from an
            # unchecked engine must be verified against features of the subject.
            "engines_trust": {e: trust.get(e, "not_checked") for e in asked},
            # A cheap flag to branch on: did all the results come from clean
            # engines. Without it every consumer would compute it themselves, and
            # differently in each place.
            "all_engines_clean": bool(answered) and all(
                trust.get(e) == "clean" for e in answered),
            # How many engines were needed. One is the healthy norm; a rise means
            # the first ones have stopped coping.
            "engines_used": len(asked),
            "tiers_used": (["core"] if all(e in core() for e in asked)
                           else ["core", "fallback"]) if asked else []}


# What the deep health check reads. A pair of (address, marker) rather than an
# address alone: a check that does not know the right answer proves only that an
# answer arrived — and a shield arrives too. Configurable so that tests can check
# reading without going outside.
DEEP_READ_REFERENCE = ((os.environ.get("DEEP_READ_URL") or "https://example.com"),
                   (os.environ.get("DEEP_READ_EXPECT") or "Example Domain"))


def _count_mcp_call(body, answer) -> None:
    """Record ONE MCP call in the counters. Shared by the single and batch paths.

    It lives here because a JSON-RPC BATCH otherwise bypasses accounting entirely:
    an array of calls performs real searches while the counters do not move, not
    even the total. While accounting lived inside the single-body handler, the
    second entrance was easy to miss — and was missed.
    """
    if not isinstance(body, dict):
        _count_call("/mcp:not-an-object", error=True)
        return
    method = str(body.get("method") or "")
    corrob = False
    tool_name = ""
    args: dict = {}
    if method == "tools/call":
        # SOMEBODY ELSE'S BODY CAN BE ANYTHING, AND IT IS NOT VALIDATED YET HERE:
        # accounting runs beside rpc(), where the shape checks live, not after
        # them. `params` arriving as a list gives `.get` on a list — a dropped
        # connection with no answer.
        params = body.get("params")
        params = params if isinstance(params, dict) else {}
        args = params.get("arguments")
        args = args if isinstance(args, dict) else {}
        tool_name_raw = params.get("name")
        tool_name_raw = tool_name_raw if isinstance(tool_name_raw, str) else ""
        # THE NAME USED FOR ACCOUNTING COMES ONLY FROM THE KNOWN SET. Building the
        # key from an unvalidated name makes the dictionary grow without bound: a
        # client with typos grows the process memory, a fuzzer grows it fast, and
        # all of it goes out in /stats.
        tool_name = (tool_name_raw if tool_name_raw in _TOOL_NAMES
                           else ("unknown" if tool_name_raw else ""))
        corrob = bool(args.get("corroborate", False)) or bool(
            args.get("min_engines", 0))
    # WHAT WAS CALLED, not "a tool was called". Counting EVERY tools/call as a
    # search puts reads and screenshots made over MCP into the search counter while
    # the read counter stays at zero.
    #
    # THE LISTS ARE DERIVED FROM THE REGISTRY rather than written out as tuples:
    # written-out ones fall behind a sixth tool silently — it lands in neither
    # searches nor reads, and no static test catches that.
    is_search = tool_name in _SEARCHING_TOOLS
    is_reading = tool_name in _READING_TOOLS
    if tool_name == "web_search" and args.get("read", True):
        is_reading = True
    # A TOOL ERROR IS AN ERROR TOO. Counting only JSON-RPC errors means a dead
    # metasearch does not move the counter over MCP while it does over the other
    # door: two doors to one tool disagreeing in their numbers.
    error = bool(isinstance(answer, dict) and answer.get("error"))
    if not error and isinstance(answer, dict):
        result = (answer.get("result") or {})
        if isinstance(result, dict) and result.get("isError"):
            error = True
    _count_call(f"/mcp:{method or 'no-method'}"
              + (f":{tool_name}" if tool_name else ""),
              search=is_search, reading=is_reading,
              error=error, corroborated=corrob)


def _degraded_paths(paths: dict[str, str]) -> list[str]:
    """Which DECLARED paths are not answering.

    "NOT WIRED UP" IS NOT A DEGRADATION, and that is the point here. Counting
    everything that does not begin with "alive" as degraded makes the field shout
    `['browser']` permanently on a perfectly normal configuration without the
    sidecar. A path that was never declared cannot degrade; and a guard that shouts
    at a healthy system gets switched off entirely.
    """
    degraded = []
    for name, state in paths.items():
        c = str(state)
        if c.startswith("alive") or c.startswith("not_wired_up"):
            continue
        degraded.append(name)
    return degraded


def health(deep: bool = False) -> tuple[bool, dict]:
    """The health of the CAPABILITY, not of the process.

    A live adapter in front of a dead metasearch must be unhealthy: a health check
    that answers for itself rather than for the thing it exists to provide will
    lie sooner or later — a green gate over a job that never started can hide an
    outage for days. So /healthz here goes to the metasearch instead of answering
    "ok" merely because the process is alive.

    The shallow check is enough for restarts, but it does NOT prove the engines
    answer: a metasearch can be green while every one of its engines refuses.
    Hence `deep` — a real search. It pays with a request to the outside world and
    is deliberately not used as the container health check.
    """
    # THE PATH STATE IS TAKEN BEFORE ANYTHING ELSE, so that it reaches EVERY
    # branch of the answer. Living in one branch of three means that with a dead
    # metasearch the answer carries neither it nor the reading state at all. A
    # missing key is not an empty list — that is the very distinction the comment
    # below calls decisive.
    read_paths = reader.paths()
    degraded = _degraded_paths(read_paths)
    # WHAT IS CONFIGURED, NOT WHAT IS INTENDED. The manifest sends the operator
    # here for the state of the model, so the state has to BE here. Two
    # capabilities out of five depend on it, and they depend on DIFFERENT halves:
    # deep search needs the text model, scan recognition needs the vision one. A
    # single "the key is set" would let an operator with no vision model believe
    # recognition works — and find out from an answer that never recognises
    # anything.
    text_ok, text_why = model.available()
    vision_ok, vision_why = model.available(vision=True)
    model_state = {"text": text_ok, "vision": vision_ok,
                   "text_reason": "" if text_ok else text_why,
                   "vision_reason": "" if vision_ok else vision_why}
    try:
        with urllib.request.urlopen(SEARXNG_URL + "/healthz", timeout=10) as r:
            ok = r.status == 200 and r.read(64).strip() == b"OK"
    except Exception as exc:  # noqa: BLE001
        return False, {"ok": False, "searxng": "unavailable",
                       "reading": read_paths, "degraded_paths": degraded,
                       "model": model_state,
                       "error": f"{type(exc).__name__}: {exc}"[:200]}
    if not ok:
        return False, {"ok": False, "searxng": "answered something other than OK",
                       "reading": read_paths, "degraded_paths": degraded,
                       "model": model_state}
    # READING IS A CAPABILITY TOO, and its paths must be visible here.
    #
    # A DEAD BROWSER MUST NOT DRAG `ok` DOWN, and that is a decision rather than an
    # oversight. This view is read by the container health check, which decides
    # whether to RESTART THE ADAPTER. Restarting the adapter does not revive
    # somebody else's sidecar: we would take search and ordinary reading down over
    # the death of a third capability.
    #
    # So `ok` answers "is the module working", while dead DECLARED paths are named
    # in a field of their own — visible, but not fatal.
    #
    # THE STATE COMES FROM THE CACHE HERE, AND THAT IS SAID PLAINLY. The container
    # health check calls this every thirty seconds and a state no older than one
    # tick suffices for it. What bypasses the cache is `/pages` — the view for a
    # person who needs the truth NOW.
    if not deep:
        return True, {"ok": True, "searxng": "OK", "reading": read_paths,
                      "model": model_state,
                      # An empty list is not decoration: it tells "checked, all
                      # alive" apart from "not checked".
                      "degraded_paths": degraded,
                      "note": ("shallow check: it does not prove the engines "
                               "answer. ok=true means the module is working; a "
                               "dead declared path is shown in degraded_paths and "
                               "deliberately does not drag ok down — restarting "
                               "the adapter does not cure somebody else's sidecar")}
    res = search("test", n=1)
    if not res.get("ok"):
        return False, {"ok": False, "searxng": "OK", "reading": read_paths,
                       "degraded_paths": degraded, "model": model_state,
                       "deep": res.get("error")}
    # The deep read check goes to a reference, not to "some page or other": a
    # probe returning zero must first show that its subject existed.
    rd = reader.read([DEEP_READ_REFERENCE[0]], max_chars=2000,
                     expect=[DEEP_READ_REFERENCE[1]], fresh=True)
    page_obj = (rd.get("results") or [{}])[0]
    readable = page_obj.get("expected_found") is True
    return readable, {"ok": readable, "searxng": "OK",
                    "deep_count": res.get("count", 0), "model": model_state,
                    "reading": read_paths, "degraded_paths": degraded,
                    "deep_read": page_obj.get("status", "no answer"),
                    "deep_read_reference": page_obj.get("expected_found")}


# THE REFERENCES LIVE IN ONE PLACE — `refs.py`. A tuple of the same strings here,
# beside the prober's list, would be two copies of one thing drifting apart
# silently.
#
# Why not all of them count: the subject-substitution verdict rests only on
# references whose domain was confirmed OUTSIDE the engines. Otherwise "hitting the
# reference" would partly mean agreeing with the majority of the engines that the
# reference was checked against. The `confirmed` flag lives beside the reference
# itself.
CONFIRMED_REFERENCES = refs.CONFIRMED
_trust_cache: tuple[float, dict] = (0.0, {})
# OVER WHAT PERIOD EACH VERDICT WAS OBTAINED, per engine. Kept apart from the
# verdicts themselves so as not to change the shape of what five places read: N
# observations span DIFFERENT periods for different engines, and without this
# number a reader of the view will assume they are comparing like with like.
_trust_spans: dict[str, dict] = {}
_trust_lock = threading.Lock()


def _trust() -> dict[str, str]:
    """How far an engine can be trusted in the sense of SUBJECT SUBSTITUTION.

    An engine can answer briskly and on the words of the query while being about a
    different subject: asked for a bank's official site, engines returned a
    cryptocurrency exchange, an online casino and a food-delivery service. All
    formally relevant. This is caught only by a reference — a comparison with a
    known-correct answer — and in the probe data the property is close to BINARY:
    an engine either never substitutes or never hits.

    It goes outward because it changes the consumer's behaviour: results from an
    unchecked engine must be re-verified against features of the subject, results
    from a clean one need not be. Without the label such verification is either
    always on (expensive and full of false alarms) or never on.

    Cached for 5 minutes: the prober adds one probe per engine per 10 minutes,
    there is nothing to recompute more often, and going to the database on every
    search would pay in latency for nothing.
    """
    global _trust_cache
    now = time.time()
    with _trust_lock:
        when, cached = _trust_cache
        # AN EMPTY RESULT IS CACHED TOO. A condition of the form `if cached and
        # ...` makes every search reopen sqlite when the database is unavailable.
        # An empty dictionary is a legitimate answer ("we do not know"), not the
        # absence of one, and there is nothing to recompute.
        if when and now - when < 300:
            return cached
    result: dict[str, str] = {}
    spans: dict[str, dict] = {}
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True, timeout=5)
        spots = ",".join("?" * len(CONFIRMED_REFERENCES))
        # THE VERDICT COMES FROM THE LAST N OBSERVATIONS, NOT FROM A PERIOD.
        # Judging over the whole probe history keeps an engine that substituted a
        # subject once, weeks ago, marked as a substituter forever. A thirty-day
        # window sees a breakage instantly and a repair a month late: an engine
        # stays condemned however many clean hits it collects afterwards.
        #
        # Freshness also reconciles trust with THE POOL, which already works this
        # way: two mechanisms judging the same engines by different rules will
        # sooner or later say opposite things about one engine, with nothing to
        # explain it.
        #
        # TEN AND NOT MORE. Of 41 engines with observations on confirmed
        # references, only five have fewer than ten — and those answered once and
        # died. At N=20 nine engines are left with no verdict at all, at N=50
        # nineteen — half of them. What decides is the DISTRIBUTION, not an
        # average: reserve engines are probed less often than core ones BY
        # CONSTRUCTION, so an average hides exactly the engines the number
        # threatens.
        for name, n, hits, first, last in conn.execute(
                f"SELECT engine, COUNT(*), SUM(hit), MIN(ts), MAX(ts) FROM ("
                f"  SELECT engine, hit, ts, ROW_NUMBER() OVER ("
                f"    PARTITION BY engine ORDER BY ts DESC) AS rn FROM probes"
                f"  WHERE hit IS NOT NULL AND results > 0 AND relevant > 0"
                f"  AND query IN ({spots})"
                f") WHERE rn <= ? GROUP BY engine",
                (*CONFIRMED_REFERENCES, TRUST_LAST_N)):
            hits = hits or 0
            # OVER WHAT PERIOD THE VERDICT WAS OBTAINED, beside it. N
            # observations span DIFFERENT periods for different engines: a core
            # engine collects them in hours, a reserve one in a week. Without this
            # the reader assumes they are comparing like with like.
            hours = max(0.0, (last - first) / 3600.0)
            spans[name] = {"observations": n, "over_hours": round(hours, 1)}
            if hits < n:
                result[name] = "substitutes"      # answered about another subject at least once
            elif n >= 3:
                result[name] = "clean"           # enough observations
            else:
                result[name] = "candidate"       # no misses, but n < 3
        # "UNAVAILABLE" IS A SEPARATE VERDICT: probes were made and ALL of them
        # ended in a refusal or emptiness, so there is nothing to judge
        # substitution by. That is news about OUR ADDRESS, not about the engine:
        # if many engines fall into it at once, we were thrown out rather than the
        # engines going bad. Heaping it into "not checked" would lose the only
        # signal distinguishing "we did not ask it" from "it does not let us in".
        for (name,) in conn.execute(
                f"SELECT engine FROM probes WHERE query IN ({spots})"
                f" GROUP BY engine HAVING SUM(results > 0 AND relevant > 0) = 0",
                CONFIRMED_REFERENCES):
            result.setdefault(name, "unavailable")
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[trust] the prober database is unavailable: {type(exc).__name__}",
              flush=True)
    with _trust_lock:
        _trust_cache = (now, result)
        _trust_spans.clear()
        _trust_spans.update(spans)
    return result


def _engines_state(hours: int = 24, category: str = "general") -> tuple[int, dict]:
    """The health of EVERY engine from the prober's measurements, not from a list
    of ours.

    The point is that the engine set stops being hand-written. A list living in
    code goes stale silently: two measurements three weeks apart disagreed by half,
    and that was discovered by accident. Here the state comes from continuous
    probes.

    THE CATEGORY IS ASKED FOR, NOT MIXED IN. Adding up web and image probes in one
    heap mixes different weight classes: images have their own engine set, their
    own reference attribute (the domain of the FILE, not of the page) and their own
    ratios. Mixed together they give an average temperature: an engine excellent on
    the web and dead on images looks mediocre in both, and decisions taken from
    such a view are the wrong ones.
    """
    import sqlite3
    if not os.path.exists(PROBE_DB):
        return 503, {"ok": False,
                     "error": f"there is no prober database ({PROBE_DB}); it has never run",
                     "engines": []}
    try:
        conn = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True, timeout=10)
        since = int(time.time()) - hours * 3600
        # OVERLAP: what share of an engine's links anyone else also found for the
        # same query. The signal exists because the text check catches subject
        # substitution but does NOT catch spam that quotes the query back: a page
        # of word salad containing the query scores 10 out of 10 "on topic".
        # Nobody else finds such an address, while a real site is found
        # independently by several engines. Computed in Python rather than SQL: the
        # addresses are stored as a JSON list, and parsing them in a query would be
        # brittle.
        raw_rows = conn.execute(
            "SELECT engine, query, urls FROM probes WHERE ts >= ? AND urls IS NOT NULL"
            " AND COALESCE(category, 'general') = ?",
            (since, category)).fetchall()
        by_query: dict[str, dict[str, int]] = {}
        url_list: dict[tuple[str, str], list[str]] = {}
        for name, qry, raw in raw_rows:
            try:
                items = [u for u in json.loads(raw or "[]") if u]
            except Exception:  # noqa: BLE001
                continue
            url_list[(name, qry)] = items
            score = by_query.setdefault(qry, {})
            for u in set(items):
                score[u] = score.get(u, 0) + 1
        overlap: dict[str, tuple[int, int]] = {}
        for (name, qry), items in url_list.items():
            total = len(set(items))
            shared = sum(1 for u in set(items) if by_query[qry].get(u, 0) > 1)
            was = overlap.get(name, (0, 0))
            overlap[name] = (was[0] + shared, was[1] + total)

        rows = conn.execute("""
            SELECT engine,
                   COUNT(*)                                        AS probes,
                   SUM(verdict = 'ok')                             AS ok_n,
                   SUM(COALESCE(hit, 0))                           AS hits,
                   SUM(verdict = 'miss')                           AS misses,
                   SUM(verdict = 'refused')                        AS refusals,
                   SUM(verdict = 'empty')                          AS empties,
                   SUM(verdict = 'irrelevant')                     AS off_topic,
                   SUM(verdict = 'error')                          AS errors,
                   AVG(results)                                    AS links_avg,
                   MAX(ts)                                         AS last_ts,
                   CAST(AVG(latency_ms) AS INTEGER)                AS ms
            FROM probes WHERE ts >= ? AND COALESCE(category, 'general') = ?
            GROUP BY engine
            ORDER BY SUM(COALESCE(hit, 0)) * 1.0 / COUNT(*) DESC, links_avg DESC
        """, (since, category)).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return 503, {"ok": False, "error": f"the database is unavailable: {type(exc).__name__}: {exc}",
                     "engines": []}
    if not rows:
        # A ZERO IS EXPLAINED BY WHAT WAS CHECKED, NOT BY AN ASSUMPTION. One
        # reason for every zero — "the prober has only just started" — makes a
        # typo in the category name produce that same reason over a database with
        # thousands of probes. "There was nowhere to look" and "nothing was found"
        # then merge, under an invented cause.
        any_probes = 0
        try:
            c3 = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True, timeout=5)
            (any_probes,) = c3.execute("SELECT COUNT(*) FROM probes").fetchone()
            categories_seen = [k for (k,) in c3.execute(
                "SELECT DISTINCT COALESCE(category, 'general') FROM probes")]
            c3.close()
        except Exception:  # noqa: BLE001
            categories_seen = []
        reason = ("there are no probes yet: the prober has only just started"
                   if not any_probes
                   else f"there are no probes in category {category!r}; the "
                        f"database holds categories: {sorted(categories_seen)}")
        return 200, {"ok": True, "window_hours": hours, "category": category,
                     "engines": [], "note": reason}
    # THE POOL AND THE EXPLANATIONS COME FROM THE SAME CATEGORY AS THE ROWS.
    # Filtering the rows while the pool and the "why not included" reasons keep
    # being computed from `general` makes the image view claim that no image engine
    # is in the pool, and show the web seed.
    pool_state = pool_now_for(category)
    polled = set(pool_state["pool"])
    # WHY A RESERVE ENGINE STAYED IN RESERVE — computed here, from the same
    # database, by the same code that computes the pool. A separate implementation
    # of "why" would diverge from the implementation of "who", and the explanation
    # would start contradicting the pool.
    why, why_failed = {}, ""
    try:
        c2 = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True, timeout=10)
        why = pool.breakdown(pool.rating_of(c2, category=category),
                             pool.families_of(c2, category=category)
                             )["why_not_included"]
        c2.close()
    except Exception as exc:  # noqa: BLE001
        # A DATABASE FAILURE IS NOT "THERE ARE NO EXPLANATIONS". A silent empty
        # dictionary here makes the view say "no explanations" where the truth is
        # "we could not compute them".
        why = {}
        why_failed = f"{type(exc).__name__}: {exc}"[:120]
    disabled = set(disabled_in_image())
    out = []
    for (name, probes, ok_n, hit, miss, refused_n, empty_n, off_topic, err_n, link_count, last_ts, ms) in rows:
        shared, links_total = overlap.get(name, (0, 0))
        out.append({"engine": name, "probes": probes,
                    # THE MAIN measure: did the engine find the known-correct
                    # answer. Absolute — spam will not fool it, and an engine with
                    # its own index is not punished for being unique.
                    "reference": round((hit or 0) / probes, 2),
                    "misses": miss or 0,
                    # The share of links somebody else also found. Low with a
                    # non-empty result set is a sign of spam, or of an engine's own
                    # junk index.
                    "corroborated_by_others": (round(shared / links_total, 2)
                                             if links_total else None),
                    "ok_share": round((ok_n or 0) / probes, 2),
                    "ok": ok_n or 0, "refusals": refused_n or 0, "empty": empty_n or 0,
                    "off_topic": off_topic or 0, "errors": err_n or 0,
                    "links_avg": round(link_count or 0, 1),
                    "latency_ms": ms,
                    "last_probe": _ago(last_ts),
                    "in_poll_order": name in polled,
                    "why_not_in_pool": why.get(name, ""),
                    "disabled_in_image": name in disabled})
    return 200, {"ok": True, "window_hours": hours,
                 # THE CATEGORY IS DECLARED IN THE ANSWER. Without it the answer
                 # does not say what it is about: two different engine sets look
                 # alike, and a typo in the request is indistinguishable from an
                 # empty database.
                 "category": category, "engines_count": len(out),
                 # The substitution verdict is computed over the last N
                 # observations, and for different engines that is a different
                 # SPAN: a core engine collects them in hours, a reserve one in a
                 # week.
                 #
                 # WE ASK EXPLICITLY RATHER THAN RELY ON A SIDE EFFECT. Reading a
                 # cache that is only filled during a SEARCH makes the view return
                 # emptiness for as long as nobody searches. A field left empty by
                 # an unrelated cause reads as "nothing to say".
                 "trust_over_span": (_trust(), dict(_trust_spans))[1],
                 "trust_last_n": TRUST_LAST_N,
                 # Empty and "could not compute" are different events; the second
                 # is named.
                 "why_not_included_error": why_failed,
                 "in_pool_now": list(pool_state["pool"]),
                 "pool_source": pool_state.get("source", "?"),
                 "pool_reason": pool_state.get("reason", ""),
                 # Why a reserve engine stayed there. The cause must stand beside
                 # the effect: an engine with a hit rate of 0.86 may be out of the
                 # pool not on quality but because its family has already filled
                 # its places with better members.
                 "why_not_included": why,
                 "disabled_in_image_list": sorted(disabled & (polled | set(why))),
                 "engines": out,
                 "note": ("`reference` is the share of probes where the engine "
                             "found the KNOWN-CORRECT answer; it is the main "
                             "measure and it is absolute. `ok_share` only says the "
                             "engine answered our query: spam that quotes the "
                             "query back passes it. `corroborated_by_others` is "
                             "auxiliary — for an engine with its own index it is "
                             "low by nature, not from junk")}


def _reading_state(hours: int = 24) -> tuple[int, dict]:
    """The health of EVERY READING PATH from the prober's references.

    A twin of /engines, and a twin for a reason: search asks "which engines to
    trust", reading asks "is the fetch path alive". Only continuous observation
    answers that, because reading fails QUIETLY: a page arrives, it has text in
    it, and a single answer does not show that the text is a shield rather than
    content.

    A note on red rows. A reference declared for the browser path while that path
    is not wired up must be red, and that is A STATEMENT ABOUT A MISSING
    CAPABILITY, not a breakage. Failure direction: no data means "not checked",
    never "sound".
    """
    import sqlite3
    if not os.path.exists(PROBE_DB):
        return 503, {"ok": False,
                     "error": f"there is no prober database ({PROBE_DB}); it has never run",
                     "pages": []}
    try:
        conn = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True, timeout=10)
        since = int(time.time()) - hours * 3600
        rows = conn.execute("""
            SELECT url, path, expected,
                   COUNT(*)                          AS probes,
                   SUM(hit)                          AS hits,
                   MAX(ts)                           AS last_ts,
                   CAST(AVG(latency_ms) AS INTEGER)  AS ms,
                   CAST(AVG(chars) AS INTEGER)       AS chars
            FROM reads WHERE ts >= ?
            GROUP BY url, path
            ORDER BY SUM(hit) * 1.0 / COUNT(*) DESC, url
        """, (since,)).fetchall()
        recent = {(u, p): (st, r, sc) for u, p, st, r, sc in conn.execute("""
            SELECT r.url, r.path, r.status, r.reason, r.stub_check FROM reads r
            JOIN (SELECT url, path, MAX(ts) AS ts FROM reads WHERE ts >= ?
                  GROUP BY url, path) m
              ON r.url = m.url AND r.path = m.path AND r.ts = m.ts
        """, (since,))}
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return 503, {"ok": False,
                     "error": f"the database is unavailable: {type(exc).__name__}: {exc}",
                     "pages": []}

    pages, by_path = [], {}
    for url, path, expect_domains, probes, hit, last_seen, ms, chars in rows:
        share = round((hit or 0) / probes, 2) if probes else None
        row, reason, rejected = recent.get((url, path), ("", "", ""))
        pages.append({
            "url": url, "path": path, "expected": expect_domains,
            "probes": probes, "hits": hit or 0, "reference_share": share,
            "last_status": row, "last_reason": reason or "",
            "rejection": rejected or "",
            "latency_avg_ms": ms, "chars_avg": chars,
            "last_probe": _ago(last_seen),
        })
        was = by_path.setdefault(path, [0, 0])
        was[0] += hit or 0
        was[1] += probes
    health = {pth: ("alive" if wave[0] else "unconfirmed")
                for pth, wave in by_path.items()}
    # THE FRESH STATE IS ASKED FOR ONCE PER ANSWER, not once per field. The point
    # of this view is the truth right now, so the cache is bypassed; but two
    # bypasses in a row would open the sidecar two sessions for one answer.
    paths_now = reader.paths(fresh_flag=True)
    health.update({pth: c for pth, c in paths_now.items()
                     if not str(c).startswith("alive")})
    return 200, {
        "ok": True,
        "hours": hours,
        "paths_declared": paths_now,
        "paths_by_reference": health,
        "references_count": len(pages),
        "pages": pages,
        "note": ("a reading reference is A MARKER IN THE TEXT, not a status "
                    "code, a title or a length: all three were measured and all "
                    "three lie on a stub. A row whose expected value looks like "
                    "status=<stub> is a NEGATIVE probe: it checks that rejection "
                    "still works, and a hit on it means the stub was recognised"),
    }


# --- Image search ------------------------------------------------------------

CONTRACT_IMAGES = "ag.images/2"


def _image_on_topic(query: str, items: list[dict]) -> bool:
    """Does this look like a result set for OUR query specifically.

    ONE IMPLEMENTATION — the same `_relevant` that judges web results. All that
    happens here is input shaping: an image has no snippet but two addresses, and
    both carry marks of the topic.

    A SECOND check with its own rule ("at least half the results are about the
    query") was tried and removed: it came out stricter than the web check for no
    reason, and on a live query it rejected the results of FOUR engines outright
    merely because the titles were in another language. The web check is
    deliberately weak — one stem match is enough — because its job is to tell
    somebody else's result set from ours, not to judge quality. Two checks of one
    thing with different rules would only diverge further.

    Why this is needed for images at all: asked for a company name, one image
    engine — an art museum catalogue — answers with a self-portrait from 1878. It
    returns its own catalogue regardless of the query and looks excellent by result
    count.
    """
    normalised = [{
        "title": it.get("title", ""),
        "snippet": it.get("author", ""),
        # Both addresses in one field: `_relevant` decodes percent-encoding
        # itself, and in an image address the file name is often the only mark of
        # the topic.
        "url": (it.get("page_url", "") or "") + " " + (it.get("image_url", "") or ""),
    } for it in items]
    return _relevant(query, normalised)


def image_search(query: str, n=12, page=0) -> dict:
    """Image search. Contract ag.images/2. Never raises.

    BUILT THE SAME WAY AS WEB SEARCH, and not out of laziness: the same one-engine-
    at-a-time order, the same rate limiter, the same computed pool — only in ITS
    OWN category. They cannot be shared: an engine excellent on the web is often
    useless on images, and the reverse.

    WHAT IS FUNDAMENTALLY DIFFERENT HERE IS THAT A RESULT HAS TWO ADDRESSES.
    `image_url` is the image file itself, `page_url` is the page it was found on.
    They get confused silently: show the page instead of the image and only a human
    sees the mistake. So the fields carry different names, both are always
    returned, and the reference is declared by the domain of the IMAGE SOURCE
    rather than of the page.
    """
    try:
        n = max(1, min(50, int(n)))
    except (TypeError, ValueError):
        n = 12
    try:
        page = max(0, int(page))
    except (TypeError, ValueError):
        page = 0
    query = (query or "").strip()
    if not query:
        return _images_refusal("empty query")

    pool_state = pool_now_for("images")
    known, unknown = _split_known(tuple(pool_state["pool"]))
    if not known:
        return _images_refusal(
            "the metasearch knows none of the image engines: " + ", ".join(unknown))

    found: dict[str, dict] = {}
    asked, answered, off_topic, skipped, silent = [], [], [], [], []
    net_error = None
    limit = time.time() + ENGINE_WAIT_MAX_S
    for engine in known:
        if len(found) >= n or time.time() > limit:
            break
        now = time.time()
        with _rate_lock:
            if not _may_ask(engine, now):
                skipped.append(engine)
                continue
            _last_asked[engine] = now
        asked.append(engine)
        r = _round(query, [engine], n, page, category="images")
        if r.get("error"):
            # THE DEATH OF THE METASEARCH IS NOT AN ENGINE REFUSAL, and they must
            # not be conflated. Recording every engine as silent and sending it to
            # cool for 120 seconds loses the real cause, sends out
            # `ok: true, count: 0`, and makes the NEXT call — right after the
            # metasearch recovers — answer with the same zero and an empty asked
            # list: two minutes of successful silence after everything works again.
            #
            # The door behind all the engines is one door, and the next engine does
            # not help.
            net_error = r["error"]
            asked.pop()          # it was not asked: the door was shut
            break
        own = r.get("results") or []
        for name, _ in (r.get("unresponsive") or []):
            if name not in silent:
                silent.append(name)
        if not own:
            continue
        if not _image_on_topic(query, own):
            # The results are not about what was asked. We discard them ENTIRELY
            # and name the engine: a partial substitution is no easier to cure than
            # a complete one.
            off_topic.append(engine)
            _mark_refusal(engine, time.time())
            continue
        answered.append(engine)
        for it in own:
            key = it.get("image_url") or it.get("url")
            if key and key not in found:
                found[key] = it

    result = list(found.values())[:n]
    for it in result:
        it["domain"] = _domain(it.get("image_url") or "")
        it["page_domain"] = _domain(it.get("page_url") or "")
        it.pop("url", None)
        it.pop("snippet", None)
    # NOTHING OBTAINED AND THE DOOR SHUT IS A REFUSAL, not an empty result set.
    # An empty result set means "we looked and found nothing"; here we did not
    # look.
    if not result and net_error:
        return _images_refusal(net_error)
    return {
        "contract": CONTRACT_IMAGES, "ok": True, "error": "",
        "query": query, "page": page, "count": len(result), "results": result,
        "engines_asked": asked, "engines_answered": answered,
        "engines_irrelevant": off_topic, "engines_skipped": skipped,
        "unresponsive_engines": silent,
        # A break MID-SWEEP: some engines answered, then the door shut. A success
        # stays a success — the images were obtained — but the results are
        # incomplete by no decision of ours, and that is named rather than hidden.
        "search_aborted": net_error or "",
        "engines_unasked": ([e for e in known
                             if e not in asked and e not in skipped]
                            if net_error else []),
        "pool_source": pool_state.get("source", "?"),
        # A DIFFERENT NAME FOR A DIFFERENT MEANING. In web search
        # `all_engines_clean` is about TRUST LABELS earned against references;
        # here there are no such labels for the image category, and the flag says
        # only that no engine answered off topic. One name for two meanings is a
        # future mistake by the caller.
        "all_engines_on_topic": bool(result) and not off_topic,
    }


# HOW MANY TOP LINKS ARE READ and at how many characters each. The numbers are
# named here rather than hidden in argument defaults: they are the cost of a call.
SEARCH_READ_TOP_N = int(os.environ.get("SEARCH_READ_TOP") or "3")
SEARCH_READ_CHARS_N = int(os.environ.get("SEARCH_READ_CHARS") or "6000")


def search_read(query: str, n: int = 6, page: int = 0, corroborate: bool = False,
                min_engines: int = 0, per_engine: int = 0,
                read: bool = True, read_top: int = 0) -> dict:
    """Search that BY DEFAULT READS the top pages.

    THE GROUND IS EMPIRICAL, not a matter of taste: in a side-by-side measurement a
    competing tool returned the answer in ONE call while returning links alone took
    two — the answer only appeared after the second. Splitting the two shifts the
    work onto the caller, and this module exists to take it off them.

    THE THREE ARGUMENTS FOR KEEPING THEM SEPARATE REMAIN TRUE AND STOPPED BEING
    BLOCKING — each is answered by the SHAPE OF THE ANSWER rather than by refusing
    to read:
      · "the cost is invisible to the caller" — it is visible: `timing_ms` is
        split into search and reading, `pages_read` says how many pages were paid
        for;
      · "search fails per ENGINE, reading per ADDRESS" — both stayed separate,
        merely inside one answer: a link has its own `read_status`, an engine its
        own verdict in engines_*;
      · "the rate limiters are counted differently" — that is our internals and
        does not concern the caller at all.

    THE CHEAP PATH DID NOT DISAPPEAR, it stopped being the default: `read: false`
    returns links alone, as before, and costs the same half second.
    """
    t0 = time.time()
    d = search(query, n, page, corroborate, min_engines, per_engine)
    search_ms = int((time.time() - t0) * 1000)
    how_many = SEARCH_READ_TOP_N
    try:
        if int(read_top or 0) > 0:
            how_many = max(1, min(SEARCH_READ_MAX, int(read_top)))
    except (TypeError, ValueError):
        pass
    # THE FIELDS ARE ALWAYS RETURNED, including the read:false case and the case
    # where search itself refused. Otherwise the consumer would branch on the shape
    # of the answer, which this module never makes anyone do.
    d["read"] = bool(read)
    d["read_top"] = how_many if read else 0
    d["pages_read"] = 0
    d["pages_empty"] = 0
    d["pages_failed"] = 0
    d["timing_ms"] = {"search_ms": search_ms, "read_ms": 0}
    if not read or not d.get("ok") or not d.get("results"):
        return d

    top = d["results"][:how_many]
    t1 = time.time()
    # RECOGNITION IS OFF ON THIS PATH, AND THAT IS THE RULE, NOT AN OPTIMISATION:
    # the cost follows the explicitness of the request. Whoever called reading on
    # a document asked for the document to be read, and recognition is how a scan
    # is read. Whoever called search asked for links with text and does not expect
    # a bill for a vision model because a scan landed in the top three. Such a
    # result says `text_source: not_recognised (scan)` and names the call that
    # reads it — a refusal with a way out, not an empty field.
    pages = reader.read_many([r.get("url", "") for r in top],
                                   max_chars=SEARCH_READ_CHARS_N,
                                   recognise=False)
    d["timing_ms"]["read_ms"] = int((time.time() - t1) * 1000)
    for r, page_res in zip(top, pages):
        page_res = page_res or {}
        text = page_res.get("content") or ""
        r["content"] = text
        r["chars"] = len(text)
        # EVERY LINK HAS ITS OWN READING OUTCOME. It must not be merged with the
        # success of the search: "an engine found it" and "the page opened" are
        # different events, and a shield passed off as an empty page lies in the
        # most convenient direction.
        r["read_status"] = page_res.get("status") or "not_read"
        r["stub_check"] = page_res.get("stub_check") or "not_checked"
        r["read_reason"] = page_res.get("reason") or ""
        # HOW THE TEXT WAS OBTAINED IS MANDATORY, and this is not symmetry for
        # its own sake. Copying five fields from the read result while dropping
        # `text_source` and `recognition` delivers the output of a vision model
        # INDISTINGUISHABLY from text that was actually on the page — and the
        # contract calls `text_source` required reading precisely because
        # recognised text must not be quoted.
        r["text_source"] = page_res.get("text_source") or ""
        r["recognition"] = page_res.get("recognition")
        # Where we ended up and how it was fetched: the address of a link in a
        # result set and of the page actually read differ more often than expected.
        r["final_url"] = page_res.get("final_url") or ""
        r["read_via"] = page_res.get("via") or ""
        if r["read_status"] == "read" and text:
            d["pages_read"] += 1
        elif r["read_status"] in ("read", "empty"):
            d["pages_empty"] += 1
        else:
            d["pages_failed"] += 1
    # UNREAD LINKS CARRY THE FIELDS TOO, and that is not pedantry: a list where
    # some elements have a field and others do not forces the consumer to guess
    # whether this is "not read" or "read and empty".
    for r in d["results"][how_many:]:
        r["content"] = ""
        r["chars"] = 0
        r["read_status"] = "not_read"
        r["stub_check"] = "not_checked"
        r["read_reason"] = f"only the first {how_many} links are read"
        r["text_source"] = ""
        r["recognition"] = None
        r["final_url"] = ""
        r["read_via"] = ""
    return d


def deep_search(question: str, waves=0) -> dict:
    """The deep-search door. The orchestration is in deep.py; this wires it up.

    Search and reading are passed in AS FUNCTIONS rather than imported inside
    deep.py: the orchestration then knows nothing about the metasearch or the
    reader beyond the shape of an answer, and can be tested without raising either.
    """
    def search_fn(query: str, n: int, corrob_flag: bool = False) -> dict:
        return search(query, n, 0, corrob_flag)

    def read_many(url_list: list[str], deadline: float = 0) -> list[dict]:
        # IN PARALLEL, ONE THREAD PER DOMAIN. Reading eats 72-76% of the time of
        # a deep search, and the pages of a corpus sit almost all on different
        # sites, so they do not compete with each other for the rate limiter.
        return reader.read_many(url_list, max_chars=deep.READ_CHARS, deadline=deadline)

    return deep.deep_search(question, search_fn, read_many, model, waves=waves)


def _images_refusal(reason: str) -> dict:
    """The full field set on the failure path too, as in search and reading."""
    return {"contract": CONTRACT_IMAGES, "ok": False, "error": reason,
            "query": "", "page": 0, "count": 0, "results": [],
            "engines_asked": [], "engines_answered": [], "engines_irrelevant": [],
            "engines_skipped": [], "unresponsive_engines": [],
            "search_aborted": "", "engines_unasked": [],
            "pool_source": "", "all_engines_on_topic": False}

# --- MCP ---------------------------------------------------------------------

TOOL = {
    "name": "web_search",
    # THE DESCRIPTION IS A PROMPT, not documentation: it is the only thing the
    # model at the other end sees. A few lines listing the result fields say
    # nothing about what makes this tool different from any other search, and a
    # model cannot then learn that an empty list here is a success: it reads it as
    # a breakage.
    "description": (
        "Web search through our own metasearch layer over several independent "
        "search engines. Returns links together with information about WHO found "
        "them and how far that source can be trusted.\n\n"
        "WHEN TO CALL. You need fresh information from the web; you need to find "
        "an organisation or a person by name. For the second and third page of "
        "results, use the same call with page=1, 2 and so on; pages are numbered "
        "from zero.\n\n"
        "TO CHECK A FACT AGAINST INDEPENDENT SOURCES, ASK FOR IT. By default the "
        "sweep STOPS at the first engine that gave enough links — that is the "
        "cheap path, and one engine is one witness. `min_engines: 3` (or "
        "`corroborate: true`) keeps asking, and only then do corroborated_by_url "
        "and corroborated_by_domain count anything. It costs several times the "
        "outbound requests.\n\n"
        "IT READS BY DEFAULT. The top three links are fetched and their text "
        "arrives in the same answer in the `content` field — no second call is "
        "needed for the content. If you only need an overview, set read=false and "
        "the call again costs a fraction of a second.\n\n"
        "WHEN NOT TO CALL. You need the text of a KNOWN page (you already have the "
        "address) — that is web_read, which reads up to five addresses and offers "
        "a cursor over a long document. You need a finished ANSWER across several "
        "sources rather than material — that is web_deep_search. Not suitable for "
        "searching inside a known document or repository.\n\n"
        "HOW IT DIFFERS FROM web_deep_search. Here there is ONE pass: what was "
        "found is what was read, and you compose the answer yourself from "
        "`content`. There the tool composes the queries itself, goes in waves and "
        "returns a DIGESTED ANSWER together with what it failed to find. This one "
        "hands you material, that one hands you a judgement.\n\n"
        "WHAT IT RETURNS. results[] with title, url, snippet, domain, content (the "
        "page text for the ones that were read), chars, read_status; pages_read, "
        "pages_empty, pages_failed — how many pages were paid for and how many of "
        "them turned out to be a block; timing_ms split into search and reading; "
        "via — which engines found this particular link; corroborated_by_url and "
        "corroborated_by_domain — by how many engines the page and the site are "
        "independently corroborated; search_aborted is non-empty if the metasearch "
        "stopped answering MID-SWEEP, in which case the results are incomplete by "
        "no decision of ours and the engines that were missed are named in "
        "engines_unasked.\n\n"
        "HOW TO READ THE ANSWER — five things that are easy to get wrong.\n"
        "1. AN EMPTY LIST IS A SUCCESS, not a failure: we looked and found "
        "nothing. A failure arrives separately, with ok=false and a reason. Do not "
        "repeat the query because the list was empty — repeat it rephrased.\n"
        "2. engines_skipped means \"not asked\" (the rate limit applied), NOT "
        "\"asked and stayed silent\". The silent ones are in unresponsive_engines, "
        "the ones that answered a different question are in engines_irrelevant.\n"
        "3. CORROBORATION IS NOT CORRECTNESS. corroborated_by_url means \"this many "
        "independent engines found this same link\" and does NOT mean \"this answer "
        "is truer\". On an ambiguous query the most corroboration goes to the "
        "best-indexed namesake rather than to the subject asked about: one name can "
        "belong to a retailer, an investigations platform, a maker of enclosures "
        "and a maker of portable power supplies at once, and all of them are real. "
        "If the sources describe DIFFERENT subjects under one name, the correct "
        "answer is \"there are several, here they are\", not a choice by the number "
        "of corroborations.\n"
        "4. engines_trust labels each engine: clean — checked against references "
        "and does not substitute the subject; substitutes — has answered about a "
        "namesake; not checked or unavailable — there is no information. If "
        "all_engines_clean is false, the results are worth verifying against "
        "features of the subject sought: an engine may have answered about a "
        "different company of the same name. The label `candidate` means "
        "\"no misses yet, but fewer than three observations\" — not \"clean\".\n"
        "5. A RESULT WITH text_source=\"not_recognised (scan)\" IS A DOCUMENT WE "
        "DID NOT READ, not a page without text. It is a scanned PDF: search does "
        "not pay for a vision model on a link it merely found. If you need it, "
        "call web_read on that address — reading recognises it."
    ),
    # The annotations are read by the HOST, not by the model: readOnly says the
    # call changes nothing and need not be put to the user for confirmation,
    # openWorld says the answer depends on the outside world and is not
    # reproducible word for word.
    "annotations": {
        "title": "Web search",
        # THE NATURE OF A CALL LIVES BESIDE THE TOOL rather than in a tuple in the
        # accounting code: a written-out tuple falls behind a sixth tool SILENTLY.
        "nature": "search",
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
    },
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description":
                      "the search query, in any language; a non-Latin script "
                      "switches the results to that language"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50,
                            "description":
                            "how many links to return, 1..50, default 6. The more "
                            "you ask for, the more engines have to be queried — "
                            "that is, dearer and slower"},
            "page": {"type": "integer", "minimum": 0, "description":
                     "the result page, from zero; default 0. A second page is for "
                     "when the first did not contain what you sought, not for "
                     "collecting more links at once"},
            "read": {"type": "boolean", "description":
                     "TRUE by default — the tool reads the top pages and returns "
                     "their content. Set false when you only need an overview of "
                     "the results: the call then costs a fraction of a second "
                     "instead of seconds, but there will be no content"},
            "read_top": {"type": "integer", "minimum": 1, "maximum": 8,
                         "description":
                         "how many top links to read, 1..8; default 3. This is the "
                         "main cost of the call: every page is a separate "
                         "download"},
            "min_engines": {"type": "integer", "minimum": 0, "maximum": 8,
                            "description":
                            "query AT LEAST this many engines, however many links "
                            "were collected earlier. 0 (the default) means stop as "
                            "soon as max_results are collected. Set 3 or more when "
                            "you need independence of sources: `via` and "
                            "corroborated_by_url will then name different "
                            "witnesses. THE PRICE: as many times more outbound "
                            "requests"},
            "per_engine": {"type": "integer", "minimum": 0, "maximum": 50,
                           "description":
                           "how many links to take from EACH engine; 0 means "
                           "however many are still missing from max_results"},
            "corroborate": {"type": "boolean", "description":
                            "a deprecated name for min_engines=3. Kept for older "
                            "callers; in new code set min_engines as a number"},
        },
        "required": ["query"],
    },
}


READ_TOOL = {
    "name": "web_read",
    # THE DESCRIPTION IS A PROMPT. For reading it carries more than for search:
    # here success and failure look alike (text arrived in both cases), and the
    # model can only tell them apart by fields it has been told about.
    "description": (
        "Read a web page by address and return its text.\n\n"
        "HOW IT DIFFERS FROM web_search, WHICH ALSO READS. That one reads the top "
        "three links of its own results at 6000 characters each — enough for an "
        "answer. This one takes the addresses YOU name, up to five at a time, "
        "reads them in full with a cursor over a long document, can demand the "
        "browser and can check for a marker. If you need an answer, search is "
        "enough; if you need to work with a document, come here.\n\n"
        "WHEN TO CALL. You need the text of a specific page whose address is "
        "already known — from web_search results or from the user. You need facts "
        "from an article rather than a snippet about it. Read a long page in "
        "parts: the same call with the offset named at the end of the truncated "
        "text.\n\n"
        "WHEN NOT TO CALL. There is no address yet — use web_search first. You "
        "need an office document (DOCX, XLSX) — the tool does not parse those and "
        "will say so plainly; PDF, however, IS read. A search-engine result page "
        "must not be read: it merges neighbouring results into one text and hands "
        "you facts about a namesake.\n\n"
        "WHAT IT RETURNS. results[] per address: content — the page text, status — "
        "what became of it, title, published, lang, final_url (where a redirect "
        "led), stub_check — whether this is a block; text_source — HOW the text "
        "was obtained.\n\n"
        "HOW TO READ THE ANSWER — four things that are easy to get wrong.\n"
        "1. EMPTY CONTENT IS A SUCCESS, not a failure: the page opened and has no "
        "text in it. Repeating is pointless, take another source. On a refusal or "
        "a failure to open, repeating does make sense.\n"
        "2. `ok` is about the TOOL, not about the pages: it stays true even if not "
        "one page was read. Look at count and failed, and at the status of each "
        "address: `read`, `empty`, `stub`, `refused`, `unreachable`, `forbidden`, "
        "`not_reached`. They mean different things and call for different next "
        "steps — `empty` is not worth repeating, `refused` and `unreachable` are; "
        "`not_reached` is news about US (out of time, the per-domain rate limit, "
        "or beyond the batch ceiling) and says nothing about the page.\n"
        "3. text_source IS REQUIRED READING when it says the text was recognised. "
        "That is a scanned PDF with no text layer: the pages were rendered and read "
        "by a vision model, and such text MUST NOT be quoted as exact — a "
        "measurement recovered 94% of the reference numbers. The details are in the "
        "`recognition` field, including the dpi and whether the model\u0027s answer "
        "was cut off. A text layer means \"copied out of the file\" and is quotable "
        "verbatim.\n"
        "4. A stub status means we met an anti-bot shield or a paywall: text "
        "arrived, but it is not from the page. Do not retell it as the content. A "
        "stub_check of \"not checked\" is NOT \"clean\".\n\n"
        "PDF. It is read; `pages` and `pages_read` say how many pages the document "
        "has and how many were parsed. An empty result on a PDF means \"there are "
        "pages and no text\" — that is a scan, cured by recognition rather than by "
        "repeating. And remember: in a PDF the characters can be extracted "
        "correctly while the reading order falls apart, so labels come away from "
        "their values. Do not assemble \"property: value\" pairs out of adjacent "
        "PDF lines without checking that they really are adjacent."
    ),
    "annotations": {
        "title": "Page reading",
        "nature": "reading",
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
    },
    "inputSchema": {
        "type": "object",
        "properties": {
            "urls": {"type": "array", "items": {"type": "string"},
                     "minItems": 1, "maxItems": reader.MAX_URLS,
                     "description":
                     f"page addresses, from 1 to {reader.MAX_URLS} per call. "
                     "Take them verbatim from web_search results, do not guess at "
                     "them. Addresses beyond the ceiling come back with a "
                     "did-not-get-through status — they never vanish silently"},
            "max_chars": {"type": "integer",
                          "minimum": reader.MIN_CHARS, "maximum": reader.MAX_CHARS,
                          "description":
                          f"how many characters of content to return per "
                          f"address, default {reader.DEFAULT_CHARS}. The remainder "
                          "is not lost — it is fetched by the next call with an "
                          "offset"},
            "offset": {"type": "integer", "minimum": 0, "description":
                       "the character to continue reading from, counted from zero. "
                       "The tool names the continuation number itself at the end "
                       "of the truncated text — take it from there rather than "
                       "computing it"},
            "format": {"type": "string", "enum": ["markdown", "text", "html"],
                       "description":
                       "markdown (default) — text with headings and links; text — "
                       "text only, cheaper in characters; html — as it came, for "
                       "parsing the markup"},
            "links": {"type": "boolean", "description":
                      "false by default. Return the page links as a list. "
                      "links_total is ALWAYS returned: an empty list with "
                      "links_total > 0 means \"you did not ask\", not \"there are "
                      "none\""},
            "expect": {"type": "array", "items": {"type": "string"},
                       "maxItems": 5, "description":
                       "markers that MUST occur in the text if this is the right "
                       "page. Set them when the address was found by an "
                       "organisation or person name: a status code and a title are "
                       "forged by a stub, knowledge of the content is not. Not "
                       "found gives expected_found=false"},
            "mode": {"type": "string", "enum": ["auto", "plain", "browser"],
                     "description":
                     "how to fetch. auto (default) and plain use an ordinary "
                     "request. browser goes through a real browser, for pages with "
                     "a script-based check; if that path is not wired up the call "
                     "returns a did-not-get-through status — see paths_available "
                     "in the answer"},
            "fresh": {"type": "boolean", "description":
                      f"false by default. Do not take the result from the read "
                      f"cache (it lives {int(reader.CACHE_S)} s). Use it when the "
                      "page is known to be changing as you watch"},
        },
        "required": ["urls"],
    },
}


IMAGE_TOOL = {
    "name": "web_image_search",
    "description": (
        "Image search through our own metasearch layer. The third tool of the "
        "module: web_search finds pages, web_read extracts their content, this one "
        "finds IMAGES.\n\n"
        "WHEN TO CALL. You need a picture of an object, a product, a building, a "
        "person, a diagram. You need the address of the image file itself rather "
        "than of a page about it.\n\n"
        "WHEN NOT TO CALL. You need text about the object — that is web_search. You "
        "need the content of a specific page — web_read. This tool does NOT look at "
        "the pictures and does not describe them: it finds addresses, and whoever "
        "can see looks at them.\n\n"
        "WHAT IT RETURNS. results[] with image_url (the file itself), page_url (the "
        "page it was found on), domain (the site the image is SOURCED from), "
        "page_domain, thumbnail, title, author, published, via.\n\n"
        "HOW TO READ THE ANSWER — three things.\n"
        "1. AN IMAGE HAS TWO ADDRESSES and they must not be confused: image_url is "
        "the file, page_url is the page. Showing the page instead of the image is a "
        "mistake only a human notices.\n"
        "2. AN EMPTY LIST IS A SUCCESS, not a failure: we looked and found nothing. "
        "A failure arrives with ok=false and a reason.\n"
        "3. engines_irrelevant names engines whose results were discarded ENTIRELY "
        "as not being about the query. With images this is common: an engine "
        "returns its own catalogue regardless of the query and looks excellent by "
        "result count."
    ),
    "annotations": {
        "title": "Image search",
        "nature": "search",
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
    },
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description":
                      "what to look for; a non-Latin script switches the results "
                      "to that language"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50,
                            "description": "how many images to return, default 12"},
            "page": {"type": "integer", "minimum": 0, "description":
                     "the result page, from zero; default 0"},
        },
        "required": ["query"],
    },
}


SHOT_TOOL = {
    "name": "web_screenshot",
    "description": (
        "A PNG screenshot of a web page. The fourth tool of the module.\n\n"
        "WHEN TO CALL. You need to SHOW a page to a person — the layout, the "
        "design, what the text does not carry. And you need to CROSS-CHECK: the "
        "shot and the text are obtained in one browser visit but by different "
        "routes — the pixels are drawn by the layout engine, the text comes from "
        "the DOM. A disagreement between them catches what neither route sees "
        "alone.\n\n"
        "WHEN NOT TO CALL. You need the text of the page — that is web_read, many "
        "times cheaper. A screenshot costs a browser launch.\n\n"
        "WHAT IT RETURNS. png_base64 — the shot itself; bytes — its size; "
        "page_text — the text of THE SAME visit, up to max_chars; page_text_chars "
        "— the length of the whole text, which may be greater; "
        "page_text_truncated; browser_version.\n\n"
        "HOW TO READ THE ANSWER — two things, and they are DIFFERENT.\n"
        "1. shot_taken — THE SHOT WAS TAKEN: the browser is alive, the page "
        "loaded.\n"
        "2. expected_found — WHAT WE EXPECTED IS ON THE PAGE (when `expect` was "
        "given). A shot can be taken flawlessly and show the wrong thing: a stub, a "
        "captcha, an error page. Do not confuse these two fields — a page that "
        "honestly failed a check and a browser that never opened are different "
        "events.\n\n"
        "A signal worth seeing: there is a shot and page_text_chars is near zero — "
        "the page drew, and has nothing to say."
    ),
    "annotations": {
        "title": "Page screenshot",
        "nature": "reading",
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
    },
    "inputSchema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "the page address"},
            "expect": {"type": "array", "items": {"type": "string"}, "maxItems": 5,
                       "description":
                       "markers that must be present on the page. They are checked "
                       "against the TEXT of the same visit: a screenshot cannot "
                       "check itself. Not found gives expected_found=false, and the "
                       "shot is still taken"},
            "full_page": {"type": "boolean", "description":
                          "false by default — the visible area. true captures the "
                          "whole page and costs more"},
            "max_chars": {"type": "integer", "minimum": 0, "maximum": 200000,
                          "description":
                          "how much of the page text to return beside the shot; "
                          "2000 by default. The text is here to be compared with "
                          "the picture — for the whole document call web_read, "
                          "which has a cursor"},
        },
        "required": ["url"],
    },
}


DEEP_TOOL = {
    "name": "web_deep_search",
    "description": (
        "Deep search: find, read and DIGEST AN ANSWER. The fifth tool of the module "
        "and the only one that answers a question rather than handing back "
        "material.\n\n"
        "WHEN TO CALL. The question requires several sources to be brought "
        "together: what is happening with something, how one thing differs from "
        "another, what the figures of a specific organisation are. The tool "
        "composes the queries itself, reads the pages and writes an answer with "
        "references to the sources.\n\n"
        "WHEN NOT TO CALL. You need a list of links — web_search is tens of times "
        "cheaper. You need the text of a known page — web_read. This tool spends a "
        "model and minutes; call it on a question, not on a query.\n\n"
        "WHAT IT RETURNS. answer — the digested answer with [1]-style references; "
        "sources[] — the pages that were read; markers — the features used to check "
        "that the pages are about THE SUBJECT ASKED ABOUT; timing_ms — where the "
        "time went (searching, reading, the model); usage.by_model — tokens per "
        "model, with money left to whoever holds the price registry.\n\n"
        "HOW TO READ THE ANSWER — five things.\n"
        "1. THE MAIN FIELD IS `outcome`, NOT `answer`. Five values: found — the "
        "markers met on a page; ambiguous — the sources hold SEVERAL DIFFERENT "
        "subjects under this name, and they are listed in ambiguity.variants; "
        "off_target — material was found but about ANOTHER subject (a namesake, a "
        "different city); not_found — there are no sources; unknown — there were no "
        "markers, so there was nothing to check with. On off_target the answer "
        "looks convincing and is about the wrong thing. On ambiguous the answer "
        "applies to THE LARGEST GROUP and not to all of them: the other variants "
        "are real, and if one of them is wanted, ask the person or refine the "
        "question rather than choosing yourself.\n"
        "2. summarised_from_on_target says whether the answer was digested from "
        "verified pages or from whatever was found. False means read the answer as "
        "a draft.\n"
        "3. stopped_because and waves_done show HOW MUCH work was done. A full "
        "answer and a short one look alike; this is the only place they can be told "
        "apart.\n"
        "4. THREE NUMBERS ABOUT SOURCES, AND THEY ARE DIFFERENT. sources_total — "
        "how many were found; sources_with_content — how many could be read (a "
        "block returns zero characters and stays in the list); sources_on_target — "
        "on how many the markers met. The answer stands on the third number and "
        "sounds weighty because of the first.\n"
        "5. sources_confirmed_2plus and confirmed_by_engines count INDEPENDENCE, "
        "not correctness: how many different engines found the same link. On an "
        "ambiguous name the most corroboration goes to the best-indexed namesake. "
        "If the answer looks confident while the question admits several different "
        "subjects under one name, look at `outcome` first: `ambiguous` means the "
        "tool composed exactly that answer — the variants are in "
        "ambiguity.variants, and the digest applies to the largest group only. "
        "Corroboration counts do NOT decide between them."
    ),
    "annotations": {
        "title": "Deep search",
        "nature": "search",
        "readOnlyHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
    },
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description":
                         "the whole question, in your own words. Not a search "
                         "query: the tool composes the queries itself"},
            "waves": {"type": "integer", "minimum": 1, "maximum": 3,
                      "description":
                      "at most this many search waves, default 3. A wave stops by "
                      "itself as soon as the markers meet — the ceiling bounds the "
                      "worst case, not the ordinary one"},
        },
        "required": ["question"],
    },
}


TOOLS = {TOOL["name"]: TOOL, READ_TOOL["name"]: READ_TOOL,
               IMAGE_TOOL["name"]: IMAGE_TOOL, SHOT_TOOL["name"]: SHOT_TOOL,
               DEEP_TOOL["name"]: DEEP_TOOL}
# Names for ACCOUNTING as a separate set: the counter key is built before any
# validation of the request shape, and reading it straight out of TOOLS would make
# accounting depend on declaration order.
_TOOL_NAMES = frozenset(TOOLS)
# THE NATURE OF A TOOL LIVES BESIDE IT, not in a tuple in the accounting code: a
# written-out tuple falls behind a sixth tool SILENTLY — it lands in neither
# searches nor reads, and no check notices.
_SEARCHING_TOOLS = frozenset(ent for ent, ts_val in TOOLS.items()
                    if (ts_val.get("annotations") or {}).get("nature") == "search")
_READING_TOOLS = frozenset(ent for ent, ts_val in TOOLS.items()
                      if (ts_val.get("annotations") or {}).get("nature") == "reading")



def _invoke(name: str, args: dict) -> dict:
    """Invoke a tool by name. One entry point for both doors, so that MCP and
    /tool-spec cannot disagree about which tools exist at all."""
    if name == TOOL["name"]:
        return search_read(args.get("query", ""), args.get("max_results", 6),
                           args.get("page", 0),
                           bool(args.get("corroborate", False)),
                           args.get("min_engines", 0),
                           args.get("per_engine", 0),
                           # READING IS THE DEFAULT. An absent argument means
                           # "read": a tool that returns links by default puts a
                           # second call on the model.
                           read=bool(args.get("read", True)),
                           read_top=args.get("read_top", 0))
    if name == IMAGE_TOOL["name"]:
        return image_search(args.get("query", ""), args.get("max_results", 12),
                            args.get("page", 0))
    if name == SHOT_TOOL["name"]:
        return reader.screenshot(args.get("url", ""), args.get("expect"),
                                 bool(args.get("full_page", False)),
                                 args.get("max_chars", reader.SHOT_TEXT_CHARS))
    if name == DEEP_TOOL["name"]:
        return deep_search(args.get("question", ""), args.get("waves", 0))
    return reader.read(args.get("urls", []),
                       args.get("max_chars", reader.DEFAULT_CHARS),
                       args.get("offset", 0),
                       args.get("format", "markdown"),
                       bool(args.get("links", False)),
                       args.get("expect"),
                       args.get("mode", "auto"),
                       bool(args.get("fresh", False)))


# An emergency ceiling on an MCP response. Not a tool policy but the last defence
# against an answer the client cannot parse: reading cuts its content by its own
# max_chars and says so with a `truncated` field, while this covers the case we did
# not foresee.
MCP_RESPONSE_CEILING = 400_000


def _shrink(res: dict) -> dict:
    """Shrink an answer without breaking its shape.

    We cut the CONTENT of fields rather than the whole string, and every field
    stays in place: the consumer branches on fields, and an answer missing half its
    keys is worse for it than an answer with shortened text.
    """
    shrunk = dict(res)
    outs = []
    for r in res.get("results", []):
        r = dict(r)
        text = r.get("content") or ""
        if len(text) > 20_000:
            r["content"] = text[:20_000] + "\n\n[...the server shrank this answer to the MCP ceiling]"
            r["chars"] = len(r["content"])
            r["truncated"] = True
        r["links"] = r.get("links", [])[:20]
        outs.append(r)
    shrunk["results"] = outs
    shrunk["response_shrunk"] = True
    return shrunk


def _ok(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def rpc(body) -> dict | None:
    """One JSON-RPC request -> one answer. None for notifications (they have no id).

    Never raises: a tool error comes back as isError inside the result, a protocol
    error as `error`. The client on the other side has no business untangling our
    exceptions.
    """
    if not isinstance(body, dict):
        return _err(None, -32600, "a JSON-RPC object was expected")
    method = str(body.get("method") or "")
    rid = body.get("id")

    if method == "initialize":
        return _ok(rid, {"protocolVersion": PROTOCOL, "serverInfo": SERVER,
                         "capabilities": {"tools": {"listChanged": False}}})
    if method.startswith("notifications/"):
        return None
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": list(TOOLS.values())})
    if method == "tools/call":
        # THE SHAPE OF THE PARAMETERS IS CHECKED, NOT ASSUMED. A plain
        # `body.get("params") or {}` lets `params` through AS A LIST, giving `.get`
        # on a list: an AttributeError and a dropped connection with no answer.
        params = body.get("params")
        params = params if isinstance(params, dict) else {}
        name_field = params.get("name")
        name = name_field if isinstance(name_field, str) else ""
        if name not in TOOLS:
            return _err(rid, -32602, f"tool not available over MCP: {name!r}")
        args = params.get("arguments")
        args = args if isinstance(args, dict) else {}
        res = _invoke(name, args)
        # THE PAYLOAD IS CUT BEFORE SERIALISATION, NOT AFTER. Cutting the
        # finished string (`json.dumps(...)[:200_000]`) is unreachable while there
        # is only search — fifty results are about 45 KB. With reading, a page at
        # max_chars=200000 gives JSON well past the ceiling, the cut lands in the
        # middle of a string, and the client receives UNPARSEABLE text: instead of
        # "the content is truncated" it sees "the module is broken" — exactly the
        # substitution of one message for another that seven distinct statuses
        # exist to prevent. The reader cuts its own content and says so with
        # `truncated`; what remains here is an emergency ceiling for the case we
        # did not foresee, and it must stay parseable JSON.
        text = json.dumps(res, ensure_ascii=False, indent=1)
        if len(text) > MCP_RESPONSE_CEILING:
            text = json.dumps(_shrink(res), ensure_ascii=False, indent=1)
        # An empty result set is a success: isError means "the tool did not do its
        # work", not "nothing was found".
        return _ok(rid, {"content": [{"type": "text", "text": text}],
                         "isError": not res.get("ok", True)})
    return _err(rid, -32601, f"method not supported: {method}")


# --- HTTP --------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # A silent keep-alive connection must not hold a thread forever: see Server.
    timeout = 30
    server_version = "ag-mod-search/1"

    def log_message(self, fmt, *args):  # noqa: A003 — quieter than the default log
        pass

    def _send(self, code: int, payload) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/healthz":
            _count_call("/healthz")
            ok, payload = health(deep=q.get("deep", ["0"])[0] not in ("0", "", "false"))
            self._send(200 if ok else 503, payload)
            return
        if parsed.path == "/engines":
            _count_call("/engines")
            self._send(*_engines_state(
                deep._as_int(q.get("hours", ["24"])[0], 24),
                (q.get("category", ["general"])[0] or "general")))
            return
        if parsed.path == "/pages":
            _count_call("/pages")
            self._send(*_reading_state(deep._as_int(q.get("hours", ["24"])[0], 24)))
            return
        if parsed.path == "/tool-spec":
            # THE TOOL DEFINITION IN THE CALLER'S REGISTRY FORMAT, so that one
            # tool has ONE description rather than two that diverge.
            #
            # A caller with its own tool entry, written for its own search
            # implementation, will get the full data — both doors return the same
            # field set — while its model goes on reading the old description:
            # with no mention that an empty result set is not a failure, no trust
            # labels, no price for the corroboration mode. Complete data with an
            # incomplete description gives a tool nobody knows how to use.
            #
            # We return it in THEIR shape rather than ours: a translation on the
            # consumer's side is one more place where the definition diverges.
            _count_call("/tool-spec")
            tags = {TOOL["name"]: ["web", "search"],
                    READ_TOOL["name"]: ["web", "read", "fetch"],
                    IMAGE_TOOL["name"]: ["web", "images", "search"],
                    SHOT_TOOL["name"]: ["web", "screenshot", "browser"],
                    DEEP_TOOL["name"]: ["web", "search", "deep", "research"]}
            # THE OBSERVATION CEILING is a field of the caller's registry: how
            # many characters of the result text reach the model before being cut.
            # Search 1500, reading 4000 — a page is longer than a result set. We
            # send it EXPLICITLY rather than leave it to a default: a tool without
            # this field deliberately fails as a configuration error there, so the
            # omission is caught on the first call instead of silently eating the
            # observation.
            ceiling = {TOOL["name"]: 1500, READ_TOOL["name"]: 4000,
                       IMAGE_TOOL["name"]: 1500,
                       # A screenshot goes as an image, not as text: the ceiling
                       # here is about the accompanying fields, not about the PNG.
                       SHOT_TOOL["name"]: 800,
                       # Deep search returns a DIGESTED answer rather than a
                       # result set: it needs more room than search, and as much
                       # as reading a page.
                       DEEP_TOOL["name"]: 4000}
            self._send(200, {
                # ALL the tools, not the first: a registry returning one of
                # several is worse than an empty one — it looks complete.
                "mcp": list(TOOLS.values()),
                "engine_registry": [
                    {
                        "name": ts_val["name"],
                        "description": ts_val["description"],
                        # In that registry `input` is an informal map of "name:
                        # type and explanation", not JSON Schema. We build it from
                        # the schema so the source stays single.
                        "input": {
                            name: (f"{info.get('type', 'str')} — {info.get('description', '')}"
                                  + ("" if name in ts_val["inputSchema"].get("required", [])
                                     else " (optional)"))
                            for name, info in ts_val["inputSchema"]["properties"].items()
                        },
                        "read_only": True,
                        "max_observation_chars": ceiling[ts_val["name"]],
                        "tags": tags[ts_val["name"]],
                    } for ts_val in TOOLS.values()
                ],
                # `mcp` and `engine_registry` are LISTS, and were objects while
                # there was only one tool. The shape changed on the day there were
                # two, and it breaks a consumer exactly once — declared here rather
                # than silently.
                "shape": "list",
                "note": ("a tool description is the only thing the model sees; "
                            "take it from here rather than writing your own, or "
                            "there will be two definitions"),
            })
            return
        if parsed.path == "/stats":
            _count_call("/stats")
            with _counters_lock:
                shot = {**_counters, "by_path": dict(_counters["by_path"])}
            self._send(200, {
                "uptime_s": int(time.time() - _started_at),
                "calls_total": shot["total"],
                "by_path": shot["by_path"],
                "searches": shot["searches"],
                "with_corroboration": shot["with_corroboration"],
                # A rule of use: the corroboration mode costs five times the
                # outbound requests and is meant for two or three key sources per
                # dossier. A share above a tenth means it is being called as a
                # fan-out — the caller's defect, and visible from here.
                "corroboration_share": (
                    round(shot["with_corroboration"] / shot["searches"], 3)
                    if shot["searches"] else None),
                "reads": shot["reads"],
                "pages_read": shot["pages"],
                "errors": shot["errors"],
                "last_call": _ago(shot["last_call"]),
                "last_search": _ago(shot["last_search"]),
                "last_read": _ago(shot["last_read"]),
                # MODEL SPEND BELONGS HERE, not only in the answer to a call. One
                # caller sees an answer and forgets it at once; for spend to be
                # VISIBLE it must be visible from outside, with no call. The ledger
                # survives a restart: an in-memory counter would show zero after
                # every restart and still be called accounting.
                "model_spend": model.usage_summary(),
                "note": ("calls are counted from the start of the process; model "
                            "spend covers the whole ledger, which survives a "
                            "restart. The container health check calls /healthz "
                            "every 30 s and is counted here too"),
            })
            return
        if parsed.path == "/ag/deep":
            res = deep_search(q.get("q", [""])[0], q.get("waves", ["0"])[0])
            _count_call("/ag/deep", search=True, error=not res.get("ok"))
            self._send(200 if res.get("ok") else 502, res)
            return
        if parsed.path == "/ag/screenshot":
            res = reader.screenshot(q.get("url", [""])[0], q.get("expect", []),
                                    q.get("full_page", ["0"])[0] not in ("0", "", "false"),
                                    q.get("max_chars", [reader.SHOT_TEXT_CHARS])[0])
            _count_call("/ag/screenshot", error=not res.get("shot_taken"))
            self._send(200 if res.get("shot_taken") else 502, res)
            return
        if parsed.path == "/ag/images":
            res = image_search(q.get("q", [""])[0], q.get("n", ["12"])[0],
                               q.get("page", ["0"])[0])
            _count_call("/ag/images", search=True, error=not res.get("ok"))
            self._send(200 if res.get("ok") else 502, res)
            return
        if parsed.path == "/ag/read":
            # The plain HTTP door for reading, contract ag.read/2. Arguments
            # arrive as strings — coercion and ceilings live in reader.read() and
            # not here, or two doors would hold two editions of one policy and
            # diverge at the first edit.
            url_list = q.get("url", []) + [
                a for chunk in q.get("urls", []) for a in chunk.split(",") if a.strip()]
            res = reader.read(
                url_list,
                q.get("max_chars", [reader.DEFAULT_CHARS])[0],
                q.get("offset", ["0"])[0],
                q.get("format", ["markdown"])[0],
                q.get("links", ["0"])[0] not in ("0", "", "false"),
                q.get("expect", []),
                q.get("mode", ["auto"])[0],
                q.get("fresh", ["0"])[0] not in ("0", "", "false"))
            _count_call("/ag/read", reading=True, error=not res.get("ok"),
                      page_count=res.get("count", 0))
            self._send(200 if res.get("ok") else 400, res)
            return
        if parsed.path == "/ag/search":
            corrob = q.get("corroborate", ["0"])[0] not in ("0", "", "false")
            do_read = q.get("read", ["1"])[0] not in ("0", "false", "no")
            res = search_read(q.get("q", [""])[0], q.get("n", ["6"])[0],
                              q.get("page", ["0"])[0], corrob,
                              q.get("min_engines", ["0"])[0],
                              q.get("per_engine", ["0"])[0],
                              read=do_read,
                              read_top=q.get("read_top", ["0"])[0])
            _count_call("/ag/search", search=True, error=not res.get("ok"),
                      corroborated=corrob)
            self._send(200 if res.get("ok") else 502, res)
            return
        _count_call("404")
        self._send(404, {"ok": False, "error": f"no such path: {parsed.path}"})

    def do_POST(self):  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/mcp":
            self._send(404, {"ok": False, "error": "MCP lives on POST /mcp"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            self._send(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "the body is not JSON"}})
            return
        # A batch of requests is allowed by the specification — we answer with a
        # list, skipping notifications (they have no id and no answer).
        if isinstance(body, list):
            # A BATCH IS COUNTED ITEM BY ITEM AND DOES NOT BYPASS ACCOUNTING. An
            # array of calls performs real searches while `/stats` does not move at
            # all — not even the total. The one view that answers "is the service
            # being called" would then stay silent about a whole entrance.
            out = []
            for x in body:
                r = rpc(x)
                _count_mcp_call(x, r)
                if r is not None:
                    out.append(r)
            self._send(200, out)
            return
        res = rpc(body)
        _count_mcp_call(body, res)
        if res is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, res)


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with two corrections to the standard-library defaults.

    THE SOCKET QUEUE. socketserver.TCPServer.request_queue_size = 5 is the
    listen() argument — how many established connections the kernel will hold
    while we have not accepted them. Measured: up to 25 concurrent requests the
    adapter answers in 3-20 ms and sustains ~1100 requests per second, and past
    that there are dips of almost exactly ~1000 ms — not our slowness but a SYN
    retransmission after the queue overflowed and the kernel dropped the packet
    silently. Resources have nothing to do with it: after a burst of 400 requests
    the process holds 1 thread and 22 MiB. It came down to one number.

    THE CONNECTION TIMEOUT. BaseHTTPRequestHandler.timeout = None, while we
    announce HTTP/1.1, where a connection is keep-alive by default. So a client
    that opens a connection and falls silent holds a thread FOREVER. This is not
    about malice but about a broken network and a hung client, after which the
    thread never comes back.
    """
    request_queue_size = 128
    daemon_threads = True


def _stdio() -> None:
    """MCP over stdio: one JSON-RPC object per line in, one answer per line out.

    THE DEFAULT TRANSPORT OF THE PROTOCOL, and its absence was invisible here for
    one reason: our only consumer speaks HTTP. A desktop client starts the server
    as a PROCESS and talks to it through the pipes; with no stdio the module is
    unusable by most clients that exist, and nothing in our own testing could show
    it.

    STDOUT BECOMES THE PROTOCOL, and that is the whole danger of this mode. One
    stray `print` — the start-up banner, a diagnostic from the pool — lands in the
    middle of the conversation, and a client reading a line at a time sees a
    broken message. A real wrapper answered our banner with `ignoring non-JSON
    output` and gave up sixty seconds later.

    So the real stdout is taken ONCE, here, and `sys.stdout` is pointed at
    stderr for the rest of the process. Every print in this module and in every
    module it imports becomes diagnostics BY CONSTRUCTION. Auditing the existing
    ten print sites would fix today and say nothing about the eleventh.

    The behaviour itself is not duplicated: both doors call the same `rpc()`,
    which was written to take a parsed body and never raise. Two transports over
    two implementations would diverge at the first edit.
    """
    import sys
    out = sys.stdout                  # taken BEFORE the substitution below
    sys.stdout = sys.stderr           # every print is now diagnostics
    print(f"ag-mod-search: MCP over stdio, metasearch={SEARXNG_URL}", flush=True)

    def answer(payload) -> None:
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        out.flush()                   # a client reads line by line and waits

    while True:
        line = sys.stdin.readline()
        if not line:                  # EOF: the client closed the pipe
            return
        line = line.strip()
        if not line:                  # a blank line is not a message
            continue
        try:
            body = json.loads(line)
        except Exception:  # noqa: BLE001
            # A BROKEN LINE IS ANSWERED, NOT SWALLOWED. Silence here is
            # indistinguishable from a hung server, and the client waits out its
            # whole timeout to learn nothing.
            answer({"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "the line is not JSON"}})
            continue
        # A batch is a list, exactly as over HTTP, and is counted item by item:
        # an array of calls must not bypass the one view that answers "is anything
        # calling us".
        if isinstance(body, list):
            batch = []
            for x in body:
                r = rpc(x)
                _count_mcp_call(x, r)
                if r is not None:
                    batch.append(r)
            if batch:
                answer(batch)
            continue
        res = rpc(body)
        _count_mcp_call(body, res)
        # A notification has no id and gets NO line back. Answering it would put
        # an unmatched message into a stream the client reads by correlation.
        if res is not None:
            answer(res)


def main() -> None:
    # THE TRANSPORT IS CHOSEN EXPLICITLY, NEVER GUESSED. A guess of the form "is
    # there a terminal on stdin" reads a sign that MERELY SITS NEXT TO the
    # subject: it is true of a pipe in a shell script as well, and one day it
    # answers for a case nobody meant.
    import sys
    if "--stdio" in sys.argv[1:] or (os.environ.get("MCP_TRANSPORT") or "").strip().lower() == "stdio":
        _stdio()
        return
    srv = Server(("0.0.0.0", PORT), Handler)
    print(f"ag-mod-search: MCP on POST :{PORT}/mcp, "
          f"ag.search/2 on GET :{PORT}/ag/search, metasearch={SEARXNG_URL}",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
