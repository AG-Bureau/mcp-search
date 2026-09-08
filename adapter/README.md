# adapter

Our process: from outside, this IS the module. The metasearch behind it is
internal.

It holds the doors over ONE implementation of each capability — MCP for tool
clients, plain HTTP contracts, a health check for orchestration, and the
observation views. A second implementation "for another protocol" would diverge
from the first at the first edit.

Everything the metasearch cannot do lives here: the order engines are asked in,
the rate limiter, discarding a substituted result set, the trust label, page
reading with stub rejection, scan recognition, deep search, and call accounting.

**Four dependencies, each taken on a measurement, and the image weighs 419 MB.**
`pypdf` parses PDFs — a standard-library parser was written first and gave
something unreadable on four files out of five. `pypdfium2` with `pillow` renders
scanned pages to raster; the pair costs +29 MB, measured on two builds of the
same Dockerfile rather than estimated from the wheel. `playwright` is the browser
client, without the browser: the chromium binary lives in the sidecar.

Everything else — the server, the HTTP layer, the reader, the pool — is standard
library. A web framework would have to be upgraded and have its CVEs closed while
nothing but routing was used from it. The arguments for all four, with the
weights, are in `adapter/Dockerfile`.

The reading policy lives in a file of its own (`reader.py`) because per-domain
pacing, stub rejection and the chunked cursor belong beside the measurements that
explain them, rather than smeared across routes.
