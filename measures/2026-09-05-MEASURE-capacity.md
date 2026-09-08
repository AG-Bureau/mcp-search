# Load measurement

> **One-off document:** a measurement · **Taken:** 2026-09-05
> What the adapter withstands and where it hits a limit. Not updated: once stale
> it is taken again as a new measurement, and this one stays as a trace of what
> the decisions stood on.

Machine: 2 cores, 3.8 GB.

## Contents

Three layers of load · where the real limit is · the profile of a deep search ·
what the load test cost · a ceiling found and removed.

## Three layers, measured separately

In volleys: N requests start at once on one signal.

| concurrent | adapter only | adapter + metasearch | a real search |
|---|---|---|---|
| 1 | 21 ms | 4 ms | 0.7 s |
| 10 | 5 ms, 1247 req/s | 14 ms, 480 req/s | 1.9 s for all ten |
| 25 | 14 ms, 1069 req/s | 33 ms, 457 req/s | — |
| 100 | 53 ms, 1083 req/s | 83 ms, 588 req/s | — |
| 400 | 198 ms, 1016 req/s | — | — |

Not one refusal at any level. Footprint: the adapter 20 MiB and one thread at
rest, the metasearch 157 MiB. No leak — threads are created per connection and
removed.

Under continuous load the adapter's CPU reaches 72% of one core at 1318 requests
per second, and memory does not grow at all. Sampled DURING the run and confirmed
by the cgroup counter: 10.6 s of CPU time over 15 s of wall clock.

**The number is understated:** the load generator ran on the same machine and ate
83% of the CPU, so half the machine was the instrument. The honest phrasing is
"not less than 1300 per second", not "1300".

## The limit is not ours

A ladder of real searches:

| concurrent | wall | who refused |
|---|---|---|
| 5 | 1.5 s | nobody |
| 10 | 2.2 s | `duckduckgo web` on 4 of 10 |
| 20 | 3.4 s | `duckduckgo web` 10, `privacywall` 6 |
| 40 | 6.1 s | `duckduckgo web` 40 of 40, `google cse` 28, `privacywall` 22 |

Across the whole run our machine spent 460 ms of CPU on the adapter and 4.7 s on
the metasearch — about 6% of one core out of 80 000 ms available.

**There is no limit in our own hardware.** What we hit is other people's
willingness to serve us, and we hit it at ten concurrent searches. Recovery after
an overload takes minutes: `duckduckgo web` came back in about 4 minutes,
`google cse` did not come back at all.

## The profile of a deep search

Eight queries in sequence, which is how it runs.

Result: **4.5 seconds, 32 links, zero refusals**, all eight answers entirely from
engines confirmed clean. Requests to other people's services: **10 for 8
searches.** A fan-out would have made it 40.

The rate limiter worked as load spreading rather than as an obstacle: the first
engine answered, on the next query it was still cooling, so the query went to a
second and then a third. The rotation happened by itself, and no engine received
more than three requests.

A ceiling that follows from the construction rather than from the measurement: 9
engines × 1 request per second = up to 9 outbound requests per second; one search
costs on average 1.25 requests, so ≈ 7 searches per second. A sequential deep
search cannot reach it — it produces at most 2 searches per second. Only
concurrent callers can.

## What the load test cost

The best engine of the pool was lost for hours: after the 40-concurrent run
`google cse` answers "unusual traffic from your network". The lesson is wider
than the case: **engines remember longer than a metasearch suspends.** There it
is minutes, at a large provider hours. A load probe against somebody else's
service is irreversible and cannot be scheduled like an ordinary measurement.

The line between a probe and ordinary work: if more requests leave in a minute
than ordinary work sends in an hour, it is a probe and it is agreed in advance.
The prober does not fall under this — one engine per ten minutes is background.

## The ceiling found, and its cause

The first run produced dips of almost exactly ~1000 ms from 25 concurrent
upwards, and lost connections at 50. The cause was neither our code nor
resources: the `listen()` queue in the standard library is five. Everything past
five was dropped silently by the kernel and the client waited for a
retransmission — hence the flat second.

After raising it to 128, throughput at a hundred concurrent grew from 7 to 588
requests per second and the refusals disappeared. A connection timeout was added
at the same time: under keep-alive, without one, a client that fell silent would
hold a thread forever.
