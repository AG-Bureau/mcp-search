# Calling the module

> **Maintained by:** the modules layer · **Source:** this repository
> **Written from the code**, not from memory: every field name and every value
> below is taken from `adapter/server.py` and `adapter/reader.py`. If the service
> stops matching this document, the service has diverged, not the document.

Five capabilities behind three doors — and the first of them opens two ways, over
HTTP and over stdio. This page is how to call them and — more
importantly — **how to read what comes back**, because in this module a failure
and a success often look alike unless you know which field separates them.

## Contents

Address · MCP door, over HTTP and over stdio · HTTP door · views · the five tools
· coming from 0.2.x · how to read a search answer · the seven reading outcomes ·
deep search · limits · how to check it works

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
  "serverInfo":{"name":"ag-mod-search","version":"0.3.0"},
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

### The same door over stdio, for clients that START the server

MCP has two transports, and stdio is the default one: the client runs the server
as a PROCESS and talks to it through the pipes. Desktop clients and most wrappers
work that way; HTTP is for the case where the server is already running
somewhere.

```bash
python adapter/server.py --stdio        # or MCP_TRANSPORT=stdio
```

One JSON-RPC object per line in, one answer per line out. Notifications get no
line back, batches are a JSON array on one line, and a line that is not JSON is
answered with `-32700` rather than swallowed.

**In this mode stdout IS the protocol.** Everything that is not an answer —
the start-up line, engine-registry diagnostics, trust-database warnings — goes to
stderr. Read the log there; a client that reads stdout must see nothing but
messages.

The transport is chosen explicitly and never guessed from whether a terminal is
attached: that sign merely sits next to the subject and would one day answer for
a case nobody meant.

**What stdio does not change: the sidecars.** The browser and the prober are
separate processes reached over the network. Started by a client with no compose
project around it, the module works — and says so honestly: the browser path
reports `not_wired_up`, and `trouble.pool_unmeasured` says the engine pool was
never computed from observation, because there is nothing to compute it from.

## Door 2 — plain HTTP, for anything that speaks GET

```
GET /ag/search      q, n, page, read, read_top, min_engines, per_engine, corroborate
GET /ag/read        url (repeatable) or urls=a,b, max_chars, offset, format, links, expect, mode, fresh
GET /ag/images      q, n, page
GET /ag/screenshot  url, expect, full_page, max_chars
GET /ag/deep        q, waves
```

Arguments arrive as strings and are coerced inside; rubbish gives a named refusal
rather than a dropped connection.

**The status code says WHOSE mistake it was.** `4xx` — yours: a required argument
missing, an address we will not read. `5xx` — ours or the metasearch's: retrying
is sensible for the second and pointless for the first. An argument this door
does not know is not a refusal at all — the call proceeds and the name is
reported in `arguments_adjusted`, because the two doors spell the same thing
differently (`q`/`n` here, `query`/`max_results` over MCP).

**Booleans are parsed, not cast, and both doors parse them identically.**
Understood: `true/false`, `1/0`, `yes/no`, `on/off`, `y/n`, `t/f` and Python's
`True/False` — case does not matter. Anything else, including `null`, an empty
value and a bare `?flag=`, counts as unreadable. **A value that cannot be read turns the flag OFF** and is
named in `arguments_adjusted`: every flag here buys something expensive when on,
so a typo must not be billed at eight times the price. `read="maybe"` therefore
does not read, and says why.

**An EMPTY value is not an absent one.** An argument nobody passed keeps the
documented default and is not reported. `read=""` — or `?read=` on the plain
door — is a value we could not read: the flag falls off and the answer says so.
The two used to collapse into "use the default", in the one place where the
default is the expensive side. What is coerced rather than refused is listed
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
GET /tool-spec      the tool definitions twice: `mcp` is the standard MCP list,
                    `engine_registry` a convenience rendering for orchestrators
                    whose registry wants a flat argument map and a per-tool
                    observation ceiling. Use the first unless you need the second.
