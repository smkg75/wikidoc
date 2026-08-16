---
name: wikidoc
description: Sort your documents into a corpus you can query.
disable-model-invocation: true
---

No `config.yaml` in the workspace (`$WIKIDOC_HOME`, default `~/.wikidoc`)? Read [`SETUP.md`](SETUP.md), run it, come back here.

Filing is the means; a corpus that answers questions is the end. Every pass ends with each file carrying what it is, and `memory.jsonl` carrying why that was decided. Query it anytime: `memory.py stats` · `show <path|md5>` · `find <term>` — memory answers, it never selects.

Scripts live in `scripts/`, next to this file. Resolve this file's directory once and invoke every script by its **absolute path** (`python3 <skill-dir>/scripts/route.py`) — a relative invocation from the wrong cwd fails halfway through a pass. Long output goes to `bench/logs/`; read it paginated rather than re-running a script to see it again.

## Invariants

- **Evidence** decides. Strength 3 = a validated identifier read in the content, 2 = a name read in the content, 1 = path or filename only — hearsay. A rule matching only strength-1 conditions is capped at `propose`.
- Read a file before the gesture that touches it. The `propose` and `residual` triages exist for exactly that.
- Removal goes to the OS bin — every gesture is reversible. A sensitive document is never routed automatically and never trashed unread: `apply.py` re-reads the file itself and refuses the entry. It can still be filed and renamed, which is the point of sorting it. The one refusal that lifts is deduplication: name a `keeper` and apply re-hashes both files at the moment of the gesture — same bytes, keeper present, keeper not itself being binned, or the refusal stands. The guard protects the content, not the copy count.
- Duplicates are byte-identical or they are not duplicates. Zero-byte files group with nothing — emptiness is not identity. Same text, different bytes is a re-download: diff in full before keeping one.
- A move counts once it is re-stat'ed at its destination.

## The working file: `bench/routing.json`

One entry per selected file; each actor writes only its columns; empty columns are the remaining work. `bench/` is the pass's working directory — archived to `logs/<pass>/` at Learn, never deleted.

| columns | writer | step |
|---|---|---|
| `path size mtime md5 ext pages text truncated prose needs_vision render ids dates doc_year duplicate_of known_as known_desc opaque error` | collect.py | ① |
| `text lu` (needs_vision entries only) | vision agent | ② |
| `triage why guards rule entity strength destination shadow` | route.py | ③ |
| `decision dst desc tags date_doc reviewed keeper` | decide agent | ④ |
| `result final` | apply.py | ⑤ |

## ① Collect

`collect.py` selects the pass — `memory.jsonl` is the seen-set. A file is a candidate when it has no memory line, its (size, mtime) changed with new content, or its last decision was `unanswered` or `refused`. Order: those two first, then `inboxes:` files, then the rest; take `batch_size` (default 500).

The walk starts at `root` **and at every inbox that lives outside it** — Desktop and Downloads are outside the document tree on most machines, and an inbox is a walk root, not merely a priority band. Their files are keyed `~/Desktop/…` in memory; everything under `root` stays root-relative. What could not be walked is named — a refused directory is reported `IGNORED`, and an unreadable walk root, or a scan of zero files against a non-empty memory, ends the pass with an error. "I could not look" is never reported as "there was nothing". Extraction is page 1 only, ~4000 chars; no text and size > 1 KiB → `needs_vision`, with a page-1 PNG in `bench/renders/` for PDFs and images — other formats reach ② renderless and climb its ladder. Byte-identical duplicates are grouped with no size threshold. A symlink is a pointer, not a document: `opaque: "symlink"` with its `link_to`, never hashed, never grouped as a duplicate of its own target, never read — a corpus may file deliberately in links, and dedup would bin them. Its memory key is its own name, never the target's: keys resolve the parent directory, never the final component, or a link and its target would share one line and one of the two would be lost.

Done when the counts in `bench/logs/collect.log` are numbers you can explain from the corpus, and every `needs_vision` PDF and image has a render.

## ② Vision

An agent reads every `needs_vision` entry and fills `text` and `lu`: `"text"` (extracted layer sufficed) · `"render"` (read from the PNG) · `"pages N-M"` (escalated into the original). Escalation goes upward through readers, never around them: page-1 render → read the original (`Read`, targeted pages) → `collect.py --render <path> --pages A-B` on other pages → convert (`sips`, or whatever the platform offers). No script ever interprets content.

A file that survives every reader is **withdrawn**, and withdrawal is torn from you, not chosen: set `decision: "unanswered"` with a `reason` that names each attempt — `"tried: render p1, Read p1-2, sips — all failed"`. A withdrawal whose reason lists no attempts is laziness with a paper trail; the file comes back first next pass either way, so skipping the ladder buys nothing. `lu` is NEVER set on a file that was not read — it is a witness, not a checkbox.

**Answering a withdrawn file**: when the user says what an unreadable file is, write the real decision (`move`, `trash`, …) straight onto the entry — a human judgement is the strongest evidence there is, and it counts as triage `propose`. Re-run `route.py` and it stamps that triage instead of blocking; and even if `apply` already ran, `--learn` accepts a stamped `result` without a triage (reported under `answered_withdrawals`). Either order closes the pass.

Containers (`.zip`, `.tar`, …) are never `needs_vision` — nothing renders them: collect marks them `opaque: "container"`, they reach ④ as residuals, and an agent that opens one to read its listing records `lu: "container"`.

Done when every `needs_vision` entry has `text` filled, or a withdrawal whose `reason` lists the attempts.

