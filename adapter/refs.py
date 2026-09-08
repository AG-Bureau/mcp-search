# -*- coding: utf-8 -*-
"""Reference queries — the single source of truth.

A reference is a query whose CORRECT ANSWER IS KNOWN IN ADVANCE: the canonical
domain of the subject. The share of hits against references is what the engine
pool is computed from — that is, who the module asks at all. These are not
illustrations in a document, they are the mechanism.

WHY THIS LIVES IN ONE FILE. The prober needs the queries and their expected
domains; the adapter needs the subset used for the object-substitution verdict.
Two copies of one set drift apart silently, and here the drift would be
especially quiet: change the references in one place and the verdict starts
selecting probes by strings nobody writes any more. Zero observations, everyone
"unverified", `all_engines_clean` false. The failure direction is safe, so
nobody gets alarmed — the showcase honestly reports "unverified" for months
while the real cause, two diverged copies, is named nowhere.

THE `confirmed` FLAG IS NOT ABOUT REFERENCE QUALITY, IT IS ABOUT HOW IT WAS
CHECKED. The substitution verdict counts only references whose domain was
confirmed OUTSIDE the engines, by fetching the site directly. Otherwise "hit the
reference" would partly mean "agreed with the majority of engines" — the very
engines the reference is meant to judge.

A NOTE ON LANGUAGE. Comments here are English; identifiers and the strings the
module RETURNS are not translated. Those are part of the response shape, and
changing them is a change of behaviour rather than of documentation.
"""
from __future__ import annotations

import hashlib

# Domains confirmed by direct fetch, outside the engines:
#   rikor-electronics.ru  200 and a matching title
#   sberbank.ru           200 and a certificate from the national CA
#   python.org            200
#   mosmetro.ru           times out from our network — NOT confirmed
#   openai.com            403 from Cloudflare on two different hosts — NOT confirmed
#
# A reference set mixing languages is deliberate: engines differ in which
# language they index well, and a single-language set would select engines by a
# question nobody asked.
REFERENCES: list[dict] = [
    {"q": "Рикор Электроникс",           # company with thin index coverage
     "expect": {"rikor-electronics.ru", "rikor.com"}, "confirmed": True},
    {"q": "Сбербанк официальный сайт",   # widely known institution
     "expect": {"sberbank.ru", "sber.ru", "sberbank.com"}, "confirmed": True},
    {"q": "Московский метрополитен",     # public institution
     "expect": {"mosmetro.ru"}, "confirmed": False},
    {"q": "Python programming language", # English, technical
     "expect": {"python.org"}, "confirmed": True},
    {"q": "OpenAI",                      # English, recent
     "expect": {"openai.com"}, "confirmed": False},
]

# The subset the substitution verdict is computed from. DERIVED, not written
# out: a hand-kept second list goes stale without a sound.
CONFIRMED: tuple[str, ...] = tuple(
    e["q"] for e in REFERENCES if e.get("confirmed"))


def generation_of(set_of: list[dict] | None = None) -> str:
    """Fingerprint of the reference SET. Changes by itself when the set changes.

    WHY A FINGERPRINT AND NOT A HAND-BUMPED NUMBER. A constant you must remember
    to increment when references change is a rule living in a person's memory.
    Forgetting it puts old and new probes under one generation — exactly the
    disease the generation is meant to cure, now with a field asserting the
    disease is absent.

    A fingerprint changes by construction; forgetting is impossible. Same rule as
    everywhere here: choose the direction in which forgetfulness is safe.
    """
    rows = sorted(e["q"] for e in (set_of if set_of is not None else REFERENCES))
    raw = "\n".join(rows).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]
