# `ag.deep/1` — the deep-search contract

> **Written from the code.** Every field name, value and limit below is taken
> from `adapter/deep.py` and `adapter/server.py`. If the service stops matching
> this document, the service has diverged.

Status: **implemented**. MCP tool `web_deep_search`, plain door `GET /ag/deep`.

## Contents

How this tool differs from the two beside it · markers, the single point of
failure of the whole verification apparatus · the five outcomes · ambiguity, and
why a mechanism finds it rather than a model · the shape of the answer · what is
visible about cost and time · what has been measured and what the measurement
does not say.

## One tool of three, and the line is drawn

| tool | what it does | what it returns |
|---|---|---|
| `web_search` | one pass: finds and reads the top links | material |
| `web_read` | the text of a known address, cursor, marker check | material |
| `web_deep_search` | composes its own queries, goes in waves, digests | **a judgement** |

The line is written into the description of each tool. Without that line nobody
remembers in a month what separates them, and the difference is erased by the
first edit.

**The module has a model, and that is a deliberate exception.** The rule
"mechanical work here, judgement at the caller" is suspended for this tool:
composing queries, judging a hit and digesting an answer are model work, and
without a model there is no tool. A service that needs somebody else's
orchestrator to produce a complete answer is not autonomous.

## MARKERS ARE THE SINGLE POINT OF FAILURE, AND THAT IS THE HEART OF THIS CONTRACT

Markers are short strings by which a page is checked to be about THE SUBJECT
ASKED ABOUT: a name, a version, a city, a year. The check requires **all markers
to meet on ONE page and close together** (a 1500-character window).

Togetherness is not optional. Counting a marker as confirmed when it occurs
anywhere in the corpus fails on real data: one marker covered 2 pages out of 16
and both were unrelated. Words of the question scattered over unconnected pages
confirm anything at all when taken separately.

**And now the failure.** When the model returns an EMPTY marker list, the whole
apparatus switches off silently: zero on-target by construction, markers meeting
nowhere by construction, namesake separation left with nothing to stand on — and
an answer is still produced, substantial and confident.

The tool then degenerates into "just an answer", indistinguishable from one that
passed verification. That is the defect class this module exists against,
occurring inside the mechanism built against it.

Three consequences, all implemented:

1. **A fallback.** If the model gives no markers, they are taken from the text of
   the question mechanically: proper nouns, Latin tokens, versions, numbers.
   Worse than the model's (it knows no synonyms) but not empty, and emptiness
   disables verification entirely. Words in inflected languages are trimmed by
   two characters from the end, or the form used in the question is not found on
   the page; the trick is crude and left VISIBLE in `markers`, because the reader
   must see what was actually searched for.
2. **`markers_from` names the source**: `model`, `question (the model gave no
   markers)`, or `no markers: neither the model nor the question gave any — THE
   CHECK DOES NOT RUN`.
3. **Empty markers announce themselves on the FIRST LINE of the answer**, not
   only in a field. Honesty hidden in a field is honesty nobody reads: to notice
   `markers: []` you must already know which field to look at.

## The five outcomes

| `outcome` | what happened |
|---|---|
| `found` | the markers met on at least one page |
| `ambiguous` | the sources hold SEVERAL DIFFERENT subjects under this name |
| `off_target` | material was found, but about another subject — a namesake, another city |
| `not_found` | not one non-empty source |
| `unknown` | there were no markers, so there was nothing to check with — "not verified", not "not found" |

**There are no thresholds on corpus size, and that is a decision.** Two runs of
one question differed twofold in characters (237932 against 190275). A threshold
taken from one measurement would be a threshold on noise. The outcome is decided
by ZEROES.

