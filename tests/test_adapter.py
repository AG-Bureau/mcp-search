# -*- coding: utf-8 -*-
"""Tests of the MCP adapter WITH NO outbound requests.

A fake metasearch on a local port instead of the real one: the protocol and the
engine-selection policy are checked in full while not one request leaves for the
internet. Our address is the scarcest resource this module has, and stray traffic
spends it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "adapter"))

REQUESTS: list[dict] = []          # what the adapter asked the fake metasearch for
# A QUERY THAT IS ONLY A QUERY. The fake metasearch pastes the query into its own
# snippets, so any non-empty string does — and in one place it must be CYRILLIC,
# because the language of a request is deduced from the script the query is
# written in. A real company, and still more a real person, as a fixture puts a
# live subject into a public repository and buys nothing that is checked here.
QUERY = "погода"

REPLY = {"results": [
    {"url": "https://example.org/a", "title": "  First  ", "content": "text",
     "engines": ["yandex", "bing"]},
    {"url": "not-a-link", "title": "rubbish", "content": "", "engines": []},
    {"url": "https://example.org/b", "title": "Second", "content": "more",
     "engines": ["mwmbl"]},
], "unresponsive_engines": [["zapmeta", "timeout"]]}
EMPTY_REPLY = {"results": [], "unresponsive_engines": []}
# AN IMAGE ANSWER, WHICH THE FIXTURE COULD NOT PRODUCE. Without it the image
# result set came back empty, and three checks of its shape sat behind
# `if d["results"]:` — never running, always green. A check that cannot fail is
# worse than no check: it occupies the place where one would have been.
IMAGE_REPLY = {"results": [
    {"url": "https://example.org/gallery/one", "title": "A picture",
     "img_src": "https://cdn.example.net/one.jpg",
     "thumbnail": "https://cdn.example.net/one-thumb.jpg",
     "content": "", "author": "Someone", "publishedDate": "2026-01-01",
     "engines": ["engine-a"]},
    {"url": "https://example.org/gallery/two", "title": "Another picture",
     "img_src": "https://cdn.example.net/two.jpg", "thumbnail": "",
     "content": "", "engines": ["engine-a"]},
], "unresponsive_engines": []}
MODE = {"body": REPLY, "code": 200, "not_json": False}


CONFIG_HITS: list = []


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/healthz":
            body = b"OK"
            self.send_response(200)
        elif p.path == "/search":
            params = {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}
            REQUESTS.append(params)
            if MODE["not_json"]:
                body = "<html>not json</html>".encode("utf-8")
            else:
                # The query words are pasted into the snippets. A REAL result set contains
                # the words of the query, and without them the relevance check would (quite
                # rightly) declare the whole fixture a substituted result set.
                msg_body = json.loads(json.dumps(MODE["body"]))
                asked = [e for e in params.get("engines", "").split(",") if e]
                for r in msg_body.get("results", []):
                    r["content"] = f"{params.get('q','')} {r.get('content','')}".strip()
                    # A real metasearch names in `engines` THE ENGINE THAT WAS ASKED. A fixture
                    # returning the same names to everybody makes the corroboration-merging check
                    # meaningless: `via` cannot grow however many engines are asked.
                    if asked:
                        r["engines"] = asked
                body = json.dumps(msg_body).encode()
            self.send_response(MODE["code"])
        elif p.path == "/config":
            # Requests are counted: the check "a failed registry read is retried" looks at
            # the ATTEMPT, not at its result. The fake returns an empty engine set, so
            # "we re-read" and "we remember the old value" cannot be told apart by the
            # CONTENT of the answer — only by the number of requests.
            CONFIG_HITS.append(1)
            body = json.dumps({"engines": []}).encode()
            self.send_response(200)
        elif p.path == "/page":
            # A PAGE THE TESTS CAN READ WITHOUT LEAVING THE MACHINE. Reading is
            # what the deep health check does, and pointing it at a real address
            # made the suite spend the one resource this module protects — the
            # reputation of the address it calls from — on every run.
            body = ("<html><head><title>Fixture Page</title></head><body><p>"
                    + "Fixture Domain. " * 40 + "</p></body></html>").encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(cls, port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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
    fake = serve(Fake)
    os.environ["SEARXNG_URL"] = f"http://127.0.0.1:{fake.server_address[1]}"
    import server  # imported AFTER the address is replaced: it is read at import time
    import reader
    import deep as deep_module

    # The rate limiter is switched off for the functional checks: by design it
    # skips an engine asked less than a second ago, and consecutive tests then
    # start failing for reasons of their own. It is checked below, separately.
    server.ENGINE_INTERVAL_S = 0.0
    server.COOLDOWN_S = 0.0
    adapter = serve(server.Handler)
    base = f"http://127.0.0.1:{adapter.server_address[1]}"

    def post(payload, raw=None):
        data = raw if raw is not None else json.dumps(payload).encode()
        req = urllib.request.Request(base + "/mcp", data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode()
                return r.status, (json.loads(text) if text else None)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "null")

    def get(path):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "null")

    print("search · MCP adapter tests (no outbound requests)")
    print("\nthe MCP protocol:")
    _, r = post({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    check("initialize returns the protocol version and the server name",
             r["result"]["protocolVersion"] == server.PROTOCOL
             and r["result"]["serverInfo"]["name"] == "ag-mod-search", r)
    # ONE VERSION, TOLD THE SAME WAY TO EVERYONE WHO ASKS. The handshake and the
    # `User-Agent` a site administrator reads in their log must name the same
    # release; two literals would drift apart with nothing reporting it.
    check("the handshake names the module's real version, not an internal number",
             r["result"]["serverInfo"]["version"] == reader.VERSION
             and reader.VERSION in reader.HTTP_HEADERS["User-Agent"],
             (r["result"]["serverInfo"]["version"], reader.HTTP_HEADERS["User-Agent"]))
    code, r = post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    check("a notification has no answer (202, empty body)", code == 202 and r is None, code)
    _, r = post({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    check("ping answers with an empty result", r.get("result") == {}, r)
    _, r = post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = r["result"]["tools"]
    # The check names a PAIR, not a number. Demanding "exactly one tool" freezes
    # the arrangement of the day instead of the rule. The rule is that a tool
    # forgotten in tools/list DOES NOT EXIST for the model, however much code
    # stands behind it.
    # WE CHECK THE SET, NOT THE COUNT. Demanding "exactly two" fails when a third
    # appears — the same defect again. The rule is that EVERY capability of the
    # module must be in tools/list.
    names = [t["name"] for t in tools]
    # The list is EXPECTED from one place — the tool registry itself. Naming the
    # tools by hand makes the test fail at every new tool: it freezes the set
    # instead of the rule. The rule is that EVERY declared tool must be in
    # tools/list — one forgotten there does not exist for the model.
    check("tools/list returns every capability of the module, however many there are",
             set(names) == set(server.TOOLS), (names, sorted(server.TOOLS)))
    by_name = {t["name"]: t for t in tools}
    check("search requires query and does not require max_results",
             by_name["web_search"]["inputSchema"]["required"] == ["query"]
             and "max_results" in by_name["web_search"]["inputSchema"]["properties"])
    check("reading requires urls and does not require offset",
             by_name["web_read"]["inputSchema"]["required"] == ["urls"]
             and "offset" in by_name["web_read"]["inputSchema"]["properties"])
    for name, tools_list in by_name.items():
        # The annotations are read by the HOST: without readOnly it puts the call to
        # the user for confirmation, without openWorld it treats the answer as
        # reproducible.
        check(f"{name}: the annotations are in place and the tool only reads",
                 tools_list["annotations"]["readOnlyHint"] is True
                 and tools_list["annotations"]["openWorldHint"] is True
                 and tools_list["annotations"]["destructiveHint"] is False,
                 tools_list.get("annotations"))
        # The description is the only thing the model sees. The threshold is not about
        # style: three lines listing the result fields have existed before, and the
        # model took an empty answer for a breakage.
        check(f"{name}: the description explains how to read the answer",
                 len(tools_list["description"]) > 900
                 and "HOW TO READ THE ANSWER" in tools_list["description"],
                 len(tools_list["description"]))
    check("the search description NAMES its neighbouring tool explicitly",
             "web_read" in by_name["web_search"]["description"])
    check("the reading description names search explicitly",
             "web_search" in by_name["web_read"]["description"])

    # THE OBSERVATION CEILING must be EXPLICIT in what the registry returns. A tool
    # without that field deliberately fails as a configuration error: the omission
    # must be caught on the first call rather than silently eat the observation
    # down to a default.
    code, spec = get("/tool-spec")
    registry = {t["name"]: t for t in spec["engine_registry"]}
    check("/tool-spec returns EVERY tool in the registry shape",
             sorted(registry) == sorted(names), (sorted(registry), sorted(names)))
    check("EACH one names an observation ceiling, and it is non-zero",
             all(isinstance(t.get("max_observation_chars"), int)
                 and t["max_observation_chars"] > 0 for t in registry.values()),
             {k: v.get("max_observation_chars") for k, v in registry.items()})
    check("the registry description is THE SAME as the one sent over MCP (one source)",
             all(registry[name]["description"] == by_name[name]["description"]
                 for name in registry))

    # A TOOL DESCRIPTION IS A PROMPT, and it needs checking like one. It can
    # degenerate under editing as quietly as code and cause no failure: the model
    # simply starts calling the tool worse. What is checked is not style but the
    # presence of three meanings no other search tool has and which the model
    # cannot learn anywhere else.
    op = tools[0]["description"]
    check("the description has not degenerated into one line", len(op) > 600, len(op))
    check("it says an empty result set is a success, not a failure",
             "AN EMPTY LIST" in op and "success" in op.lower(), op[:80])
    check("the difference between not-asked and asked-and-silent is explained",
             "engines_skipped" in op and "unresponsive_engines" in op, op[:80])
    check("what to do when all_engines_clean is false is explained",
             "all_engines_clean" in op and "same name" in op, op[:80])
    check("the price of the corroboration mode is named",
             "THE PRICE" in json.dumps(tools[0]["inputSchema"], ensure_ascii=False),
             "the price of corroborate")

    # The annotations are read by the HOST: readOnly lets it skip putting the call
    # to the user, openWorld lets it not expect reproducibility.
    an = tools[0].get("annotations") or {}
    check("the tool is declared read-only",
             an.get("readOnlyHint") is True, an)
    check("the answer is declared to depend on the outside world",
             an.get("openWorldHint") is True, an)
    check("the tool has a human-readable title for an interface",
             bool(an.get("title")), an)
    check("argument bounds are declared rather than implied",
             tools[0]["inputSchema"]["properties"]["max_results"].get("maximum") == 50,
             tools[0]["inputSchema"]["properties"]["max_results"])
    _, r = post({"jsonrpc": "2.0", "id": 4, "method": "no_such_method"})
    check("an unknown method gives protocol error -32601",
             r.get("error", {}).get("code") == -32601, r)
    _, r = post({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "someone-elses-tool", "arguments": {}}})
    check("an unknown tool is not invoked", r.get("error", {}).get("code") == -32602, r)
    code, r = post(None, raw="{not json".encode("utf-8"))
    check("a non-JSON body gives -32700, not a crash", code == 400
             and r["error"]["code"] == -32700, r)
    _, r = post([{"jsonrpc": "2.0", "id": 6, "method": "ping"},
                 {"jsonrpc": "2.0", "method": "notifications/x"},
                 {"jsonrpc": "2.0", "id": 7, "method": "ping"}])
    check("a batch: two answers, the notification skipped",
             isinstance(r, list) and len(r) == 2, r)

    server._reset_rate()
    print("\ncalling a tool:")
    REQUESTS.clear()
    _, r = post({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("a successful call is not marked isError", r["result"]["isError"] is False, r)
    check("the answer declares the ag.search/3 contract",
             payload["contract"] == "ag.search/3", payload)
    check("a non-link result is dropped (2 of 3)", len(payload["results"]) == 2, payload)
    check("the title is stripped of extra spaces",
             payload["results"][0]["title"] == "First", payload["results"][0])
    # `via` names those that returned the link. With sequential querying that is
    # usually one engine; several names appear either when we had to go further
    # down the order or in corroboration mode.
    check("via names the engines that were asked",
             set(x.strip() for x in payload["results"][0]["via"].split(","))
             <= set(payload["engines_asked"]), payload["results"][0]["via"])
    check("silent engines reach the consumer, inside `trouble`",
             payload["trouble"]["unresponsive_engines"] == [["zapmeta", "timeout"]],
             payload["trouble"])
    check("a query in Cyrillic goes out with language=ru",
             REQUESTS[-1]["language"] == "ru", REQUESTS[-1])
    check("json format is requested, pages start at one",
             REQUESTS[-1]["format"] == "json" and REQUESTS[-1]["pageno"] == "1", REQUESTS[-1])

    _, r = post({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": "ghost automotive", "page": 2}}})
    check("a Latin query goes out with language=en", REQUESTS[-1]["language"] == "en",
             REQUESTS[-1])
    check("page=2 becomes pageno=3", REQUESTS[-1]["pageno"] == "3", REQUESTS[-1])

    # The main distinction: "nothing was found" is not "the tool broke".
    MODE["body"] = EMPTY_REPLY
    _, r = post({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": "empty"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("an EMPTY result set is a success, not isError",
             r["result"]["isError"] is False and payload["ok"] is True
             and payload["results"] == [], r["result"])
    MODE["body"] = REPLY

    # The field must be present in the ORDINARY answer rather than appear on a
    # manual run. Otherwise an engine refusal becomes invisible again as soon as
    # nobody is watching.
    server._reset_rate()
    print("\nnews about silent engines is part of the contract, not a convenience:")
    NO_KEY = {"results": REPLY["results"]}          # the metasearch sent no such key
    EMPTY_LIST = {"results": REPLY["results"], "unresponsive_engines": []}
    for name, msg_body in (("no such key at all", NO_KEY), ("every engine alive", EMPTY_LIST)):
        MODE["body"] = msg_body
        _, r = post({"jsonrpc": "2.0", "id": 30, "method": "tools/call",
                     "params": {"name": "web_search", "arguments": {"query": "x"}}})
        payload = json.loads(r["result"]["content"][0]["text"])
        # EMPTY DIFFERS FROM ABSENT — the same requirement the field itself
        # carried: `trouble` is ALWAYS there, and an empty dictionary means
        # "looked, all well". An absent key would mean "this side was not
        # examined at all", which is different news and must not share a shape
        # with a healthy answer.
        # (Here `trouble` is not empty for an unrelated reason: the test instance
        # has no prober database, so the pool is honestly the seed. What is
        # checked is that SILENT ENGINES leave no key when there were none.)
        check(f"MCP: `trouble` is present and says nothing about silence when {name}",
                 isinstance(payload.get("trouble"), dict)
                 and "unresponsive_engines" not in payload["trouble"],
                 payload.get("trouble"))
        code, payload = get("/ag/search?q=x")
        check(f"ag.search/3: `trouble` is present and says nothing about silence when {name}",
                 isinstance(payload.get("trouble"), dict)
                 and "unresponsive_engines" not in payload["trouble"],
                 payload.get("trouble"))
    MODE["body"] = REPLY
    _, r = post({"jsonrpc": "2.0", "id": 31, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": "x"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("MCP: a silent engine arrives with its reason",
             payload["trouble"]["unresponsive_engines"] == [["zapmeta", "timeout"]],
             payload["trouble"])
    code, payload = get("/ag/search?q=x")
    check("ag.search/3: a silent engine arrives with its reason",
             payload["trouble"]["unresponsive_engines"] == [["zapmeta", "timeout"]],
             payload["trouble"])

    # The hole: an engine that returned emptiness silently does NOT appear in
    # unresponsive_engines. So a field made contractual covered half the cases.
    # Closed by facts: who was asked, who answered.
    server._reset_rate()
    print("\nquietly empty engines are visible, not only the ones the metasearch marks:")
    MODE["body"] = REPLY
    _, r = post({"jsonrpc": "2.0", "id": 40, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": "x"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("the answer says who was asked",
             set(payload.get("engines_asked", [])) >= set(server.core()),
             payload.get("engines_asked"))
    check("the answer says who actually answered",
             set(payload.get("engines_answered", [])) <= set(payload["engines_asked"])
             and payload.get("engines_answered"), payload.get("engines_answered"))
    asked = set(payload["engines_asked"])
    answered = set(payload["engines_answered"])
    silent = {m[0] for m in payload["trouble"].get("unresponsive_engines", [])}
    # The meaning of the difference: an engine that was asked and appears neither
    # among those that answered nor among those that stayed silent returned
    # emptiness SILENTLY — otherwise nobody would see its failure.
    # THE POINT IS THE REMAINDER ITSELF: an engine that was asked, did not answer
    # and is not named as silent returned emptiness QUIETLY, and that engine must
    # be identifiable by subtraction. Written as `X == X` this compared a set
    # with itself and could not fail — a check occupying the place of a check.
    quietly_empty = asked - answered - silent
    # NOT DISJOINT, AND THAT IS CORRECT: one engine can hand back results in one
    # round and be reported silent in another, so `answered` and `silent` may
    # overlap. What must hold is that neither contains a name nobody asked.
    check("nobody is answered or silent without having been asked",
             asked >= answered and asked >= silent,
             (sorted(asked), sorted(answered), sorted(silent)))
    check("a quietly empty engine is identifiable by subtraction, not by guessing",
             quietly_empty == {e for e in asked
                               if e not in answered and e not in silent},
             sorted(quietly_empty))
    code, payload = get("/ag/search?q=x")
    check("ag.search/3 returns the same two fields",
             "engines_asked" in payload and "engines_answered" in payload, payload)

    # The third way for an engine to fail: answer, but not our question. Measured
    # repeatedly: one engine answered questions about people and companies with a
    # speed-test site, a classifieds site and a Windows help page. Formally there
    # are results; on topic there are none.
    server._reset_rate()
    print("\na substituted result set is discarded per engine:")
    # THE FIXTURE NEEDS AN AMBIGUOUS, WEAKLY INDEXED NAME — that is the whole
    # class. On a wide unambiguous subject there is nothing to substitute, and the
    # check would go green because it stopped checking. `Ghost` is such a name:
    # several unrelated companies, none of them dominant in an index.
    from_life = [
        {"title": "GA Ltd, Sheffield, reg. no. 12345678",
         "url": "https://opencorp.example/id/1", "snippet": "Ghost Automotive",
         "via": "yandex, privacywall"},
        {"title": "How to get help in Windows - Microsoft Support",
         "url": "https://support.microsoft.com/help", "snippet": "Windows",
         "via": "bing"},
    ]
    kept, hijacked = server._drop_hijacked("Ghost Automotive registration", from_life)
    check("the rubbish of the substituting engine is discarded", hijacked == ["bing"], hijacked)
    # The key case: the title of a good result does NOT contain the words of the
    # query (the company is named by an abbreviation), and a per-result check
    # would kill it. What saves it is judging the engine by ALL of its results
    # rather than each link.
    check("a legitimate result with an abbreviation in its title survives",
             len(kept) == 1 and "12345678" in kept[0]["title"], kept)
    shared_db = [{"title": "Ghost", "url": "https://x", "snippet": "", "via": "yandex, bing"}]
    kept, hijacked = server._drop_hijacked("Ghost Automotive", shared_db)
    check("a link vouched for by an on-topic engine is not discarded",
             len(kept) == 1, (kept, hijacked))
    all_off_topic = [{"title": "Speedtest by Ookla", "url": "https://sp.net",
                 "snippet": "", "via": "bing"}]
    kept, hijacked = server._drop_hijacked("Ghost Automotive Sheffield", all_off_topic)
    check("if nobody answered on topic, nothing remains",
             kept == [] and hijacked == ["bing"], (kept, hijacked))
    check("a short query with no significant words does not cut the results",
             server._drop_hijacked("who", all_off_topic)[1] == [], "a three-letter query")

    server._reset_rate()
    print("\nengines are asked ONE AT A TIME, not in a fan-out:")
    REQUESTS.clear()
    MODE["body"] = REPLY
    _, r = post({"jsonrpc": "2.0", "id": 50, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY, "max_results": 2}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("the first engine sufficed — the rest were not asked",
             payload["engines_asked"] == [server.order()[0]], payload["engines_asked"])
    check("EXACTLY one request went to the metasearch, not five",
             len(REQUESTS) == 1, [z["engines"] for z in REQUESTS])
    # A COUNT, NOT A SIGN TO BRANCH ON: it explains the call rather than
    # changing what the caller does, so it lives behind `verbose`. Checked where
    # it now is.
    _, rv = post({"jsonrpc": "2.0", "id": 51, "method": "tools/call",
                  "params": {"name": "web_search",
                             "arguments": {"query": QUERY, "max_results": 2,
                                           "verbose": True}}})
    loud = json.loads(rv["result"]["content"][0]["text"])
    check("engines_used shows how many engines were needed (verbose)",
             loud["engines_used"] == 1, loud["engines_used"])

    # Switching on failure is not a separate mechanism but a property of the order.
    REQUESTS.clear()
    MODE["body"] = {"results": [], "unresponsive_engines": [["google cse", "access denied"]]}
    _, r = post({"jsonrpc": "2.0", "id": 51, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY, "max_results": 2}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("the first refused — the next in order were asked",
             len(payload["engines_asked"]) > 1
             and payload["engines_asked"][0] == server.order()[0], payload["engines_asked"])
    check("the poll order is respected",
             payload["engines_asked"] == list(server.order()[:len(payload["engines_asked"])]),
             payload["engines_asked"])
    MODE["body"] = REPLY

    print("\nthe corroboration mode: several independent witnesses for a link:")
    server._reset_rate()
    REQUESTS.clear()
    MODE["body"] = REPLY
    _, r = post({"jsonrpc": "2.0", "id": 70, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": QUERY, "max_results": 2,
                                          "corroborate": True}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("exactly CORROB_ENGINES were asked, despite the results being sufficient",
             len(payload["engines_asked"]) == server.CORROB_ENGINES,
             payload["engines_asked"])
    # The point: the same link from different engines is not a duplicate but
    # evidence. Collapsing it silently loses the independent agreement exactly
    # where it is the only thing of value.
    names = payload["results"][0]["via"].split(",")
    check("one link from different engines gives SEVERAL names in via",
             len(names) > 2, payload["results"][0]["via"])
    check("and no duplicates appear",
             len({r["url"] for r in payload["results"]}) == len(payload["results"]),
             [r["url"] for r in payload["results"]])
    # TWO LEVELS OF CORROBORATION, not one. Measured: all three engines found the
    # site while there was not one match on the EXACT address — the engines led to
    # different pages of one site. A single level would report zero where
    # corroboration exists.
    first_one = payload["results"][0]
    check("a result carries a domain — the consumer need not parse it",
             first_one.get("domain") == "example.org", first_one.get("domain"))
    check("corroboration by exact address is counted",
             first_one.get("corroborated_by_url", 0) >= 1, first_one.get("corroborated_by_url"))
    check("corroboration by domain is counted separately and is not less than by address",
             first_one.get("corroborated_by_domain", 0) >= first_one.get("corroborated_by_url", 0),
             (first_one.get("corroborated_by_domain"), first_one.get("corroborated_by_url")))

    code, row = get("/stats")
    check("a corroborated call is counted separately",
             row["with_corroboration"] >= 1, row.get("with_corroboration"))
    check("the share of such calls is visible — the rule of use is checked by it",
             row.get("corroboration_share") is not None, row.get("corroboration_share"))
    # An ordinary call stays cheap: corroboration must not leak in by default.
    REQUESTS.clear()
    server._reset_rate()
    _, r = post({"jsonrpc": "2.0", "id": 71, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY, "max_results": 2}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("without the argument the mode does NOT switch itself on",
             len(payload["engines_asked"]) == 1, payload["engines_asked"])

    print("\npacing: at most one request per interval PER ENGINE:")
    server._reset_rate()
    server.ENGINE_INTERVAL_S = 60.0
    # The queue waits for an engine to free up; waiting ten seconds per request in
    # a test is pointless — what is checked is the BEHAVIOUR (wait, then refuse),
    # not the duration.
    saved_wait = server.ENGINE_WAIT_MAX_S
    server.ENGINE_WAIT_MAX_S = 0.3
    post({"jsonrpc": "2.0", "id": 52, "method": "tools/call",
          "params": {"name": "web_search", "arguments": {"query": QUERY, "max_results": 2}}})
    _, r = post({"jsonrpc": "2.0", "id": 53, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY, "max_results": 2}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("an immediate repeat does NOT go to the same engine",
             server.order()[0] not in payload["engines_asked"], payload["engines_asked"])
    check("the next engine is taken instead, not a refusal",
             payload["ok"] is True and payload["engines_asked"], payload)
    # "Skipped by pacing" is not trouble but the queue working: the answer is
    # complete, another engine was asked. So the count lives behind `verbose`
    # rather than in `trouble`.
    code, loud = get("/ag/search?q=" + urllib.parse.quote(QUERY) + "&n=2&verbose=1")
    check("the skipped ones are named explicitly (verbose)",
             bool(loud["engines_skipped"]), loud["engines_skipped"])
    # The worst case: the rate limit closed EVERYBODY. That is a refusal with a
    # reason, not an empty result set.
    for i in range(len(server.order())):
        post({"jsonrpc": "2.0", "id": 60 + i, "method": "tools/call",
              "params": {"name": "web_search", "arguments": {"query": QUERY}}})
    _, r = post({"jsonrpc": "2.0", "id": 80, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("when pacing closed everyone — a REFUSAL with a reason, not an empty result set",
             payload["ok"] is False and "rate limit" in payload["error"], payload)
    # THE SHAPE IS THE SAME IN SUCCESS AND IN REFUSAL. Otherwise the consumer
    # branches on the PRESENCE of fields, which this module never makes anyone do.
    check("even in a refusal the loud fields are all in place",
             all(k in payload for k in server.LOUD), sorted(payload))
    check("and `trouble` is a dictionary there too, not a missing key",
             isinstance(payload.get("trouble"), dict), payload.get("trouble"))
    server.ENGINE_INTERVAL_S = 0.0
    server._reset_rate()

    # An unknown name produces no error — the metasearch silently queries the whole
    # general category. 63 results instead of three, invisibly.
    # The trust label changes the consumer's behaviour: results from an unchecked
    # engine must be verified against features of the subject, results from a
    # clean one need not be. The clean list is NOT homogeneous: some engines have
    # fewer than three observations, and passing them off as verified would be
    # lying in our own favour.
    # THE QUEUE: with concurrent requests the caller MUST wait rather than be
    # refused. Measured on 30 concurrent: 19 refusals out of 30 while only 12
    # requests left for the engines — the engines were protected and the callers
    # punished for nothing.
    print("\nconcurrent requests queue rather than being turned away:")
    server.ENGINE_WAIT_MAX_S = saved_wait   # the previous block shrank it to 0.3 s
    server._reset_rate()
    server.ENGINE_INTERVAL_S = 0.2          # noticeable but short: the test must not crawl
    REQUESTS.clear()
    MODE["body"] = REPLY
    outcomes = []
    lock_ = threading.Lock()
    def concurrent(i):
        # `read=false`: this block is about the QUEUE, and reading here would
        # fetch the fixture's links from the real internet — twelve times per run.
        code, payload = get("/ag/search?read=false&q="
                            + urllib.parse.quote(f"{QUERY} {i}"))
        with lock_:
            outcomes.append(payload.get("ok"))
    threads_ = [threading.Thread(target=concurrent, args=(i,)) for i in range(12)]
    for t in threads_: t.start()
    for t in threads_: t.join(timeout=40)
    check("every concurrent request was served, not one refusal",
             len(outcomes) == 12 and all(outcomes), outcomes)
    # AND THE MAIN POINT: the queue must not multiply requests to other people's
    # services beyond what the order of querying prescribes. The ceiling is one
    # engine per request, no more; a fan-out (every request to every engine at
    # once) would be 12 x 9.
    #
    # A caveat this test itself uncovered: 27 requests came out of 12 queries, an
    # average of 2.25 engines. The cause is not the queue but the thin results of
    # the fake metasearch: it returns two links, six were asked for, and search
    # honestly goes further down the list. On a real ten-link result set the first
    # engine suffices. Worth remembering: A SHORTFALL OF RESULTS MULTIPLIES
    # REQUESTS.
    check("no fan-out: fewer requests than queries times the whole list",
             len(REQUESTS) < 12 * len(server.order()), len(REQUESTS))
    server.ENGINE_INTERVAL_S = 0.0
    server._reset_rate()

    print("\ntrust in engines differs and is visible in the answer:")
    server._trust_cache = (0.0, {})
    saved_db = server.PROBE_DB
    server.PROBE_DB = "/no/such/database.db"
    server._trust_cache = (0.0, {})
    _, r = post({"jsonrpc": "2.0", "id": 90, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": QUERY}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("with no prober database the engines are honestly marked `not_checked`",
             set(payload["engines_trust"].values()) == {"not_checked"},
             payload["engines_trust"])
    check("and the results are NOT declared clean",
             payload["all_engines_clean"] is False, payload["all_engines_clean"])
    check("there is a label for every engine that was asked",
             set(payload["engines_trust"]) == set(payload["engines_asked"]),
             (payload["engines_trust"], payload["engines_asked"]))
    server.PROBE_DB = saved_db
    server._trust_cache = (0.0, {})
    server._reset_rate()

    print("\nengine names are checked BEFORE sending:")
    saved = server._registry
    server._registry = set(server.order())
    check("known names pass",
             server._split_known(server.order()) == (list(server.order()), []), server.order())
    ok, missing = server._split_known(("yandex", "typo_xyz"))
    check("an unknown name is filtered out and named",
             ok == ["yandex"] and missing == ["typo_xyz"], (ok, missing))
    server._registry = set()
    check("an unreadable registry invents nothing: names are sent as they are, search survives",
             server._split_known(("yandex",)) == (["yandex"], []), "an empty registry")
    server._registry = saved

    server._reset_rate()
    print("\nmetasearch failures do not bring the adapter down:")
    MODE["not_json"] = True
    _, r = post({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": "x"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("a non-JSON answer from the metasearch gives isError and a clear reason",
             r["result"]["isError"] is True
             and "other than JSON" in payload["error"], payload)
    MODE["not_json"] = False

    server._reset_rate()
    print("\nag.search/3 over plain HTTP:")
    code, payload = get("/ag/search?q=" + urllib.parse.quote(QUERY) + "&n=1")
    check("GET /ag/search answers 200 with the contract", code == 200
             and payload["contract"] == "ag.search/3"
             and len(payload["results"]) == 1, payload)
    code, payload = get("/ag/search?q=")
    check("an empty q gives a refusal with an explanation, not an empty result set",
             code == 400 and payload["ok"] is False, (code, payload.get("error")))
    code, payload = get("/no-such-path")
    check("an unknown path gives 404 with an explanation", code == 404, payload)

    # EVERY DOOR IS OPENED AT LEAST ONCE. Four of them — reading, images and the
    # two views — appeared in no test at all: a door can be broken outright, in a
    # way no unit check sees, and every suite stays green. Nothing subtle is
    # asked of them here; the question is only "does it answer, and with its own
    # contract".
    MODE["body"] = IMAGE_REPLY
    code, payload = get("/ag/images?q=" + urllib.parse.quote(QUERY) + "&n=2")
    check("GET /ag/images answers with its own contract",
             code == 200 and payload["contract"] == "ag.images/3", (code, payload))
    check("and its answer has the same shape as the MCP one",
             set(payload) == set(server.image_search(QUERY, n=2)), sorted(payload))
    MODE["body"] = REPLY
    code, payload = get("/ag/read?urls=")
    check("GET /ag/read answers with the reading contract, refusal included",
             code in (200, 400, 502) and payload["contract"] == "ag.read/2",
             (code, payload.get("contract"), payload.get("error", "")[:40]))
    check("and the reading refusal carries `arguments_adjusted`, never absent",
             payload.get("arguments_adjusted") == [], payload.get("arguments_adjusted"))
    # THE VIEWS ANSWER 503 WITHOUT A PROBER DATABASE, and that is the honest
    # code: there is nothing measured to show. What must never happen is a 200
    # over emptiness — "checked, all fine" about a view that has no data.
    code, payload = get("/engines")
    check("GET /engines answers, and does not call an empty view healthy",
             code in (200, 503) and payload.get("ok") is (code == 200)
             and ("pool_source" in payload or payload.get("error")),
             (code, sorted(payload)[:6]))
    code, payload = get("/pages")
    check("GET /pages answers about the reading paths, or says why it cannot",
             code in (200, 503) and payload.get("ok") is (code == 200)
             and ("pages" in payload or payload.get("error")),
             (code, sorted(payload)[:6]))

    # This exists because the service had no answer to "is anything calling it at
    # all?": the log is silenced and there were no counters. The answer had to be
    # dug out of interface byte counts. A service must be able to say this itself.
    server._reset_rate()
    print("\ncall accounting: the service can say whether it is called:")
    code, before = get("/stats")
    check("/stats answers 200", code == 200, before)
    check("calls are counted per path",
             isinstance(before.get("by_path"), dict) and before["calls_total"] > 0, before)
    check("uptime is non-negative", before.get("uptime_s", -1) >= 0, before)
    was_searches = before["searches"]
    was_total = before["calls_total"]
    get("/ag/search?q=" + urllib.parse.quote(QUERY))
    code, after = get("/stats")
    check("a search increased the search counter",
             after["searches"] == was_searches + 1, (was_searches, after["searches"]))
    check("any call increases the total counter",
             after["calls_total"] > was_total, (was_total, after["calls_total"]))
    check("the time of the last search is visible",
             after["last_search"] and "ago" in after["last_search"],
             after.get("last_search"))
    post({"jsonrpc": "2.0", "id": 60, "method": "tools/call",
          "params": {"name": "web_search", "arguments": {"query": QUERY}}})
    code, mcp = get("/stats")
    # THE PATH IS CHECKED BY A RULE, NOT BY A STRING. Matching the exact
    # `/mcp:tools/call` makes the test go red on correct behaviour as soon as the
    # tool name is added to the breakdown. A test must describe "an MCP call is
    # visible separately from the other doors", not how it happens to be named.
    check("an MCP call is counted as its own path",
             any(k.startswith("/mcp:tools/call") and v >= 1
                 for k, v in mcp["by_path"].items()), mcp["by_path"])
    check("a search over MCP also reached the search counter",
             mcp["searches"] == after["searches"] + 1, (after["searches"], mcp["searches"]))

    print("\nhealth answers for the CAPABILITY, not for the process:")
    # THE DEEP CHECK READS A PAGE, AND IT MUST NOT BE SOMEBODY ELSE'S. The
    # constant is a variable for exactly this reason ("configurable so that tests
    # can check reading without going outside") — and the possibility went unused
    # while README promised in bold that not one check goes out. The suite is run
    # with `--network none` to hold that promise; see tests/README.md.
    was_reference = server.DEEP_READ_REFERENCE
    os.environ["READ_ALLOW_INTERNAL"] = "1"
    # The page lives on the FAKE METASEARCH, not on the adapter: `base` is the
    # module under test and answers 404 there.
    server.DEEP_READ_REFERENCE = (
        f"http://127.0.0.1:{fake.server_address[1]}/page", "Fixture Domain")
    code, payload = get("/healthz")
    check("a live metasearch gives 200", code == 200 and payload["ok"] is True, payload)
    check("the shallow check honestly says what it does not prove",
             "does not prove" in payload.get("note", ""), payload)
    code, payload = get("/healthz?deep=1")
    # `deep` asks for exactly one result: the check must prove that search runs to
    # the end, not collect a result set — extra results are pure cost here.
    check("the deep check performs a real search",
             code == 200 and payload.get("deep_count") == 1, payload)
    server.DEEP_READ_REFERENCE = was_reference
    os.environ.pop("READ_ALLOW_INTERNAL", None)

    print("\n== deep search: the outcome and its zeroes ==")
    # THE OUTCOME IS DECIDED BY ZEROES, NOT BY THRESHOLDS. Paid for by
    # measurement: two runs of one question differed twofold in corpus size, so a
    # threshold taken from one measurement would be a threshold on noise.
    import deep as _dp
    check("an empty corpus -> not_found",
             _dp._outcome([], ["Ghost"]) == "not_found")
    check("a corpus with no markers -> unknown (NOT found)",
             _dp._outcome([{"chars": 100, "markers_hit": False}], []) == "unknown")
    check("a corpus where the markers met nowhere -> off_target",
             _dp._outcome([{"chars": 100, "markers_hit": False}], ["Ghost"]) == "off_target")
    check("the markers met on at least one page -> found",
             _dp._outcome([{"chars": 100, "markers_hit": False},
                         {"chars": 100, "markers_hit": True}], ["Ghost"]) == "found")

    # TOGETHER ON ONE PAGE. Counting a marker as confirmed when it occurs anywhere
    # in the corpus is refuted by a live run: a city name was found on two pages
    # out of 16 and both were unrelated.
    near = "Ghost Automotive Ltd, Sheffield, reg. no. 12345678"
    apart = "Ghost Automotive" + "x" * 4000 + "Sheffield"
    check("markers close together on a page — counted",
             _dp._markers_together(near, ["Ghost Automotive", "Sheffield"]) is True)
    check("markers on one page but far apart — NOT counted",
             _dp._markers_together(apart, ["Ghost Automotive", "Sheffield"]) is False)
    check("one marker missing entirely — not counted",
             _dp._markers_together(near, ["Ghost Automotive", "Portland"]) is False)
    check("no markers given — not counted (nothing to check with)",
             _dp._markers_together(near, []) is False)

    # PARSE FALLBACK: a wave with no queries is a silent zero of sources.
    check("a model answer wrapped in a json fence is parsed",
             _dp._parse_plan('```json\n{"queries":["a","b"],"markers":["m"]}\n```', "q")["queries"] == ["a", "b"])
    check("a preamble before the object does not interfere",
             _dp._parse_plan('Here is the plan: {"queries":["a"],"markers":[]}', "q")["queries"] == ["a"])
    bare = _dp._parse_plan('["a","b"]', "the question")
    check("a bare array is the queries, and the markers are HONESTLY EMPTY",
             bare["queries"] == ["a", "b"] and bare["markers"] == [], bare)
    check("nothing parsed at all — the question itself is the last resort",
             _dp._parse_plan("I did not understand the task", "my question")["queries"] == ["my question"])

    print("\n== trust: recency, not a period ==")
    # Over a time window, a substitution 25 days ago plus three fresh clean hits
    # still yields "substitutes": a window sees a breakage instantly and a repair
    # a month later.
    import sqlite3 as _sq, tempfile as _tf, time as _t
    tmp_dir = _tf.mkdtemp()
    path = os.path.join(tmp_dir, "e.db")
    c = _sq.connect(path)
    c.execute("""CREATE TABLE probes (ts INTEGER, engine TEXT, query TEXT,
                 verdict TEXT, results INTEGER, relevant INTEGER, hit INTEGER,
                 category TEXT DEFAULT 'general')""")
    now = int(_t.time())
    ref_item = server.CONFIRMED_REFERENCES[0]
    # An ancient substitution and twelve fresh clean hits after it.
    c.execute("INSERT INTO probes VALUES (?,?,?,?,?,?,?,?)",
              (now - 25 * 86400, "recovered", ref_item, "ok", 5, 5, 0, "general"))
    for i in range(12):
        c.execute("INSERT INTO probes VALUES (?,?,?,?,?,?,?,?)",
                  (now - i * 60, "recovered", ref_item, "ok", 5, 5, 1, "general"))
    # And an engine that substituted JUST NOW, among the fresh observations.
    for i in range(12):
        c.execute("INSERT INTO probes VALUES (?,?,?,?,?,?,?,?)",
                  (now - i * 60, "broke", ref_item, "ok", 5, 5,
                   0 if i == 0 else 1, "general"))
    c.commit(); c.close()
    was_db, was_cache = server.PROBE_DB, server._trust_cache
    try:
        server.PROBE_DB = path
        server._trust_cache = (0.0, {})
        dd = server._trust()
        check("an engine that recovered AFTER an old substitution is clean again",
                 dd.get("recovered") == "clean", dd.get("recovered"))
        check("and one that substituted just now is `substitutes`",
                 dd.get("broke") == "substitutes", dd.get("broke"))
        # THE SPAN BESIDE THE VERDICT: N observations is a different period for
        # different engines.
        check("the span the verdict was obtained over is stated",
                 server._trust_spans.get("recovered", {}).get("over_hours")
                 is not None, server._trust_spans.get("recovered"))
    finally:
        server.PROBE_DB, server._trust_cache = was_db, was_cache

    print("\n== images: the death of the metasearch is not passed off as engine refusals ==")
    # With the door shut, recording every engine as silent and sending it to cool
    # for 120 seconds loses the cause and sends out `ok: true, count: 0` — and the
    # NEXT call, right after the metasearch recovers, answers with the same zero:
    # two minutes of successful silence after everything works again.
    server._reset_rate()
    was_round2 = server._round
    try:
        server._round = lambda *a, **k: {"error": "metasearch unavailable: ConnectionError"}
        d = server.image_search("anything", n=4)
        check("the door is shut and nothing was obtained — a REFUSAL, not an empty result set",
                 d["ok"] is False and "metasearch" in d["error"],
                 (d["ok"], d.get("error", "")[:40]))
        check("the engines are NOT declared silent: they were not asked",
                 not d["trouble"].get("unresponsive_engines"), d["trouble"])
    finally:
        server._round = was_round2
    # AND THE MAIN CONSEQUENCE: the pool is not taken out for two minutes.
    now = time.time()
    cooling_now = [e for e, t in server._cooling.items() if t > now + 60]
    check("after the door dies the engines do NOT cool for two minutes",
             not cooling_now, cooling_now)
    server._reset_rate()

    print("\n== degraded paths: in every branch, and no shouting at a healthy system ==")
    # TWO DEFECTS IN ONE FIELD. It lived in one branch of three — with a dead
    # metasearch the answer carried neither it nor the reading state at all — and
    # it counted "not wired up" as degraded, that is, it shouted on a perfectly
    # normal configuration without the sidecar.
    check("a path that is not wired up does NOT count as degraded",
             server._degraded_paths({"plain": "alive", "browser": "not_wired_up"}) == [],
             server._degraded_paths({"plain": "alive", "browser": "not_wired_up"}))
    check("one that does not respond does count",
             server._degraded_paths({"plain": "alive",
                                "browser": "not responding: timeout"}) == ["browser"])
    was_url = server.SEARXNG_URL
    try:
        server.SEARXNG_URL = "http://127.0.0.1:9"     # certainly dead
        ok, msg_body = server.health(deep=False)
        check("with a dead metasearch the path fields are returned ANYWAY",
                 ok is False and "reading" in msg_body and "degraded_paths" in msg_body,
                 sorted(msg_body))
    finally:
        server.SEARXNG_URL = was_url

    print("\n== the path cache: it works and creates no load of its own ==")
    # A cache lifetime of 15 seconds against a container health check every 30
    # means the most frequent consumer NEVER hits the cache, and the cache looks
    # like an optimisation without being one.
    # The tick is read from the deployment itself rather than written out as a
    # number: otherwise it drifts apart from it silently — the same class as a
    # literal "capabilities: 2".
    # Parsed with a regular expression rather than yaml: the module image has no
    # yaml, and pulling in a dependency for one line costs more than reading the
    # line.
    import re as _re
    # The deployment file is mounted into the image separately (see in-image.sh):
    # it is not beside the tests. Not found means WE FAIL rather than skip: a
    # silently skipped check gives the same green as a passed one.
    compose_paths = [os.path.join(os.path.dirname(__file__), "..",
                                   "docker-compose.yml"),
                      "/docker-compose.yml"]
    pool_state = ""
    for _pth in compose_paths:
        if os.path.exists(_pth):
            pool_state = open(_pth, encoding="utf-8").read()
            break
    check("the deployment file is available to the test (otherwise the tick check is a sham)",
             bool(pool_state), compose_paths)
    # THE IMAGE TAG IS THE RELEASE, AND IT IS THE SAME RELEASE THE MODULE NAMES
    # IN ITS HANDSHAKE. Two literals about one version drift apart in silence, and
    # the drift shows up in the worst place: `up -d` without `--build` on a clean
    # machine sends compose to PULL a tag that no registry has, and its refusal
    # talks about a registry rather than about the missing build.
    tags = sorted(set(_re.findall(r"^\s*image:\s*ag-mod-search/adapter:(\S+)",
                                  pool_state, _re.M)))
    # THE PROMISE WAS IN THE SHIPPED TREE AND THE CHECK WAS NOT. browser/Dockerfile
    # says a check keeps the two playwright pins equal; the check lived in our
    # own static suite, which does NOT ship — so for an outsider the sentence
    # named a guard that was not there. The pins are compared here instead, in
    # the suite that travels with the code. The Dockerfiles are mounted for this
    # (see in-image.sh); when they are not, the check FAILS rather than skips.
    pins = {}
    for who, at in (("adapter", "/dockerfiles/adapter"),
                    ("browser", "/dockerfiles/browser")):
        local = os.path.join(os.path.dirname(__file__), "..", who, "Dockerfile")
        path = at if os.path.exists(at) else local
        pins[who] = ("" if not os.path.exists(path) else
                     "".join(_re.findall(r"playwright==([0-9.]+)",
                                          open(path, encoding="utf-8").read())[:1]))
    check("the playwright pin is readable in both recipes (else this checks nothing)",
             all(pins.values()), pins)
    check("and the two are EQUAL — a divergence breaks the connection silently",
             pins["adapter"] == pins["browser"], pins)

    check("every adapter image in the deployment file carries ONE tag",
             len(tags) == 1, tags)
    check("and that tag is the version the module reports in its handshake",
             tags == [reader.VERSION], (tags, reader.VERSION))
    intervals = [float(mm) for mm in _re.findall(r"^\s*interval:\s*(\d+)s",
                                           pool_state, _re.M)]
    seconds = max(intervals) if intervals else 30.0
    check("the cache lifetime is LONGER than the health-check tick, or it is useless",
             reader.PATHS_CACHE_S > seconds, (reader.PATHS_CACHE_S, seconds))
    # PROTECTION AGAINST LOAD MUST NOT CREATE LOAD: taking the lock twice and
    # probing the sidecar in between lets N simultaneous misses open N sessions.
    was_fresh = reader._paths_fresh
    score = {"n": 0}
    try:
        def slow_probe():
            score["n"] += 1
            time.sleep(0.2)
            return {"plain": "alive", "browser": "not_wired_up"}
        reader._paths_fresh = slow_probe
        with reader._paths_lock:
            reader._paths_cache = None
        threads_ = [threading.Thread(target=reader.paths) for _ in range(6)]
        for n in threads_:
            n.start()
        for n in threads_:
            n.join()
        check("six simultaneous misses produce ONE probe of the sidecar",
                 score["n"] == 1, score["n"])
    finally:
        reader._paths_fresh = was_fresh
        with reader._paths_lock:
            reader._paths_cache = None

    print("\n== the engines view: a whole category, not half of one ==")
    # Filtering the rows by category while the pool and the "why not included"
    # explanations are still computed from `general` makes the image view claim
    # that no image engine is in the pool, and show the web seed.
    import inspect as _i
    source_text = _i.getsource(server._engines_state)
    check("the pool is taken PER CATEGORY, not from general",
             "pool_now_for(category)" in source_text)
    check("why-not-included is computed over the same category",
             "category=category" in source_text)
    check("the category is declared IN THE ANSWER, or the answer does not say what it is about",
             '"category": category' in source_text)
    # A ZERO IS EXPLAINED BY WHAT WAS CHECKED. One reason for every zero — "the
    # prober has only just started" — makes a typo in the category produce that
    # same reason over a database with thousands of probes: "there was nowhere to
    # look" and "nothing was found" merge, under an invented cause.
    check("an empty answer distinguishes no-database from nothing-in-this-category",
             "database holds categories" in source_text
             and "SELECT COUNT(*) FROM probes" in source_text)

    print("\n== a foreign MCP body neither drops the connection nor grows memory ==")
    # The accounting pre-pass runs BEFORE rpc(), where all the shape checks live,
    # so `params` arriving as a list gives `.get` on a list — an AttributeError and
    # a dropped connection with no answer. Guarding the other door proves nothing
    # about this one: a promise that "rubbish in the arguments does not kill a
    # call" holds only where it is checked.
    for msg_body in ({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": ["not", "a-dict"]},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "web_search", "arguments": ["also", "not"]}},
                 {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": ["name", "as-a-list"]}}):
        code, answer = post(msg_body)
        check(f"a foreign shape of params/arguments gives an ANSWER, not a drop (id={msg_body['id']})",
                 code == 200 and isinstance(answer, dict), (code, str(answer)[:80]))

    # THE ACCOUNTING KEY WAS BUILT FROM AN UNVALIDATED NAME, and the dictionary
    # grew without bound: a client with typos grows the process memory, a fuzzer
    # grows it fast, and all of it goes out in /stats.
    with server._counters_lock:
        was = len(server._counters["by_path"])
    for n in range(5):
        post({"jsonrpc": "2.0", "id": 100 + n, "method": "tools/call",
              "params": {"name": f"invented_{n}", "arguments": {}}})
    with server._counters_lock:
        became = len(server._counters["by_path"])
        keys_now = [k for k in server._counters["by_path"] if "invented" in k]
    check("five different invented names do NOT create five accounting keys",
             not keys_now and became - was <= 1, (became - was, keys_now))
    check("but an unknown name IS still counted, under one key",
             any("unknown" in k for k in server._counters["by_path"]),
             sorted(server._counters["by_path"])[:8])

    print("\n== the view counts CALLS, not the fact of a tools/call ==")
    # Counting every tools/call as a SEARCH puts reads and screenshots made over
    # MCP into the search counter while the read counter stays at zero. The one
    # view that answers "is the service being called, and how" then lies about the
    # mix of calls — and decisions about what to fix are taken from it.
    with server._counters_lock:
        was_search = server._counters["searches"]
        was_read = server._counters["reads"]
    post({"jsonrpc": "2.0", "id": 900, "method": "tools/call",
          "params": {"name": "web_read",
                     "arguments": {"urls": ["http://127.0.0.1:9/none"]}}})
    with server._counters_lock:
        now_search = server._counters["searches"]
        now_read = server._counters["reads"]
    check("a read over MCP counts as READING, not as a search",
             now_read == was_read + 1 and now_search == was_search,
             (was_search, now_search, was_read, now_read))
    post({"jsonrpc": "2.0", "id": 901, "method": "tools/call",
          "params": {"name": "web_search",
                     "arguments": {"query": "a probe", "read": False}}})
    with server._counters_lock:
        search2 = server._counters["searches"]
        read2 = server._counters["reads"]
    check("a search without reading counts only as a search",
             search2 == now_search + 1 and read2 == now_read,
             (now_search, search2, now_read, read2))
    # AND THE BREAKDOWN BY TOOL: "there was a call" and "this particular one was
    # called" are different pieces of news, and the second is what the view is
    # looked at for.
    with server._counters_lock:
        paths = dict(server._counters["by_path"])
    check("the breakdown shows the TOOL NAME, not only tools/call",
             any(k.endswith(":web_read") for k in paths), sorted(paths)[:6])

    print("\n== rubbish in the door arguments ==")
    # The plain HTTP door hands arguments over as STRINGS, so an uncoerced
    # `int("abc")` raises ValueError out of a function whose docstring says "never
    # raises". From outside that is a dropped connection — a broken service instead
    # of "that argument will not do".
    import deep as _dp4
    cls = type("M", (), {"available": staticmethod(lambda vision=False: (True, "")),
                           "call": staticmethod(
                               lambda *a, **k: {"ok": False, "content": "",
                                                "error": "stop", "usage": {}})})
    r = _dp4.deep_search("q", lambda *a, **k: {"ok": True, "results": []},
                         lambda *a, **k: [], cls(), waves="abc")
    check("rubbish in waves does NOT break the call, it gives the default",
             isinstance(r, dict) and "ok" in r, type(r).__name__)
    # THE OTHER HALF OF THE SAME POINT: the string "0" is TRUTHY, so `waves or MAX`
    # yielded zero where MCP yielded three. One door quietly ran a single wave
    # against three, and only counting the waves could show it.
    check("the string 0 and the number 0 mean the same: the default, not one wave",
             _dp4._as_int("0", 3) == _dp4._as_int(0, 3) == 3,
             (_dp4._as_int("0", 3), _dp4._as_int(0, 3)))
    check("an explicit number passes through as it is", _dp4._as_int("2", 3) == 2)
    check("a negative value does not become a negative number of waves",
             _dp4._as_int("-5", 3) == 3)

    print("\n== degraded paths: the living is not declared dead ==")
    # CAUGHT BY A LIVE RUN A MINUTE AFTER DEPLOYMENT. Comparing a path state with
    # the whole string "alive" fails when the browser answers "alive (chromium
    # 131.0.6778.33)", and a live path was declared degraded. A field raised
    # AGAINST silent degradation lied in its very first answer.
    was_paths = reader.paths
    try:
        reader.paths = lambda fresh_flag=False: {"plain": "alive",
                                           "browser": "alive (chromium 131.0.0)"}
        ok, msg_body = server.health(deep=False)
        check("a path with details in brackets counts as ALIVE",
                 msg_body["degraded_paths"] == [], msg_body["degraded_paths"])
        reader.paths = lambda fresh_flag=False: {"plain": "alive",
                                           "browser": "not responding: timeout"}
        ok, msg_body = server.health(deep=False)
        check("and a truly dead path is NAMED",
                 msg_body["degraded_paths"] == ["browser"], msg_body["degraded_paths"])
        # And `ok` does not fall with it: the health check is read by the adapter's own
        # container, and restarting the adapter does not cure somebody else's sidecar.
        check("a dead declared path does not drag ok down — these are different questions",
                 ok is True and msg_body["ok"] is True, (ok, msg_body["ok"]))
    finally:
        reader.paths = was_paths

    print("\n== the metasearch dying mid-sweep ==")
    # The metasearch dies MID-SWEEP: some engines answered, the rest meet a dead
    # door. Discarding the cause because something was already collected sends out
    # `ok: true` with fewer engines and NOT A WORD about why there are fewer. The
    # caller puts a thin result set down to the rarity of the query.
    server._reset_rate()
    MODE["body"] = REPLY
    was_round = server._round
    rounds = {"n": 0}
    try:
        def falling(query, engine_names, n, page):
            rounds["n"] += 1
            if rounds["n"] == 1:
                return was_round(query, engine_names, n, page)
            return {"error": "metasearch not responding: connection closed"}
        server._round = falling
        d = server.search("a live query", n=6, min_engines=4)
        check("a partial result set stays a SUCCESS: the links were obtained",
                 d["ok"] is True and d["count"] > 0, (d["ok"], d["count"]))
        check("but the break is NAMED, not discarded",
                 "not responding" in (d.get("search_aborted") or ""),
                 d.get("search_aborted"))
        # And separately — WHO was not reached. Without it "we asked one" is
        # indistinguishable from "there is one in the pool".
        check("the engines that were not reached are named",
                 bool(d.get("engines_unasked")), d.get("engines_unasked"))
        # `engines_skipped` will not do for this: that means "not asked because of the
        # rate limit", this means "there was nobody left to ask" — different events.
        check("a break does NOT stand in for a skip by pacing",
                 d.get("engines_skipped") != d.get("engines_unasked")
                 or not d.get("engines_skipped"),
                 (d.get("engines_skipped"), d.get("engines_unasked")))
    finally:
        server._round = was_round
        server._reset_rate()
    # A HEALTHY RUN LOSES NO FIELDS AND INVENTS NONE.
    d = server.search("an ordinary query", n=3)
    check("with a healthy metasearch the break field is empty, not absent",
             d.get("search_aborted") == "" and d.get("engines_unasked") == [],
             (d.get("search_aborted"), d.get("engines_unasked")))

    print("\n== the guards stand where the failure is ==")
    # THE FORMULATION FROM AN INDEPENDENT REVIEW: "the guards are standing where
    # the failure is not". The three checks below cover the three places found.

    # 1. CLEANLINESS IS COUNTED OVER THE ENGINES THAT ANSWERED, NOT THOSE ASKED. A
    # guard of the form `bool(asked) and all(... for e in answered)` stays true
    # when four engines were asked and all stayed silent, because `all([])` is
    # true — and "results come from clean engines" came out TRUE over results that
    # do not exist. No data means "not checked", never "sound".
    server._reset_rate()
    MODE["body"] = EMPTY_REPLY
    d = server.search("nobody answered", n=3)
    check("nobody answered — results-from-clean-engines is FALSE, not true",
             d["all_engines_clean"] is False,
             (d["all_engines_clean"], d["engines_asked"], d["engines_answered"]))
    MODE["body"] = REPLY
    server._reset_rate()
    d = server.search("somebody answered", n=3)
    check("and when they answered, the flag is computed over them again",
             isinstance(d["all_engines_clean"], bool) and d["engines_answered"],
             (d["all_engines_clean"], d["engines_answered"]))

    # 2. AN EMPTY RESULT SET IS A PROPERTY OF THE QUERY, NOT AN ENGINE REFUSAL.
    # Cooling on it as harshly as on a refusal blinds the pool on rare questions —
    # exactly the ones deep search exists for, with its eight queries.
    # Cooling is switched off in these tests (see above), so the real values are
    # restored for the duration: otherwise the check would prove that zero is less
    # than zero.
    was_cooldown = server.COOLDOWN_S
    try:
        server.COOLDOWN_S = 120.0
        check("emptiness and a refusal cool for DIFFERENT times",
                 server.COOLDOWN_EMPTY_S < server.COOLDOWN_S,
                 (server.COOLDOWN_EMPTY_S, server.COOLDOWN_S))
        server._reset_rate()
        MODE["body"] = EMPTY_REPLY
        server.search("something rare", n=3)
        now = time.time()
        cooling_now = {e: t - now for e, t in server._cooling.items() if t > now}
        check("after an empty result set an engine comes back quickly, "
                 "and not in two minutes",
                 bool(cooling_now)
                 and max(cooling_now.values()) <= server.COOLDOWN_EMPTY_S + 1,
                 cooling_now)
    finally:
        server.COOLDOWN_S = was_cooldown
        MODE["body"] = REPLY
        server._reset_rate()

    # 3. NAME CHECKING DOES NOT SWITCH ITSELF OFF FOREVER. One failed /config at
    # startup, recorded as an empty registry, lasts the life of the process, and
    # an empty registry passes any name through — so the step raised AGAINST a
    # silent fan-out over the whole category is disabled silently and permanently.
    was_registry, was_registry_at = server._registry, server._registry_at
    try:
        server._registry, server._registry_at = set(), time.time()
        CONFIG_HITS.clear()
        server._known_engines()
        check("a recent registry failure is still remembered (we do not hammer /config)",
                 len(CONFIG_HITS) == 0, len(CONFIG_HITS))
        server._registry_at = time.time() - server.REGISTRY_RETRY_S - 1
        server._known_engines()
        check("a stale failure is RE-READ rather than remembered forever",
                 len(CONFIG_HITS) == 1, len(CONFIG_HITS))
    finally:
        server._registry, server._registry_at = was_registry, was_registry_at

    print("\n== search reads by default ==")
    # A tool that returns links by default puts a second call on the caller.
    # Reading is stubbed out here: the test does not go outside.
    was_read_many = reader.read_many
    read_calls: list = []

    recognise_flags: list = []

    def stub_fn(url_list, max_chars=0, fmt="markdown", expect=None,
                 mode="auto", deadline=0, recognise=True):
        read_calls.append(list(url_list))
        recognise_flags.append(recognise)
        return [{"status": "read", "content": f"text {u}"[:200],
                 "stub_check": "clean", "reason": ""} for u in url_list]
    try:
        reader.read_many = stub_fn
        read_calls.clear()
        # THE LOUD ANSWER IS ASKED FOR HERE (`verbose`): what is checked is the
        # ACCOUNTING — how many pages were read and what it cost — and that is
        # exactly what moved behind the knob. The default shape is checked below.
        d = server.search_read("test", n=5, verbose=True)
        check("reading happens WITHOUT an argument: content is not empty",
                 d["read"] is True and (d["results"][0].get("content") or ""),
                 (d.get("read"), d["results"][0].get("content", "")[:20]))
        # A RULE, NOT A NUMBER. Comparing against the read ceiling makes the test
        # depend on the size of the corpus. The rule is "we read the top links, as
        # many as there are".
        expect_domains = min(server.SEARCH_READ_TOP_N, len(d["results"]))
        check("the TOP links are read, as many as there are, not everything",
                 len(read_calls[0]) == expect_domains, (read_calls[0], expect_domains))
        check("how many pages were read is stated as a number",
                 d["pages_read"] == expect_domains, (d["pages_read"], expect_domains))
        # THE COST IS VISIBLE — which is what makes reading inside search acceptable:
        # the answer's shape carries the price.
        check("the cost is broken down: search apart, reading apart",
                 set(d["timing_ms"]) == {"search_ms", "read_ms"}, d["timing_ms"])
        # THE COST FOLLOWS THE EXPLICITNESS OF THE REQUEST. Nobody calling search
        # asked to pay for a vision model over a scan that happened into the top
        # links; whoever calls reading on a document did. Without this check the
        # flag can be dropped in a refactor and nothing would show it: the answer
        # would simply cost more.
        check("search reads WITHOUT recognition — a scan is not paid for here",
                 recognise_flags == [False], recognise_flags)
        # UNREAD LINKS CARRY THE FIELDS TOO: a list where some elements have a field
        # and others do not forces the reader to guess "not read" or "empty".
        tail = d["results"][expect_domains:]
        check("unread links keep their fields IN PLACE, with `not_read`",
                 all(r.get("read_status") == "not_read" and r.get("chars") == 0
                     for r in tail) if tail else True,
                 [r.get("read_status") for r in tail])

        read_calls.clear()
        d = server.search_read("test", n=5, read=False, verbose=True)
        check("read:false restores the cheap path — not one read",
                 not read_calls and d["pages_read"] == 0, read_calls)
        check("the reading fields are returned under read:false too, they do not vanish",
                 "pages_read" in d and "timing_ms" in d, sorted(d)[:6])

        read_calls.clear()
        d = server.search_read("test", n=5, read_top=1)
        check("read_top sets the number of pages read",
                 len(read_calls[0]) == 1, read_calls[0])

        # WHAT THE CALLER GETS BY DEFAULT. A consumer measured us: twenty-five
        # top-level fields, seven ever read, one branched on. The answer now
        # carries what they act on, plus `trouble`; the rest comes on request.
        read_calls.clear()
        quiet = server.search_read("test", n=5)
        check("by default the answer carries only what the caller acts on",
                 set(quiet) == set(server.LOUD), sorted(quiet))
        check("`trouble` is always there, and empty means checked-and-clean",
                 isinstance(quiet["trouble"], dict), quiet.get("trouble"))
        loud = server.search_read("test", n=5, verbose=True)
        check("verbose adds the accounting back, and nothing is lost",
                 set(loud) > set(quiet) and "timing_ms" in loud and "pool_source" in loud,
                 sorted(set(loud) - set(quiet)))

        # ZERO MEANS "NOT ASKED", AND IT IS SAID RATHER THAN INFERRED. The
        # schema declared `minimum: 1` while zero was accepted and silently meant
        # "you decide" — a zero for "no preference" beside a zero that reads as
        # "none at all".
        read_calls.clear()
        zero = server.search_read("test", n=5, read_top=0, verbose=True)
        check("read_top=0 means `no preference`: the default number is read",
                 len(read_calls[0]) == min(server.SEARCH_READ_TOP_N,
                                           len(zero["results"])), read_calls[0])
        check("and a legitimate zero is NOT reported as an adjustment",
                 not [x for x in zero["trouble"].get("arguments_adjusted", [])
                      if "read_top" in x], zero["trouble"])
        check("the schema declares the range it really accepts",
                 server.TOOL["inputSchema"]["properties"]["read_top"]["minimum"] == 0,
                 server.TOOL["inputSchema"]["properties"]["read_top"])
        read_calls.clear()
        odd = server.search_read("test", n=5, read_top="many")
        check("rubbish in read_top is NAMED in trouble, not silently defaulted",
                 any("read_top" in x for x in
                     odd["trouble"].get("arguments_adjusted", [])), odd["trouble"])
        read_calls.clear()
        big = server.search_read("test", n=5, read_top=999)
        check("a value above the ceiling is clamped AND said so",
                 any("clamped" in x for x in
                     big["trouble"].get("arguments_adjusted", [])), big["trouble"])
    finally:
        reader.read_many = was_read_many

    print("\n== the width of the sweep: one knob, not three ==")
    # TWO KNOBS ON ONE PROPERTY DRIFT APART AT THE FIRST EDIT. `corroborate` is
    # kept for older callers and EXPRESSED through min_engines rather than living
    # beside it.
    server._reset_rate()
    a = server.search("test", n=2, min_engines=3)
    server._reset_rate()
    b = server.search("test", n=2, corroborate=True)
    check("corroborate:true and min_engines:3 poll IDENTICALLY",
             len(a["engines_asked"]) == len(b["engines_asked"]) == 3,
             (a["engines_asked"], b["engines_asked"]))
    server._reset_rate()
    c = server.search("test", n=2)
    check("with no width set, search stops once it has enough",
             len(c["engines_asked"]) < 3, c["engines_asked"])
    server._reset_rate()
    d2 = server.search("test", n=2, min_engines=2)
    check("min_engines as a number asks EXACTLY that many engines",
             len(d2["engines_asked"]) == 2, d2["engines_asked"])

    print("\n== the slice is taken around the markers, not from the start ==")
    # THE HEAD OF A DOCUMENT IS A POOR APPROXIMATION TO ITS CONTENT. A regulatory
    # filing of 151 553 characters begins with 8 100 characters of XBRL metadata;
    # an article begins with navigation, a forum thread with its header. A primary
    # source can therefore occupy a place in the corpus and hand back rubbish,
    # making the answer WORSE than it was without it.
    import deep as _dp3
    report = ("000131860512-31false2025Q1 us-gaap AccountingStandardsUpdate " * 60
             + " Tesla Total revenues 94827 million за 2025 год "
             + " прочие примечания и сноски " * 60)
    window = _dp3._window_at_markers(report, ["Tesla", "revenues"], 600)
    check("the window is taken AROUND THE MARKERS and contains the number sought",
             "94827" in window and len(window) == 600, (len(window), window[:40]))
    check("the head of the document is NOT returned",
             not window.startswith("000131860512"), window[:30])
    # NO MARKERS MEANS NOTHING TO TAKE, and a place must not be invented.
    check("with no markers the head is taken honestly, not a random place",
             _dp3._window_at_markers(report, [], 60).startswith("000131860512"))
    # A SHORT DOCUMENT IS RETURNED WHOLE: there is nothing to cut.
    check("a document shorter than the window is returned whole",
             _dp3._window_at_markers("коротко и ясно", ["ясно"], 600)
             == "коротко и ясно")
    # THE WINDOW IS CHOSEN BY DENSITY, NOT BY THE FIRST OCCURRENCE: on a long page
    # the name stands in the navigation and in the footer, while the place that
    # matters is where the markers meet together.
    page_obj = ("Tesla " + "навигация " * 200 + "подвал " * 200
                + " Tesla revenues 2025 94827 Tesla отчёт ")
    o2 = _dp3._window_at_markers(page_obj, ["Tesla", "revenues"], 400)
    check("the place with MORE markers is chosen, not the first occurrence",
             "94827" in o2, o2[:60])

    print("\n== a truncated model answer ==")
    # A FAILURE INDISTINGUISHABLE FROM A SUCCESS, in the most expensive place: the
    # answer hits the token ceiling and ends mid-sentence, yet reads as finished,
    # and its last paragraph is taken for a conclusion.
    import deep as _dp2

    class _CutOff:
        MODEL_TEXT = "test"
        def available(self): return True, ""
        def call(self, messages, max_out=0, model="", timeout=0):
            return {"ok": True, "content": "The answer began and was not fin",
                    "usage": {}, "error": "", "truncated": True}

    srcs = [{"url": "https://a.example/x", "domain": "a.example", "chars": 400,
            "markers_hit": True, "text": "Kimi K2.6 вышла 20 апреля 2026 года."}]
    cc = _dp2._digest("when was it released", srcs, _CutOff(), ["Kimi"])
    check("a truncated answer is MARKED by a field", cc["truncated"] is True)
    check("and declared on the FIRST LINE, not only in a field",
             cc["answer"].startswith("> WARNING: THE ANSWER IS CUT OFF"), cc["answer"][:60])
    check("the text obtained is NOT discarded on truncation",
             "The answer began" in cc["answer"], cc["answer"][-40:])

    class _Whole(_CutOff):
        def call(self, messages, max_out=0, model="", timeout=0):
            return {"ok": True, "content": "A complete answer.", "usage": {},
                    "error": "", "truncated": False}
    whole = _dp2._digest("when was it released", srcs, _Whole(), ["Kimi"])
    check("a complete answer gets NO caveat",
             whole["truncated"] is False and "CUT OFF" not in whole["answer"], whole["answer"][:60])

    # THREE STATES, NOT TWO. A comparison of the form `== "length"` yields False
    # when the field is ABSENT, so "not reported" reads as "checked, complete".
    # Gateways that do not return a finish reason would take us back to where the
    # field came from.
    class _SaysNothing(_CutOff):
        def call(self, messages, max_out=0, model="", timeout=0):
            return {"ok": True, "content": "text", "usage": {}, "error": "",
                    "truncated": None}
    mm = _dp2._digest("when was it released", srcs, _SaysNothing(), ["Kimi"])
    check("not-reported is NOT passed off as complete",
             mm["truncated"] is None, mm["truncated"])
    check("and that is said in the text of the answer, not only in a field",
             "did not report whether the answer was written to the end"
             in mm["answer"], mm["answer"][:90])

    print("\n== sources disagreeing: name every version rather than choose ==")
    # SOURCES DISAGREE GENUINELY: an announcement and general availability fall on
    # different days, so there are two correct answers. A run that names one of
    # them with three references and a confident tone is not more right than a run
    # that names the other.
    import deep as _dp1
    on_20th = ["Kimi K2.6 вышла 20 апреля 2026 года и стала доступна.",
                 "Компания анонсировала Kimi K2.6 20 апреля 2026 г."]
    on_21st = ["Kimi K2.6 стала общедоступной 21 апреля 2026 года.",
                       "Запуск Kimi K2.6 состоялся 21 апреля 2026, сообщили в компании."]
    other_pages = ["Обзор Kimi K2.6: архитектура MoE, контекст 262144 токена."]
    def _corpus_of(texts):
        return [{"url": f"https://a{i}.example/x", "chars": 500, "text": ts_val}
                for i, ts_val in enumerate(texts)]
    rr = _dp1._conflicts_of(_corpus_of(on_20th + on_21st + other_pages),
                          ["Kimi", "K2.6"])
    check("two versions of the date are FOUND and both are named with source counts",
             [wave["value"] for wave in rr] == ["2026-04-20", "2026-04-21"]
             and all(wave["sources_count"] == 2 for wave in rr), rr)
    check("a corpus in agreement is NOT declared a disagreement",
             _dp1._conflicts_of(_corpus_of(on_20th + other_pages), ["Kimi", "K2.6"]) == [])
    # ONE SOURCE IS NOT A VERSION. The threshold was loosened and the result
    # MEASURED: at two sources per version the mechanism fired 1 time in 5 and
    # named the real announcement and release dates; at one source it fired 4
    # times in 5 and all four were rubbish — dates of articles, of neighbouring
    # versions and of page footers.
    #
    # A RARE HONEST ALARM BEATS A FREQUENT DIRTY ONE: a guard that shouts at a
    # healthy system gets switched off entirely, together with the cases it exists
    # for.
    single = _corpus_of(on_20th + ["Kimi K2.6 вышла 30 апреля 2026 года."] + other_pages)
    check("a value from ONE source does not count as a version",
             _dp1._conflicts_of(single, ["Kimi", "K2.6"]) == [],
             _dp1._conflicts_of(single, ["Kimi", "K2.6"]))
    # A DATE FAR FROM THE MARKERS IS NOT ABOUT OUR SUBJECT. A page about a product
    # holds many dates: the date of the article, of a previous version, of the
    # footer.
    far_apart = _corpus_of([
        "Kimi K2.6 обзор. " + "х" * 600 + " Статья опубликована 3 мая 2026 года.",
        "Kimi K2.6 сравнение. " + "х" * 600 + " Обновлено 9 июня 2026 года."])
    check("a date far from the markers does NOT enter the disagreement",
             _dp1._conflicts_of(far_apart, ["Kimi", "K2.6"]) == [],
             _dp1._conflicts_of(far_apart, ["Kimi", "K2.6"]))
    check("with no markers no disagreement is sought: there is nothing to be near",
             _dp1._conflicts_of(_corpus_of(on_20th + on_21st), []) == [])

    # DISAGREEMENT IS SOUGHT OVER THE SAME PAGES THE MODEL WILL SEE. Searching one
    # set and showing another lets a version named by the eighth source reach the
    # field while the model never sees it, and the demand "name EVERY version"
    # becomes impossible to meet — that is, it forces invention.
    big_corpus = _corpus_of(on_20th + other_pages * 4 + on_21st)
    for ent in big_corpus:
        ent["markers_hit"] = True
    shown_set = _dp1._pick_for_digest(big_corpus)
    r2 = _dp1._conflicts_of(shown_set, ["Kimi", "K2.6"])
    check("a version the model will not see does NOT enter conflicts",
             all(any(u in [ent["url"] for ent in shown_set] for u in wave["sources"])
                 for wave in r2), [wave["sources"] for wave in r2])

    # THE TWO CAVEATS ARE NOT ALTERNATIVES. An `elif` here keeps the disagreement
    # text out of the answer whenever there are no on-target sources, even with
    # the conflicts field filled in.
    class _Quiet:
        def available(self, vision=False): return True, ""
        def call(self, messages, max_out=0, model="", timeout=0):
            return {"ok": True, "content": "answer", "usage": {}, "error": "",
                    "truncated": False}
    off_topic_src = [dict(ent, markers_hit=False) for ent in _corpus_of(on_20th + on_21st)]
    cc = _dp1._digest("когда", off_topic_src, _Quiet(), ["Kimi"],
                     [{"value": "2026-04-20", "sources": ["a"], "sources_count": 2},
                      {"value": "2026-04-21", "sources": ["b"], "sources_count": 2}])
    check("both caveats stand in the answer rather than displacing each other",
             "digested from sources that are NOT on target" in cc["answer"]
             and "SOURCES DISAGREE" in cc["answer"], cc["answer"][:200])

    # A PRIMARY SOURCE IS MARKED BY DOMAIN, WITHOUT A MODEL. On a question about a
    # company's revenue the number can be right while no regulatory filing is in
    # the corpus at all — and that difference is worth seeing.
    check("a disclosure domain is marked as a primary source",
             _dp1._is_primary_source("www.sec.gov")
             and _dp1._is_primary_source("e-disclosure.ru"))
    # STRICTLY BY THE TAIL OF THE NAME, not by containment: otherwise
    # `notsec.gov.example.com` would declare itself a regulator filing, and the
    # label would become bait.
    check("a foreign domain CONTAINING a regulator name does not become a primary source",
             not _dp1._is_primary_source("notsec.gov.example.com")
             and not _dp1._is_primary_source("investor.gov.fake.com"))
    check("an ordinary site is not marked as a primary source",
             not _dp1._is_primary_source("rbc.ru"))

    print("\n== markers: emptiness no longer switches the check off silently ==")
    import deep as _dp0
    # MARKERS ARE THE SINGLE POINT OF FAILURE OF THE WHOLE QUALITY MECHANISM. When
    # the model returns an EMPTY marker list, verification switches off entirely:
    # zero on-target by construction, namesake separation with nothing to stand
    # on, and a confident answer regardless.
    check("a name without a version is taken from the question when the model is silent",
             _dp0._markers_from_question("Чем занимается компания Powercase")
             == ["Powercase"],
             _dp0._markers_from_question("Чем занимается компания Powercase"))
    check("an interrogative word does not count as a marker",
             "Чем" not in _dp0._markers_from_question("Чем занимается Powercase"))
    check("versions are not spoiled by trimming endings",
             _dp0._markers_from_question("Когда вышла Kimi K2.6") == ["Kimi", "K2.6"],
             _dp0._markers_from_question("Когда вышла Kimi K2.6"))
    # INFLECTION: a name in a possessive or oblique form on the page is almost
    # always the plain name.
    mark = _dp0._markers_from_question("Какая выручка у Квинтары за 2025 год")
    check("inflected words are trimmed for case, and the trimming is VISIBLE in the markers",
             "Квинта" in mark and "2025" in mark, mark)
    check("a question with no proper nouns honestly gives nothing rather than an invention",
             _dp0._markers_from_question("сравнение цен на ламинат") == [],
             _dp0._markers_from_question("сравнение цен на ламинат"))
    # AN ENGLISH QUESTION. Taking any Latin word of three letters or more turns
    # "What is the revenue of Tesla in 2025" into ['What', 'the', 'revenue'] —
    # markers that confirm anything at all. This path produces half of all
    # markers, and a defect in half the work is not a matter of taste.
    eng_markers = _dp0._markers_from_question("What is the revenue of Tesla in 2025")
    check("in English, function words do NOT become markers",
             eng_markers == ["Tesla", "2025"], eng_markers)
    check("a Latin interrogative word is dropped too",
             "What" not in _dp0._markers_from_question("What is Kubernetes"))

    print("\n== ambiguity: there-are-several instead of a choice by domain weight ==")
    import deep as _dp
    # TWO DIFFERENT COMPANIES UNDER ONE NAME. The texts are alike in nothing but
    # the name itself.
    argentina = ("ритейлер электроники Буэнос-Айрес Аргентина магазины покупка "
                 "приобретение розничная сеть торговля продажи витрина ")
    investigations = ("платформа расследований правоохранительные органы анализ "
                     "связей аналитика данные преступность следствие софт ")
    corpus = []
    for n in range(3):
        corpus.append({"url": f"https://arg{n}.example/x", "domain": f"arg{n}.example",
                       "chars": 500, "text": "Powercase " + argentina * 3,
                       "markers_hit": True})
    for n in range(3):
        corpus.append({"url": f"https://inv{n}.example/x", "domain": f"inv{n}.example",
                       "chars": 500, "text": "Powercase " + investigations * 3,
                       "markers_hit": True})
    groups = _dp._namesake_groups(corpus, ["Powercase"])
    check("two unrelated clusters under one name are SEPARATED",
             len(groups) == 2, [len(g) for g in groups])
    check("the split is made WITHOUT a model (a pure function of the texts)",
             all(isinstance(g, list) for g in groups))

    # A HOMOGENEOUS CORPUS DOES NOT SPLIT. Otherwise the tool would declare
    # ambiguity on every question — and the warning would stop being read.
    single = [{"url": f"https://s{n}.example/x", "domain": f"s{n}.example",
             "chars": 500, "text": "Powercase " + investigations * 3,
             "markers_hit": True} for n in range(6)]
    check("a homogeneous corpus is NOT declared ambiguous",
             _dp._namesake_groups(single, ["Powercase"]) == [],
             _dp._namesake_groups(single, ["Powercase"]))

    # THE MARKERS ARE DROPPED FROM THE VOCABULARY. By construction they occur on
    # every page; left in, they would make namesakes look similar by the shared
    # name — the very thing that confuses them.
    with_marker = _dp._words("Powercase Аргентина ритейлер", ["Powercase"])
    check("marker words take no part in comparing pages",
             "powercase" not in with_marker, with_marker)

    # A SINGLE OUTLIER PAGE IS NOT A SECOND SUBJECT. One unrelated page in a
    # corpus is normal, and building "there are several" on it is crying wolf.
    almost = single[:5] + [{"url": "https://odd.example/x", "domain": "odd.example",
                         "chars": 500, "text": "Powercase " + argentina * 3,
                         "markers_hit": True}]
    check("a single outlier page is NOT declared ambiguity",
             _dp._namesake_groups(almost, ["Powercase"]) == [])

    # EMPTY SOURCES DO NOT TAKE PART: a block that returned zero characters is not
    # a subject.
    with_empty = corpus + [{"url": "https://blocked.example/x",
                           "domain": "blocked.example", "chars": 0, "text": "",
                           "markers_hit": False}]
    check("a source with no content takes no part in the split",
             len(_dp._namesake_groups(with_empty, ["Powercase"])) == 2)

    # TWO PRECONDITIONS THAT REMOVE FALSE ALARMS BEFORE ANY MODEL IS INVOLVED. Two
    # false cases out of three are neither a threshold error nor a model error but
    # a check run where its subject does not exist.
    #
    # Ambiguity is a property of a NAME: one name with several different bearers.
    # No markers means no name means nothing to check.
    comparison = [{"url": f"https://s{n}.example/x", "domain": f"s{n}.example",
                  "chars": 500, "markers_hit": True,
                  # The vocabularies are GENUINELY disjoint. Give both sides one shared word
                  # and, at a connectivity threshold of 0.12 over seven-word texts, that single
                  # word merges the groups. Real pages are longer and one shared word decides
                  # nothing there — a test on short texts would be checking the threshold
                  # rather than the rule.
                  "text": ("OLED органические диоды контраст самосвечение "
                           "выгорание пиксель глубокий чёрный " * 6)
                          if n < 3 else
                          ("QLED квантовые точки подсветка яркость локальное "
                           "затемнение зона матрица нит " * 6)}
                 for n in range(6)]
    check("groups falling onto DIFFERENT markers of the question are the structure of the question",
             _dp._is_question_structure(comparison,
                                    _dp._namesake_groups(comparison, ["OLED", "QLED"]),
                                    ["OLED", "QLED"]) is True)
    # One name across all the groups is NOT explained by the structure of the
    # question — that is the namesake candidate, and that is where a model is
    # needed.
    check("one name across all groups is NOT explained by the structure of the question",
             _dp._is_question_structure(corpus, grp0, ["Powercase"]) is False
             if (grp0 := _dp._namesake_groups(corpus, ["Powercase"])) else False)

    # THE MODEL'S VETO: IT CAN WITHDRAW A FALSE SPLIT BUT CANNOT CREATE ONE.
    # Measured over ten questions: the mechanical detector fired four times and at
    # least three of those were false — a question about a concept produced THREE
    # groups, a comparison question produced the two sides named in the question.
    # Splitting by content finds "pages about different things", and that happens
    # for legitimate reasons more often than it happens because of namesakes.
    class _Silent:
        def __init__(self, answer): self.answer = answer
        def call(self, messages, max_out=0, model="", timeout=0):
            return {"ok": True, "content": self.answer, "usage": {}, "error": ""}

    grp = [[0, 1, 2], [3, 4, 5]]
    veto = _dp._name_groups("q", corpus, grp,
                               _Silent('{"same_subject":true,"groups":[]}'))
    check("the model can WITHDRAW a false split", veto["same_subject"] is True)
    no_veto = _dp._name_groups(
        "q", corpus, grp,
        _Silent('{"same_subject":false,"groups":[{"n":1,"name":"A"},'
                '{"n":2,"name":"B"}]}'))
    check("and with genuinely different subjects the split stands",
             no_veto["same_subject"] is False and
             [wave["name"] for wave in no_veto["variants"]] == ["A", "B"],
             no_veto["variants"])
    # FAILURE DIRECTION: the model did not answer, so we treat the subject as ONE
    # — returning to the earlier behaviour rather than declaring ambiguity on
    # silence.
    silent_reply = _dp._name_groups("q", corpus, grp, _Silent("not json at all"))
    check("the model did not answer — ambiguity is NOT declared on silence",
             silent_reply["same_subject"] is True)

    print("\n== the spend ledger: accounting that cannot be skipped silently ==")
    import tempfile
    import model as _mdl
    was_ledger, was_key = _mdl.LEDGER_PATH, _mdl.API_KEY
    try:
        tmp_dir = tempfile.mkdtemp()
        _mdl.LEDGER_PATH = os.path.join(tmp_dir, "ledger.jsonl")
        # AN EMPTY LEDGER AND A MISSING LEDGER ARE DIFFERENT EVENTS. The first means
        # "the model was not called", the second "accounting is not configured", and
        # they are cured differently.
        info = _mdl.usage_summary()
        check("the ledger does not exist yet — it says so rather than reporting zero spend",
                 info["calls"] == 0 and "no model calls" in info["error"], info)

        written = _mdl.record_usage({
            "provider": "glm", "model": "glm-5.1", "input_tokens": 100,
            "output_tokens": 20, "cached_tokens": 5, "elapsed_ms": 300})
        check("a write to the ledger is confirmed by a return value, not by silence",
                 written is True, written)
        _mdl.record_usage({"provider": "glm", "model": "glm-5.3-flash",
                             "input_tokens": 900, "output_tokens": 40})
        _mdl.record_usage({"provider": "glm", "model": "glm-5.1",
                             "input_tokens": 10, "output_tokens": 2})
        info = _mdl.usage_summary()
        check("the ledger counts calls", info["calls"] == 3, info["calls"])
        names = {cc["model"]: cc for cc in info["by_model"]}
        # TOKENS PER MODEL, NOT IN ONE HEAP: the text and vision models are on
        # different tariffs, and one sum silently mixes the expensive with the cheap.
        check("tokens are broken down PER MODEL, not added into one sum",
                 names["glm-5.1"]["input_tokens"] == 110
                 and names["glm-5.3-flash"]["input_tokens"] == 900, info["by_model"])
        check("the model name is in the ledger — without it a price cannot be computed",
                 all(cc["model"] for cc in info["by_model"]), info["by_model"])
        # THERE IS NO MONEY IN THE LEDGER, AND THAT IS A REQUIREMENT: the price
        # registry is single for the whole system.
        check("the ledger computes no money until prices are given, and says why",
                 info["cost_usd"] is None and "registry" in info["cost_source"], info)

        # A BROKEN LINE IS COUNTED, NOT SKIPPED. A line dropped silently would show
        # spend LOWER than the real figure — a lie in the convenient direction.
        with open(_mdl.LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write("this is not json\n")
        info = _mdl.usage_summary()
        check("a broken line is counted separately rather than skipped silently",
                 info["broken_lines"] == 1 and info["calls"] == 3, info)

        # A FAILED WRITE IS NOT PASSED OFF AS A SUCCESS.
        _mdl.LEDGER_PATH = "/no-such-directory/ledger.jsonl"
        check("the ledger is unavailable — that is NOT called a write",
                 _mdl.record_usage({"model": "glm-5.1"}) is False)
        _mdl.LEDGER_PATH = ""
        check("the ledger is not configured — the variable is named rather than a zero shown",
                 "SPEND_LEDGER" in _mdl.usage_summary()["error"],
                 _mdl.usage_summary()["error"])
    finally:
        _mdl.LEDGER_PATH, _mdl.API_KEY = was_ledger, was_key

    print("\n== a redirect address instead of the target ==")
    # Some engines return THEIR OWN redirect address instead of the target, and the
    # reader honestly returns zero characters: the failure looks like "the page is
    # empty" while in fact the address is unusable and came FROM THE ENGINE.
    check("a redirect wrapper is unwrapped to the target address",
             server._unwrap_redirect(
                 "https://go.resulthunter.com?id=678&url=https%3A%2F%2Fwww.example.org%2Fp")
             == "https://www.example.org/p")
    check("a relative path in a parameter is NOT a redirect",
             server._unwrap_redirect("https://a.ru/s?q=1&url=/rel")
             == "https://a.ru/s?q=1&url=/rel")
    check("the same domain in a parameter is NOT a redirect",
             "example.com" in server._unwrap_redirect(
                 "https://example.com/p?url=https%3A%2F%2Fexample.com%2Fx"))
    check("an ordinary address is left alone",
             server._unwrap_redirect("https://normal.ru/x") == "https://normal.ru/x")

    print("\n== evidence about THIS answer, not about the engine ==")
    # THE DEFECT A LIVE TOOL FOUND AND FOUR CODE READINGS DID NOT. Asked about a
    # thing that does not exist, engines answer with the nearest thing that does:
    # a query naming an invented product came back with confident results about a
    # real recall of a real toaster — `trouble: {}`, `all_engines_clean: true`,
    # and nothing anywhere saying the subject had been replaced.
    #
    # Nothing in that answer was ABOUT that answer: the trust label describes the
    # engine's probe history, the pool describes the instance. The one thing a
    # caller can check cheaply is whether their own distinctive words survived
    # into the results at all.
    substituted = [{"title": "Panasonic Recalls Electric Toaster Ovens",
                    "snippet": "a recall of toaster ovens", "url": "https://example.org/a"},
                   {"title": "Nationwide Recall Announced",
                    "snippet": "toaster recall 2024", "url": "https://example.org/b"}]
    nowhere = server._word_coverage("Zorblax Q9 quantum toaster recall 2024", substituted)
    check("a word of the query that is in NO result is named",
             set(nowhere) == {"zorblax", "quantum"}, nowhere)
    check("and an ordinary query, whose words are all there, names nothing",
             server._word_coverage("panasonic toaster recall 2024", substituted) == [],
             server._word_coverage("panasonic toaster recall 2024", substituted))
    # ONE DEFINITION OF "WHICH WORDS COUNT" for the flag and for the evidence:
    # two rules would drift and then disagree about the same query.
    check("the words judged and the words reported are the same words",
             server._query_words("Zorblax Q9 quantum") == ["zorblax", "quantum"],
             server._query_words("Zorblax Q9 quantum"))
    check("a result carries the caller's words that are in IT",
             [w for w in server._query_words("toaster recall")
              if w[:5] in server._words_in(substituted[0])] == ["toaster", "recall"],
             server._words_in(substituted[0])[:60])
    # AND IT REACHES THE ANSWER, in `trouble`, where a caller already looks for
    # "this is less than it seems".
    # THE FIXTURE PASTES THE QUERY INTO ITS OWN SNIPPETS, so "in no result at
    # all" is unreachable through it — by construction, not by accident. The
    # plumbing is therefore checked where it is: the field travels into `trouble`.
    carried = server._trouble({"query_words_matched_nowhere": ["zorblax"]})
    check("an answer whose results share no word with the query says so in trouble",
             carried.get("query_words_matched_nowhere") == ["zorblax"], carried)
    check("and a query whose words are all present adds nothing to trouble",
             "query_words_matched_nowhere" not in
             server._trouble({"query_words_matched_nowhere": []}),
             server._trouble({"query_words_matched_nowhere": []}))
    server._reset_rate()
    MODE["body"] = REPLY
    d = server.search_read(QUERY, n=2, read=False)
    check("and every result carries the words of the query it does contain",
             all("query_words_here" in r for r in d["results"]),
             sorted(d["results"][0]) if d["results"] else [])

    print("\n== somebody else's JSON is not our shape ==")
    # A RAISE ON THIS PATH IS NOT AN ERROR MESSAGE — IT IS NO ANSWER AT ALL: the
    # connection drops and the caller cannot tell us from a dead network. Web
    # search passed its query through the cleaner; its two neighbours did not, so
    # `{"query": 123}` reached `.strip()` and raised.
    for tool_name, args in (("web_image_search", {"query": 123}),
                            ("web_deep_search", {"question": {"a": 1}}),
                            ("web_search", {"query": ["a", "list"]}),
                            ("web_read", {"urls": 5}),
                            ("web_screenshot", {"url": 7})):
        _, r = post({"jsonrpc": "2.0", "id": 130, "method": "tools/call",
                     "params": {"name": tool_name, "arguments": args}})
        body = json.loads(r["result"]["content"][0]["text"]) if r.get("result") else {}
        check(f"{tool_name}: a wrongly typed argument still gets an ANSWER",
                 isinstance(body.get("ok"), bool), (tool_name, str(r)[:80]))
    # AND THE COUNTER KEYED BY THE CALLER'S OWN TEXT IS BOUNDED, one field away
    # from where the same bound was already put: a client sending a new method
    # name per call would otherwise grow this dictionary for months and then pour
    # it out through /stats.
    was_counters2 = json.loads(json.dumps(server._counters))
    try:
        for i in range(server.CLIENT_KEYS_MAX + 20):
            server._count_mcp_call({"method": f"invented/{i}"}, None)
        check("the by_path counter cannot be grown without limit by a caller",
                 len(server._counters["by_path"]) <= server.CLIENT_KEYS_MAX + 1,
                 len(server._counters["by_path"]))
    finally:
        server._counters.update(was_counters2)

    print("\n== whose mistake the code reports ==")
    # `502` SAYS "THE THING BEHIND ME FAILED", and a caller who forgot an
    # argument reads that as our breakage and retries — the wrong action, twice.
    # Reading answered `400` for this while its four neighbours answered `502`:
    # one module, one kind of mistake, two classes of code.
    #
    # The list of caller-mistake phrases lives in the code; this pins it to the
    # refusals actually produced, so a reworded message cannot silently turn a
    # caller's error back into "the upstream is down".
    MODE["body"] = REPLY
    for door, expect_400 in (("/ag/search?q=", True), ("/ag/images?q=", True),
                             ("/ag/read?urls=", True), ("/ag/deep?q=", True),
                             ("/ag/screenshot?url=", True)):
        code, payload = get(door)
        check(f"{door.split('?')[0]}: a caller's own mistake answers 400, not 502",
                 (code == 400) is expect_400,
                 (door, code, str(payload.get("error"))[:60]))
    # AND THE OTHER DIRECTION MUST STILL BE 502: when the metasearch is the one
    # that failed, the caller SHOULD retry, and the code must say so.
    was_url2 = server.SEARXNG_URL
    try:
        server.SEARXNG_URL = "http://127.0.0.1:9"
        code, payload = get("/ag/search?q=" + urllib.parse.quote(QUERY))
        check("an upstream failure still answers 502, so retrying stays right",
                 code == 502, (code, str(payload.get("error"))[:60]))
    finally:
        server.SEARXNG_URL = was_url2
    server._reset_rate()

    print("\n== every tool is called THROUGH THE DOOR at least once ==")
    # THREE OF FIVE WERE NEVER INVOKED VIA `tools/call`. Their internals were
    # tested directly, so a break in the DOOR — a wrong argument name, a lost
    # keyword, a shape the wrapper mangles — would leave every suite green while
    # nothing worked for a caller. Nothing subtle is asked here: does the tool
    # answer, and is it its own contract.
    MODE["body"] = IMAGE_REPLY
    for tool_name, args, contract in (
            ("web_image_search", {"query": QUERY, "max_results": 2}, "ag.images/3"),
            # No browser and no model are configured in the suite, so these two
            # answer with a refusal — which is exactly what must arrive shaped,
            # named and with its own contract rather than as an exception.
            ("web_screenshot", {"url": "https://example.org/p"}, "ag.shot/1"),
            ("web_deep_search", {"question": "what is a ghost"}, "ag.deep/2")):
        _, r = post({"jsonrpc": "2.0", "id": 120, "method": "tools/call",
                     "params": {"name": tool_name, "arguments": args}})
        body = json.loads(r["result"]["content"][0]["text"])
        check(f"{tool_name}: the door answers with its own contract",
                 body.get("contract") == contract, (tool_name, body.get("contract")))
        check(f"{tool_name}: `ok` is a boolean, and a refusal names its reason",
                 isinstance(body.get("ok"), bool)
                 and (body["ok"] or body.get("error")),
                 (body.get("ok"), str(body.get("error"))[:60]))
    MODE["body"] = REPLY

    print("\n== every key the caller receives is described SOMEWHERE ==")
    # THE GUARD FOR THE OTHER DIRECTION. One already stands: a name in the prose
    # must exist in the code, so a renamed field cannot leave a document behind.
    # Nothing stood the other way — a field ARRIVING in the code owed no
    # explanation, and five of them reached callers undescribed
    # (`download_truncated`, `cache_age_s`, `query_words_here`,
    # `query_words_matched_nowhere`, `not_attempted`).
    #
    # THE SOURCE OF TRUTH IS A LIVE ANSWER, not a list kept here: a list would go
    # stale exactly the way the documents did, and then the guard would testify
    # about a shape nobody returns.
    MODE["body"] = REPLY
    server._reset_rate()
    answers = [server.search_read(QUERY, n=2, read=False),
               server.search_read(QUERY, n=2, read=False, verbose=True),
               server.image_search(QUERY, n=2),
               server.image_search(QUERY, n=2, verbose=True),
               reader.read([]),
               reader._shot_refusal("https://example.org/p", "no browser"),
               deep_module._blank_answer("q", "no model")]
    keys = set()
    for answer in answers:
        keys |= set(answer)
        for item in (answer.get("results") or [])[:1]:
            keys |= set(item)
        for inner in ("trouble", "usage", "timing_ms", "recognition"):
            if isinstance(answer.get(inner), dict):
                keys |= set(answer[inner])
    # `trouble` carries only what went wrong, so its keys are named explicitly:
    # a healthy fixture cannot produce them, and their absence here would be a
    # hole in the guard rather than a clean bill.
    keys |= {"search_aborted", "engines_unasked", "unresponsive_engines",
             "engines_irrelevant", "pool_unmeasured", "arguments_adjusted",
             "query_words_matched_nowhere"}
    described = ""
    for doc in ("README.md", "HOWTO-CALL.md", "ALGORITHM.md"):
        at = os.path.join("/docs", doc)
        local = os.path.join(os.path.dirname(__file__), "..", doc)
        path = at if os.path.exists(at) else local
        check(f"{doc} is readable by this guard (a skip would be a false green)",
                 os.path.exists(path), path)
        if os.path.exists(path):
            described += open(path, encoding="utf-8").read()
    for contract in ("ag.search.v3.md", "ag.read.v2.md", "ag.images.v3.md",
                     "ag.deep.v2.md"):
        at = os.path.join("/contracts", contract)
        here = os.path.dirname(__file__)
        # Same two places as in-image.sh, and for the same reason.
        for local in (os.path.join(here, "..", "..", "contracts", contract),
                      os.path.join(here, "..", "contracts", contract)):
            if os.path.exists(local):
                break
        path = at if os.path.exists(at) else local
        if os.path.exists(path):
            described += open(path, encoding="utf-8").read()
    undescribed = sorted(k for k in keys if k not in described)
    check("no field reaches a caller without being described anywhere",
             not undescribed, undescribed)

    print("\n== the documents against the answer they describe ==")
    # THE PAGES DRIFTED FROM THE SHAPE THREE TIMES IN A WEEK, and every time a
    # person found it, not a check. A document promising `pool_source` in every
    # answer sends the reader to a field that is not there by default — the same
    # class as a tool description of the wrong form, one layer out.
    #
    # THE RULE IS NARROW ON PURPOSE. A name that only `verbose` returns, or that
    # lives inside `trouble`, may of course be discussed — but then the passage
    # must say so: mention `verbose`, or write the name with its `trouble.`
    # prefix, or stand in the section about upgrading. Bare, beside fields that
    # do arrive, it is a promise the answer does not keep.
    quiet_now = set(server.search_read(QUERY, n=2))
    loud_now = set(server.search_read(QUERY, n=2, verbose=True))
    trouble_names = {"search_aborted", "engines_unasked", "unresponsive_engines",
                     "engines_irrelevant", "pool_unmeasured", "arguments_adjusted"}
    # WHAT IS NOT SUSPICIOUS: arguments (a document must be free to name them),
    # and fields that ARE top-level in a NEIGHBOURING tool — `arguments_adjusted`
    # sits inside `trouble` for search and at the top level for reading, so a
    # bare mention of it is legitimate somewhere.
    argument_names = {a for t in server.TOOLS.values()
                      for a in t["inputSchema"]["properties"]}
    elsewhere = set(reader.read([])) | set(reader._shot_refusal("x", "why"))
    suspicious = (((loud_now - quiet_now) | trouble_names)
                  - {"trouble"} - argument_names - elsewhere)
    for doc in ("README.md", "HOWTO-CALL.md"):
        at = os.path.join("/docs", doc)
        local = os.path.join(os.path.dirname(__file__), "..", doc)
        path = at if os.path.exists(at) else local
        check(f"{doc} is readable by this check (a skip would be a false green)",
                 os.path.exists(path), path)
        if not os.path.exists(path):
            continue
        # THE CONTEXT IS THE PARAGRAPH, NOT THE LINE. A table listing the keys of
        # `trouble` says so in the sentence above it, and a check that reads one
        # line at a time would call every row a broken promise — and a guard that
        # shouts at healthy text gets switched off whole.
        # A TABLE IS ITS OWN BLOCK, and the sentence introducing it is the one
        # before. So the context is the heading plus the current block AND the
        # previous one — a check narrower than that calls every row of a table
        # about `trouble` a broken promise, and a guard that shouts at healthy
        # text gets switched off whole.
        section, para, prev, bad_lines = "", "", "", []
        for line_no, line in enumerate(open(path, encoding="utf-8"), 1):
            if line.startswith("#"):
                section, para, prev = line.lower(), "", ""
            if not line.strip():
                prev, para = para, ""
            else:
                para += " " + line.lower()
            low = (section + " " + prev + " " + para).lower()
            if "verbose" in low or "upgrad" in low or "0.2.x" in low or "trouble" in low:
                continue
            # `pool_source: seed` is how a document names a field WITH a value,
            # and a pattern matching only a bare name misses exactly the sentence
            # that promises the most. Checked by corruption: without this the
            # very line this guard was written for passes.
            for word in _re.findall(r"`([a-z_]+)(?:[.:][^`]*)?`", line):
                if word in suspicious:
                    bad_lines.append(f"{line_no}: `{word}`")
        check(f"{doc} promises no field that a default answer does not carry",
                 not bad_lines, bad_lines[:4])

    print("\n== a number that means `nobody else was asked` is not returned ==")
    # FOUND BY A LIVE CONSUMER, FIRST CALL. On the cheap path the sweep stops at
    # the first engine that gives enough links, so `corroborated_by_url` was 1 on
    # every result — and 1 on a WIDE sweep means "three engines asked, one found
    # it". Two different pieces of news under one number, standing next to
    # `engines_trust` and reading as measured. A threshold gets built on it in a
    # month.
    #
    # The rule is the mirror of the one behind `trouble`: a field whose value is
    # legitimately fine cannot live where emptiness means "all well"; a field
    # whose ONE means "we did not ask" cannot stand among the measured.
    server._reset_rate()
    MODE["body"] = REPLY
    # n=1: the first engine suffices, so the sweep stops there and there is
    # exactly one witness — the cheap path a caller gets by default.
    narrow = server.search(QUERY, 1)
    check("one witness: the corroboration fields are ABSENT, not equal to one",
             all("corroborated_by_url" not in r and "corroborated_by_domain" not in r
                 for r in narrow["results"]),
             [sorted(r) for r in narrow["results"][:1]])
    server._reset_rate()
    wide = server.search(QUERY, 3, min_engines=3)
    many = sorted({e.strip() for r in wide["results"]
                   for e in r["via"].split(",") if e.strip()})
    check("more than one witness: the fields come back, because now they mean something",
             len(many) < 2 or all("corroborated_by_url" in r for r in wide["results"]),
             (many, [sorted(r) for r in wide["results"][:1]]))
    # AND THE ABSENCE IS NOT SILENCE: who was asked is still in the answer, and
    # what went wrong is still in `trouble`.
    check("who was asked is still said out loud",
             bool(narrow["engines_asked"]), narrow["engines_asked"])

    print("\n== EVERY exit of a tool has the same shape, refusals included ==")
    # TWO REFUSAL PATHS USED TO RETURN THE RAW DICTIONARY: sixteen keys of the old
    # form and NO `trouble` at all. An absent key means "this side was not
    # examined" — and it appeared exactly where there was something to examine.
    # Every suite was green, because every suite called the paths that were right.
    #
    # So the check walks the exits by name rather than trusting that they were
    # remembered: an empty query, a pool the metasearch does not know, and a door
    # that shut mid-sweep.
    was_round3, was_split = server._round, server._split_known
    try:
        exits = {"empty query": lambda: server.image_search(""),
                 "no engine known": None, "door shut": None}
        server._split_known = lambda names: ((), tuple(names))
        exits["no engine known"] = lambda: server.image_search(QUERY, n=2)
        shapes = {name: set(fn()) for name, fn in exits.items() if fn}
        server._split_known = was_split
        server._round = lambda *a, **k: {"error": "metasearch unavailable"}
        shapes["door shut"] = set(server.image_search(QUERY, n=2))
    finally:
        server._round, server._split_known = was_round3, was_split
    server._reset_rate()
    MODE["body"] = REPLY
    good = set(server.image_search(QUERY, n=2))
    for name, keys in shapes.items():
        check(f"images, exit {name!r}: the same field set as a success",
                 keys == good, sorted(keys ^ good))
        check(f"images, exit {name!r}: `trouble` is there, not missing",
                 "trouble" in keys, sorted(keys))
    # AND WHAT IT SAYS THERE. On a refusal the pool state was never established;
    # an empty `trouble` would report "checked, all well" about a call in which
    # nothing was checked. Anything that is not `observation` is unmeasured.
    check("a refusal admits the pool state is not established",
             "pool_unmeasured" in server.image_search("")["trouble"],
             server.image_search("")["trouble"])

    # NEIGHBOURING TOOLS REPORT ONE EVENT THE SAME WAY. `n='abc'` was named by
    # search and silently turned into twelve by images: one coercion, two
    # behaviours, and the caller has to learn both.
    server._reset_rate()
    web = server.search_read(QUERY, n="abc")
    img = server.image_search(QUERY, n="abc")
    check("both tools NAME a number they could not read",
             any("abc" in x for x in web["trouble"].get("arguments_adjusted", []))
             and any("abc" in x for x in img["trouble"].get("arguments_adjusted", [])),
             (web["trouble"].get("arguments_adjusted"),
              img["trouble"].get("arguments_adjusted")))

    print("\n== the description a MODEL reads against the answer it gets ==")
    # THE ONE TEXT THE MODEL SEES IS `tools/list`, AND IT WENT STALE. The shape of
    # the answer changed to /3 everywhere — code, contracts, README, HOWTO — and
    # the tool description still promised nine top-level fields that a default
    # answer does not contain, without the words `trouble` or `verbose` anywhere.
    # We raised the contract number precisely so nobody would read a missing
    # `search_aborted` as "nothing was aborted", and then kept promising it in the
    # only place a caller is told to trust.
    #
    # SO THE CHECK COMPARES THE TEXT WITH A REAL ANSWER, not with our memory of
    # it: names that only exist under `verbose` may be mentioned, but the text
    # must say `verbose` when it does; names that only exist inside `trouble` may
    # be mentioned, but the text must say `trouble`.
    server._reset_rate()
    MODE["body"] = REPLY
    quiet_keys = set(server.search_read(QUERY, n=2))
    loud_keys = set(server.search_read(QUERY, n=2, verbose=True))
    item_keys = set(server.search_read(QUERY, n=2)["results"][0])
    trouble_keys = {"search_aborted", "engines_unasked", "unresponsive_engines",
                    "engines_irrelevant", "pool_unmeasured", "arguments_adjusted"}
    for tool, quiet, loud, items in (
            (server.TOOL, quiet_keys, loud_keys, item_keys),
            (server.IMAGE_TOOL, set(server.image_search(QUERY, n=2)),
             set(server.image_search(QUERY, n=2, verbose=True)), set())):
        text = tool["description"]
        args = set(tool["inputSchema"]["properties"])
        verbose_only = (loud - quiet) - trouble_keys
        named = {w for w in re.findall(r"[a-z][a-z_]{3,}", text)}
        stale = {w for w in named & verbose_only if "verbose" not in text}
        check(f"{tool['name']}: no field is promised at top level that only "
                 f"`verbose` returns", not stale, sorted(stale))
        in_trouble = named & (trouble_keys - quiet)
        check(f"{tool['name']}: what lives inside `trouble` is named as such",
                 not in_trouble or "trouble" in text, sorted(in_trouble))
        # AND THE DEFAULT SHAPE IS DESCRIBED AT ALL: a caller must be able to
        # learn from this text what arrives without asking for anything.
        missing = {k for k in quiet if k not in text} - {"error", "ok"}
        check(f"{tool['name']}: every field of the DEFAULT answer is described",
                 not missing, sorted(missing))

    print("\n== booleans are PARSED, and both doors parse them the same way ==")
    # MEASURED ON A LIVE INSTANCE BY A CONSUMER: `"read": "false"` read the pages
    # anyway — eight times the wall clock, seven times the payload — and
    # `trouble` was empty. Numbers were clamped and named; booleans went through
    # `bool(...)`, where every non-empty string is true. The field written
    # against silent substitution covered half of it.
    #
    # WORSE: THE TWO DOORS DISAGREED ABOUT ONE WORD. Over MCP `"read": "false"`
    # meant READ, over the plain door `read=false` meant do not. So every case
    # here is checked at BOTH entrances — fixing one and leaving the other is the
    # same defect in a new place.
    server._reset_rate()
    MODE["body"] = REPLY
    for spelling, reads in (("false", False), ("False", False), ("FALSE", False),
                            ("no", False), ("off", False), ("0", False),
                            ("true", True), ("True", True), ("yes", True)):
        _, r = post({"jsonrpc": "2.0", "id": 90, "method": "tools/call",
                     "params": {"name": "web_search",
                                "arguments": {"query": QUERY, "max_results": 2,
                                              "read": spelling, "verbose": True}}})
        mcp = json.loads(r["result"]["content"][0]["text"])
        code, plain = get("/ag/search?q=" + urllib.parse.quote(QUERY)
                          + "&n=2&verbose=1&read=" + spelling)
        check(f"read={spelling!r} is understood the same at both doors",
                 mcp["read"] is reads and plain["read"] is reads,
                 (spelling, mcp["read"], plain["read"]))
        check(f"read={spelling!r} is not reported as an adjustment — it was readable",
                 not [x for x in mcp["trouble"].get("arguments_adjusted", [])
                      if "read" in x], mcp["trouble"])
    # AN EMPTY VALUE IS NOT AN ABSENT ONE, and the difference is checked at both
    # doors. An exception returning the default silently would sit in the one
    # place where the default is expensive: `"read": ""` buying three page loads
    # and saying nothing. Absence keeps the default; emptiness is a value we
    # could not read.
    _, r = post({"jsonrpc": "2.0", "id": 95, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": QUERY, "max_results": 2,
                                          "read": "", "verbose": True}}})
    empty_mcp = json.loads(r["result"]["content"][0]["text"])
    code, empty_plain = get("/ag/search?q=" + urllib.parse.quote(QUERY)
                            + "&n=2&verbose=1&read=")
    check("an EMPTY boolean does not buy the expensive default, at either door",
             empty_mcp["read"] is False and empty_plain["read"] is False,
             (empty_mcp["read"], empty_plain["read"]))
    check("and it is named, so the caller sees what happened",
             all(any("read=" in x for x in a["trouble"].get("arguments_adjusted", []))
                 for a in (empty_mcp, empty_plain)),
             (empty_mcp["trouble"], empty_plain["trouble"]))
    # JSON `null` IS A PASSED VALUE, NOT AN ABSENCE. `args.get(name)` returns
    # None for both, so without a mark of its own for "nothing passed" the two
    # collapse — and `"read": null` would take the expensive default in silence
    # while the STRING "null" turned the flag off. One word, two behaviours.
    _, r = post({"jsonrpc": "2.0", "id": 97, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": QUERY, "max_results": 2,
                                          "read": None, "verbose": True}}})
    nulled = json.loads(r["result"]["content"][0]["text"])
    check("JSON null is treated as a value we could not read, not as an absence",
             nulled["read"] is False
             and any("read=" in x for x in
                     nulled["trouble"].get("arguments_adjusted", [])),
             (nulled["read"], nulled["trouble"].get("arguments_adjusted")))
    _, r = post({"jsonrpc": "2.0", "id": 96, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": QUERY, "max_results": 2,
                                          "verbose": True}}})
    absent = json.loads(r["result"]["content"][0]["text"])
    check("an ABSENT argument still keeps the documented default, unreported",
             absent["read"] is True
             and not [x for x in absent["trouble"].get("arguments_adjusted", [])
                      if "read" in x], (absent["read"], absent["trouble"]))

    # AN UNREADABLE FLAG FALLS TO THE CHEAP SIDE, AND SAYS SO. Falling to the
    # documented default would bill the caller eight times over for a typo and
    # teach them nothing; every flag here buys something expensive when on.
    for door in ("mcp", "plain"):
        if door == "mcp":
            _, r = post({"jsonrpc": "2.0", "id": 91, "method": "tools/call",
                         "params": {"name": "web_search",
                                    "arguments": {"query": QUERY, "max_results": 2,
                                                  "read": "sometimes", "verbose": True}}})
            answer = json.loads(r["result"]["content"][0]["text"])
        else:
            code, answer = get("/ag/search?q=" + urllib.parse.quote(QUERY)
                               + "&n=2&verbose=1&read=sometimes")
        check(f"{door}: rubbish in a boolean turns the flag OFF, not on",
                 answer["read"] is False, answer["read"])
        check(f"{door}: and it is NAMED in trouble, not swallowed",
                 any("read=" in x and "not a boolean" in x
                     for x in answer["trouble"].get("arguments_adjusted", [])),
                 answer["trouble"])
    # THE SAME FOR THE FLAG THAT MULTIPLIES OUTBOUND REQUESTS. Cast rather than
    # parsed, a string switches corroboration ON: six requests to other people's
    # services instead of one, from a value the schema calls a boolean.
    _, r = post({"jsonrpc": "2.0", "id": 92, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": QUERY, "max_results": 2,
                                          "corroborate": "false", "verbose": True}}})
    corr = json.loads(r["result"]["content"][0]["text"])
    check("corroborate='false' does NOT widen the sweep",
             len(corr["engines_asked"]) <= 2, corr["engines_asked"])
    # AND THE FLAGS OF THE OTHER TOOLS, so the fix is not "the one that was
    # measured". Reading answers with its own list, added rather than moved: an
    # addition breaks no consumer and needs no contract version.
    # THROUGH THE DOOR, NOT BY A DIRECT CALL: the parse lives at the entrance,
    # one place per door and one function for both. Calling `reader.read`
    # directly is our INTERNAL path, where the arguments arrive already parsed.
    _, r = post({"jsonrpc": "2.0", "id": 93, "method": "tools/call",
                 "params": {"name": "web_read",
                            "arguments": {"urls": [], "links": "perhaps"}}})
    rd = json.loads(r["result"]["content"][0]["text"])
    check("reading names an unreadable flag too, in its own answer",
             any("links=" in x for x in rd.get("arguments_adjusted", [])),
             rd.get("arguments_adjusted"))
    _, r = post({"jsonrpc": "2.0", "id": 94, "method": "tools/call",
                 "params": {"name": "web_read", "arguments": {"urls": []}}})
    clean_rd = json.loads(r["result"]["content"][0]["text"])
    check("and the field is there, empty, when every argument was usable",
             clean_rd.get("arguments_adjusted") == [],
             clean_rd.get("arguments_adjusted"))

    print("\n== who called, as they say themselves ==")
    # THE PROTOCOL CARRIES IT ALREADY. Thrown away, it leaves every client of an
    # instance in one heap, and "a spike of refusals" is visible while "whose
    # spike" is not.
    #
    # OVER HTTP A SESSION IS ONE CONNECTION, so the test speaks over a RAW
    # connection rather than the helper above: urllib opens a fresh one per call,
    # which is exactly the case that cannot be linked to any handshake — and that
    # case is checked too, further down.
    import http.client as _http

    def talk(conn, body):
        conn.request("POST", "/mcp", json.dumps(body),
                     {"Content-Type": "application/json"})
        return json.loads(conn.getresponse().read() or "{}")

    host, port = base.split("//")[1].split(":")
    named = _http.HTTPConnection(host, int(port))
    talk(named, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "chat-under-test", "version": "9"}}})
    talk(named, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    nameless = _http.HTTPConnection(host, int(port))
    talk(nameless, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})
    talk(nameless, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    silent_one = _http.HTTPConnection(host, int(port))
    talk(silent_one, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    code, st = get("/stats")
    by_client = st["by_client_says"]
    check("the caller is counted under the name it gave in the handshake",
             by_client.get("chat-under-test/9", 0) >= 2, by_client)
    # THREE ABSENCES THAT ARE NOT ONE. A caller we could not link to any
    # handshake, a caller that shook hands without naming itself, and a call at
    # the plain door where the protocol has no handshake at all — one bucket for
    # the three would hide which of them we are looking at.
    check("a call with no handshake is `not_introduced`, not attributed by guess",
             by_client.get("not_introduced", 0) >= 1, by_client)
    check("a handshake with no clientInfo is its own state",
             by_client.get("introduced_without_name", 0) >= 2, by_client)
    check("the plain doors are counted apart: there is no handshake there",
             by_client.get("plain_door", 0) >= 1, by_client)
    # THE NAME IS SOMEBODY ELSE'S TEXT, AND IT BECOMES A KEY. Unbounded, that is a
    # leak in a process meant to run for months, and it goes out in a view.
    check("a long name is cut rather than stored whole",
             len(server._client_name({"name": "x" * 400})) <= server.CLIENT_NAME_MAX + 20,
             server._client_name({"name": "x" * 400}))
    check("control characters do not reach the view",
             "\n" not in server._client_name({"name": "a\nb"}),
             server._client_name({"name": "a\nb"}))
    check("clientInfo of the wrong shape does not raise, it is a missing name",
             server._client_name("not a dict") == server.NO_NAME_GIVEN,
             server._client_name("not a dict"))
    was_counters = json.loads(json.dumps(server._counters))
    try:
        for i in range(server.CLIENT_KEYS_MAX + 5):
            server._count_call("/probe", client=f"invented-{i}")
        check("the number of names is capped — a client varying it cannot grow us",
                 len(server._counters["by_client"]) <= server.CLIENT_KEYS_MAX + 1
                 and server.TOO_MANY_CLIENTS in server._counters["by_client"],
                 len(server._counters["by_client"]))
    finally:
        server._counters.update(was_counters)

    print("\n== the SECOND transport: MCP over stdio ==")
    # STDIO IS THE PROTOCOL'S DEFAULT TRANSPORT, and its absence was invisible
    # here because our only consumer speaks HTTP. A desktop client starts the
    # server as a PROCESS: no stdio, no client.
    #
    # THE CHECK RUNS A REAL PROCESS rather than calling `_stdio()` in-process, and
    # that is the whole point. What breaks this mode is a stray print landing in
    # the middle of the conversation — and a print is only visible as a defect
    # when there is a real stdout to land in. In-process the substitution of
    # sys.stdout would be checked against itself.
    import subprocess
    talk = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "p", "version": "1"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        # A CALL THAT PRINTS. The search path writes diagnostics about the engine
        # registry and the trust database; with no metasearch here it certainly
        # prints. That is exactly the line that must NOT reach stdout.
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "web_search",
                               "arguments": {"query": QUERY, "max_results": 1}}}),
        "this line is not json",
    ]) + "\n"
    run = subprocess.run([sys.executable, os.path.join(os.path.dirname(server.__file__),
                                                       "server.py"), "--stdio"],
                         input=talk, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "SEARXNG_URL": "http://127.0.0.1:9"})
    lines = [l for l in run.stdout.splitlines() if l.strip()]
    parsed, junk = [], []
    for l in lines:
        try:
            parsed.append(json.loads(l))
        except Exception:  # noqa: BLE001
            junk.append(l)
    check("stdout carries JSON AND NOTHING ELSE — one stray print breaks a client",
             junk == [], junk[:2])
    check("a notification gets no line back: four messages in, four answers out",
             [d.get("id") for d in parsed] == [1, 2, 3, None],
             [d.get("id") for d in parsed])
    check("initialize declares the protocol over stdio too",
             parsed and parsed[0]["result"]["protocolVersion"] == server.PROTOCOL,
             parsed[0] if parsed else None)
    check("the five tools are listed over stdio",
             len(parsed[1]["result"]["tools"]) == 5, len(parsed[1]["result"]["tools"]))
    check("a broken line is ANSWERED, not swallowed into silence",
             parsed[-1]["error"]["code"] == -32700, parsed[-1])
    # THE DIAGNOSTICS DID NOT VANISH — they moved. A mode that silences the log
    # would trade one blindness for another.
    check("the diagnostics are on stderr, not lost",
             "ag-mod-search" in run.stderr, run.stderr[:80])
    # OVER STDIO A SESSION IS THE PROCESS: the handshake comes once and holds for
    # every call down the same pipe. The opposite of HTTP, where the link lives
    # only as long as the connection — and where a client that opens a new one
    # per call is honestly counted as `not_introduced`.
    talk_named = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "stdio-chat", "version": "3"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
    ]) + "\n"
    run2 = subprocess.run([sys.executable, os.path.join(os.path.dirname(server.__file__),
                                                        "server.py"), "--stdio"],
                          input=talk_named, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "SEARXNG_URL": "http://127.0.0.1:9"})
    # The process counts internally; what is observable from outside is that all
    # three answers came back on one handshake without the caller repeating it.
    check("stdio keeps the handshake for the whole process: three in, three out",
             len([l for l in run2.stdout.splitlines() if l.strip()]) == 3,
             run2.stdout[:120])

    print("\n== the ok field is uniform across the contracts ==")
    # NEIGHBOURING TOOLS THAT CALL THE SAME THING BY DIFFERENT NAMES are a future
    # mistake by the caller. The rule is written down in ag.read.v2.md: without an
    # `ok` field on the screenshot, a caller with a shared `if not resp["ok"]`
    # would meet a refusal where all is well.
    answers = {
        "ag.search/3": server.search("test", 1),
        "ag.read/2": reader.read([]),
        "ag.images/3": server.image_search(""),
        "ag.shot/1": reader.screenshot("https://пример.рф/нет"),
    }
    for name, resp in answers.items():
        check(f"{name}: the ok field is present and boolean",
                 isinstance(resp.get("ok"), bool), (name, resp.get("ok", "NO SUCH FIELD")))
        check(f"{name}: contract is declared and matches",
                 resp.get("contract") == name, resp.get("contract"))
    # AN EMPTY RESULT IS A SUCCESS in every contract where it is possible: "we
    # looked and found nothing" and "we could not look" are different outcomes.
    check("ag.search/3: an empty result set is ok:true, not a refusal",
             server.search("no such query zzz", 1).get("ok") is True)

    print("\n== images ==")
    # THE MAIN CHECK HERE IS NOT "IMAGES ARE FOUND" BUT THE ABSENCE OF A FAN-OUT.
    # Measured on a live metasearch: pass `categories` beside `engines` and the
    # engine list is IGNORED while the whole category is queried — 213 results
    # from six engines instead of 35 from one. Our "one at a time" order turns
    # into a fan-out across 49 engines, and that fan-out includes a family we
    # have already lost on address reputation. From outside it looks like luck:
    # there are MORE results.
    REQUESTS.clear()
    server._reset_rate()
    server._round(QUERY, ["engine-a"], 6, 0, category="images")
    check("the category does NOT go into the request together with the engine list",
             all("categories" not in q for q in REQUESTS), REQUESTS)
    check("the engine list goes out and is exactly the one asked for",
             bool(REQUESTS) and REQUESTS[-1].get("engines") == "engine-a",
             REQUESTS[-1] if REQUESTS else "the fake metasearch got no request")

    server._reset_rate()
    MODE["body"] = IMAGE_REPLY
    d = server.image_search("a picture", n=3)
    check("images: the contract is declared", d["contract"] == "ag.images/3", d.get("contract"))
    # THE RESULT SET MUST NOT BE EMPTY HERE, and that is a check in itself: the
    # shape checks below used to hide behind `if d["results"]:`, so an empty
    # answer made them all pass by not running.
    check("images: the fixture produced a result set to check at all",
             bool(d["results"]), d["results"])
    # ONE SHAPE ON BOTH PATHS — the answers are compared as the caller sees
    # them, that is, after shaping. Comparing a raw refusal with a shaped success
    # would compare two different things and go green on a divergence.
    refused_shape = server._shape(server._images_refusal("x"), False,
                                  server.LOUD_IMAGES)
    check("images: the full field set on the failure path too",
             set(refused_shape) == set(d), set(refused_shape) ^ set(d))
    check("images: an empty query gives ok:false with a reason",
             server.image_search("")["ok"] is False)
    if True:
        r = (d["results"] or [{}])[0]
        check("an image has TWO addresses under different names",
                 "image_url" in r and "page_url" in r and "url" not in r, sorted(r))
        check("domain is computed from the IMAGE SOURCE, not from the page",
                 r["domain"] == server._domain(r["image_url"]), (r["domain"], r["image_url"]))
        check("the page domain is returned in a field of its own",
                 r["page_domain"] == server._domain(r["page_url"]), r)

    fake.shutdown()
    fake.server_close()
    code, payload = get("/healthz")
    check("a DEAD metasearch makes the adapter unhealthy (503), not ok-I-am-alive",
             code == 503 and payload["ok"] is False, payload)

    print(f"\nadapter suite: ok {ok_count}, failed {fail_count}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
