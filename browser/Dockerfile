# The browser sidecar of the search module — headless chromium + run-server.
#
# THE RECIPE IS TAKEN VERBATIM from the sidecar recipe already in use elsewhere in
# the estate, and deliberately not rebuilt our own way. A playwright connection
# requires MATCHING MINOR versions of server and client, and two independently
# built browsers drift apart SILENTLY — until the first connection failure in
# production. Sameness here is a property to be HELD, not obtained once.
#
# WHY OUR OWN SIDECAR RATHER THAN A BRIDGE TO SOMEBODY ELSE'S. A build system's
# browser checks the layout of its own builds; ours reads other people's pages. A
# shared sidecar would be a coupling with no cause, and the machines differ —
# docker networks do not stretch between hosts.
#
# THE VERSION IS PINNED HERE AND IN adapter/Dockerfile, AND A STATIC CHECK KEEPS
# THE TWO EQUAL: a divergence must go red at build time rather than surface as a
# dropped connection in production.

# --- the verbatim shared recipe follows ---
# Playwright browser sidecar — headless chromium + run-server.
#
# Isolates the ~1.3GB chromium binary + browser OS libs OUT of the engine worker
# image. A client connects here via chromium.connect(<ws url>).
# The recipe is deliberate and proven to build: `playwright install chromium`
# plus an explicit apt library list (NOT
# `--with-deps`, which needs apt-get update/sudo and fails on slim). playwright is
# pinned to 1.49.1 to match the adapter's client — connect() requires the server
# and client minor versions to match. A static check keeps the two pins equal.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir playwright==1.49.1 \
    && playwright install chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 \
       libcairo2 libcups2 libdbus-1-3 libdrm2 libegl1 libgbm1 libglib2.0-0 libgtk-3-0 \
       libnspr4 libnss3 libpango-1.0-0 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 \
       libxdamage1 libxext6 libxfixes3 libxrandr2 libxshmfence1 fonts-noto-color-emoji \
       fonts-unifont libfontconfig1 libfreetype6 fonts-ipafont-gothic fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

EXPOSE 3000
# run-server exposes the Playwright protocol over ws://0.0.0.0:3000/. `python -m
# playwright` is the invocation that works when the module is restarted.
CMD ["python", "-m", "playwright", "run-server", "--port", "3000", "--host", "0.0.0.0"]
