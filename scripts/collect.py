#!/usr/bin/env python3
"""Step ① — choose the pass's files, read their evidence.

Selection has no cursor and no xattr: memory.jsonl IS the seen-set. A file is
a candidate when memory has no line for its path, when its (size, mtime)
changed and the content really is new (same md5 = a touch or a copy back, not
new work), or when its last decision is `unanswered`. Unanswered files come
back FIRST, then whatever sits in the inboxes, then the rest — in sorted walk
order, so two runs over the same disk pick the same batch. The walk starts at
`root` and at every inbox outside it (Desktop, Downloads), which is the only
way those ever get scanned; a scan of zero files against a non-empty memory
ends the pass with an error, never a green report.

Evidence, per file: page-1 text truncated at ~4000 chars (`truncated` flag),
page count, validated identifiers and dates, byte-identical duplicates by
(size, md5) with no size threshold and no group cap — the thresholds were
exactly where an earlier extractor's misses lived. No readable text and more than 1 KiB of
bytes → `needs_vision` plus a page-1 PNG; at 1 KiB or less → opaque
"no-content".

Writes bench/routing.json (evidence columns only, atomic), bench/renders/,
bench/logs/collect.log — counts mirrored on stdout. Interpretation belongs to
the agents downstream; this script only converts bytes.

Usage:
    collect.py [N]                        select and extract a batch
                                          (default batch_size, else 600)
    collect.py --render PATH --pages A-B  on-demand page renders for an agent
                                          whose harness cannot read the file

Nothing imports collect.py.
"""
import argparse
import json
import os
import re
import sys
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import (Memory, dir_fingerprint, file_md5, flatten,  # noqa: E402
                    inbox_dirs, is_inside, nfc, pass_roots, require_config,
                    self_ingestion_guard, write_json_atomic)
import extract  # noqa: E402

TEXT_CAP = 4000
OPAQUE_MAX = 1024          # no text and this few bytes: nothing to see either

# extension tables live in extract.py, next to the readers they gate
GATED_EXT = {".pdf"} | set(extract.ZIP_XML)   # extractor-mediated: debris can
                                              # pass for text


# ------------------------------------------------------------- selection ----
def excluded(rel, patterns):
    rel = "/" + rel.replace(os.sep, "/")
    return any(fnmatch(rel, p if p.startswith(("/", "*")) else "*/" + p)
               for p in patterns)


def hashed(path, size, md5s):
    """One md5 per path per pass — selection, dedup and the entry share it."""
    if path not in md5s:
        md5s[path] = file_md5(path, size)
    return md5s[path]


