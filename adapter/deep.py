# -*- coding: utf-8 -*-
"""Deep search: contract ag.deep/2. A wave is find, read, verify.

WHAT THIS FILE PAID FOR IN MEASUREMENTS, AND WHY THOSE NOTES OUTLIVE THE CODE.

1. STOPPING BY CORPUS SIZE IS REFUTED. A deliberately POOR question — a tiny
   company plus a city — produced 203 613 characters, MORE than a broad
   technical one. Volume measures DOWNLOADING, not FINDING. The same run shows
   how it lies: the company name appeared on 9 pages out of 16 and the city on
   NONE. The corpus was about same-named firms in three other regions, and by
   volume it looked excellent.

2. THERE ARE NO NUMERIC THRESHOLDS, AND THAT IS A DECISION. Two runs of ONE
   question differ twofold in corpus size. A threshold taken from a single
   measurement would be a threshold on noise. The outcome is decided by ZEROS,
   which do not depend on a threshold.

3. MARKERS ARE VERIFIED ON ONE PAGE, NOT ACROSS THE CORPUS. Counting a marker as
   confirmed if it occurs anywhere was tried and refuted by the same run: the
   city gave 2 pages out of 16 and BOTH were unrelated — a news item about a
   different company, a list of regions in a ranking. Words of the question
   scattered over unconnected pages confirm anything. The single page where the
   name and the city met was the company's own card.

4. FALLING BACK TO LOOSE PARSING OF THE MODEL'S PLAN IS DELIBERATE. Models
   regularly answer in the wrong shape: wrapped in ```json, with a preamble, a
   bare array instead of an object. A wave without queries is a silent zero
   sources, indistinguishable from "nothing was found".

5. QUERIES GO IN A QUEUE, NOT A FAN-OUT. What breaks is not our machine (6% of
   one core at forty concurrent) but ACCESS TO SOMEONE ELSE'S SEARCH ENGINES:
   refusals start at ten concurrent. Only page fetching may be parallelised —
   those are ordinary sites, not search engines.
"""
from __future__ import annotations

import json
import os
import re
import time

CONTRACT_NAME = "ag.deep/2"

# Wave ceilings. Not quality thresholds — cost boundaries.
MAX_QUERIES = 8
PAGES_PER_QUERY = 4
PER_DOMAIN_CAP = 2            # else one verbose site eats the whole wave
MAX_WAVES = 3
WAVE_BUDGET_S = 240.0

_CHARS_FROM_PAGE = 6000
# HOW MUCH TEXT WE TAKE FROM THE READER. More than we hand to the model, and
# deliberately so: from a long document we need a WINDOW AROUND THE MARKERS, and
# picking it requires having the document whole.
#
# THE HEAD OF A DOCUMENT IS A POOR APPROXIMATION TO ITS CONTENT. A regulator
# filing runs to 151 553 characters and begins with 8 100 characters of XBRL
# metadata; the figures sit far past any small cut. Taking only the head makes a
# primary source occupy a slot in the corpus and return noise — the answer gets
# WORSE for having found the better source.
READ_CHARS = int(os.environ.get("DEEP_READ_CHARS") or "60000")


def _window_at_markers(text: str, markers: list[str], size: int) -> str:
    """A window AROUND THE MARKERS, not from the top of the document.

    The head of a document is a poor approximation of its content: a filing has
    metadata there, an article has navigation, a forum has a header. Taking the
    first N characters means taking them merely because they are first.

    The place is chosen by marker DENSITY — and by the number of DISTINCT markers
    first of all. Not by first occurrence: on a long page the name also stands in
    the navigation and in the footer, while the place we want is where the
    markers come TOGETHER.
    """
    if not text or len(text) <= size:
        return text
    low = text.lower()
    spots: list[int] = []
    # A CEILING PER MARKER, NOT PER LIST. A shared ceiling of 200 positions was
    # consumed by the FIRST frequent marker, and the rest were never collected at
    # all — so the window missed in exactly the case it exists for: a company
    # name stands in a filing's header a hundred times while the year and the
    # metric sit in a table far below.
    for num, mark in enumerate(markers or []):
        pth = str(mark).lower()
        if not pth:
            continue
        n, own_n = 0, 0
        while own_n < 200:
            i = low.find(pth, n)
            if i < 0:
                break
            spots.append((i, num))
            own_n += 1
            n = i + len(pth)
    if not spots:
        return text[:size]      # no markers: nothing to aim at, take the head
    spots.sort()
    # COUNT DISTINCT MARKERS, NOT REPEATS OF ONE. A company name repeated 250
    # times in a header IS the densest cluster by occurrence count, and a window
    # chosen that way lands on the header — no ceiling on how much text is
    # collected changes that, because the SELECTION RULE is what decides.
    #
    # The place we want is where markers COME TOGETHER — precisely what
    # `_markers_together` checks. Ranking by occurrence count pulls in the opposite
    # direction from the very test the corpus is judged by.
    #
    # Distinct markers are primary; total occurrences break ties only.
    #
    # DENSITY IS MEASURED ON THE WINDOW THAT WILL BE RETURNED. Searching the
    # cluster over [start, start+size) while returning [start-size/4, …) cuts the
    # last quarter of whatever was found, ALWAYS. Stepping back to catch a label
    # on the left is right, but it must be paid for out of the size, not out of
    # the chosen cluster.
    lead = size // 4
    core = size - lead          # this much of the cluster actually fits
    best, best_score = spots[0][0], (0, 0)
    for start, _ in spots:
        inside = [n for pth, n in spots if start <= pth < start + core]
        score = (len(set(inside)), len(inside))
        if score > best_score:
            best, best_score = start, score
    began = max(0, best - lead)
    return text[began:began + size]
# The window within which markers count as having met TOGETHER. Markers of one
# subject stand close to each other — in a card, a header, a set of details;
# spread across the ends of a long document they confirm adjacency, not identity.
#
# THE NUMBER IS CHOSEN, NOT MEASURED, and saying so is the point: 1500 characters
# is about a screen of text — a card with details, an infobox, an opening
# paragraph. No measurement fixes it, and none is claimed. What WOULD move it is a
# count of misses on real pages: too narrow shows up as `off_target` on documents
# where the subject is genuinely described, too wide as a confident answer
# assembled out of two different subjects standing on one page.
TOGETHER_WINDOW = 1500


def _ask_plan(question: str, model_client, already: list[str]) -> dict:
    """The model composes the queries and the DISTINGUISHING MARKERS.

    The prompt forbids inventing markers, and firmly, for a reason: without that
    ban the model adds plausible ones, and the check starts confirming itself.
    """
    was = ("\n\nALREADY ASKED (do not repeat): " + "; ".join(already)) if already else ""
    prompt = (
        "You are drawing up a web-search plan. Return STRICTLY JSON, no prose:\n"
        '{"queries": ["...", "..."], "markers": ["...", "..."]}\n\n'
        "queries — from 3 to 8 DIFFERENT search queries which together cover "
        "the question from several sides: the basics, recent data and dates, "
        "disputed points, practice.\n"
        "IF THE QUESTION IS ABOUT AN ORGANISATION\u0027S FIGURES — revenue, "
        "profit, market share, headcount — you MUST add a query leading to a "
        "PRIMARY SOURCE: the annual report, a regulatory filing (10-K, 20-F, "
        "annual report, e-disclosure), a press release by the organisation "
        "itself. A retelling of a retelling does not always agree with the "
        "primary source, and where it disagrees it agrees with itself.\n\n"
        "markers — from 2 to 5 DISTINGUISHING FEATURES showing that a page is "
        "about THE SUBJECT ASKED ABOUT and not about a namesake: a proper name, "
        "a city, a year, a number, a model. Short, one or two words each.\n"
        "DO NOT INVENT features that are not in the question: an invented "
        "feature makes the check confirm itself.\n"
        "IF THE QUESTION IS NOT ABOUT A SPECIFIC OBJECT that could be confused "
        "with a namesake — about a concept, a technology, how something works in "
        "general — return markers as an EMPTY LIST. An empty list is honester "
        "than invented features.\n\n"
        f"QUESTION: {question}{was}")
    r = model_client.call([{"role": "user", "content": prompt}], max_out=1200)
    if not r["ok"]:
        return {"queries": [], "markers": [], "usage": r.get("usage") or {},
                "error": r["error"]}
    plan = _parse_plan(r["content"], question)
    plan["usage"] = r["usage"]
    plan["error"] = ""
    return plan


