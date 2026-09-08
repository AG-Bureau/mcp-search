# -*- coding: utf-8 -*-
"""Reading a page by address — the module's second capability, contract ag.read/2.

WHY IT IS SEPARATE FROM SEARCH. The costs are incomparable and invisible to the
caller: a search is 0.2-1 s and one outbound request, a read is seconds and
megabytes, and the browser stage is up to a minute and a half. An argument like
`read_results: true` on web_search would make the cost of a call unpredictable.
Second: search failures arrive per ENGINE, reading failures per ADDRESS; merged
into one response they produce exactly the blind spot ag.search/2 was written
against.

WHAT WAS MEASURED, and why the code looks like this. A corpus of 194 addresses
taken from our own results: 141 HTML, 27 PDF, 16 network failures. 40 of the 141
HTML pages (28%) return fewer than 1200 characters to a plain request — and ALL
FORTY turned out to be a block, a JS application or an error, not one real page;
the shortest real page was 1212 characters. A tool that cannot tell those 28%
from an empty page lies to its caller in one read out of four. Hence `_stub`.

The same corpus disproved the common belief that certain publishers can only be
read with a browser: two of the three named ones returned 16781 and 7367
characters to a plain request. One was shut tight (401 and 290 bytes of an
anti-bot shield). So the browser is a RARE escalation over a few per cent of
addresses, not the main path.

THE MOST EXPENSIVE THING FOUND WAS NOT BLOCKS BUT OUR OWN ADDRESS: some 30
requests to one site within half an hour walked it 200 -> 429 -> total silence,
and it stayed silent for us while answering another machine in 0.22 s. One link
read without a rate limiter takes a source away from every future read. That is
why the rate limit here is PER DOMAIN, not per path and not per process.

HTML IS PARSED ON THE STANDARD LIBRARY, and the price is honest and named in the
contract: tables come out of html.parser worse than out of lxml. What that buys
is one less library to keep updated on the path where every page from the outside
world arrives.

It does NOT mean the module has no dependencies — that sentence outlived its
subject. The image carries four (pypdf, pypdfium2, pillow, playwright) and weighs
419 MB; each is taken on a measurement and argued for in adapter/Dockerfile.
"""
from __future__ import annotations

import gzip
import html as _html
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser

import model      # the model client: vision for scans
import render     # rendering PDF pages to PNG

CONTRACT = "ag.read/2"

# --- Thresholds and policy ---------------------------------------------------
# Everything below is MODULE policy, not a caller's argument. Search is built the
# same way: the caller says WHAT to read, while how often and how long we are
# willing to do it is our knowledge about our own addresses, and the caller has
# nothing to change it with.

MAX_URLS = 5          # batch ceiling: five addresses in sequence already take
                          # minutes, and an MCP client will not wait that long
DEFAULT_CHARS = 20000
MIN_CHARS, MAX_CHARS = 1000, 200_000
TIMEOUT_S = 20.0          # per address
WHOLE_CALL_S = 90.0       # for the whole call; past it, "did not get through"
                          # rather than a hang
DOWNLOAD_CEILING = 2 * 1024 * 1024   # 2 MB of HTML: an article is never larger,
                                     # a data dump is, and we cannot handle it
# PDF HAS ITS OWN, LARGER CEILING. An ordinary specification runs to ~3 MB, so
# the 2 MB HTML ceiling truncates it, and a truncated PDF makes the parser say
# "Stream has ended unexpectedly": A TRUNCATED FILE LOOKS BROKEN.
PDF_CEILING = int(os.environ.get("PDF_MAX_BYTES") or str(30 * 1024 * 1024))
THIN_CHARS = 1200              # "suspiciously little text": below it every page
                               # in the corpus was a block, an app or an error
CACHE_S = 300.0             # how long a read result lives
CACHE_ENTRIES = 64

# RATE LIMIT PER DOMAIN. A second per engine is enough for search: an engine
# expects requests, that is its job. An ordinary site expects nothing, and the
# punishment for haste is not a refusal on one request but the loss of a source
# for good (see the header).
DOMAIN_INTERVAL_S = 2.0
DOMAIN_WAIT_MAX_S = 8.0

# Headers. We introduce ourselves honestly: a site we are bothering must be able
# to say so instead of banning us silently. We do not pretend to be a browser:
# getting around a shield is the operator's decision, not the module's default.
#
# THE DEFAULT CONTACT IS THE CODE, NOT A COMPANY. An administrator reading their
# log and deciding whether to ban us needs to see WHAT is walking their site. A
# company site answers "complain to us about somebody else's crawling" — the
# operator running this is somebody else. A repository answers "here is the code
# that is walking you", which is the thing being judged, and it is the same answer
# whoever runs the module.
#
# The operator's own address goes in READ_CONTACT and replaces it: whoever answers
# for the traffic should be the one named in it.
#
# THE VERSION IS THE REAL ONE. A bare `/1` is internal numbering that matches no
# release, and an administrator cannot tell from it which build they are looking at.
#
# THE LANGUAGE IS NOT OURS TO CHOOSE. A hard-wired Accept-Language decides which
# language the web answers in, on behalf of an operator who was never asked; the
# default is therefore no preference at all. Setting it is a documented example,
# not a default.
VERSION = "0.2.1"
READ_HOME = "https://github.com/AG-Bureau/mcp-search"
READ_CONTACT = (os.environ.get("READ_CONTACT") or "").strip() or READ_HOME
READ_LANGUAGES = (os.environ.get("READ_LANGUAGES") or "").strip()
HTTP_HEADERS = {
    "User-Agent": f"ag-mod-search/{VERSION} (+{READ_CONTACT})",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}
if READ_LANGUAGES:
    HTTP_HEADERS["Accept-Language"] = READ_LANGUAGES

# Shield markers. Not a "smart detector" but a list of markers, and it errs in
# both directions — it is held honest by a NEGATIVE reference in the prober (a
# page that certainly does not exist; if rejection lets it through, the reference
# goes red).
_SHIELD_MARKS = (
    "qrator", "servicepipe", "ddos-guard", "incapsula", "cloudflare",
    "just a moment", "checking your browser", "проверка браузера",
    "enable javascript", "включите javascript", "captcha", "капча",
    "attention required", "access denied", "доступ ограничен",
    "are you a robot", "являетесь ли вы роботом",
)
# "No such page" markers — sought ONLY at the start of the text: a real page may
# mention "page not found" somewhere in the middle, a stub says it immediately.
_NO_SUCH_PAGE = (
    "публикация недоступна", "недоступна для просмотра",
    "страница не найдена", "страница не существует", "нет такой страницы",
    "page not found", "not found", "404", "ничего не найдено",
    "материал недоступен", "удалена или перемещена",
)
_NO_SUCH_PAGE_WINDOW = 400
# Paywall markers. isAccessibleForFree in Schema.org markup is exact, the rest
# are approximate, which is why a paywall only counts together with a shortage of
# text.
_PAYWALL_MARKS = (
    '"isaccessibleforfree":false', '"isaccessibleforfree": false',
    "оформите подписку", "продолжить чтение", "subscribe to continue",
    "for subscribers only", "только для подписчиков",
)
# Search-engine result pages. Reading them is forbidden outright: web_search has
# already returned the snippets, and parsing somebody else's result page as "a
# page" once handed a caller "facts" about a namesake scraped out of a SERP.
_SERP_HOSTS = re.compile(
    r"^(www\.)?("
    r"google\.[a-z.]+|bing\.com|duckduckgo\.com|yandex\.[a-z]+|search\.marcia|"
    r"baidu\.com|search\.brave\.com|lite\.duckduckgo\.com|searx|mojeek\.com|"
    r"startpage\.com|ecosia\.org|qwant\.com|imgur\.com|search\.yahoo\.com"
    r")$")
_SERP_PATHS = ("/search", "/images/search", "/web", "/?q=", "/s")

_TEXT_TYPES = ("text/", "application/json", "application/xml",
                "application/xhtml", "+xml", "application/javascript")

# --- Rate limiting -----------------------------------------------------------
_rate_lock = threading.Lock()
_domain_last: dict[str, float] = {}


def _wait_domain(domain: str, deadline: float) -> bool:
    """Wait for our turn at a domain. False means the wait ran out of time.

    We wait INSIDE the call rather than refusing at once: five links from one
    publisher is an ordinary case, and refusing the fourth would be worse than
    waiting eight seconds. But waiting without limit is not allowed either: the
    caller cannot tell a long wait from a hung network.
    """
    limit = min(time.time() + DOMAIN_WAIT_MAX_S, deadline)
    while True:
        now = time.time()
        with _rate_lock:
            elapsed = now - _domain_last.get(domain, 0.0)
            if elapsed >= DOMAIN_INTERVAL_S:
                _domain_last[domain] = now
                return True
            wait = DOMAIN_INTERVAL_S - elapsed
        if now + wait > limit:
            return False
        time.sleep(min(wait, 0.25))


def _reset_rate() -> None:
    """Forget the request history. Needed by the tests — without it a second run
    in a row gets "did not get through" on every address and the test becomes
    order-dependent."""
    with _rate_lock:
        _domain_last.clear()