def walk_select(cfg, mem, limit, md5s):
    """Candidates in (priority, walk-order), plus sizes of the whole corpus.

    Sizes of everything scanned come back too: duplicate detection needs the
    whole disk, not just the batch. Only stat-drifted files are hashed here —
    that is what keeps a pass over tens of thousands of files cheap.

    What the walk cannot take comes back named in `ignored` — dangling
    symlinks, unstatable files, AND directories it was refused (`onerror`).
    A skip nobody can see is a file that silently never gets sorted, and a
    refused directory nobody sees is an empty report that reads like a clean
    one: os.walk swallows every scandir error by default, so a root gone
    missing or locked by the OS looks exactly like a corpus already tidy.
    """
    guards = self_ingestion_guard(cfg)
    excl = cfg.get("exclude", [])
    inboxes = inbox_dirs(cfg)
    sizes, cands, ignored = {}, [], []
    counts = {"scanned": 0, "seen": 0, "unreadable_dirs": 0}

    def refused(err):
        counts["unreadable_dirs"] += 1
        ignored.append((nfc(getattr(err, "filename", "") or "?"),
                        f"unreadable directory ({err.strerror or err})"))

    bad_roots = []
    for wroot in pass_roots(cfg):
        # a root that cannot be opened is asked about, never inferred: a typo
        # in `root:`, an unmounted volume and a permission the OS withdrew all
        # end the walk before its first iteration, and os.walk reports none of
        # the three. Checked here so the answer is "this root", not "0 files".
        if not os.path.isdir(wroot) or not os.access(wroot, os.R_OK | os.X_OK):
            bad_roots.append(nfc(wroot))
            ignored.append((nfc(wroot), "walk root unreadable — missing, "
                                        "unmounted, or refused by the OS"))
            continue
        for dirpath, dirnames, filenames in os.walk(wroot, followlinks=False,
                                                    onerror=refused):
            if any(is_inside(dirpath, g) for g in guards):
                dirnames[:] = []
                continue
            drec = mem.dirs.get(mem.rel(dirpath))
            if drec is not None:
                # one line covers this whole subtree (`type: "dir"`). Cheap
                # check first — count, bytes, newest mtime; on drift, the
                # tree_md5 decides touch vs new work, exactly as the per-file
                # md5 re-check does. Identical content behind drifted stats
                # stays seen but is named in the log: the fingerprint will be
                # recomputed every pass until compact.py refreshes the line.
                # Real drift = the whole subtree comes back as candidates
                # (its files have no lines of their own — that is the deal).
                fp = dir_fingerprint(dirpath, with_md5=False)
                stat_ok = all(fp[k] == drec.get(k) for k in
                              ("count", "total_size", "max_mtime"))
                if not stat_ok and drec.get("tree_md5"):
                    stat_ok = (dir_fingerprint(dirpath, with_md5=True)
                               ["tree_md5"] == drec["tree_md5"])
                    if stat_ok:
                        ignored.append((nfc(dirpath),
                                        "dir line stale (touched, content "
                                        "identical) — re-run compact.py to "
                                        "refresh the fingerprint"))
                if stat_ok:
                    counts["scanned"] += fp["count"]
                    counts["seen"] += fp["count"]
                    counts["seen_dirs"] = counts.get("seen_dirs", 0) + 1
                    dirnames[:] = []
                    continue
                ignored.append((nfc(dirpath),
                                "dir line DIVERGED — subtree re-collected"))
            rel_dir = os.path.relpath(dirpath, wroot)
            dirnames[:] = sorted(d for d in dirnames
                                 if not excluded(os.path.join(rel_dir, d), excl))
            for fn in sorted(filenames):
                if fn == ".DS_Store":
                    continue
                p = os.path.join(dirpath, fn)
                if excluded(nfc(os.path.relpath(p, wroot)), excl):
                    continue
                key = mem.rel(p)
                try:
                    st = os.stat(p)
                except OSError:
                    ignored.append((nfc(p), "dangling symlink"
                                    if os.path.islink(p) else "unstatable"))
                    continue
                size, mtime = st.st_size, int(st.st_mtime)
                counts["scanned"] += 1
                if not os.path.islink(p):
                    # a link is never a duplicate candidate — neither its own
                    # (it carries the target's size and md5), nor its target's:
                    # leaving it in `sizes` marks the ORIGINAL as a duplicate
                    # of its own pointer
                    sizes.setdefault(size, []).append(p)
                inbox = next((disp for disp, ap in inboxes if is_inside(p, ap)),
                             None)
                rec = mem.by_path.get(key)
                if rec is not None and rec.get("decision") in ("unanswered",
                                                               "refused"):
                    # `refused` rides the same band: a guard kept the file, so
                    # it is still unfiled and must come back — but the ledger
                    # says a decision WAS made and refused, not that nobody
                    # could read it.
                    cands.append((0, p, size, mtime, inbox))  # re-selected first
                    continue
                if rec is not None and (rec.get("size"), rec.get("mtime")) == (size, mtime):
                    counts["seen"] += 1
                    continue
                if rec is not None:
                    # stat drifted: re-check the content before calling it new —
                    # same md5 is a touch, not new work
                    if hashed(p, size, md5s) == rec.get("md5"):
                        counts["seen"] += 1
                        continue
                cands.append((1 if inbox else 2, p, size, mtime, inbox))
    cands.sort(key=lambda c: c[0])      # stable: walk order within each band
    counts["unreadable_roots"] = bad_roots
    counts["candidates"] = len(cands)
    picked = cands[:limit]
    # per-inbox accounting: a sweep sized on one inbox starves the others,
    # and only these numbers show it
    per = {disp: {"candidates": 0, "selected": 0} for disp, _ in inboxes}
    for c in cands:
        if c[4]:
            per[c[4]]["candidates"] += 1
    for c in picked:
        if c[4]:
            per[c[4]]["selected"] += 1
    counts["inboxes"] = {d: {**v, "remaining": v["candidates"] - v["selected"]}
                         for d, v in per.items()}
    return picked, sizes, counts, ignored


# ------------------------------------------------------------ extraction ----
def page1_text(path, ext):
    """First-page text, or None when no extractor reads this format.

    None means "not read" (minimal mode, no reader); "" means "read and
    empty". Both feed the same needs_vision gate, but the distinction must
    survive extract.py — an unread PDF must never pass for an empty one.
    """
    if ext == ".pdf":
        return extract.pdf_text(path)
    if ext in extract.ZIP_XML:
        return extract.zip_text(path, extract.ZIP_XML[ext])
    if ext == ".rtf":
        return extract.rtf_text(path)
    if ext in extract.IMG_EXT:
        return None
    if ext in extract.TEXT_EXT or extract.looks_textual(path):
        return extract.read_text_file(path)
    return None


