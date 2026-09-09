# `ag.search/3` — the web-search provider contract

> **Written from the code.** Every field name, value and limit below is taken
> from `adapter/server.py`. If the service stops matching this document, the
> service has diverged.

Status: **implemented**. MCP tool `web_search`, plain door `GET /ag/search`.

This is the contract of a CAPABILITY: how a module offers "search the web" to a
caller, and what the caller is owed in return. It says nothing about how any
particular caller registers it — that is the caller's business, and mixing the
two produces a document that is half promise and half plumbing.

## Contents

What changed against `/1` and `/2` · Why the policy lives in the module · the interface · the three engine fields that
are always present · the four ways an engine fails · what counts as success ·
failure direction · what corroboration does and does not mean.

## What changed against `/1`

`/1` and `/2` carry the same fields, the same shapes and the same guarantees.
What changed is the DICTIONARY OF VALUES the fields take, and that is why the
name changed with it: a caller branches on values, so the dictionary is part of
the contract, and a changed dictionary under an unchanged name is
indistinguishable in a stored answer and in code being read.

The break is loud on purpose: an answer says `/2`, and anything expecting `/1`
fails at once instead of silently matching nothing.

## What changed against `/2`: the shape, not the dictionary

`/2` renamed the values; `/3` changes WHICH FIELDS ARRIVE BY DEFAULT. The values
are untouched, so the table above still reads a `/2` answer.

The ground is a measurement taken by a consumer ON US: twenty-five fields at the
top level, seven ever touched, ONE branched on. The rest arrived on every call
and took room from a client with a narrow observation ceiling.

**Four of them were not deletable, and they are why this contract exists**: an
aborted sweep, engines that stayed silent, engines discarded as off topic, and a
pool that was never measured. They say the answer is incomplete BY NO DECISION OF
OURS. A consumer failing to read a field and the field not being there are
different things, and the first is cured by making it visible.

So they collapse into `trouble` rather than disappearing:

| `/2` | `/3` |
|---|---|
| `search_aborted`, `engines_unasked` | `trouble.search_aborted`, `trouble.engines_unasked` |
| `unresponsive_engines` | `trouble.unresponsive_engines` |
| `engines_irrelevant` | `trouble.engines_irrelevant` |
| `pool_source: "seed"` | `trouble.pool_unmeasured`, with the reason |
| `arguments_adjusted` | `trouble.arguments_adjusted` |
| `corroborated_by_url`, `corroborated_by_domain` (always present, `1` on the cheap path) | absent unless at least two engines found results |

**`corroborated_by_url` and `corroborated_by_domain` are ABSENT when there was
only one witness.** In `/2` they were always there, and on the cheap path they
were always `1` — where the sweep stops at the first engine that gives enough
links, one is not a measurement but "nobody else was asked". The same `1` on a
wide sweep means "three engines asked, one found it". Two different pieces of
news under one number, standing next to `engines_trust` and reading as measured.

This is the one place where an ABSENT field is the right form: with a single
witness the question "how many independently found it" was never put. Who was
asked is in `engines_asked`, and who failed to answer is in `trouble`. Ask for
width with `min_engines` and the fields come back, now meaning something.

**`trouble` is ALWAYS present and empty when nothing went wrong.** `if not
trouble` is the whole of the good case. An empty dictionary means "checked, all
well"; an absent key would mean "this side was not examined at all", which is
different news and must not share a shape with it.

Everything else that only explains the call — `count`, `query`, `page`, `read`,
`read_top`, `pages_read`, `pages_empty`, `pages_failed`, `timing_ms`,
`engines_skipped`, `engines_used`, `tiers_used`, `pool_source`, `pool_reason` —
is returned when you ask: `verbose: true` (or `&verbose=1` on the plain door).
Nothing was removed from the module; it moved behind a request.

**Why a version number for this at all.** A consumer that read `search_aborted`
would find it absent and, in most languages, read the absence as "nothing was
aborted" — a silent false negative, which is precisely the failure direction this
module refuses. Better a loud break for one caller than a quiet lie to all.

## Why the engine-selection policy belongs to the module

A caller that keeps its own list of engines, its own batch size and its own
round-robin is describing the INTERNALS of a metasearch layer it does not own.
The same knowledge is then written a second time in the metasearch settings.

**The duplication is silent.** Once the two lists drift apart the metasearch
returns fewer results, with no error and no log line, and from outside it looks
like "search got worse". Two places obliged to agree, and nothing comparing them.

So the policy is taken by the module whole, and the caller does not know about
it. The price is that the module needs a process of its own — a metasearch has no
such API. The price is accepted deliberately: an extra process is a visible
thing and its absence is noticed at once; a silent divergence of two lists is
never noticed at all.

## Interface

```
GET {base_url}/ag/search?q=<query>&n=<how many>&page=<0,1,2…>
```

