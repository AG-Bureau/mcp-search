# What happens when a request arrives

> **Written from the code.** The steps below are the code paths in
> `adapter/server.py`, `adapter/reader.py` and `adapter/deep.py`, in order. If
> the service stops matching this document, the service has diverged.

This page exists because an algorithm that lives only in the source cannot answer
questions like "what stops thirty concurrent requests from spamming the engines".

## Contents

What the caller receives · a search, step by step · a page read, step by step ·
the escalation ladder · images and screenshots · who pays for recognition · a
deep search · where the engine pool comes from · the queue under concurrency ·
what multiplies outbound requests · what the algorithm does NOT guarantee.

## What the caller receives, and what only explains the call

The answer carries what a caller ACTS on: `results`, who was asked and who
answered, the trust labels, and `trouble`. Everything that explains the call
rather than changing what to do with it — timings, page counts, echoes of the
arguments, the per-engine breakdown — comes with `verbose: true`.

`trouble` is always present and empty when nothing went wrong, so `if not
trouble` is the whole good case. What enters it is only news that makes the
answer less than it looks: an aborted sweep and who was missed by it, engines
that stayed silent, engines discarded as off topic, a pool that was never
measured, and arguments we had to adjust.

The ground is a measurement taken ON US by a consumer: twenty-five top-level
fields, seven ever read, one branched on. The cure was not deletion — four of
those fields are why this module exists — but collapsing them into one field a
caller can branch on by its emptiness.

## A search, step by step

**1. Parse the input.** An empty query is refused at once. The result count is
clamped into range, the page number to non-negative. Rubbish neither breaks the
search nor passes silently: it is replaced by the default, and every such
substitution is NAMED — `n='many' is not a number, using 6`, `page=99999 clamped
to 10000` — and travels out in `trouble.arguments_adjusted`. Silent coercion
answers a question other than the one asked, and the caller reads the answer as
an answer to theirs.

**Booleans are parsed by the same function at both doors, and that shared parse
is the point.** They used to be cast with `bool(...)`, where every non-empty
string is true: over MCP `"read": "false"` READ the pages — eight times the wall
clock, seven times the payload — while the plain door understood the same word
correctly. One argument name behaving in opposite ways at two entrances is
something no caller can be expected to guess.

A value that cannot be read turns its flag OFF rather than falling back to the
documented default. That is a decision about cost: every flag here buys something
expensive when on, and a caller who typed `read=off` should not be billed
eightfold for a misunderstanding. The substitution is named, so the mistake is
visible rather than merely cheap.

`read_top: 0` is NOT such a case: zero means "no preference" and gives the
default.

**An argument this door does not have is named too.** The two doors spell the
same thing differently — `query`/`max_results` over MCP, `q`/`n` on the plain
door, because a URL is written by hand and a JSON object by a machine. A caller
who brings one door's spelling to the other used to get an answer to a different
question in silence; now the name is reported, with the spelling this door
actually takes. A zero standing for "not asked" beside a zero that reads as "none at
all" is precisely the confusion this module is written against, so the schema
declares the range it really accepts and the meaning is written into the tool
description rather than inferred.

**2. Check the pool against the metasearch registry.** Engine names are compared
with what the metasearch actually knows; unknown names are NOT sent and are named
in the log. An unknown name does not produce an error there — it silently queries
the whole category, dozens of engines instead of one, and nothing in the answer
shows it.

**2a. Where the pool came from is part of the answer.** `pool_source` is
`observation` when there were enough probes to compute the pool from, and `seed`
when there were not — the starting list is then returned AND SAID to be the
starting list, with `pool_reason` in words. A pool is never invented to look
measured. In the default answer the unmeasured case travels as
`trouble.pool_unmeasured`; the field itself comes with `verbose: true`, because
`observation` is the healthy state and healthy news does not belong in a place
whose emptiness means "all well".

**3. Read the trust labels.** From the prober's database: which engines can be
trusted in the sense of substituting the subject. Cached — the prober adds one
probe per engine per ten minutes, so there is nothing to recompute more often. If
the database is unavailable, every engine becomes `not_checked` and
`all_engines_clean` becomes false. The failure direction is fixed: a broken
observation switches verification ON at the consumer.