def prep_entry(cfg, path, size, mtime, ws, render_dir, idx, md5s):
    ext = os.path.splitext(path)[1].lower()
    e = {"path": path, "size": size, "mtime": mtime, "ext": ext or "?",
         "md5": hashed(path, size, md5s),
         "text": "", "truncated": False, "prose": False,
         "needs_vision": False, "ids": {}}
    if os.path.islink(path):
        # A symlink is a POINTER, not a document. os.stat follows it, so its
        # size and md5 are the target's — which makes every link look like a
        # byte-identical duplicate of the file it points at, and a dedup rule
        # would bin the link. Some corpora file deliberately in links (an
        # archived mail-out that must not duplicate the originals); binning
        # those destroys the structure. So: no hash, no duplicate group, no
        # vision, no reading. It gets a memory line and stops being work.
        # (a link whose target is gone never reaches here: the walk's stat
        # fails and it comes back as `ignored: dangling symlink`)
        e["md5"] = None
        e["opaque"] = "symlink"
        e["link_to"] = nfc(os.path.realpath(path))
        return e
    if e["md5"] is None and size < (1 << 30):     # past 1 GB md5 is skipped,
        e["error"] = "unreadable"                 # below it None means EPERM
    if ext == ".pdf":
        n = extract.pdf_pages(path)
        if n is not None:
            e["pages"] = n
    raw = page1_text(path, ext)
    prose = bool(raw) and extract.looks_like_prose(raw)
    if raw and ext in GATED_EXT and not prose:
        raw = ""        # a broken font yields characters, not language
    if raw:
        e["prose"] = prose
        e["truncated"] = len(raw) > TEXT_CAP
        e["text"] = raw[:TEXT_CAP]
        flat = flatten(raw)
        ids = extract.extract_ids(flat, cfg)
        if ids:
            e["ids"] = ids
        dates = extract.extract_dates(flat)
        if dates:
            e["dates"] = dates
            year = extract.doc_year(dates)
            if year:
                e["doc_year"] = year
    elif ext in extract.CONTAINER_EXT:
        # nothing extracts an archive and nothing renders one: `needs_vision`
        # would be unmeetable. Residual lane; the decide agent opens it if it
        # wants to, recording `lu: "container"` when it reads the listing.
        e["opaque"] = "container"
    elif size > OPAQUE_MAX:
        e["needs_vision"] = True
        out = os.path.join(render_dir, f"{idx:04d}-p1.png")
        ok = (extract.pdf_render(path, out) if ext == ".pdf"
              else extract.image_render(path, out) if ext in extract.IMG_EXT
              # .doc, .xls, .pages, .numbers…: no extractor here and no renderer
              # of their own, so this is the difference between a vision step
              # that can look and one that only has a filename
              else extract.office_render(path, out) if ext in extract.QL_EXT
              else False)
        if ok:
            e["render"] = os.path.relpath(out, ws)
    else:
        e["opaque"] = "no-content"
    return e


