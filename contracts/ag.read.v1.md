# `ag.read/1` — the page-reading contract

> **Written from the code.** Every field name, value and limit below is taken
> from `adapter/reader.py` and `adapter/server.py`. If the service stops matching
> this document, the service has diverged.

Status: **implemented**. MCP tool `web_read`, plain door `GET /ag/read`.
Reference observation runs continuously; the view is `GET /pages`.

## Contents

What reading promises · the interface · the shape of the answer · the seven
outcomes · a scan with no text layer · what counts as success · the reference
attribute and why it differs from search · how it pairs with `web_search` · what
is deliberately absent.

## Reading is a separate tool, and search reads anyway

`web_search` reads the top three results by default and returns their text. That
is not a contradiction: **one call should answer a question**, and a tool that
returns links alone moves the second call onto the caller.

Three arguments once stood against reading inside search. All three are still
true and none of them blocks, because each is answered by the SHAPE OF THE ANSWER
rather than by refusing to read:

| the argument | how the shape answers it |
|---|---|
| the cost is invisible to the caller | `timing_ms` is split into search and reading; `pages_read` says how many pages were paid for |
| failures are of different natures | both stayed separate inside one answer: a link has `read_status`, an engine has its verdict in `engines_*` |
| the rate limiters are counted differently | that is internal and does not concern the caller |

**`web_read` is neither cancelled nor absorbed.** It remains the only way to read
a KNOWN address, take up to five addresses at once, walk a long document with a
cursor (`offset`), demand the browser path, and check a marker (`expect`). Search
reads three top links at 6000 characters each — enough for an answer, not enough
for working with a document.

The line between the three tools:

* `web_search` — ONE pass: what was found is what was read; the caller composes
  the answer;
* `web_read` — the text of a known address, with a cursor and a marker check;
* `web_deep_search` — composes its own queries, goes in waves, returns a DIGESTED
  ANSWER together with what it failed to find.

The first two return material; the third returns a judgement.

## Why reading is separate at all

**The costs are incomparable and invisible to the caller.** A search is 0.2-1 s
and one outbound request. A read is seconds and megabytes, and on the browser
path up to a minute and a half. An argument like `read_results: true` would make
the cost of a call unpredictable and hidden: the model asks to search and pays
for five downloads.

**Failures are of different natures.** Search fails per ENGINE, reading fails per
ADDRESS. Merged into one answer they produce a single place where "nothing was
found" and "we were not let in" look alike.

**The rate limiter is counted differently.** For search it is per engine: an
engine expects requests, that is its job. For reading it must be PER DOMAIN, or
five links from one publisher leave in a burst from a single address.

That last one is not theory. Some thirty requests to one site within half an hour
walk it down the ladder `200 → 429 → total silence`; the same address then
answers another machine in 0.22 s and ours not at all. **One link read without a
rate limiter takes a source away from every future read.** The address a module
calls from is its scarcest resource.

## Interface

The parallel with `web_search` is deliberate: neighbouring tools that call the
same thing by different names are a future mistake by the caller.

| `web_search` | `web_read` | what it does |
|---|---|---|
| `query` (required) | `urls` (required, 1..5) | addresses verbatim, with no normalisation on the caller's side |
| `max_results` 1..50, default 6 | `max_chars` 1000..200000, default 20000 | the content ceiling per address |
| `page` from zero | `offset` from zero, in characters | continuation of THE SAME read, not a new call |
| `min_engines` | `expect` | what must be present if this is the right thing |
| — | `mode` = `auto` \| `plain` \| `browser` | which path to fetch by |
| — | `format` = `markdown` \| `text` \| `html` | the form of the content |
| — | `links` | return the page's links |
| — | `fresh` | bypass the read cache (300 s) |

`urls` is an array rather than a single address: reading five sources of one
dossier is the ordinary case, and a partial result must be VISIBLE as partial.

