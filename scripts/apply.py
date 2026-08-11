#!/usr/bin/env python3
"""Execute a decided plan. Dry run unless you say otherwise.

This is the only script that changes anything on disk, so it is the one with no
settings: every safety below runs on every invocation and nothing in
`config.yaml` can turn one off.

    dry run by default          `--execute` is the only way to touch a file
    re-stat at destination      the move is confirmed present before it counts
    the bin, never unlink       send2trash, or `<workspace>/.trash/<pass>/`
    sensitive stays             a sensitive file is never a trash candidate
    no nesting, no clobber      a collision becomes `name (2).ext` or it fails

plan.json:
    {"moves": [{"src", "dst", "desc", "reason", "tags", "ids", "date_doc"}],
     "trash": [{"path", "reason"}],
     "tags":  [{"path", "tags", "desc", "reason"}],
     "keep":  [{"path", "desc", "reason"}]}

Usage: apply.py plan.json [--execute]
"""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger import (Ledger, file_md5, is_inside, nfc, pass_id,  # noqa: E402
                    require_config, self_ingestion_guard)
from route import sensitive_hit  # noqa: E402

MINIMAL = os.environ.get("WIKIDOC_MINIMAL") == "1"


def own_text(path):
    """Re-extract the text of a file, for the guards to judge on their own."""
    try:
        import prepare
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return prepare.pdf_text(path)
        if ext in prepare.ZIP_XML:
            return prepare.zip_text(path, prepare.ZIP_XML[ext])
        if ext in prepare.TEXT_EXT or prepare.looks_textual(path):
            return prepare.read_text_file(path)
    except Exception:
        pass
    return ""


def resolve(p):
    """Find the real entry on disk — macOS stores NFD, everyone types NFC."""
    if os.path.exists(p):
        return p
    d, want = os.path.dirname(p), nfc(os.path.basename(p))
    if os.path.isdir(d):
        for e in os.listdir(d):
            if nfc(e) == want:
                return os.path.join(d, e)
    return None


def free_destination(src, dst):
    """The path a move may land on, or a reason it may not.

    A `dst` ending in a separator is a folder to file into — whether or not it
    exists yet. Anything else is the full path the file should end up at, which
    is how a rename is expressed.
    """
    if dst.endswith(os.sep) or os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    real_src = os.path.realpath(src)
    real_parent = os.path.realpath(os.path.dirname(dst) or ".")
    if real_parent == real_src or real_parent.startswith(real_src + os.sep):
        return None, "destination sits inside the source"
    if os.path.exists(dst):
        try:
            if os.stat(dst).st_ino == os.stat(src).st_ino:
                return dst, None       # already there
        except OSError:
            pass
        stem, ext = os.path.splitext(os.path.basename(dst))
        dst = os.path.join(os.path.dirname(dst), f"{stem} (2){ext}")
        if os.path.exists(dst):
            return None, "a file is already there, and so is its (2)"
    return dst, None


def to_bin(path, cfg, pass_name, execute):
    """The OS bin when it exists, a dated folder in the workspace otherwise."""
    if not execute:
        return "would go to the bin"
    if not MINIMAL:
        try:
            from send2trash import send2trash
            send2trash(path)
            return "bin"
        except Exception:
            pass
    fallback = os.path.join(cfg["workspace"], ".trash", pass_name)
    os.makedirs(fallback, exist_ok=True)
    dst, why = free_destination(path, os.path.join(fallback, os.path.basename(path)))
    if not dst:
        raise OSError(why)
    shutil.move(path, dst)
    return dst