# ------------------------------------------------------------------ main ----
def render_verb(cfg, path, pages):
    """On-demand renders for an escalating agent. The READER is the agent —
    this verb only converts pages to pixels."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        sys.exit(f"no such file: {path}")
    render_dir = os.path.join(cfg["workspace"], "bench", "renders")
    os.makedirs(render_dir, exist_ok=True)
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", pages or "1")
    if not m:
        sys.exit("--pages wants A-B or N (1-based)")
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    ext = os.path.splitext(path)[1].lower()
    stem = re.sub(r"[^\w-]+", "-",
                  os.path.splitext(os.path.basename(path))[0])[:40] or "page"
    done = []
    for page in range(first, last + 1):
        out = os.path.join(render_dir, f"{stem}-p{page}.png")
        ok = (extract.pdf_render(path, out, page=page) if ext == ".pdf"
              else extract.image_render(path, out))
        if ok:
            done.append(out)
            print(out)
        if ext != ".pdf":
            break               # an image has one page
    if not done:
        sys.exit(f"no render produced for {path}")


def main():
    ap = argparse.ArgumentParser(
        description="step 1: choose the pass's files, read their evidence")
    ap.add_argument("limit", nargs="?", type=int,
                    help="batch size (default: batch_size from config, else 600)")
    ap.add_argument("--render", metavar="PATH",
                    help="render pages of one file and exit")
    ap.add_argument("--pages", metavar="A-B", default="1",
                    help="page range for --render (1-based, default 1)")
    args = ap.parse_args()

    cfg = require_config()
    if args.render:
        render_verb(cfg, args.render, args.pages)
        return

    mem = Memory(root=cfg["root"])
    limit = args.limit if args.limit is not None else int(cfg.get("batch_size", 600))
    ws = cfg["workspace"]
    bench = os.path.join(ws, "bench")
    render_dir, log_dir = os.path.join(bench, "renders"), os.path.join(bench, "logs")
    for d in (render_dir, log_dir):
        os.makedirs(d, exist_ok=True)

    md5s = {}
    picked, sizes, counts, ignored = walk_select(cfg, mem, limit, md5s)
    counts.update(selected=len(picked),
                  unanswered=sum(1 for c in picked if c[0] == 0),
                  inbox=sum(1 for c in picked if c[0] == 1),
                  ignored=len(ignored),
                  with_text=0, needs_vision=0, opaque=0,
                  duplicates=0, known_content=0, errors=0)

    entries, lines = [], []
    for idx, (_, p, size, mtime, _ib) in enumerate(picked, 1):
        e = prep_entry(cfg, p, size, mtime, ws, render_dir, idx, md5s)

        # identity is (size, md5) — every size twin gets hashed, no threshold,
        # no group cap: a 200-byte duplicate is still a duplicate. Except at
        # zero bytes: every empty file (cloud placeholders, lock stubs) is
        # trivially byte-identical to every other, and emptiness is not
        # identity — no duplicate group, no known_as match.
        if e["md5"] and size:
            twins = [q for q in sizes.get(size, [])
                     if q != p and hashed(q, size, md5s) == e["md5"]]
            if twins:
                e["duplicate_of"] = twins
                counts["duplicates"] += 1
            # same content already recorded under another name: a move,
            # not new work — route.py will triage it `skip`. Only while the
            # recorded copy still exists: if it is gone (trashed, moved out),
            # this file is the only copy now, and a `why` must never cite a
            # path that no longer resolves.
            prev = mem.seen_md5(e["md5"])
            if isinstance(prev, list):
                prev = prev[-1] if prev else None
            if prev and prev.get("path") != mem.rel(p) \
                    and os.path.exists(mem.abs(prev.get("path", ""))):
                e["known_as"] = prev.get("path")
                if prev.get("desc"):
                    e["known_desc"] = prev.get("desc")
                counts["known_content"] += 1

        counts["with_text"] += bool(e["text"])
        counts["needs_vision"] += e["needs_vision"]
        counts["opaque"] += "opaque" in e
        counts["errors"] += "error" in e
        entries.append(e)
        lines.append(f"{nfc(p)} ext={e['ext']} text={len(e['text'])}c"
                     + (" TRUNC" if e["truncated"] else "")
                     + (" VISION" if e["needs_vision"] else "")
                     + (f" render={e['render']}" if e.get("render") else "")
                     + (f" ids={','.join(e['ids'])}" if e["ids"] else "")
                     + (f" dup={len(e['duplicate_of'])}" if e.get("duplicate_of") else "")
                     + (f" known_as={e['known_as']}" if e.get("known_as") else "")
                     + (f" opaque={e['opaque']}" if e.get("opaque") else "")
                     + (f" ERROR={e['error']}" if e.get("error") else ""))

    # what the walk could not take, named: a silent skip is a file that
    # never gets sorted and an inbox that can never report itself empty
    skips = [f"IGNORED {path} <- {why}" for path, why in ignored]
    write_json_atomic(os.path.join(bench, "routing.json"), entries)
    with open(os.path.join(log_dir, "collect.log"), "w", encoding="utf-8") as f:
        # the counts step 1 is judged on go in the log too: stdout scrolls
        # away, and re-running the script to see them is what the log avoids
        f.write(json.dumps(counts, ensure_ascii=False, indent=1) + "\n\n")
        f.write("\n".join(skips) + ("\n\n" if skips else ""))
        f.write("\n".join(lines) + ("\n" if lines else ""))
    print(json.dumps(counts, ensure_ascii=False, indent=1))
    for line in skips:
        print(line)
    print(f"evidence -> {os.path.join(bench, 'routing.json')}", file=sys.stderr)

    # "I could not look" and "there was nothing" are not the same answer, and
    # only one of them may end a pass quietly. A root nobody could open, or a
    # memory holding thousands of files under roots that now scan to zero, is
    # a broken setup — never a tidy corpus. Refuse the pass out loud; the next
    # steps have nothing to chew on anyway.
    if counts["unreadable_roots"]:
        sys.exit("\nunreadable walk root: %s — nothing was collected there. "
                 "Fix the path or the permission before re-running; a refused "
                 "root is not an empty one."
                 % ", ".join(counts["unreadable_roots"]))
    if counts["scanned"] == 0 and mem.lines:
        sys.exit(f"\nscanned 0 files under {', '.join(pass_roots(cfg))} while "
                 f"memory holds {mem.lines} lines — this is not an empty "
                 "corpus. Nothing was collected; check the roots before "
                 "re-running.")


if __name__ == "__main__":
    main()
