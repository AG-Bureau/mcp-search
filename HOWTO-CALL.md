# Calling the module

> **Maintained by:** the modules layer · **Source:** this repository
> **Written from the code**, not from memory: every field name and every value
> below is taken from `adapter/server.py` and `adapter/reader.py`. If the service
> stops matching this document, the service has diverged, not the document.

Five capabilities behind three doors. This page is how to call them and — more
importantly — **how to read what comes back**, because in this module a failure
and a success often look alike unless you know which field separates them.

## Contents

Address · MCP door · HTTP door · views · the five tools · how to read a search
answer · the seven reading outcomes · deep search · limits · how to check it works

## Address

The module **publishes no port at all** by default. That is deliberate: a search
engine reachable from the internet is an open proxy that goes to the network in
the machine owner's name.

To reach it from the same machine, add the one overlay that ships:

```bash
docker compose -f docker-compose.yml -f wiring/expose-localhost.yml up -d
curl -s http://127.0.0.1:8081/healthz
```

That binds `127.0.0.1:8081` only. Exposing it more widely is a deliberate step
and needs a firewall rule of your own — see the header of the overlay file for
what to check first, because published docker ports bypass the usual firewall
chain.

`<host>` below means whatever address you bound.

## Door 1 — MCP, for tool clients

```
POST /mcp        JSON-RPC 2.0, protocol version 2024-11-05
```

Handshake:

```bash
curl -s -X POST http://<host>:8081/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2024-11-05","capabilities":{},
                 "clientInfo":{"name":"probe","version":"1"}}}'
```

```json
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2024-11-05",
  "serverInfo":{"name":"ag-mod-search","version":"1"},
  "capabilities":{"tools":{"listChanged":false}}}}
```

`tools/list` returns all five tools with their full descriptions. **Take the
description from there rather than writing your own** — it is the only thing the
model on the other end sees, and a second copy diverges at the first edit.

A tool call:

```bash
curl -s -X POST http://<host>:8081/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"web_search","arguments":{"query":"...","max_results":6}}}'
```

The result arrives as `result.content[0].text` — a JSON document, described
below. `result.isError` is true only when the TOOL did not do its work; an empty
result set is not an error.

Batches are supported and each call in a batch is counted separately.

## Door 2 — plain HTTP, for anything that speaks GET

```
GET /ag/search      q, n, page, read, read_top, min_engines, per_engine, corroborate
GET /ag/read        url (repeatable) or urls=a,b, max_chars, offset, format, links, expect, mode, fresh
GET /ag/images      q, n, page
GET /ag/screenshot  url, expect, full_page, max_chars
GET /ag/deep        q, waves
```

Arguments arrive as strings and are coerced inside; rubbish gives a named refusal
rather than a dropped connection. What is coerced rather than refused is listed
in `arguments_adjusted` — an empty list when everything arrived usable, never a
missing field. A number silently replaced by a default is an answer to a
different question than the one asked.

```bash
curl -s "http://<host>:8081/ag/search?q=example&n=3"
curl -s "http://<host>:8081/ag/read?url=https://example.com&max_chars=2000"
```

## Door 3 — views, for a person and for monitoring

```
GET /healthz        health of the CAPABILITY; ?deep=1 performs a real search
GET /engines        every engine with its reference hit share; ?category=images
GET /pages          every reading path with its reference share
GET /stats          call counters and model spend
GET /tool-spec      the tool definitions in a registry-friendly shape
```

`/healthz` goes to the metasearch instead of answering "ok, I am alive": a health
check that answers for itself lies exactly when it matters. It returns `ok:false`
when search is impossible. A dead **browser** does not drag `ok` down — it is
named in `degraded_paths` instead, because restarting this container does not
revive a sidecar.

## The five tools

| tool | what it is for | the cost |
|---|---|---|
| `web_search` | find pages AND read the top ones | one search plus 3 page loads |
| `web_read` | read pages whose addresses you already have | up to 5 pages |
| `web_image_search` | find images | one search |
| `web_screenshot` | a PNG of a page, plus its text from the same visit | a browser launch |
| `web_deep_search` | compose queries, read, and DIGEST AN ANSWER | a model and minutes |

`web_search` **reads by default**. The top three results are fetched and their
text comes back in the same answer in `content`. Set `read: false` when you only
want an overview — the call then costs a fraction of a second.

Arguments are in `tools/list`; the ones worth knowing:

* `min_engines: 3` — query at least three engines regardless of how many links
  were already collected. Use it when you need INDEPENDENT sources. The price is
  proportional: three engines means three times the outbound requests.
* `read_top: 1..8` — how many results to read. This is the main cost of a call.
* `expect: ["..."]` on `web_read` — markers that must occur in the text if this
  is the right page. Set them whenever the address came from a name search.
* `mode: "browser"` on `web_read` — force the browser path. Without a browser
  sidecar the call answers `not_reached`, not silence.

## How to read a search answer

Four things are easy to get wrong.

**1. An empty list is a SUCCESS.** `count: 0` with `ok: true` means we looked and
found nothing. A failure comes separately, with `ok: false` and a reason. Do not
repeat the query because the list was empty — repeat it rephrased.

**2. Three fates of a query, and they are different fields.**

| field | meaning |
|---|---|
| `engines_asked` | who was actually asked |
| `engines_answered` | who returned something |
| `engines_skipped` | **not asked**: the rate limit, or cooling after a refusal |
| `unresponsive_engines` | asked and stayed silent |
| `engines_irrelevant` | answered a different question — results discarded |
| `pool_source` | `computed` from observation, or `seed` — the starting list, because there were not enough observations to compute one |
| `search_aborted` | non-empty means the metasearch died MID-SWEEP |
| `engines_unasked` | who was missed because of that abort, and only them |

