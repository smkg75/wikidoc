# 📚 wikidoc

Sort your documents into a corpus you can actually query.

wikidoc files your documents, sorts them by what they contain, and writes down
what each one is. Once that's done, you stop digging through folders — you ask.

## 🔍 What people use it for

- 🧾 **Answering a tax claim.** The tax office says a payment was never credited — pull the transfer proof, the return it belongs to, and the dates, in one question.
- 📊 **Feeding the accountant.** Every document for one company and one fiscal year, already in the right folder, before they have to ask twice.
- 🏠 **Property paperwork.** Rent receipts, charge statements, insurance, management reports — see what exists and what's missing.
- 📮 **Forwarding receipts.** Supplier invoices found and sent to your accounting inbox without opening a folder.
- 🧠 **Giving an assistant real context.** An LLM that knows which company existed when, who you worked with, and what you decided — because it's all written down.

## 🚀 First run

No config yet? It sets itself up:

1. Checks what your machine can do.
2. Reads a sample of your documents — company IDs, dates, who keeps sending you things.
3. Asks you only what it couldn't work out on its own: *"this company ID shows up in 47 files — whose is it?"*
4. Writes your config, in plain YAML you can read and edit.
5. Dry run — shows what it would move, moves nothing.

About ten minutes, once.

## ⚙️ How a pass works

Text gets extracted from your files first. Then a router sorts each document
into one of three lanes:

- **route** — a rule recognised it. The agent double-checks and files it.
- **propose** — sensitive, or unclear. The agent opens the file before doing anything.
- **residual** — a scan, or genuinely ambiguous. Vision agents read it properly.

Rules live in your `config.yaml`. After each pass the agent suggests new ones
from what it just learned, and you decide whether to keep them — so the same
kind of document doesn't need thinking about twice.

## 💸 What it costs

The scripts are free — they are Python. Walking the tree, hashing, extracting
text, matching rules, moving files: hundreds of documents, none of which land in
the conversation. What costs tokens is the reading an agent has to do itself.

| Cheapest first | Why |
|---|---|
| `collect` · `route` · `apply` | Python, no model in the loop |
| Asking the memory (`stats`, `show`, `find`) | a few lines back, whatever the corpus size |
| **route** lane | a rule already recognised it; the agent only confirms |
| **propose** lane | the agent opens the file and reads the text |
| **residual** lane | nothing matched, so the agent reads to find out |
| **vision** | page images — one scan can cost more than everything above it |

So the whole game is keeping files out of the vision lane:

- **Install `pypdf` and `pypdfium2`.** A PDF with a text layer then extracts in
  Python for nothing. Without them, that same file gets rendered and looked at.
- **Scans have no text layer**, so they always cost. Sitting on a pile of them?
  Give them their own pass and lower `batch_size` for it.
- **Promote rules.** Every rule that goes `active` moves a whole class of
  document from *residual* to *route*, for good.
- **Nothing is read twice.** A file already in `memory.jsonl` is skipped on its
  hash — the memory is what stops a corpus costing the same thing every pass.

## ⏱️ How to spend it

Two habits, from using it daily:

**One pass per context window, and start that window fresh.** A pass carries your
config, the ledger, hundreds of entries and every decision taken along the way.
Sharing that window with unrelated work makes both go badly. It is the same
reason vision is always delegated to subagents: so page images never reach the
main conversation.

**One pass per five-hour window, if you are working alongside it.** Claude and
ChatGPT subscriptions meter usage in rolling windows, and a full pass is one
large chunk of it. Run the pass, then get on with your day — leftovers are
written to `wiki/state.md`, and the next pass picks them up first.

Neither is enforced anywhere. It is just what the tool feels like when it is
used well.

## 📦 What goes where

| Place | Holds |
|---|---|
| **the skill** | the steps and the scripts — the same for everyone |
| **your workspace** | `config.yaml` · `memory.jsonl` · `logs/` · `wiki/` — yours, in plain text |

The workspace is where your setup, your history and everything the agent learned
about your documents lives. Copy it to another machine and you keep all of it.

## 🛟 Safety

- Files are sorted by what's inside them, not by their name.
- Nothing is ever deleted — things go to the bin, and you can put them back.
- Nothing goes to the bin at all until your config says what's protected.
- Sensitive documents are never proposed for deletion.
- Every move is checked afterwards.
- The agent reads a file before touching it.
- The first pass is a dry run.

Rules are optional. A config that says only where your documents live is a
working install — every file goes to the careful lane and the agent reads it.
You add a rule when you've decided the same thing twice, not before.

## 💻 Platforms

macOS, Windows and Linux. Needs Python 3.9+ and PyYAML.

Three more packages are optional and worth having: `pypdf` and `pypdfium2` read
PDFs, `send2trash` uses the real bin. Without them PDFs go to the careful lane
and removals land in a folder inside your workspace — slower, never destructive.

On macOS it also writes Finder comments and tags, so Spotlight finds a document
by what it says rather than what it's called. On Windows and Linux, search goes
through the memory instead (`memory.py find <text>`).