**4. The outer wait loop.** Is any engine free? If none is, sleep briefly and
look again until the wait deadline. Polling rather than computing a wake-up time:
between the computation and the attempt a neighbour could take the engine that
came free.

**5. Walk the pool, top down.**
   - collected as many results as asked — stop;
   - this engine may not be asked yet, by rate or by cooling after a refusal —
     skip it, remember the name, and return it in the answer;
   - otherwise mark the time and ask THAT ONE ENGINE.

**6. Parse the engine's answer.** Non-link results are dropped. A redirect
wrapper is unwrapped to the real target — some engines return their own address
with the target hidden in a parameter, and a reader handed that address honestly
returns zero characters. The domain is computed. The result set is checked for
substitution: if ALL of an engine's results fail to answer the query, they are
discarded entirely and the name goes into the answer as a separate list.

A refusal and a substitution send the engine to cool for two minutes; an EMPTY
result set cools it for five seconds only. They are different events: a refusal
is about the engine, emptiness is about the query. Cooling on emptiness punishes
the pool for the rarity of a question.

**7. Merge.** A new link is added. A link already seen is NOT a duplicate but
CORROBORATION: the engine's name is appended to it. Separately counted is who led
to the same SITE — engines often return different pages of one domain, and
corroboration by exact address would then report zero although every engine found
the site.

**8. Back to step 4** if there is not enough yet and time remains. If nobody was
asked at all and the time ran out — a refusal with a reason, not an empty result
set: "we asked nobody" must be distinguishable from "nothing was found".

**9. Read the top links** (unless `read: false`). The top three by default, in
parallel, one thread per domain. The time spent is returned split into
`search_ms` and `read_ms`, and the pages are counted three ways: read, empty,
failed.

**10. Assemble the answer.** Silent engines are de-duplicated. The difference
between "asked" and "answered" gives the engines that returned emptiness
silently. Every list is returned ALWAYS, empty ones included — an empty list is
information too.

Corroboration is attached only where it means something: with a single witness
the two fields are ABSENT, because there `1` would mean "nobody else was asked"
rather than a measurement.

**And each result carries the words of the query that are in IT**
(`query_words_here`), while words present in NO result reach `trouble` as
`query_words_matched_nowhere`. This is the only evidence in the answer that is
about THIS ANSWER: the trust label describes an engine's probe history and
`pool_source` describes the instance, so without it a query for a thing that does
not exist comes back as confident results about the nearest thing that does. It
is evidence and not a verdict — a missing word can be a synonym or a translation
— and the rule for "which words count" is the same one the relevance check uses,
so the flag and the evidence cannot disagree about one query.

## A page read, step by step

The same rules, a different subject.

**1. Parse the input.** Addresses become a list; ceilings are applied silently:
`max_chars` is clamped, `offset` made non-negative, an unknown `format` becomes
`markdown`. Addresses BEYOND FIVE are not dropped but returned with
`status: not_reached` — the client must see that part of the batch was skipped.

**2. Check that reading is allowed at all.** Scheme, host present, no
search-results pages, nothing inside the perimeter. The last is not hygiene: the
address comes FROM OUTSIDE, out of somebody else's results, and without the check
`web_read` becomes a way to ask the container to visit its network neighbours. It
is checked against the RESOLVED address, not the look of the string: a public
name can point at a private one.

**3. The cache.** A read result lives 300 seconds. A hit is returned with
`via: "cache"` — not for speed but for the address: reading the same link twice
within one task must not cost a second request to the site. `read_at` stays the
moment the page was READ, and `cache_age_s` says how old the copy is: stamping
"now" on a copy hides the only thing that distinguishes it from a fresh read.

A refusal issued on somebody else's behalf is never cached. A scan met on the
search path is returned unrecognised, and remembering that under the address
would answer a later `web_read` — which asked for exactly the thing refused —
with a refusal it never earned.

**4. Wait for the turn AT THE DOMAIN.** Not per path and not per process — per
domain. Five links from one publisher are an ordinary case and must not leave in
a burst. We wait up to eight seconds, then answer `not_reached` honestly: the
caller cannot tell a long wait from a hung network.

**5. Download with a ceiling, a deadline and a rate.** The READ is bounded before
unpacking, because what needs protecting is memory; the CEILING is measured after
it, because that is what the caller receives. Measuring one and cutting the other
made a compressed page that unpacks past the limit arrive as "read, whole".