The ceiling is five: five sequential addresses on the browser path already take
minutes, and a tool client does not wait that long. Addresses named beyond the
ceiling **do not disappear silently** — they come back with `status: not_reached`.

## The shape of the answer

Fields are ALWAYS present, empty ones included, on both paths. Otherwise the
consumer would have to branch on the SHAPE of the answer, and would one day fall
over doing it.

Top level: `contract`, `ok`, `error`, `requested`, `count`, `failed`, `results[]`,
`via_used`, `paths_available`, `all_pages_clean`, `deadline_hit`.

Per address:

* **identity** — `url` (as asked), `final_url` (after redirects), `domain`,
  `canonical`;
* **outcome** — `status`, `reason`, `http_status`, `content_type`;
* **content** — `content`, `chars`, `total_chars`, `truncated`, `offset`,
  `format`;
* **provenance** — `via` (`plain` \| `browser` \| `cache`), `read_at`,
  `elapsed_ms`;
* **metadata** — `title`, `published`, `lang`;
* **links** — `links[]` and `links_total`, ALWAYS both;
* **rejection** — `stub_check`, `stub_reason`;
* **reference** — `expected` (echo), `expected_found`, `robots`;
* **how the text was obtained** — `text_source` (`text_layer` \| `recognised` \|
  `not_recognised (scan)` \| empty) and `recognition` (recognised scans only).

About `links`: an empty list with `links_total: 57` means "you did not ask"; with
`links_total: 0` it means "there are none". One field for both would be a blind
spot.

### The seven outcomes

| `status` | what happened | what the caller should do |
|---|---|---|
| `read` | text obtained | use it |
| `empty` | we arrived, the page opened, there is no text | **do not retry**, take another source |
| `stub` | we arrived, but this is an anti-bot page, a paywall or a "no such page" page | do not retell the text — it is not from the page |
| `refused` | we were not let in: 401/403/429/5xx | retrying makes sense, so does another path |
| `unreachable` | network, timeout, DNS | retrying makes sense |
| `forbidden` | OUR guard: an internal address, a search-results page, an unsupported type, a PDF over the ceiling | the address will not do |
| `not_reached` | out of time, held by the per-domain rate limit, or beyond the batch ceiling | news ABOUT US, not about the page |

The split is not pedantry but the same rule as the three ways an engine fails:
"not asked" is about us, "asked and not let in" is about them. **If the second
kind suddenly multiplies, that is news about our address**, and it is worth more
than any single page.

The ground for a separate `stub` is a measurement. Of 141 HTML pages taken from
the module's own results, 40 — 28% — return fewer than 1200 characters to a plain
request, and **all forty** were a block, a JS application or an error; not one
real page. A tool that cannot tell those 28% from an empty page lies to its
caller in one read out of four.

### A scan with no text layer is recognised in the same call

A PDF whose pages are images has no text layer to extract. Answering `empty` with
the advice "recognition is needed" leaves the caller alone with that advice, so
the module recognises it itself: the pages are rendered and sent to a vision
model, and `status` becomes `read`.

**`text_source` is required reading.** `text_layer` was copied out of the file
and may be quoted verbatim. `recognised` is what a model believes is written, and
it must NOT be quoted as exact: over 17 pages checked against an independent
reference, the faster vision model recovered 159 of 170 numbers (94%) and the
stronger one 165 (97%). The caveat also travels in the answer, in
`recognition.fidelity`.

**Who pays for it: whoever asked for it.** Reading recognises because a call to
`web_read` on a document asked for the document to be read. Search does not: it
was asked for links with text, and a scan that landed in the top three is not a
reason to bill for a vision model. A scan met on the search path comes back as
`text_source: not_recognised (scan)` with a reason naming this call — the same
document read here IS recognised, and that refusal is never cached over reading.

`recognition` carries `model`, `dpi_requested`, `dpi_actual`, `dpi_steps_down`,
`pages_rendered`, `pages_total`, `image_format`, `payload_b64_bytes`,
`payload_over_limit`, `truncated`, `usage`.

