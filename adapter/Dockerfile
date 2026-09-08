# The adapter of the web-search module: MCP + ag.search/1 over a metasearch.
#
# Pinned by tag AND digest rather than latest: a `latest` tag can already point at
# a build other than the one running, and a single pull would then swap the engine
# under production silently. The same rule applies to the base image — it is about
# reproducibility, not about a particular image.
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

# THE ONE DEPENDENCY, AND IT IS TAKEN ON A MEASUREMENT RATHER THAN FOR
# CONVENIENCE. The server, the reader and the pool are written on the standard
# library: the module has two clients — containers of our own — and a web
# framework for their sake would have to be upgraded and have its CVEs closed
# while nothing but routing is used from it.
#
# PDF survives that rule, and here is why. A stdlib parser was WRITTEN and
# MEASURED (measures/2026-09-06-MEASURE-pdf-extraction.md): on four files out of
# five it produces something unreadable, and on documents with font subsets it
# gives 41 characters instead of 52 858, control codes instead of letters. Keeping
# the rule would cost a capability that does not work.
#
# The price is honest: pypdf is 3.6 MiB of pure Python with no binary parts, two
# per cent of the image. `fontTools`, which pypdf asks for in its warnings, was
# CHECKED AND NOT TAKEN: it changed nothing on the hard file and weighs 27 MB,
# seven times the parser itself. A library asking for something in a warning is
# not the same as a benefit.
#
# Pinning the version is mandatory for the same reason as pinning the image: a
# parser of somebody else's format changes behaviour between versions silently,
# and our references are built on its output.
RUN pip install --no-cache-dir "pypdf==6.17.0"

# A RENDERER OF PDF PAGES TO RASTER — for scans that have no text layer. pypdf
# does not read those and cannot: there is nothing to read, there is a picture.
#
# The library is CHOSEN BETWEEN TWO, not taken as the first to hand. The weights
# of the wheels themselves:
#     pypdfium2   BSD-3-Clause, Apache-2.0          +13 MB
#     pymupdf     AGPL-3.0 or Artifex Commercial    +64 MB
# pypdfium2 is taken: five times lighter and on a calmer licence.
#
# PILLOW COMES ATTACHED, AND THAT IS EASY TO MISS WHEN COUNTING. pypdfium2
# produces a raster but cannot save it: `to_pil()` requires Pillow. Thirteen
# megabytes is the price of pypdfium2 ALONE, not of the capability.
#
# THE PRICE IS DECLARED AS A DECISION AND NOT ALLOWED TO SEEP IN, so it is
# MEASURED rather than estimated. Two builds of this same Dockerfile differing in
# one line:
#     without the renderer   390 MB
#     with the renderer      419 MB      (+29, not +13)
RUN pip install --no-cache-dir "pypdfium2==4.30.0" "pillow==11.0.0"

# THE BROWSER CLIENT, WITHOUT THE BROWSER ITSELF. `playwright install` is
# deliberately NOT called here: the chromium binary (~1.3 GB) lives in the
# sidecar, and only the protocol client is needed here. The version is pinned to
# the sidecar's — the connection requires matching MINOR versions, and a
# divergence breaks it SILENTLY. Verifying the versions costs nothing and happens
# in /healthz; a divergence costs production.
RUN pip install --no-cache-dir "playwright==1.49.1"

COPY server.py /app/server.py
# The page reader (ag.read/1). A file of its own rather than a section of
# server.py: the reading policy — per-domain pacing, stub rejection, the chunked
# cursor — lives beside the measurements that explain it instead of being smeared
# across routes.
COPY reader.py /app/reader.py
# The engine pool. One module shared by the prober and the adapter: the first
# computes it, the second reads it. Two implementations of "whom to ask" would
# diverge at the first edit — exactly the defect for whose sake the engine-
# selection policy was moved into the module in the first place.
COPY pool.py /app/pool.py
# The references — a single source for the prober and for the adapter's views. A
# file of their own precisely because two copies of one set drift apart silently.
COPY refs.py /app/refs.py
# Rendering PDF pages to raster. A file of its own for the same reason as the
# reader: it is the only place where the module knows about rasters, and the
# argument for the library choice belongs beside the code rather than in the
# commit history.
COPY render.py /app/render.py
# The model client and the deep-search orchestration, in separate files: the
# client knows about the provider and nothing about search, the orchestration the
# other way round. Merging them would create a place where changing the model
# touches the logic of the waves.
COPY model.py /app/model.py
COPY deep.py /app/deep.py

# Not root: the process goes to the network and parses somebody else's JSON —
# exactly the work that needs no extra privileges.
RUN useradd --system --no-create-home --uid 10001 ag
# The directory for the prober database is created IN THE IMAGE and given to the
# same user: docker inherits the owner of a named volume from the image
# directory, and without this the volume goes to root and the process running as
# ag cannot create the database file.
RUN mkdir -p /data && chown ag:ag /data
# The same for the spend ledger — the only place the adapter WRITES to. Without
# this line the volume would go to root, the write would fail, and accounting
# would honestly report `ledger_written: false` forever.
RUN mkdir -p /spend && chown ag:ag /spend
USER ag

ENV PYTHONUNBUFFERED=1
EXPOSE 8081
CMD ["python", "/app/server.py"]
