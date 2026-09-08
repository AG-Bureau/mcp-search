# Engine measurement

> **One-off document:** a measurement · **Taken:** 2026-09-05
> The state of the engines on 5 September. Not updated: "how things stand now"
> lives in the live views, and this is "how it was then".

How the module works is described in its README; here there are only numbers and
names.

## Contents

How it was measured · five references · the resulting trust · substitution of the
subject · how the pool changed · what turned out to be wrong in earlier
conclusions.

## How it was measured

The prober asks one engine every ten seconds, round-robin, taking the list from
the metasearch registry — 60 entries in the general category. A full lap ≈ 10
minutes. Over two hours 675 probes accumulated across all 60 engines and all five
references.

A reference is a query whose correct answer is known in advance. A hit is counted
by domain; subdomains count.

| reference | must be found | confirmed outside the engines |
|---|---|---|
| a company with thin index coverage | its own domain | yes: 200, title matched |
| a widely known institution | its own domain | yes: 200, valid certificate |
| a programming language | `python.org` | yes: 200 |
| a public transport operator | its own domain | no: timeout from both machines |
| a well-known AI company | `openai.com` | no: 403 from a CDN on both machines |

**Trust is computed over the three CONFIRMED references only.** Otherwise "a hit"
would partly mean agreeing with the majority of the engines — the very engines
the reference is meant to judge.

**A known sampling bias.** The references are verified from our own machine, so
the set is skewed towards sites that admit data-centre addresses. Anything behind
a strict anti-bot service drops out systematically. "An engine is clean" means
"clean on the sites reachable from this machine", not "clean in general".

## Result: 7 clean out of 60

| engine | probes | hits | verdict |
|---|---|---|---|
| `abcnyheter` | 5 | 5 | clean |
| `bing` | 5 | 5 | clean |
| `privacywall` | 5 | 5 | clean |
| `vuhuv` | 5 | 5 | clean |
| `yandex` | 5 | 5 | clean |
| `zapmeta` | 5 | 5 | clean |
| `duckduckgo web` | 4 | 4 | clean |
| `yep` | 2 | 2 | candidate |
| `brave` | 1 | 1 | candidate |
| `360search` | 5 | 2 | substitutes |
| `mwmbl` | 3 | 1 | substitutes |
| `naver` | 5 | 1 | substitutes |
| `seznam` | 5 | 1 | substitutes |

In total: 7 clean, 3 candidates, 20 substituting, 29 unavailable — probes were
made and all of them ended in a refusal.

Refusal causes observed live: access denied (`searchtoday`, `infospace`,
`tusksearch`, `fastbot`, `fireball`), captcha (`startpage`, `qwant`), rate limit
(`brave`, `google cse`), a protocol error (`yahoo`), timeout (`yacy`).

`google cse` answers "Our systems have detected unusual traffic from your
network" after our own load test at 40 concurrent searches. Before the test it
gave 20 hits out of 20 and was the best in the pool.

## Substitution of the subject

One query, on which a specific official site must be found:

| engine | links | containing the query words | what it returned |
|---|---|---|---|
| `gabanza` | 30 | 30 | a cryptocurrency exchange's "official site" |
| `mwmbl` | 67 | 67 | a code repository whose title contains "official" |
| `360search` | 7 | 7 | an online casino's "official site" |
| `seznam` | 10 | 7 | a food delivery service |

All of them latched onto the words "official site" and led elsewhere. Formally
100% relevant: a check against the words of the query passes this every time.

**Not every miss is a substitution.** One engine did not return the canonical
domain but led to the institution's encyclopedia article: the subject is right,
the domain is not canonical. The two cannot be told apart by address alone — the
subject has to be judged. For a consumer that means a missed reference is a
signal to verify the source, not to discard it.

**The distribution is close to binary, but not strictly.** Of 48 substantive
answers there were 20 hits and 28 substitutions, and an engine usually either
never substitutes or almost never hits; two were in between. A check on the
statistics: with three observations an engine with a true rate of 50% looks
binary with probability 0.25, and at 70% with probability 0.37; over 19 engines
that would give an expected 5-7 binary ones, while 17 were observed. So the true
rates really are extreme, and substitution can be cut by pool composition.

The caveat: the calculation shows that the rates are extreme, and does NOT show
that any particular engine is clean — that needs observations of that engine.

## Spam that passes a word check

One engine scored 10 "on topic" out of 10 on a page of generated word salad that
quoted the query verbatim, at throwaway domains. Neither a word check nor overlap
with neighbours filters it out — only a reference does.

## How the pool changed, and what turned out to be wrong

Corrections to earlier statements that had become the grounds for decisions.

**`bing` was excluded as junk — wrong.** The grounds were three observations, in
two of which the results were off topic. Against the references it is 5 hits out
of 5, clean. Three observations against five references are not an argument; they
are different weight classes. The engine was returned.

**`mwmbl` was kept in reserve on the argument "its own index, low overlap with
neighbours — a property, not junk" — the argument is true and about something
else.** It explains the overlap, while the reference judges another thing: 1 hit
out of 3, with a substituted subject. Removed.

**`resulthunter` was the best in the 14 August measurement (18 results) and on 5
September returned zero in any language from a clean address.** It died silently,
with no error — which is why it went unnoticed for three weeks.

**Engines come back.** `brave`, `qwant` and `yep` moved from refusal to working
within a single day. A point measurement would have written each of them off for
good.
