# What to parse PDFs with, and what counts as a reference

> **One-off document:** a measurement · **Taken:** 2026-09-06
> A comparison of two extraction paths over five live files, and the conclusion
> about what kind of reference is fit for reading PDFs. Not updated: once stale
> it is taken again.

## Contents

Why it was measured · five files · the standard library against `pypdf` · the
price of a dependency · an escape repair that comes free · the main point:
character accuracy and structural accuracy diverge · what follows for the
reference.

## Why it was measured

Page reading was finished and declared working, but answered `forbidden` on PDFs
— and of 194 addresses in the corpus 27 were PDFs, one in seven. Before closing
the gap it had to be decided whether the standard library suffices. The module's
"no dependencies" rule is not dogma, but it is also not something to break on a
feeling.

## Five files

Two English documents with a text layer, two national standards in Cyrillic, and
a flooring catalogue — the last turned out to be the hardest and the most useful:
460 streams, 125 fonts, 58 images, four ToUnicode maps for the whole document.

## The standard library against `pypdf`

A stdlib parser was written specifically for the measurement: FlateDecode streams
through `zlib`, `BT..ET` blocks, the `Tj/TJ` operators. It does not parse
ToUnicode maps or CID fonts — which is exactly where it was expected to fail.

| file | stdlib | pypdf |
|---|---|---|
| a conference paper | 3 628 characters, words fused: `1IntroductionRecurrentneural…` | **39 510**, readable |
| a long specification | 470 971, but with `\x00\x04\x00\x05` mixed in | **461 291**, clean |
| a national standard | **41 characters** | **52 858**, readable |
| another national standard | 2 217 characters of control codes | **49 859**, readable |
| the flooring catalogue | 4 621 characters of rubbish `\x1b\x1c\x1a…` | **5 752**, readable |

**The conclusion is unambiguous: stdlib will not do.** On four files out of five
it produces something unreadable, and worst of all on documents with font subsets
— exactly what will be read in practice.

## The price of the dependency

`pypdf` is **3.6 MiB of pure Python**, with no binary parts. Against an image of
187 MB that is two per cent.

`fontTools`, which pypdf asks for on complex CFF Type1 fonts, was **checked and
NOT taken**: on the catalogue it changed nothing (131 undecoded escapes with it
and without it) and weighs **27 MB** — seven times the parser itself. A library
asking for something in a warning is not the same as a benefit; that is settled
by measurement.

## The escape repair comes free

On the catalogue pypdf left 131 sequences of the form `/uni041C` — a font with no
map, a glyph name instead of a letter. One substitution fixes it
(`/uniXXXX` → `chr(0xXXXX)`):

    before: 'COLLECTION 2019\n/uni041C/uni0414/uni0424 • /uni041B/uni0410…'
    after:  'COLLECTION 2019\nMDF • LAMINATE FLOORING • WALL PANELS…'

Undecoded remaining: **zero**. What 27 MB were being asked for is solved here by
a regular expression.

## THE MAIN POINT: CHARACTER ACCURACY AND STRUCTURAL ACCURACY ARE DIFFERENT QUANTITIES

Confirmed literally on the catalogue. Page 3, pypdf extraction, characters
perfect:

    lamella size           <- three labels in a row
    pack
    pack area
    1382x195 mm            <- three values in a row
    8 pcs
    2.156 m2
    Deck Oak               <- the object all of it belongs to
    …
    chamfer                <- and here the relation is severed entirely:
    8                         "chamfer 8 mm" spread across three lines
    mm

**The PDF was read character by character flawlessly and lost the relation
"object ↔ its properties" all the same.** A check of the form "text was
extracted" misses that by construction: plenty of characters, correct encoding,
no rubbish.

A separate recognition measurement over other catalogues shows the same shape: 44
pages out of 48 clean by characters, while on dense pages the reading order
collapses and an article number drifts away from its specification.

## What follows for the reference

**A reference for reading PDFs must check a RELATION, not extraction.** Not "is
`1382x195` in the text" but "does that size stand within sight of the label and
of the collection name". Otherwise we get a green light on exactly the case PDF
support was added for.

A second rule from the same place: **a reference must contain only what is
physically observable in the source.** In a recognition measurement the strict
score came out at 69-76% and was depressed by a contaminated reference — the
expected list held article numbers that were not printed on the page at all,
synthesised from a database. The parser "failed to find" what was never there;
the honest score over what was printed is 85-90%. A reference not taken from the
source measures the wrong thing, and measures it downwards — which makes you
repair what works.

A third, for later, if OCR is ever added: **an extra language pack is not
neutral.** A `rus+eng` configuration on English catalogues substitutes Cyrillic
homoglyphs into English text (`Case`→`Сазе`, `420mm`→`З20тт`); plain `eng` is
2-3 percentage points better on every metric. The language is chosen by
measurement, not by caution.

## The decision

Take `pypdf` plus the escape substitution. Do not take `fontTools`. Build the PDF
reference on the relation "object ↔ property" rather than on the presence of a
substring, and take it only from what is printed on the page.
