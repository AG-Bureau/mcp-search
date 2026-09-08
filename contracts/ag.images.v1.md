# `ag.images/1` — the image-search contract

> **Written from the code.** Every field name and limit below is taken from
> `adapter/server.py`. If the service stops matching this document, the service
> has diverged.

Status: **implemented**. MCP tool `web_image_search`, plain door `GET /ag/images`.

## Contents

Why images are a separate tool · TWO ADDRESSES on one result and why they must
not be confused · its own engine pool and its own reference · the shape of the
answer · the fan-out defect · what is deliberately absent.

## Why separate from `web_search`

Not for symmetry. Images have a different subject, different engines and a
different verification attribute, and merged into web search they would make web
search worse.

**Different engines.** The image category queries its own set, overlapping the
web set only partly. The pool is computed from observation SEPARATELY per
category: an engine's reference hit share on the web says nothing about its
images. A test enforces this directly — the web pool and the image pool must be
DIFFERENT objects, not one shared between them.

**A different reference.** For the web the attribute is the canonical domain of
the answer. For images it is the domain of the IMAGE SOURCE, not of the page the
image sits on. The difference is not theoretical: aggregators and social sites
display other people's files, and a check against the page domain would count a
hit where the picture belongs to somebody else.

Why this matters at all: asked for a company name, an image engine backed by a
museum catalogue answers with a self-portrait from 1878. By result count that
engine looks excellent.

## TWO ADDRESSES, AND THEY MUST NOT BE CONFUSED

This is the heart of the contract, and the likeliest future mistake by a caller.

| field | what it is |
|---|---|
| `image_url` | the address of the IMAGE FILE itself |
| `page_url` | the address of the page it was found on |
| `domain` | the domain of `image_url` — the source of the FILE |
| `page_domain` | the domain of `page_url` |

A result has **no `url` field, deliberately**. One `url` would make the caller
guess what they were given, and half the time they would guess wrong: what you
download is `image_url`, what you cite is `page_url`. A test checks the ABSENCE
of `url` as strictly as it checks the presence of the rest.

## The fan-out defect

Asking engines in a fan-out and merging their results by the PAGE address
collapses different images from one page into one, while one image found on three
pages counts as three.

Merging is done by `image_url` — by the file, not by the page. This is the same
rule as corroboration in web search being counted by the exact address rather
than by the site.

## The shape of the answer

Top level: `contract`, `ok`, `error`, `query`, `page`, `count`, `results[]`,
`engines_asked`, `engines_answered`, `engines_irrelevant`, `engines_skipped`,
`unresponsive_engines`, `search_aborted`, `engines_unasked`, `pool_source`,
`all_engines_on_topic`.

Per result: `image_url`, `page_url`, `domain`, `page_domain`, `thumbnail`,
`title`, `author`, `published`, `via`.

`pool_source` says whether the pool was COMPUTED from observation or handed back
as the seed list: when observations are insufficient the pool is not invented —
the seed is returned and it is named. The failure direction is the module's own:
no data means "not checked", never "sound".

An engine whose results are not about the query is discarded ENTIRELY and named
in `engines_irrelevant`. With images this is common, and a partial substitution
is no easier to cure than a complete one.

**Deferred, with the condition for coming back to it: image references.** Trust
is earned against references, and every reference the prober runs is a WEB query;
there are none for the image category, so no image engine has a label and the
image pool runs on its seed to this day — which `pool_source` states in every
answer rather than hiding. The two follow from one piece of work: when image
references exist, the pool becomes computed from observation and a trust flag can
join the one below. Until then, nothing here pretends to be measured.

**`all_engines_on_topic` IS NOT `all_engines_clean`, and the different name is
deliberate.** In web search that flag is about TRUST LABELS earned against
references. There are no image references yet, so no image engine carries a label,
and this flag says one narrower thing: no engine that answered was discarded as
off topic. One name over two meanings is a future mistake by the caller, so the
names differ — and when image references exist, a trust flag can be added beside
this one instead of quietly changing what it means.

## What is deliberately absent

**Images are not downloaded.** The tool returns addresses, not bytes. Fetching
other people's files is a different cost, a different ceiling and a different
responsibility; mixing it into search would make the cost of a call
unpredictable.

**Image content is not described.** The module has a vision model, but it is
called only to recognise scanned PDFs. Describing every image found would mean
paying a model for every result in a result set.

**No filters by size or licence.** Not every engine returns them, and a field
filled in for a third of the results reads as "the rest are unrestricted" —
exactly the blind spot `ag.search/1` is written against.