A page cut at the ceiling says so — `download_truncated` — and then `total_chars`
is ABSENT, because the document's size is not something we established;
`total_chars_at_least` carries the floor. At the tail of what we did fetch the
answer still reports `truncated: true`: the end of our copy is not the end of the
document, and a cursor that says otherwise stops the reader with megabytes
unread.

**The whole fetch has an end, not just each wait inside it.** A page delivered a
byte a second keeps the connection healthy and the data flowing, so no timeout
that measures WAITING ever fires — measured: 380 seconds against a declared
ceiling of ninety, and no answer at all. The bound is on the download as a whole,
and a page that overruns it is a named refusal quoting the rate, not a fragment
passed off as the text.

**What cannot be unpacked is not called text.** We ask for gzip and deflate; a
stream that is broken, cut, or packed as something else (`br`) yields no content
and a stated reason, rather than raw bytes arriving as `content` under
`status: read`.

A PDF has its own, larger ceiling, chosen by the file signature rather than the
declared type. The error body is read DELIBERATELY: shields answer 401/403 and
put into the body exactly the marker that identifies them, while the status code
lies.

Every redirect hop is checked, not only the address we started from. Otherwise
another site can send us inside the perimeter and get the content back.

**6. Parse in one pass.** Text, markdown, links and metadata are collected in a
single walk of the markup. Not for speed: three passes are three places where
"the main content" is decided differently, and one day they diverge. The encoding
comes from the header, then from the markup, then by trying utf-8 and falling
back to a legacy single-byte encoding — mojibake is a silent defect, and the text
length under it is plausible.

**7. Rejection.** The ORDER matters more than the markers. First "no such page"
at the start of the text — at any length, because that kind of stub can be wordy.
Then: **if there is enough text the page is real**, whatever sits in the markup.
Only when text is short are the shield, paywall and script-application markers
looked for. The reverse order rejects a live 33 905-character article because the
word `captcha` occurs in the page's own chrome.

**8. Slice and cursor.** Content is cut BEFORE serialisation, and the number to
continue from is appended to the text. Cutting a finished JSON string would slice
it mid-string, and the client would receive "the module is broken" instead of
"the content is truncated".

**9. Assemble the answer.** Every field for every address, always, on every path.
`all_pages_clean` uses the same failure direction as `all_engines_clean`: true
only if every address was read, rejection said `clean`, and the requested marker
was found. No data means false.

### The escalation ladder, and why it is short

Measured over 39 blocked pages: a plain request covers 70-77% of addresses;
imitating a browser fingerprint would add eight of the 39; a real browser three
more. Against the strongest anti-bot services a fingerprint does not help AT ALL.
So the browser is a rare escalation over a few per cent of addresses, not the
main path — and the common belief that certain publishers can only be read with a
browser is false: two of three named ones return 16 781 and 7367 characters to a
plain request.

Under `mode: auto` the browser is tried only when the plain request gave `stub`,
`empty` or `refused`, and its result is accepted only if it is BIGGER. Otherwise
an honest `empty` would be replaced by the same `empty`, dearer.

If the browser sidecar is absent, `mode: browser` answers `not_reached` rather
than quietly falling back to the plain path: a caller told the shield was passed,
and handed a stub, is worse off than one told the path is unavailable.

## Images and screenshots

An image search follows the search steps with one category-wide difference: its
own pool, its own reference (the domain of the image FILE), and merging by
`image_url` rather than by page — merging by page would collapse different images
from one page into one.

A screenshot is a single browser visit that produces two things by different
routes: the pixels from the layout engine and the text from the DOM. That is what
makes cross-checking possible — a disagreement between them catches what neither
route sees alone. Both come back: `png_base64` and `page_text`, bounded by
`max_chars` (2000 by default) with the whole length in `page_text_chars`. Returned
as a count alone, the text would have to be fetched by a second call in another
visit — and a comparison of two different moments of a page is not a
cross-check. `shot_taken` and `expected_found` are separate fields on purpose: a
shot can be taken flawlessly and show a captcha.

## Who pays for recognition