```

`/healthz` goes to the metasearch instead of answering "ok, I am alive": a health
check that answers for itself lies exactly when it matters. It returns `ok:false`
when search is impossible. A dead **browser** does not drag `ok` down — it is
named in `degraded_paths` instead, because restarting this container does not
revive a sidecar.

### `/stats` splits the calls by WHO SAYS they made them

With several clients on one instance, "a spike of refusals" is visible while
"whose spike" is not. The protocol already carries the answer: a client sends
`clientInfo` in `initialize`, and `by_client_says` counts the calls under that
name.

**The name is a claim, not a fact** — hence `says`. Anyone can call themselves
anything, and nothing here verifies it. It answers "which of my callers behaves
oddly", never "is this caller who they claim".

Three absences are kept apart, because one bucket would hide which of them you
are looking at:

| value | what it means |
|---|---|
| `not_introduced` | an MCP call we could not link to any handshake |
| `introduced_without_name` | a handshake arrived, `clientInfo` did not |
| `plain_door` | a call at `GET /ag/...`, where the protocol has no handshake |

**What links a call to a handshake differs by transport, and this is worth
knowing before reading the numbers.** Over stdio a session is the PROCESS: the
handshake comes once and holds for everything after it. Over HTTP a session is
the CONNECTION: with keep-alive the calls after `initialize` are linked, but a
client that opens a fresh connection per call cannot be linked to anything — such
calls are counted as `not_introduced` rather than attributed by guesswork to
whoever spoke last. A high `not_introduced` count therefore says something about
the client's connection handling, not about the client's honesty.

## The five tools

| tool | what it is for | the cost |
|---|---|---|
| `web_search` | find pages AND read the top ones | one search plus 3 page loads |
| `web_read` | read pages whose addresses you already have | up to 5 pages |
| `web_image_search` | find images | one search |
| `web_screenshot` | a PNG of a page, plus its text from the same visit | a browser launch |
| `web_deep_search` | compose queries, read, and DIGEST AN ANSWER | a model and minutes |

### The one choice to make before calling, and what it costs

`web_search` **reads by default**: the top three results are fetched and their
text comes back in the same answer in `content`. That is the cost of the call,
and it is not small. Measured by a consumer on a live door, same query:

| call | what comes back | wall clock |
|---|---|---|
| default (`read: true`) | ~12 400 characters | 4.8-10.5 s |
| `read: false` | ~2 800 characters | ~0.7 s |

**Both numbers are right for somebody.** If you need the text of the top results,
one call has already brought it and a second call would cost more. If you are
mapping WHAT EXISTS on a question, or working under a narrow context ceiling,
that text is spent on pages you will discard — set `read: false` and fetch what
you actually want with `web_read`.

The default stays as it is deliberately: for a caller that wants the answer, it
is the cheaper of the two. What was wrong was not the default but that the fork
was invisible until the bill arrived — a consumer paid for reading it was
discarding for four months without seeing the choice.

`read_top` sets how many pages are read. **`read_top: 0` means "no preference"**
— the default of three — and NOT "read nothing"; for nothing, use `read: false`.

Arguments are in `tools/list`; the ones worth knowing:

* `min_engines: 3` — query at least three engines regardless of how many links
  were already collected. Use it when you need INDEPENDENT sources. The price is
  proportional: three engines means three times the outbound requests.
* `read_top: 0..8` — how many results to read; the main cost of a call. Zero
  means "you decide" and gives the default three.
* `verbose: true` — adds the accounting to the answer: timings, pages read, the
  engines skipped and why, the echo of the arguments. Off by default, because it
  explains the call rather than changing what you do with it. What went WRONG is
  in `trouble` either way.
* `expect: ["..."]` on `web_read` — markers that must occur in the text if this
  is the right page. Set them whenever the address came from a name search.
* `mode: "browser"` on `web_read` — force the browser path. Without a browser
  sidecar the call answers `not_reached`, not silence.

## Coming from 0.2.x? Read this first

The search and image answers changed SHAPE in 0.3, and the contract name says so:
`ag.search/3`, `ag.images/3`. Four fields you may be reading today —
`search_aborted`, `unresponsive_engines`, `engines_irrelevant` and a `seed`
`pool_source` — now live inside `trouble`, and the accounting (`count`,
`timing_ms`, `pages_*`, `engines_skipped`, `engines_used`, `tiers_used`, the echo
of the arguments) comes back with `verbose: true`.

`if not trouble` replaces the four separate checks. The full table, and the older
history of value renames, is in the contract:
[`contracts/ag.search.v3.md`](contracts/ag.search.v3.md).

Booleans are now parsed rather than cast — see the note under the plain door —
and `read_top: 0` means "no preference", not "read nothing".

## How to read a search answer

Five things are easy to get wrong.

**0. The default answer is nine fields, and one of them is `trouble`.** Empty
`trouble` means "checked, nothing wrong" — `if not trouble` is the whole good
case. The accounting that merely explains the call comes with `verbose: true`.

**1. An empty list is a SUCCESS.** An empty `results` with `ok: true` means we
looked and found nothing. A failure comes separately, with `ok: false` and a
reason. Do not repeat the query because the list was empty — repeat it rephrased.

**2. What is inside `trouble`, and why each key is a different piece of news.**

| key | meaning |
|---|---|
| `arguments_adjusted` | an argument was unusable, so we answered a slightly different question — including a boolean we could not read, which turns its flag off |
| `search_aborted` | the metasearch died MID-SWEEP; the results are incomplete by no decision of ours |
| `engines_unasked` | who was missed because of that abort, and only them |
| `unresponsive_engines` | asked and stayed silent |
| `engines_irrelevant` | answered a different question — their results are already discarded |
| `pool_unmeasured` | the engine pool is the seed list, not computed from probes |

A key that is absent means that particular thing did not happen. `trouble` itself
is never absent: an empty dictionary is "we looked and all is well", while a
missing field would mean "this was never examined", and the two must not share a
shape.

Under `verbose: true` the same news is also available raw, beside its neighbours:
`engines_asked`, `engines_answered`, `engines_skipped` (**not asked** — the rate
limit, or cooling after a refusal), `pool_source` (`observation` or `seed`),
`timing_ms`, `pages_read` and the rest.

`engines_skipped` is not "asked and silent". Conflating the two is how a broken
pool looks healthy — and it is not `trouble`: an engine skipped by pacing means
the queue worked and somebody else was asked.

**3. Corroboration is not correctness — and by default there is nothing to
corroborate with.** The sweep stops at the first engine that gave enough links,
so one engine is one witness and `corroborated_by_url` is 1 by construction. Ask
for width: `min_engines: 3` (or `corroborate: true`) keeps asking, at several
times the outbound requests.

**With one witness the two fields are not returned at all** — neither as `1` nor
as `0`. There the number would mean "nobody else was asked", which is a different
thing from "one engine of three found it", and the two would be indistinguishable.
Ask for width and they come back.

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

### Fields you will meet that are not covered above

| field | where | what it says |
|---|---|---|
| `query_words_here` | each search result | which words of YOUR query this result actually contains. Evidence about this answer — the trust label is about the engine's history, the pool about the instance |
| `query_words_matched_nowhere` | search `trouble` | words of your query that appear in NO result. Not a verdict: a missing word can be a synonym or a translation. It catches the case this module exists for — the subject quietly replaced by a better-indexed neighbour |
| `download_truncated` | each read page | the page exceeded the download ceiling and was cut there. When true, `total_chars` is ABSENT — its size is unknown — and `total_chars_at_least` carries the floor |
| `cache_age_s` | each read page | how old the copy is, in seconds. `read_at` is when the page was READ, not when it was handed to you; `via: cache` says it is a copy, this says how stale |
| `page_text_truncated` | screenshot | the page text was cut at `max_chars`. `page_text_chars` is the WHOLE length |
| `width`, `height` | screenshot | the image's pixel size, read from the PNG header. Null only when no shot was taken |
| `browser_version` | screenshot, and a read that went through the browser | which chromium answered. It must match the client pinned in the adapter, or the connection breaks silently |
| `input_tokens`, `output_tokens` | deep search `usage` | what the model was paid for. `cost_usd` is null unless you supplied prices |
| `ledger_note` | deep search `usage` | why the spend ledger says what it says — for example that a line could not be written |

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

* `robots` — `allowed` · `disallowed_by_site` · `not_checked`. **The module
  reports the site's rules and does NOT obey them**: a forbidden page is fetched
  and the field says so. Obeying is the operator's decision, and the gate is
  yours to add — see the README section on `robots.txt`. If you add it, stop on
  `not_checked` as well: it means the rules could not be read.
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
| `not_found` | we looked and no source came back non-empty |
| `not_attempted` | the search never ran — no model configured, or an empty question. Not the same as finding nothing |
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
