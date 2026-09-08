# tests — checking the module

Three suites ship, and they differ by what they need in order to run.

**The protocol and search**, against a FAKE metasearch on a local port:
handshake, notifications, batched requests, result parsing, the poll order, the
rate limiter, discarding a substituted result set, both health modes, call
accounting.

**Reading**, against a fake site that serves not "a page" but CLASSES OF DEFECT,
each measured on live sources: an anti-bot shield, a wordy "this publication is
unavailable" stub, a live article with the word `captcha` in its own chrome, a
legacy encoding with no declaration, a JS application, PDFs with and without a
text layer.

**The computed engine pool**, over a database built in memory. The main test
there is FALSIFICATION rather than "the pool was computed": a mechanism that
always computes the same thing passes any check of the form "the pool is
non-empty" and never notices an engine degrading — the very thing it exists for.

```bash
IMAGE=ag-mod-search/adapter:0.2.1 bash tests/in-image.sh
```

They run inside the module image, because the PDF parser lives there. Run
outside, the PDF checks go red with a note saying where to run them — the skip is
never silent, because a green run that checked nothing is exactly the defect
these suites look for.

**Not one of them makes an outbound request.** For reading that matters more than
for search: a test that went to the internet would spend the very resource the
tool protects — the reputation of the single address it calls from.

## What these suites do NOT check

* **the quality of the model's answers** — the model is faked here; quality is a
  matter for measurement on real pages;
* **the real engine pool** — these run on a database built for the test, not on
  the prober's;
* **load** and **the browser sidecar**.

"There are tests" and "it is verified" are different claims.

The checks are written to fail when a PROPERTY is lost, not when a wording
changes. A test that pins down a judgement instead of a fact sooner or later
starts defending a mistake.