def _parse_plan(text: str, question: str) -> dict:
    """From strict parsing to loose, with THE QUESTION ITSELF as the last resort.

    A wave must not be lost to the shape of an answer: a wave without queries
    gives a silent zero sources, indistinguishable from "nothing was found".
    """
    raw = text.strip()
    # ```json ... ``` and other wrappers
    m = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    # an object somewhere inside a preamble
    if not raw.startswith(("{", "[")):
        m = re.search(r"[{\[].*[}\]]", raw, re.S)
        if m:
            raw = m.group(0)
    try:
        d = json.loads(raw)
    except Exception:  # noqa: BLE001
        d = None
    queries, markers = [], []
    if isinstance(d, dict):
        queries = [str(x).strip() for x in (d.get("queries") or []) if str(x).strip()]
        markers = [str(x).strip() for x in (d.get("markers") or []) if str(x).strip()]
    elif isinstance(d, list):
        # A BARE ARRAY: these are queries, there are no markers. The marker
        # list stays HONESTLY EMPTY — inventing markers would mean confirming
        # ourselves, while an empty list yields the outcome "not verified", which
        # is more honest than "found".
        queries = [str(x).strip() for x in d if str(x).strip()]
    if not queries:
        # LAST RESORT: the question itself. The wave runs worse, but it runs.
        queries = [question]
    return {"queries": queries[:MAX_QUERIES], "markers": markers[:5],
            "bad_format": not isinstance(d, dict)}


def _markers_together(text: str, markers: list[str]) -> bool:
    """Did ALL markers meet on ONE page, and close to each other.

    THIS IS THE PRIMARY CHECK OF THE WHOLE DEEP SEARCH, and it was paid for by a
    measurement. Counting a marker as confirmed when it occurs anywhere in the
    corpus was tried and refuted: a city marker covered 2 pages out of 16, and
    BOTH were unrelated — a news item about a different company and a list of
    regions in a ranking. Words of the question scattered over unconnected pages
    confirm anything at all.

    Co-occurrence does not admit such scatter: in the same run the single page
    where the name and the city met was the card of the company being asked about.

    The window is needed on top of co-occurrence: in a long document a city in
    the header and a name in the footer confirm adjacency, not identity.
    """
    if not markers:
        return False
    low = text.lower()
    positions = []
    for mark in markers:
        i = low.find(str(mark).lower())
        if i < 0:
            return False        # one missing is enough: they did not meet
        positions.append(i)
    return (max(positions) - min(positions)) <= TOGETHER_WINDOW


# --- Markers taken from the question itself ---------------------------------
# A FALLBACK, AND IT IS NOT DECORATIVE. The model returns markers in about HALF
# of questions. On the other half everything standing on markers collapses — zero
# on-topic by construction, markers never meeting by construction, namesake
# separation left without footing. And the answer is still produced, substantial
# and confident.
#
# MARKERS ARE THE SINGLE POINT OF FAILURE OF THE WHOLE QUALITY APPARATUS.
# Without them the tool silently degenerates into "just an answer",
# indistinguishable from an answer that passed verification.
#
# When it happens is visible too: where proper names and versions stand in the
# question text, the model returns them; where there is one name and a generic
# verb, it does not. The model supplies markers when they are already in plain
# sight.
#
# Mechanical extraction is worse than the model's: it knows no synonyms and will
# not guess that a short name and a full legal name are one subject. But it is
# NOT EMPTY, and emptiness switches the verification off entirely.
# LATIN WORDS ARE TAKEN ONLY WHEN CAPITALISED. Taking ANY Latin word of three
# letters or more turned "What is the revenue of Tesla in 2025" into markers
# ['What','the','revenue'] — that is, into a check that confirms anything.
#
# This path supplies markers in half of all questions, so a defect here is a
# defect in half the mechanism, not a matter of taste.
_LATIN_RE = re.compile(r"\b[A-Z][A-Za-z0-9\.\-]{2,}\b")
_WITH_DIGIT_RE = re.compile(r"\b[A-Za-zА-Яа-яЁё]*\d[\w\.\-]*\b")
_CAPITALISED_RE = re.compile(r"\b[А-ЯЁ][а-яё\-]{2,}\b")
# Words a question opens with are not markers. The list is short and closed on
# purpose: a long stop-word dictionary would need maintaining and catches no more.
_NOT_A_MARKER = {"Когда", "Какие", "Какая", "Какой", "Какое", "Кто", "Что",
               "Чем", "Где", "Почему", "Зачем", "Сколько", "Как", "Компания",
               "Организация", "Фирма",
               # English: a Latin-script question is capitalised the same way,
               # and its first word must not become a marker.
               "What", "When", "Where", "Which", "Who", "Why", "How",
               "Does", "Did", "Is", "Are", "Can", "Should", "The", "This",
               "Company", "Compare"}


def _markers_from_question(question: str) -> list[str]:
    """Proper names, versions and numbers taken from the question. No model."""
    found: list[str] = []
    for chunk in (_LATIN_RE.findall(question) + _WITH_DIGIT_RE.findall(question)
                  + _CAPITALISED_RE.findall(question)):
        k = chunk.strip(".,;:!?()[]«»\"'")
        if len(k) < 3 or k in _NOT_A_MARKER or k in found:
            continue
        found.append(k)
    # INFLECTION. In an inflected language the form used in a question is
    # rarely the form printed on the page, so an exact-substring check would fail
    # for no good reason. Two trailing characters are dropped from long Cyrillic
    # words, which makes the stem match every case ending. Latin words and
    # versions are left alone: they have no endings, and trimming would ruin
    # something like "K2.6".
    #
    # The trick is crude and is left VISIBLE in the markers rather than hidden
    # inside the comparison: a reader of the answer must see WHAT was searched
    # for.
    result = []
    for k in found[:3]:
        if len(k) >= 7 and re.fullmatch(r"[А-Яа-яЁё\-]+", k):
            k = k[:-2]
        result.append(k)
    return result


# --- Disagreement between sources --------------------------------------------
# TWO RUNS OF ONE QUESTION CAN NAME DIFFERENT DATES, each with citations and a
# confident tone. Not only completeness varies — THE ANSWER DOES.
#
# The cause is that the sources genuinely disagree: an announcement and general
# availability fall on different days, so there are two correct answers. A run
# either reports the disagreement or quietly takes a side, and that variation is
# more dangerous than varying completeness: a reader handed one date with three
# citations has no reason to doubt it.
#
# WHY THE MECHANISM IS THE SAME AS FOR AMBIGUITY AND THE OUTCOME IS NOT. There,
# DIFFERENT subjects share one name; here, ONE subject carries different claims.
# Neither may be collapsed — but `outcome` answers the question "did we find
# pages about the right subject", and that answer is still yes. So disagreement
# is a separate field and the FIRST LINE of the answer, not a sixth outcome
# value. Same lesson as with empty markers: honesty hidden in a field does not
# work.
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})\s+(январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|"
    r"октябр|ноябр|декабр)\w*\s+(\d{4})", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTHS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август",
        "сентябр", "октябр", "ноябр", "декабр")
