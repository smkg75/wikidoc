---
name: wikidoc
description: Sort your documents into a corpus you can query.
disable-model-invocation: true
---

No `config.yaml` in the workspace (`$WIKIDOC_HOME`, default `~/.wikidoc`)? Read [`SETUP.md`](SETUP.md), run it, come back here.

Filing is the means; a corpus that answers questions is the end. Every pass therefore ends with each file carrying what it is, and the **ledger** carrying why that was decided.

## Invariants

- **Evidence** decides. What the bytes say classifies a document; the filename is hearsay. A rule that reads only a name is capped at the `propose` **lane** by `route.py`.
- Read a file before the gesture that touches it. The `propose` and `residual` lanes exist for exactly that, and a summary you did not verify stays a summary, not a decision.
- Removal goes to the OS bin, so every gesture is reversible. A sensitive document is never routed automatically and never binned — `apply.py` re-reads the file and refuses the line. It can still be filed and renamed, which is the point of sorting it.
- Duplicates are byte-identical or they are not duplicates. Same text, different bytes is a re-download: diff in full before keeping one.
- A move counts once it is re-stat'ed at its destination.

## A pass

Scripts live in `scripts/`, next to this file. Long output goes to `batch/logs/` — read it paginated rather than re-running a script to see it again.

### 1. Prepare

`python3 scripts/prepare.py [LIMIT]` selects the backlog, chunks it, extracts page-1 text, renders text-less scans to PNG, and flags byte-identical duplicates.

Done when `sum_chunks == batch`, and `opaque` and `duplicates` are numbers you can explain from the corpus rather than from a failure.

### 2. Route

`python3 scripts/route.py` puts every file in exactly one lane: `route` (a rule recognised it), `propose` (sensitive, duplicated, claimed by two entities at once, or matched on weak evidence), `residual` (nothing to reason on, or nothing matched), `skip` (this content is already in the ledger).

Documents name several entities at once — an invoice from your company also carries your name — so the strongest **evidence** decides: an identifier read in the content beats a name printed on the page, which beats where the file happens to sit. The order entities appear in `config.yaml` decides nothing. Two entities tied at the top means the document belongs to both as far as the bytes go, and that goes to `propose`.

Done when `counts` covers every prepared file and no lane count surprises you. `--dry-run` prints the same routing and writes nothing.

### 3. Decide

The `route` lane needs confirming, the `propose` lane needs opening, the `residual` lane needs eyes.

Chunks are cut by `prepare.py` before anything is routed, so one chunk mixes all three lanes. Read them however suits this session — directly, through subagents, or through a workflow — and answer for **every path in the chunk**, not only its residual files: `review.py` sends back any chunk with a gap.

Whatever reads them writes **one JSON file per chunk** into `batch/vision/chunk-NNN.json`, a list of:

```json
{"path": "/absolute/path", "desc": "one to three sentences, in the corpus language",
 "lu": "text|image|render", "date_doc": "2024-03-01", "ids": {"siren": ["123456789"]},
 "suggest": {"decision": "move|tag|trash|keep|rename", "destination": "...", "tag": "facture"}}
```

Every entry needs `lu` filled in and a `desc` that says something the filename does not — those, plus the corpus language, are what `review.py` grades. It then names the chunks to run again — as a **new** run, since a resume replays the cached empty answer.

Route an entity by the identifiers read in the content, never by an agent's impression. A date absent from the document is not invented. Ask the user the questions the evidence cannot settle, at the moment they come up.

Done when `review.py` flags no chunk, every file has a decision, and each trash candidate has been confirmed against its content — not its name.

### 4. Apply

Write `plan.json` (`moves`, `trash`, `tags`, `keep` — the shape is in `apply.py`'s docstring), then `python3 scripts/apply.py plan.json` for the **dry run**, and `--execute` once its output is what you meant. A `dst` ending in `/` files into that folder; anything else is the full path, which is how a rename is written.

Done when the dry run and the execution report the same counts, `failed` is 0, and every reported failure has been settled rather than skipped.

### 5. Close

`python3 scripts/close.py` writes the ledger line for every remaining file — including the ones nothing happened to, whose reason is the thing the old pipeline never recorded — appends the journal, and clears the bench.

The journal takes only what the ledger cannot compute: arbitrations, incidents, promoted rules, open questions. Pass them as typed lines (`decision` · `rule` · `incident` · `question`) via `batch/journal.md` or `--note`.

Done when `close.py` reports `bench: empty`; a populated `batch/` means the pass was interrupted.

## What is fixed and what is yours

The scripts hold the parts where being wrong is expensive and judgement adds nothing: the gesture on disk, the state, the OS plumbing, and the **guards** — already-in-ledger, sensitive, byte-identical, two entities at once. Those are not configurable, and they run whatever `config.yaml` says.

Everything about *your* documents is config, and all of it is optional. A config naming only `workspace:` and `root:` is a working install: no rule fires, every file reaches the `residual` lane, and you read them. Rules are an accelerator for what you have already decided twice — never a prerequisite. `verify.py --bare` runs the whole pipeline on exactly that config.

One consequence worth knowing: **nothing goes to the bin until `sensitive:` says what is protected.** A fresh install files and tags, and refuses every trash line in a plan.

## After the pass

A rule earns its way in; it is never handed over on the strength of looking right. Write it as `status: shadow` and leave it alone: `route.py` evaluates it on every pass without ever applying it, and `close.py` compares what it proposed against what was actually decided. Passes, hits, agreements and **disagreements** accumulate in the rule itself.

Only a rule with enough passes, enough hits and zero disagreement is put to the user at close, with that record attached. One that keeps diverging is wrong about their documents — rewrite it or drop it. A rule still at 0 hits after ten passes is dead weight.

When writing a candidate, take the discriminating phrases `close.py` measured — present in every file that went to one destination, in no other file of the pass — over a phrase that merely sounds right. "compte de gérance" reads like a rule for a rental property until it also matches the flat you rent yourself.

`wiki/` holds what the corpus cannot say about itself: who is who, which entity was live in which period, which arbitration was made and why. The files answer factual questions through their annotations; the wiki answers relational ones. Start at `wiki/index.md` and split it as it grows.
