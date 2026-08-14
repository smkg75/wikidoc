# wikidoc v2 — interface contracts

Frozen before implementation. Every author agent builds ONE file against this
document. If a contract is wrong, report it back — do not silently deviate.

Work happens IN THIS REPO on branch `v2`. `~/.wikidoc/` (production workspace)
is READ-ONLY until the v2 cold test passes.

Language: all code, comments and docs in English (the skill will be
published). Python 3.9+ stdlib; optional deps (`pypdf`, `pypdfium2`,
`send2trash`, `striprtf`; `yaml` required) follow the existing try/except
pattern.

## Architecture rule

Three **steps** (verbs, executed, never imported):
`collect.py` · `route.py` · `apply.py`.
Three **libraries** (nouns, imported, never steps):
`memory.py` · `extract.py` · `enrich_macos.py`.

**A step never imports or invokes another step. Steps import libraries.**
Scripts convert bytes and move files; they never interpret. Interpretation —
reading a document, writing a desc, deciding — belongs to agents.

## Vocabulary

- **triage** — where a file lands after route.py: `route` (a rule recognised
  it), `propose` (needs eyes: sensitive, duplicated, tied entities, inbox,
  weak evidence, unresolved destination), `residual` (nothing matched),
  `skip` (content already in memory). JSON field `triage`; v1 records used
  `level` — readers accept both, writers emit only `triage`.
- **evidence strength** — 3 = validated identifier read in content, 2 = name
  read in content, 1 = path/filename only ("hearsay"). A rule matching only
  on strength-1 conditions is capped at `propose`.
