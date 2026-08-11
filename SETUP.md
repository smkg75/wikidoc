# Setup

Read once in the life of an installation, then never again — which is why it is
not in `SKILL.md`.

The output is a `config.yaml` already filled from evidence found in the user's
own documents, and a workspace ready for a first pass. Ten minutes.

Work in `$WIKIDOC_HOME` (default `~/.wikidoc`); create it if it is missing.

## 1. PROBE

Find out what this machine can actually do, by trying it rather than by assuming
from the platform name.

```bash
python3 -VV
python3 -c "import importlib.util as u;print({m:bool(u.find_spec(m)) for m in ('yaml','pypdf','pypdfium2','send2trash','striprtf')})"
python3 -c "import sys;print(sys.platform)"
```

`yaml` is required (`python3 -m pip install --user pyyaml`; on a PEP 668 Python
add `--break-system-packages`, or make a venv and use its interpreter). The
other four are optional: without them PDFs land in the `residual` lane and
removal falls back to a folder in the workspace, which is a slower pass, not a
wrong one.

Then probe the filesystem itself, in the user's document root. **Count the cloud
placeholders first** — the other two probes create empty files, and empty files
are exactly what that count looks for:

- **cloud placeholders** — look for `.icloud` files or zero-byte stand-ins under
  the root. Files that are not materialised have no bytes to classify.
- **xattr round-trip** — write an extended attribute on a scratch file and read
  it back. Success means Finder comments and tags will hold; failure means the
  ledger is the only record, which is fine.
- **case sensitivity** — create `.probe`, then try `.PROBE`. One file means the
  volume folds case, and two names differing only by case are one document.

Done when each capability is a yes or a no you have observed, and the scratch
files are gone.

## 2. SEED

Write the smallest `config.yaml` that lets the pipeline run — `root`, the
obvious `exclude` globs (version control, dependency folders, code checkouts),
and `identifiers:` for whatever numbers identify a company or an account where
the user lives. Only `iban` and `email` are built in; without a pattern for the
local company number, step 3 has nothing to count. `config.example.yaml` holds
the key names.

Create empty `ledger.jsonl` and `journal.md` beside it.

Done when `python3 scripts/ledger.py stats` prints an empty ledger without error.

## 3. MINE

Let the pipeline do the reading: `python3 scripts/prepare.py 300` extracts text,
identifiers and dates from a real sample. Then read `batch/prep/*.json` and pull
out:

- identifiers by frequency — company numbers, tax ids, IBANs, and how many files
  each appears in;
- folders by volume, and which of them look like inboxes rather than homes;
- recurring senders and document families (the same issuer, monthly);
- the extensions that produced no text at all.

Done when you can name the top identifiers with their counts, the top folders
with their file counts, and the document families that repeat.

## 4. GRILL

Ask only what the evidence could not settle, and anchor every question in what
you found — a question carrying its own count gets an answer, an abstract one
gets a shrug.

> SIREN 123456789 appears in 47 files — which company is that, and which folder
> should its documents live in?
> 212 files sit on the Desktop — is that an inbox to empty, or a real workspace
> to leave alone?
> Payslips appear under two different names — same employer, or two?

Cover: who the documents belong to — a person, a household, an employer, a
company — and how each is recognised in the text; where each files; what counts
as sensitive here; what to leave untouched; the tag taxonomy and its colours;
the language summaries should be written in.

Most people have one or two entities and no company at all. An entity with no
identifier is normal: it is recognised by a `match:` on the text instead.

Done when every open question has an answer, or is written into
`wiki/index.md` as an open question with its date.

## 5. WRITE

Fill `config.yaml` from the answers, using `config.example.yaml` as the shape —
read it, do not copy it. Comments are part of the contract: the agent reads this
file too, so write the `notes:` that a stranger would need.

Seed the first rules from the document families found in MINE, each as
`status: shadow` with `learned_from`, `passes: 0`, `hits: 0`, `agreed: 0`,
`disagreed: 0`. A shadow rule is evaluated on every pass and never applied; it
is put to the user once it has proved itself. Any other status makes the rule
do nothing at all.

Then `wiki/index.md`: the entities, their periods, and the arbitrations already
known. Clear the bench (`rm -r batch/*`) — the mining sample was not a pass, and
nothing about it belongs in the ledger.

Done when `config.yaml` parses, everyone and everything the user named has an
entry under `entities:`, and `batch/` is empty.

## 6. DRY RUN

`python3 scripts/prepare.py 50` then `python3 scripts/route.py --dry-run`.

Read the lanes out loud with the user: what routes, what is proposed, what falls
to residual, and whether any sensitive file was about to route automatically.

Done when the user recognises their own documents in the routing, and the run
has touched nothing — verified by `ledger.jsonl` still being empty.
