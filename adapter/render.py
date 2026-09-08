# -*- coding: utf-8 -*-
"""Rendering PDF pages to raster. The only place that knows about pixels.

A NOTE ON LANGUAGE. Comments here are English; the strings the module RETURNS
are not translated. They are part of the response shape — contracts, tests and
callers match on them — so changing them is a change of behaviour, not of
documentation, and belongs to a contract version rather than to a comment pass.

WHY pypdfium2 AND NOT PyMuPDF. Both render PDF pages; the choice was made on
three grounds, in descending weight:

  · five times lighter: +13 MB to the image against +64 MB;
  · a licence that leaves a door open: BSD-3-Clause and Apache-2.0 against
    AGPL-3.0. This module is itself AGPL-3.0, so the network clause is no longer
    the objection it was — what remains is that somebody else's AGPL code cannot
    be relicensed by us, and a dependency under it would block any commercial
    licence we might grant. A permissive dependency keeps that choice ours;
  · the rendering path was already exercised on 73 pages of two real product
    catalogues before it landed here.
"""
from __future__ import annotations

import io
import os

try:
    import pypdfium2 as _pdfium
    HAS_RENDERER = True
except Exception:  # noqa: BLE001
    HAS_RENDERER = False

DPI_DEFAULT = int(os.environ.get("RENDER_DPI") or "300")
DPI_FLOOR = int(os.environ.get("RENDER_DPI_MIN") or "72")
# Downscale ladder. Steps rather than smooth division: a step gives a result you
# can name — "dropped two steps" is legible where "dropped to 137 dpi" is not.
#
# The ladder starts at 300 because that is the resolution the rendering path was
# proven at. It is a starting point, not a measured optimum, and is meant to be
# re-measured against recognition quality.
DPI_LADDER = (300, 220, 160, 110, 72)

# IMAGE FORMAT. Measured on a product catalogue, pages 1-4:
#     300 dpi   PNG 2630..10433 KB   JPEG q85 451..1683 KB   4.4-6.3x smaller
#     160 dpi   PNG 1208..4398 KB    JPEG q85 191..674 KB    4.3-6.7x smaller
# So under one payload ceiling JPEG buys roughly two dpi steps.
#
# ONLY THE ARITHMETIC IS MEASURED, NOT THE QUALITY. Whether two extra steps beat
# JPEG artefacts on small print takes a model call to establish, and that has not
# been done. PNG is therefore the default: it is the side that introduces no
# unmeasured loss. JPEG is written and enabled by one line of configuration —
# enabling it before the measurement would trade a known quantity for a guess.
IMAGE_FORMAT = (os.environ.get("RENDER_FORMAT") or "png").strip().lower()
JPEG_QUALITY = int(os.environ.get("RENDER_JPEG_QUALITY") or "85")
# Payload ceiling. base64 inflates bytes by about 4/3, and the ENCODED size is
# what must be counted: that is what the provider accepts, not the raw bytes.
BASE64_CEILING = int(os.environ.get("RENDER_MAX_B64") or str(5 * 1024 * 1024))


def pages_to_raster(body: bytes, how_many: int = 6,
                   dpi: int = 0) -> tuple[list[bytes], dict]:
    """(list of images, details). Never raises.

    AUTO-DOWNSCALE: render, measure the base64 size, and if it does not fit, take
    the next step down.

    THE DETAILS ARE RETURNED ALWAYS, and that is the point of this function as
    much as the pixels are. When downscaling happens silently, "the model read it
    badly" and "we handed it 72 dpi instead of 300" become INDISTINGUISHABLE —
    two different pieces of news that need opposite fixes.

    Requested dpi, actual dpi and the number of steps down are the only thing
    that separates a bad model from a bad picture.
    """
    details = {"dpi_requested": dpi or DPI_DEFAULT, "dpi_actual": None,
                "dpi_steps_down": 0, "pages_rendered": 0, "error": ""}
    if not HAS_RENDERER:
        return [], dict(details, error="the PDF renderer is not installed in the image")
    initial = dpi or DPI_DEFAULT
    ladder = [d for d in DPI_LADDER if d <= initial and d >= DPI_FLOOR] or [DPI_FLOOR]
    try:
        doc = _pdfium.PdfDocument(body)
        total = len(doc)
    except Exception as e:  # noqa: BLE001
        return [], dict(details, error=f"{type(e).__name__}: {e}"[:160])

    page_count = min(how_many, total)
    for step, d in enumerate(ladder):
        images, size = [], 0
        try:
            for i in range(page_count):
                page_res = doc[i]
                # scale = dpi / 72: the PDF unit is the typographic point.
                raster = page_res.render(scale=d / 72.0)
                picture = raster.to_pil()
                buf = io.BytesIO()
                if IMAGE_FORMAT in ("jpeg", "jpg"):
                    # convert("RGB") is required: the raster carries an alpha
                    # channel, which JPEG does not know and fails to save.
                    picture.convert("RGB").save(buf, format="JPEG",
                                                 quality=JPEG_QUALITY,
                                                 optimize=True)
                else:
                    picture.save(buf, format="PNG")
                b = buf.getvalue()
                images.append(b)
                size += (len(b) + 2) // 3 * 4      # size once base64-encoded
        except Exception as e:  # noqa: BLE001
            return [], dict(details, error=f"{type(e).__name__}: {e}"[:160])
        if size <= BASE64_CEILING or d == ladder[-1]:
            return images, dict(details, dpi_actual=d, dpi_steps_down=step,
                                  pages_rendered=len(images), format=IMAGE_FORMAT,
                                  payload_b64_bytes=size,
                                  # Hit the floor and still over the ceiling:
                                  # we hand it over but SAY SO, otherwise the
                                  # provider gets a truncated payload and the
                                  # refusal reads as a model failure.
                                  over_limit=size > BASE64_CEILING)
    return [], dict(details, error="no rung of the ladder fitted within the limit")