A scanned PDF becomes text only through a vision model, and the cost follows the
EXPLICITNESS OF THE REQUEST. `web_read` on a document was asked to read the
document — recognition is how a scan is read, and the caller accepted the price by
making that call. `web_search` was asked for links with text, and a scan that
happened into the top three is not a reason to bill for a model.

So reading recognises and search does not. On the search path such a result
carries `text_source: not_recognised (scan)` and a reason naming `web_read` — a
refusal with a way out rather than an empty field. It is deliberately NOT cached:
under the plain address it would answer a later `web_read` with a refusal issued
on somebody else's behalf.

## A deep search

One wave is: ask the model for a plan (queries plus markers), run ALL the
searches, then run ALL the reads in one parallel pass, then check the markers.

Searches go in a QUEUE, reads go in PARALLEL, and the asymmetry is measured.
Concurrent searching is what gets us blocked — at forty concurrent, three engines
out of five went silent. Reading does not compete with itself: twenty pages of a
corpus sit on nearly twenty different domains, so the per-domain limiter never
binds. Reading is 72-76% of the whole run, which is why it is the part that was
parallelised.

The wave stops as soon as the markers meet on a page. Ambiguity is checked BEFORE
the digest — asking a digested answer whether there were several subjects is
asking the side that has just chosen. The digest is built only from pages where
the markers met; if there are none, the answer says so on its first line.

**A search that never ran ends in `not_attempted`, not `not_found`.** Refused
before the first query — no model configured, an empty question — it produces no
corpus at all, and giving that the value of "we looked and the sources were
empty" hands a caller branching on `outcome` the module's own cardinal error.
The reason is in `error`; a caller who branches does not read prose.

## Where the engine pool comes from at read time

The adapter never computes the pool: the prober writes it, and `read_pool` reads
it back, opening the database BY PATH and read-only. The split is deliberate —
the process that measures and the process that answers must not share a writable
handle — and it means the reading side is the only way a computed pool reaches an
answer at all. If it breaks, nothing shouts: the module falls back to the seed
and says so honestly in `pool_source`, which is exactly what makes such a
breakage look like ordinary life. That is why the reading side is tested against
a real file rather than an in-memory database.

## The queue under concurrency

One limit: **at most one request per engine per interval.** The check and the
timestamp happen under one lock, so two threads cannot take the same engine
within one interval.

A request that got no engine WAITS for one to free up, up to the wait deadline,
and only then refuses. Measured on thirty concurrent requests: all thirty served
in 5.6 s, median 3.06 s, and exactly thirty outbound requests — one per search.

Waiting once BEFORE the sweep instead gives 11 successes and 19 refusals at 12
outbound requests: the engines protected and the callers punished for nothing.
The mistake is in the placement of the wait, not in the limiter.

The ceiling follows from the construction: the number of engines divided by the
interval gives the outbound requests per second, and a search costs on average
slightly more than one. A sequential sweep cannot reach that ceiling — only
concurrent ones can.

## What multiplies outbound requests

There are more requests than searches when engines return FEW links: more was
asked for than arrived, and the sweep honestly goes further down the list. On a
generous result set the first engine suffices.

Hence the unobvious consequence: **a thin result set costs more than a rich one.**
A rare query, on which little exists, costs the system several times the requests
of a common one.

The corroboration mode multiplies deliberately: `min_engines: 3` asks three
engines regardless of what was already collected. That is what buys independent
witnesses, and the price is proportional.

## What the algorithm does NOT guarantee

**This is not a fair queue.** There is no service order: waiters compete for
whichever engine frees up, and in a long queue someone may be unlucky several
times running. A real FIFO becomes worthwhile when a consumer appears that cares
about order; there is none today, and the complexity of a priority queue would be
paid for always.

**There is no limit on the number of concurrent callers.** What is limited is the
rate OUTWARD, not the number of callers: a thousand concurrent requests will not
spam the engines, but they will occupy a thousand threads and receive refusals
when the wait deadline expires.

**The pool is recomputed, but not instantly.** It is computed by the prober from
its own probes and cached by the adapter for a minute. An engine that dies is
noticed within the prober's lap, not within the next request.

**Nothing verifies that an answer is TRUE.** Everything here verifies that the
answer is about the subject that was asked about, and that its sources are
distinguishable from blocks and from each other. Truth is the reader's problem,
and the fields exist so that the reader has something to work with.