**The three dpi fields are not decoration.** When pages do not fit a payload
ceiling the resolution steps down; if that happens silently, "the model read it
badly" and "we gave it 72 dpi instead of 300" are indistinguishable from outside.
The fields are returned WHENEVER a render happened — including when everything
after it broke.

**A recognition failure does not become `empty`.** No renderer, no key, a silent
model — the document is still not empty: it exists, it is a picture, and we did
not read it. The reason is named in words, including the name of the missing
environment variable.

## What counts as success

* **`ok: true`** — the work was done: every named address got a named outcome.
  Even if every outcome is a refusal.
* **`ok: false`** — the work was not done on OUR side of the seam: not one
  address was even attempted, or the module itself is broken. The full field set
  is returned then too.
* **Empty content is a success.** The page opened and has no text:
  `status: "empty"`, `chars: 0`, `ok: true`.

Implementations that count "no text found on the page" as an error are making a
different choice. Ours follows the rule that a probe returning zero must first
show that its subject existed: `http_status`, `content_type`, `chars` and
`stub_check` in the same answer show exactly that.

MCP `isError` stays `not ok`. The risk is named plainly: a model looking only at
`isError` would take "all five addresses failed" for a success. It is cured the
same way as in search — by the "how to read the answer" section of the tool
description, which is what the model actually sees.

## The reference attribute: a marker in the text

For search the reference is a canonical domain in the results. For reading that
signal does not work: it asks whether we ARRIVED at the right place, while for
reading the address is named in advance and the question is different — **was
what we were given the page**.

The four obvious signals were measured and all four lie:

| signal | how it lies |
|---|---|
| status code | a deliberately non-existent article path answers **200** five times out of six. Not even stable |
| title | the same non-existent page carries a `<title>` echoing the request |
| canonical | a 404 page declares itself the canonical address of itself |
| length | a legitimate page can be 167 characters; a stub can be 2990 characters of coherent text |

What remains is the one thing a stub does not forge by accident: **knowledge of
what specifically must be on the page.** It is applied at two levels.

**(a) Per call, on the caller's demand** — the `expect` argument. Set and found:
`expected_found: true`. Set and not found: `ok` does not change, but
`expected_found: false` and `all_pages_clean: false`. Not set:
`expected_found: null`, and cleanliness is judged by rejection alone.

**(b) Continuously, and this is what declares the tool ready** — the prober's
reference set, shown in `GET /pages`. Nine references, each covering its own
defect class:

| what it checks |
|---|
| the reader is alive at all; a short page is not a stub |
| encoding, and non-ASCII in the URL |
| a non-HTML type, truncation and continuation by `offset` |
| **is the browser path alive** — a plain request to that address gets 401 |
| **negative**: a page that certainly does not exist must come back as `stub` |
| a PDF of solid prose: parsing, and the relation "document ↔ its subtitle" |
| a two-column PDF with a dense results table: "method ↔ its number" |
| a PDF with font subsets: "object ↔ its property" |
| **negative for PDF**: a `.pdf` address that does not exist must not pass as read |

The negative references are the main difference from search references. A search
reference counts hits only; reading must have a check ON REJECTION, because what
we defend against is precisely "looks like a result" — and a stub detector nobody
checks degrades unnoticed, since it always says `clean`.

The browser reference is the only honest check of the browser: with a dead
sidecar that address returns `empty`, which without a reference is
indistinguishable from "the page has no text".

**Path state is not stated in this contract.** It lives in `GET /pages` and in
the `paths_available` field of every answer, and it is computed there. Writing it
into a document would create a line that becomes a lie on the day the path
changes, without giving any sign.

**A PDF reference checks a RELATION, not extraction.** Character accuracy and
structural accuracy are different quantities and they diverge: a parser can
extract every character correctly and still spread one specification across three
lines, so the labels come away from their values. A check of the form "text was
extracted" misses that by construction. So a reference requires two known
fragments to stand close TO EACH OTHER — once they come apart the structure is
lost, however many characters were extracted.

