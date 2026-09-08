# PROTOCOL — the rules a module here obeys

Rules for the module layer. Contracts of individual CAPABILITIES live in
`contracts/` and are a different genre: those are versioned agreements about
calling, this is about construction.

Every rule below was paid for by a defect. They are stated as prohibitions rather
than as advice, because a rule that forbids nothing concrete is a preference.

## What a module is

A self-contained capability that any caller can attach. In this repository a
module is a directory containing:

1. **manifest.yaml** — the name, the kind, what it provides, how it is attached
   (environment variables, endpoint, network), the health check.
2. **Its own deployment** — a compose file, if the module needs a process. That
   file is self-contained: it creates its own network and depends on nothing
   external. Otherwise the module works on one machine only and is not a module.
3. **Its own tests** — how to check the module apart from anything that calls it.
4. **README.md** — why the module exists.

A caller knows a module ONLY through its manifest: an endpoint and environment
variables. No imports of module code into the caller, ever — that is what makes
the layer a layer.

## Failure directions

**A health signal has a chosen FAILURE DIRECTION: no data means "not checked",
never "sound".** A module that returns a trust, quality or freshness signal must
degrade towards caution. A broken observation switches verification ON at the
consumer, not off.

**A probe that returns ZERO must first show that its subject existed.** "Nothing
was found" and "there was nowhere to look" give the same answer and call for
different actions. The rule came from four independent cases in one day: zero
rows from a table join, zero files in a checked area, zero leaks from a container
that never came up, and zero files in a section that turned out to be an access
denial with no key header. In every case the command worked and the result meant
something other than what was assumed.

**A tool whose check always answers "clean" must have a NEGATIVE probe.** A
detector that only looks for the good degrades unnoticed: it returns the same
green verdict when working and when broken, and nothing tells them apart. In this
module the stub rejection is guarded by a reference page that certainly does NOT
exist, from which the expected verdict is `stub` — it goes red exactly when
rejection stops working. This continues the rule about zero: there the probe must
show that the subject existed, here that the subject is still caught.

**Absence of data and a refusal by the source are DIFFERENT outcomes and must not
be merged.** "Not asked" is about us, "asked and not let in" is about them; if
the second kind suddenly multiplies, that is news about our address rather than
about their breakage.

**A declared but unwired capability must REFUSE, not substitute an easier path.**
A silent substitution is worse than a refusal: the caller assumes they got what
was declared and builds a conclusion on it. And the STATE of such a path lives in
a live view, not in the text of a rule — otherwise a line saying "not wired up"
becomes a lie in seven documents at once, without giving a sign in any of them.

## Checks that outlive their subject

**A check that searches for a NAME must first prove the name still exists.** A
negative check of the form "this file does not contain X" passes when X was
merely renamed, and from that moment it goes green over any code, guarding
nothing. The positive half comes FIRST: the subject must be found where it
belongs, and only then is a second declaration of it forbidden.

**A check must show that the FILE WAS OPENED, not merely that the subject
existed.** Green over an unopened file is indistinguishable from green over a
sound one. Two guards once took a path as "root / entry" while part of what they
checked lay one level above — and both honestly reported zero over files they
never opened. The repair introduced the same class a second time, because
`.parent` of a relative path gives the path itself. So it is a property, not an
accident: a probe that counts what it FOUND must separately name what it
EXAMINED, or an empty sweep and a clean sweep give the same answer.

**A name that reads as an identifier in one place and as a string in another is a
separate class of failure, and tools do not see it.** A keyword argument is a
NAME to a tokeniser and a DICTIONARY KEY by meaning: renaming identifiers changes
the key of an answer while a string lookup beside it stays as it was. Three
checks placed BEFORE such an edit saw nothing, because they worked at the level
of names while the other half of the link lived at the level of strings. The cure
is not care but removing the ambiguity: a dictionary key is written as a STRING,
never as a keyword argument.

**A name from the past in a record of an incident is not a dangling reference,
and "fixing" it is forbidden.** If the record describes a case in which something
happened UNDER THAT NAME, substituting the new name falsifies the record. A tool
cannot tell "the name changed" from "the name was that then" — it only sees
whether the name is in the code. So: if the rule does not depend on the name,
remove the name from the text entirely; if it does, put a word beside it saying
the name is from the past, or the next guard or the next person will "fix" it
back.

