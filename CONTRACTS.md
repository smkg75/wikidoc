# wikidoc — interface contracts

The reference the code is built against, and the memory of why it is built
that way: interfaces first, then the production incidents that shaped them.
When code and contract disagree, one of the two is a bug — report it, never
deviate silently. What the agent does during a pass is `SKILL.md`'s to say;
this file states what the scripts guarantee, and repeats nothing the runtime
files already carry. Deferred work lives in [`BACKLOG.md`](BACKLOG.md).

Work happens IN THIS REPO on `main` — one branch, no feature branches: the
repo has a single author who iterates in place. Every change ends with a
commit AND a push to `origin/main` (github.com/smkg75/wikidoc, public).
`~/.wikidoc/` is the production workspace and is never versioned here.

Language: all code, comments and docs in English. Python 3.9+ stdlib;
optional deps (`pypdf`, `pypdfium2`, `send2trash`, `striprtf`; `yaml`
required) follow the existing try/except pattern.

## Architecture rule

Three **steps** (verbs, executed, never imported):
`collect.py` · `route.py` · `apply.py`.
Three **libraries** (nouns, imported, never steps):
`memory.py` · `extract.py` · `enrich_macos.py`.

**A step never imports or invokes another step. Steps import libraries.**
Scripts convert bytes and move files; they never interpret. Interpretation —
reading a document, writing a desc, deciding — belongs to agents.

## The scripts are optional; the record is not

The hand-filing protocol is the SKILL.md invariant of the same name — what a
hand gesture owes (`Memory.record(...)`, `triage: "propose"`, a `reason` that
says why, written after the destination is re-stat'ed) lives there and only
there. Two contract-side additions: `provenance: "human-decision"` covers any
judgement made outside a pass, not only a user's word; and the verification
bound — reload `Memory` from disk and walk the destination: zero files
without a line. The one file that may legitimately have no line is one whose
destination is undecided — its absence from memory IS the open question, and
it belongs in `wiki/state.md`.

## Vocabulary

- **triage** — where a file lands after route.py: `route` · `propose` ·
  `residual` · `skip` (glosses in SKILL.md ③). JSON field `triage`; migrated
  records use `level` — readers accept both, writers emit only `triage`.
- **evidence strength** — the 3/2/1 scale and the strength-1 cap:
  SKILL.md Invariants.
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
 "triage": "route",
 "decision": "move|trash|tag|rename|none|unanswered|refused",
 "reason": "…", "size": 48213, "mtime": 1754899200, "md5": "…"|null,
 "provenance": "pass"|"migrated"|"human-decision"}
```

`desc`, `ids`, `tags`, `date_doc` are optional keys, omitted when absent —
only `md5` is ever written null (unreadable file). Migrated records
additionally carry `stat`, `at`, `level` and sometimes `unreadable`;
they are tolerated and carried, but only `level` is consumed (read-side
mapping to `triage`), and their mtime lives inside `stat`, so the
(size, mtime) fast path cannot see them — recognition falls back to the
md5 re-check. In a corpus that lived through a migration they are the
majority of the file.

`path` is NFC and root-relative, or `~/…` for a file an inbox outside root
brought in — never a `../` climb, which breaks the moment root moves.
**by_path is the primary index** (last-write-wins per path); by_md5
secondary, one md5 may map to a LIST. `stats` reports
`distinct_files = len(by_path)`.

A second record kind, `type: "dir"`, puts a whole subtree behind ONE line —
for **homogeneous binary payloads only** (DICOM slices, viewer exports, photo
plates), where per-file lines are hundreds of copies of the same reason. A
folder of heterogeneous documents stays line-per-file; that is where the
granularity pays. The full decision text lives in the wiki (decisions.md) —
the dir line's `reason` stays short and points there.

```json
{"type": "dir", "path": "Perso/Sante/…/2024-04-19 IRM cheville droite",
 "pass": "…", "triage": "…", "decision": "…", "reason": "short — wiki has the rest",
 "count": 697, "total_size": 76543210, "max_mtime": 1713545545,
 "tree_md5": "…", "provenance": "…", "desc": "…", "tags": […], "date_doc": "…"}
