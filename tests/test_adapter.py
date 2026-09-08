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
    check("the answer declares the ag.search/1 contract",
             payload["contract"] == "ag.search/1", payload)
    check("a non-link result is dropped (2 of 3)", payload["count"] == 2, payload)
    check("the title is stripped of extra spaces",
             payload["results"][0]["title"] == "First", payload["results"][0])
    # `via` names those that returned the link. With sequential querying that is
    # usually one engine; several names appear either when we had to go further
    # down the order or in corroboration mode.
    check("via names the engines that were asked",
             set(x.strip() for x in payload["results"][0]["via"].split(","))
             <= set(payload["engines_asked"]), payload["results"][0]["via"])
    check("silent engines reach the consumer",
             payload["unresponsive_engines"] == [["zapmeta", "timeout"]], payload)
    check("a query in Cyrillic goes out with language=ru",
             REQUESTS[-1]["language"] == "ru", REQUESTS[-1])
    check("json format is requested, pages start at one",
             REQUESTS[-1]["format"] == "json" and REQUESTS[-1]["pageno"] == "1", REQUESTS[-1])

    _, r = post({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                 "params": {"name": "web_search",
                            "arguments": {"query": "Boris Ivanov", "page": 2}}})
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
             and payload["count"] == 0, r["result"])
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
        check(f"MCP: the field is present and [] when {name}",
                 payload.get("unresponsive_engines") == [], payload)
        code, payload = get("/ag/search?q=x")
        check(f"ag.search/1: the field is present and [] when {name}",
                 payload.get("unresponsive_engines") == [], payload)
    MODE["body"] = REPLY
    _, r = post({"jsonrpc": "2.0", "id": 31, "method": "tools/call",
                 "params": {"name": "web_search", "arguments": {"query": "x"}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    check("MCP: a silent engine arrives with its reason",
             payload["unresponsive_engines"] == [["zapmeta", "timeout"]], payload)
    code, payload = get("/ag/search?q=x")
    check("ag.search/1: a silent engine arrives with its reason",
             payload["unresponsive_engines"] == [["zapmeta", "timeout"]], payload)

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
    silent = {m[0] for m in payload["unresponsive_engines"]}
    # The meaning of the difference: an engine that was asked and appears neither
    # among those that answered nor among those that stayed silent returned
    # emptiness SILENTLY — otherwise nobody would see its failure.
    check("asked minus answered minus silent is computable and consistent",
             (asked - answered - silent) == (asked - answered - silent)
             and asked >= answered, (sorted(asked), sorted(answered)))
    code, payload = get("/ag/search?q=x")
    check("ag.search/1 returns the same two fields",
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
    check("engines_used shows how many engines were needed",
             payload["engines_used"] == 1, payload["engines_used"])

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
    check("the skipped ones are named explicitly",
             server.order()[0] in payload["engines_skipped"], payload["engines_skipped"])
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
    check("even in a refusal the engine fields are in place",
             all(k in payload for k in ("unresponsive_engines", "engines_asked",
                                        "engines_answered", "engines_irrelevant")), payload)
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
        code, payload = get("/ag/search?q=" + urllib.parse.quote(f"{QUERY} {i}"))
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
    print("\nag.search/1 over plain HTTP:")
    code, payload = get("/ag/search?q=" + urllib.parse.quote(QUERY) + "&n=1")
    check("GET /ag/search answers 200 with the contract", code == 200
             and payload["contract"] == "ag.search/1" and payload["count"] == 1, payload)
    code, payload = get("/ag/search?q=")
    check("an empty q gives a refusal with an explanation, not an empty result set",
             code == 502 and payload["ok"] is False, payload)
    code, payload = get("/no-such-path")
    check("an unknown path gives 404 with an explanation", code == 404, payload)

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
    code, payload = get("/healthz")
    check("a live metasearch gives 200", code == 200 and payload["ok"] is True, payload)
    check("the shallow check honestly says what it does not prove",
             "does not prove" in payload.get("note", ""), payload)
    code, payload = get("/healthz?deep=1")
    # `deep` asks for exactly one result: the check must prove that search runs to
    # the end, not collect a result set — extra results are pure cost here.
    check("the deep check performs a real search",
             code == 200 and payload.get("deep_count") == 1, payload)
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
                 not d["unresponsive_engines"], d["unresponsive_engines"])
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
        d = server.search_read("test", n=5)
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
        d = server.search_read("test", n=5, read=False)
        check("read:false restores the cheap path — not one read",
                 not read_calls and d["pages_read"] == 0, read_calls)
        check("the reading fields are returned under read:false too, they do not vanish",
                 "pages_read" in d and "timing_ms" in d, sorted(d)[:6])

        read_calls.clear()
        d = server.search_read("test", n=5, read_top=1)
        check("read_top sets the number of pages read",
                 len(read_calls[0]) == 1, read_calls[0])
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

    print("\n== the ok field is uniform across the contracts ==")
    # NEIGHBOURING TOOLS THAT CALL THE SAME THING BY DIFFERENT NAMES are a future
    # mistake by the caller. The rule is written down in ag.read.v1.md: without an
    # `ok` field on the screenshot, a caller with a shared `if not resp["ok"]`
    # would meet a refusal where all is well.
    answers = {
        "ag.search/1": server.search("test", 1),
        "ag.read/1": reader.read([]),
        "ag.images/1": server.image_search(""),
        "ag.shot/1": reader.screenshot("https://пример.рф/нет"),
    }
    for name, resp in answers.items():
        check(f"{name}: the ok field is present and boolean",
                 isinstance(resp.get("ok"), bool), (name, resp.get("ok", "NO SUCH FIELD")))
        check(f"{name}: contract is declared and matches",
                 resp.get("contract") == name, resp.get("contract"))
    # AN EMPTY RESULT IS A SUCCESS in every contract where it is possible: "we
    # looked and found nothing" and "we could not look" are different outcomes.
    check("ag.search/1: an empty result set is ok:true, not a refusal",
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
    d = server.image_search("ghost automotive", n=3)
    check("images: the contract is declared", d["contract"] == "ag.images/1", d.get("contract"))
    check("images: the full field set on the failure path too",
             set(server._images_refusal("x")) == set(d), 
             set(server._images_refusal("x")) ^ set(d))
    check("images: an empty query gives ok:false with a reason",
             server.image_search("")["ok"] is False)
    if d["results"]:
        r = d["results"][0]
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