# --- Cache -------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _from_cache(key: str) -> dict | None:
    with _cache_lock:
        z = _cache.get(key)
        if not z:
            return None
        when, what = z
        if time.time() - when > CACHE_S:
            _cache.pop(key, None)
            return None
        return dict(what)


def _to_cache(key: str, what: dict) -> None:
    with _cache_lock:
        if len(_cache) >= CACHE_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[key] = (time.time(), dict(what))


def _reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --- HTML parsing ------------------------------------------------------------
_DROP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe",
             "form", "button", "select", "canvas", "video", "audio"}
# Page furniture: menus, footers, sidebars. Dropped by tag — on the standard
# library that is all one can do honestly.
_CHROME_TAGS = {"nav", "header", "footer", "aside"}
_BLOCK_TAGS = {"p", "div", "section", "article", "br", "tr", "li", "dd", "dt",
            "blockquote", "pre", "table", "hr", "figcaption", "main"}
_H_TAGS = {"h1": "#", "h2": "##", "h3": "###",
                "h4": "####", "h5": "#####", "h6": "######"}


class _HtmlText(HTMLParser):
    """One pass: text, markdown, links, metadata.

    One pass rather than three, and not for speed: three passes are three places
    where "the main content" is decided differently, and one day they diverge.
    """

    def __init__(self, db: str) -> None:
        super().__init__(convert_charrefs=True)
        self.db = db
        self.chunks: list[str] = []      # for format=text
        self.mdl: list[str] = []         # for format=markdown
        self.links: list[dict] = []
        self.title = ""
        self.canonical = ""
        self.published = ""
        self.lang = ""
        self._noise_depth = 0
        self._from_title = False
        self._from_h = ""
        self._LINK_RE: tuple[str, list[str]] | None = None
        self._from_li = False
        self._jsonld: list[str] = []
        self._from_jsonld = False

    # -- internals
    def _text_of(self, s: str) -> None:
        if not s.strip():
            return
        self.chunks.append(s)
        if self._LINK_RE is not None:
            self._LINK_RE[1].append(s)
        self.mdl.append(s)

    def _BREAK_RE(self, what: str = "\n") -> None:
        if self.chunks and not self.chunks[-1].endswith("\n"):
            self.chunks.append("\n")
        if self.mdl and not self.mdl[-1].endswith("\n"):
            self.mdl.append(what)

    # -- HTMLParser
    def handle_starttag(self, tag, attrs):  # noqa: D102
        a = dict(attrs)
        if tag in _DROP_TAGS or tag in _CHROME_TAGS:
            self._noise_depth += 1
            if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
                self._from_jsonld = True
            return
        if tag == "html":
            self.lang = (a.get("lang") or "").strip()[:16]
        elif tag == "title":
            self._from_title = True
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower():
            self.canonical = urllib.parse.urljoin(self.db, (a.get("href") or "").strip())
        elif tag == "meta":
            name = (a.get("property") or a.get("name") or "").lower()
            if name in ("article:published_time", "datepublished", "pubdate",
                       "date", "og:published_time") and not self.published:
                self.published = (a.get("content") or "").strip()[:64]
        if self._noise_depth:
            return
        if tag in _H_TAGS:
            self._BREAK_RE("\n\n")
            self._from_h = tag
            self.mdl.append(_H_TAGS[tag] + " ")
        elif tag == "a":
            href = urllib.parse.urljoin(self.db, (a.get("href") or "").strip())
            if href.startswith(("http://", "https://")):
                self._LINK_RE = (href, [])
                self.mdl.append("[")
        elif tag == "li":
            self._BREAK_RE()
            self.mdl.append("- ")
            self._from_li = True
        elif tag in ("td", "th"):
            self.chunks.append(" ")
            self.mdl.append(" | ")
        elif tag in _BLOCK_TAGS:
            self._BREAK_RE("\n\n" if tag in ("p", "div", "section", "article") else "\n")

    def handle_endtag(self, tag):  # noqa: D102
        if tag in _DROP_TAGS or tag in _CHROME_TAGS:
            self._noise_depth = max(0, self._noise_depth - 1)
            if tag == "script":
                self._from_jsonld = False
            return
        if tag == "title":
            self._from_title = False
        if self._noise_depth:
            return
        if tag in _H_TAGS:
            self._from_h = ""
            self._BREAK_RE("\n\n")
        elif tag == "a" and self._LINK_RE is not None:
            href, parts = self._LINK_RE
            caption = re.sub(r"\s+", " ", "".join(parts)).strip()
            self.links.append({"url": href, "text": caption[:200]})
            self.mdl.append(f"]({href})")
            self._LINK_RE = None
        elif tag == "li":
            self._from_li = False
            self._BREAK_RE()
        elif tag in _BLOCK_TAGS:
            self._BREAK_RE("\n\n" if tag in ("p", "div", "section", "article") else "\n")

    def handle_data(self, data):  # noqa: D102
        if self._from_jsonld:
            self._jsonld.append(data)
            return
        if self._from_title:
            self.title += data
            return
        if self._noise_depth:
            return
        self._text_of(data)

    # -- assembly
    def collect(self) -> tuple[str, str]:
        text = re.sub(r"[ \t\xa0]+", " ", "".join(self.chunks))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        mdl = re.sub(r"[ \t\xa0]+", " ", "".join(self.mdl))
        mdl = re.sub(r"\n{3,}", "\n\n", mdl).strip()
        return text, mdl

    def from_jsonld(self) -> None:
        """Publication date from Schema.org markup, when meta carried none."""
        if self.published or not self._jsonld:
            return
        for chunk in self._jsonld[:8]:
            try:
                data = json.loads(chunk)
            except Exception:  # noqa: BLE001
                continue
            stack = [data]
            while stack:
                is_ = stack.pop()
                if isinstance(is_, dict):
                    for key in ("datePublished", "dateCreated", "uploadDate"):
                        if isinstance(is_.get(key), str) and not self.published:
                            self.published = is_[key][:64]
                            return
                    stack.extend(is_.values())
                elif isinstance(is_, list):
                    stack.extend(is_)


def _charset(kind: str, raw: bytes) -> str:
    """Response encoding: header -> meta -> utf-8 -> cp1251.

    The defect class is mojibake: a legacy single-byte encoding read as utf-8.
    The error is silent — there is text, the length is plausible, and there is no
    content.
    """
    m = re.search(r"charset=([\w\-]+)", kind or "", re.I)
    if m:
        return m.group(1)
    m = re.search(rb'charset=["\']?([\w\-]+)', raw[:4096], re.I)
    if m:
        return m.group(1).decode("ascii", "replace")
    try:
        raw[:20000].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1251"


def _decode(raw: bytes, kind: str) -> str:
    name = _charset(kind, raw)
    try:
        return raw.decode(name, "replace")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


# --- PDF ---------------------------------------------------------------------
# The only place where the module steps outside the standard library, and the
# argument is a measurement rather than a preference: a stdlib parser gives
# something unreadable on four files out of five, and 41 characters instead of
# 52 858 on a document with font subsets (measures/2026-09-06-MEASURE-pdf-
# extraction.md).
try:
    import pypdf
    HAS_PDF = True
except Exception:  # noqa: BLE001
    HAS_PDF = False

# How many pages are parsed. The ceiling is not cosmetic: a thousand-page PDF
# takes minutes to parse while the caller is waiting seconds. Pages beyond the
# ceiling do not disappear silently — the answer says so.
MAX_PDF_PAGES = int(os.environ.get("PDF_MAX_PAGES") or "60")

# Below this many characters PER PAGE there is no text layer. The threshold is
# deliberately far below any real density (a live document runs 640-2400
# characters per page): title and divider pages are legitimately almost empty, and
# a higher threshold would call a real document a scan.
CHARS_PER_PAGE = 20

# A glyph name instead of a letter. A font with no ToUnicode map yields
# `/uni041C`, and substitution fixes it for free: one catalogue held 131 of them,
# and none after substitution. The alternative on offer was 27 MB of fontTools,
# which changed nothing at all on the same file.
_GLYPH_RE = re.compile(r"/uni([0-9A-Fa-f]{4})")


def _from_pdf(body: bytes) -> tuple[str, dict]:
    """(text, details). Never raises: somebody else's format breaks in many ways."""
    details = {"pages": None, "pages_parsed": 0, "truncated_by_pages": False}
    if not HAS_PDF:
        return "", {**details, "error": "the PDF parser is not installed in the image"}
    import io
    try:
        pdf_reader = pypdf.PdfReader(io.BytesIO(body))
        total = len(pdf_reader.pages)
        details["pages"] = total
        chunks = []
        for page_res in pdf_reader.pages[:MAX_PDF_PAGES]:
            try:
                chunks.append(page_res.extract_text() or "")
            except Exception:  # noqa: BLE001
                chunks.append("")   # one broken page does not cancel the rest
        details["pages_parsed"] = min(total, MAX_PDF_PAGES)
        details["truncated_by_pages"] = total > MAX_PDF_PAGES
    except Exception as e:  # noqa: BLE001
        return "", {**details, "error": f"{type(e).__name__}: {e}"[:200]}
    text = _GLYPH_RE.sub(lambda m: chr(int(m.group(1), 16)), "\n".join(chunks))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip(), details