Also accepted: `read` (default true), `read_top` (0 means "no preference"),
`min_engines`, `per_engine`, `verbose`.

```json
{
  "contract": "ag.search/3",
  "ok": true,
  "error": "",
  "results": [
    {"title": "…", "url": "https://…", "snippet": "…", "domain": "example.com",
     "via": "engine-a, engine-b",
     "corroborated_by_url": 2, "corroborated_by_domain": 3,
     "content": "…", "chars": 5812, "read_status": "read",
     "stub_check": "clean", "text_source": "text_layer"}
  ],
  "engines_asked":        ["engine-a", "engine-b", "engine-c"],
  "engines_answered":     ["engine-a", "engine-c"],
  "engines_trust":        {"engine-a": "clean", "engine-c": "not_checked"},
  "all_engines_clean":    false,
  "trouble":              {}
}
```

That is the whole default answer: nine fields, and `if not trouble` is the good
case. With `verbose: true` the accounting comes back beside them —
`count`, `query`, `page`, `read`, `read_top`, `pages_read`, `pages_empty`,
`pages_failed`, `timing_ms`, `engines_skipped`, `engines_unasked`,
`engines_used`, `tiers_used`, `pool_source`, `pool_reason`, plus the raw
`search_aborted`, `unresponsive_engines`, `engines_irrelevant` and
`arguments_adjusted` that `trouble` is built from.

**`trouble` when something did go wrong:**

```json
{
  "trouble": {
    "search_aborted": "metasearch stopped answering: ConnectionError",
    "engines_unasked": ["engine-c"],
    "unresponsive_engines": [["engine-b", "captcha"]],
    "engines_irrelevant": ["engine-d"],
    "pool_unmeasured": "not enough observations, the seed list was returned",
    "arguments_adjusted": ["n='many' is not a number, using 6"]
  }
}
```

Each key names a different kind of "less than it looks": the sweep stopped
mid-way and these engines were never asked; this one was asked and stayed silent;
this one answered about something else and its results are already discarded; the
engine pool is the starting list rather than a measured one; and we answered a
slightly different question than the one asked, because an argument was unusable.

**`pool_unmeasured` rather than `pool_source` in `trouble`.** A field with a
legitimately fine value cannot live in a place whose emptiness means "all well" —
`observation` is the healthy state, so only the unmeasured case enters, under a
name that says what is wrong. `pool_source` itself is still there under `verbose`.

**`arguments_adjusted` is never silent.** Numbers out of range are clamped and
rubbish falls back to a default — silently, that would be the module answering a
question other than the one asked. `read_top: 0` is NOT such a case: zero means
"no preference" and is documented as the default's name, not an adjustment.

**Booleans are parsed, not cast, and identically at both doors.** Understood:
`true/false`, `1/0`, `yes/no`, `on/off`, `y/n`, `t/f` and Python's `True/False`,
case-blind. JSON `null` is a PASSED value, not an absence: it is unreadable like
any other, while an argument nobody sent keeps its default in silence.
A value that cannot be read **turns the flag off** and is named here — not
because off is the default (for `read` it is not) but because every flag here
buys something expensive when on, and a refusal must fall to the cheap side.
Measured before the fix: `"read": "false"` over MCP read the pages anyway, at
eight times the wall clock and seven times the payload, with nothing said.

An ABSENT argument keeps its documented default and is not reported — nobody
made a mistake. An EMPTY one is a value we could not read, and is treated like
any other unreadable value. Collapsing the two hid the expensive case: `""` used
to take the default, and for `read` the default is the costly side.

**Search reads by default.** The top `read_top` results (3 unless asked
otherwise) are fetched and their text arrives in `content`. `read: false` returns
links alone and costs a fraction of a second. The cost is never hidden:
`timing_ms` is split, and `pages_read` / `pages_empty` / `pages_failed` say what
was paid for and what came back as a block.

Mandatory fields, and why each is mandatory:

* `contract` — the caller must check it and answer an unknown value with an
  EXPLICIT error rather than an empty result set. A version mismatch across a
  seam otherwise looks exactly like "nothing was found".
* `ok: false` — the module answered and could not do the work; `error` carries
  the reason in words.
* `results` — may be empty. **An empty result set is not an error**: it is an
  honest "nothing found", and it must be distinguishable from "the module is
  broken".
* `via` — which engines produced this link. It shows what an instance is still
  alive on and what it is not.
* `page` — from zero, echoed under `verbose`. A module that cannot paginate
  returns the first page again; the caller de-duplicates by address.
* `trouble` — present in every answer, success and refusal alike. Empty means
  checked and clean.

## The engine fields are always present, empty ones included

An engine fails in **four** ways, and three of them give no sign. All four must
be visible, or search is merely "worse today":

