# measures

Dated measurements: how things stood on a given day. Kept apart from the maps of
the module deliberately — they are different genres with opposite rules.

**A map** (the README, the calling instructions, the algorithm) answers "how it
works" and must survive a change of engine set, a renamed machine and a grown
corpus. Counters, instance names and state-as-of-a-date have no place in it.

**A measurement** answers "how it was then". Counters, names and shares are its
direct duty: without them a measurement is not one. Such a document **is not
updated**: once stale it is taken again as a new one, while the old stays as a
trace of what decisions stood on.

A file name begins with a date so the order is visible in a listing without
reading anything.

A measurement whose numbers depend on a piece of text — a prompt, a tool
description — **names that text**. Otherwise the next change to the wording
quietly turns the numbers into a measurement of something else.

## Why here and not under `prober/`

The prober is one source of measurements but not the only one: load and transport
were measured without it entirely. Filing everything under its source would sort
the documents by instrument rather than by question, and in a month one would be
looking for a load measurement in the prober's directory.

The prober's own data is not here: it writes to a database on a volume, which
survives containers being recreated. What is here are the analyses a person made
on top of that data and on top of their own runs.