# --- Scan recognition --------------------------------------------------------
# THE THIRD WAY TO GET TEXT, after the plain download and the browser.
#
# The page window is deliberately small, and that is arithmetic rather than
# caution: the more pages in one call, the fewer bytes each of them gets, and the
# lower the dpi settles — silently. Asking for many pages at once means quietly
# degrading the resolution.
OCR_MAX_PAGES = int(os.environ.get("OCR_MAX_PAGES") or "6")
# Its own timeout: recognition takes SECONDS, not milliseconds — 17 pages run
# 69 s on a fast vision model and 193 s on a heavier one, so the general model
# timeout would cut a legitimate answer short.
OCR_TIMEOUT_S = float(os.environ.get("OCR_TIMEOUT_S") or "120")


# WHAT A SCAN LOOKS LIKE WHEN NOBODY ASKED FOR RECOGNITION. Not "empty": the
# document exists and is a picture, and the caller is told which call turns it
# into text. See `recognise` in _read_one for why reading recognises and search
# does not.
NOT_RECOGNISED = "not_recognised (scan)"


def _cacheable(done: dict) -> bool:
    """Is this result worth remembering under the address.

    A scan left unrecognised is NOT. It is a refusal that depends on WHO asked —
    search does not pay for a vision model, reading does — and cached under the
    plain address it would answer a later `web_read` with somebody else's refusal.
    """
    return (done["status"] in ("read", "empty")
            and done.get("text_source") != NOT_RECOGNISED)


def _recognise_scan(out: dict, body: bytes, chars: int, page_res: int) -> dict:
    """A scanned PDF -> text. Mutates and returns the answer it was given.

    A FAILURE HERE DOES NOT BECOME "EMPTY". If recognition did not happen — no
    renderer, no key, the model silent — the document is still NOT empty: it
    exists, it is a picture, and we did not read it. The failure-direction rule is
    the module's own: no data means "not checked", never "sound".
    """
    out.update(status="empty", total_chars=chars, content="",
               stub_check="clean", text_source="",
               reason=f"no text layer: {chars} characters over {page_res} pages")
    # WE ASK ABOUT VISION SPECIFICALLY. An operator may have configured a text
    # model and no vision model — providers differ in both price and availability.
    # A scan must then refuse WITH A REASON, rather than call a text model with a
    # picture and collect a vague provider error that reads as a broken service.
    may, why = model.available(vision=True)
    if not render.HAS_RENDERER or not may:
        out["reason"] += ("; recognition was not attempted: "
                          + ("the PDF renderer is not installed in the image"
                             if not render.HAS_RENDERER else why))
        return out

    images, info = render.pages_to_raster(body, OCR_MAX_PAGES)
    # THE RESOLUTION IS ALWAYS RETURNED, even when everything after it broke.
    # Where the step-down happens silently, "the model read it badly" and "we gave
    # it 60 dpi instead of 300" are INDISTINGUISHABLE. This field is the only
    # thing that tells them apart.
    out["recognition"] = {
        "model": model.MODEL_VISION,
        "dpi_requested": info.get("dpi_requested"),
        "dpi_actual": info.get("dpi_actual"),
        "dpi_steps_down": info.get("dpi_steps_down"),
        "pages_rendered": info.get("pages_rendered"),
        "image_format": info.get("format"),
        "pages_total": out.get("pages"),
        "payload_b64_bytes": info.get("payload_b64_bytes"),
        # HIT THE FLOOR AND STILL DID NOT FIT. The renderer computes this; when
        # it is dropped, the images go to the model as they are — knowingly over
        # the ceiling — and the provider's refusal would read as "the model could
        # not cope".
        "payload_over_limit": bool(info.get("over_limit")),
        "fidelity": "recognised, not quoted: a measurement recovered 94% of the "
                    "reference numbers — check figures against the source",
    }
    if not images:
        out["reason"] += f"; pages were not rendered: {info.get('error') or 'no reason given'}"
        return out

    # THE TIMEOUT IS PASSED, NOT MERELY DECLARED. A constant that is written,
    # named in a comment and handed to nobody leaves the call running on the model
    # client's general timeout. Inside a search that means the search could hang on
    # the model for longer than the search is allowed to last in total.
    answer = model.recognise(images, max_out=0,
                             timeout=OCR_TIMEOUT_S)
    if not answer.get("ok"):
        out["reason"] += f"; recognition failed: {answer.get('error')}"
        out["recognition"]["usage"] = answer.get("usage") or {}
        return out
    text = (answer.get("content") or "").strip()
    out["recognition"]["usage"] = answer.get("usage") or {}
    # RECOGNISED TEXT CUT OFF BY THE TOKEN CEILING IS NOT THE WHOLE DOCUMENT. It
    # ends in the middle of a page, while a "read" status says the opposite.
    out["recognition"]["truncated"] = answer.get("truncated")
    if not text:
        out["reason"] += "; the model returned empty text"
        return out

    rendered = info.get("pages_rendered") or 0
    tail = ""
    if (out.get("pages") or 0) > rendered:
        # Unrecognised pages do not disappear silently — by the same rule that
        # keeps pages beyond the parsing ceiling from disappearing.
        tail = (f"the first {rendered} pages of {out.get('pages')} were "
                 f"recognised: we did not look further")
    # `via` IS LEFT ALONE HERE. It answers "how was the FILE obtained" (plain
    # download, browser, cache), and the file was obtained by a plain download.
    # "How was the TEXT obtained" is a different question, and `text_source`
    # answers it. Merging the two questions into one field would also break the
    # summary of fetch paths, which counts transport specifically.
    if answer.get("truncated") is True:
        # A tail on the reason, not a change of status: the page WAS read, just
        # not to the end, and there is no cause to discard what was obtained.
        tail = ((tail + "; ") if tail else "") + (
            "recognition was cut off at the model\u0027s answer-length ceiling: "
            "the text ends in the middle of the document")
    # THE `truncated` FIELD IS NOT TOUCHED HERE. It is already taken by a
    # DIFFERENT event: "we returned less than all the text we have, the rest is
    # fetched with an offset cursor". The two are opposite in meaning — there the
    # text is complete and a slice is shown, here the text is itself incomplete.
    # Merging them would create exactly the indistinguishability this module hunts
    # everywhere else.
    out.update(status="read", content=text, total_chars=len(text),
               pages_read=rendered, text_source="recognised", reason=tail)
    return out


# --- The browser stage -------------------------------------------------------
# Our own sidecar, not a bridge to somebody else's. A build system's browser
# checks the layout of its own builds, ours reads other people's pages — different
# jobs, and on different machines, since docker networks do not stretch between
# hosts.
#
# THE VERSION IS PINNED, AND THE CONNECTION ITSELF VERIFIES IT: a playwright
# `connect()` REFUSES when the minor versions of client and server differ. Two
# independently built browsers would otherwise drift apart SILENTLY — until the
# first refusal in production. Nothing here compares version strings: the
# handshake already did it, and a second comparison would shout at a healthy
# pair (see `_paths_fresh`).
BROWSER_URL = (os.environ.get("BROWSER_WS_URL") or "").strip()
BROWSER_TIMEOUT_MS = int(os.environ.get("BROWSER_TIMEOUT_MS") or "30000")
# How long to wait after load so scripts can finish drawing.
#
# THE NUMBER IS SMALLER THAN THE WORST CASE ON PURPOSE. A page behind a heavy
# anti-bot shield gives up its body 5-7 seconds in, and waiting that long on
# EVERY browser read would cost seconds per page for the few per cent that need
# it. The shield is not lost by the shorter wait: a page still holding the shield
# comes back as `stub`, which is an honest outcome, and `mode: browser` with a
# longer BROWSER_SETTLE_MS is the deliberate second attempt.
BROWSER_SETTLE_MS = int(os.environ.get("BROWSER_SETTLE_MS") or "2500")

try:
    from playwright.sync_api import sync_playwright
    HAS_BROWSER_CLIENT = True
except Exception:  # noqa: BLE001
    HAS_BROWSER_CLIENT = False


