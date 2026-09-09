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

**A FIELD EXISTS, ITS VALUE DOES NOT — and this has now bitten in four separate
places.** `width` and `height` returned `null` on every screenshot ever taken;
`corroborated_by_url` returned `1` where nobody else had been asked;
`total_chars` reported our own ceiling as the size of somebody else's page; an
empty `trouble` said "checked, all well" about a refusal where nothing had been
checked. In each case the SHAPE promised data the module never had, and the
caller could not tell "we did not measure" from "there is nothing to measure".

The rule: a field is worth its place only if it can carry an answer. If the value
cannot be established, the field must be ABSENT (and the absence documented), or
carry a name that states what it really is — a floor, a claim, an engine's
history. A name that promises more than the value delivers is a lie that no test
detects, because the field is dutifully present and dutifully typed.

**A MEASUREMENT THAT DEPENDS ON WORDING IS TAKEN ON THE FINAL WORDING, OR
DECLARED SINGLE-USE AT THE MOMENT IT IS TAKEN.** Five of our eleven measurements
carry "these numbers are void": the prompts were translated after the numbers
were collected, so each file documents an edition of the code that no longer
exists. Two paths led to this rule on the same day — counting the caveats, and an
owner asking what one such file is still for — and a rule reached twice from
different directions is worth more than one argued once.

Two corollaries, both learned from the same file. **Probing a provider's model
names tests OUR GUESSES, not their catalogue** — ask the provider's own index
instead. And **sightedness is not visible in a model's name**: concluding "this
model is text-only" from its name is inference from a neighbouring sign, the same
error as reading a green guard as a sound file.

**A TRIAL IS NEVER STRICTER THAN THE IMAGINATION OF WHOEVER BUILT IT.** Our fake
site served what was asked for, at the declared length, uncompressed, at once. It
could produce a redirect and a stub — the failures we had already thought of —
and not one case of LEGITIMATE PATHOLOGY: nothing compressed, nothing arriving a
byte at a time, no stream cut in the middle, no header lying about its own
length. That is why three reviews, five suites and seven acceptance probes all
came back green over four defects that a trap found in ten minutes.

The mechanism is not that the trap was cleverer. It asked questions we had not
asked ourselves, and a test bench assembled from our own expectations cannot
contain what we did not expect.

**So a bench is stocked from OTHER PEOPLE'S failures, not from our own
predictions.** Every pathology a live site actually commits — slow delivery,
compression, truncation, a header that lies — earns a permanent fixture the day
it is met. What we imagine belongs there too, but it is the smaller half, and it
is the half that is already covered by the code being written to it.

**A SHARED PARSE PROVES SAMENESS ONLY FROM THE LAYER WHERE IT STANDS.** Two
doors were made to read booleans through one function, and they still disagreed
about one word: the caller wrote `?read=`, and by the time the shared code ran,
the query parser had already dropped the empty parameter — so an argument that
WAS written arrived as one that was never mentioned, and took the expensive
default. The difference had moved above the common code, into the layer where an
argument is still becoming an argument.

Hence: check at the ENTRANCE, not at the parse. A test that calls the shared
function proves the function; only a test that goes in through each door proves
the doors. The same shape as a guard greening on a comment instead of on code —
the instrument was sound and was aimed one layer away from the defect.

**OUR OWN REVIEW CHECKS THAT THE TREE AGREES WITH ITSELF; SOMEBODY ELSE'S CALL
CHECKS THE ASSUMPTIONS THAT WERE NEVER WRITTEN DOWN IN IT.** Not one check ever
asked "what if a boolean arrives as a string", because inside the tree a boolean
is a boolean by construction — the assumption was nowhere, so there was nothing
to refute. Five consumers on the tool found in a day what five guards had not
found in a day of reading the same lines. Consequence: a real caller is not a
late stage of testing but a DIFFERENT INSTRUMENT, and the earliest one that can
see this class at all. Until there is one, say so rather than counting the guards
as coverage.

**A SIGN AVAILABLE ONLY AT A COST THE CONSUMER CANNOT PAY DOES NOT EXIST FOR
THEM.** Seven reading outcomes were separated, and the separation was counted as
delivered. Measured at the consumer: the one field they would have branched on
arrives only together with the reading itself — four to ten seconds and some
seven and a half thousand characters — and then sinks under the text they
truncate. The distinction existed in the answer and not in their reach, which is
the same as not existing. A capability is finished when the sign that carries it
is affordable ON THE PATH THE CONSUMER ACTUALLY TAKES, not when the field is
present.

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
