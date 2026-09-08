# prober

Our process: continuously, one engine at a time, it asks every engine the
metasearch knows and writes a verdict to a database.

It exists because a point measurement misses the main thing: the set of living
engines changes within hours. An engine that was the best of the set returns
nothing weeks later without saying a word about it — and another comes back from
a refusal the same day.

It asks with REFERENCES — queries whose correct answer is known in advance and
confirmed outside the engines. A check against the words of the query will not do
here: it passes both spam that quotes the query back and results about a
different subject of the same name.

Whether a result is on topic is judged by THE SAME check that runs in production:
it is imported from the adapter rather than rewritten. A second implementation
would diverge from the first, and the prober would start measuring something
other than what the consumer receives.

The same process measures the READING references, and the read probe goes through
the adapter's door rather than through a copy of the reader in this process:
checking must happen from where the consumer calls from.

Two of the reading references are NEGATIVE — addresses that certainly do not
exist, from which the expected verdict is `stub`. Without them the shield
detector would degrade unnoticed, because it always says `clean`.

The pace is deliberately gentle. A burnt reference does not come back: some
thirty requests to one site within half an hour walk it down the ladder
`200 → 429 → total silence`. The address a module calls from is its scarcest
resource, and a prober that burns through its own reference sources lies exactly
where it is supposed to tell the truth.