## What a check cannot see

**A string that appears only under ACCUMULATED STATE is checked separately from a
fresh install.** A field born after hours of a background process running does
not exist at the moment somebody deploys the module and looks at the output: the
defect is invisible exactly when it is looked for, and visible when nobody is
looking. "Deployed clean and everything was clean" then means "checked half of
it".

**A mean over a document hides a fragment.** A check that judges a docstring as a
whole passes a single line of stale values among thirty English ones. Anything
measuring a share must also look line by line, or it answers a question about the
average instead of about the text.

**A comment written in ANOTHER LANGUAGE INSIDE a string literal is invisible to
the outer language's parser.** An SQL schema in triple quotes carried three
comments a Python-comment counter reported as zero: it looked for lines starting
with `#` and there were `--`. The same applies to any embedded language — yaml in
a string, a shell fragment, a query. A guard must know which languages it has
nested inside it, or it honestly reports zero over text it cannot see.

**AN INSTALLATION INSTRUCTION CANNOT BE CHECKED BY ANYONE WHO HAS ALREADY
INSTALLED.** They perform the missing steps by reflex and never notice the
absence. A published `## Install` began at `cp .env.example .env` — no clone, no
`cd`: written from inside a directory the reader does not have. The instruction
was reviewed twice and passed both times, because both reviewers had cloned by
hand BEFORE opening it. The reader's first action is an open repository page, and
the check starts there: from the first action of the reader, not from a directory
on disk. The same principle as checking from where the consumer calls, applied to
text.

## Where to look, and from where

**Check FROM WHERE THE CONSUMER CALLS FROM. A probe physically incapable of
seeing the failure is not a check but the appearance of one.** A probe from
another position answers a question nobody asked, and its green means nothing. A
deployment once lost its port publication, the door died from outside, and the
check ran `docker exec … urlopen(127.0.0.1)` — from inside the same machine,
where that failure is invisible by construction — and reported "deployed and
alive". The consumer found the breakage, not us. Consequence for tests: a live
suite must have a half that runs FROM THE CONSUMER'S MACHINE, and skipping that
half must be declared as "not checked" rather than pass silently.

**Look at the number of LIVE CALLS to a mechanism BEFORE going deeper into it,
not after.** A view once showed `searches 32, with_corroboration 0` — the
corroboration mechanism had never been called in production. A day had gone into
it. The cause of the zero was external and known, and the number was sitting in
our own view, created for exactly the question "is anything calling us". Nobody
looked, including the person who created the view.

## Truth in one place

**The truth about one subject lives in ONE place.** A manifest does not repeat
what is set by code or by a deployment file: a list copied into a second place
goes stale silently and starts lying about the very thing it is read for.

**Renaming a project costs not in references but in names DERIVED from its
name.** References are visible to a search; derived names are visible nowhere —
they come into existence at start-up. A volume name assembled from a compose
project name is one: a rename orphans the data together with its whole history,
and you learn about it from an empty answer rather than from an error.

## Lies, and which kind is worse

**A lie that looks like PERMISSION is more dangerous than a lie that looks like
an error.** An error gets checked; a permission gets acted on. A manifest once
declared `ports: []` — "nothing is published". In fact the port was published and
protected by a firewall rule. A reader who believed the empty list would conclude
there was nothing to protect and remove the rule as redundant. Statements that
relieve the reader of an obligation are checked more strictly than the rest.

**A secret must never become a value that SOMEBODY ELSE'S error handler can
display.** The rule "do not print secrets" is weak: it forbids printing them
yourself and does not forbid putting them where another program will print them.
A shell variable name written in a non-ASCII script was rejected by the shell —
which printed THE WHOLE ASSIGNMENT LINE, value included, in its error text. The
secret was disclosed not by intent but by a typo plus somebody else's handler.

Dangerous places where a value is displayed by no decision of ours: shell
variables, command-line arguments (`ps` sees them), exception texts, a library's
debug output. The right way to move a secret is AS A STREAM from file to file, so
that the value never becomes a string anybody knows how to show.