`engines_skipped` is not "asked and silent". Conflating the two is how a broken
pool looks healthy.

**3. Corroboration is not correctness — and by default there is nothing to
corroborate with.** The sweep stops at the first engine that gave enough links,
so one engine is one witness and `corroborated_by_url` is 1 by construction. Ask
for width: `min_engines: 3` (or `corroborate: true`) keeps asking, at several
times the outbound requests.

Where it does count, `corroborated_by_url` says how many independent engines
found THIS SAME link. On an ambiguous name the most corroboration goes to the
best-indexed namesake, not to the subject you asked about. One name can belong to
a retailer, a platform and two manufacturers at once, and all of them are real.

**4. `engines_trust` labels every engine that took part:**

| value | meaning |
|---|---|
| `clean` | checked against references, does not substitute the subject |
| `candidate` | no misses, but fewer than three observations |
| `substitutes` | has answered about a namesake |
| `unavailable` | probes were made and all refused — news about YOUR address |
| `not_checked` | no probes |

`all_engines_clean: false` means the results are worth verifying against features
of the subject: an engine may have answered about a different company of the same
name.

## The seven reading outcomes

Every address in `web_read` gets its own `status`. They are seven, and they are
not interchangeable:

| `status` | what happened | what to do |
|---|---|---|
| `read` | text obtained | use it |
| `empty` | the page opened and has no text | **do not retry**, take another source |
| `stub` | an anti-bot shield, a paywall, or a "no such page" page | do not retell the text — it is not from the page |
| `refused` | we were not let in: 401/403/429/5xx | retrying makes sense, so does another path |
| `unreachable` | network, timeout, DNS | retrying makes sense |
| `forbidden` | OUR guard: an internal address, a search-results page, an unsupported type, a PDF over the ceiling | the address will not do |
| `not_reached` | out of time, held by the per-domain rate limit, or beyond the batch ceiling | news about US, not about the page |

Two more fields decide whether the text can be trusted:

* `stub_check` — `clean` · `looks_like_stub` · `not_checked`. **`not_checked` is
  not `clean`.**
* `text_source` — `text_layer` · `recognised` · `not_recognised (scan)` · empty.
  The last one appears in SEARCH results: a scanned PDF among the top links is not
  recognised there, because nobody asked search to pay for a vision model. Call
  `web_read` on that address and it will be. **`recognised` is required
  reading**: that is a scanned PDF read by a vision model, and such text must not
  be quoted as exact — a measurement recovered 94% of the reference numbers. The
  `recognition` field carries the dpi and whether the model's answer was cut off.

`ok` belongs to the TOOL, not to the pages: it stays true even if not one page
was read. Look at `count`, `failed`, and each address's `status`.

## Deep search

`web_deep_search` is the only tool that returns a judgement rather than material.
It composes its own queries, goes in waves, reads, and digests an answer.

**The main field is `outcome`, not `answer`:**

| `outcome` | meaning |
|---|---|
| `found` | the markers met on a page |
| `ambiguous` | SEVERAL DIFFERENT subjects under one name — listed in `ambiguity.variants` |
| `off_target` | material found, but about ANOTHER subject |
| `not_found` | no sources |
| `unknown` | there were no markers, so there was nothing to check with |

On `ambiguous` the answer applies to the LARGEST group only; the other variants
are real. On `off_target` the answer looks convincing and is about the wrong
thing.

Also worth reading: `summarised_from_on_target` (false means treat the answer as
a draft), `stopped_because` and `waves_done` (a full answer and a short one look
alike otherwise), the three source counts (`sources_total` ·
`sources_with_content` · `sources_on_target` — the answer stands on the third and
sounds weighty because of the first), and `conflicts` (the sources give different
values for one fact — an announcement and a release are different events).

Answers carry a first-line warning when verification did not run, when the digest
came from off-target pages, when the sources disagree, or when the model's answer
was cut off. The warning is on the first line because a note at the end of a long
answer is not read by anyone who took the first paragraph.

## Limits

| limit | value | why |
|---|---|---|
| addresses per `web_read` call | 5 | five sequential reads are already minutes |
| characters per address | 1 000 … 200 000 | the remainder is fetched with `offset` |
| whole read call | 90 s | past it, `not_reached` rather than a hang |
| per-domain pacing | one request per 2 s | a burnt source does not come back |
| per-engine pacing | one request per second | the engines' willingness is the scarce resource |
| MCP response ceiling | 400 000 characters | content is cut BEFORE serialisation, so the JSON stays parseable |

Addresses beyond the batch ceiling are returned with `not_reached` — they never
disappear silently.

## How to check it works

```bash
# the capability, not the process
curl -s http://<host>:8081/healthz | python3 -m json.tool

# a real search end to end (pays with one outbound request)
curl -s 'http://<host>:8081/healthz?deep=1' | python3 -m json.tool

# which engines are actually alive, by reference hit share
curl -s http://<host>:8081/engines | python3 -m json.tool

# which reading paths are alive
curl -s http://<host>:8081/pages | python3 -m json.tool
```

`/engines` is the honest answer to "is search working". `reference` is the share
of probes where an engine found a KNOWN-CORRECT answer; `ok_share` only says the
engine answered at all, and spam that quotes the query back passes that.
