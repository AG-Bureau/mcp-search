# search — web search as a module

Five capabilities behind one MCP server: **find** (`web_search`), **read**
(`web_read`), **find images** (`web_image_search`), **screenshot a page**
(`web_screenshot`), and **answer a question from several sources**
(`web_deep_search`).

The module keeps no index of its own. It queries other people's search engines
through a metasearch container, and everything it adds is about one problem:

> **A search tool fails in ways that look exactly like success.** An engine
> answers with somebody else's subject; a page returns text that is an anti-bot
> shield; a corpus of sixteen sources turns out to be two engines counted eight
> times. This module's job is to make those cases *distinguishable*, and to say
> so in fields you can branch on.

**Search reads by default.** The top three results come back with their text in
`content`, so one call answers a question instead of handing over links. The
cheap path is still there: `read: false` returns links alone in a fraction of a
second.

Three of the tools are easy to confuse, so the line is drawn explicitly:
`web_search` and `web_read` return **material**; `web_deep_search` returns a
**judgement** — it composes its own queries, goes in waves, and says what it
failed to find.

## Where to go next

* **[HOWTO-CALL.md](HOWTO-CALL.md)** — how to call it and, more importantly, how
  to read what comes back. Start here.
* **[ALGORITHM.md](ALGORITHM.md)** — what happens, step by step, when a request
  arrives.
