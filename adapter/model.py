# -*- coding: utf-8 -*-
"""Model client: the only place the module talks to a model.

WHY A SEARCH MODULE HAS A MODEL AT ALL. The rest of the module follows one rule
— mechanical work here, judgement at the caller. Deep search breaks it on
purpose: composing queries, judging whether a page is about the right subject
and summarising an answer are judgement, and without a model there is no deep
search. A service that needs someone else's orchestrator to produce a full
answer is not self-contained.

THE MODEL IS CHOSEN BY WHOEVER INSTALLS THE MODULE, NOT BY US. Provider, base
URL, key and model names come from LLM_* variables; none of them are in the
code, defaults included. Anyone whose provider speaks the OpenAI-compatible
protocol can plug in their own key — requiring a particular vendor would mean
requiring a relationship with a vendor the operator did not choose.

WHAT THAT IMPLIES ABOUT PRICE, AND IT IS PERMANENT RATHER THAN TEMPORARY. No
tariff is written here, and none is guessed: with an arbitrary provider the
tariffs are unknown, so `cost_usd: null` is not an omission but the only correct
answer. What the module always reports is TOKENS and the MODEL NAME. Prices given
in LLM_PRICE_* are multiplied out — arithmetic on the operator's own numbers,
which is a different thing from holding a price registry. Do not "fix" the empty
case with invented numbers: an invented price is worse than a missing one,
because someone will cite it.

VISION IS ASKED FOR SEPARATELY from text. Not every provider has a model that
can look at an image, and where it exists it is priced differently. A rejection
of the form `model does not exist` means "not in your plan", not "no such model"
— by probing names those two are indistinguishable, by reading the provider's
documentation they are not.

REASONING IS DISABLED EXPLICITLY on every call. Without it the answer is eaten
by the reasoning budget and `content` arrives EMPTY — and empty output reads
from the outside as "the model saw nothing", while in fact it said nothing out
loud. Two different events under one observable sign.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# Provider is not hard-wired: base URL and key come from the environment. The
# protocol is OpenAI-compatible, which is the de-facto common language.
API_BASE = (os.environ.get("LLM_API_BASE") or "").strip().rstrip("/")
API_KEY = (os.environ.get("LLM_API_KEY") or "").strip()

# NO DEFAULT MODEL NAMES, and this matters more than it looks. A hard-wired name
# under someone else's key would silently ask for a model the provider does not
# have, and the provider's refusal would read as "the service is broken". Empty
# means the capability declares itself unconfigured and names the variable.
MODEL_TEXT = (os.environ.get("LLM_MODEL_TEXT") or "").strip()
MODEL_VISION = (os.environ.get("LLM_MODEL_VISION") or "").strip()

# PROVIDER NAME IS A FACT TAKEN FROM THE ADDRESS, not a guess about it. The host
# of the base URL is unambiguous, needs no registry, and does not lie when the
# provider changes. Empty while the address is unset: "we do not know" is more
# honest than an invented name, because money is computed from that name.
#
# THE PARENTHESES ARE LOAD-BEARING. A conditional expression binds looser than
# `or`, so `explicit or from_url if API_BASE else ""` reads as
# `(explicit or from_url) if API_BASE else ""` — an EXPLICIT provider name would
# be ignored until a base URL is set.
PROVIDER = ((os.environ.get("LLM_PROVIDER") or "").strip()
             or (urllib.parse.urlparse(API_BASE).hostname or "" if API_BASE else ""))

# A DIALECT SWITCH, OFF BY DEFAULT, WITH A MEASURED REASON TO TURN IT ON.
#
# `thinking: {type: disabled}` is not part of the OpenAI protocol. A strict
# provider answers `400: Unrecognized request argument supplied: thinking`, which
# a caller reads as a broken product. So it is not sent unless asked for, and the
# module speaks plain OpenAI out of the box.
#
# WHAT TURNING IT ON BUYS, measured on a provider that reasons by default, on a
# question that requires reasoning:
#
#     max_tokens   with the field        without it
#        80        305 chars,  62 tok    0 chars, finish_reason=length,  80 tok
#       200        261 chars,  49 tok    0 chars, finish_reason=length, 200 tok
#       600        314 chars,  54 tok    273 chars, finish_reason=length, 600 tok
#
# Ten times the tokens for the same answer, paid by whoever runs it. On a short
# question there is no difference at all.
#
# THE OLD DANGER IS GONE AND THE COMMENT MUST NOT OUTLIVE IT. Without the field a
# short budget returns an EMPTY answer — but no longer an indistinguishable one:
# `finish_reason` arrives as `length`, and the three-state truncation flag carries
# it out to `answer_truncated`. The reader can tell "said nothing out loud" from
# "found nothing" without this switch.
DISABLE_THINKING = (os.environ.get("LLM_DISABLE_THINKING") or "").strip().lower() \
    in ("1", "true", "yes")

TIMEOUT_S = float(os.environ.get("MODEL_TIMEOUT_S") or "120")
MAX_TOKENS = int(os.environ.get("MODEL_MAX_TOKENS") or "4000")

# PRICES DO NOT LIVE HERE, and that is a requirement rather than an omission.
# Two places that know tariffs diverge on the first edit. This module states
# WHICH model it called and counts TOKENS; converting tokens to money is done by
# whoever holds the price registry.
#
# If prices are supplied anyway, they are used — but ONLY then. Unset means None,
# NOT ZERO.
#
# The difference between None and zero is load-bearing: zero makes spending look
# FREE. "We do not know the price" and "it cost nothing" are opposite pieces of
# news under one number, and someone will build a spending report on the zero.
_price_in = os.environ.get("LLM_PRICE_INPUT_PER_1K")
_price_out = os.environ.get("LLM_PRICE_OUTPUT_PER_1K")
PRICE_IN = float(_price_in) if _price_in else None
PRICE_OUT = float(_price_out) if _price_out else None


def available(vision: bool = False) -> tuple[bool, str]:
    """Can a model be called at all. Failure direction: an unset setting is said
    out loud here, not discovered by the first live call in the middle of work.

    VISION IS ASKED FOR SEPARATELY. Not every provider has a model that can look
    at an image, and it is priced differently; an operator may configure text and
    not vision. Scan recognition must then refuse WITH A REASON rather than hand
    a picture to a text model and get an opaque refusal from the provider.
    """
    if not API_KEY:
        return False, "the model key is not set (LLM_API_KEY)"
    if not API_BASE:
        return False, "the model API base URL is not set (LLM_API_BASE)"
    if vision and not MODEL_VISION:
        return False, ("no vision model is configured (LLM_MODEL_VISION); "
                       "scan recognition is impossible without one")
    if not vision and not MODEL_TEXT:
        return False, "the model name is not set (LLM_MODEL_TEXT)"
    return True, ""


def call(messages: list[dict], model: str = "", max_out: int = 0,
            timeout: float = 0) -> dict:
    """One model call. Never raises.

    AN EMPTY ANSWER IS NOT "THE MODEL FOUND NOTHING". With a provider that
    reasons by default, a short budget is spent on reasoning and `content`
    arrives empty. What tells the two apart is `finish_reason: length`, carried
    out as `answer_truncated`; and what makes the reasoning cheap is
    LLM_DISABLE_THINKING, off by default because the field is a dialect (see
    above, with the measurement).

    `usage.thinking_disabled` says which way this call went, so that a reader of
    the ledger is not left guessing why one call cost ten times another.
    """
    may, why = available(vision=bool(model) and model == MODEL_VISION)
    if not may:
        return {"ok": False, "content": "", "error": why, "usage": {}}
    body = {
        "model": (model or MODEL_TEXT),
        "max_tokens": (max_out or MAX_TOKENS),
        "messages": messages,
    }
    # A DIALECT FIELD IS SENT ONLY WHEN ASKED FOR. Some providers accept a
    # `thinking` switch and answer with empty content when reasoning is left on;
    # others reject the field outright with
    # `400: Unrecognized request argument supplied`. Sending it unconditionally
    # turns "any OpenAI-compatible provider" into "one provider", and the caller
    # reads the refusal as a broken product rather than as an unknown argument.
    if DISABLE_THINKING:
        body["thinking"] = {"type": "disabled"}
    t0 = time.perf_counter()
    req = urllib.request.Request(
        API_BASE + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=(timeout or TIMEOUT_S)) as o:
            d = json.loads(o.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # The error body is read on purpose: the provider puts the reason
        # there and the status code does not carry it. THE KEY CANNOT LEAK INTO
        # THE MESSAGE — it travels in a header, not in the body.
        try:
            text = e.read(4000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        return {"ok": False, "content": "",
                "error": f"the model answered {e.code}: {text[:200]}", "usage": {}}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": "",
                "error": f"{type(e).__name__}: {e}"[:200], "usage": {}}

    ms = int((time.perf_counter() - t0) * 1000)
    choices = d.get("choices") or []
    message = (choices[0].get("message") if choices else {}) or {}
    content = message.get("content") or ""
    # AN ANSWER CUT OFF BY THE TOKEN CEILING LOOKS FINISHED FROM THE OUTSIDE.
    # The text stops mid-sentence and the caller takes it for a complete answer —
    # a refusal indistinguishable from success, in the two most expensive places
    # this module has: summarising an answer and recognising scanned pages.
    finish_reason_raw = str((choices[0].get("finish_reason") if choices else "") or "")
    # NO SIGN MEANS "NOT CHECKED", NOT "COMPLETE". Comparing `== "length"`
    # yields False when the field is ABSENT, and `answer_truncated: false` then
    # reads as "checked, the answer is whole". Gateways that omit finish_reason,
    # or spell it their own way, would put us back where the field started.
    #
    # Three states rather than two: True cut off, False complete, None not told.
    _CUT_OFF_WORDS = {"length", "max_tokens", "max_output_tokens", "token_limit"}
    _COMPLETE_WORDS = {"stop", "end_turn", "eos", "complete", "finished"}
    if finish_reason_raw in _CUT_OFF_WORDS:
        cut_off = True
    elif finish_reason_raw in _COMPLETE_WORDS:
        cut_off = False
    else:
        cut_off = None
    u = d.get("usage") or {}
    tokens_in, tokens_out = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    usage = {
        # PROVIDER COMES FROM THE ADDRESS AND IS NEVER HARD-WIRED. A literal
        # here would report "provider: glm" while the key points at another
        # vendor entirely, and whoever holds the price registry would apply the
        # wrong tariff. An invented provider name is the same offence as an
        # invented price, one step earlier: the price is derived from the name.
        "provider": PROVIDER, "model": body["model"],
        "input_tokens": tokens_in, "output_tokens": tokens_out,
        # NOT REPORTED IS NOT ZERO. Only some providers report a cache hit; a
        # literal 0 would read as "the cache did not work" instead of "this
        # provider does not say". Same rule as the truncation flag.
        "cached_tokens": (int(u["prompt_cache_hit_tokens"])
                          if "prompt_cache_hit_tokens" in u else None),
        # None rather than 0, per the note above; and beside it, where the
        # answer came from, so the reader need not guess why it is empty.
        "cost_usd": (round(tokens_in / 1000 * PRICE_IN + tokens_out / 1000 * PRICE_OUT, 6)
                     if (PRICE_IN is not None and PRICE_OUT is not None) else None),
        "cost_source": ("prices were given to the module" if PRICE_IN is not None
                        else "prices are not ours: compute from the tokens where "
                             "the registry is"),
        "elapsed_ms": ms,
        "finish_reason": finish_reason_raw,
        "truncated": cut_off,
    }
    # THE LEDGER ENTRY IS WRITTEN HERE, NOT AT THE CALLER. The cost is incurred
    # in this function, and this is the last point where it cannot be lost on the
    # way. A call with an empty answer is recorded too: its tokens were charged
    # exactly like a useful one.
    usage["ledger_written"] = record_usage(usage)
    usage["thinking_disabled"] = "thinking" in body
    if not content:
        # AN EMPTY ANSWER IS ITS OWN OUTCOME, not "the model found nothing".
        # The distinction is mandatory: the first is fixed by call settings, the
        # second cannot be fixed at all. The reasoning flag is reported beside
        # it, because it is the hint that tells them apart.
        reasoned = bool(message.get("reasoning_content"))
        return {"ok": False, "content": "", "usage": usage,
                "error": ("the model returned an empty answer"
                          + (" with non-empty reasoning: the budget went into "
                             "reasoning — raise max_tokens or set "
                             "LLM_DISABLE_THINKING=1 if your provider knows the "
                             "field" if reasoned or cut_off else ""))}
    return {"ok": True, "content": content, "usage": usage, "error": "",
            # A cut-off answer is SUCCESS with a caveat, not a failure: the
            # text exists and is useful, it merely stops early. Returning it as a
            # failure would throw away work already paid for.
            #
            # None means "the provider did not say". For a consumer that is NOT
            # the same as False, and the two must not be merged.
            "truncated": cut_off}


# --- Vision -----------------------------------------------------------------
# A separate door rather than "call with pictures": vision has its own message
# shape, its own answer ceiling and its own level of trust in the result.
# Merging them into one function loses the last of those.

# A PROMPT IS BEHAVIOUR, NOT DOCUMENTATION. Rewriting the wording changes what
# the model does, so the accuracy figures quoted below hold for THIS wording and
# for no other: a changed prompt requires a new measurement before those numbers
# may be repeated.
RECOGNITION_PROMPT = (
    "These are the pages of a document, one image per page, in order. Transcribe "
    "ALL visible text exactly as it stands.\n"
    "Rules:\n"
    "1. Do not summarise, shorten or translate anything. Only what is written on "
    "the page.\n"
    "2. Reading order is a human one: column by column, top to bottom.\n"
    "3. Give TABLES row by row, separating columns with a tab character. An empty "
    "cell is an empty field between tabs — do not shift the row.\n"
    "4. Copy numbers, article codes and units character by character. Do not "
    "round, do not correct, do not add units that are not there.\n"
    "5. Mark an unreadable place as [?] and move on. DO NOT GUESS: an invented "
    "number is worse than a missing one, because a gap is visible and an "
    "invention is not.\n"
    "6. Begin each page with a line of the form === PAGE N ===\n"
    "No explanations before or after — the text of the pages only.")


def recognise(images: list[bytes], hint: str = "",
               model: str = "", max_out: int = 0, timeout: float = 0) -> dict:
    """Read pages that are images. Returns the same shape as `call`.

    WHAT THIS TEXT IS, AND HOW IT DIFFERS FROM A TEXT LAYER. The layer is what
    the file says; recognised text is what a model believes the file says. Over 17
    pages of product catalogues checked against an independent reference, of 170
    numbers the stronger vision model returns 165 (97%) and the faster one 159
    (94%) at a third of the time.

    94% is therefore a CEILING, not an unlucky run, and the answer must show it: a
    recognised number may not be quoted as exact.
    """
    if not images:
        return {"ok": False, "content": "", "error": "there is nothing to recognise",
                "usage": {}}
    parts: list[dict] = []
    for frame in images:
        # The type is taken FROM THE BYTES, not from what we were told. A file
        # signature does not lie, whereas a passed-in flag drifts away from the
        # content silently — and then the provider gets a JPEG labelled PNG and
        # refuses in some opaque way.
        kind = "image/jpeg" if frame[:3] == b"\xff\xd8\xff" else "image/png"
        parts.append({"type": "image_url", "image_url": {
            "url": f"data:{kind};base64,"
                   + base64.b64encode(frame).decode("ascii")}})
    parts.append({"type": "text", "text": hint or RECOGNITION_PROMPT})
    return call([{"role": "user", "content": parts}],
                   model=(model or MODEL_VISION), max_out=max_out,
                   timeout=timeout)


# --- Spend ledger ------------------------------------------------------------
# SPENDING MUST BE VISIBLE, and returning it in the response is not enough: a
# response is seen by one caller and forgotten. The precedent this is written
# against is expensive — a recognition tool that returned usage and wrote it
# nowhere: eleven calls went past accounting and nobody noticed, because silence
# from accounting is indistinguishable from no calls at all.
#
# THE LEDGER HOLDS TOKENS AND THE MODEL NAME, NOT MONEY. Same requirement as
# above: one price registry per system, and a second place that knows tariffs
# diverges from the first on the first edit. We record everything a price is
# computed from; whoever holds the registry computes it.
#
# ONE LINE PER CALL, APPENDED. Not a running total: a total cannot be
# re-checked, whereas lines show which call cost what and when. The file grows
# by about 120 bytes per call — a hundred thousand calls give 12 MB.
LEDGER_PATH = (os.environ.get("SPEND_LEDGER") or "").strip()
_ledger_lock = __import__("threading").Lock()


def record_usage(u: dict) -> bool:
    """Record one call in the ledger. Returns WHETHER IT WAS WRITTEN.

    The return value is not decoration: it travels into the response as
    `ledger_written`, which makes a silent accounting gap impossible by
    construction. A failed write does NOT fail the call — the cost is already
    incurred, and losing the answer over it buys nothing.
    """
    if not LEDGER_PATH or not u:
        return False
    line = json.dumps({
        "ts": int(time.time()), "provider": u.get("provider") or "",
        "model": u.get("model") or "", "in": int(u.get("input_tokens") or 0),
        "out": int(u.get("output_tokens") or 0),
        # None here means the provider does not report a cache hit at all; the
        # summary keeps that apart from a reported zero.
        "cached": (None if u.get("cached_tokens") is None
                   else int(u["cached_tokens"])),
        "ms": int(u.get("elapsed_ms") or 0),
    }, ensure_ascii=False)
    try:
        with _ledger_lock:
            with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def usage_summary() -> dict:
    """Ledger summary for the views (`GET /stats`). Never raises.

    BROKEN LINES ARE COUNTED SEPARATELY, not skipped in silence: a ledger that
    quietly drops lines reports less spending than actually happened — it lies in
    the most convenient direction.
    """
    summary = {"ledger": LEDGER_PATH or "(not set)", "calls": 0, "broken_lines": 0,
            "since": None, "until": None, "by_model": [], "cost_usd": None,
            "cost_source": ("prices were given to the module" if PRICE_IN is not None
                            else "prices are not ours: compute from the tokens "
                                 "where the registry is"),
            "error": ""}
    if not LEDGER_PATH:
        return dict(summary, error="the ledger is not configured (SPEND_LEDGER)")
    by_model: dict[str, dict] = {}
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            for raw_val in f:
                raw_val = raw_val.strip()
                if not raw_val:
                    continue
                try:
                    entry = json.loads(raw_val)
                except Exception:  # noqa: BLE001
                    summary["broken_lines"] += 1
                    continue
                summary["calls"] += 1
                t = int(entry.get("ts") or 0)
                summary["since"] = t if summary["since"] is None else min(summary["since"], t)
                summary["until"] = t if summary["until"] is None else max(summary["until"], t)
                name = entry.get("model") or "?"
                c = by_model.setdefault(name, {
                    "model": name, "provider": entry.get("provider") or "",
                    "calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "cached_tokens": 0, "cached_reported": False})
                c["calls"] += 1
                c["input_tokens"] += int(entry.get("in") or 0)
                c["output_tokens"] += int(entry.get("out") or 0)
                if entry.get("cached") is not None:
                    c["cached_tokens"] += int(entry["cached"])
                    c["cached_reported"] = True
    except FileNotFoundError:
        return dict(summary, error="the ledger does not exist yet: no model calls")
    except Exception as e:  # noqa: BLE001
        return dict(summary, error=f"{type(e).__name__}: {e}"[:160])
    summary["by_model"] = sorted(by_model.values(), key=lambda x: -x["calls"])
    if PRICE_IN is not None and PRICE_OUT is not None:
        summary["cost_usd"] = round(sum(
            c["input_tokens"] / 1000 * PRICE_IN
            + c["output_tokens"] / 1000 * PRICE_OUT for c in summary["by_model"]), 6)
    return summary