```

Indexed in `Memory.dirs` (relpath → record), never in by_path — a dir is not
a file and `distinct_files` must not count it. The fingerprint comes from
`dir_fingerprint(abs_dir, with_md5)` — regular files only, `.DS_Store` and
symlinks excluded, sorted walk; `tree_md5` hashes the sorted `relpath:md5`
lines. Writer (`compact.py`) and checker (`collect.py`) both call it: two
definitions of "the same subtree" is how a guard rots. Written by
`scripts/compact.py`, the ONE writer allowed to rewrite memory.jsonl
(append-only is the law of passes, not of maintenance): it refuses while any
file under the dir is undecided, backs up first (unique name even twice in
one second), rewrites atomically, appends the dir line, then re-verifies the
fingerprint from a fresh load. `--keep` globs preserve the individual lines
of text-bearing pieces so their md5s stay in by_md5 for dedup/known_as.

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
whose token set is a subset of the filename's tokens — a desc that only
rearranges the filename read nothing, and that lie must die where the data
enters.

CLI: `stats`, `show <path|md5>`, `find <term>`. **No selection — memory.py
chooses nothing.**

### extract.py — the library that reads bytes (imported, never a step)

Why it exists: three consumers need to read file content — collect.py (mass
extraction), apply.py (the sensitive probe re-reads text AND ids itself,
trusting nothing from the working file), route.py --full-audit (confronting
a text rule with the whole disk). An earlier version duplicated this code
and ended up with two notions of text equality, the weaker one guarding the
RIBs.

Provides: `pdf_text`, `pdf_render(path, out_png, page=1)` (pypdfium2 →
stdlib zlib/struct PNG encoder), `zip_text`, docx/odt/rtf text,
`read_text_file`, `looks_like_prose` (with the mid-word-capital rejection),
`image_render` (HEIC and friends via `sips`, best effort, macOS-guarded),
`extract_ids(text, cfg)`, `extract_dates(text)`, `doc_year(dates)` — and
the **condition engine**: `COND`, `matches()` (all/any/not grammar) and
`sensitive_hit(entry, cfg)`. The engine lives here because two STEPS
evaluate config conditions — route.py for triage, apply.py for the trash
probe — and steps may not import steps; the RIB incident above is what a
second copy costs. One matcher, one notion of what a condition means.

- **Identifiers are validated, not just matched**: SIREN/SIRET must pass
  Luhn; IBAN must pass mod-97; a configured pattern may declare
  `validate: luhn|iban|none`. A candidate failing its check is NOT an id
  (any 9 digits once passed the regex). `normalise_id` squeezes only
  all-digit values. The builtin IBAN pattern accepts group tails under
  4 chars — a French IBAN ends on 3 — and lets mod-97 do the real filtering.
- Dates: the inherited forms plus `MM/YYYY`. `doc_year` only from recognised
  dates, bounded 1900..current_year+1. Never a bare year harvested from text.
- `WIKIDOC_MINIMAL=1`: pdf text/render unavailable. extract.py returns None
  (distinct from ""), so callers mark `needs_vision` — minimal mode must
  NEVER quietly produce an empty text that passes for "read and empty".

### enrich_macos.py — inherited UNCHANGED

One addition allowed: warn (never fail) when a tag is absent from the
config taxonomy.

## The single working file: bench/routing.json

One entry per selected file. Each pipeline actor writes ONLY its columns;
writes are atomic (temp file + rename). Empty columns = remaining work.
The column table — who writes what, at which step — lives in `SKILL.md`
and only there: it drifted the moment it existed twice. So do `lu`, the
reader ladder, withdrawal and the answered-withdrawal path (SKILL.md ②).
Two contract details: apply records `propose` in the memory line when a
user-decided entry's triage column is empty; and the last rung before a
withdrawal is recorded is asking the user — an unreadable file is a
blocking question, not silently deferred work. Dies with the pass
(archived, not deleted).

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

One decision field per entry means the bug class "same path in moves AND
trash" is structurally impossible.

## Steps

### collect.py — step ①: choose the pass's files, read their evidence

Selection (no cursor, no xattr — memory.jsonl IS the seen-set): walk
`pass_roots(cfg)` — root, plus every inbox outside it, each a walk root of
its own. An inbox is where files ARRIVE, and Desktop/Downloads arrive
outside the tree; treating `inboxes:` as a priority band only is how the
tool once shipped with two inboxes that reported `candidates: 0` forever.
Nested inboxes fold into the root that covers them: no file is scanned
twice. Config excludes match relative to the walk root that found the file.
Candidate definition, selection order and batch size are SKILL.md ①'s;
the contract detail is the re-check — a (size, mtime) change triggers an
md5 comparison: same content = moved, recorded as `known_as`; new content =
candidate.

A directory holding a `type: "dir"` line is checked as a UNIT: stat
fingerprint match → the whole subtree pruned (`seen += count`, `seen_dirs`
counted), its files never stat'ed and never in the duplicate map. Stat
drift → `tree_md5` decides: identical content stays seen but is named in
the log ("re-run compact.py to refresh"); real drift = the subtree comes
back as candidates and the log says "dir line DIVERGED".

Extraction: **page 1 only**, text truncated at ~4000 chars, page count
kept. Non-office containers (`extract.CONTAINER_EXT`) → `opaque:
"container"`, never `needs_vision` — nothing extracts or renders an
archive, so the vision promise would be unmeetable; residual lane, the
decide agent opens one if it wants (`lu: "container"`). Otherwise: no text
AND size > 1024 → `needs_vision: true` + page-1 PNG render; no text AND
size ≤ 1024 → `opaque: "no-content"`. Duplicates grouped by (size, md5)
with **no threshold and no group cap** (a size threshold once missed 6/6
real groups) — **except zero-byte files**: every empty file is trivially
byte-identical to every other (cloud placeholders, lock stubs), so size 0
is exempt from both duplicate grouping and the `known_as` md5 match.
`known_as` is only set while the recorded copy still exists on disk — a
`why` never cites a path that no longer resolves.

**Symlinks are pointers, never documents.** `followlinks=False` stops the
walk descending into a linked directory but still lists linked FILES, and
`os.stat` follows them — so a link carries its target's size and md5 and
lands as a byte-identical duplicate of the very file it points at. A corpus
may file deliberately in links (an archived mail-out that must not duplicate
its originals); a dedup decision would bin exactly those. So a symlink gets
`md5: null`, `opaque: "symlink"` and `link_to` (resolved target), no
duplicate group, no `needs_vision`, no reading — route sends it residual
naming the target, a `none` decision gives it a memory line, and it stops
being work. A dangling link never becomes an entry at all: its stat fails
in the walk and it is counted and named under `ignored`.

Writes `bench/routing.json` + `bench/renders/` + `bench/logs/collect.log`
(counts mirrored on stdout, broken down candidates/selected/remaining PER
configured inbox, so a sweep sized on one inbox cannot starve another in
silence). Entries the walk cannot take are counted (`ignored`) and listed
by name — an invisible skip is a file that never gets sorted.

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

`collect.py --render <path> --pages A-B` — on-demand renders for agents
whose harness cannot read the original directly.

### route.py — step ③ plus the whole rule lifecycle

**Default verb** — fills the triage columns of every entry. The vision
barrier and its exemptions (known_as, withdrawals, user-decided entries)
are SKILL.md ③'s; the contract details: the barrier condition is
`needs_vision: true` AND empty `text` AND no decision, exit 2 with the
paths listed. Shadow predictions computed BEFORE guards (guards must not
blind learning). Guard order: skip (known md5) → sensitive → duplicate →
inbox → entity tie; then rules. Strength graded on the branch that MATCHED;
an empty match dict matches nothing. Destination rendering: always trailing
`os.sep`; an unresolved variable ({doc_year} with no date) → the rule does
not fire, entry goes `propose` with the variable named in `why` — never an
`undated/` folder. All text matching through `memory.norm()`. No
`--dry-run` flag exists: this verb only writes columns in bench/; dry-run
belongs to apply.py alone.

**`--learn`** — end of pass, after apply:
- For each shadow rule: compare what it would have done with each file's
  FINAL path (already in the right place = agreed); increment
  passes/hits/agreed/disagreed in config.yaml — comment-tolerant, creates
  missing fields. It is a comparison and two counters, nothing more.
- Mine candidates from this pass + memory, simplest form first:
  ① ext+source-folder ② filename pattern ③ discriminating phrase (present
  in every file that went to one destination, absent from every other file
  of the pass) ④ identifier ⑤ combinations. Keep the SIMPLEST form with
  zero counterexamples; born `status: shadow, cycle: 1`, counters 0,
  `learned_from` filled. Nothing found = nothing born.
- Report `ripe` rules (config `learning:` thresholds) — reporting only.
- Report `unanswered` BY NAME and write their memory lines so selection
  re-picks them first. No `unrecorded` arithmetic, no `--force`.
- Accept an entry whose `result` is stamped even without a triage — the
  answered-withdrawal path (apply ran before route could stamp `propose`);
  refusing it would wedge the pass, and the cold test hit exactly that.
- **Anchor check**: for each file in config `anchors:`, warn on any
  backticked path it cites that no longer resolves — a dead pointer is a
  fact that left the instructions file and arrived nowhere. A relative
  pointer resolves against the workspace AND against root before it warns;
  slash-commands are not paths.

**Rule-craft, `--learn` included, runs in a subagent** — the protocol and
the two traps it must be told (a rule mined in the pass's own context
matches the pass, not the corpus; `--audit` tests text conditions against
recorded descs, not documents) are SKILL.md ⑥'s, with the measurement that
justifies them.

**`--audit <rule-id>`** and **`--full-audit <rule-id>`** — semantics, the
desc-only caveat and the promotion requirement are SKILL.md's ("Rules",
steps 1-2). Contract details: `--full-audit` announces the candidate count
BEFORE extracting any content, and writes every file the rule would touch,
with its rendered destination, to stdout + `logs/full-audit-<rule-id>.json`.

Rule lifecycle and field shape: SKILL.md ("Rules: born shadow, promoted by
the user") and the annotated `rules:` block of config.example.yaml are the
sources of truth — this file adds nothing to them.

### apply.py — step ⑤: the only script that touches disk

Reads the decision columns of `bench/routing.json`. Dry-run by default:
prints every gesture touching nothing; `--execute` only after the printout
matches intent, and the execution report must equal the dry-run report.

Inherited verbatim where proven: bin never unlink (send2trash →
`<ws>/.trash/<pass>/` fallback), `(2)` collision never clobber, re-stat at
destination before a move counts, self-ingestion guard, trailing-`/` = file
into folder, no `sensitive:` block declared → every trash refused.

The four hard-won fixes:
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
   `pass_roots(cfg)` (a file may COME from an inbox outside root, it may
   only ever GO inside root), fails the entry before any gesture.

**`refused` is not `failed`.** A guard declining a gesture — sensitive hit,
unreadable text on a trash, no `sensitive:` block — is counted `refused` and
printed `REFUSE`; only a broken entry is `failed` and only `failed` sets the
exit code. They were one counter until "done when `failed` is 0" started
teaching agents that a working guard is a problem to route around.

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

**A refusal is recorded, and it is not a withdrawal.** The mechanics are
SKILL.md ⑤'s; `--learn` lists it under `refused`, never `unanswered`.
Recording it as `unanswered` (what shipped first) erased both the decision
and the guard: the next pass met a file that looked as if nobody had ever
read it, and proposed the same refused gesture again.

## config.example.yaml

Comments are part of the contract — the agent reads this file too, and
counter updates must be comment-tolerant. The example stays person-first
(most users run no company) and inside the Cleanliness rule below.

The workspace is the user's, not the tool's: notes, reports, scripts and
caches of their own live happily beside `config.yaml`, `memory.jsonl`,
`logs/` and `wiki/` — each install grows the wiki its owner needs. The
contract binds only the files named here; nothing may assume it owns the
directory.

## Publication scope and cleanliness

The whole repo is public — `tests/`, `CONTRACTS.md` and `BACKLOG.md`
included, published for contributors. What the *skill* loads at runtime is
only `SKILL.md`, `SETUP.md`, `config.example.yaml` and `scripts/`; the rest
is development material and never appears in a pass.

Cleanliness is a hard rule for every file:
- Zero real-user data anywhere: no real person, company, place, email,
  account or identifier — in code, comments, tests or docs. Earlier sources
  leaked (a real company name in a fixture, a real SIREN and a real syndic
  email in tests and comments): keep the lessons, replace the examples with
  invented ones.
- Test-corpus identifiers are fictitious AND pass their validators (Luhn-valid
  invented SIRENs, mod-97-valid invented IBANs) so the pipeline treats them
  as real ids without naming anyone.
- Before any release: a sweep for personal markers over the tree must
  return nothing. The marker list lives outside the repo.

## tests/ — build.py + TRAPS.md, no frozen assertions, never a skill step

There is deliberately NO regressions.py. A predecessor verify.py taught the
lesson: a suite written by the code's author lies (its duplicate fixture was
padded past the code's own threshold, and three green runs hid a dead guard).

What ships instead — nothing in the skill imports or invokes tests/; it
exists for the developer and the verification agents only:

- **tests/build.py** — the booby-trapped corpus: NFD, case twins, cp437 zip,
  Icon\r, CON.pdf, >260 chars, typographic apostrophe, re-download pair,
  a dangling symlink, an image-only sensitive scan, an MM/YYYY rent receipt,
  byte-identical duplicates UNDER 4096 bytes (unpadded), an inbox folder, an
  already-filed folder, a two-entity contract without a shared identifier.
- **tests/TRAPS.md** — the manifest: one entry per trap, what it is, and
  what correct handling looks like, pinned production bugs included.

Verification is an AGENT's job, done freely and with fresh eyes: run the
pipeline over the generated corpus (WIKIDOC_HOME at a temp dir, never
~/.wikidoc) and grade the outcome against TRAPS.md. The manifest is the
contract; how to check it is the agent's judgement. Deterministic where
being wrong is expensive (the corpus), model-driven where judgement matters
(the grading).

## Open points — assumed limits, not bugs

- **Sensitive duplicates are reducible, under proof.** Byte-identical
  copies of a sensitive document both land `propose` (duplicate + sensitive
  guards). A trash on one is refused UNLESS the entry names a `keeper` —
  the re-hash proof is SKILL.md's invariant, plus two contract details: the
  keeper must differ from the source, and a proven keeper lifts the
  unreadable-text refusal too, since neither describes a risk once the same
  bytes survive.
- **A `sensitive: supersedable:` family is the second, user-declared
  proof.** Some sensitive documents are reissued on demand — an attestation
  de droits, a justificatif de domicile, a re-downloaded RIB — so ten copies
  differ in bytes and date while saying the same thing, and byte-equality
  can never prove the survivor. `apply.superseded_by()` lifts the refusal
  when BOTH the source and the named `keeper` match the SAME family declared
  under `sensitive: supersedable:` in config.yaml, evidence re-read from
  disk at the moment of the gesture. The authority is the USER's standing
  rule, never the agent's reading of the moment.
- Beyond those two proofs, do not weaken the guard: no `reviewed:` flag, no
  other config switch, no "trust the bench" shortcut may bin a sensitive
  file. The guard was never about copy count; it is about never losing
  sensitive CONTENT.