def enrich(path, desc, tags, meta, cfg):
    if MINIMAL:
        return
    try:
        import enrich_macos
        enrich_macos.enrich(path, desc=desc, tags=tags, meta=meta,
                            tag_colors=cfg.get("tags") or {})
    except Exception:
        pass                     # best effort: a pass is correct without it


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    execute = "--execute" in sys.argv
    cfg = require_config()
    led = Ledger(root=cfg["root"])
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    pass_name = plan.get("pass") or pass_id(led)
    guards = self_ingestion_guard(cfg)

    done, failed, records = [], [], []

    def guarded(path):
        if any(is_inside(path, g) for g in guards):
            return "inside the workspace — the tool does not ingest itself"
        return None

    # -- moves and renames ---------------------------------------------------
    for m in plan.get("moves", []):
        src = resolve(m["src"])
        if not src:
            failed.append((m["src"], "not found")); continue
        bad = guarded(src)
        if bad:
            failed.append((m["src"], bad)); continue
        dst, why = free_destination(src, os.path.expanduser(m["dst"]))
        if not dst:
            failed.append((m["src"], why)); continue
        size = os.stat(src).st_size
        md5 = file_md5(src, size)
        if not execute:
            done.append(("move", m["src"], dst)); continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        st = os.stat(dst) if os.path.exists(dst) else None      # re-stat: proof
        if not st or os.path.exists(src):
            failed.append((m["src"], "move not confirmed at destination")); continue
        done.append(("move", m["src"], dst))
        enrich(dst, m.get("desc"), m.get("tags"), m, cfg)
        records.append(led.record(dst, pass_id=pass_name, level=m.get("lane", "route"),
                                  decision="move", reason=m.get("reason", ""),
                                  size=st.st_size, mtime=int(st.st_mtime), md5=md5,
                                  desc=m.get("desc"), ids=m.get("ids"),
                                  tags=m.get("tags")))

    # -- trash ---------------------------------------------------------------
    # Nothing is binned until the config says what is protected. A fresh install
    # has no `sensitive:` block, so on day one this script only ever files and
    # tags — you cannot lose a passport to a setting you have not written yet.
    guards_declared = bool(cfg.get("sensitive"))
    for t in plan.get("trash", []):
        p = resolve(t["path"])
        if not p:
            failed.append((t["path"], "not found")); continue
        if not guards_declared:
            failed.append((t["path"], "config declares no `sensitive:` — "
                                      "nothing is binned until it does")); continue
        bad = guarded(p)
        if bad:
            failed.append((t["path"], bad)); continue
        # The guard reads the file itself. Taking the plan's word for the
        # contents would make the protection only as strong as the plan: a trash
        # line written without a `text` field would silently disable every
        # content-based test and leave a passport protected by nothing but its
        # folder.
        probe = {"path": p, "_rel": os.path.relpath(p, cfg["root"]),
                 "text": own_text(p) or t.get("text", ""),
                 "ext": os.path.splitext(p)[1].lower(),
                 "ids": t.get("ids") or {}}
        hit = sensitive_hit(probe, cfg)
        if hit:
            failed.append((t["path"], f"sensitive ({hit}) — kept")); continue
        st = os.stat(p)
        md5 = file_md5(p, st.st_size)
        try:
            where = to_bin(p, cfg, pass_name, execute)
        except OSError as err:
            failed.append((t["path"], str(err))); continue
        done.append(("trash", t["path"], where))
        if execute:
            records.append(led.record(p, pass_id=pass_name, level=t.get("lane", "propose"),
                                      decision="trash", reason=t.get("reason", ""),
                                      size=st.st_size, mtime=int(st.st_mtime), md5=md5,
                                      desc=t.get("desc")))

    # -- tags and descriptions ----------------------------------------------
    for tg in plan.get("tags", []):
        p = resolve(tg["path"])
        if not p:
            failed.append((tg["path"], "not found")); continue
        if guarded(p):
            failed.append((tg["path"], guarded(p))); continue
        done.append(("tag", tg["path"], ",".join(tg.get("tags") or [])))
        if not execute:
            continue
        enrich(p, tg.get("desc"), tg.get("tags"), tg, cfg)
        st = os.stat(p)
        records.append(led.record(p, pass_id=pass_name, level=tg.get("lane", "route"),
                                  decision="tag", reason=tg.get("reason", ""),
                                  size=st.st_size, mtime=int(st.st_mtime),
                                  desc=tg.get("desc"), ids=tg.get("ids"),
                                  tags=tg.get("tags")))

    # -- deliberate non-actions ---------------------------------------------
    for k in plan.get("keep", []):
        p = resolve(k["path"])
        if not p:
            failed.append((k["path"], "not found")); continue
        done.append(("keep", k["path"], k.get("reason", "")))
        if not execute:
            continue
        enrich(p, k.get("desc"), k.get("tags"), k, cfg)
        st = os.stat(p)
        records.append(led.record(p, pass_id=pass_name, level=k.get("lane", "residual"),
                                  decision="none", reason=k.get("reason", ""),
                                  size=st.st_size, mtime=int(st.st_mtime),
                                  desc=k.get("desc"), ids=k.get("ids"),
                                  tags=k.get("tags")))

    written = led.append_many(records) if execute else 0
    kinds = {}
    for kind, _, _ in done:
        kinds[kind] = kinds.get(kind, 0) + 1
    print(json.dumps({"mode": "execute" if execute else "dry-run", "pass": pass_name,
                      "applied": kinds, "ledger_lines": written,
                      "failed": len(failed)}, ensure_ascii=False, indent=1))
    for kind, a, b in done:
        print(f"  {kind:5} {nfc(a)}" + (f"  ->  {nfc(str(b))}" if b else ""))
    for a, why in failed:
        print(f"  FAIL  {nfc(a)}  <- {why}")
    if not execute:
        print("\nnothing was touched. re-run with --execute to apply.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
