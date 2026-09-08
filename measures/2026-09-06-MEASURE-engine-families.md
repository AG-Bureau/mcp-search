# Pairwise overlap of result sets

> **One-off document:** a measurement · **Taken:** 2026-09-06
> How many of the engines being asked are DIFFERENT sources, and how many are
> shopfronts over one index. Not updated: the pool changes, and a recomputation
> is a new measurement rather than an edit of this one.

## Contents

How it was measured and why without a single outbound request · the measure ·
where the threshold comes from and what went into the code · the families and how
stable they are · what this means for corroboration · what the measurement does
not show.

## How it was measured

**Not one new outbound request.** The data was already in the prober's database:
the `urls` field of every probe names the addresses an engine returned. Over 22.7
hours, 7638 probes with addresses accumulated across 61 engines and 6 reference
queries.

The unit of comparison is a pair "engine + query": the union of addresses over
all probes of that engine on that query. Individual probes cannot be compared:
engines are polled at different times and different numbers of times, and an
overlap in a single probe is noise rather than a property.

## The measure: containment, not Jaccard

What is counted is not "how similar the result sets are" but **how much of A's
result set B has already returned**: `|A∩B| / |A|`. The measure is asymmetric,
and that is the point.

An example from this same data: `bing` and `yahoo` give a Jaccard of 0.29 — by
that they look different. But **95% of the addresses found by yahoo lie inside
bing's results**. As an independent witness yahoo adds nothing. Jaccard cannot
see this, because it penalises a difference in SIZE, while what matters is not
size but novelty.

**Filtering by result-set size.** An engine returning two links is contained in
almost anything arithmetically: one such engine showed 0.833 into nine different
engines at once, including certainly independent ones. That is a property of the
denominator, not of a shared index. We require at least five addresses on average
per query, which leaves 24 engines of the 43 that answer.

## Where the 0.5 threshold comes from

Two independent routes gave the same number, which is why it was taken.

**Route one — meaning.** "More than half of what A found, B has already found"
means that as an independent witness A is worth less than half. The statement
reads without any knowledge of statistics and does not depend on our sample.

**Route two — the distribution.** Overlap is normally close to zero: the median
over 508 directed pairs is **0.038**, three quarters are below 0.167. The 95th
percentile is **0.534**. So a threshold of 0.5 cuts off the top 5% of the
distribution rather than drawing a line through its dense part.

The sensitivity is stated honestly: a threshold of 0.40 gives 42 directed pairs,
0.50 gives 31, 0.60 gives 17. There is no cliff in the distribution — the number
was chosen, not found. Hence the note below on which conclusions depend on it and
which do not.

**What went into the code is 0.6, and this measurement is the reason.** The
second "family" below exists at exactly 0.50 and falls apart at 0.60 — at 0.50 it
glues five good engines into one and hands their pool places to engines four
times worse. Both routes above argue for the line being SOMEWHERE around a half;
the artefact decides where exactly. `POOL_FAMILY_OVERLAP` moves it.

## The families

### The Bing family — firmly established

    bing · duckduckgo · duckduckgo web · privacywall · vuhuv · yahoo

**It survives at EVERY threshold from 0.40 to 0.80.** It does not depend on the
choice of number.

| containment | what is inside what |
|---|---|
| 1.00 | `yahoo` already returned by `duckduckgo web` |
| 1.00 | `yahoo` already returned by `duckduckgo` |
| 0.98 | `duckduckgo web` already returned by `duckduckgo` |
| 0.95 | `yahoo` already returned by `bing` |
| 0.86 | `duckduckgo` already returned by `bing` |
| 0.75 | `vuhuv` already returned by `duckduckgo` |
| 0.65 | `privacywall` already returned by `duckduckgo` |

**Four of the six sat in the poll at the same time**: `bing`, `duckduckgo web`,
`privacywall`, `vuhuv`.

### The abcnyheter/brave/google/resulthunter/zapmeta "family" — NOT established

It exists at exactly 0.50 and falls apart at 0.60. The links inside it are
0.53-0.58, that is, right at the threshold. **It must not be called a family**:
it is an artefact of the chosen number, not a property of the engines. It is
recorded here precisely so that the next reader does not take it for a finding.

### The independent ones

`yandex` shares an index with nobody: its maximum containment is **0.14** (into
`privacywall`). This confirms and explains an earlier observation that its
corroboration by neighbours is low: it finds unique sources, and the low share
was a sign of value rather than of junk.

**A refuted guess.** It was supposed that `privacywall` and `zapmeta` were two
shopfronts over one index, neither with a search of its own. Measured: 0.354 and
0.417 in the two directions, below the threshold. They do not share an index.

## What this means for corroboration

**Eight engines were in the poll. Independent sources among them: five**, if only
the firmly established Bing family is counted — Bing itself (one voice for four),
`yandex`, `zapmeta`, `abcnyheter`, `brave`.

The corroboration mode requires agreement across three engines and knows nothing
about families. The direct consequence, computed over the same data:

| witnesses from the pool | links | of them ALL from the Bing family |
|---|---|---|
| 2 | 37 | 11 (30%) |
| 3 | 21 | **10 (48%)** |
| 4 | 11 | 3 (27%) |
| 5 and more | 21 | 0 (0%) |

**Of the 53 links corroborated by three or more witnesses, 13 (25%) are
corroborated by one index counted three times.** And among those with exactly
three witnesses — the borderline case the threshold decides — it is almost half.

The corroboration looks complete throughout: the answer carries three different
names.

## What the measurement does NOT show

* **The cause of the overlap.** The measure says "the result sets coincide", not
  "this engine queries somebody else's index". For one pair the cause is known
  from the code (two ways of fetching from one source), for others there is only
  a number.
* **Stability over time.** 22.7 hours and six queries. A shopfront can change its
  index supplier, and this measurement would not notice — that needs continuous
  recomputation, not a dated document.
* **Behaviour on other kinds of query.** All six references are short queries
  about existing objects. On long and rare queries indexes diverge more, and the
  shares would differ.
* **A separate direction for `yandex` on queries in its own language.** It was
  computed over all six references at once.