def _via_browser(url: str, shot_flag: bool, full_page_flag: bool = False) -> dict:
    """Fetch a page with the browser. Never raises.

    TWO DIFFERENT OUTCOMES, AND THEY MUST NOT BE CONFLATED. A definition of the
    form `ok = check_passed and no error` makes a page that honestly FAILED a
    check return exactly what a browser that never opened returns. Success is then
    named after somebody else's event: the tool counts as working while it has
    never worked once.

    So here:
      · `ok`   — THE FETCH HAPPENED: the browser is alive, the page loaded;
      · `html` / `png` — what exactly was fetched;
      · `error` — why it did not happen, if it did not.
    Judging the content ("is this what we expected") is the caller's job, against
    the caller's own markers, and it is NEVER mixed with the fact of the fetch.
    """
    fallback = {"ok": False, "html": "", "png": b"", "error": "", "version": "",
                "status": None}
    if not BROWSER_URL:
        return dict(fallback, error="the browser path is not wired up to the module")
    if not HAS_BROWSER_CLIENT:
        return dict(fallback, error="the playwright client is not installed in the image")
    try:
        with sync_playwright() as pw:
            own_conn = pw.chromium.connect(BROWSER_URL, timeout=BROWSER_TIMEOUT_MS)
            version = own_conn.version
            page_res = own_conn.new_page()
            try:
                page_response = page_res.goto(url, timeout=BROWSER_TIMEOUT_MS,
                                     wait_until="domcontentloaded")
                # THE BROWSER FOLLOWS REDIRECTS ON ITS OWN AND WE CANNOT FORBID
                # IT. So we check AFTERWARDS: where it ended up. Unchecked, this
                # is the same hole as an unguarded plain download, and more
                # dangerous — the sidecar sits inside its own docker network.
                #
                # This is a LATE check and weaker than an early one: the request
                # to an internal host has already happened. But the content does
                # not leave, and saying so is honester than silence.
                #
                # WE CHECK EXACTLY WHAT WE FEAR, not everything in sight. Calling
                # the full address guard here breaks the health check, which
                # visits `about:blank`: the guard honestly reports "only http and
                # https are supported" and the browser turns into "not
                # responding". A guard that breaks the legitimate gets switched
                # off entirely, together with the useful part.
                end = urllib.parse.urlparse(page_res.url or "")
                if end.scheme in ("http", "https"):
                    if _is_internal(end.hostname or ""):
                        raise RedirectBlocked(
                            "the browser ended up inside the perimeter; the "
                            "content is not returned")
                elif (page_res.url or "") not in ("", "about:blank"):
                    # Not http and not our own blank page: `file:`, `chrome:`
                    # and the like are never ours to read.
                    raise RedirectBlocked(
                        f"the browser left for scheme {end.scheme or '-'!r}")
                page_res.wait_for_timeout(BROWSER_SETTLE_MS)
                html = page_res.content()
                # THE FLAG REACHES THE BROWSER. A literal `full_page=False` here
                # while the door accepts a `full_page` argument and advertises it
                # in the tool schema means the argument is accepted and discarded
                # — a promise that does not exist: the caller sees a shot of the
                # first screen and believes they saw the page.
                png = (page_res.screenshot(full_page=bool(full_page_flag))
                       if shot_flag else b"")
            finally:
                page_res.close()
                own_conn.close()
        return {"ok": True, "html": html, "png": png, "error": "",
                "version": version,
                # THE STATUS CODE COMES FROM THE BROWSER, IT IS NOT INVENTED. A
                # literal 200 here would mean the module asserting something about
                # somebody else's server that it never checked: a page served with
                # 403 behind a shield would arrive as "200, read". None is honester
                # than zero: "the browser did not report a code".
                "status": (page_response.status if page_response is not None else None)}
    except Exception as e:  # noqa: BLE001
        # DIFFERENT CAUSES, DIFFERENT WORDS. "The address is wrong" and "the page
        # did not open" reported as one error teaches nobody anything, however
        # many times it repeats.
        text = f"{type(e).__name__}: {e}"[:300]
        if "invalid URL" in text or "Cannot navigate" in text:
            text = f"the browser rejected the address (check scheme and host): {text}"
        return dict(fallback, error=text)

# --- What must not be read ---------------------------------------------------

def _domain(url: str) -> str:
    """Domain without www. Computed the SAME way as in search (server.py:_domain),
    so that "read the third link" needs no normalisation at the seam."""
    try:
        d = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    return d.split(":")[0].removeprefix("www.")


def _allow_internal() -> bool:
    """Whether addresses inside the perimeter may be read. FOR TESTS ONLY.

    Without this gate the reading tests would have to go to the real internet —
    checking us over somebody else's network, disturbing somebody else's
    measurements and failing on somebody else's refusals. With it a fake server
    lives on 127.0.0.1 and not one request leaves the machine, as in every other
    test in this module.

    READ EVERY TIME, not at import: a test must first check that the ban holds
    with the gate closed and only then open it. A constant captured at import
    would make that order impossible, and the defence would go unverified — and an
    unverified defence is no better than none.

    The gate is absent from the shipped configuration: docker-compose does not set
    it, and a static test verifies that. Enabled in production it would turn
    web_read into a way to ask our container to visit its docker-network
    neighbours.
    """
    return (os.environ.get("READ_ALLOW_INTERNAL") or "") == "1"


