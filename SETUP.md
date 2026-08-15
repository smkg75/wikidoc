# Setup

Read once in the life of an installation, then never again — which is why it is
not in `SKILL.md`.

The output: a `config.yaml` filled from evidence found in the user's own
documents, instruction files that point at the wiki instead of duplicating it,
and a first real pass already run over every configured inbox. Work in
`$WIKIDOC_HOME` (default `~/.wikidoc`); create it if missing. Invoke scripts by
absolute path from the skill directory, as `SKILL.md` says.

## 1. PROBE

Find out what this machine can do by trying it, not by assuming from the
platform name.

```bash
python3 -VV
python3 -c "import importlib.util as u;print({m:bool(u.find_spec(m)) for m in ('yaml','pypdf','pypdfium2','send2trash','striprtf')})"
```

`yaml` is required (`python3 -m pip install --user pyyaml`; on a PEP 668 Python
add `--break-system-packages`, or make a venv and use its interpreter). The
others are optional: without the PDF libraries, PDFs go through vision; without
`send2trash`, removal falls back to a workspace folder. A slower pass, not a
wrong one.

Then the filesystem, in the user's document root. **Count the cloud
placeholders FIRST** — the other probes create empty files, and empty files are
exactly what that count looks for:

- **cloud placeholders** — `.icloud` files or zero-byte stand-ins: files that
  are not materialised have no bytes to classify.
- **xattr round-trip** — write an extended attribute on a scratch file, read it
  back. Failure means `memory.jsonl` is the only record, which is fine.
- **case sensitivity** — create `.probe`, then try `.PROBE`. One file means the
  volume folds case, and two names differing only by case are one document.

Done when each capability is a yes or a no you have observed, and the scratch
files are gone.

## 2. SURVEY

Three Explore agents in parallel, one zone each: **Desktop**, **Downloads**,
**Documents**. Each reports: volume (files, bytes), the document families that
repeat (same issuer, monthly), folders that look like inboxes rather than
homes, and every identifier pattern seen — company numbers, account numbers,
tax ids — with rough counts. Nothing is moved, nothing is written.

Done when you can name the top identifiers with their counts, the top folders
with their file counts, and the repeating families, for all three zones.

## 3. GRILL

Interview the user, every question anchored in a count — a question carrying
its own evidence gets an answer; an abstract one gets a shrug.

> One company number appears in 47 files — which company is that, and where do
> its documents live?
> 212 files sit on the Desktop — an inbox to empty, or a workspace to leave
> alone?
> Payslips appear under two employer names — same employer, or two?

Write `config.yaml` EARLY — after the first answers, not at the end — and
re-grill from it: read it back block by block; every block the user corrects is
a misunderstanding you almost shipped. Note context into `wiki/context.md` as
it comes — who is who, which entity was live when — not into your own head.

Cover: the entities and how each is recognised in text (most people have one or
two and no company; an entity with no identifier is normal — it matches on
text); what counts as sensitive here; what must never be touched; the tag
taxonomy; the corpus language.

Done when every open question is answered, or dated in `wiki/context.md`.

## 4. LAYOUT

The target tree — where documents will live. Offer three named options, then a
free choice:

- **by-entity** — one top folder per entity, life domains inside
  (`Personal/Banking/`, `AcmeCorp/Accounting/`).
- **by-domain** — one top folder per domain, entities inside
  (`Banking/Personal/`, `Banking/AcmeCorp/`).
- **flat-years** — shallow domains with years inside (`Taxes/2026/`), for a
  small corpus.
- **your own** — the user sketches it; restate it as a tree and have them
  confirm.

Then the inboxes, defaults on the table: **Downloads empty** (policy `empty` —
everything files out), **Desktop transit** (policy `transit` — work in progress
tolerated, residue proposed each pass), everything under Documents by entity.
Store each answer in `inboxes:` — an inbox file is always proposed, never
silently routed.

Done when the tree is in config `layout:` and every inbox has a policy the user
chose.

## 5. WRITE

Finish `config.yaml` with `config.example.yaml` as the shape — read it, do not
copy it; the comments are part of the contract. Each identifier from SURVEY
gets a pattern and a `validate:` (`luhn`, `iban`, or `none`) — a candidate that
fails its check is not an id. `anchors:` lists the user's instruction files so
every pass checks their pointers. Seed rules from the SURVEY families, each
born `status: shadow`, counters at 0, `learned_from` filled — evaluated every
pass, never applied until the user promotes them. Create empty `memory.jsonl`;
complete `wiki/context.md`.

Done when `config.yaml` parses, `memory.py stats` prints an empty memory, and
everyone the user named has an entry under `entities:`.

## 6. ANCHOR

Audit the user's instruction files (CLAUDE.md, AGENTS.md…). Two kinds of lines:

- **Process lines stay.** A hard rule gating an irreversible gesture —
  "removal = OS bin only, never rm" — MUST stay in the instructions file: it
  holds in sessions that never open the wiki.
- **Corpus facts move** to `wiki/context.md` — who is who, which folder holds
  what, entity histories. Each moved fact is replaced by a CONDITIONAL pointer,
  "situation → path", never a bare link:

> Filing or searching personal documents → read `~/.wikidoc/wiki/context.md`.
> A question about an entity's history → `~/.wikidoc/wiki/context.md`.

Back up the original to `<workspace>/legacy/` before touching it. Mechanically
check every pointer — each backticked path must resolve. Show the user the full
diff before writing anything.

Done when the instruction files hold process and pointers only, the backup
exists, every pointer resolves, and the user approved the diff.

## 7. FIRST SWEEP

A real first pass, scoped to the inboxes by the selection order itself: inbox
files select ahead of the rest — so run `collect.py N` with N = the SUM of the
file counts of ALL the inboxes in `inboxes:`, not just Desktop + Downloads.
GRILL routinely surfaces a third inbox, and a sweep sized on two of them
starves the rest in silence; collect.py prints selected/remaining counts per
inbox — check that no inbox was left behind before going on. Full machinery,
nothing weakened: Vision on every unread scan, Route, Decide with the user
answering blocking questions as they arise, the Apply dry-run read together,
then `--execute`.

The honest target: an `empty`-policy inbox (Downloads) emptied or nearly so; a
`transit` inbox (Desktop) reduced to its legitimate work in progress — a
Desktop full of live WIP will not visibly empty, and promising that is a
promise the pass cannot keep. Whatever remains is named, never waved at.

End on a **bilan**: what moved where (counts plus a few sample paths), and the
residues BY NAME — each one recorded `unanswered`, so the next pass selects it
first and opens with its question.

Then tell the user, explicitly: continue in this same conversation, or start a
fresh one — both work, `memory.jsonl` carries the state.

Done when the pass is archived under `logs/`, every inbox's remaining count is
one you can explain, and every residue is named in the bilan and recorded
`unanswered`.