| how it fails | what it looks like | what shows it |
|---|---|---|
| answers with a refusal | 429, captcha, access denied | `unresponsive_engines` |
| silently returns nothing | zero results, no marking anywhere | `engines_asked` minus `engines_answered` |
| answers a different question | results exist, but they are somebody else's | `engines_irrelevant` |
| answers THE question about ANOTHER subject | results look entirely real | `engines_trust` |

The third is measurable and common: asked for a company's tax number, an engine
returned a Windows help page; asked about a person, a speed-test site — four
times running, laid out as ordinary results. Such a failure is more dangerous
than a block: a block is visible, this looks like a result and travels silently
into a dossier.

**The granularity of the check is per engine — not per batch, not per link.** Per
batch is wrong because several engines arrive interleaved: "is there any match in
this batch" passes on the good engine's answers and the bad one's rubbish rides
along. Per link is wrong too: a legitimate result can carry none of the query
words, because a company is named by an abbreviation there. A good result thrown
away silently is no better than rubbish let through silently. So an engine is
judged by ALL of its results, and a link is discarded only when EVERY engine that
returned it is judged to have substituted the subject.

Two further fields separate "not asked" from "asked and silent", and they must
not be conflated:

* `engines_skipped` — not asked at all: the rate limit, or cooling after a
  refusal.
* `engines_unasked` — not reached because the sweep BROKE OFF, and only those.
  Non-empty `search_aborted` says the metasearch stopped answering mid-sweep, so
  the results are incomplete by no decision of the module's.

## The fourth failure, and the label that catches it

An engine can answer briskly, on the words of the query, about a different
subject: asked for a bank's official site it returns a cryptocurrency exchange,
an online casino, a food-delivery service. All formally relevant. A check against
the words of the query passes this every time — the words did match.

It is caught only by a **reference**: a query whose correct answer is known in
advance and confirmed OUTSIDE the engines. A prober runs the references
continuously, and by hitting them an engine earns a label:

| `engines_trust` value | meaning |
|---|---|
| `clean` | three or more observations on confirmed references, no misses |
| `candidate` | no misses, but fewer than three observations |
| `substitutes` | has answered about a namesake at least once |
| `unavailable` | probes were made and ALL refused — news about the address, not the engine |
| `not_checked` | no probes |

`all_engines_clean` is a cheap flag to branch on: true only when every engine that
ANSWERED is `clean`. It is counted over the engines that answered, not those that
were asked — over an empty answered-list a naive `all()` returns true, and
"results come from clean engines" would come out TRUE over results that do not
exist.

**The label has a chosen failure direction.** No data means `not_checked`, never
`clean`: a broken observation switches verification ON at the consumer, not off.

The verdict is computed over the LAST N observations rather than over a period. A
period sees a breakage instantly and a repair only when the old observation ages
out, so an engine that substituted a subject once stays condemned however many
clean hits it collects afterwards.

## Corroboration is not correctness

`corroborated_by_url` says how many independent engines found THIS SAME link;
`corroborated_by_domain` does the same for the site, because engines often return
different pages of one site and a check by exact address would then report zero.

Neither means the answer is truer. **On an ambiguous name the most corroboration
goes to the best-indexed namesake.** One name can belong to a retailer, a
platform and two manufacturers at once, and all of them are real.

There is a measured caveat, still open: three NAMES are not three independent
witnesses. Several engines share one index, and of the links corroborated by
three engines a quarter can be corroborated by ONE index counted three times. The
pool holds a per-family cap, which reduces the harm without curing the count.

`min_engines: N` asks at least N engines regardless of how many links were
already collected — that is what buys independence, and the price is proportional
in outbound requests.

## What counts as success

* **`ok: true`** — the work was done: the engines were asked and each of them got
  a named fate. Even when nothing was found.
* **`ok: false`** — the work was not done on the module's side of the seam:
  nobody could be asked, or the metasearch is unreachable. The FULL field set is
  returned then too, empty lists included — otherwise the consumer would have to
  branch on the shape of the answer.
* **"Nobody was asked" is a refusal, not an empty result set.** An empty result
  set means we looked and found nothing.

## Failure direction

The module stands FIRST in a caller's chain, so its unavailability is the most
likely failure in the whole construction. **Unavailability must degrade, not
fall over**: a timeout, a refused connection, a non-JSON body or an unknown
`contract` value must move the caller to its next provider rather than break the
search.

The health check answers for the CAPABILITY, not for the process. A live adapter
in front of a dead metasearch must be unhealthy — a health check that answers for
itself lies exactly when it matters. A dead browser is a separate case: it is
named in `degraded_paths` and deliberately does not drag `ok` down, because
restarting the adapter does not revive a sidecar.

## Deployment is not part of this contract

Where the adapter lives, what the compose project is called, which network it
joins — none of that is promised here. A contract describes a PROMISE; names of
deployment are exactly what breaks consumers when they change.