## ③ Route

`route.py` gives every entry a triage: `route` (a rule recognised it), `propose` (needs eyes — sensitive, duplicated, tied entities, inbox, weak evidence, unresolved destination), `residual` (nothing matched), `skip` (content already in memory). It exits 2, paths listed, while any entry is unread — no judgement on unread bytes. A `known_as` entry is exempt: its md5 is already in memory, `skip` is decided on identity and never reads the text. Recognising bytes read in an earlier pass is not judging them — and binning one still requires reading it this pass. The one way past the barrier is a withdrawal from ②: those entries are counted `withdrawn`, get no triage columns, and wait for the next pass. Shadow rules predict before guards, so guards never blind learning.

A document names several entities at once; the strongest evidence decides, config order decides nothing, and a tie goes to `propose`. An `inboxes:` file is always proposed, never silently routed. A destination variable that does not resolve ({doc_year} with no date) means the rule does not fire — never an `undated/` folder. All matching is accent- and case-insensitive.

Done when every entry has a triage and no count surprises you. This verb only writes columns in `bench/` — there is no `--dry-run` here; dry-run belongs to Apply alone.

## ④ Decide

The `route` triage needs confirming, `propose` opening, `residual` eyes. Fill `decision` (`move|trash|tag|rename|none`), `dst` (trailing `/` files into that folder; anything else is the full path, which is how a rename is written), a `desc` that says something the filename does not, `tags`, `date_doc` (never invented), and `reviewed: "vision"` when you read the render this pass — the sensitive probe requires it before trashing a file whose text will not extract. On a duplicate, `keeper` names the copy that stays; it is the only way a sensitive file is ever binned, and apply proves the bytes survive before acting.

Blocking questions go to the user during this step, at the moment they arise — never batched to the end. What stays unsettled is left undecided; Learn will record it.

Done when every entry has a decision, or a reason it does not that you can say out loud.

## ⑤ Apply

`apply.py` is dry-run by default: it prints every gesture and touches nothing. `--execute` once the printout matches intent — and the execution report must equal the dry-run report. Each successful action appends its memory line immediately and stamps `result`; a crash leaves both exactly at the interruption point, and `apply.py --resume --execute` continues where `result` is missing (`--resume` alone prints the dry-run of the remainder — dry-run stays the default, always). Before replaying an entry whose source is gone, resume reconciles: a gesture the killed run already performed (memory line from this pass, or the file at its decided destination with matching size and md5) is stamped `result`, reported as `reconcile` — never a FAIL, never a ghost `unanswered`. Trash goes to the OS bin (fallback `<workspace>/.trash/<pass>/`), collisions get `(2)`, nothing is clobbered. The sensitive probe trusts nothing from the bench: text and ids are re-extracted from the file itself, and extractor debris does not count as a reading — a non-trivial file whose re-read yields no language is kept until `reviewed: "vision"`. No `sensitive:` block in config → every trash is refused.

A refusal writes its own memory line — `decision: "refused"`, naming the decision it refused and the guard that stopped it — and stamps `result`. The file is unfiled, so the next collect re-selects it first; but the record says a judgement was made and held back, which is not the same sentence as "nobody could read it".

Done when dry-run and execution report the same counts and `failed` is 0. `refused` is a separate count and is not a defect: a guard that keeps a sensitive or unread file is the tool working. Never chase it to zero — read each refusal, and answer it with evidence (`reviewed: "vision"` after actually looking) or leave the file alone.

## ⑥ Learn

`route.py --learn` closes the pass: scores every shadow rule against each file's final path, mines new candidates (the simplest form with zero counterexamples, born `status: shadow`), reports `ripe` rules, reports every `unanswered` file BY NAME and writes its memory line so the next pass selects it first, lists the `refused` files separately (their lines are already written, by apply), warns on dead `anchors:` pointers, archives `bench/` → `logs/<pass>/`. You then write the leftovers and their open questions into `wiki/state.md`.

Done when `bench/` is gone from the workspace and every leftover is named in `wiki/state.md`.

## Questions — three moments, never a fourth

- **Blocking** — during Decide, at the moment they arise.
- **Rules** — at promotion, one at a time, counters and full-audit list attached.
- **Leftovers** — written to `wiki/state.md` at Learn, asked at the next session start; their files are re-selected first.

## Rules: born shadow, promoted by the user

A rule earns its way in; it is never handed over on the strength of looking right. Born `status: shadow` from `--learn` only, evaluated on every pass, **never applied — a shadow rule fills its own column and nothing else, and route.py stops the pass if a non-active rule ever reaches the routing columns** — passes, hits, agreed, **disagreed** accumulate in the rule itself. After ≥5 passes with zero disagreement:

1. `route.py --audit <rule-id>` — replay against memory: retroactive precision on files already judged, diverging files listed. Cheap, read-only, runs anytime.
2. `route.py --full-audit <rule-id>` — confront the whole disk, including what no pass ever judged. Beyond 500 candidates it says so and you sample. No ground truth here — the list is for human judgement.
3. The user promotes, on that evidence: `status: active` plus a dated `history:` line. Refusal → history line, rewrite, `cycle: N+1`, counters reset.

Retired after 3 failed cycles, or 10 passes at 0 hits. A rule that keeps diverging is wrong about these documents — rewrite it or drop it.

## The wiki

`wiki/` holds what the corpus cannot say about itself: who is who, which entity was live in which period, which arbitration was made and why. `context.md` carries the durable facts; `state.md` carries what the next session must pick up. Start there.
