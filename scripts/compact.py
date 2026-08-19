#!/usr/bin/env python3
"""Collapse the per-file lines of one directory into a single `type: "dir"` line.

For homogeneous binary payloads ONLY — DICOM slices, viewer exports, photo
plates: subtrees where every per-file line repeats the same reason and desc
and the granularity buys nothing. A folder of heterogeneous documents (each
with its own desc, its own date_doc) stays line-per-file — that is where the
granularity pays. The full decision text lives in the wiki (decisions.md);
the dir line's `reason` stays short and points there.

The dir line carries the subtree fingerprint (count, total_size, max_mtime,
tree_md5) computed from DISK at compaction time — collect.py checks it every
pass and prunes the subtree when it holds. `--keep` globs preserve the
individual lines of the text-bearing pieces (compte rendu, prescription…):
their md5s stay in by_md5, so dedup and known_as keep working for them.

Refuses to compact when any file on disk under the directory has no memory
line, or a line still `unanswered`/`refused`/undecided — compaction must
never hide an unsorted file behind a green fingerprint.

This is the ONE writer that rewrites memory.jsonl (append-only is the law of
passes, not of maintenance): backup first, temp + rename, then the dir line
is appended. Idempotent — recompacting a compacted dir refreshes its line.

Usage:
    compact.py <dir> --reason TEXT [--keep GLOB ...] [--desc TEXT]
               [--tags a,b] [--date-doc YYYY-MM-DD] [--dry-run]
"""
import argparse
import fnmatch
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import (Memory, dir_fingerprint, is_inside, nfc,  # noqa: E402
                    require_config)


def kept(rel_under, keep_globs):
    base = os.path.basename(rel_under)
    return any(fnmatch.fnmatch(rel_under, g) or fnmatch.fnmatch(base, g)
               for g in keep_globs)


def main():
    ap = argparse.ArgumentParser(
        description="collapse one directory's file lines into a dir line")
    ap.add_argument("dir")
    ap.add_argument("--reason", required=True,
                    help="short — the full decision lives in wiki/decisions.md")
    ap.add_argument("--keep", action="append", default=[], metavar="GLOB",
                    help="files whose individual lines survive (repeatable)")
    ap.add_argument("--desc")
    ap.add_argument("--tags", help="comma-separated")
    ap.add_argument("--date-doc", dest="date_doc")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = require_config()
    mem = Memory(root=cfg["root"])
    abs_dir = os.path.abspath(os.path.expanduser(args.dir))
    if not os.path.isdir(abs_dir):
        sys.exit(f"not a directory: {abs_dir}")
    if not is_inside(abs_dir, cfg["root"]):
        sys.exit(f"outside root {cfg['root']}: {abs_dir}")
    rel = mem.rel(abs_dir)
    prefix = rel + "/"

    # every file on disk must be covered by a decided line — same file set as
    # the fingerprint (no .DS_Store, no symlinks), or the fingerprint would
    # vouch for files nobody triaged
    blockers = []
    for dp, dns, fns in os.walk(abs_dir, followlinks=False):
        dns.sort()
        for fn in sorted(fns):
            if fn == ".DS_Store":
                continue
            p = os.path.join(dp, fn)
            if os.path.islink(p):
                continue
            r = mem.by_path.get(mem.rel(p))
            if r is None:
                blockers.append((mem.rel(p), "no memory line"))
            elif not r.get("decision") or r["decision"] in ("unanswered",
                                                            "refused"):
                blockers.append((mem.rel(p), f"decision={r.get('decision')}"))
    if blockers:
        for b in blockers[:20]:
            print(f"BLOCKED {b[0]} <- {b[1]}", file=sys.stderr)
        sys.exit(f"{len(blockers)} file(s) not decided under {rel} — "
                 "triage them first, compaction hides nothing")

    covered = {p: r for p, r in mem.by_path.items()
               if p.startswith(prefix) and not kept(p[len(prefix):], args.keep)}
    if not covered:
        sys.exit(f"no collapsible lines under {rel} (all kept or none exist)")

    def dominant(field):
        vals = [r.get(field) for r in covered.values() if r.get(field)]
        if not vals:
            return None
        keys = [json.dumps(v, ensure_ascii=False, sort_keys=True) for v in vals]
        return vals[keys.index(max(set(keys), key=keys.count))]

    fp = dir_fingerprint(abs_dir, with_md5=True)
    tags = ([t.strip() for t in args.tags.split(",") if t.strip()]
            if args.tags else dominant("tags"))
    rec = {"type": "dir", "path": rel,
           "pass": datetime.now().strftime("%Y-%m-%d") + "-compact",
           "triage": dominant("triage") or "propose",
           "decision": dominant("decision"),
           "reason": args.reason, **fp,
           "provenance": dominant("provenance") or "pass"}
    for k, v in (("desc", args.desc or dominant("desc")), ("tags", tags),
                 ("date_doc", args.date_doc or dominant("date_doc"))):
        if v:
            rec[k] = v

    # rewrite: drop EVERY line (all generations) for the collapsed paths and
    # any previous line of this dir record, then append the fresh dir line
    dropped, out = 0, []
    with open(mem.path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                r = json.loads(s)
            except ValueError:
                out.append(line.rstrip("\n"))
                continue
            key = nfc(r.get("path", ""))
            if r.get("type") == "dir" and key.rstrip("/") == rel:
                dropped += 1
                continue
            if key.startswith(prefix) and not kept(key[len(prefix):],
                                                   args.keep):
                dropped += 1
                continue
            out.append(line.rstrip("\n"))
    out.append(json.dumps(rec, ensure_ascii=False))

    before = os.path.getsize(mem.path)
    if args.dry_run:
        print(json.dumps({"dir": rec, "lines_dropped": dropped,
                          "bytes_before": before}, ensure_ascii=False, indent=1))
        return
    stamp = mem.path + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    bak, n = stamp, 0
    while os.path.exists(bak):        # two compactions in one second must
        n += 1                        # not share a backup — the second write
        bak = f"{stamp}-{n}"          # would eat the first
    with open(mem.path, "rb") as src, open(bak, "wb") as dst:
        dst.write(src.read())
    tmp = mem.path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, mem.path)

    # proof, not promise: reload and re-check the guard the next pass will use
    mem2 = Memory(root=cfg["root"])
    drec = mem2.dirs.get(rel)
    live = dir_fingerprint(abs_dir, with_md5=True)
    ok = drec is not None and all(live[k] == drec.get(k) for k in live)
    print(json.dumps({"dir": rel, "count": fp["count"],
                      "lines_dropped": dropped,
                      "bytes_saved": before - os.path.getsize(mem.path),
                      "backup": os.path.basename(bak),
                      "fingerprint_verified": ok}, ensure_ascii=False, indent=1))
    if not ok:
        sys.exit("fingerprint does NOT verify after rewrite — memory restored "
                 f"from {bak} is the fallback; do not trust this compaction")


if __name__ == "__main__":
    main()
