<div align="center">

# search

**A self-hosted MCP server for web search that reports how much of each answer to believe**

[![MCP registry](https://img.shields.io/badge/MCP_registry-com.ag--bureau%2Fsearch-2ea44f?style=for-the-badge)](https://registry.modelcontextprotocol.io)
[![License](https://img.shields.io/badge/License-AGPL--3.0-E23A50?style=for-the-badge)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white&style=for-the-badge)](adapter/Dockerfile)
[![Self-hosted](https://img.shields.io/badge/Self--hosted-Docker_Compose-2496ED?logo=docker&logoColor=white&style=for-the-badge)](#-install)

[![Glama score](https://glama.ai/mcp/servers/AG-Bureau/mcp-search/badge)](https://glama.ai/mcp/servers/AG-Bureau/mcp-search)

</div>

---

A search tool fails in ways that look exactly like success. An engine answers with
somebody else's subject. A page returns text that is an anti-bot shield. Sixteen
sources turn out to be two engines counted eight times. None of that raises an
error, and the model on the other end builds on it.

**This server's job is to make those cases distinguishable, in fields you can
branch on.** It runs on your machine, over your own metasearch instance, with your
own model key — or none at all.


## 🔧 Tools

| Tool | What it does | Required | Notable options |
|---|---|---|---|
| `web_search` | Finds pages **and reads the top ones** — one call, links with their text | `query` | `read: false` for links only · `read_top` how many to read · `min_engines` to force breadth · `corroborate` |
| `web_read` | Reads pages by address: text, PDF, or a scan recognised by a vision model | `urls` | `mode: browser` for JS-rendered pages · `expect` to assert what must be there · `offset` to continue |
| `web_image_search` | Finds images: the address of the FILE and, separately, of the page it sits on | `query` | `max_results`, `page` |
| `web_screenshot` | A PNG of a page **plus its text from the same visit**, so the two can be cross-checked | `url` | `max_chars` for how much text · `full_page` · `expect` |
| `web_deep_search` | Composes its own queries, reads in waves, and answers from several sources — saying what it could not confirm | `question` | `waves` |

Full argument reference, response shapes and failure modes: **[HOWTO-CALL.md](HOWTO-CALL.md)**.

## 📦 Install

From an open repository page to a working answer. Nothing is assumed to be on
your disk already:

```bash
git clone https://github.com/AG-Bureau/mcp-search
cd mcp-search
cp .env.example .env
echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> .env
docker compose -f docker-compose.yml -f wiring/expose-localhost.yml up -d --build
curl -s http://127.0.0.1:8081/healthz
```

The fourth line is not decoration. Without a value in `SEARXNG_SECRET` the very
next command refuses — and that refusal is deliberate: with no key of its own the
metasearch does not fail, it comes up with a publicly known one from its image
template, silently.

The overlay publishes the port **on loopback only**. A published container port
does not go through the host firewall's usual chain, so exposing it more widely
is a separate, deliberate step — see [Deployment](#-deployment-and-exposure).

### Two transports

MCP has two, and they answer different questions. **HTTP** — the commands above —
is for a server that is already running somewhere. **stdio** is the protocol's
default: the client starts the server as a process and talks to it through the
pipes, which is how most desktop clients and wrappers work.

```bash
python adapter/server.py --stdio        # or MCP_TRANSPORT=stdio
```

One JSON-RPC object per line in, one answer per line out. The mode is chosen
explicitly and never guessed from whether a terminal is attached — that sign
merely sits next to the subject, and one day it answers for a case nobody meant.

In stdio mode **stdout is the protocol**: answers and nothing else, with the log
on stderr. One stray line of anything else breaks the client reading it.

The sidecars do not depend on the choice. Started by a client with no compose
project around it, the module still works and names what is missing instead of
pretending: the browser path reports `not_wired_up`, and the engine pool comes
back as `pool_source: seed`.

## ⚙️ Configuration

| Variable | Required | What it is |
|---|---|---|
| `SEARXNG_SECRET` | **yes** | Session key for the metasearch. Any long random string that is not from somebody's history. |
| `LLM_API_KEY` | no | Key for any OpenAI-compatible endpoint. **Secret.** |
| `LLM_API_BASE` | no | Base URL of that endpoint. Take it from your provider's documentation, not by analogy — the obvious guess can answer `429: Insufficient balance` because the subscription lives on a different path of the same domain. |
| `LLM_MODEL_TEXT` | no | Model that plans queries and composes answers. No default is shipped: a default would silently ask your provider for a model it may not have. |
| `LLM_MODEL_VISION` | no | Model that reads scanned PDFs. Unset, such documents return an explicit refusal naming the reason. |
| `LLM_DISABLE_THINKING` | no | Set for providers whose reasoning budget swallows the answer, leaving it empty with `finish_reason: length`. |
| `READ_CONTACT` | no | Contact placed in the `User-Agent` when fetching pages. Defaults to this repository; set your own if you run this at scale. |
| `READ_LANGUAGES` | no | `Accept-Language` when reading. Unset by default — the language of the pages you read is not ours to choose. |

Pacing, pool size and read limits have their own variables with measured
defaults; see [`.env.example`](.env.example), which explains each one where you
set it.

**A model key is optional.** Search, reading, image search and screenshots are
HTTP requests and spend no model tokens. A model is called in exactly two places,
and both are named in the answer: `web_deep_search`, and recognising a PDF with no
text layer — which happens only when you ask to read such a document, never behind
your back in a search.

## 🎯 What a bundled search tool does not do

**Cost you control.** One argument changes the answer by an order of magnitude:

| call | payload | time | model tokens |
|---|---|---|---|
| `read: false`, 6 links | 3.8 KB | 0.6 s | **0** |
| `read_top: 1`, 3 links | 8.8 KB | 1.6 s | **0** |
| `read_top: 3`, 6 links | 9.8 KB | 6.3 s | **0** |
| `web_deep_search` | full account | 36 s | 6 calls |

*Measured on one machine, one query. Take the shape, not the digits.*

**The engine list maintains itself.** A hand-written list goes stale in silence:
an engine that was the best returns nothing weeks later and says nothing about it.
Ours was revised three times in a single day — each revision against the previous
one, each correct on its own data. The problem was never the engines: a decision
freezes while observation goes on.

So the list is not written here. A prober asks every known engine, continuously,
with questions whose correct answer is known in advance, and the pool is the best
few by reference hit share — recomputed on its own. Verified by falsification: a
planted bad run took an engine out of the pool **with no code change**, and
restoring the run brought it back by itself.

Until enough observation accumulates, the pool is a seed list and every answer
says so in `pool_source`.

**Failure is distinguishable from success.** Four ways an engine can fail, and
what shows each:

| how it fails | what shows it |
|---|---|
| answers with a refusal: captcha, rate limit, ban | `unresponsive_engines` |
| silently returns nothing | the difference between `engines_asked` and `engines_answered` |
| answers a different question | `engines_irrelevant` — its results are already discarded |
| substitutes the subject with a better-indexed namesake | `engines_trust`, earned against references |

The same applies to reading: seven distinct outcomes, and a page that returned a
shield is `stub`, not empty text.

## 📖 How it works

- **[ALGORITHM.md](ALGORITHM.md)** — what happens, step by step, on each call.
- **[contracts/](contracts/)** — the call contracts, versioned separately from
  the code that implements them.
- **[measures/](measures/)** — dated measurements: which engines were alive, what
  the load ladder gives, what the transport change bought. Numbers, with what was
  measured and when.

## 🔒 Deployment and exposure

`wiring/expose-localhost.yml` publishes the adapter on `127.0.0.1` only. Anything
wider is a separate overlay, and its header says what to check first: Docker
passes traffic to published ports through `FORWARD` after DNAT, while the
firewall's own chain sits before its hooks — so a firewall that says "closed" can
be open to the internet on a published port.

A search server open to the outside is an open proxy that goes to the network in
the machine owner's name.

## ✅ Tests

```bash
IMAGE=ag-mod-search/adapter:0.2.1 bash tests/in-image.sh
```

Three suites — the protocol and search against a fake metasearch, reading against
a fake site, the computed pool against a database built in memory. **Not one of
them makes a single outbound request**: for reading that matters more than for
search, because a test that went to the internet would spend the very resource
the tool protects — the reputation of the one address it calls from.

They run inside the built image rather than on the machine where the code is
edited: the PDF parser lives in the image, and a suite run outside would skip
everything that touches it. The skip is not silent — the check goes red with a
note saying where to run it.

What these suites cannot check is written down in
[tests/README.md](tests/README.md).

## 🤝 Contributing

A capability, engine or heuristic is not accepted until its **reference
attribute** is declared — a property of the correct answer that the thing being
tested could not have told us itself — and a pool of checked questions is
attached. See [CONTRIBUTING](https://github.com/AG-Bureau/.github/blob/main/CONTRIBUTING.md).

## 📄 License

[GNU Affero General Public License v3.0](LICENSE). Run it, change it, build on
it. If you make it available to others OVER A NETWORK, the changes you made go
back out under the same licence — that is the one obligation, and running a
service counts as making it available.

For whoever cannot live with that clause, a commercial licence is a question to
ask rather than a fork to make.