`unknown` exists separately because a question about a concept ("what is the HTTP
protocol") otherwise comes out as `off_target` — "material about ANOTHER
subject". That is untrue: a concept has no namesakes. The marker mechanism exists
to TELL ENTITIES APART, and on a general question it has nothing to tell apart.

## Ambiguity is found by a MECHANISM, not by a model

One name can belong to four different subjects at once — a retailer, an
investigations platform, a maker of enclosures, a maker of portable power. An
answer that picks the one with three domains against one for the others looks
like corroboration working and is in fact a choice of the MOST INDEXED namesake.

**Corroboration answers "how many independent sources say THE SAME THING", not
"which of the different answers is right".** A mechanism that collapses ambiguity
produces confident untruth — the familiar failure, except the source of
confidence is a counter rather than a model.

**Two preconditions first, and both before any model.** Over ten questions the
mechanical detector fires on four and at least three of those are false: a
question about a concept gives THREE groups, a comparison question gives the two
sides named in the question, a price comparison gives different shops.

1. **No name, nothing to check.** Ambiguity is a property of a NAME: one name
   with several bearers. With no markers the check does not run at all — the same
   rule as everywhere here, that a probe returning zero must first show its
   subject existed.
2. **Groups falling onto DIFFERENT markers of the question are the structure of
   the question.** The question itself named two subjects, and a comparison
   yields pages about different things by construction. Checked mechanically: for
   each group, the markers present on the majority of its pages.

**The split is computed from the page texts, without a model.** Connectivity with
asymmetric containment (the share of shared words measured against the smaller
page), threshold 0.12, a group from two pages up. Marker words are dropped from
the vocabularies: by construction they occur on every page, and leaving them in
would make namesakes look similar by the shared name — the very thing that
confuses them.

**The model is called AFTERWARDS, and only to NAME the groups already found.**
Separating and naming are different jobs, and the side inclined to collapse
ambiguity is never let near the first: a coherent answer looks better than a
list, and a model will prefer it. If it does not name them, the finding stands —
"there are several" is true without names.

**The model has a VETO, not a vote.** It cannot create ambiguity — the groups
were found before it and without it — but it can withdraw a false one. The
failure direction is the opposite of the one used everywhere else in the module,
and deliberately so: **one subject until proven otherwise.** Silence, rubbish or
an unreachable model do not declare ambiguity. Declaring it on silence would mean
crying "there are several" the more often the worse the model works.

**`ambiguity: null` covers FOUR different events**, and `ambiguity_check`
separates them in words: not run (no name); run and found no split; found a split
that fell onto the question's own markers; found a split the model judged to be
one subject. One value for four events is exactly the indistinguishability this
module hunts everywhere.

**The check runs BEFORE the digest.** Asking "were there perhaps several?" after
digesting is asking the side that has just chosen.

On `ambiguous` the answer is built from the LARGEST group and applies to it
alone; the other variants are real and are listed in `ambiguity.variants`.

## The shape of the answer

Fields are always present, on the failure path too.

* **the answer** — `answer` (with `[1]`-style references), `answer_basis` in
  words, `summarised_from_on_target`, `answer_truncated`;
* **verification** — `outcome`, `markers`, `markers_from`, `stopped_because`,
  `waves_done`, `waves_max`;
* **sources** — `sources[]`, `sources_total`, `sources_with_content`,
  `sources_on_target`, `sources_confirmed_2plus`, `sources_primary`,
  `confirmation_note`;
* **ambiguity** — `ambiguity`, `ambiguity_check` in words;
* **disagreement** — `conflicts`, `conflicts_note`;
* **cost** — `usage` (`model_calls`, tokens, `by_model`, `cost_usd`,
  `cost_source`, `ledger_written`), `timing_ms`, `elapsed_ms`;
* **queries** — `queries_asked`, `queries_failed`, `queries_empty`.

**Three numbers about sources, and they are different.** `sources_total` — how
many were found; `sources_with_content` — how many could be read;
`sources_on_target` — on how many the markers met. Of sixteen sources five can be
empty (shields, redirects), and "16 sources" then sounds four times more
convincing than "10 on target". A source with no content is not a source — but it
must not be discarded either: "found and could not read" is news about blocks and
about the address we call from.

**A query has three fates and they must not be conflated:** it returned links, it
ran and found nothing (`queries_empty`), or it never happened
(`queries_failed`). A single count of queries asked would report work that was
not done.

**`conflicts` is disagreement between sources about one fact** — an announcement
and a release fall on different days, so there can be two correct answers. The
mechanism requires TWO sources per version: at one source it fires four times out
of five and every time on rubbish (dates of articles, of neighbouring versions, of
page footers). A rare honest alarm beats a frequent dirty one — a guard that
shouts at a healthy system gets switched off entirely.

## Cost is visible

`usage.by_model` — tokens beside the model NAME. Total tokens without names
cannot be turned into money: text and vision models are on different tariffs, and
one sum silently mixes the expensive with the cheap.

**The module holds no price registry, and that is a requirement rather than an
omission.** The registry is single for a system; a second place knowing the
tariffs diverges from the first at the first edit. So with nothing supplied the
answer is `cost_usd: null`, with `cost_source` explaining why — never zero, which
would read as "it cost nothing".

Prices set by the operator in `LLM_PRICE_INPUT_PER_1K` and
`LLM_PRICE_OUTPUT_PER_1K` ARE multiplied out, and `cost_source` then says they
came from those variables. That is arithmetic on somebody else's numbers, not a
tariff of ours: the module still knows no prices, it only uses the ones it was
handed.

`ledger_written` is set BY THE FACT of a write to the spend ledger (a line per
call, visible in `GET /stats`). The precedent it exists for: a vision call
returning `usage` and writing it nowhere — eleven calls past the accounts, with
nobody noticing, because silence from a ledger is indistinguishable from no calls
at all.

`timing_ms` is split into `search_ms`, `read_ms`, `model_ms`. Before the split, a
hundred seconds could be explained by any of the three causes, and the
explanation sounded convincing either way.

## What has been measured

**Spread**, five runs of one question: **completeness varies, correctness does
not.** The date was named in four runs out of five; the fifth said it was not in
the sources. None named a wrong one. The acceptance rule from that: read `answer`
as a draft and `sources_on_target` as the measure of completeness.

**Time**, same runs: reading ate 72-76% of the total. With parallel reading (one
thread per domain) the whole run fell from 106 s to 42 s and reading from 79 s to
8.7 s.

**What the measurement does not say:** the size of the spread on other classes of
question. The rule "a single run proves nothing" is established; the number is
not.
