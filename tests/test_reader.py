# -*- coding: utf-8 -*-
"""Tests of page reading (ag.read/2) WITH NO outbound requests.

A fake site on a local port instead of the internet. The reason is the same as
for the search tests and stronger here: reading is exactly the work for which
sites close an address for good — one site stopped answering our machine after
some thirty requests. A test that went outside would spend the very resource the
tool exists to protect.

The fake site serves not "a page in general" but CLASSES OF DEFECT, each of them
measured on live sources: an anti-bot shield, a wordy "this publication is
unavailable" stub, a live article with the word captcha in its own chrome, a
legacy encoding with no declaration, a JS application.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "adapter"))

# A PDF WITH NO TEXT LAYER, assembled by hand: one page, not a single text
# block. That is what a scan looks like — the file is valid, the pages are
# there, the text is not. Telling this case apart is mandatory: "the page
# opened and has no text" and "the document exists but it is a picture" call
# for different things, and only the second is cured by recognition.
def _empty_pdf() -> bytes:
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<<>>>>",
    ]
    outside = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, msg_body in enumerate(objects, start=1):
        offsets.append(len(outside))
        outside += b"%d 0 obj\n" % n + msg_body + b"\nendobj\n"
    start_at = len(outside)
    outside += b"xref\n0 %d\n" % (len(objects) + 1)
    outside += b"0000000000 65535 f \n"
    for off in offsets:
        outside += b"%010d 00000 n \n" % off
    outside += (b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, start_at))
    return bytes(outside)


EMPTY_PDF = _empty_pdf()

LONG_TEXT = ("A real coherent paragraph of an article. " * 48)          # ~1900 characters
PAGES: dict[str, tuple[int, str, bytes]] = {
    "/ok": (200, "text/html; charset=utf-8", (
        "<html lang='ru'><head><title> The article title </title>"
        "<link rel='canonical' href='/ok'>"
        "<meta property='article:published_time' content='2026-09-06T10:00:00Z'>"
        "</head><body><nav>a menu that must not appear</nav>"
        "<h2>Section</h2><p>" + LONG_TEXT + "</p>"
        "<p>A paragraph with <a href='/other'>an inward link</a> and "
        "<a href='https://outward.example/x'>an outward one</a>.</p>"
        "<script>var noise = 'this must not be in the text';</script>"
        "<footer>a footer that must not appear</footer>"
        "</body></html>").encode("utf-8")),
    # A live article with the word captcha in its own chrome. Rejection that
    # searches the whole markup without regard to length declares a live
    # 33 905-character article a stub on exactly this case.
    "/live-with-marker": (200, "text/html; charset=utf-8", (
        "<html><body><p>" + LONG_TEXT + "</p>"
        "<form action='/login'><input name='captcha'></form>"
        "<div>cloudflare</div></body></html>").encode("utf-8")),
    # A shield: status 401 and an almost empty body carrying the marker.
    "/qrator": (401, "text/html", (
        "<html><head><script src='/__qrator/ldr_v2e_v0986.js'></script></head>"
        "<body></body></html>").encode("utf-8")),
    # A stub that length does not catch: 200, plenty of coherent text, but it
    # begins with "this publication is unavailable". Measured: 2990 characters
    # at status 200.
    "/no-such-page": (200, "text/html; charset=utf-8", (
        "<html><head><title>Certainly No Such Page 12345</title></head><body><p>"
        "Page not found. " + LONG_TEXT +
        "</p></body></html>").encode("utf-8")),
    "/no-such-page-ru": (200, "text/html; charset=utf-8", (
        "<html><head><title>Certainly No Such Page 12345</title></head><body><p>"
        "Публикация недоступна для просмотра. " + LONG_TEXT +
        "</p></body></html>").encode("utf-8")),
    "/empty": (200, "text/html; charset=utf-8",
               b"<html><body><div></div></body></html>"),
    "/refused": (403, "text/html", ("<html><body><p>" + LONG_TEXT +
                                  "</p></body></html>").encode("utf-8")),
    "/pdf-broken": (200, "application/pdf", "%PDF-1.4 not a pdf at all".encode("utf-8")),
    "/pdf-no-text": (200, "application/pdf", EMPTY_PDF),
    # Served under the WRONG type: servers were measured sending PDFs as
    # application/octet-stream and even text/html. We recognise it by the file
    # signature.
    "/pdf-wrong-type": (200, "application/octet-stream", EMPTY_PDF),
    "/cp1251": (200, "text/html", ("<html><head>"
                "<meta http-equiv='Content-Type' content='text/html; charset=windows-1251'>"
                "</head><body><p>Работы по кириллице. " + LONG_TEXT +
                "</p></body></html>").encode("cp1251")),
    "/кириллица/путь": (200, "text/html; charset=utf-8",
                        ("<html><body><p>Кириллица в адресе. " + LONG_TEXT +
                         "</p></body></html>").encode("utf-8")),
    # The content DEPENDS ON POSITION. Filling a page with sixty thousand
    # identical letters makes the check "offset returned a different slice" pass
    # even with a broken cursor: every slice equals every other.
    "/long": (200, "text/html; charset=utf-8",
                 ("<html><body><p>"
                  + " ".join(f"fragment{i:05d}" for i in range(4000))
                  + "</p></body></html>").encode("utf-8")),
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /disallowed\n".encode("utf-8")),
    "/disallowed/here": (200, "text/html; charset=utf-8",
                     ("<html><body><p>" + LONG_TEXT + "</p></body></html>").encode("utf-8")),
}
HITS: list[str] = []


class Site(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        HITS.append(path)
        # REDIRECTS. They exist for the defect where the address check sits at the
        # entrance while the download follows 3xx with a plain opener — letting
        # another site send us inside the perimeter. Where to lead is set by the
        # request itself, so one branch serves both the allowed redirect and the
        # forbidden one.
        if path.startswith("/redirect"):
            to_where = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("to", ["/ok"])[0]
            self.send_response(302)
            self.send_header("Location", to_where)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        code, kind, msg_body = PAGES.get(path, (404, "text/html", b"<html><body>"
                                             b"<p>Page not found</p></body></html>"))
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(msg_body)))
        self.end_headers()
        self.wfile.write(msg_body)


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


def main() -> int:
    # THE DEFENCE IS CHECKED BEFORE THE GATE. The order matters: opening
    # READ_ALLOW_INTERNAL on the first line would mean never checking the ban on
    # internal addresses — and that ban is what makes web_read safe when the
    # address comes out of somebody else's result set.
    os.environ.pop("READ_ALLOW_INTERNAL", None)
    import reader

    print("\n== prohibitions (the test gate is CLOSED) ==")
    for addr, why in [
            ("http://127.0.0.1:9/x", "loopback"),
            ("http://10.0.0.5/x", "a private network"),
            ("http://192.168.1.1/", "a private network"),
            ("http://localhost/x", "localhost by name"),
            ("http://searxng:8080/search", "a docker-network neighbour")]:
        r = reader.read([addr])["results"][0]
        check(f"inside the perimeter is refused: {why}",
                 r["status"] == "forbidden", f"{addr} -> {r['status']}")
    for addr, why in [
            ("ftp://example.org/x", "a foreign scheme"),
            ("not an address at all", "not an address"),
            ("https://www.google.com/search?q=x", "a search results page"),
            ("https://bing.com/search?q=x", "another search results page"),
            ("https://imgur.com/search?q=x", "an image search page")]:
        r = reader.read([addr])["results"][0]
        check(f"reading is forbidden: {why}", r["status"] == "forbidden",
                 f"{addr} -> {r['status']} {r['reason'][:60]}")

    r = reader.read(["https://example.org/article?utm=1"])["results"][0]
    check("an ordinary external address is NOT forbidden (the ban did not eat everything)",
             r["status"] != "forbidden", r["status"])

    # From here on the gate is needed: without it the fake site is unreachable
    # and the tests would go to the internet.
    os.environ["READ_ALLOW_INTERNAL"] = "1"
    site = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=site.serve_forever, daemon=True).start()
    API_BASE = f"http://127.0.0.1:{site.server_address[1]}"

    def read_at(path, **kw):
        reader._reset_rate()
        return reader.read([API_BASE + path], **kw)

    def one_page(path, **kw):
        return read_at(path, **kw)["results"][0]

    print("\n== parsing a page ==")
    r = one_page("/ok", links=True)
    check("status `read`", r["status"] == "read", r)
    check("the title is taken and cleaned", r["title"] == "The article title", r["title"])
    check("the page language", r["lang"] == "ru", r["lang"])
    check("the publication date", r["published"].startswith("2026-09-06"), r["published"])
    check("canonical is made absolute",
             r["canonical"].endswith("/ok") and r["canonical"].startswith("http"),
             r["canonical"])
    check("a script did not reach the text", "this must not be in the text" not in r["content"])
    check("page furniture (nav/footer) is dropped",
             "a menu that must not" not in r["content"] and "a footer that must not" not in r["content"])
    check("markdown: a heading as markup", "## Section" in r["content"], r["content"][:80])
    check("markdown: a link as markup", "[an inward link](" in r["content"])
    check("links are collected and made absolute",
             r["links_total"] == 2 and all(l["url"].startswith("http") for l in r["links"]),
             r["links"])
    check("via = plain", r["via"] == "plain", r["via"])
    check("domain is computed", r["domain"] == "127.0.0.1", r["domain"])

    r = one_page("/ok", fmt="text")
    check("format=text: no markdown markup",
             "## Section" not in r["content"] and "Section" in r["content"])
    r = one_page("/ok", fmt="html")
    check("format=html: markup as it came", "<html" in r["content"].lower())

    r = one_page("/ok")
    check("links=false: the list is empty but links_total is named",
             r["links"] == [] and r["links_total"] == 2,
             f"{r['links']} / {r['links_total']}")

    print("\n== the seven outcomes ==")
    r = one_page("/empty")
    check("an empty page is `empty`, NOT an error", r["status"] == "empty", r)
    check("an empty page: the subject is shown (status and type in place)",
             r["http_status"] == 200 and r["content_type"] == "text/html", r)
    o = read_at("/empty")
    check("an empty page: ok:true and count=1 (this is a success)",
             o["ok"] is True and o["count"] == 1, o)

    r = one_page("/qrator")
    check("an anti-bot shield is recognised as `stub`", r["status"] == "stub", r)
    check("the shield: the reason names the marker", "qrator" in r["stub_reason"].lower(),
             r["stub_reason"])
    check("the shield: the content is NOT returned as page text", r["content"] == "")

    r = one_page("/no-such-page")
    check("a 2000-character unavailable-page is a stub; length does not save it",
             r["status"] == "stub", f"{r['status']} {r['total_chars']}")

    r = one_page("/no-such-page-ru")
    check("the marker list is multilingual by design: a Russian stub too",
             r["status"] == "stub", f"{r['status']} {r['total_chars']}")

    r = one_page("/live-with-marker")
    check("REGRESSION: a live article with `captcha` in its markup is `clean`",
             r["status"] == "read" and r["stub_check"] == "clean",
             f"{r['status']} / {r['stub_reason']}")

    r = one_page("/refused")
    check("403 with text is `refused`, not `read`", r["status"] == "refused", r)
    r = one_page("/no-page-at-all")
    check("404 is a no-such-page stub", r["status"] == "stub", r)
    print("\n== PDF ==")
    if not reader.HAS_PDF:
        # FAILURE DIRECTION. A silent skip here would give a green run that checked
        # nothing — the defect this module is written against.
        check("PDF: the parser is available (run the tests IN THE IMAGE, see tests/in-image.sh)",
                 False, "pypdf is not installed on this machine")
    else:
        r = one_page("/pdf-no-text")
        check("a PDF with no text layer is `empty`, not `read`",
                 r["status"] == "empty", r)
        check("a PDF with no text: the reason is named and points at recognition",
                 "no text layer" in r["reason"], r["reason"])
        check("PDF: the page count is returned", r["pages"] == 1, r["pages"])
        r = one_page("/pdf-wrong-type")
        check("a PDF under the wrong type is recognised by signature, not by header",
                 r["pages"] == 1 and r["content_type"] == "application/pdf", r)
        r = one_page("/pdf-broken")
        check("a broken PDF is `forbidden` with a reason, not `empty`",
                 r["status"] == "forbidden" and "the PDF was not parsed" in r["reason"], r)

        # A TRUNCATED FILE MUST NOT LOOK BROKEN. RFC 9110 as a PDF weighs 2.86 MB;
        # under a blanket 2 MB download ceiling it is cut, and the parser honestly
        # says "Stream has ended unexpectedly". A caller would conclude that the
        # source is broken and go looking for another, instead of understanding that
        # the source is good and merely large.
        was = reader.PDF_CEILING
        reader.PDF_CEILING = 100          # deliberately smaller than our fixture
        reader._reset_cache()            # otherwise the earlier read comes back
        try:
            r = one_page("/pdf-no-text", fresh=True)
            check("a PDF over the ceiling: the CEILING is named, not the parsing",
                     r["status"] == "forbidden" and "ceiling" in r["reason"], r)
            check("a PDF over the ceiling is not passed off as a broken file",
                     "was not parsed" not in r["reason"], r["reason"])
        finally:
            reader.PDF_CEILING = was
    r = one_page("/ok")
    check("for a non-PDF the page fields are empty rather than absent",
             r["pages"] is None and "pages_read" in r, r.get("pages", "NO SUCH FIELD"))
    r = one_page("/ok", mode="browser")
    check("mode=browser gives `not_reached`, not a quiet fallback to the plain path",
             r["status"] == "not_reached" and "not wired up" in r["reason"], r)

    print("\n== encoding and address ==")
    r = one_page("/cp1251")
    check("a legacy encoding undeclared in the header is read correctly",
             "Работы по кириллице" in r["content"], r["content"][:60])
    r = one_page("/кириллица/путь")
    check("non-ASCII IN THE PATH of an address does not break reading",
             r["status"] == "read", f"{r['status']} {r['reason'][:70]}")

    print("\n== chunked reading ==")
    r = one_page("/long", max_chars=1000)
    check("truncation: no more than asked is returned, plus the hint",
             r["truncated"] is True and r["chars"] < 1200, r["chars"])
    check("truncation: the total is stated", r["total_chars"] > 50000, r["total_chars"])
    check("truncation: how to continue is named", "offset=" in r["content"], r["content"][-90:])
    r2 = one_page("/long", max_chars=1000, offset=1000)
    check("offset: the continuation is a different slice, not the same one",
             r2["offset"] == 1000 and r2["content"][:50] != r["content"][:50],
             f'{r["content"][:24]!r} -> {r2["content"][:24]!r}')
    whole_doc = one_page("/long", max_chars=reader.MAX_CHARS)["content"]
    check("offset: the slice sits exactly where promised",
             whole_doc[1000:1050] == r2["content"][:50],
             f'{whole_doc[1000:1024]!r} vs {r2["content"][:24]!r}')
    r3 = one_page("/long", max_chars=999_999)
    check("max_chars above the ceiling is clamped rather than failing",
             r3["chars"] > 1000, r3["chars"])
    r4 = one_page("/ok", max_chars=1, )
    check("max_chars below the floor is clamped to the floor", r4["chars"] > 1, r4["chars"])

    print("\n== the reference in the text ==")
    r = one_page("/ok", expect=["A real coherent paragraph"])
    check("the marker is found", r["expected_found"] is True, r)
    check("the marker is echoed back", r["expected"] == ["A real coherent paragraph"])
    o = read_at("/ok", expect=["A real coherent paragraph"])
    check("marker found -> all_pages_clean", o["all_pages_clean"] is True, o)
    o = read_at("/ok", expect=["what is not on the page"])
    check("marker NOT found: ok is unchanged, but cleanliness falls",
             o["ok"] is True and o["results"][0]["expected_found"] is False
             and o["all_pages_clean"] is False, o)
    r = one_page("/ok")
    check("no marker set -> expected_found = null (not true)",
             r["expected_found"] is None, r["expected_found"])
    o = read_at("/qrator")
    check("a stub -> all_pages_clean false with no marker at all",
             o["all_pages_clean"] is False, o)

    print("\n== batch, pacing, cache ==")
    reader._reset_rate()
    o = reader.read([API_BASE + "/ok", API_BASE + "/empty", API_BASE + "/qrator"])
    check("batch: three addresses, three outcomes", len(o["results"]) == 3, o)
    check("batch: count and failed count different things",
             o["count"] == 2 and o["failed"] == 1, o)
    check("batch: ok:true although one address was not read", o["ok"] is True)
    reader._reset_rate()
    o = reader.read([API_BASE + f"/x{i}" for i in range(8)])
    check("beyond the ceiling: addresses do NOT vanish silently",
             o["requested"] == 8 and len(o["results"]) == 8, o["requested"])
    check("beyond the ceiling: the extras are marked `not_reached`",
             sum(1 for r in o["results"] if "at most" in r["reason"]) == 3,
             [r["reason"][:40] for r in o["results"]])
    reader._reset_rate()
    o = reader.read([API_BASE + "/qrator", API_BASE + "/pdf-broken"])
    check("every address failed — still ok:true, the work was done",
             o["ok"] is True and o["count"] == 0 and o["failed"] == 2, o)

    reader._reset_cache(); reader._reset_rate()
    a = reader.read([API_BASE + "/ok"])["results"][0]
    b = reader.read([API_BASE + "/ok"])["results"][0]
    check("a second read of the same address comes from the cache",
             a["via"] == "plain" and b["via"] == "cache", f"{a['via']} -> {b['via']}")
    was = len(HITS)
    reader.read([API_BASE + "/ok"])
    check("the cache makes no network request", len(HITS) == was, len(HITS) - was)
    reader._reset_rate()
    c = reader.read([API_BASE + "/ok"], fresh=True)["results"][0]
    check("fresh=true bypasses the cache", c["via"] == "plain", c["via"])

    reader._reset_rate(); reader._reset_cache()
    t0 = time.time()
    reader.read([API_BASE + "/ok", API_BASE + "/live-with-marker"], fresh=True)
    elapsed = time.time() - t0
    check("per-domain pacing: two addresses of one site do not leave in a burst",
             elapsed >= reader.DOMAIN_INTERVAL_S * 0.9, f"{elapsed:.2f} s")

    print("\n== the shape of the answer ==")
    o = reader.read([])
    check("an empty address list is ok:false (the work was NOT done)",
             o["ok"] is False and o["error"], o)
    check("a refusal returns the full top-level field set",
             all(k in o for k in ("contract", "requested", "count", "failed",
                                  "results", "via_used", "paths_available",
                                  "all_pages_clean", "deadline_hit")), sorted(o))
    o = reader.read("not a list")
    check("urls as a string is accepted as one address", o["requested"] == 1, o)
    fields = {"url", "final_url", "domain", "canonical", "status", "reason",
            "http_status", "content_type", "content", "chars", "total_chars",
            "truncated", "offset", "format", "via", "read_at", "elapsed_ms",
            "title", "published", "lang", "links", "links_total", "stub_check",
            "stub_reason", "robots", "expected", "expected_found",
            "pages", "pages_read"}
    reader._reset_rate()
    for path in ("/ok", "/qrator", "/pdf-broken", "/empty"):
        r = one_page(path)
        check(f"all 27 fields are returned on the {r['status']} path too",
                 set(r) >= fields, sorted(fields - set(r)))
    r = reader.read(["http://10.0.0.5/x"])["results"][0]
    check("all 27 fields are returned on a refusal too", set(r) >= fields, sorted(fields - set(r)))
    check("contract is named", reader.read([])["contract"] == "ag.read/2")
    check("paths_available tells the truth about the browser",
             reader.paths()["browser"] == "not_wired_up", reader.paths())

    print("\n== the RELATION check (the PDF reference attribute) ==")
    # It lives in the prober and is checked here: this is a rule of reading, not
    # of probing.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "prober"))
    import probe as _pr
    ts_val = "Method-A 28.4 41.8 " + "x" * 400 + " Method-B 99.9"
    check("the relation holds — counted",
             _pr._near_each_other(ts_val, "Method-A", "28.4", 20) is True)
    check("the relation came apart — not counted",
             _pr._near_each_other(ts_val, "Method-A", "99.9", 20) is False)
    check("a fragment is absent entirely — not counted",
             _pr._near_each_other(ts_val, "Method-A", "no such thing", 20) is False)
    # THE POINT: a value repeated across a document is the norm, not the
    # exception. Taking the first occurrence of each fragment compares a number
    # from the abstract with a method from the table and declares the relation
    # lost.
    t2 = "28.4 in the abstract " + "x" * 900 + " Method-A 28.4 in the table"
    check("a repeated value: ANY pair of occurrences is sought, not the first",
             _pr._near_each_other(t2, "Method-A", "28.4", 20) is True)

    print("\n== the browser path neither invents nor discards ==")
    # TWO DEFECTS OF ONE CLASS: the module asserted what it had not checked, and
    # accepted an argument it did not act on.
    was_browser = reader._via_browser
    caught: dict = {}
    try:
        def stub_fn(url, shot_flag=False, full_page_flag=False):
            caught["full_page"] = full_page_flag
            return {"ok": True, "html": "<html><body><p>" + LONG_TEXT + "</p></body></html>",
                    "png": b"\x89PNG", "error": "", "version": "1.49.1",
                    "status": 403}
        reader._via_browser = stub_fn
        reader._reset_rate(); reader._reset_cache()
        r = one_page("/ok", mode="browser", fresh=True)
        # A LITERAL 200 HERE makes a page served with 403 behind a shield arrive as
        # "200, read": the module asserting something about somebody else's server
        # that it never asked.
        check("the status code comes FROM THE BROWSER, it is not invented",
                 r["http_status"] == 403, r["http_status"])
        # TWO CONSIDERATIONS, BOTH TRUE — HENCE THEY ARE SEPARATED. The code comes
        # from the FIRST response, the text from the LAST state: a shield answers
        # 401, the browser passes it during the settle pause, and the DOM holds real
        # text. A verdict taken from the code throws that text away.
        check("a passed shield is NOT discarded because of the first response code",
                 r["status"] == "read" and r["total_chars"] > 1000,
                 (r["status"], r["total_chars"]))
        check("and the code is NAMED, not hidden",
                 "first response 403" in r["reason"], r["reason"])
        # AND IF THE SHIELD WAS NOT PASSED, the ordinary content checks say so: the
        # status code is beside the point, what was obtained decides.
        reader._via_browser = lambda url, shot_flag=False, full_page_flag=False: {
            "ok": True, "html": "<html><head><script src='/__qrator/x.js'>"
                                "</script></head><body></body></html>",
            "png": b"", "error": "", "version": "1.49.1", "status": 403}
        reader._reset_rate(); reader._reset_cache()
        r2 = one_page("/ok", mode="browser", fresh=True)
        check("a shield that was not passed stays a shield, not `read`",
                 r2["status"] in ("stub", "empty"), (r2["status"], r2["reason"][:60]))
        reader._via_browser = stub_fn
        # And `full_page` was accepted by the door, advertised in the tool schema and
        # silently discarded: the caller saw a shot of the first screen and believed
        # they had seen the page.
        reader._reset_rate(); reader._reset_cache()
        caught.clear()
        reader.screenshot(API_BASE + "/ok", full_page=True)
        check("full_page REACHES the browser rather than being discarded",
                 caught.get("full_page") is True, caught)
        reader._reset_rate(); reader._reset_cache()
        caught.clear()
        reader.screenshot(API_BASE + "/ok")
        check("with no argument the first screen is captured, as declared",
                 caught.get("full_page") is False, caught)
        # THE TEXT TRAVELS WITH THE SHOT. Returned as a count alone, the shot is
        # just a picture: the text to compare it against would have to be fetched
        # by a second call, in another visit, where the page may already differ —
        # and then the two halves of the comparison are of different pages.
        reader._reset_rate(); reader._reset_cache()
        shot = reader.screenshot(API_BASE + "/ok", max_chars=100)
        check("the shot carries the text of THE SAME visit",
                 shot["page_text"][:20] == LONG_TEXT[:20],
                 shot["page_text"][:30])
        check("max_chars bounds the text: a cross-check needs text, not all of it",
                 len(shot["page_text"]) == 100, len(shot["page_text"]))
        # THE LENGTH IS OF THE WHOLE TEXT, NOT OF THE PIECE. Otherwise "the page is
        # silent" and "we brought back a little of it" look identical.
        check("page_text_chars is the WHOLE length, and truncation is named",
                 shot["page_text_chars"] > 100 and shot["page_text_truncated"] is True,
                 (shot["page_text_chars"], shot["page_text_truncated"]))
        reader._reset_rate(); reader._reset_cache()
        refused = reader._shot_refusal(API_BASE + "/ok", "no browser")
        # A REFUSAL CARRIES THE SAME FIELDS: a caller reading `page_text` must not
        # meet a missing key where all went wrong on our side.
        check("a refused shot has the text fields too, empty",
                 refused["page_text"] == "" and refused["page_text_chars"] is None,
                 (refused.get("page_text"), refused.get("page_text_chars")))
    finally:
        reader._via_browser = was_browser
        reader._reset_rate()

    print("\n== a redirect inside the perimeter ==")
    # REPRODUCED LIVE: a local 302 to an internal address yielded a read status
    # and an internal final_url. The one defect of this set with consequences
    # BEYOND the quality of results: the address comes out of somebody else's
    # result set, so our code does not choose the redirect target.
    was_internal = reader._is_internal
    try:
        reader._is_internal = lambda host: (host or "").lower() == "127.0.0.2"
        reader._reset_rate(); reader._reset_cache()
        port2 = site.server_address[1]
        r = one_page(f"/redirect?to=http://127.0.0.2:{port2}/ok", fresh=True)
        check("the inward redirect is STOPPED, not read",
                 r["status"] == "forbidden", (r["status"], r["reason"][:70]))
        check("it says WE stopped the hop, not that the site refused",
                 "hop stopped" in r["reason"] and "perimeter" in r["reason"],
                 r["reason"])
        check("the content of the internal address did not leave",
                 not r["content"] and not r["final_url"],
                 (r["content"][:40], r["final_url"]))
        # AND THE REVERSE: an ordinary redirect must not suffer. A guard that breaks
        # legitimate redirects is worse than none — it gets switched off entirely.
        reader._reset_rate(); reader._reset_cache()
        r = one_page("/redirect?to=/ok", fresh=True)
        check("a legitimate redirect still passes",
                 r["status"] == "read" and r["final_url"].endswith("/ok"),
                 (r["status"], r["final_url"]))
    finally:
        reader._is_internal = was_internal
        reader._reset_rate()

    print("\n== parallel reading: faster, with per-domain pacing intact ==")
    # THE RATE LIMITER IS THE MAIN THING THAT CAN BREAK HERE. One site closed its
    # door to our address FOR GOOD after three dozen consecutive requests, and
    # every future read pays for it. Parallelism that removed the pacing would
    # trade seconds for sources.
    was_interval = reader.DOMAIN_INTERVAL_S
    try:
        reader.DOMAIN_INTERVAL_S = 0.4
        reader._reset_rate()
        reader._reset_cache()
        paths = ["/ok", "/live-with-marker", "/cp1251", "/long"]
        t0 = time.time()
        own = reader.read_many([API_BASE + pth for pth in paths])
        took_one_domain = time.time() - t0
        check("the order of answers matches the order of addresses",
                 [r["url"] for r in own] == [API_BASE + pth for pth in paths],
                 [r["url"][-12:] for r in own])
        check("every address got an outcome, none was lost",
                 len(own) == len(paths) and all(r["status"] for r in own))
        # FOUR PAGES OF ONE DOMAIN ARE READ IN SEQUENCE, not at once: an interval
        # must fall between them. Three intervals of 0.4 s is the lower bound; with
        # the pacing removed it would come out noticeably less.
        check("pages of ONE domain are read in sequence, the pacing holds",
                 took_one_domain >= 3 * reader.DOMAIN_INTERVAL_S,
                 f"{took_one_domain:.2f} s against a bound of "
                 f"{3 * reader.DOMAIN_INTERVAL_S:.2f}")

        # DIFFERENT DOMAINS GO AT ONCE. The same site under two names: to the
        # limiter those are different domains, and they have no reason to wait for
        # each other.
        reader._reset_rate()
        reader._reset_cache()
        port = site.server_address[1]
        two_hosts = [f"http://127.0.0.1:{port}/ok", f"http://localhost:{port}/cp1251",
               f"http://127.0.0.1:{port}/live-with-marker",
               f"http://localhost:{port}/long"]
        t0 = time.time()
        r2 = reader.read_many(two_hosts)
        took_two_domains = time.time() - t0
        check("two domains are read IN PARALLEL, not one after another",
                 took_two_domains < took_one_domain,
                 f"{took_two_domains:.2f} s against {took_one_domain:.2f} s "
                 "on the same number of pages")
        check("parallel reading returns content, not blanks",
                 all(r["status"] in ("read", "empty") for r in r2),
                 [r["status"] for r in r2])

        # ONE ADDRESS FAILING DOES NOT CANCEL THE REST.
        reader._reset_rate()
        mixed = reader.read_many([API_BASE + "/ok", "http://does-not-exist.invalid/x",
                                     API_BASE + "/cp1251"])
        check("a broken address does not take its neighbours down",
                 mixed[0]["status"] == "read"
                 and mixed[2]["status"] == "read"
                 and mixed[1]["status"] != "read",
                 [r["status"] for r in mixed])
        check("an empty address list gives an empty answer, not a refusal",
                 reader.read_many([]) == [])
    finally:
        reader.DOMAIN_INTERVAL_S = was_interval
        reader._reset_rate()

    print("\n== a scan: recognition instead of advice to recognise ==")
    # A FAKE RENDERER AND A FAKE MODEL, not live ones. What is checked is the
    # BRANCHING, not the quality of recognition: quality is measured on real
    # pages, while a test must answer the same on any machine.
    import render as _rnd
    import model as _mdl
    # ALL THREE ARE SET, NOT JUST A KEY. With an arbitrary provider (LLM_*),
    # readiness for vision is made of the key, the base URL AND the name of the
    # vision model: providers do not always have vision, and price it differently.
    was = (_rnd.HAS_RENDERER, _rnd.pages_to_raster, _mdl.API_KEY, _mdl.recognise,
            _mdl.API_BASE, _mdl.MODEL_VISION)
    try:
        _rnd.HAS_RENDERER = True
        _rnd.pages_to_raster = lambda msg_body, how_many=6, dpi=0: (
            [b"\x89PNG\r\n\x1a\n"] * 2,
            {"dpi_requested": 300, "dpi_actual": 160, "dpi_steps_down": 2,
             "pages_rendered": 2, "format": "png", "payload_b64_bytes": 4096})

        _mdl.API_KEY, _mdl.API_BASE = "not-a-real-key", "http://no.model.invalid"
        _mdl.MODEL_VISION = "fake-vision-model"
        # The provider SAID the answer was complete: `truncated: False`. That is not
        # the same as staying silent — see the check below.
        _mdl.recognise = lambda images, hint="", model="", max_out=0, timeout=0: {
            "ok": True, "content": "=== PAGE 1 ===\nArticle\t47.48",
            "usage": {"input_tokens": 10, "output_tokens": 5}, "error": "",
            "truncated": False}
        r = one_page("/pdf-no-text", fresh=True)
        check("a scan is RECOGNISED in the same call: `read`, not `empty`",
                 r["status"] == "read", r["status"])
        # THE COST FOLLOWS THE EXPLICITNESS OF THE REQUEST. Reading was called on
        # a document, so the document is read — recognition is how a scan is read.
        # Search was called for links, and a scan that landed in the top three is
        # not a reason to bill for a vision model: there the answer NAMES the path
        # instead of leaving an empty field.
        reader._reset_rate(); reader._reset_cache()
        without = reader.read_many([API_BASE + "/pdf-no-text"], recognise=False)[0]
        check("with recognition off a scan is not recognised, and says so",
                 without["text_source"] == "not_recognised (scan)",
                 without.get("text_source"))
        check("and the refusal names the way out rather than falling silent",
                 "web_read" in without["reason"], without["reason"])
        # AND IT IS NOT REMEMBERED UNDER THE ADDRESS. Cached, this refusal would
        # answer a later `web_read` — which asked for exactly the thing refused.
        after = one_page("/pdf-no-text")
        check("a refusal on somebody else's behalf is not cached over reading",
                 after["status"] == "read" and after["text_source"] == "recognised",
                 (after["status"], after.get("text_source")))
        check("a complete answer is marked as NOT truncated",
                 (r["recognition"] or {}).get("truncated") is False,
                 (r["recognition"] or {}).get("truncated"))
        # THREE STATES, NOT TWO. A provider that reported nothing must not look like
        # one that reported "complete": gateways without a finish reason would take
        # us back to where the field came from.
        good_one = _mdl.recognise          # put it back at once: below it is needed intact
        _mdl.recognise = lambda images, hint="", model="", max_out=0, timeout=0: {
            "ok": True, "content": "text", "usage": {}, "error": ""}
        r = one_page("/pdf-no-text", fresh=True)
        check("a silent provider gives not-reported, not complete",
                 (r["recognition"] or {}).get("truncated") is None,
                 (r["recognition"] or {}).get("truncated"))
        _mdl.recognise = good_one
        r = one_page("/pdf-no-text", fresh=True)
        check("the text is returned, not advice on getting it",
                 "47.48" in r["content"], r["content"][:60])
        check("HOW the text was obtained is stated: recognised, not a text layer",
                 r["text_source"] == "recognised", r["text_source"])
        # TWO DIFFERENT QUESTIONS, AND TWO FIELDS FOR THEM. `via` is how the FILE was
        # obtained, and it was obtained by a plain download; `text_source` is how the
        # TEXT was obtained. Writing "ocr" into `via` breaks both: the field is
        # rewritten by the transport anyway, and the fetch-path summary counts
        # transport.
        check("transport stays transport; recognition does not replace it",
                 r["via"] == "plain" and r["text_source"] == "recognised",
                 (r["via"], r["text_source"]))
        # THE KEY FIELD. Without it "the model read it badly" and "we gave it 72 dpi
        # instead of 300" are indistinguishable.
        rec = r["recognition"] or {}
        check("the resolution IS IN THE ANSWER: requested, actual, and the steps down",
                 (rec.get("dpi_requested"), rec.get("dpi_actual"),
                  rec.get("dpi_steps_down")) == (300, 160, 2), rec)
        check("the model used for recognition is named",
                 rec.get("model") == _mdl.MODEL_VISION, rec.get("model"))
        check("recognised text is NOT passed off as a quotation: the accuracy is stated",
                 "not quoted" in (rec.get("fidelity") or ""), rec.get("fidelity"))
        check("the cost of recognition is visible", bool(rec.get("usage")), rec.get("usage"))
        # The fake document has one page and two were "rendered" — there must be no
        # tail; the opposite is checked on a document with more pages than were
        # rendered.
        out = reader._blank("http://x/s.pdf", "read", "")
        out["pages"] = 9
        x = reader._recognise_scan(out, EMPTY_PDF, 0, 9)
        check("the unrecognised tail of pages is NAMED, not lost silently",
                 "9" in x["reason"] and "first 2 pages" in x["reason"], x["reason"])

        # TRUNCATED RECOGNITION IS A FAILURE INDISTINGUISHABLE FROM A SUCCESS if it
        # is not declared: the text ends in the middle of the document while the
        # status says the page was read.
        _mdl.recognise = lambda images, hint="", model="", max_out=0, timeout=0: {
            "ok": True, "content": "=== PAGE 1 ===\nArticle\t47.4",
            "usage": {}, "error": "", "truncated": True}
        r = one_page("/pdf-no-text", fresh=True)
        # THE FLAG LIVES IN `recognition`, NOT IN `truncated`, and this test pins
        # that down. `truncated` answers a different question — "we returned less
        # than all the text we have, the rest is fetched with a cursor". There the
        # text is complete and a slice is shown; here the text is itself incomplete.
        check("truncated recognition is NAMED truncated, not complete",
                 (r["recognition"] or {}).get("truncated") is True,
                 (r["recognition"] or {}).get("truncated"))
        check("and it does NOT replace cursor truncation — different events",
                 r["truncated"] is False, r["truncated"])
        check("the reason for the truncation is in words, not only in a flag",
                 "cut off" in r["reason"], r["reason"])
        check("the text obtained is NOT discarded",
                 "47.4" in r["content"], r["content"][:40])

        # A MODEL REFUSAL IS NOT AN EMPTY DOCUMENT. The failure direction is the
        # module's own: no data means "not checked", never "sound".
        _mdl.recognise = lambda images, hint="", model="", max_out=0, timeout=0: {
            "ok": False, "content": "", "usage": {}, "error": "the model answered 429"}
        r = one_page("/pdf-no-text", fresh=True)
        check("the model refused — the document is NOT declared empty in substance",
                 r["status"] == "empty" and "429" in r["reason"], r["reason"])
        check("on a model refusal the resolution is returned anyway",
                 (r["recognition"] or {}).get("dpi_actual") == 160, r["recognition"])

        # AN EMPTY ANSWER FROM THE MODEL IS A SEPARATE EVENT, not "the page has no
        # text".
        _mdl.recognise = lambda images, hint="", model="", max_out=0, timeout=0: {
            "ok": True, "content": "   ", "usage": {}, "error": ""}
        r = one_page("/pdf-no-text", fresh=True)
        check("an empty model answer is named an empty ANSWER, not an empty PDF",
                 "returned empty text" in r["reason"], r["reason"])

        # THE RENDERER COULD NOT — its own reason too, not a general emptiness.
        _rnd.pages_to_raster = lambda msg_body, how_many=6, dpi=0: (
            [], {"dpi_requested": 300, "dpi_actual": None, "dpi_steps_down": 0,
                 "pages_rendered": 0, "error": "PdfiumError: broken raster"})
        r = one_page("/pdf-no-text", fresh=True)
        check("the render failed — it says render, not model",
                 "were not rendered" in r["reason"] and "PdfiumError" in r["reason"],
                 r["reason"])

        # NO KEY — recognition was not attempted, and that is DECLARED.
        _mdl.API_KEY = ""
        r = one_page("/pdf-no-text", fresh=True)
        check("with no key: it says recognition was not attempted, and why",
                 "was not attempted" in r["reason"] and "LLM_API_KEY" in r["reason"],
                 r["reason"])
        # VISION IS ASKED ABOUT SEPARATELY FROM TEXT. An operator may have configured
        # a text model and no vision model: providers do not always have vision. A
        # scan must then name THAT reason specifically, rather than a general "no
        # key", and certainly must not call a text model with a picture.
        _mdl.API_KEY, _mdl.MODEL_VISION = "not-a-real-key", ""
        r = one_page("/pdf-no-text", fresh=True)
        check("vision unconfigured — the VISION variable specifically is named",
                 "LLM_MODEL_VISION" in r["reason"], r["reason"])
    finally:
        (_rnd.HAS_RENDERER, _rnd.pages_to_raster, _mdl.API_KEY, _mdl.recognise,
         _mdl.API_BASE, _mdl.MODEL_VISION) = was

    print("\n== the dpi ladder: a rule, not a table of numbers ==")
    if _rnd.HAS_RENDERER:
        # A real renderer exists only inside the image. The scan is built RIGHT HERE
        # out of pictures — such a file has no text layer physically, not by our
        # declaration.
        import io as _io
        import pypdfium2 as _pdfium
        from PIL import Image as _Im
        canvas = _Im.new("RGB", (900, 1200), "white")
        buf_ = _io.BytesIO()
        canvas.save(buf_, format="PDF", save_all=True, append_images=[canvas])
        scan_pdf = buf_.getvalue()
        was_ceiling = _rnd.BASE64_CEILING
        try:
            steps = []
            # The second ceiling is deliberately unreachable: an empty white canvas
            # compresses so well that 64 KB was enough even at 160 dpi, and the
            # "we hit the floor" test then checked something other than it announced.
            for ceiling in (50 * 1024 * 1024, 1024):
                _rnd.BASE64_CEILING = ceiling
                _, info = _rnd.pages_to_raster(scan_pdf, 2)
                steps.append((info["dpi_actual"], info["dpi_steps_down"],
                             info.get("over_limit")))
            check("a tight ceiling does NOT raise dpi (the ladder only goes down)",
                     steps[1][0] <= steps[0][0], steps)
            check("a step down is COUNTED rather than happening silently",
                     steps[1][1] >= steps[0][1], steps)
            check("hit the floor and still did not fit — said, not hidden",
                     steps[1][2] is True, steps)
        finally:
            _rnd.BASE64_CEILING = was_ceiling
    else:
        check("the dpi ladder is checked in the image (no renderer here)", False,
                 "run tests/in-image.sh")

    print("\n== robots.txt: reported, not enforced ==")
    r = one_page("/disallowed/here")
    check("robots: the site's ban IS VISIBLE in the answer",
             r["robots"] == "disallowed_by_site", r["robots"])
    check("robots: but the page was read anyway (there is no gate)",
             r["status"] == "read", r["status"])
    r = one_page("/ok")
    check("robots: an allowed path is marked differently",
             r["robots"] == "allowed", r["robots"])

    site.shutdown()
    print(f"\nreading suite: ok {ok_count}, failed {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
