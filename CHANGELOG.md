# Changelog

## 0.3.0

**Read this before updating.** The default answer changed shape, and the contract
name changed with it: `ag.search/2` → `ag.search/3`, `ag.images/2` → `ag.images/3`.
Anything matching on the old name fails loudly instead of matching nothing
quietly — that is the point of the break.

Version 0.2.2 was never published; its changes are folded in below.

---

### The answer says what it checked, and stops saying what it did not

**`trouble` replaces four separate fields, and is always present.** An empty
dictionary means "checked, nothing wrong" — `if not trouble` is the whole of the
good case. A missing key would mean "this side was never examined", and the two
must not share a shape.

| before | now |
|---|---|
| `search_aborted`, `engines_unasked` | `trouble.search_aborted`, `trouble.engines_unasked` |
| `unresponsive_engines` | `trouble.unresponsive_engines` |
| `engines_irrelevant` | `trouble.engines_irrelevant` |
| `pool_source: "seed"` | `trouble.pool_unmeasured`, with the reason |
| `arguments_adjusted` | `trouble.arguments_adjusted` |

**Everything that only explains the call moved behind `verbose: true`** —
`count`, `query`, `page`, `read`, `read_top`, `pages_read`, `pages_empty`,
`pages_failed`, `timing_ms`, `engines_skipped`, `engines_used`, `tiers_used`,
`pool_source`, `pool_reason`. Nothing was removed from the module; it is returned
when you ask. A consumer measured us from outside: twenty-five top-level fields
arrived on every call, seven were ever read, one was branched on. The rest took
room from a client with a narrow observation ceiling.

**`corroborated_by_url` and `corroborated_by_domain` are ABSENT when only one
engine was asked.** They used to be `1` — a number that looks measured and means
"nobody else was asked". On the cheap path the sweep stops at the first engine
that returns enough links, so one witness is one witness by construction. A field
whose `1` means "we did not look" must not stand beside measured ones. Ask for
width (`min_engines: 3`) and they come back.

**Two new fields say what this answer carries, not what the engine did in the
past.** `engines_trust` is earned over weeks of reference probes and says nothing
about the query you just sent — so a search for a thing that does not exist came
back with six confident results about something else, all marked clean. Now each
result carries `query_words_here` (which of your words are in it), and
`trouble.query_words_matched_nowhere` lists words found in no result at all.
This is evidence, not a verdict: a missing word does not make a result wrong —
synonyms, translations and abbreviations exist — so the judgement stays with the
caller.

**`all_engines_clean` is documented for what it is:** a statement about the
ENGINES, not about whether these results are on your subject.

---

### Arguments are parsed, not coerced — and both doors parse them the same way

**Booleans.** `bool("false")` is true, because every non-empty string is. So
`"read": "false"` over MCP meant READ, while `read=false` over the plain door
meant do not — one argument name behaving in opposite ways at two entrances, which
no caller can be expected to guess. Understood now, case-insensitively:
`true/false`, `1/0`, `yes/no`, `on/off`, `y/n`, `t/f`, and Python's `True/False`.

**A value we cannot read turns its flag OFF and says so** in
`arguments_adjusted`, e.g. `read='maybe' is not a boolean, treated as off`. This
is a decision about cost: every flag here buys something expensive when on —
reading pages, sweeping more engines, a browser, a bigger answer — so a
misunderstanding must fall to the cheap side. `False` with a capital F is what
Python prints, and a model writing arguments writes booleans as strings routinely;
these are not exotic inputs but the likeliest ones.

**An empty value is not an absent one.** `?read=` used to arrive as an argument
nobody mentioned — `parse_qs` drops blank values — and quietly took the expensive
default. An argument the caller wrote is now a value the caller wrote. An argument
nobody passed still keeps its documented default and is not reported.

**Numbers are clamped into range and named**, never silently substituted:
`n='abc' is not a number, using 6`. And **an argument name we do not know is no
longer dropped in silence**: the plain door speaks `q`, `n`, `max_chars`, MCP
speaks `query`, `max_results` — sending one door the other's spelling now says so
instead of answering a different question.

---

### Reading survives pages that are legal but hostile