# How close to a marker a date must stand to count as being about the subject.
# The window is narrow DELIBERATELY: a page carries many dates — the article's
# own, a previous version's, one in the footer — and a wide window would assemble
# a "disagreement" out of things that are not one.
#
# 300 characters is roughly a sentence with its neighbours — chosen for that,
# measured against nothing. The failure direction is what makes a guess acceptable
# here: too narrow loses a disagreement that exists, and a lost disagreement leaves
# the answer as it was, while an invented one is put in front of the reader as
# news. The variable makes it movable without a rebuild.
DATE_WINDOW = int(os.environ.get("DEEP_DATE_WINDOW") or "300")

# PRIMARY SOURCES. On a question about a company's revenue our figure was right,
# yet the regulator filing it comes from was not among our sources at all.
#
# The mark is MECHANICAL, by domain, and that is its value: it depends neither on
# a model nor on how a site describes itself. A regulator or a disclosure system
# is not "a more authoritative site" but the place where the document is
# PRIMARY — everyone else retells it, and when they diverge from it they agree
# with each other.
_PRIMARY_DOMAINS = ("sec.gov", "e-disclosure.ru", "disclosure.skrin.ru",
                   "nalog.gov.ru", "rkn.gov.ru", "cbr.ru", "gks.ru",
                   "rosstat.gov.ru", "eur-lex.europa.eu", "europa.eu",
                   "investor.gov", "sedar.com", "companieshouse.gov.uk")


def _is_primary_source(domain: str) -> bool:
    """A disclosure or regulator domain. Matched strictly by name suffix, never
    by substring: `notsec.gov.example.com` contains `sec.gov` and is not one."""
    d = (domain or "").lower().strip(".")
    return any(d == m or d.endswith("." + m) for m in _PRIMARY_DOMAINS)
# TWO SOURCES PER VERSION, NOT ONE.
#
# At two, the mechanism fires in 1 run out of 5 and fires cleanly: it names the
# two genuine dates, the announcement and the release. Firing rarely looks like
# the problem, and a minority version seems to deserve a single source, since the
# reader sees the count beside it anyway.
#
# At one, over the same five runs it fires in 4 out of 5 and names RUBBISH in ALL
# FOUR — an unrelated release date as the leading version, with article dates,
# neighbouring versions and footer dates as the minority.
#
# THE CONCLUSION RUNS AGAINST THE INTENTION THAT PROMPTED THE CHANGE: A RARE
# HONEST ALARM BEATS A FREQUENT DIRTY ONE. A guard that shouts at nothing gets
# switched off entirely — together with the cases it exists for.
#
# Hence the direction for later work: recall is raised by precision of
# extraction, not by the threshold — a date next to a word about release rather
# than merely next to a name.
SOURCES_PER_VERSION = 2