* **[manifest.yaml](manifest.yaml)** — the machine-readable description.
* **contracts/** — one contract per capability: what is promised, what is not,
  and what the failure directions are.
* **[measures/](measures/)** — the numbers: which engines are alive, what the
  module withstands under load, what changing transport bought.

## Install

From an open repository page to a working answer. Nothing is assumed to be on
your disk already:

```bash
git clone https://github.com/AG-Bureau/mcp-search
cd mcp-search
cp .env.example .env
echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> .env
docker compose -f docker-compose.yml -f wiring/expose-localhost.yml up -d --build
curl -s http://127.0.0.1:8081/healthz
curl -s -X POST http://127.0.0.1:8081/mcp \
     -H 'Content-Type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The fourth line is not decoration. Without a value in `SEARXNG_SECRET` the very
next command refuses — `required variable SEARXNG_SECRET is missing a value` —
and that refusal is deliberate (see below). `openssl rand -hex 32` is one way to
satisfy it; any long random string that is not taken from somebody's history
will do.

**`SEARXNG_SECRET` is mandatory and `up` refuses without it.** That is
deliberate: with no key of its own the metasearch does not fail — it comes up
with a publicly known one from its image template, silently.

**A model key is NOT mandatory.** Search, image search and screenshots never
call a model; reading calls one on a single branch — a PDF with no text layer,
which has to be recognised. Without a key that branch and deep search declare
themselves unconfigured and name the missing variable rather than returning a
quiet nothing.

**The protocol is OpenAI-compatible, and it is verified on GLM.** Providers
differ in details beyond the common part, so the differences are carried in
variables rather than in code — `LLM_API_BASE`, the two model names, and
`LLM_DISABLE_THINKING`. If your provider refuses a request, look there first: the
dialect field is not sent by default precisely because a strict provider answers
`400` to it.

**Set `LLM_API_BASE` in full, and verify it.** The obvious guess at a provider's
address can answer `429: Insufficient balance` because the subscription lives on
a different path of the same domain. The module behaves correctly — it returns
the provider's answer verbatim — but from outside that reads as a broken product.
Take the address from your provider's documentation, not by analogy.

**`--build` is in the command on purpose.** The services carry both `build:` and
`image:`; with an image of the same tag already present, compose reuses it and
builds nothing. On a clean machine there is no image, but the flag keeps an
update from raising yesterday's build.

The overlay publishes the port **on loopback only**, and it is the only
publishing overlay that ships. Publishing more widely is two lines of your own,
and the checks to run before them are listed in
`wiring/expose-localhost.yml` — a published container port does not go through
the host firewall's usual chain, so an empty `DOCKER-USER` means the firewall
does not apply to it at all while still reporting that it does.

## Cost, and the knobs that control it

**Three capabilities out of five never call a model.** Search, image search and
screenshots are HTTP requests: they spend no tokens at all. Reading spends tokens
on ONE branch — a PDF with no text layer, recognised by a vision model — and is
free on every other. The fifth, `web_deep_search`, always calls a model. Without
a key the two model-dependent paths declare themselves unconfigured rather than
failing.

That is the difference from a search built into a model. A built-in tool has no
knobs: it always works in one mode, always returns its own volume of text into
the context, and that text is always paid for in tokens.

Here the volume is an argument. One and the same query, measured on one machine:

| call | returned | time | model calls |
|---|---|---|---|
| `read: false`, `max_results: 6` | 3 844 characters | 0.6 s | 0 |
| `read_top: 1`, `max_results: 3` | 8 833 characters | 1.6 s | 0 |
| `read_top: 3`, `max_results: 6` | 9 805 characters | 6.3 s | 0 |
| `web_deep_search` | a full digest | 36 s | 6 |

A single argument moves the volume by an order of magnitude and the time by a
factor of sixty. The knobs are `read`, `read_top`, `max_results`, `min_engines`
and `per_engine`; `min_engines` is the one that buys independence and the one
that multiplies outbound requests proportionally.

*(One machine, one query. Take the shape, not the digits.)*

**And the honest other half.** On a well-covered question the module is SLOWER
than a search built into a model and returns the same answer. Measured on a
question about a large company's annual revenue: 36 seconds for a deep search
against seconds for a built-in one, and the same figure in both.

The gain begins where the answer is not on the surface, or where you need to know
what to trust: which engine found a link, whether the sources agree, whether the
page was a page or an anti-bot wall, whether one name covers several different
subjects. A built-in search answers the question; this one also answers how much
the answer is worth.

## What it is made of

**A metasearch container** — somebody else's open service. It holds the result
parsers for dozens of engines, maintained by their community rather than by us.
That is exactly why it is here: our own parsers would need repairing after every
redesign somebody else ships.

**The adapter** — our process, and the module proper: from outside, only this is
visible. It holds the doors over ONE implementation of each capability — MCP for
tool clients, plain HTTP contracts, a health check for orchestration, and the
observation views `/engines` and `/pages`. A second implementation "for another
protocol" would diverge from the first at the first edit.

**The reader** — part of the adapter, in a file of its own, because the reading
policy (per-domain pacing, stub rejection, the chunked cursor) belongs beside the
measurements that explain it. HTML is parsed on the standard library: the price
is named honestly — tables come out as lines rather than tables — and in exchange
there is one less library to update on the path where every outside page arrives.
PDFs are another matter: a stdlib parser was written, measured and rejected, so
the image carries four libraries and 419 MB, each argued for in
`adapter/Dockerfile`.

**The prober** — our process. Continuously, one engine at a time, it asks every
engine the metasearch knows and writes a verdict to a database. It exists because
the set of living engines changes within hours, and a point measurement cannot
see that: an engine that was the best of the set returns nothing weeks later
without saying a word about it.

The same prober measures **reading references** — nine pages whose content is
known in advance, each covering its own defect class. Two of them are
**negative**: addresses that certainly do not exist, from which the expected
verdict is `stub`. Without them the shield detector would degrade unnoticed,
because it always says `clean`.

## How engines are chosen

**One engine at a time, not a fan-out.** The order is fixed; we walk it top down
and stop as soon as there is enough. If an engine refuses, the next one goes —
switching is not a separate mechanism but a property of the order. A fan-out
costs several times the requests to other people's services and brings a block
closer, while adding almost nothing on an ordinary query: different engines
overlap heavily on the same question.

**At most one request per engine per interval.** The metasearch has no rate
regulator at all, so the module holds one. An engine that may not be asked right
now is skipped rather than waited for. If nobody may be asked, the answer is a
refusal with a reason — "we asked nobody" must be distinguishable from "nothing
was found".

**The pool is COMPUTED from observation, not written by hand.** This is the main
rule here. A hand-written list of engines needs revising as often as the engines
change — three times in one day is not unusual, each revision against the
previous one and each correct on the data available. The problem is neither the
engines nor the quality of the decisions: a decision freezes while observation
goes on.

So the pool is the best-N by reference hit share, recomputed continuously.
**No entry threshold, no exit threshold, no floor**: a threshold is a number
somebody assigns which then freezes — the same disease in another place. The pool
cannot empty out by construction, because it is always full.

Two limiters remain, and both are about noise rather than quality: an engine with
two lucky probes does not displace one proven over a hundred and twenty, and a
swap happens only when a candidate is clearly better — otherwise the pool would
twitch on hundredths.

**When there is not enough to compute from, the pool is not invented.** The
starting list — the seed — is returned instead, and the answer says so:
`pool_source: seed` with the reason in words, against `computed`. A fresh install
and an install whose observations were lost look identical from outside unless
this is said out loud. The image pool runs on its seed to this day: its
references have not been written yet.

**One family takes at most two slots.** A family is a set of engines sharing an
index; families are computed too, not declared. Without the cap the pool fills
with shopfronts of a single source: excellent by share, and not one independent
witness among them. A cap rather than a ban — quality comes first, diversity is
the limiter.

**Leaving and returning happen by themselves.** Verified by falsification: a
planted bad run takes an engine out of the pool with no code change, and
restoring the run brings it back. The snapshot must name who displaced whom and
WHY — by share or by the family cap — because an engine can leave with an
excellent share, and without the reason beside it that reads as a broken
mechanism.

**An engine disabled in the metasearch settings still answers** when named
explicitly. The `disabled` flag is shown beside the hit share as a reason to
look, never as grounds to exclude: dozens of engines ship disabled, including
ones that score 0.98 here. Only observation excludes.

**An unknown name is never sent.** The metasearch does not answer an unknown
engine name with an error — it silently queries the whole category, dozens of
engines instead of one, and nothing in the answer shows it. So names are checked
against its own registry before sending.

**An engine with no index of its own is not worth adding.** Many public "search
engines" are shopfronts over two or three indexes. Adding them adds NAMES, not
sources: measured, one such shopfront had 95-100% of its links inside another
engine's index. A shopfront inflates the witness count without adding
independence, and does it invisibly — the answer carries different names and
`corroborated_by_url` grows an honest-looking number.

## Telling a bad result set from a good one

An engine fails in **four** ways, and three of them give no sign. All four are
visible in the answer, empty lists included — an empty list is information too.

| how it fails | what shows it |
|---|---|
| answers with a refusal: captcha, rate limit, ban | `unresponsive_engines` |
| silently returns nothing | the difference between `engines_asked` and `engines_answered` |
| answers a different question | `engines_irrelevant` — its results are already discarded |
| answers THE question about ANOTHER subject | `engines_trust` |

The last is the most dangerous and the least visible. The results look real: live
links, the query words present, texts on topic. It is simply a different company
of the same name, or a different "official site". A check against the words of
the query passes it every time — the words did match.

**It is caught by a reference** — a query whose correct answer is known in
advance and confirmed outside the engines. The prober runs the references in a
loop, and by hitting them an engine earns a label: `clean`, `candidate`,
`substitutes`, `unavailable`, `not_checked`. The label travels in every answer,
because it changes what a consumer should do.

**The label has a chosen failure direction.** No data means `not_checked`, never
`clean`: a broken observation switches verification ON at the consumer, not off.

## Telling a page that was read from a shield

Reading has the same defect class as search, and a nastier one: **the page
arrives, there is text in it, and a single answer does not show that the text is
an anti-bot page rather than content.** Of 141 HTML pages taken from our own
results, 40 — one in four — returned fewer than 1200 characters, and ALL FORTY
were a block, a JS application or an error. Not one real page.

The four obvious signals were measured and all four lie: the status code (a
non-existent page answers 200 five times out of six), the title (it echoes the
request), the canonical link (an error page declares itself the address sought),
and the length (a legitimate page of 167 characters against a stub of 2990).

So **the reference attribute for reading is a marker in the text**: knowledge of
what specifically must be on the page. A caller sets it per call with `expect`;
the prober measures it continuously against its references, and `/pages` shows
whether each fetch path is alive.

Reading has **seven outcomes**, separated by what to do next: retrying is
pointless on `empty` and sensible on `refused`; `not_reached` is news about us,
`refused` is news about them. The full table is in
[HOWTO-CALL.md](HOWTO-CALL.md).

## Deployment and exposure

A compose project of its own, self-contained: it creates its own network and
depends on nothing external.

Only the adapter is ever exposed, and only together with a firewall rule limiting
the source. The metasearch stays internal: reachable from outside, it becomes an
open proxy that goes to the network in the machine owner's name.

**A published container port is NOT protected by the host firewall's usual
rules** — that traffic bypasses them. It needs an explicit rule in the chain that
container traffic passes through; without one the port is open to everyone while
the firewall keeps reporting otherwise. The command to check this is in the
exposing overlay.

## Secret

The metasearch signing key comes from the environment; it is not in the
repository. The requirement is enforced by the compose file rather than by the
metasearch, which without the variable does not fail but comes up with a publicly
known key from its image template. A "fix" consisting only of removing the secret
from a file would therefore produce a system that is worse than before and looks
repaired.

## Tests

Three suites ship: the protocol and search against a fake metasearch, reading
against a fake site, and the computed pool against a database built in memory.

```bash
IMAGE=ag-mod-search/adapter:1 bash tests/in-image.sh
```

**Not one of them makes a single outbound request.** For reading that matters
more than for search: a test that went to the internet would spend the very
resource the tool protects — the reputation of the single address it calls from.

They run **inside the module image**, not on the machine where the code is
edited: the PDF parser lives in the image, and a suite run outside would skip
everything that touches it. The skip would not be silent — the PDF check goes red
with a note saying where to run it — but a green run that checked nothing is
exactly the defect these tests look for.

What these suites cannot check is in [tests/README.md](tests/README.md).
