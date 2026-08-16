# wikidoc v2 — interface contracts

Frozen before implementation. Every author agent builds ONE file against this
document. If a contract is wrong, report it back — do not silently deviate.

Work happens IN THIS REPO on `main` — one branch, no feature branches: the
repo has a single author who iterates in place. Every change ends with a
commit AND a push to `origin/main` (github.com/smkg75/wikidoc, private).
`~/.wikidoc/` is the production workspace and is never versioned here.

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

`path` is NFC and root-relative, or `~/…` for a file an inbox outside root
brought in — never a `../` climb, which breaks the moment root moves.
**by_path is the primary index** (last-write-wins per path); by_md5
secondary, one md5 may map to a LIST. `stats` reports
`distinct_files = len(by_path)`.

Shared helpers, owned here, imported by everyone — names are LAW:
`nfc(s)`, `norm(s)` (THE one text normaliser: NFC → casefold → strip
combining marks → collapse whitespace; every text comparison in the codebase
goes through it), `flatten(s)`, `file_md5(path, size)` (None on unreadable),
`is_inside(path, root)`, `rel_key(path, root)` (THE memory key — collect,
route and apply must agree or a file is filed twice; it resolves the PARENT
directory and never the final component, so a symlink keeps a key of its own
instead of colliding with its target's), `inbox_dirs(cfg)`,
`pass_roots(cfg)` (root + every inbox outside it: the directories a pass
walks, and the directories apply accepts a source from), `workspace()`,
`skill_dir()`, `self_ingestion_guard(cfg)`, `load_config()`,
`require_config()`, `pass_id(mem)`.

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
`doc_year(dates)` — and the **condition engine**: `COND`, `matches()`
(all/any/not grammar) and `sensitive_hit(entry, cfg)`. The engine lives here
because two STEPS evaluate config conditions — route.py for triage,
apply.py for the trash probe — and steps may not import steps; a second,
smaller matcher in apply is how v1's weakest copy ended up guarding the
most sensitive files. One matcher, one notion of what a condition means.

- **Identifiers are validated, not just matched**: SIREN/SIRET must pass
  Luhn; IBAN must pass mod-97; a configured pattern may declare
  `validate: luhn|iban|none`. A candidate failing its check is NOT an id
  (any 9 digits passed the v1 regex). `normalise_id` squeezes only all-digit
  values. The builtin IBAN pattern accepts group tails under 4 chars — a
  French IBAN ends on 3 — and lets the mod-97 check do the real filtering.
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
`--resume` = "continue where `result` is missing" — with `--execute` (a bare
`--resume` prints the dry-run of the remainder; dry-run stays apply's
default), and reconciling first: a gesture a killed run already performed is
stamped, not replayed (see apply.py). Dies with the pass (archived, not
deleted).

| columns | writer | step |
|---|---|---|
| `path size mtime md5 ext pages text truncated prose needs_vision render ids dates doc_year duplicate_of known_as known_desc opaque error` | collect.py | ① |
| `text lu` — or, on an unreadable file, `decision: "unanswered"` + `reason` (the withdrawal) | vision agent | ② |
| `triage why guards rule entity strength destination shadow` | route.py | ③ |
| `decision dst desc tags date_doc reviewed` | decide agent | ④ |
| `result final` | apply.py | ⑤ |

`lu`: `"text"` (extracted layer sufficed) · `"render"` (read from PNG) ·
`"pages N-M"` (agent escalated and read the original's targeted pages) ·
`"container"` (agent opened an archive and read its listing/members).
`lu` is NEVER set on a file that was not read. A file that survives every
reader — render, `Read` on the original, `collect.py --render`, conversion —
is **withdrawn**: `decision: "unanswered"` with a `reason` naming each
attempt (`"tried: render p1, Read p1-2, sips — all failed"`). The retry is
real and mandatory before withdrawal; the file is re-selected FIRST next
pass, so a lazy withdrawal buys nothing and shows in its own reason.

**Answering a withdrawn file**: when the user says what an unreadable file
is, the decide agent writes the real decision straight onto the unread
entry. A human judgement counts as triage `propose` — the strongest evidence
there is. route.py stamps that triage instead of blocking, apply records
`propose` in the memory line when the triage column is empty, and `--learn`
accepts a stamped `result` without a triage (reported under
`answered_withdrawals`). Either verb order closes the pass; nothing wedges.

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
walk `pass_roots(cfg)` — root, plus every inbox outside it, each a walk root
of its own (an inbox is where files ARRIVE, and Desktop/Downloads arrive
outside the tree; treating `inboxes:` as a priority band only is how v2
shipped with two inboxes that reported `candidates: 0` forever). Nested
inboxes fold into the root that covers them: no file is scanned twice.
Config excludes are matched relative to the walk root that found the file.
A file is a **candidate** when it has no memory
line, OR its (size, mtime) changed (md5 re-checked: same content = moved,
recorded as `known_as`; new content = candidate), OR its last decision is
`unanswered`. Order: unanswered first, then files under `inboxes:` paths,
then the rest. Take N (default `batch_size: 500`).

Extraction: **page 1 only**, text truncated at ~4000 chars
(`truncated: true`), page count kept. Non-office containers
(`extract.CONTAINER_EXT`: zip/tar/gz/7z/…) → `opaque: "container"`, never
`needs_vision` — nothing extracts or renders an archive, so the vision
promise would be unmeetable; residual lane, the decide agent opens one if it
wants (`lu: "container"`). Otherwise: no text AND size > 1024 →
`needs_vision: true` + page-1 PNG render. No text AND size ≤ 1024 →
`opaque: "no-content"`. Duplicates grouped by (size, md5) with **no
threshold and no group cap** (v1's DEDUP_MIN_SIZE missed 6/6 real groups) —
**except zero-byte files**: every empty file is trivially byte-identical to
every other (cloud placeholders, lock stubs), so size 0 is exempt from both
duplicate grouping and the `known_as` md5 match. `known_as` is only set
while the recorded copy still exists on disk — a `why` never cites a path
that no longer resolves.

**Symlinks are pointers, never documents.** `followlinks=False` stops the
walk descending into a linked directory but still lists linked FILES, and
`os.stat` follows them — so a link carries its target's size and md5 and
lands as a byte-identical duplicate of the very file it points at. A corpus
may file deliberately in links (an archived mail-out that must not duplicate
its originals); a dedup decision would bin exactly those. So a symlink gets
`md5: null`, `opaque: "symlink"`, `link_to` (resolved target) and
`link_broken`, no duplicate group, no `needs_vision`, no reading — route
sends it residual naming the target, a `none` decision gives it a memory
line, and it stops being work.

Writes `bench/routing.json` (evidence columns) + `bench/renders/` +
`bench/logs/collect.log` (counts mirrored on stdout). Entries the walk
cannot take (dangling symlinks, unstatable files) are counted (`ignored`)
and listed by name (`IGNORED <path> <- <reason>`) on stdout AND in
collect.log — an invisible skip is a file that never gets sorted. Counts
also break down candidates/selected/remaining PER configured inbox, so a
sweep sized on one inbox cannot starve another in silence.

**A refused look is never reported as an empty corpus.** `os.walk` swallows
every scandir error by default; a mistyped `root:`, an unmounted volume and
a permission the OS withdrew all produced `scanned: 0, errors: 0` and exit 0
— five green reports for zero work, which is the worst failure mode a
filing tool has. So: `onerror` counts every refused directory into
`unreadable_dirs` and names it in `ignored`; each walk root is probed before
it is walked and, if it cannot be opened, listed in `unreadable_roots`; and
the pass **exits non-zero** when any walk root is unreadable, or when
`scanned == 0` while `memory.jsonl` is not empty. An empty corpus with an
empty memory still exits 0 — that one really is nothing to do.

`collect.py --render <path> --pages A-B` — on-demand renders for agents whose
harness cannot read the original directly. The escalation READER is always
an agent; no script ever interprets content.

**Nothing imports collect.py.**

### route.py — step ③ plus the whole rule lifecycle

**Default verb** — fills the triage columns of every entry:
- **Vision barrier**: exit 2, listing the paths, if any entry still has
  `needs_vision: true`, empty `text` and no decision. A `known_as` entry is
  EXEMPT: its md5 is already in memory, the first guard triages it `skip` on
  identity and never consults the text, so re-reading it teaches nothing.
  Recognising bytes read in an earlier pass is not a judgement on them — and
  the safety net is unchanged, since apply refuses a trash with no readable
  text (`reviewed: "vision"`) and always refuses a sensitive one. No judgement on unread
  bytes — but an entry already carrying a decision passes: a withdrawal
  (`unanswered`) is counted `withdrawn` and left untriaged, and a real user
  decision on an unread file is triaged `propose` (the human judged; that is
  the strongest evidence there is, and it must be able to close the pass).
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
- Accept an entry whose `result` is stamped even without a triage — the
  answered-withdrawal path (apply ran before route could stamp `propose`);
  reported under `answered_withdrawals`. Refusing it would wedge the pass:
  that exact sequence is what v2's cold test hit.
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
2. **`--resume`**: skip entries whose `result` is stamped, and RECONCILE
   before replaying one that is not: a crash can land between the disk
   gesture, the memory append and the `result` stamp, and replaying then
   reports `FAIL … not found` while `--learn` invents a ghost `unanswered`.
   A vanished source whose gesture is proven — a memory line from THIS pass
   for the same content, or the file already at its decided destination with
   matching size and md5 — is stamped `result` (its missing memory line
   appended if the crash predated it) and reported `reconcile`. No md5, no
   proof: the entry fails normally. `--resume` acts only with `--execute`
   (alone it prints the dry-run of the remainder). `os.stat` guarded
   everywhere; a dangling symlink (`lexists` but not `exists`) = failed
   entry, the pass continues.
3. **The sensitive probe trusts nothing**: text AND ids re-extracted from
   the file via extract.py. A trash entry whose re-extracted text is empty
   and size > 1024 is REFUSED unless `reviewed: "vision"` — an agent
   asserting it read the render this pass.
4. Entry validation up front: an unknown decision value, or a source outside
   `pass_roots(cfg)` (root and the inboxes — a file may COME from an inbox
   outside root, it may only ever GO inside root), fails the entry before
   any gesture.

**`refused` is not `failed`.** A guard declining a gesture — sensitive hit,
unreadable text on a trash, no `sensitive:` block — is counted `refused` and
printed `REFUSE`; only a broken entry is `failed` and only `failed` sets the
exit code. They were one counter until SKILL.md's "done when `failed` is 0"
started teaching agents that a working guard is a problem to route around.

**Debris is not text, and a title is not debris.** `looks_like_prose` decides
whether an extractor returned language or its own wreckage, and it was wrong in
both directions on a 4 395-PDF audit: 32 bank statements of `/UNIC0037/UNIC0035…`
passed as prose (the word test read "UNIC" as a plausible word, thousands of
times) while 73 of the 98 texts it refused were legitimate — 66 of them because a
title has fewer than four words. It now strips glyph names (`/UNIC00xx`,
`cid:114`, `/uni00A0`) before judging and refuses only when they drown the text,
closes letter-spacing before counting words, allows five consonants in a row
(`inscription`, `prescription` — French contracts), and reads case sprayed
through a token rather than any capital mid-word (`ÅÁgkIYIr` is debris,
`IdentitéBancaire` is a word extraction glued). No repetition test: a real
invoice repeats `mission` in 39 % of its words.

**A guard must survive damaged extraction.** Two things reach the text before a
condition does — `norm()` folds typographic apostrophes and dashes (a
`sensitive:` line reading `relevé d'identité bancaire` met `Relevé d’Identité
Bancaire` and said nothing), and `text_contains_any/all` compare a second time
with every space removed (4,2 % of real PDFs come back glued). `extract_ids`
harvests twice: as extracted, then with a space restored at each lower→upper
transition, because `BankIdentiferCodeFR9120041…` hides an IBAN from every
`\b`-anchored pattern. On a real RIB those three together turn one accidental
`path_under` hit into two independent guards.

**`None` is not `""`, in every reader.** `zip_text` and `rtf_text` returned ""
on any exception, so a corrupt `.docx`, an `.xlsx` written with inline strings
(no `xl/sharedStrings.xml`) and a missing `striprtf` were indistinguishable from
an empty document — a 40 000-character export filed as blank. Readers return
None for "could not read"; only a real emptiness is "".

**A refusal is recorded, and it is not a withdrawal.** apply writes the
memory line itself — `decision: "refused"`, `reason` naming the decision it
refused and the guard that stopped it — and stamps `result: "refused"`.
`--learn` therefore leaves it out of `unanswered` and lists it under
`refused`; collect re-selects it in the same first band as a withdrawal,
because the file is still unfiled. Recording it as `unanswered` (what shipped
first) erased both the decision and the guard: the next pass met a file that
looked as if nobody had ever read it, and proposed the same refused gesture
again.

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
pass scoped to ALL the configured inboxes — N = the SUM of their file
counts (GRILL routinely surfaces a third inbox; sizing on two starves the
rest), per-inbox selected/remaining counts checked — full machinery,
nothing weakened: vision, triage, decide with the user's blocking
questions, apply dry-run read together, then --execute. Honest target: an
`empty`-policy inbox emptied or nearly so, a `transit` inbox reduced to its
legitimate work in progress, the remainder named — never "~80% of both
zones", which a WIP-laden Desktop cannot keep.
Ends with a **bilan**: what moved where (counts + samples), and the
residues BY NAME → written `unanswered`, re-selected first at the next
pass. Tell the user explicitly: continue in this same conversation, or in
a fresh one — both work, memory.jsonl carries the state.

## Publication scope and cleanliness

The published skill is: `SKILL.md`, `SETUP.md`, `config.example.yaml`,
`scripts/`. **`tests/` and `CONTRACTS.md` are development
artifacts and are NEVER published** — the skill ships clean.

Cleanliness is a hard rule for every v2 file, published or not:
- Zero real-user data anywhere: no real person, company, place, email,
  account or identifier — in code, comments, tests or docs. The
  v1 sources leak (a real company name in a fixture, a real SIREN and a
  real syndic email in tests and comments): do NOT carry those tokens; keep
  the lessons, replace the examples with invented ones.
- Test-corpus identifiers are fictitious AND pass their validators (Luhn-valid
  invented SIRENs, mod-97-valid invented IBANs) so the pipeline treats them
  as real ids without naming anyone.
- Before any publication: a sweep for personal markers over the exported
  tree must return nothing. The marker list lives outside the repo.

## tests/ — build.py + TRAPS.md, no frozen assertions, never a skill step

There is deliberately NO regressions.py. v1's verify.py taught the lesson:
a suite written by the code's author lies (its duplicate fixture was padded
past the code's own threshold, and three green runs hid a dead guard).

What ships instead:
Nothing in the skill imports or invokes tests/ — it exists for the
developer and the verification agents only, and never appears in SKILL.md
or SETUP.md.

- **tests/build.py** — the booby-trapped corpus. Carry v1 traps (NFD,
  case twins, cp437 zip, Icon\r, CON.pdf, >260 chars, typographic
  apostrophe, EDF re-download) and ADD: a dangling symlink, an image-only
  sensitive scan, an MM/YYYY rent receipt, byte-identical duplicates UNDER
  4096 bytes (unpadded), an inbox folder, an already-filed folder, a
  two-entity contract without a shared identifier.
- **tests/TRAPS.md** — the manifest: one entry per trap, what it is,
  and what correct handling looks like — including the pinned v1 bugs
  (incremental memory + symlink survival, probe re-extraction + empty-text
  trash refusal, small duplicates detected, MM/YYYY parsed,
  accent-insensitive matching, by_path index, vision barrier, unanswered
  by name and re-selected first, bench archived, anchor check warns on a
  dead pointer).

Verification is an AGENT's job, done freely and with fresh eyes: run the
pipeline over the generated corpus (WIKIDOC_HOME at a temp dir, never ~/.wikidoc)
and grade the outcome against TRAPS.md. The manifest is the contract; how
to check it is the agent's judgement. Deterministic where being wrong is
expensive (the corpus), model-driven where judgement matters (the grading).

## Open points — assumed limits, not bugs

- **Sensitive duplicates are reducible, under proof.** Byte-identical
  copies of a sensitive document both land `propose` (duplicate + sensitive
  guards). A trash on one is refused UNLESS the entry names a `keeper`:
  apply then re-reads both files from disk at the moment of the gesture and
  requires the keeper to exist, to differ from the source, to not be binned
  by this same pass, and to hash identically. Only then does the sensitive
  refusal lift — and the unreadable-text refusal with it, since neither
  describes a risk once the same bytes are proven to survive. The guard was
  never about copy count; it is about never losing sensitive CONTENT, and a
  proven surviving twin is exactly that proof. Do not weaken it further: no
  `reviewed:` flag, no config switch, no "trust the bench" shortcut may bin a
  sensitive file — only a re-hashed survivor.

## Future improvements — noted, not scheduled

Each entry: what you observe, why it happens, the direction a fix would take.
None of these blocks production; they are recorded here so the reason survives
until someone picks them up.

1. **Kept files have no content identity.** A `tag`/`none` decision writes its
   memory line with `md5: null` (apply hashes only what it moves or bins). So
   `seen_md5`/`known_as` recognise a duplicate of a *moved* file, never of a
   file *kept in place* — a re-download of something you tagged last month
   arrives as a stranger. Direction: hash on tag/none too (costs one md5 per
   kept file), or lazy-hash at collect when a size collision suggests a twin.

2. **`banned_phrases` no longer enforced.** v1's review.py rejected descs
   containing hedge phrases ("ce document semble…"); v2 kept only the
   paraphrase test in `Memory.record()`. Production config still carries a
   `review: banned_phrases:` block that nothing reads. Direction: fold the
   list into `record()`'s desc validation, same ValueError path as the
   paraphrase test.

3. **`--audit` replays rules against the recorded desc, not the document.**
   Full text is not stored in memory, so a text condition can only match what
   the desc happens to quote — retroactive agreed/disagreed underestimates
   hits (labelled in the output, but still). Direction: an `--audit --reread`
   variant that re-extracts page 1 of the judged files it replays, at the
   cost of the read.

4. **No per-rule triage cap.** v1 rules could declare `level: propose` ("bank
   statements: a human always confirms"). v2 has no equivalent: once promoted
   `active`, a rule routes — there is no way to say "this rule may only ever
   propose". The production config carries exactly this intent on its banking
   rule, currently expressed as a comment. Direction: an optional `cap:
   propose` honoured at application time, checked at promotion.

5. **Containers are opaque by fiat.** Zips/archives are `opaque: "container"`
   (correct: unrenderable, so never `needs_vision`), but their entry *names*
   are cheap evidence collect could harvest as text — often enough to triage
   without opening anything. Direction: list entry names (bounded, say 50)
   into `text`, flagged so the prose gate does not mistake it for a read.

6. **`--full-audit` samples the head of the walk.** Beyond 500 candidates it
   takes the first 500 by path order — an alphabetical prefix, not a sample.
   A rule whose overreach lives in `Z-archive/` audits clean. Direction:
   stratify by top-level folder, or take a seeded random sample (seed passed
   in, never generated — scripts stay deterministic).

7. **A rename orphans the symlinks that point at the file.** A corpus may
   file deliberately in links (an archived mail-out pointing at the canonical
   originals). Renaming an original leaves those links dangling: apply reports
   the broken link on the NEXT pass (`FAIL … dangling symlink`) but never
   repairs it, and the pass that caused it says nothing. Observed in
   production the first time a rename hit a linked file. Direction: before a
   `move`/`rename`, scan for links resolving to the source and re-point them
   in the same gesture — or at minimum name them in the dry-run so the
   operator sees what the rename is about to break.

8. **Inbox guard vs promoted rules — a watch-point, not a bug.** The inbox
   guard preempts rules by design (nothing routes silently out of an inbox),
   which means even a promoted `active` rule never fires on inbox files: they
   are proposed forever. That is the contract as decided; watch whether the
   friction in practice (every Downloads invoice needing a click) justifies a
   per-inbox `allow_rules: true` escape hatch some day.