def _dates_near_markers(text: str, markers: list[str]) -> set:
    """Normalised dates standing near the markers. No model involved."""
    low = (text or "").lower()
    spots = []
    for mark in markers:
        n = 0
        pth = str(mark).lower()
        while True:
            i = low.find(pth, n)
            if i < 0:
                break
            spots.append(i)
            n = i + 1
            if len(spots) > 40:      # a long page must not cost a minute
                break
    if not spots:
        return set()
    found = set()
    for m in _DAY_MONTH_RE.finditer(text or ""):
        if any(abs(m.start() - pth) <= DATE_WINDOW for pth in spots):
            month = m.group(2).lower()
            num = next((k + 1 for k, x in enumerate(_MONTHS) if month.startswith(x)), 0)
            if num:
                found.add(f"{int(m.group(3)):04d}-{num:02d}-{int(m.group(1)):02d}")
    for m in _ISO.finditer(text or ""):
        if any(abs(m.start() - pth) <= DATE_WINDOW for pth in spots):
            found.add(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return found


def _conflicts_of(sources: list[dict], markers: list[str]) -> list[dict]:
    """Versions of one fact named by different sources. No model involved.

    Only DATES are compared today. Numbers and names disagree just as often, but
    extracting them without a model is noisy, and a noisy guard that raises an
    alarm at nothing gets switched off entirely. A date is the case where
    mechanical extraction is reliable, so it is where this starts.
    """
    if not markers:
        return []
    by_value: dict[str, list[str]] = {}
    for ent in sources:
        if not (ent.get("chars") or 0):
            continue
        for d in _dates_near_markers(ent.get("text") or "", markers):
            by_value.setdefault(d, [])
            if ent.get("url") not in by_value[d]:
                by_value[d].append(ent.get("url", ""))
    versions = [{"value": d, "sources": srcs, "sources_count": len(srcs)}
              for d, srcs in by_value.items()
              if len(srcs) >= SOURCES_PER_VERSION]
    if len(versions) < 2:
        return []
    versions.sort(key=lambda wave: (-wave["sources_count"], wave["value"]))
    return versions[:4]


# --- Ambiguity ---------------------------------------------------------------
# ONE NAME CAN BELONG TO FOUR DIFFERENT SUBJECTS AT ONCE — a retailer, an
# investigations platform, a maker of enclosures, a maker of portable power. An
# answer that picks the one with three domains against one for the others looks
# like corroboration working and is in fact a choice of the MOST INDEXED namesake:
# the other three do not stop existing because of it.
#
# ON AN AMBIGUOUS QUESTION THE CORRECT ANSWER IS "THERE ARE FOUR", NOT "IT IS
# THIS ONE". A mechanism that collapses ambiguity into one variant by domain
# weight produces confident untruth — the familiar failure, except the source of
# confidence is a counter rather than a model.
#
# WHY THE DETECTOR IS MECHANICAL AND NOT A MODEL. Asking a model "are these
# namesakes?" appoints as judge the very side that collapses: it will gladly name
# one subject, because a coherent answer looks better than a list. So the
# SEPARATION is computed from the texts, without a model, and the model is called
# AFTERWARDS and only to NAME the groups already found. Separating and naming are
# different jobs, and the side inclined to collapse must not do the first.
#
# The measures are deliberately coarse: precise clustering is not needed here,
# the question is only "one cluster or several".
_WORD_RE = re.compile(r"[\w\-]{4,}", re.UNICODE)
# Similarity of two pages: share of shared words measured against the SMALLER
# vocabulary — asymmetric containment, the same device used for engine families,
# so that a short page is not called dissimilar merely for being short.
#
# 0.12 IS A CHOSEN NUMBER AND HAS NO MEASUREMENT BEHIND IT — unlike the engine
# families, where the threshold was picked off a distribution over 508 pairs.
# Here the question asked of it is coarse ("one cluster or several"), and pages
# about different subjects share the general vocabulary of a language, which is
# why the line sits low rather than near a half. It is a variable so that it can
# be moved on evidence; if that evidence is ever collected, the number belongs in
# a measurement and this comment must say so.
SIMILARITY_THRESHOLD = float(os.environ.get("DEEP_SIMILARITY") or "0.12")
# Fewer than two non-empty pages is not a group but a lone page. A single
# outlier is normal in any corpus and cannot carry the claim "there are several".
GROUP_MIN = 2


def _words(text: str, except_: list[str]) -> set:
    """A page vocabulary with the marker words removed.

    Removing markers is MANDATORY: by construction they occur on every page, and
    leaving them in would make namesakes look similar by the very thing that
    confuses them — the shared name.
    """
    drop = set()
    for mark in except_:
        drop |= {c.lower() for c in _WORD_RE.findall(str(mark))}
    return {c.lower() for c in _WORD_RE.findall(text or "")} - drop


def _similarity(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _is_question_structure(sources: list[dict], groups: list[list[int]],
                       markers: list[str]) -> bool:
    """Whether the groups fall onto DIFFERENT markers of the question. No model.

    THE SECOND PRECONDITION, AND IT REMOVES A WHOLE CLASS OF FALSE ALARMS FOR
    FREE. A comparison question ("how does A differ from B") yields two groups by
    construction — exactly the two sides the question itself named. That is the
    structure of the question, not ambiguity of a subject.

    RULE: if the groups found correspond to entities ALREADY NAMED in the
    question, it is question structure and not ambiguity.

    Checked by matching: for each group take the markers present on the majority
    of its pages. Groups split across different markers mean the question named
    several subjects itself, and there is nothing to discover.
    """
    if len(markers) < 2 or len(groups) < 2:
        return False
    sets = []
    for grp in groups[:3]:
        own = set()
        for mark in markers:
            has = sum(1 for i in grp
                       if str(mark).lower() in (sources[i].get("text") or "").lower())
            if has * 2 > len(grp):        # marker present on most pages of the group
                own.add(mark)
        sets.append(own)
    non_empty = [n for n in sets if n]
    if len(non_empty) < 2:
        return False
    # Different marker sets per group mean the question decomposed, not namesakes.
    return any(a != b for a in non_empty for b in non_empty)


def _namesake_groups(sources: list[dict], markers: list[str]) -> list[list[int]]:
    """Split the sources into groups that are "about the same thing". No model.

    Connectivity, not clustering: two pages share a group if they are similar
    directly or through a chain. That is exactly what the question "one cluster
    or several" needs, and exactly what does not require choosing the number of
    groups in advance.
    """
    on_topic = [i for i, ent in enumerate(sources)
              if (ent.get("chars") or 0) > 0 and ent.get("text")]
    if len(on_topic) < GROUP_MIN * 2:
        return []
    vocabs = {i: _words(sources[i].get("text") or "", markers) for i in on_topic}
    parent = {i: i for i in on_topic}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pi, i in enumerate(on_topic):
        for j in on_topic[pi + 1:]:
            if _similarity(vocabs[i], vocabs[j]) >= SIMILARITY_THRESHOLD:
                ri, rj = root(i), root(j)
                if ri != rj:
                    parent[ri] = rj
    by_root: dict = {}
    for i in on_topic:
        by_root.setdefault(root(i), []).append(i)
    groups = [g for g in by_root.values() if len(g) >= GROUP_MIN]
    groups.sort(key=len, reverse=True)
    return groups if len(groups) >= 2 else []


def _outcome(sources: list[dict], markers: list[str]) -> str:
    """found | off_target | not_found | unknown. Decided by ZEROES.

    There are NO numeric thresholds here, and that is a decision rather than an
    omission: two runs of the same question differed twofold in corpus size
    (237932 characters against 190275, 203613 against 99087). A threshold taken
    from one measurement would be a threshold on noise.

    Zeroes do not depend on that spread:
      · no non-empty source at all         -> not_found;
      · the model gave no markers          -> unknown ("not checked", which is
        HONESTER than declaring "found" over an unverified corpus);
      · markers matched on no page at all  -> off_target.

    The third outcome is mandatory. Without it namesakes read as success: the
    corpus is full, the character count is high, and every word of it is about a
    different company in a different city.

    ON GENERAL QUESTIONS. A question about a concept ("what is the HTTP
    protocol") comes out as `off_target` — "material found, but about ANOTHER
    subject". That is untrue: a concept has no namesakes, there is nobody to
    confuse it with. The marker mechanism exists to TELL ENTITIES APART, and on a
    general question it has nothing to tell apart. The cure is not here but in
    the prompt: the model is told to return an EMPTY marker list when the
    question is not about a specific object. The outcome is then `unknown` —
    "there was nothing to check with" — which is true, unlike `off_target`.
    """
    non_empty_n = [ent for ent in sources if (ent.get("chars") or 0) > 0]
    if not non_empty_n:
        return "not_found"
    if not markers:
        return "unknown"
    return "found" if any(ent.get("markers_hit") for ent in non_empty_n) else "off_target"


def _wave(question: str, plan: dict, search_fn, read_many, deadline: float) -> dict:
    """One wave: ask the engines, read the pages, check the markers.

    SEARCHES GO IN A QUEUE, NOT IN A FAN. What breaks is not our machine — at
    forty concurrent searches it was busy on 6% of one core — but ACCESS TO
    SOMEBODY ELSE'S ENGINES: refusals begin at ten concurrent, at forty three
    engines out of five went silent, and recovery takes minutes. The more waves
    there are, the stronger the temptation to parallelise, and the higher the
    price of doing it.

    READING, HOWEVER, DOES PARALLELISE. Measured: reading eats 72-76% of the
    whole run, and the twenty pages of a corpus sit on nearly twenty DIFFERENT
    domains, so they do not compete with each other for the per-domain rate
    limiter. The limiter argument holds for one domain and fails for twenty.

    HENCE THE ORDER: ALL searches first (they are cheap, 11-13 s for five
    queries), then ALL reads in a single parallel pass. An interleaved
    search-read-search-read order would save memory and cost minutes.
    """
    markers = plan.get("markers") or []
    sources, links_seen, per_domain = [], set(), {}
    queries_asked, links_found, picked = [], 0, []
    failed_queries, empty_queries = [], []

    for query in plan.get("queries") or []:
        if time.time() > deadline:
            break
        # CORROBORATION IS A PROPERTY HERE, NOT A CALLER'S FLAG. A tool that
        # builds an ANSWER rather than handing back links is obliged to know how
        # many independent sources that answer stands on.
        #
        # Without it, a corpus of sixteen sources can arrive entirely by the
        # cheap path — one engine per query — holding exactly as much
        # independence as two engines gave it, with nothing showing that.
        # Tolerable for a list of links, not for an answer.
        #
        # The price is honest and high: several times more outbound calls. That
        # is why it is paid here and not in ordinary search.
        out_set = search_fn(query, PAGES_PER_QUERY * 2, True)
        queries_asked.append(query)
        if not out_set.get("ok"):
            # A FAILED QUERY IS NAMED, IT DOES NOT VANISH INTO A `continue`.
            # The query is already recorded in `queries_asked`; the reader sees
            # eight queries and cannot see that some of them never happened —
            # a report of work that was not done.
            failed_queries.append({"query": query,
                             "reason": out_set.get("error") or "search failed"})
            continue
        # AN ENGINE ABORT INSIDE A SUCCESSFUL QUERY IS RECORDED TOO. Search
        # answers `ok: true` with a partial result set and names the abort in a
        # field of its own. Looking only at `ok` here reports "every query
        # succeeded" — a field raised by search that never reaches the report.
        if out_set.get("search_aborted"):
            failed_queries.append({"query": query, "reason": out_set["search_aborted"],
                             "partial": True})
        if not (out_set.get("results") or []):
            # An empty result set is NOT a failure: we asked and found nothing.
            # But in the report it must be distinguishable from a query that did
            # return links.
            empty_queries.append(query)
        taken = 0
        for r in out_set.get("results") or []:
            if taken >= PAGES_PER_QUERY or time.time() > deadline:
                break
            url = r.get("url") or ""
            dom = r.get("domain") or ""
            if not url or url in links_seen:
                continue
            # PER-DOMAIN CAP: otherwise one verbose site eats the whole wave.
            if per_domain.get(dom, 0) >= PER_DOMAIN_CAP:
                continue
            links_seen.add(url)
            per_domain[dom] = per_domain.get(dom, 0) + 1
            links_found += 1
            picked.append(r)
            taken += 1

    pages = read_many([r.get("url") or "" for r in picked], deadline)
    for r, page_res in zip(picked, pages):
        text = (page_res or {}).get("content") or ""
        window = _window_at_markers(text, markers, _CHARS_FROM_PAGE)
        sources.append({
                "url": r.get("url") or "", "domain": r.get("domain") or "",
                "title": (r.get("title") or "")[:200],
                "status": (page_res or {}).get("status", ""),
                "chars": len(text),
                # Whether the markers met ON THIS PAGE — the decisive signal.
                "markers_hit": _markers_together(text, markers),
                "via": r.get("via", ""),
                # HOW MANY INDEPENDENT ENGINES FOUND THIS LINK.
                # A number, not a string of names: independence read off the
                # `via` field by eye gets miscounted. An answer built out of
                # sources needs a NUMBER, otherwise only the reader who goes
                # looking for independence ever sees it.
                #
                # The caveat without which the number lies: more witnesses means
                # "more independent engines found this same page", NOT "this page
                # answers the question better".
                "confirmed_by_engines": int(r.get("corroborated_by_url") or 0),
                # Whether the document is primary or retold. Marked by domain,
                # without a model: disclosure and regulator sites are where a
                # document is PRIMARY.
                "primary_source": _is_primary_source(r.get("domain") or ""),
                "text": window,
                # THREE DIFFERENT LENGTHS, EASY TO CONFUSE. `chars` is what the
                # reader handed us; `chars_total` is what the WHOLE PAGE held,
                # before our own read ceiling; `chars_window` is what went to the
                # model.
                #
                # The true total comes from the reader: it alone knows how long
                # the document was before we asked for 60 000 characters.
                # NO DATA MEANS ZERO PLUS A SEPARATE WORD, NEVER A SUBSTITUTE.
                # A fallback of `or len(text)` here would silently return the
                # exact lie being avoided — `chars_total` equal to `chars` — and
                # would tip the failure direction to the comfortable side.
                "chars_total": int((page_res or {}).get("total_chars") or 0),
                "chars_total_known": (page_res or {}).get("total_chars") is not None,
                "chars_window": len(window),
        })
    return {"sources": sources, "queries_asked": queries_asked,
            "queries_failed": failed_queries, "queries_empty": empty_queries,
            "links_found": links_found,
            "outcome": _outcome(sources, markers)}


def _name_groups(question: str, sources: list[dict], groups: list[list[int]],
                    model_client) -> dict:
    """Give names to groups that have ALREADY been found. The model names here,
    it does not decide.

    The separation was computed mechanically before this call; the model receives
    finished groups and answers "what is each one about", not "how many are
    there". The side inclined to collapse ambiguity is never let near the first
    question.
    """
    chunks = []
    for n, grp in enumerate(groups[:5], 1):
        samples = []
        for i in grp[:2]:
            ent = sources[i]
            samples.append(f"{ent.get('url','')}\n{(ent.get('text') or '')[:900]}")
        chunks.append(f"GROUP {n}:\n" + "\n---\n".join(samples))
    prompt = (
        "The pages are already split into groups by content. Do two things.\n"
        "1. For EACH group, say what subject it is about — an organisation, a "
        "product, a concept: what it is and how it differs from the others.\n"
        "2. Say whether this is ONE subject seen from different sides or "
        "DIFFERENT subjects that happen to share a name.\n"
        "Answer with JSON ONLY, of the form {\"same_subject\":true,"
        "\"groups\":[{\"n\":1,\"name\":\"...\",\"distinguisher\":\"...\"}]}\n"
        "Set same_subject=true if the groups describe THE SAME THING: different "
        "facets of one concept, different shops selling one product, the two "
        "sides of a comparison asked for in the question itself, different news "
        "about one organisation.\n"
        "Set same_subject=false ONLY if one name hides DIFFERENT subjects — "
        "different companies, different products by different makers, a concept "
        "and a company of the same name.\n"
        "Rules: name is a short name of the subject; distinguisher is one phrase "
        "that keeps it from being confused with the other groups (country, "
        "industry, owner, purpose). Do not merge groups and do not invent "
        "subjects that are not in them.\n\n"
        f"USER QUESTION: {question}\n\n" + "\n\n".join(chunks))
    r = model_client.call([{"role": "user", "content": prompt}], max_out=800)
    names: dict = {}
    # ONE SUBJECT UNTIL PROVEN OTHERWISE. The failure direction here is chosen
    # DELIBERATELY and needs explaining, because it is the opposite of the one
    # used everywhere else in this module.
    #
    # Over ten questions the mechanical detector fires on four and at least three
    # of those are FALSE — a question about a concept (three groups!), a
    # comparison question (the two sides named in the question), a price
    # comparison (different shops). Splitting by content finds "pages about
    # different things", and that happens for legitimate reasons far more often
    # than it happens because of namesakes.
    #
    # So the model gets a VETO, not a vote: it cannot create ambiguity — the
    # groups were found before it and without it — but it can withdraw a false
    # one. A wrong veto returns the behaviour to what it is without the detector,
    # no worse. That is the difference between "the model decides" and "the model
    # rejects".
    same_subject = True
    if r.get("ok"):
        try:
            raw_val = r["content"]
            began = raw_val.find("{")
            fin = raw_val.rfind("}")
            d = json.loads(raw_val[began:fin + 1]) if began >= 0 else {}
            same_subject = bool(d.get("same_subject", True))
            for g in (d.get("groups") or []):
                names[int(g.get("n") or 0)] = {
                    "name": str(g.get("name") or "")[:120],
                    "distinguisher": str(g.get("distinguisher") or "")[:200]}
        except Exception:  # noqa: BLE001
            names = {}
    variants = []
    for n, grp in enumerate(groups[:5], 1):
        sub = names.get(n) or {}
        variants.append({
            "name": sub.get("name") or f"variant {n}",
            "distinguisher": sub.get("distinguisher") or "",
            # THE NAMES MAY NOT HAVE ARRIVED, AND THAT DOES NOT CANCEL THE
            # FINDING: the groups were found mechanically, and "there are several"
            # holds even with no names for them.
            "named_by_model": bool(sub.get("name")),
            "sources": [sources[i].get("url", "") for i in grp],
            "sources_count": len(grp)})
    return {"variants": variants, "same_subject": same_subject,
            "usage": r.get("usage") or {},
            "error": "" if r.get("ok") else r.get("error", "")}


def _pick_for_digest(sources: list[dict]) -> list[dict]:
    """Which sources the model is actually shown. Separated out for a reason.

    Detecting conflicts over one set of sources while a different set goes into
    the prompt lets a version named by the eighth source reach the `conflicts`
    field while the model never sees it — and the prompt still demands "name ALL
    versions with their source numbers".

    **A requirement that cannot be met is worse than no requirement**: it forces
    the model to invent — either attribute a version to the wrong number, or say
    "the sources do not contain it" while the first line of the answer promises
    the reader the opposite.
    """
    on_topic = [ent for ent in sources if ent.get("markers_hit") and ent.get("chars")]
    fallback = [ent for ent in sources if ent.get("chars")][:3]
    return on_topic[:6] or fallback


def _digest(question: str, sources: list[dict], model_client,
            markers: list[str] | None = None,
            conflicts: list[dict] | None = None) -> dict:
    """Digest what was read into an answer, using only pages where the markers met.

    Digesting the whole corpus is not allowed: a quarter of it tends to be
    namesakes, and a model handed unrelated pages will faithfully retell
    unrelated pages.
    """
    on_topic = [ent for ent in sources if ent.get("markers_hit") and ent.get("chars")]
    take = _pick_for_digest(sources)
    # A FALLBACK PATH IS DECLARED IN THE ANSWER ITSELF, NOT ONLY IN A FIELD
    # BESIDE IT. A run can produce `sources_on_target: 0` together with a
    # detailed, confident answer. The accompanying boolean honestly says false,
    # but what people read is `answer`, and it looks exactly as sure of itself as
    # a verified one.
    #
    # Either the field counts something other than what it names, or the answer
    # is built on sources the tool itself does not consider on topic. The second
    # case is worse: then `sources_on_target` is not a measure of the answer at
    # all, merely a number standing next to it.
    #
    # The caveat goes on the FIRST LINE: a note at the end of a long answer is
    # not read by anyone who took the first paragraph.
    caveat = ""
    if not markers:
        # NO MARKERS AT ALL — the heaviest caveat, and it goes on the first
        # line. Honesty hidden in a field instead of the first line is honesty
        # nobody reads: to notice `markers: []` you must already know which
        # field to look at.
        caveat = ("> WARNING: THE CHECK DID NOT RUN. No markers of the subject "
                    "could be extracted, neither by the model nor from the text "
                    "of the question, so there was nothing to verify that the "
                    "sources are about THE SUBJECT ASKED ABOUT. The answer below "
                    "is a retelling of what was found, with no verification at "
                    "all.\n\n")
    elif not on_topic:
        caveat = ("> WARNING: digested from sources that are NOT on target. The "
                    "markers of the subject met on no page at all, and the answer "
                    "below is assembled from whatever was found. Treat it as a "
                    "draft.\n\n")
    if conflicts and "SOURCES DISAGREE" not in caveat:
        # FIRST LINE, AND IN OUR OWN WORDS. If the model fails to obey the
        # demand below, the reader still sees that there are several versions —
        # because we write the caveat, not the model.
        listing = ", ".join(
            f"{wave['value']} ({wave['sources_count']} src)" for wave in conflicts)
        # THIS ADDS TO THE PREVIOUS CAVEAT, IT DOES NOT REPLACE IT. An `elif`
        # here kept the disagreement caveat out of the text whenever there were
        # no on-target sources, even with `conflicts` filled in. The two warnings
        # are not alternatives: a corpus can be off target and discordant at once.
        caveat += ("> WARNING: SOURCES DISAGREE. About the subject of the "
                     f"question they give different dates: {listing}. The answer "
                     "below is required to name every version; if it names one, "
                     "do not trust it — look at the conflicts field.\n\n")
    if not take:
        return {"answer": "", "usage": {}, "error": "nothing to digest: empty corpus"}
    chunks = []
    for i, ent in enumerate(take, 1):
        chunks.append(f"[{i}] {ent['url']}\n{ent['text'][:3000]}")
    # DISAGREEMENT IS PASSED TO THE MODEL AS A DEMAND, NOT AS A QUESTION. Asking
    # "is there a contradiction here?" would hand the decision to the side that
    # prefers a coherent answer; we found the disagreement ourselves,
    # mechanically, and order it to name EVERY version. Compliance is checkable:
    # the values sit beside the answer in `conflicts`, and the reader can see
    # whether all of them made it in.
    demand = ""
    if conflicts:
        enum = "; ".join(
            f"{wave['value']} ({wave['sources_count']} sources)" for wave in conflicts)
        demand = (
            "\n\nWARNING, THE SOURCES DISAGREE. Different dates are given for "
            f"the subject of the question: {enum}. Name EVERY version with its "
            "source numbers and, if the text shows it, what distinguishes them "
            "(announcement, release, start of sales). Do NOT pick one by "
            "majority and do not keep silent about the rest.")
    prompt = (
        "Answer the question from the sources below. Rules:\n"
        "- rely ONLY on the sources, do not fill in from your own knowledge;\n"
        "- follow every statement with its source number in square brackets;\n"
        "- if the sources do not answer the question, say so instead of "
        "retelling their content;\n"
        "- quote numbers, dates and names verbatim.\n\n"
        f"QUESTION: {question}{demand}\n\nSOURCES:\n" + "\n\n".join(chunks))
    r = model_client.call([{"role": "user", "content": prompt}], max_out=2000)
    # A TRUNCATED ANSWER IS DECLARED, ALSO ON THE FIRST LINE. Text that hit the
    # token ceiling ends mid-sentence yet looks like a finished answer, and its
    # last paragraph reads as a conclusion, which it is not.
    if r.get("truncated") is True:
        caveat += ("> WARNING: THE ANSWER IS CUT OFF at the length ceiling. It "
                     "was not written to the end — the last thought may be "
                     "unfinished, and some sources may not be covered at all."
                     "\n\n")
    elif r.get("truncated") is None and r.get("ok"):
        # THE PROVIDER DID NOT SAY — so we say that. Silence about a flag and a
        # flag saying "complete" are different pieces of news, and the second is
        # reassuring for no reason.
        caveat += ("> The model provider did not report whether the answer was "
                     "written to the end. If the last thought breaks off, that "
                     "may be the length ceiling rather than the end of the "
                     "reasoning.\n\n")
    return {"answer": (caveat + r["content"]) if r["ok"] else "",
            # Three states, not two: True, False and None — "not reported".
            "truncated": r.get("truncated"),
            "usage": r.get("usage") or {},
            "error": "" if r["ok"] else r["error"],
            "digested_from": len(take),
            "basis": ("sources on target" if on_topic else
                          "no markers extracted: there was no check" if not markers
                          else "sources with no marker match"),
            "from_on_target": bool(on_topic)}


def _blank_answer(question: str, reason: str, **extra) -> dict:
    """The full field set on the failure path too, as in every contract here."""
    return {"contract": CONTRACT_NAME, "ok": False, "error": reason,
            "question": question, "answer": "", "outcome": "not_found",
            "waves_done": 0, "waves_max": 0, "stopped_because": reason,
            "queries_asked": [], "queries_failed": [], "queries_empty": [],
            "markers": [], "markers_from": "",
            "sources": [], "sources_total": 0, "sources_on_target": 0,
            "ambiguity": None, "ambiguity_check": "", "answer_basis": "",
            "conflicts": [], "conflicts_note": "", "answer_truncated": False,
            "sources_with_content": 0, "sources_confirmed_2plus": 0,
            "sources_primary": 0,
            "confirmation_note": "",
            "timing_ms": {"search_ms": 0, "read_ms": 0, "model_ms": 0},
            "usage": {"model_calls": 0, "input_tokens": 0, "output_tokens": 0,
                      "cost_usd": None, "cost_source": "", "by_model": [],
                      "ledger_written": False, "ledger_note": ""},
            "elapsed_ms": None, **extra}


def _as_int(what, default: int) -> int:
    """An int out of anything. Rubbish and emptiness give the default, not a raise."""
    try:
        n = int(str(what).strip())
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _as_float(what, default: float) -> float:
    try:
        n = float(str(what).strip())
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def deep_search(question: str, search_fn, read_many, model_client,
                waves: int = 0, budget_s: float = 0) -> dict:
    """Deep search. Contract ag.deep/2. Never raises.

    THE DEPTH IS CHOSEN BY US BUT DECLARED IN THE ANSWER. "Depending on the
    question" means the number of waves is our decision; therefore the caller is
    owed a view of WHAT was decided — how many waves ran, on what signal we
    stopped, what was left unverified. Otherwise a full answer is
    indistinguishable from a short one, and that indistinguishability is exactly
    what this module exists to remove.

    THE TIME CEILING. A model that goes off reasoning takes a run past any
    caller's reaper — 1200 s is reachable. Several waves in a row hit time before
    they hit quality, so each wave has its own budget, and on exhaustion a PARTIAL
    result is returned with the reason named rather than everything being lost.
    """
    began = time.time()
    question = (question or "").strip()
    if not question:
        return _blank_answer("", "empty question")
    may, why = model_client.available()
    if not may:
        # Without a model there is no deep search. We say so plainly instead of
        # handing back ordinary search dressed as deep: substituting the cheap
        # path is the same defect as a silent fallback to our own parsers.
        return _blank_answer(question, f"model unavailable: {why}")

    # NUMBERS ARE COERCED, NOT TRUSTED. Callers hand arguments over as STRINGS,
    # and an uncoerced `int("abc")` raises ValueError out of a function whose
    # docstring says "never raises". From outside that is a dropped connection —
    # a broken service instead of "that argument will not do".
    #
    # The other half of the same point: the string "0" is TRUTHY, so `waves or
    # MAX_WAVES` yielded zero where MCP yielded three. One entry point quietly
    # ran a single wave against three. Coercion removes that too: empty and zero
    # both mean "use the default".
    waves = max(1, min(MAX_WAVES, _as_int(waves, MAX_WAVES)))
    budget = float(_as_float(budget_s, WAVE_BUDGET_S))
    # SPEND ACCOUNTING. The shape of these fields is taken wholesale from the
    # caller's own ledger, so that a dashboard adds up TWO SPENDS rather than two
    # different schemas: there are now two places where money is spent.
    usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0,
              "cost_usd": None, "cost_source": "", "ledger_written": False,
              # `ledger_written` IS NOT DECORATION. The precedent it exists for:
              # a vision call returned usage and wrote it nowhere — eleven calls
              # went past the accounts and nobody noticed, because silence from
              # the ledger is indistinguishable from no calls at all. The flag is
              # set BY THE FACT of the write, never by the intention: true means
              # "the lines are in the ledger", false means "the money was spent
              # and recorded nowhere", and those are different news.
              "ledger_note": "",
              # A BREAKDOWN BY MODEL, NOT ONE HEAP OF TOKENS. The price registry
              # must not be duplicated here, so the module owes the caller
              # everything a price is computed from — the model NAME beside its
              # tokens. Total tokens without names cannot be turned into money:
              # the text and vision models are on different tariffs, and one sum
              # silently mixes the expensive with the cheap.
              "by_model": []}

    def account(u: dict) -> None:
        if not u:
            return
        usage["model_calls"] += 1
        tokens_in = int(u.get("input_tokens") or 0)
        tokens_out = int(u.get("output_tokens") or 0)
        usage["input_tokens"] += tokens_in
        usage["output_tokens"] += tokens_out
        usage["cost_source"] = u.get("cost_source", "")
        # All calls were recorded or not all were — "partly recorded" is not a
        # separate outcome: for accounting it is the same as not recorded.
        if not u.get("ledger_written"):
            usage["ledger_written"] = False
            usage["ledger_note"] = ("some calls did not reach the ledger: check "
                                     "SPEND_LEDGER and the file permissions")
        elif usage["ledger_note"] == "":
            usage["ledger_written"] = True
        if u.get("cost_usd") is not None:
            usage["cost_usd"] = round((usage["cost_usd"] or 0) + u["cost_usd"], 6)
        name = u.get("model") or "?"
        for line in usage["by_model"]:
            if line["model"] == name:
                break
        else:
            line = {"model": name, "provider": u.get("provider") or "",
                      "calls": 0, "input_tokens": 0, "output_tokens": 0,
                      "cached_tokens": 0, "cached_reported": False}
            usage["by_model"].append(line)
        line["calls"] += 1
        line["input_tokens"] += tokens_in
        line["output_tokens"] += tokens_out
        # None means the provider does not report cache hits; adding it as zero
        # would turn "not reported" into "the cache did not work".
        if u.get("cached_tokens") is not None:
            line["cached_tokens"] += int(u["cached_tokens"])
            line["cached_reported"] = True

    # TIME, BROKEN DOWN. A deep run takes 78-118 s against five seconds for a
    # plain search, and the only useful form of that number is a decomposition:
    # how much went on searching, how much on reading, how much on the model.
    #
    # It is counted HERE, not guessed from logs: a total with no breakdown can be
    # explained by any one of the three causes, and the explanation will sound
    # convincing whether or not it is true.
    timing = {"search_ms": 0, "read_ms": 0, "model_ms": 0}

    def add_time(to_where: str, was: float) -> None:
        timing[to_where] += int((time.time() - was) * 1000)

    def search_timed(query, n, corrob_flag=False):
        t = time.time()
        try:
            return search_fn(query, n, corrob_flag)
        finally:
            add_time("search_ms", t)

    def read_many_timed(url_list, deadline=0):
        t = time.time()
        try:
            return read_many(url_list, deadline)
        finally:
            add_time("read_ms", t)

    all_sources, all_queries, markers = [], [], []
    failed_all, empty_all = [], []
    markers_from, done_n, stop_reason = "", 0, ""
    for num in range(waves):
        deadline = time.time() + budget
        _t = time.time()
        plan = _ask_plan(question, model_client, all_queries)
        add_time("model_ms", _t)
        account(plan.get("usage"))
        if plan.get("error"):
            stop_reason = f"wave {num + 1}: no plan — {plan['error']}"
            break
        if not markers:
            markers = plan.get("markers") or []
            markers_from = "model"
            if not markers:
                # THE MODEL GAVE NO MARKERS — TAKE THEM FROM THE QUESTION
                # rather than carry on with nothing. Empty markers switch the
                # whole verification apparatus off silently: zero on-target by
                # construction, namesake separation with nothing to stand on, and
                # a confident answer regardless.
                markers = _markers_from_question(question)
                markers_from = ("question (the model gave no markers)" if markers
                                   else "no markers: neither the model nor the "
                                        "question gave any — THE CHECK DOES NOT "
                                        "RUN")
        plan["markers"] = markers
        wave = _wave(question, plan, search_timed, read_many_timed, deadline)
        done_n += 1
        all_queries.extend(wave["queries_asked"])
        failed_all.extend(wave.get("queries_failed") or [])
        empty_all.extend(wave.get("queries_empty") or [])
        all_sources.extend(wave["sources"])
        # STOPPING ON ZEROES, not on volume: see `_outcome`.
        if_found = any(ent.get("markers_hit") for ent in all_sources)
        if if_found:
            stop_reason = "the markers met on a page — no reason to search further"
            break
        if time.time() > deadline:
            stop_reason = f"the wave budget ran out ({budget:.0f} s)"
            break
        stop_reason = "waves exhausted, the markers met nowhere"

    # AMBIGUITY IS CHECKED BEFORE THE DIGEST, NOT AFTER. The order matters: a
    # digested answer has already chosen a subject, and asking it afterwards
    # "were there perhaps several?" is asking the side that just chose.
    #
    # FIRST PRECONDITION: NO NAME, NOTHING TO CHECK. Ambiguity is a property of a
    # NAME, not of a result set: one name with several different bearers. A
    # question like a price comparison has no markers and no name at all — yet
    # the splitter still produced THREE groups (different shops).
    #
    # Same rule as everywhere else here: a probe that returns zero must first
    # show that its subject existed.
    groups = _namesake_groups(all_sources, markers) if markers else []
    ambiguity, names = None, {}
    # WHAT EXACTLY HAPPENED TO THE CHECK, IN WORDS. `ambiguity: null` covers
    # three different events: not attempted, attempted and found nothing, found
    # and vetoed. One field for three events is the very indistinguishability
    # this module hunts everywhere else.
    ambiguity_check = ("not run: the question names no subject, there is "
                             "nothing for ambiguity to apply to"
                             if not markers else
                             "no split by content was found"
                             if not groups else "")
    if groups and _is_question_structure(all_sources, groups, markers):
        # SECOND PRECONDITION, also before the model: the groups fell onto
        # different markers of the question itself. That is the question
        # decomposing, not namesakes.
        ambiguity_check = (
            f"not ambiguity: {len(groups)} group(s) fell onto different markers "
            "of the question itself — that is how the question is built, not "
            "what the search returned")
        groups = []
    if groups:
        _t = time.time()
        names = _name_groups(question, all_sources, groups, model_client)
        add_time("model_ms", _t)
        account(names.get("usage"))
        ambiguity_check = f"found {len(groups)} group(s) by content"
    if groups and names.get("same_subject"):
        ambiguity_check = (
            f"found {len(groups)} group(s) by content, but it is ONE subject "
            "seen from different sides — not counted as ambiguity")
        # The model exercised its veto: there are groups, but they are about the
        # same thing. Digest over the WHOLE corpus, as if there had been no
        # split — otherwise a comparison question would be answered from one side
        # of the comparison only.
        groups = []
    if groups:
        ambiguity = {
            "groups": len(groups),
            "variants": names["variants"],
            "detected_by": ("split by page content without a model; the model "
                            "confirmed that the subjects are different"),
            "note": ("the pages of this corpus describe DIFFERENT subjects under "
                     "one name. The answer below is built from the largest group "
                     "and applies to that group alone — the other variants are "
                     "real and are listed here"),
        }

    _t = time.time()
    # Digest over the LARGEST group rather than the whole corpus: mixing
    # namesakes gives an answer glued together out of two different subjects,
    # which is worse than an answer about one of them with an honest caveat that
    # there are several.
    for_digest = ([all_sources[i] for i in groups[0]] if groups
                  else all_sources)
    # DISAGREEMENT IS SOUGHT OVER THE SAME PAGES THE MODEL WILL SEE. Over the
    # whole corpus it would find contradictions between namesakes; over a
    # different set it would produce a demand that cannot be met — "name every
    # version" while half the versions are not shown.
    shown = _pick_for_digest(for_digest)
    conflicts = _conflicts_of(shown, markers)
    digest = _digest(question, for_digest, model_client, markers, conflicts)
    add_time("model_ms", _t)
    account(digest.get("usage"))
    outcome = "ambiguous" if groups else _outcome(all_sources, markers)
    ours = [{k: v for k, v in ent.items() if k != "text"} for ent in all_sources]
    return {
        "contract": CONTRACT_NAME, "ok": True, "error": "",
        "question": question,
        "answer": digest.get("answer", ""),
        # The outcome is not found/not-found but THREE different events plus
        # "not verified".
        "outcome": outcome,
        "waves_done": done_n, "waves_max": waves,
        "stopped_because": stop_reason,
        "queries_asked": all_queries,
        # THREE FATES FOR A QUERY, AND THEY MUST NOT BE CONFLATED: it returned
        # links, it ran and found nothing, it never happened. A single
        # `queries_asked` count showed eight queries where half had failed.
        "queries_failed": failed_all,
        "queries_empty": empty_all,
        "markers": markers, "markers_from": markers_from,
        "sources": ours,
        # THREE DIFFERENT COUNTS, AND ALL THREE ARE SHOWN. Of sixteen sources,
        # five can be empty — blocked pages of six characters, a redirect, a
        # paywall — standing in the list beside the real ones, so that "16 sources"
        # sounds four times more convincing than "10 on target".
        #
        # A SOURCE WITH NO CONTENT IS NOT A SOURCE. But it must not be discarded
        # either: "found and could not read" is news about blocks and about our
        # own address, and it disappears if only one number is kept.
        "sources_total": len(ours),
        "sources_with_content": sum(1 for ent in ours if (ent.get("chars") or 0) > 0),
        "sources_on_target": sum(1 for ent in ours if ent.get("markers_hit")),
        # How many sources are primary. Zero on a question about an
        # organisation's figures means the answer stands entirely on retellings.
        #
        # And how many are corroborated by more than one engine: an answer built
        # from lone pages and an answer built from pages three engines found
        # independently carry different weight, and the caller cannot otherwise
        # see the difference.
        "sources_primary": sum(1 for ent in ours if ent.get("primary_source")),
        "sources_confirmed_2plus": sum(
            1 for ent in ours if (ent.get("confirmed_by_engines") or 0) >= 2),
        "confirmation_note": (
            "confirmed_by_engines is how many independent engines found THIS SAME "
            "link. It is NOT a measure of correctness: what gets corroborated "
            "more often is the better indexed source, not the truer one"),
        "summarised_from_on_target": digest_from_on_topic(digest),
        # The same thing in words rather than as a flag: a flag is easy to miss,
        # and an answer built from unverified pages must say so about itself.
        "answer_basis": digest.get("basis", ""),
        # Whether the answer was written to the end. A field beside the caveat
        # in the text: one for a person, one for a caller that branches on fields.
        "answer_truncated": digest.get("truncated"),
        # EMPTY IS NOT "CHECKED AND UNAMBIGUOUS" BUT "NO SPLIT WAS FOUND". The
        # same difference as between "not checked" and "sound", and it must not
        # be blurred: a corpus of three pages is too small to look for a split
        # at all.
        "ambiguity": ambiguity,
        "ambiguity_check": ambiguity_check,
        # VERSIONS OF ONE FACT AS GIVEN BY DIFFERENT SOURCES. An empty list
        # means "looked and found no disagreement"; absent markers mean there was
        # nothing to look beside, and that is visible in `markers`.
        "conflicts": conflicts,
        "conflicts_note": (
            "the sources give different values for one fact. This is NOT an error "
            "on their part: announcement and release, plan and outcome are "
            "different events under one question. The answer is required to name "
            "every version rather than pick one by majority"
            if conflicts else ""),
        "usage": usage,
        "elapsed_ms": int((time.time() - began) * 1000),
        # WHERE THE TIME WENT. The three add up to less than the total: the
        # remainder is parsing, digesting and our own work between the steps.
        "timing_ms": timing,
    }


def digest_from_on_topic(digest: dict) -> bool:
    """Whether the answer was digested from pages where the markers MET.

    Exposed as a field of its own: an answer digested from unverified pages looks
    exactly like one digested from verified pages, and this flag is the only way
    the caller can tell them apart.
    """
    return bool(digest.get("from_on_target"))