def _is_internal(host: str) -> bool:
    """Whether a host name leads inside the perimeter. Checked against the
    RESOLVED address, not the string: a public name may point at 10.0.0.5, and a
    ban by the look of the string lets that through.

    Needed because the address arrives FROM OUTSIDE — out of a search result set,
    chosen neither by an engine nor by us. Without this check web_read becomes a
    way to ask our own container to visit its docker-network neighbours.
    """
    if not host:
        return True
    if _allow_internal():
        return False
    if host.lower() in ("localhost", "searxng", "ag-mod-search"):
        return True
    try:
        details = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001
        return False        # did not resolve — that is "did not open", not "forbidden"
    for family, _t, _p, _c, url in details:
        try:
            ip = ipaddress.ip_address(url[0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def _is_serp(url: str) -> bool:
    """A search-engine result page. Reading it is forbidden.

    Not hygiene but measurement: result pages found their way into a corpus, and
    parsing somebody else's result page as "a page" produced "facts" about a
    namesake — the snippets of neighbouring results sat on the same page as the
    name sought and merged into one text.
    """
    pth = urllib.parse.urlparse(url)
    host = (pth.netloc or "").lower().split(":")[0]
    if not _SERP_HOSTS.match(host):
        return False
    path = (pth.path or "/").rstrip("/") or "/"
    return path.startswith(_SERP_PATHS) or bool(pth.query and "q=" in pth.query)


def _check_url(url: str) -> tuple[str, str]:
    """('', '') if reading is allowed, otherwise (status, reason)."""
    if not isinstance(url, str) or not url.strip():
        return "forbidden", "empty address"
    pth = urllib.parse.urlparse(url.strip())
    if pth.scheme not in ("http", "https"):
        return "forbidden", f"only http and https are supported, not {pth.scheme or '-'!r}"
    if not pth.netloc:
        return "forbidden", "the address has no host name"
    if _is_serp(url):
        return "forbidden", ("this is a search-engine result page; links come from "
                             "web_search, and its content merges neighbouring "
                             "results into one text")
    if _is_internal(pth.hostname or ""):
        return "forbidden", "the address leads inside the perimeter"
    return "", ""


# --- robots.txt --------------------------------------------------------------
# WE REPORT, WE DO NOT FORBID. The reference implementation (mcp fetch) gates an
# autonomous call on robots.txt. That cannot be our default for two reasons:
# whether a client's tool must obey robots is the owner's decision, not the module
# layer's; and the reference rule "401/403 on robots.txt means forbidden" produces
# a false ban on our sources — the same sources return 401 to everyone through an
# anti-bot shield. So the field exists, the action does not, and the question is
# named as open in the contract.
_robots_lock = threading.Lock()
_robots: dict[str, tuple[float, str]] = {}
ROBOTS_CACHE_S = 3600.0


def _robots_verdict(url: str, deadline: float) -> str:
    root = urllib.parse.urlparse(url)
    key = f"{root.scheme}://{root.netloc}"
    with _robots_lock:
        z = _robots.get(key)
        if z and time.time() - z[0] < ROBOTS_CACHE_S:
            rules = z[1]
        else:
            rules = None
    if rules is None:
        if time.time() > deadline - 3:
            return "not_checked"
        try:
            req = urllib.request.Request(key + "/robots.txt", headers=HTTP_HEADERS)
            # THE SAME GUARD AS ON THE PAGE DOWNLOAD. The content of robots is
            # not returned, but a request to an internal host is already an event:
            # it proves the host exists, and it is made at somebody else's
            # direction.
            with _OPENER.open(req, timeout=5) as o:
                rules = o.read(200_000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            rules = ""    # no file, or we were not let in — that is NOT a ban
        with _robots_lock:
            _robots[key] = (time.time(), rules)
    if not rules.strip():
        return "allowed"
    # DECODED paths are compared. The request goes out percent-encoded (otherwise
    # http.client chokes on non-ASCII) while robots.txt is written as-is, and an
    # encoded path never matches a written one. Compare them raw and a site's ban
    # is invisible in the answer: the field stays silent instead of reporting.
    path = urllib.parse.unquote(root.path or "/")
    our, denied = False, []
    for line in rules.splitlines():
        line = line.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        field, _, val = line.partition(":")
        field, val = field.strip().lower(), val.strip()
        if field == "user-agent":
            our = val in ("*", "ag-mod-search")
        elif field == "disallow" and our and val:
            denied.append(urllib.parse.unquote(val))
    for entry in denied:
        if path.startswith(entry):
            return "disallowed_by_site"
    return "allowed"


# --- Rejection ---------------------------------------------------------------

def _stub_check(text: str, raw_low: str, code: int, chars: int) -> tuple[str, str]:
    """("clean" | "looks_like_stub", reason).

    THE ORDER OF THE CHECKS MATTERS MORE THAN THE MARKERS THEMSELVES. Searching
    the whole markup for shield markers declares a 33 905-character article a stub
    because the word `captcha` occurs in the wiki page's own chrome. The detector
    then lies in both directions: it lets a real stub through and rejects a live
    article.

    The cure is not a longer marker list but a measurement: of 141 HTML pages in
    the corpus, all 40 that returned fewer than 1200 characters were a block, a JS
    application or an error, while the shortest real page returned 1212. So A
    SHORTAGE OF TEXT is a necessary condition for a shield, and the markers only
    name which kind. If there is enough text the page is real, whatever sits in
    the markup.

    One exception: "no such page" is recognised at any length, because that kind
    of stub can be wordy. Measured: a deliberately non-existent article path
    returned 2990 characters of coherent text beginning with "this publication is
    unavailable". A length threshold lets it through; the POSITION of the marker at
    the start of the text catches it.
    """
    began = text[:_NO_SUCH_PAGE_WINDOW].lower()
    for marker in _NO_SUCH_PAGE:
        if marker in began:
            return "looks_like_stub", f"at the start of the text: {marker!r}"
    if chars >= THIN_CHARS:
        return "clean", ""
    for marker in _SHIELD_MARKS:
        if marker in raw_low[:20000] or marker in began:
            return "looks_like_stub", f"shield marker: {marker!r}"
    for marker in _PAYWALL_MARKS:
        if marker in raw_low:
            return "looks_like_stub", f"paywall: {marker!r}"
    # Plenty of markup and almost no text is a JS application, not a page. The
    # threshold is on the raw bytes, not the text: a real article does not spend
    # 50 KB of markup on a thousand characters.
    if len(raw_low) > 50_000:
        return "looks_like_stub", (
            f"{len(raw_low)} bytes of markup for {chars} characters of text — "
            "the content is assembled by a script")
    if code in (401, 403, 429):
        return "looks_like_stub", f"status {code} and {chars} characters of text"
    return "clean", ""


def _blank(url: str, status: str, reason: str, **extra) -> dict:
    """A per-address result with the FULL field set.

    The same device as the failure path in search: the fields are always present,
    empty ones included. Otherwise the consumer would have to branch on the SHAPE
    of the answer, and would one day fall over doing it.
    """
    return {"url": url, "final_url": "", "domain": _domain(url), "canonical": "",
            "status": status, "reason": reason,
            "http_status": None, "content_type": "",
            "content": "", "chars": 0, "total_chars": None, "truncated": False,
            "offset": 0, "format": "",
            "via": "", "read_at": None, "elapsed_ms": None,
            "title": "", "published": "", "lang": "",
            "links": [], "links_total": None,
            "stub_check": "not_checked", "stub_reason": "",
            # PDF only; null for pages. ALWAYS present, like everything else: the
            # consumer must not have to branch on the shape of the answer.
            "pages": None, "pages_read": None,
            # HOW THE TEXT WAS OBTAINED. A text layer is copied out of the file;
            # a recognised one was read off a picture by a model. The difference is
            # not technical: a layer can be quoted verbatim, recognition cannot.
            "text_source": "", "recognition": None,
            "robots": "not_checked",
            # Sidecar version, if the browser fetched it. Empty means otherwise.
            "browser_version": "",
            "expected": [], "expected_found": None,
            **extra}


# --- Downloading -------------------------------------------------------------

def _decompress(raw: bytes, encoding: str) -> bytes:
    k = (encoding or "").lower()
    try:
        if "gzip" in k:
            return gzip.decompress(raw)
        if "deflate" in k:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:  # noqa: BLE001
        return raw    # broken packaging — hand it on, the parser will see it
    return raw


class RedirectBlocked(Exception):
    """The server sent us somewhere we must not go. A type of its own rather than
    a general error: this is A REFUSAL BY OUR SIDE, and it must not be confused
    with "the site did not open"."""


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    """Checks EVERY hop, not only the address we started from.

    With the address check only at the entrance, `urlopen` follows 3xx with a
    plain opener: a server answering `302 -> 127.0.0.1/secret` yields a "read"
    status and an internal `final_url`.

    THE COST OF THIS MISTAKE IS NOT RESULT QUALITY BUT EXPOSURE. The address comes
    out of somebody else's result set, so our code does not choose it. Another
    site therefore gains the ability to make us read OUR internal address and hand
    the content to the caller — the one defect class here with consequences beyond
    the module.

    The check runs BEFORE each hop, not on the final address: a chain of
    external -> internal -> external looks clean by its final address, while the
    internal host has already been queried.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        status, reason = _check_url(newurl)
        if status:
            raise RedirectBlocked(reason)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Our own opener for the whole module: the global `urlopen` takes the default
# one, which has no guard in it.
_OPENER = urllib.request.build_opener(_RedirectGuard)


def _fetch(url: str, timeout: float) -> tuple[int, str, bytes, str, str, bool]:
    """(code, type, body, final_url, error, truncated). Never raises.

    We read WITH A LIMIT (`read(CEILING + 1)`), not to the end: the address comes
    from outside, and a 900 MB response is not a hypothesis but an ordinary data
    dump behind a link in a result set. The ceiling is applied before decoding,
    because what needs protecting is memory, not the tidiness of a number.
    """
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    try:
        with _OPENER.open(req, timeout=timeout) as o:
            kind = o.headers.get("Content-Type", "") or ""
            body = o.read(DOWNLOAD_CEILING + 1)
            # THE CEILING IS CHOSEN BY THE CONTENT, not by the header: a PDF can
            # be served under any type, while the first five bytes do not lie. We
            # read on only when the general ceiling was reached AND it really is
            # a PDF.
            is_pdf = body[:5] == b"%PDF-" or "pdf" in kind.lower()
            if len(body) > DOWNLOAD_CEILING and is_pdf:
                body += o.read(PDF_CEILING - len(body) + 1)
            limit = PDF_CEILING if is_pdf else DOWNLOAD_CEILING
            truncated = len(body) > limit
            body = _decompress(body, o.headers.get("Content-Encoding", ""))
            return o.status, kind, body[:limit], o.geturl(), "", truncated
    except RedirectBlocked as e:
        # The distinct code -1 means "stopped by us", not "the server answered".
        # Zero here would mean "did not open" and would heap our own refusal in
        # with network ones — the very indistinguishability this module hunts.
        return -1, "", b"", url, str(e), False
    except urllib.error.HTTPError as e:
        # The error body is read DELIBERATELY: shields answer 401/403 and put
        # into the body exactly the marker by which a shield is recognised.
        # Discarding it would leave "we were not let in" and "no such page" to be
        # told apart by the status code alone — and the code lies (a missing page
        # answers 200 five times out of six).
        try:
            body = _decompress(e.read(DOWNLOAD_CEILING + 1),
                                e.headers.get("Content-Encoding", ""))[:DOWNLOAD_CEILING]
            kind = e.headers.get("Content-Type", "") or ""
        except Exception:  # noqa: BLE001
            body, kind = b"", ""
        return e.code, kind, body, url, "", False
    except urllib.error.URLError as e:
        return 0, "", b"", url, f"network: {e.reason}", False
    except (TimeoutError, socket.timeout):
        return 0, "", b"", url, f"timed out after {timeout:.0f} s", False
    except UnicodeError as e:
        # Non-ASCII in the host name: urllib does not punycode it for us, and the
        # resulting failure looks like the site blocking us while it is our own
        # defect.
        return 0, "", b"", url, f"host name: {e}", False
    except Exception as e:  # noqa: BLE001
        return 0, "", b"", url, f"{type(e).__name__}: {e}", False


def _to_ascii(url: str) -> str:
    """An address fit for http.client: host in punycode, path and query
    percent-encoded.

    THIS IS OUR DEFECT, NOT SOMEBODY ELSE'S BLOCK, and it has already fooled one
    measurement: two addresses were recorded as "did not open", the obvious
    conclusion was a block, and in fact we were raising UnicodeEncodeError
    ourselves on non-ASCII in the address.

    The PATH must be encoded as well: the host is cured by idna and the path by
    percent-encoding, and one without the other is not enough (in both specimens
    the non-ASCII sat in the path).
    """
    pth = urllib.parse.urlsplit(url)
    host = pth.hostname or ""
    if host and not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:  # noqa: BLE001
            host = pth.hostname or ""
    port = f":{pth.port}" if pth.port else ""
    net = host + port if host else pth.netloc
    path = urllib.parse.quote(pth.path, safe="/%:@!$&'()*+,;=~-._")
    query = urllib.parse.quote(pth.query, safe="/%:@!$&'()*+,;=~-._?")
    return urllib.parse.urlunsplit((pth.scheme, net, path, query, ""))


def _read_one(url: str, max_chars: int, offset: int, fmt: str, links: bool,
          expect: list[str], mode: str, fresh: bool, deadline: float,
          recognise: bool = True) -> dict:
    began = time.time()
    status, reason = _check_url(url)
    if status:
        return _blank(url, status, reason, expected=expect)


    target = _to_ascii(url.strip())
    key = f"{target}|{fmt}|{links}|{mode}"
    from_cache = None if fresh else _from_cache(key)
    if from_cache is not None:
        done = from_cache
        done["via"] = "cache"
    elif mode == "browser":
        domain = _domain(target)
        if not _wait_domain(domain, deadline):
            return _blank(url, "not_reached",
                           f"per-domain rate limit on {domain}: our turn did not "
                           f"come within {DOMAIN_WAIT_MAX_S:.0f} s", expected=expect)
        b = _via_browser(target, shot_flag=False)
        if not b["ok"]:
            # THE FETCH DID NOT HAPPEN — and that is NOT "the page is bad". They
            # are different outcomes: "did not get through" is about us and our
            # path, "stub" would be about the page. Mixing them would name success
            # after somebody else's event.
            return _blank(url, "not_reached", b["error"], expected=expect,
                           via="browser",
                           elapsed_ms=int((time.time() - began) * 1000))
        done = _parse_page(url, target, b.get("status"), "text/html",
                            b["html"].encode("utf-8", "replace"), fmt, links,
                            code_is_stale=True, recognise=recognise)
        done["robots"] = _robots_verdict(target, deadline)
        done["via"] = "browser"
        done["browser_version"] = b["version"]
        if _cacheable(done):
            _to_cache(key, done)
    else:
        domain = _domain(target)
        if not _wait_domain(domain, deadline):
            return _blank(url, "not_reached",
                           f"per-domain rate limit on {domain}: our turn did not "
                           f"come within {DOMAIN_WAIT_MAX_S:.0f} s", expected=expect)
        left = max(1.0, min(TIMEOUT_S, deadline - time.time()))
        code, kind, body, final_url, error, truncated = _fetch(target, left)
        if code == -1:
            # OUR DEFENCE, NOT THE SITE REFUSING. "forbidden" and "did not open"
            # send the caller in different directions: retrying is pointless for
            # the first and sensible for the second.
            return _blank(url, "forbidden",
                           f"redirect hop stopped: {error}",
                           expected=expect,
                           elapsed_ms=int((time.time() - began) * 1000))
        if error:
            return _blank(url, "unreachable", error, expected=expect,
                           elapsed_ms=int((time.time() - began) * 1000))
        done = _parse_page(url, final_url, code, kind, body, fmt, links, truncated,
                            recognise=recognise)
        done["robots"] = _robots_verdict(final_url, deadline)
        done["via"] = "plain"
        if _cacheable(done):
            _to_cache(key, done)
        # ESCALATION. mode=auto: the plain request produced no content and the
        # browser is wired up, so we try it. Cheap first, browser on refusal. We
        # try ONCE and only on those outcomes where scripts and shields are the
        # explanation rather than the network.
        if (mode == "auto" and BROWSER_URL
                and done["status"] in ("stub", "empty", "refused")
                and time.time() < deadline - 5):
            b = _via_browser(target, shot_flag=False)
            if b["ok"]:
                second = _parse_page(url, target, b.get("status"), "text/html",
                                    b["html"].encode("utf-8", "replace"), fmt,
                                    links, code_is_stale=True,
                                    recognise=recognise)
                # The browser result is taken ONLY if it is better: otherwise an
                # honest "empty" would be replaced by the same "empty", dearer.
                if (second["status"] == "read"
                        and (second.get("total_chars") or 0)
                        > (done.get("total_chars") or 0)):
                    second["robots"] = done["robots"]
                    second["via"] = "browser"
                    second["browser_version"] = b["version"]
                    second["reason"] = ("the plain request gave "
                                        f"{done['status']!r}, fetched with the browser")
                    done = second
                    _to_cache(key, done)

    done["elapsed_ms"] = int((time.time() - began) * 1000)
    done["read_at"] = int(time.time())
    # THE MARKER IS CHECKED AGAINST THE FULL TEXT, NOT THE VISIBLE SLICE, and the
    # order here is load-bearing. Slicing the content first and then looking for
    # the marker in what remains turns "not found" into "not in this window" on a
    # long page — a front page can carry hundreds of characters of quotes before
    # its own name — and the caller gets a false expected_found=false.
    _verify_expect(done, expect)
    _slice_out(done, max_chars, offset)
    return done


def _parse_page(url: str, final_url: str, code: int, kind: str, body: bytes,
               fmt: str, links: bool, truncated: bool = False,
               code_is_stale: bool = False, recognise: bool = True) -> dict:
    out = _blank(url, "read", "")
    out.update(final_url=final_url, domain=_domain(final_url or url),
               http_status=code, content_type=kind.split(";")[0].strip())

    # A PDF is recognised by type OR by signature: servers serve PDFs as
    # application/octet-stream and even as text/html, while the first five bytes of
    # the file do not lie.
    is_pdf = ("pdf" in kind.lower()) or body[:5] == b"%PDF-"
    if is_pdf:
        if truncated:
            # THE FILE IS LARGER THAN THE CEILING — AND THAT MUST BE SAID AS
            # SUCH, not returned as a parse error. A parser honestly calls a
            # truncated PDF broken, and the caller then goes looking for another
            # source instead of understanding that this source is good and merely
            # large.
            out.update(status="forbidden", content_type="application/pdf",
                       reason=f"the PDF exceeds the download ceiling "
                              f"({PDF_CEILING // (1024*1024)} MB): we do not read it "
                              "whole, and a partly read PDF is indistinguishable "
                              "from a broken one")
            return out
        pdf_text, info = _from_pdf(body)
        out.update(format=fmt, links_total=0, content_type="application/pdf",
                   pages=info.get("pages"), pages_read=info.get("pages_parsed"))
        if info.get("error"):
            # The file is there and we could not parse it: that is neither "empty"
            # nor "stub". "Did not open" would be about the fetch, and the fetch
            # succeeded — so the refusal is ours, and it is named as such with a
            # reason.
            out.update(status="forbidden", reason=f"the PDF was not parsed: {info['error']}")
            return out
        chars = len(pdf_text)
        page_res = info.get("pages_parsed") or 0
        # NO TEXT LAYER IS A SEPARATE OUTCOME, not "empty in general". The
        # distinction is mandatory: "the page opened and has no text" and "the
        # document exists but it is a picture" call for different things — the
        # second is cured by recognition, the first is not.
        if page_res and chars < page_res * CHARS_PER_PAGE:
            # A SCAN. Reading recognises it — an answer that ends at "recognition
            # is needed" leaves the caller alone with it, and whoever called
            # `web_read` on a document asked for the document to be READ and
            # accepted the cost of it by that call.
            if recognise:
                return _recognise_scan(out, body, chars, page_res)
            # NOBODY ASKED FOR A MODEL HERE. Search was asked for links with
            # text, and a scan that happened into the top three is not a reason
            # to bill the caller for a vision model. So the refusal NAMES THE
            # PATH instead of leaving an empty field: the caller who wants this
            # document calls reading, and pays knowingly.
            out.update(status="empty", total_chars=chars, content="",
                       stub_check="clean", text_source=NOT_RECOGNISED,
                       reason=f"no text layer: {chars} characters over "
                              f"{page_res} pages; recognition is not done here — "
                              "call web_read on this address to have it read")
            return out
        if chars == 0:
            out.update(status="empty", total_chars=0, content="",
                       stub_check="clean", reason="no text was found in the PDF")
            return out
        out.update(content=pdf_text, total_chars=chars, stub_check="clean",
                   text_source="text_layer")
        if info.get("truncated_by_pages"):
            # Pages beyond the ceiling do not disappear silently.
            out["reason"] = (f"the first {page_res} pages of {info.get('pages')} "
                             f"were parsed: we did not read further")
        return out

    is_text = any(m in kind.lower() for m in _TEXT_TYPES) or not kind
    if not is_text:
        # DOCX, XLSX, images. We refuse EXPLICITLY and name the type: "forbidden"
        # is about us, "empty" would be about the page, and those call for
        # different actions from the caller — change the source, or fetch it by
        # another means.
        out.update(status="forbidden",
                   reason=f"type {out['content_type'] or '-'} is not read by this "
                          "tool (office formats are not parsed)")
        return out

    raw = _decode(body, kind)
    is_html = ("html" in kind.lower()
                  or bool(re.search(r"<\s*(html|body|div|p)\b", raw[:4000], re.I)))
    if is_html:
        r = _HtmlText(final_url or url)
        try:
            r.feed(raw)
            r.close()
        except Exception:  # noqa: BLE001
            pass            # broken markup — we take whatever was collected
        r.from_jsonld()
        text, mdl = r.collect()
        out.update(title=re.sub(r"\s+", " ", _html.unescape(r.title)).strip()[:300],
                   canonical=r.canonical, published=r.published, lang=r.lang,
                   links_total=len(r.links),
                   links=r.links[:200] if links else [])
        # Three forms of content, one parse. Rejection and the character count
        # are always computed over the EXTRACTED TEXT, whichever form was asked
        # for: otherwise format=html would report "a hundred thousand characters"
        # for an empty page with a lot of markup — exactly the lie rejection
        # exists to prevent.
        content = {"markdown": mdl, "text": text}.get(fmt, raw)
        for_check = text
    else:
        content = for_check = raw.strip()
        out.update(links_total=0)

    out["format"] = fmt
    chars = len(for_check)
    verdict, why_stub = _stub_check(for_check, raw.lower(), code, chars)
    out.update(stub_check=verdict, stub_reason=why_stub)

    if verdict != "clean":
        out.update(status="stub", reason=why_stub, content="",
                   total_chars=chars)
        return out
    if (code in (401, 403, 407, 429) or code >= 500) and not code_is_stale:
        out.update(status="refused", reason=f"status {code}", content="",
                   total_chars=chars)
        return out
    if (code in (401, 403, 407, 429) or code >= 500) and code_is_stale:
        # THE BROWSER PATH: THE CODE COMES FROM THE FIRST RESPONSE, THE TEXT FROM
        # THE FINAL STATE. A shielded site answers 401, the shield clears during
        # the settle pause, and the DOM then holds real text — while a verdict
        # taken from the code throws it away as a refusal.
        #
        # Both considerations are right, so they are SEPARATED rather than traded
        # off: the code stays REAL (we do not invent a 200) while the outcome is
        # decided by what was finally obtained. Passing shields is what the browser
        # exists for; discarding a shield it passed cancels its purpose.
        out["reason"] = (f"first response {code}, but the browser passed the "
                         "shield: the text was obtained after that")
        # The ordinary emptiness and stub checks follow — if the shield was NOT
        # passed, they will say so from the content.
    if chars == 0:
        out.update(status="empty", reason="the page opened and has no text in it",
                   total_chars=0, content="")
        return out
    out.update(content=content, total_chars=len(content))
    return out


def _slice_out(out: dict, max_chars: int, offset: int) -> None:
    """Chunked reading with a cursor. The one place where the reference MCP
    implementation beats the paid tools: those simply cut at maxCharacters and say
    nothing about the tail, so a model does not know the text ended in us rather
    than on the page.

    The CONTENT is cut here, before serialisation. Cutting the finished JSON
    string on a 200 000-character page would slice it mid-string and return
    unparseable text — "the module is broken" instead of "the content is
    truncated".
    """
    total = out.get("total_chars") or 0
    content = out.get("content") or ""
    if not content:
        out.update(chars=0, offset=offset, truncated=False)
        return
    tail = content[offset:offset + max_chars]
    cut = offset + len(tail) < len(content)
    if cut:
        # THE CONTINUATION NAMES A TOOL, NOT "THE SAME CALL". "The same call with
        # offset=N" was true while text came only from web_read; with reading
        # inside search the hint started to lie, because web_search has no offset
        # argument at all and the caller would go looking for a handle that does
        # not exist.
        tail += (f"\n\n[...truncated: characters {offset}-{offset + len(tail)} "
                  f"of {total} are shown. To continue, call web_read on this "
                  f"address with offset={offset + len(tail)}]")
    out.update(content=tail, chars=len(tail), offset=offset, truncated=cut)


def _verify_expect(out: dict, expect: list[str]) -> None:
    """A marker in the text — the reference attribute for reading.

    Why not the status code, the title, the canonical link or the length: all four
    were measured and all four lie. A deliberately non-existent article path
    answers 200 (five times out of six), carries a title echoing the request and
    3075 characters of text; a 404 page declares itself the canonical address of
    itself. The one thing that is not forged by accident is knowledge of WHAT
    SPECIFICALLY must be on the page.
    """
    out["expected"] = list(expect)
    if not expect:
        out["expected_found"] = None
        return
    where = (out.get("content") or "").lower()
    out["expected_found"] = all(str(pth).lower() in where for pth in expect)


def _shot_refusal(url: str, reason: str, **extra) -> dict:
    """The full field set on the failure path too, as everywhere in this module."""
    return {"contract": "ag.shot/1", "ok": False, "url": url,
            "shot_taken": False, "png_base64": "", "bytes": 0,
            "width": None, "height": None,
            "page_text": "", "page_text_chars": None,
            "page_text_truncated": False,
            "expected": [], "expected_found": None,
            "browser_version": "", "elapsed_ms": None,
            "error": reason, **extra}


# HOW MUCH TEXT TRAVELS WITH A SHOT. Modest on purpose: the text is here to be
# cross-checked against the pixels, and a cross-check needs text, not all of it.
# Whoever wants the whole document calls reading, where the cursor is.
SHOT_TEXT_CHARS = int(os.environ.get("SHOT_TEXT_CHARS") or "2000")


def screenshot(url: str, expect=None, full_page: bool = False,
               max_chars: int = SHOT_TEXT_CHARS) -> dict:
    """A PNG screenshot of a page. Contract ag.shot/1. Never raises.

    WHAT IT IS FOR — NOT FOR SHOWING. It exists so that a result can be COMPARED:
    reading has no independent second opinion otherwise. We can say "text was
    extracted"; we cannot say "THE text that is on the page was extracted".

    The shot and the text are obtained in ONE AND THE SAME browser visit but by
    different routes: the pixels are drawn by the layout engine, the text comes
    from the DOM. A disagreement between them catches what neither route sees
    alone.

    THE TEXT IS RETURNED, NOT MERELY COUNTED, and that is the whole reason this
    capability stands apart from reading. Sent back as a number alone, the shot is
    just a picture, and the text to compare it with has to be fetched by a second
    call in ANOTHER visit — where the page may already differ, so the two halves
    of the comparison would no longer be of the same page. It is bounded by
    `max_chars` rather than by cutting the shot: see SHOT_TEXT_CHARS.

    TWO OUTCOMES IN TWO FIELDS, and this is a lesson bought expensively:
      · `shot_taken` — THE SHOT WAS TAKEN: the browser is alive, the page loaded;
      · `expected_found` — WHAT WE EXPECTED IS ON THE PAGE.
    A definition of the form `ok = check_passed and no error` makes a page that
    honestly failed a check return exactly what a browser that never opened
    returns. Success is then named after somebody else's event, the tool counts as
    working and has never worked once. Merged into one field, these two outcomes
    reproduce that blindness.

    The PNG is returned by a SEPARATE CALL rather than mixed into a text answer: a
    megabyte of image inside a response asked for as text makes the cost of a call
    unpredictable — the same argument that separates search from reading.
    """
    import base64
    began = time.time()
    if isinstance(expect, str):
        expect = [expect]
    expect = [str(x) for x in (expect or []) if str(x).strip()][:5]

    status, reason = _check_url(url)
    if status:
        return _shot_refusal(url, f"{status}: {reason}", expected=expect)
    target = _to_ascii((url or "").strip())
    domain = _domain(target)
    if not _wait_domain(domain, time.time() + DOMAIN_WAIT_MAX_S):
        return _shot_refusal(url, f"per-domain rate limit on {domain}: our turn did not come",
                             expected=expect)
    b = _via_browser(target, shot_flag=True, full_page_flag=bool(full_page))
    if not b["ok"]:
        return _shot_refusal(url, b["error"], expected=expect,
                             elapsed_ms=int((time.time() - began) * 1000))

    # The text comes from THE SAME visit — otherwise there would be nothing to
    # compare against anything.
    r = _HtmlText(target)
    try:
        r.feed(b["html"])
        r.close()
    except Exception:  # noqa: BLE001
        pass
    text, _ = r.collect()
    try:
        max_chars = max(0, min(200_000, int(max_chars)))
    except (TypeError, ValueError):
        max_chars = SHOT_TEXT_CHARS
    found = None
    if expect:
        where = text.lower()
        found = all(str(x).lower() in where for x in expect)
    return {
        "contract": "ag.shot/1",
        # `ok` EXISTS IN EVERY CONTRACT HERE and means the same thing in all of
        # them: the work was done on OUR side of the seam. Omitting it because
        # `shot_taken` is more precise would be right if this tool lived alone. It
        # lives beside the others, and a caller with a shared `if not resp["ok"]`
        # would meet a missing field on a screenshot: a refusal where all is well.
        #
        # Our rule: neighbouring tools that call the same thing by different names
        # are a future mistake by the caller.
        #
        # HONESTLY ABOUT THEIR COINCIDING: today `ok` and `shot_taken` are always
        # equal — the shot was either taken or not taken for a reason of ours. Both
        # are kept not for the difference but for uniform handling: `ok` is the
        # protocol field, identical across all contracts, `shot_taken` is the
        # subject-matter fact. They will diverge on the day a mode appears where
        # the call is legitimate and no shot is taken.
        "ok": True,
        "url": url,
        "shot_taken": True,
        "png_base64": base64.b64encode(b["png"]).decode("ascii"),
        "bytes": len(b["png"]),
        "width": None, "height": None,
        # THE TEXT OF THE SAME VISIT, and its full length beside it. The length
        # is of the WHOLE text, not of the piece returned — otherwise "the page is
        # silent" and "we brought back a little of it" look identical, and a
        # megabyte of image at zero characters is exactly the signal worth seeing.
        "page_text": text[:max_chars],
        "page_text_chars": len(text),
        "page_text_truncated": len(text) > max_chars,
        "expected": expect, "expected_found": found,
        "browser_version": b["version"],
        "elapsed_ms": int((time.time() - began) * 1000),
        "error": "",
    }

# --- The door ----------------------------------------------------------------

def _refusal(reason: str, **extra) -> dict:
    """The answer when the work was NOT done on our side of the seam."""
    return {"contract": CONTRACT, "ok": False, "error": reason,
            "requested": 0, "count": 0, "failed": 0, "results": [],
            "via_used": [], "paths_available": paths(),
            "all_pages_clean": False, "deadline_hit": False, **extra}


# HOW LONG THE PATH STATE IS REMEMBERED. Without a cache, `paths()` opens A
# BROWSER SESSION on every call — and it is called by every read, every refusal
# and by `/healthz` every thirty seconds from the deployment. The health check
# then becomes a load on the very thing whose health it checks.
#
# THE LIFETIME IS TIED TO THE HEALTH-CHECK RATE. Fifteen seconds against a
# thirty-second health check means THE MOST FREQUENT consumer never hits the
# cache, and the cache looks like an optimisation without being one. A cache that
# does not work is worse than none: it stops the next person seeing the real load.
#
# Forty-five seconds exceeds a thirty-second tick, so that consumer hits it too.
# The price is that the state in `/healthz` can be up to forty-five seconds stale;
# that is tolerable because `ok` does not depend on it (see server.health), and
# the `/pages` dashboard a person reads asks for the state FRESH, bypassing the
# cache.
PATHS_CACHE_S = float(os.environ.get("PATHS_CACHE_S") or "45.0")
_paths_lock = threading.Lock()
_paths_cache: tuple[float, dict] | None = None


def paths(fresh_flag: bool = False) -> dict[str, str]:
    """Which fetch paths the module actually has.

    Returned both in a read answer and in /healthz. The failure-direction rule
    applies: no data means "not checked", never "sound" — a path that does not
    exist must be visible as absent, otherwise a dead browser is indistinguishable
    from a page with no text.

    THE STATE LIVES HERE, NOT IN THE DOCUMENTS. A sentence like "the browser path
    is not wired up" written into seven documents at once becomes a lie in all
    seven on the day it is wired up, without giving any sign. The documents point
    at this view instead, and the state is computed.
    """
    global _paths_cache
    # THE PROBE IS INSIDE THE LOCK, not just the cache read. Taking the lock
    # twice and probing the sidecar in between lets N simultaneous misses open N
    # `about:blank` sessions — the protection against load creating load exactly
    # when there is most of it.
    with _paths_lock:
        if not fresh_flag and _paths_cache and time.time() - _paths_cache[0] < PATHS_CACHE_S:
            return dict(_paths_cache[1])
        result = _paths_fresh()
        _paths_cache = (time.time(), dict(result))
        return result


def _paths_fresh() -> dict[str, str]:
    """Ask the sidecar for real. The cache lives outside this function, so that
    this stays the one to call when an exact answer is needed."""
    if not BROWSER_URL:
        return {"plain": "alive", "browser": "not_wired_up"}
    if not HAS_BROWSER_CLIENT:
        return {"plain": "alive", "browser": "the address is set but the client is not in the image"}
    # "Declared" and "answering" are different claims. We ask the sidecar itself
    # rather than trust an environment variable: it speaks of intent, not fact.
    probe = _via_browser("about:blank", shot_flag=False)
    if not probe["ok"]:
        return {"plain": "alive", "browser": f"not responding: {probe['error'][:80]}"}
    # PLAYWRIGHT DOES THE VERSION CHECK ITSELF, and better than we can. Comparing
    # `browser.version` with the pinned playwright version shouts "version
    # mismatch" at a perfectly healthy pair: they are different quantities —
    # `version` returns the CHROMIUM version while what is pinned is the
    # PLAYWRIGHT version.
    #
    # The real check has already happened, for free: `connect()` REFUSES when the
    # minor versions of client and server differ — that is what the pin is for. The
    # connection succeeded, therefore the versions match. A check that shouts at a
    # healthy system is worse than none: people quickly learn not to read it.
    return {"plain": "alive", "browser": f"alive (chromium {probe['version']})"}


# HOW MANY PAGES ARE READ AT ONCE. Not "as many as possible": every thread holds
# a connection and memory for a page body, and the gain is bounded by the slowest
# domain rather than by the number of threads.
READ_PARALLEL_N = int(os.environ.get("READ_PARALLEL") or "6")


def read_many(url_list: list[str], max_chars: int = DEFAULT_CHARS,
                 fmt: str = "markdown", expect=None, mode: str = "auto",
                 deadline: float = 0, recognise: bool = True) -> list[dict]:
    """Read many pages IN PARALLEL. The answers keep the order of the addresses.

    THE OBVIOUS ARGUMENT AGAINST PARALLELISM IS WRONG HERE, and it is worth
    stating because it is convincing. It runs: the reader has a PER-DOMAIN rate
    limiter, so parallel loading would simply run into it. Measured over five
    runs: reading eats 72-76% of the whole time (65-75 s out of 90-104), while
    twenty pages sit on nearly twenty DIFFERENT domains and do not compete with
    each other for the limiter at all. The argument holds for one domain and
    fails for twenty.

    WE GROUP BY DOMAIN INSTEAD OF HANDING ADDRESSES TO A SHARED POOL. The
    difference is not cosmetic: in a shared pool two addresses on one site occupy
    two threads, and one of them JUST SLEEPS for two seconds on the limiter. One
    thread per domain keeps the same pacing with no wasted slots — and, more
    importantly, preserves the limiter's main property: a site must never see
    simultaneous requests from us. One site closed its door to our address FOR
    GOOD after three dozen consecutive requests, and every future read paid for it.
    """
    if not url_list:
        return []
    deadline = deadline or (time.time() + WHOLE_CALL_S)
    expect = expect or []
    by_domain: dict[str, list[int]] = {}
    for i, u in enumerate(url_list):
        by_domain.setdefault(_domain(u) or f"?{i}", []).append(i)
    done: dict[int, dict] = {}

    def _domain_queue(indexes: list[int]) -> None:
        for i in indexes:
            if time.time() > deadline:
                done[i] = _blank(url_list[i], "not_reached",
                                    "the call deadline expired", expected=expect)
                continue
            try:
                done[i] = _read_one(url_list[i], max_chars, 0, fmt, False,
                                  expect, mode, False, deadline, recognise)
            except Exception as e:  # noqa: BLE001
                # One failed thread does not cancel the rest, and the failure IS
                # NAMED.
                done[i] = _blank(url_list[i], "unreachable",
                                    f"{type(e).__name__}: {e}"[:160],
                                    expected=expect)

    threads = []
    for indexes in by_domain.values():
        while len([pth for pth in threads if pth.is_alive()]) >= READ_PARALLEL_N:
            time.sleep(0.05)
            if time.time() > deadline:
                break
        pth = threading.Thread(target=_domain_queue, args=(indexes,), daemon=True)
        pth.start()
        threads.append(pth)
    for pth in threads:
        pth.join(timeout=max(0.0, deadline - time.time()) + 1.0)
    return [done.get(i) or _blank(url_list[i], "not_reached",
                                     "the read did not finish in time",
                                     expected=expect)
            for i in range(len(url_list))]


def read(urls, max_chars=DEFAULT_CHARS, offset=0, fmt="markdown",
         links=False, expect=None, mode="auto", fresh=False) -> dict:
    """Read pages by address. Contract ag.read/2.

    SUCCESS MEANS "EVERY NAMED ADDRESS GOT A NAMED OUTCOME", even when every
    outcome is a refusal. The same rule as in search: `ok: false` if and only if
    NOTHING was done. An empty page is a success: it opened, it has no text, and
    retrying is pointless — whereas a refusal is worth retrying. We deliberately
    differ here from implementations that count "no text found" as an error, and
    the module's own rule says we are right: a probe returning zero must first
    show that its subject existed. http_status, content_type and stub_check in the
    same answer show it.
    """
    began = time.time()
    deadline = began + WHOLE_CALL_S

    if isinstance(urls, str):
        urls = [urls]           # a single address is accepted as a string: the
                                # contract asks for an array, but this is far too
                                # easy to get wrong
    if not isinstance(urls, (list, tuple)):
        return _refusal("urls: an array of addresses was expected")
    url_list = [str(u).strip() for u in urls if str(u or "").strip()]
    if not url_list:
        return _refusal("urls: not a single address was given")
    surplus = url_list[MAX_URLS:]
    url_list = url_list[:MAX_URLS]

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = DEFAULT_CHARS
    max_chars = max(MIN_CHARS, min(MAX_CHARS, max_chars))
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    fmt = fmt if fmt in ("markdown", "text", "html") else "markdown"
    mode = mode if mode in ("auto", "plain", "browser") else "auto"
    if isinstance(expect, str):
        expect = [expect]
    expect = [str(x) for x in (expect or []) if str(x).strip()][:5]

    results = []
    for url in url_list:
        if time.time() > deadline:
            results.append(_blank(url, "not_reached",
                                      f"the call deadline of {WHOLE_CALL_S:.0f} s expired",
                                      expected=expect))
            continue
        results.append(_read_one(url, max_chars, offset, fmt, links,
                                expect, mode, bool(fresh), deadline))
    for url in surplus:
        # Named but not read. Dropping them silently is not allowed: the client
        # must SEE that part of the batch was skipped (ag.search/2, "a partial
        # case is not an error").
        results.append(_blank(url, "not_reached",
                                  f"at most {MAX_URLS} addresses are read per "
                                  "call; this one was not read", expected=expect))

    read_ok = [r for r in results if r["status"] in ("read", "empty")]
    paths_used = sorted({r["via"] for r in results if r["via"]})
    clean = bool(results) and all(
        r["status"] in ("read", "empty")
        and r["stub_check"] == "clean"
        and (r["expected_found"] is not False)
        for r in results)
    return {"contract": CONTRACT, "ok": True, "error": "",
            "requested": len(url_list) + len(surplus),
            "count": len(read_ok),
            "failed": len(results) - len(read_ok),
            "results": results,
            "via_used": paths_used,
            "paths_available": paths(),
            # The exact analogue of all_engines_clean, failure direction
            # included: true only if EVERY address ended in read/empty, rejection
            # said clean, and the requested marker was found. No data means false.
            "all_pages_clean": clean,
            "deadline_hit": time.time() > deadline}
