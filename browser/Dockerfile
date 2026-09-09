# The browser sidecar of the search module — headless chromium + run-server.
#
# THE RECIPE IS KEPT VERBATIM rather than rebuilt in our own style, and the
# reason is mechanical: a playwright connection requires MATCHING MINOR versions
# of server and client, and two independently built browsers drift apart
# SILENTLY — until the first connection failure in production. Sameness here is a
# property to be HELD, not obtained once.
#
# THE MODULE RUNS ITS OWN SIDECAR rather than borrowing one. Sharing a browser
# with another service couples two lifetimes for no gain, and a docker network
# does not stretch between hosts, so a shared one would have to be published —
# which is a decision about exposure, not about browsers.
#
# THE VERSION IS PINNED HERE AND IN adapter/Dockerfile, AND A TEST KEEPS THE TWO
# EQUAL — `tests/test_adapter.py`, which ships and runs in the image: a
# divergence must go red in a test run rather than surface as a dropped
# connection in production.

# --- the verbatim shared recipe follows ---
# Playwright browser sidecar — headless chromium + run-server.
#
# Isolates the ~1.3GB chromium binary and the browser's OS libraries in an image
# of their own, so the adapter stays small. A client connects here via
# chromium.connect(<ws url>).
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