- **bench/** — the pass's working directory inside the workspace. Archived to
  `logs/<pass>/` at the end of a pass, never rmtree'd.
- **unanswered** — a selected file that reached the end of a pass without a
  decision. Recorded by name, re-selected FIRST next pass, its question asked
  at session start.

## Libraries

### memory.py — the library of memory.jsonl (owns the shared helpers)

`memory.jsonl`: one JSON object per line, append-only, the durable record —
what the tool knows about every document and why.

```json
{"path": "Acme/Comptabilité/f.pdf", "pass": "2026-08-14-1",
 "triage": "route", "decision": "move|trash|tag|rename|none|unanswered",
 "reason": "…", "size": 48213, "mtime": 1754899200, "md5": "…"|null,
 "desc": "…"|null, "ids": {"siren": ["123456782"]}|null, "tags": […]|null,
 "date_doc": "YYYY-MM-DD"|null, "provenance": "pass"|"migrated"}
```

`path` is NFC, relative to root. **by_path is the primary index**
(last-write-wins per path); by_md5 secondary, one md5 may map to a LIST.
`stats` reports `distinct_files = len(by_path)`.

Shared helpers, owned here, imported by everyone — names are LAW:
`nfc(s)`, `norm(s)` (THE one text normaliser: NFC → casefold → strip
combining marks → collapse whitespace; every text comparison in the codebase
goes through it), `flatten(s)`, `file_md5(path, size)` (None on unreadable),
`is_inside(path, root)`, `workspace()`, `skill_dir()`,
`self_ingestion_guard(cfg)`, `load_config()`, `require_config()`,
`pass_id(mem)`.

API: `class Memory: by_path, by_md5, seen_stat(path, size, mtime),
seen_md5(md5), record(**fields) -> dict, append(rec)` (writes ONE line
immediately), `append_many(recs)`. `record()` raises ValueError on a `desc`
whose token set is a subset of the filename's tokens — the one honest test
from v1 review.py, applied where the data enters.

CLI: `stats`, `show <path|md5>`, `find <term>`. **No selection — memory.py
chooses nothing.**

### extract.py — the library that reads bytes (imported, never a step)

Why it exists: three consumers need to read file content — collect.py (mass
extraction), apply.py (the sensitive probe re-reads text AND ids itself,
trusting nothing from the working file), route.py --full-audit (confronting
a text rule with the whole disk). Duplicating this code is how v1 ended up
with two notions of text equality, the weaker one guarding the RIBs.

Provides: `pdf_text`, `pdf_render(path, out_png, page=1)` (pypdfium2 →
stdlib zlib/struct PNG encoder, carried from v1), `zip_text`, docx/odt/rtf
text, `read_text_file`, `looks_like_prose` (with the mid-word-capital
rejection), `image_render` (HEIC and friends via `sips`, best effort,
macOS-guarded), `extract_ids(text, cfg)`, `extract_dates(text)`,
`doc_year(dates)`.

- **Identifiers are validated, not just matched**: SIREN/SIRET must pass
  Luhn; IBAN must pass mod-97; a configured pattern may declare
  `validate: luhn|iban|none`. A candidate failing its check is NOT an id
  (any 9 digits passed the v1 regex). `normalise_id` squeezes only all-digit
  values.
- Dates: v1 forms plus `MM/YYYY`. `doc_year` only from recognised dates,
  bounded 1900..current_year+1. Never a bare year harvested from text.
- `WIKIDOC_MINIMAL=1`: pdf text/render unavailable. extract.py returns None
  (distinct from ""), so callers mark `needs_vision` — minimal mode must
  NEVER quietly produce an empty text that passes for "read and empty".

### enrich_macos.py — carried from v1 UNCHANGED

One addition allowed: warn (never fail) when a tag is absent from the
config taxonomy.

## The single working file: bench/routing.json

One entry per selected file. Each pipeline actor writes ONLY its columns;
writes are atomic (temp file + rename). Empty columns = remaining work;
`--resume` = "continue where `result` is missing". Dies with the pass
(archived, not deleted).

| columns | writer | step |
|---|---|---|
| `path size mtime md5 ext pages text truncated prose needs_vision render ids dates doc_year duplicate_of known_as known_desc opaque error` | collect.py | ① |
| `text lu` (needs_vision entries only) | vision agent | ② |
| `triage why guards rule entity strength destination shadow` | route.py | ③ |
| `decision dst desc tags date_doc reviewed` | decide agent | ④ |
| `result final` | apply.py | ⑤ |

`lu`: `"text"` (extracted layer sufficed) · `"render"` (read from PNG) ·
`"pages N-M"` (agent escalated and read the original's targeted pages).

Entry lifecycle (a 2-page scan, abridged):

```jsonc
// ① collect
{"path": "/…/Downloads/scan0034.pdf", "size": 412083, "md5": "a41f…",
 "ext": ".pdf", "pages": 2, "text": "", "prose": false,
 "needs_vision": true, "render": "bench/renders/034-p1.png", "ids": {}}
// ② vision       "text": "Ordonnance — Dr Marchand, 12/07/2026 …", "lu": "render"
// ③ route        "triage": "propose", "guards": ["sensitive"],
//                "why": "sensitive (text_contains_any: ordonnance)"
// ④ decide       "decision": "move", "dst": "Perso/Sante/2026/",
//                "desc": "…", "reviewed": "vision"
// ⑤ apply        "result": "moved", "final": "Perso/Sante/2026/scan0034.pdf"
```

One decision field per entry means the v1 bug class "same path in moves AND
trash" is structurally impossible.

## Steps

### collect.py — step ①: choose the pass's files, read their evidence

Selection (no cursor, no xattr — memory.jsonl IS the seen-set):
walk root (config excludes), a file is a **candidate** when it has no memory
line, OR its (size, mtime) changed (md5 re-checked: same content = moved,
recorded as `known_as`; new content = candidate), OR its last decision is
`unanswered`. Order: unanswered first, then files under `inboxes:` paths,
then the rest. Take N (default `batch_size: 500`).

Extraction: **page 1 only**, text truncated at ~4000 chars
(`truncated: true`), page count kept. No text AND size > 1024 →
`needs_vision: true` + page-1 PNG render. No text AND size ≤ 1024 →
`opaque: "no-content"`. Duplicates grouped by (size, md5) with **no
threshold and no group cap** (v1's DEDUP_MIN_SIZE missed 6/6 real groups).

Writes `bench/routing.json` (evidence columns) + `bench/renders/` +
`bench/logs/collect.log` (counts mirrored on stdout).

`collect.py --render <path> --pages A-B` — on-demand renders for agents whose
harness cannot read the original directly. The escalation READER is always
an agent; no script ever interprets content.

**Nothing imports collect.py.**

### route.py — step ③ plus the whole rule lifecycle

**Default verb** — fills the triage columns of every entry:
- **Vision barrier**: exit 2, listing the paths, if any entry still has
  `needs_vision: true` and empty `text`. No judgement on unread bytes.
- Shadow predictions computed BEFORE guards (guards must not blind
  learning). Guard order: skip (known md5) → sensitive → duplicate →
  inbox (`inboxes:` file → propose, never a silent route) → entity tie.
  Then rules. Strength graded on the branch that MATCHED; entity ties go to
  propose; an empty match dict matches nothing.
- Destination rendering: always trailing `os.sep`; an unresolved variable
  ({doc_year} with no date) → the rule does not fire, entry goes `propose`
  with `why: "destination variable unresolved: doc_year"`. Never `undated/`.
- All text matching through `memory.norm()` — accent-insensitive everywhere.
- No `--dry-run`: this verb only writes columns in bench/, it never touches
  a user file. Dry-run belongs to apply.py alone.

**`--learn`** — end of pass, after apply:
- For each shadow rule: compare what it would have done with each file's
  FINAL path (already in the right place = agreed). Increment
  passes/hits/agreed/disagreed in config.yaml — comment-tolerant, creates
  missing fields. No jargon for this: it is a comparison and two counters.
- Mine candidates from this pass + memory, simplest form first:
  ① ext+source-folder ② filename pattern ③ discriminating phrase (present in
  every file that went to one destination, absent from every other file of
  the pass) ④ identifier ⑤ combinations. Keep the SIMPLEST form with zero
  counterexamples; born `status: shadow, cycle: 1`, counters 0,
  `learned_from` filled. Nothing found = nothing born.
- Report `ripe` rules (config `learning:` thresholds, defaults
  min_passes 5, min_hits 5, disagreed == 0) — reporting only; promotion is
  the agent + user's act.
- Report `unanswered` BY NAME and write their memory lines
  (`decision: "unanswered"`) so selection re-picks them first. No
  `unrecorded` arithmetic, no `--force`.
- **Anchor check**: for each file in config `anchors:`, extract the
  backticked paths it cites and warn on any that no longer resolves — a dead
  pointer is a fact that left the instructions file and arrived nowhere.
- Archive `bench/` → `logs/<pass>/` (move, never delete).

**`--audit <rule-id>`** — confront a rule with everything ALREADY JUDGED:
replay it against memory (ground truth exists), print retroactive
agreed/disagreed with the diverging files listed. Cheap, read-only, runs
anytime — test a rule idea against 5000 past decisions in one command.

**`--full-audit <rule-id>`** — confront a rule with the WHOLE DISK,
including what no pass ever judged. Announces the candidate count BEFORE
extracting content; beyond 500 it says so and the agent samples. Output:
every file the rule would touch with its rendered destination, to stdout +
`logs/full-audit-<rule-id>.json`. No ground truth here — the list is for
human judgement. **Promotion requires the user's validation of this list.**

Rule lifecycle: born shadow (from --learn only) → ≥5 passes of counters →
`--audit` (retroactive precision on judged files) → `--full-audit`
(overreach on unjudged files) → user promotes (`status: active` + dated
`history:` line). Refusal → history line, improvement agent rewrites,
`cycle: N+1`, counters reset. Retired after 3 failed cycles, or 10 passes
at 0 hits.

```yaml
- id: quittance-loyer
  status: shadow          # shadow | active — anything else = warning + inert
  cycle: 1
  passes: 0
  hits: 0
  agreed: 0
  disagreed: 0
  learned_from: "2026-08-14-1: 9 moves to Perso/Logement/quittances-2026/"
  history: []             # dated promote/refuse/rewrite lines
  when: {ext: [".pdf"], text_contains_any: ["quittance de loyer"]}
  destination: "Perso/Logement/quittances-{doc_year}"
  tags: [logement]
```

### apply.py — step ⑤: the only script that touches disk

Reads the decision columns of `bench/routing.json`. Dry-run by default:
prints every gesture (`move src → dst`, `trash x`, failures) touching
nothing; `--execute` only after the printout matches intent, and the
execution report must equal the dry-run report.

Carried from v1 verbatim where proven: bin never unlink (send2trash →
`<ws>/.trash/<pass>/` fallback), `(2)` collision never clobber, re-stat at
destination before a move counts, self-ingestion guard, trailing-`/` = file
into folder, no `sensitive:` block declared → every trash refused.

The four v2 fixes:
1. **Incremental memory**: each successful action appends its memory line
   IMMEDIATELY and stamps `result` in routing.json. A crash mid-pass leaves
   both exactly at the interruption point.
2. **`--resume`**: skip entries whose `result` is stamped. `os.stat` guarded
   everywhere; a dangling symlink (`lexists` but not `exists`) = failed
   entry, the pass continues.
3. **The sensitive probe trusts nothing**: text AND ids re-extracted from
   the file via extract.py. A trash entry whose re-extracted text is empty
   and size > 1024 is REFUSED unless `reviewed: "vision"` — an agent
   asserting it read the render this pass.
4. Entry validation up front: a decision on a path outside root, or an
   unknown decision value, fails the entry before any gesture.

## config.example.yaml

Blocks: `workspace, root, language, batch_size (500), exclude,
inboxes ([{path, policy: empty|transit}]), layout (target buckets,
informational), anchors ([instruction files to pointer-check]),
identifiers (pattern + validate:), entities, sensitive, tags,
learning ({min_passes: 5, min_hits: 5, max_cycles: 3,
dead_after_passes: 10}), rules`.
Comments are part of the contract — the agent reads this file too. Entities
example leads with a person without any identifier, then a landlord, then a
company marked "only if you run one". No private data from any real user.

## SKILL.md (≤ ~115 lines)

Six steps — ① Collect ② Vision ③ Route ④ Decide ⑤ Apply ⑥ Learn — each with
a done-when. Invariants carried from v1 (evidence decides, bin reversible,
sensitive never auto-binned and never trashed unread, byte-identical
duplicates, re-stat) restated with `triage` vocabulary. Must include, fixing
v1's five documented doc failures: how to invoke the scripts (absolute paths
from the skill dir), the routing.json column table INLINE, bench lifecycle
(archived at learn), recovery of a half-applied pass (`--resume`), and the
three question moments (blocking → during Decide, at the moment they arise;
rules → one at a time at promotion, with counters + full-audit list
attached; leftovers → wiki/state.md at next session start). Escalation
contract: p.1 evidence → agent reads targeted pages of the original (`Read`,
or `collect.py --render` where the harness cannot) → question the user →
`unanswered`. Shadow lifecycle as above.

## SETUP.md (≤ ~150 lines)

PROBE (capabilities observed, cloud placeholders counted FIRST) →
SURVEY (3 parallel Explore agents: Desktop, Downloads, Documents →
volumes, families, inboxes, identifiers seen) → GRILL (interview anchored
in counts; config.yaml written EARLY and re-grilled; context noted into
wiki/context.md as it comes) → LAYOUT (target tree: 3 named options + free
choice; defaults offered: Downloads empty, Desktop transit, everything
under Documents by entity; policies stored in `inboxes:`) → WRITE (config
final + `anchors:`, wiki/context.md, empty memory.jsonl; mined rules born
shadow) → ANCHOR (audit the user's CLAUDE.md/AGENTS.md: process lines
stay — hard rules gating irreversible gestures MUST stay, they hold in
sessions that never open the wiki; corpus facts move to the wiki, replaced
by CONDITIONAL pointers "situation → path", never a bare link; original
backed up to legacy/; every pointer mechanically checked; diff shown before
writing) → **FIRST SWEEP** (replaces the old dry-run ending): a REAL first
pass scoped to Desktop + Downloads — full machinery, nothing weakened:
vision, triage, decide with the user's blocking questions, apply dry-run
read together, then --execute. Target ~80% of both zones visibly emptied.
Ends with a **bilan**: what moved where (counts + samples), and the
residues BY NAME → written `unanswered`, re-selected first at the next
pass. Tell the user explicitly: continue in this same conversation, or in
a fresh one — both work, memory.jsonl carries the state.

## Publication scope and cleanliness

The published skill is: `SKILL.md`, `SETUP.md`, `config.example.yaml`,
`scripts/`. **`fixtures/`, `TRAPS.md` and `CONTRACTS.md` are development
artifacts and are NEVER published** — the skill ships clean.

Cleanliness is a hard rule for every v2 file, published or not:
- Zero real-user data anywhere: no real person, company, place, email,
  account or identifier — in code, comments, tests, fixtures or docs. The
  v1 sources leak (a real company name in a fixture, a real SIREN and a
  real syndic email in tests and comments): do NOT carry those tokens; keep
  the lessons, replace the examples with invented ones.
- Fixture identifiers are fictitious AND pass their validators (Luhn-valid
  invented SIRENs, mod-97-valid invented IBANs) so the pipeline treats them
  as real ids without naming anyone.
- Before any publication: a sweep for personal markers over the exported
  tree must return nothing. The marker list lives outside the repo.

## fixtures/build.py + TRAPS.md — no frozen test suite

There is deliberately NO regressions.py. v1's verify.py taught the lesson:
a suite written by the code's author lies (its duplicate fixture was padded
past the code's own threshold, and three green runs hid a dead guard).

What ships instead:
- **fixtures/build.py** — the booby-trapped corpus. Carry v1 traps (NFD,
  case twins, cp437 zip, Icon\r, CON.pdf, >260 chars, typographic
  apostrophe, EDF re-download) and ADD: a dangling symlink, an image-only
  sensitive scan, an MM/YYYY rent receipt, byte-identical duplicates UNDER
  4096 bytes (unpadded), an inbox folder, an already-filed folder, a
  two-entity contract without a shared identifier.
- **fixtures/TRAPS.md** — the manifest: one entry per trap, what it is,
  and what correct handling looks like — including the pinned v1 bugs
  (incremental memory + symlink survival, probe re-extraction + empty-text
  trash refusal, small duplicates detected, MM/YYYY parsed,
  accent-insensitive matching, by_path index, vision barrier, unanswered
  by name and re-selected first, bench archived, anchor check warns on a
  dead pointer).

Verification is an AGENT's job, done freely and with fresh eyes: run the
pipeline over the fixtures (WIKIDOC_HOME at a temp dir, never ~/.wikidoc)
and grade the outcome against TRAPS.md. The manifest is the contract; how
to check it is the agent's judgement. Deterministic where being wrong is
expensive (the corpus), model-driven where judgement matters (the grading).