**A page that arrives one byte per second now ends.** A timeout measures WAITING,
and a slow page never makes anyone wait: the connection is healthy, the data keeps
flowing, so no socket timeout ever fires. Measured on a trap server: 380 seconds
against a declared 90-second ceiling, and no answer at all. The bound is now on
the whole download rather than on the pause inside it, and the refusal names the
speed — so "the site is slow" is distinguishable from "the site is down".

**Compression that fails is a named refusal, not text.** A gzip stream cut in
half used to come back as `status: read`, `stub_check: clean`, with the raw
compressed bytes in `content` — the assumption being that something downstream
would notice. Nothing downstream looks. The same now applies to an encoding we
did not announce: a server may answer `br` anyway, and what we cannot decode we do
not call decoded.

**`robots.txt` is unpacked like any other body.** We announce `Accept-Encoding:
gzip, deflate`, so a site is entitled to answer compressed — and compressed bytes
decoded as text become rubbish in which `Disallow` is never found. The verdict
came back `allowed` over a site that had forbidden us. A file we could not unpack
is now `not_checked`, not `allowed`: failing to read the rules is not permission.

**Whether to obey `robots.txt` is still the operator's decision, not the
module's** — see the deployment section of the README, where that policy is now
stated before the first call rather than only in the contract.

**A bad port is a refusal like any other.** `http://example.com:99999/` used to
kill the request with no answer at all — not a refusal, not a status, an empty
body: the port is parsed on a path with no guard around it, while the address
check looked only at the host name. Verdicts about addresses are now made where
addresses are judged.

**Truncation is visible.** `download_truncated` says the download hit the ceiling
— a different event from `truncated`, which is about your own `max_chars` window.
And a size we do not know is no longer stated: when the download was cut,
`total_chars` is absent rather than reporting the size of the part we took.

**A cached answer is dated when the page was READ**, and `cache_age_s` says how
old the copy is. It used to be stamped "now".

---

### Whose mistake, and who is calling

**The HTTP code follows the CAUSE, not the door.** A missing required argument
came back as `502 Bad Gateway` on four doors out of five — the module blaming the
service behind it for the caller's own mistake, while the metasearch was alive.
Proxies and monitors read that as "the backend is down". A caller error is now
`4xx` on every door; `5xx` stays for a refusal that really is upstream.

**`/stats` splits calls by who says they made them.** MCP clients send
`clientInfo` in `initialize`; that name now counts in `by_client_says`. The name
is a claim, not a fact — hence `says`. Three absences that must not be one are
kept apart: `not_introduced` (a call on a connection that never introduced
itself), `introduced_without_name` (a handshake with no name in it), and
`plain_door` (no handshake exists there at all).

---

### Images, screenshots, deep search

**Every exit from image search goes through the shaping, including the early
ones.** Two refusal paths bypassed it and returned the old shape with no `trouble`
key at all.

**Image search names adjusted arguments the way web search does.** `n=abc` was
clamped in silence there while its neighbour said so — one rule, two
implementations.

**A screenshot reports its real size.** `width` and `height` were always `null`:
a field promising data nobody ever set. They are read from the PNG header now, and
stay `null` only where no screenshot was taken.

**Deep search distinguishes "did not start" from "found nothing".** An abort
before the first request returned `outcome: not_found` — the same value as an
honest search that looked and found nothing, with `waves_done: 0` beside it. It is
`not_attempted` now.

---

### Tests

**The suites make no outbound requests, and this is now enforced rather than
promised.** Two of them did: the deep health check read a real reference page,
and the concurrency block searched with reading left on. The claim in the README
was false and could be disproved in one command. The suites now run with the
network switched off in CI.

**Guards that could not fail were repaired**, among them an assertion of the form
`X == X`, three checks closed behind a condition that never held, three of the
five tools never called through `tools/call`, and four plain doors mentioned in no
test at all. The pool's read path — the only door through which the adapter learns
the computed engine list — was exercised only with a non-existent file, that is,
on its refusal branch alone.

**A new guard runs the other way round:** every key that reaches a caller in a
live answer must be described in a shipped document. Its source is the live
answer, not a list inside the check — a list would fall out of date exactly as the
documents did.