## How it pairs with `web_search`

Separate calls, separate tools. What binds them:

1. **Descriptions.** The "when not to call" section of `web_search` names
   `web_read` explicitly. Otherwise the pair exists in the code and does not
   exist for the model.
2. **A shared vocabulary of identity.** `domain` is computed by the same
   function, `url` is taken verbatim from the results, `final_url` shows where a
   redirect led.
3. **A one-way prohibition.** `web_read` REFUSES to read a search-results page —
   `status: forbidden`. Parsing somebody else's result page as "a page" merges
   the snippets of neighbouring results into one text and hands the caller
   "facts" about a namesake.
4. **One prober, two views.** `/engines` — which engines to trust; `/pages` —
   whether each reading path is alive. Same database, same process.
5. **`/tool-spec` returns every tool.** Otherwise a tool acquires two diverging
   descriptions.

## What is deliberately absent

**A timeout in the arguments.** Deadlines and pacing are module policy. The
caller says WHAT to read; how often and how long we are willing to do it is our
knowledge about our own address.

**Batch reading beyond five, actions in a browser, country and proxy selection,
schema-driven extraction by an LLM.** The last is the MODEL's work, and the
module has no model while the caller does: copying it would duplicate the caller.

**Parsing office formats** (DOCX, XLSX). We answer `forbidden` and name the type.

**PDF IS read.** The decision was made by measurement rather than preference: a
standard-library parser was written and gave something unreadable on four files
out of five — 41 characters instead of 52 858 on a document with font subsets.
`pypdf` is taken (3.6 MiB of pure Python); `fontTools` was checked and NOT taken —
it changed nothing on the hard file and weighs 27 MB.

PDF has three outcomes of its own over the general seven, and all three are
distinguishable:

| case | the answer |
|---|---|
| text extracted | `read`, plus `pages` and `pages_read` |
| pages present, no text | recognised in the same call; without a vision model, `empty` with the reason naming the missing variable |
| file over the ceiling | `forbidden`, with the word "ceiling". A truncated PDF makes the parser call the file broken, and the caller goes looking for another source instead of understanding that this one is good and merely large |

The page ceiling is 60: a thousand-page document takes minutes to parse while the
caller waits seconds. Unparsed pages do not disappear silently — `reason` says so.

**A `robots.txt` gate.** The `robots` field is in the answer: the verdict is
computed and reported. There is no prohibition, because whether a client's tool
must obey `robots.txt` is the operator's decision, not the module's. And the
common rule "401/403 on `robots.txt` means forbidden" produces a false ban on
these sources — the same sources answer 401 to everyone through an anti-bot
shield. **This is an open question, not a default we kept quiet about.**

**Bridges to somebody else's browser.** The browser stage is the module's own. A
build system's browser checks the layout of its own builds; ours reads other
people's pages. A shared sidecar would be a coupling with no cause, and docker
networks do not stretch between hosts.

The browser is a RARE ESCALATION, not the main path: a plain request covers
70-77% of addresses and the browser adds a few per cent — but exactly the ones
that are shut tight. Under `mode: auto` it is tried only when the plain request
gave `stub`, `empty` or `refused`, and its result is accepted only if it is
BIGGER: otherwise an honest `empty` would be replaced by the same `empty`, dearer.

**Dependencies.** HTML is parsed on the standard library's `html.parser`, and the
price is named honestly: tables come out as lines rather than tables, and
extraction quality is below a dedicated parser's. What that buys is one less
library to keep updated on the path where every outside page arrives.

PDFs are the opposite decision, and for the same reason — measurement. A parser
written on the standard library gave something unreadable on four files out of
five, so `pypdf` is taken; scans additionally need `pypdfium2` with `pillow` to
render pages to raster. With the browser client that is four libraries and a
419 MB image.
