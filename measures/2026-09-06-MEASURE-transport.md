# Changing transport, and what it brought back

> **One-off document:** a measurement · **Taken:** 2026-09-06
> What updating the metasearch image achieved. Not updated: this is a dated
> event, not a description of how something works.

## Contents

Why it was changed · what came back · what did not · a correction to an earlier
conclusion · what the measurement does not prove.

## Why it was changed

The older build used an ordinary http client, and its fingerprint was
recognisably non-browser. Measured at three levels:

- **headers:** all `Sec-Fetch-*` were absent, `Accept` went out as `*/*` instead
  of a browser's, while `Cache-Control: no-cache` and `DNT: 1` were sent, which
  browsers do not send at all. Meanwhile we presented as a browser that has sent
  `Sec-Fetch-*` since version 90 — a self-contradiction checkable in one line on
  the anti-bot side;
- **TLS:** shuffling the cipher list, the only defence of that build, changes JA3
  and **does not touch JA4**, where ciphers are sorted. Within one process JA3
  did not change either: four consecutive requests gave one hash;
- **HTTP/2:** the frame-order fingerprint was the library default, matching no
  browser, and the pseudo-header order contradicted the declared user agent.

The newer build uses `curl_cffi` with Chrome impersonation enabled **by
default**. A real browser fingerprint therefore comes with an image update, with
no second runtime and no result parsers of our own — an order of magnitude
cheaper than the headless browser that was the alternative.

## What came back

Five engines that had been refusing, at the same address and with the same
configuration:

| engine | before | after |
|---|---|---|
| `brave` | 429, too many requests | 21 links |
| `yep` | access denied | 20 links |
| `resulthunter` | zero in any language | 20 links |
| `duckduckgo` | connection error | 10 links |
| `yahoo` | protocol error | 7 links |

Two of them — `duckduckgo` and `yahoo` — were on the work list as manual repairs
(add headers, disable HTTP/2). The update did both by itself, which argues for
the order "transport first, manual work second": half the planned work fell away.

## What did not come back

| cause | engines |
|---|---|
| captcha | `startpage`, `qwant`, `gmx` |
| access denied | `fastbot`, `fireball`, `tusksearch`, `mojeek` |
| too many requests | `searchtoday`, `infospace`, `google cse` |
| timeout | `yacy` |
| empty with no reason | `ayo`, `360search` |

`google cse` keeps refusing from our own load test at 40 concurrent searches —
that is the reputation of an address, and a fingerprint does not cure it.

## A correction to an earlier conclusion

**The earlier statement, which had become the grounds for a decision:**
"`resulthunter` is a dead parser rather than a ban: zero in any language and from
a CLEAN address." On those grounds it was removed from the pool.

**Refuted:** after the transport change it returns 20 links. The parser was
alive.

**Where the reasoning went wrong, rather than the measurement.** The clean-machine
run was set up to test the guess "we were banned for frequency". It refuted that
correctly: an address with no requests in its history got the same refusal. But
the conclusion drawn went further — that the fault was in the engine. Both
machines carried THE SAME unusable fingerprint, and an experiment that varied the
address did not vary the fingerprint. The control was over one variable and the
conclusion was about another.

The same flaw applies to `brave` and `yep`, whose refusals were attributed to
address reputation.

## What the measurement does not prove

It does **not** show that the fingerprint was the only cause. Some of the engines
that came back might have recovered on their own — `brave`, `qwant` and `yep` had
already moved between refusing and working within a single day with no change
from us. There was no clean before-and-after over unchanged engines: hours passed
between the measurements.

What can be asserted: after the update more engines were reachable, and two
specific breakages (`duckduckgo` on headers, `yahoo` on HTTP/2) are explained by
transport independently — they would have been fixed by the same means by hand.
