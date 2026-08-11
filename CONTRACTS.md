# wikidoc v2 — interface contracts

Frozen before implementation. Every author agent builds ONE file against this
document. If a contract is wrong, report it back — do not silently deviate.

Staging dir: `~/.agents/skills/wikidoc-next/`. The live skill at
`~/.agents/skills/wikidoc/` and the production workspace `~/.wikidoc/` are
READ-ONLY references — never modified.

Language: all code, comments and docs in English (the skill will be published).
Python 3.9+ stdlib; optional deps (`pypdf`, `pypdfium2`, `send2trash`,
`striprtf`, `yaml` required) follow the existing try/except pattern.

## Vocabulary

- **triage** — where a file lands after route.py: `route` (a rule recognised
  it), `propose` (needs eyes: sensitive, duplicated, tied entities, inbox,
  weak evidence, unresolved destination), `residual` (nothing matched or
  nothing to reason on), `skip` (content already in memory).
  The JSON field is `triage`. v1 records used `level` — readers accept both,
  writers emit only `triage`.
- **bench/** — the pass's working directory inside the workspace (ex `batch/`).
  Archived to `logs/<pass>/` at the end of a pass, never rmtree'd.
- **evidence strength** — 3 = validated identifier read in content,
  2 = name read in content, 1 = path/filename only ("hearsay").
  A rule whose match uses only strength-1 conditions is capped at `propose`.

## Layout

```
wikidoc-next/
  SKILL.md  SETUP.md  config.example.yaml  CONTRACTS.md
  scripts/ memory.py  extract.py  route.py  apply.py  enrich_macos.py
  fixtures/build.py
  regressions.py
```

## Shared helpers (owned by memory.py, imported by everyone)

```python
nfc(s)                      # unicodedata NFC
norm(s)                     # THE one text normaliser: NFC -> casefold -> strip
                            # combining marks -> collapse whitespace. Every
                            # text comparison in the codebase goes through it.
flatten(s)                  # whitespace -> single spaces (pre-norm display)
file_md5(path, size)        # None on unreadable (TCC)
is_inside(path, root)       # realpath + NFC + casefold + segment-wise
workspace()                 # $WIKIDOC_HOME or ~/.wikidoc
skill_dir()
self_ingestion_guard(cfg)   # [workspace, skill_dir, ...]
load_config() / require_config()
pass_id(mem)                # "YYYY-MM-DD-N"
```

## memory.jsonl — one JSON object per line, append-only

```json
{"path": "ACMECORP/Comptabilité/f.pdf", "pass": "2026-08-11-1",
 "triage": "route", "decision": "move|trash|tag|rename|none",
 "reason": "...", "size": 48213, "mtime": 1754899200, "md5": "…"|null,
 "desc": "…"|null, "ids": {"siren": ["917963183"]}|null, "tags": […]|null,
 "date_doc": "YYYY-MM-DD"|null, "provenance": "pass"|"migrated"}
```

`path` is NFC, relative to `root`. Indexes: **by_path is primary**
(last-write-wins per path — fixes v1 where by_md5 last-write-wins made 4 of
165 documents unreachable); by_md5 secondary and may map one md5 to a LIST.
`stats` reports `distinct_files = len(by_path)`.

### memory.py CLI

```
memory.py stats
memory.py backlog [N]     # walk root (cfg excludes), fast-path
                          # (path,size,mtime) against by_path, write
                          # bench/backlog.json = [abs paths], print counts
memory.py show <path|md5>
memory.py find <term>
```

Module API: `class Memory: by_path, by_md5, seen_stat(path,size,mtime),
seen_md5(md5), record(**fields) -> dict, append(rec) -> writes ONE line
immediately (fsync not required), append_many(recs)`.
`record()` REFUSES (raises ValueError) a `desc` whose token set is a subset of
the filename's tokens — the one honest test from v1 review.py, applied where
the data enters. Caller catches and surfaces; never silently dropped.

## extract.json — written by extract.py, list of entries

```json
{"path": "/abs", "rel": "…", "size": 1, "mtime": 1, "md5": "…", "ext": ".pdf",
 "text": "first page, flattened", "prose": true,
 "needs_vision": false, "render": "bench/renders/012.png"|null,
 "ids": {"siren": […]}, "dates": ["05/08/2026"], "doc_year": 2026|null,
 "duplicate_of": ["rel", "…"], "known_as": "rel"|null, "known_desc": "…"|null,
 "opaque": "reason"|null, "error": "…"|null}
```

### extract.py rules

- Input `bench/backlog.json`, output `bench/extract.json` + `bench/renders/`.
  Summary counts to stdout AND `bench/logs/extract.log`.
- `needs_vision = true` when usable text is empty/garbled AND size > 1024.
  Render PDFs via pypdfium2 -> stdlib zlib/struct PNG (carry v1 encoder).
  Also render image formats macOS can convert (HEIC via `sips`, best effort).
  Under `WIKIDOC_MINIMAL=1` PDF text is unavailable: the entry becomes
  `needs_vision: true` — minimal mode must NEVER quietly produce `text: ""`
  with `prose` unset (v1 silently disabled the sensitive guard this way).
  size <= 1024 and no text = empty/stub file: `opaque: "no-content"`.
- Carry from v1: `looks_like_prose` (with mid-word-capital rejection),
  `flatten`, `normalise_id` (squeeze only all-digit values).
- **Identifiers are validated, not just matched**: SIREN/SIRET must pass Luhn;
  IBAN must pass mod-97; a configured pattern may declare
  `validate: luhn|iban|none`. A candidate failing its check is NOT an id.
- Dates: add `MM/YYYY` to the recognised forms. `doc_year` only from
  recognised dates, bounded 1900..current_year+1.
- Duplicates: group by (size, md5) with **no size threshold and no group cap**
  (v1's `DEDUP_MIN_SIZE=4096` missed 6/6 real groups); md5 is computed for
  every file anyway.

## routing.json — written by route.py

```json
{"pass": "…", "counts": {"route": 1, "propose": 2, "residual": 3, "skip": 4},
 "files": [{"path": "/abs", "rel": "…", "triage": "propose",
   "why": "sensitive (text_contains_any)", "rule": "id"|null,
   "entity": "ACMECORP"|null, "strength": 3|2|1|null,
   "guards": ["sensitive"], "destination": "…/"|null,
   "shadow": [{"rule": "id", "destination": "…/"}]}]}
```

### route.py rules

- `route.py [--dry-run]`: reads `bench/extract.json` + config, writes
  `bench/routing.json` (dry-run writes `bench/logs/routing-dry-run.json`
  instead and touches nothing else).
- **Vision barrier**: exit 2, listing the paths, if any entry still has
  `needs_vision: true` and empty `text`. Convention became a gate.
- Shadow predictions are collected BEFORE guards run (guards must not blind
  learning). Guards in order: skip (known md5) -> sensitive -> duplicate ->
  inbox (file sits under a configured `inboxes:` path -> propose) ->
  entity tie. Then rules. Strength graded on the branch that MATCHED.
- Entities: strongest evidence wins; a tie at the top -> propose. An empty
  match dict matches nothing.
- Destination rendering: always trailing `os.sep`; an unresolved variable
  (`{doc_year}` with no date) -> the rule does NOT fire; file goes `propose`
  with `why: "destination variable unresolved: doc_year"`. Never `undated/`.
- All text matching through `memory.norm()` — accent-insensitive everywhere.

### route.py --learn plan.json   (end of pass)

- Scores every shadow rule against the FINAL path of each file (already in
  the right place counts as agreement). Updates counters in config.yaml —
  comment-tolerant, creates missing fields.
- Mines candidates from this pass + memory: ladder ① ext+source-folder
  ② filename pattern ③ discriminating phrase (present in every file that
  went to one destination, absent from every other file of the pass)
  ④ identifier ⑤ combinations. Keep the SIMPLEST form with zero
  counterexamples; write it as `status: shadow, cycle: 1`, counters at 0,
  `learned_from` filled. No candidate found = nothing born.
- Reports `ripe` rules (thresholds from `learning:` config block, defaults
  `min_passes: 5, min_hits: 5, disagreed == 0`) — reporting only, promotion
  is the agent+user's act (SKILL.md documents editing `status:` with a dated
  `history:` line).
- Reports `unanswered`: backlog files absent from plan.json, BY NAME, to
  carry to the next pass. No `unrecorded` arithmetic, no `--force`.
- Archives `bench/` -> `logs/<pass>/` (move, never delete).

### route.py --audit <rule-id>   (promotion)

- Walks the ENTIRE root (excludes respected). Announces the candidate count
  BEFORE extracting content; beyond 500 it says so and the agent samples.
- Output: every file the rule would touch, with rendered destination, as
  JSON to stdout and `logs/audit-<rule-id>.json`.

### Rule shape in config.yaml

```yaml
- id: quittance-loyer
  status: shadow          # shadow | active — anything else = warning + inert
  cycle: 1
  passes: 0
  hits: 0
  agreed: 0
  disagreed: 0
  learned_from: "2026-08-11-1: 9 moves to Perso/Logement/quittances-2026/"
  history: []             # dated promote/refuse/rewrite lines
  when: {ext: [".pdf"], text_contains_any: ["quittance de loyer"]}
  destination: "Perso/Logement/quittances-{doc_year}"
  tags: [logement]
```

## plan.json — written by the Decide agent (shape unchanged from v1)

`{"moves": [{"src","dst","desc","reason","tags","ids","date_doc","triage"}],
  "trash": [{"path","reason","reviewed"?}], "tags": […], "keep": […]}`
A `dst` ending in `/` files into that folder; anything else is a full path
(that is how a rename is written).

### apply.py rules (carry v1, fix four holes)

1. **Incremental memory**: each successful action appends its line
   IMMEDIATELY (`Memory.append`), not batched after four loops. A crash
   mid-pass leaves memory exactly at the point of interruption.
2. **`--resume plan.json`**: skips plan entries whose path already has a line
   for this pass. `os.stat` guarded everywhere; `resolve()` returns None for
   dangling symlinks (`os.path.lexists` but not `exists` -> "dangling
   symlink", failed entry, pass continues).
3. **The sensitive probe trusts nothing from the plan**: text AND ids
   re-extracted from the file itself (import extract). A trash line for a
   file whose re-extracted text is empty and size > 1024 is REFUSED unless
   the entry carries `"reviewed": "vision"` — the agent asserting it read
   the render this pass. Image-only prescriptions stop dying here.
4. **Plan validation before any gesture**: the same path in two sections
   (move+trash) rejects the whole plan; nothing is binned when the config
   declares no `sensitive:` block (kept from v1).

Everything else carries over verbatim where proven: dry-run default and
count parity, `(2)` collision, bin never unlink, re-stat at destination,
self-ingestion guard, `to_bin` fallback, enrich best-effort.

## enrich_macos.py

Copied UNCHANGED from v1 by the integrator. One addition allowed: warn (not
fail) when a tag is absent from the config taxonomy.

## config.example.yaml v2

Blocks: `workspace, root, language, batch_size, exclude, inboxes
([{path, policy: empty|transit}]), layout (target buckets, informational),
identifiers (pattern + validate:), entities, sensitive, tags,
learning ({min_passes: 5, min_hits: 5, max_cycles: 3, dead_after_passes: 10}),
rules`. Comments are part of the contract — the agent reads this file too.
Entities example leads with a person (no identifier), then a landlord, then
a company marked "only if you run one". No private data from any real user.

## SKILL.md v2 (≤ ~110 lines)

Six steps: Collect -> Vision -> Route -> Decide -> Apply -> Learn, each with
a done-when. Invariants carried from v1 (evidence decides, bin reversible,
sensitive never auto-binned, byte-identical duplicates, re-stat) restated
with `triage` vocabulary. MUST include, fixing v1's five documented failures:
how to invoke scripts (absolute paths from the skill dir), the plan.json
shape INLINE, bench lifecycle (archived at learn), recovery of a half-applied
pass (`--resume`), and the three question moments (blocking -> during Decide;
rules -> at promotion with the audit list; leftovers -> wiki/state.md at next
session start). Shadow lifecycle: born from --learn only, 5 passes, audit
agent, promotion by user, cycle N+1 on refusal, retired after 3 cycles or
10 passes at 0 hits.

## SETUP.md v2 (≤ ~140 lines)

PROBE (capabilities observed, cloud placeholders counted FIRST) ->
SURVEY (3 parallel Explore agents: Desktop, Downloads, Documents root ->
volumes, families, inboxes, identifiers seen) -> GRILL (interview anchored in
counts; config.yaml v1 written EARLY and re-grilled; context noted into
wiki/context.md as it comes) -> LAYOUT (target tree: 3 named options +
free choice; defaults offered: Downloads empty, Desktop transit, everything
under Documents by entity; policy stored in `inboxes:`) -> WRITE (config
final, wiki/context.md, empty memory.jsonl; mined rules born shadow) ->
ANCHOR (audit the user's CLAUDE.md/AGENTS.md: process lines stay, corpus
facts move to the wiki and are replaced by CONDITIONAL pointers ("dating a
document -> wiki/context.md"), original backed up to legacy/, every pointer
mechanically checked to resolve, diff shown to the user before writing) ->
DRY RUN (50 files, read aloud, memory still empty). Hard rules that gate
irreversible gestures stay in the instructions file — they must hold in
sessions that never open the wiki.

## fixtures/build.py + regressions.py

fixtures: carry v1 traps (NFD, case twins, cp437 zip, Icon\r, CON.pdf,
>260 chars, typographic apostrophe, EDF re-download) and ADD: a dangling
symlink, an image-only sensitive scan (prescription), an MM/YYYY-dated rent
receipt, byte-identical duplicates UNDER 4096 bytes (unpadded — v1's fixture
was padded past the threshold to dodge the bug), an inbox folder, an
already-filed folder, a two-entity contract without a shared identifier.

regressions.py: runs the pipeline over fixtures in full / minimal / bare
modes and pins, at minimum: symlink crash + incremental memory (B1), ids
re-extraction + empty-text trash refusal (B2), small duplicates detected
(S1), MM/YYYY parsed (S3), accent-insensitive matching (S4), by_path index
(S5), vision barrier blocks route, unanswered reported by name, bench
archived not deleted. Header comment states plainly: green means the known
bugs stayed fixed, nothing more.
