#!/usr/bin/env python3
"""Step ⑤ — the only script that touches disk. Dry run unless you say otherwise.

Reads the decision columns of `bench/routing.json` (decision, dst, desc, tags,
date_doc, reviewed) and performs the gestures. Because this is the one script
that changes anything, it is the one with no settings: every safety below runs
on every invocation and nothing in `config.yaml` can turn one off.

    dry run by default          `--execute` is the only way to touch a file
    re-stat at destination      a move is confirmed present before it counts
    the bin, never unlink       send2trash, or `<workspace>/.trash/<pass>/`
    sensitive stays             the probe re-reads the file itself
    no nesting, no clobber      a collision becomes `name (2).ext` or it fails
    incremental memory          each action writes its memory line immediately
                                and stamps `result` — a crash leaves both at
                                the interruption point, `--resume` continues

Decisions: move | rename | trash | tag | none. `unanswered` and empty are not
actions — route.py --learn records them. Anything else fails the entry before
any gesture, as does a path outside root.

Usage: apply.py [--execute] [--resume]
"""
import json
import os
import re
import shutil
import sys
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402
from memory import (Memory, file_md5, is_inside, nfc, norm, pass_id,  # noqa: E402
                    require_config, self_ingestion_guard)

MINIMAL = os.environ.get("WIKIDOC_MINIMAL") == "1"
DECISIONS = ("move", "rename", "trash", "tag", "none")
RESULT = {"move": "moved", "rename": "renamed", "trash": "trashed",
          "tag": "tagged", "none": "kept"}


# ---------------------------------------------------------------- disk ------
def resolve(p):
    """Find the real entry on disk — macOS stores NFD, everyone types NFC."""
    if os.path.exists(p):
        return p
    d, want = os.path.dirname(p), nfc(os.path.basename(p))
    if os.path.isdir(d):
        for entry in os.listdir(d):
            if nfc(entry) == want:
                return os.path.join(d, entry)
    return None


def stat_or_none(p):
    try:
        return os.stat(p)
    except OSError:
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
        st_dst, st_src = stat_or_none(dst), stat_or_none(src)
        if st_dst and st_src and st_dst.st_ino == st_src.st_ino:
            return dst, None       # already there
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


def save_routing(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------- probe ------
def own_text(path, ext):
    """Re-read the file's text; the probe trusts nothing already written down.

    A failure to read is None, never "": the caller must be able to tell
    "read and empty" from "could not read" — the second one refuses a trash.
    """
    try:
        if ext == ".pdf":
            return extract.pdf_text(path)
        if ext in extract.ZIP_XML:
            return extract.zip_text(path, extract.ZIP_XML[ext])
        if ext == ".rtf":
            return extract.rtf_text(path)
        return extract.read_text_file(path)
    except Exception:
        return None


SENSITIVE_KEYS = ("text_contains_any", "name_matches", "path_under", "ext_in",
                  "id_kind_present")


def _sens_key(key, val, probe):
    if key == "text_contains_any":
        return any(norm(str(x)) in probe["text"] for x in val)
    if key == "name_matches":
        return bool(re.search(val, probe["name"], re.I))
    if key == "path_under":
        rel = "/" + probe["rel"].replace(os.sep, "/")
        return any(fnmatch(rel, (p if p.startswith(("/", "*")) else "*/" + p)
                   .rstrip("/") + "*") for p in val)
    if key == "ext_in":
        return probe["ext"] in [str(x).lower() for x in val]
    if key == "id_kind_present":
        return any(k in probe["ids"] for k in val)
    return False


def sensitive_hit(probe, cfg):
    """Which sensitive test the file trips, judged only on re-read evidence.

    Taking routing.json's word for the contents would make the protection only
    as strong as the working file: a trash entry written without a `text`
    column would silently disable every content-based test.
    """
    s = cfg.get("sensitive") or {}
    for key in SENSITIVE_KEYS:
        if key in s and _sens_key(key, s[key], probe):
            return key
    for rule in s.get("rules", []):
        keys = [k for k in rule if k in SENSITIVE_KEYS]
        if keys and all(_sens_key(k, rule[k], probe) for k in keys):
            return rule.get("id", "sensitive-rule")
    return None


# ---------------------------------------------------------------- main ------
def main():
    execute = "--execute" in sys.argv[1:]
    resume = "--resume" in sys.argv[1:]
    cfg = require_config()
    root = cfg["root"]
    routing_path = os.path.join(cfg["workspace"], "bench", "routing.json")
    if not os.path.exists(routing_path):
        sys.exit(f"no {routing_path} — run collect, route and decide first")
    with open(routing_path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data if isinstance(data, list) else data.get("entries", [])
    mem = Memory(root=root)
    pass_name = (data.get("pass") if isinstance(data, dict) else None) or pass_id(mem)
    guards = self_ingestion_guard(cfg)

    done, failed = [], []
    skipped = {"done": 0, "undecided": 0}
    memory_lines = 0

    def act(entry, rec, result, final, kind, shown):
        """One successful gesture: memory line out NOW, result stamped NOW."""
        nonlocal memory_lines
        mem.append(rec)
        memory_lines += 1
        entry["result"] = result
        entry["final"] = final
        save_routing(routing_path, data)
        done.append((kind, entry["path"], shown))

    for e in entries:
        raw = e.get("path") or ""
        if resume and e.get("result"):
            skipped["done"] += 1
            continue
        decision = e.get("decision")
        if not decision or decision == "unanswered":
            skipped["undecided"] += 1
            continue

        # -- validation before any gesture -----------------------------------
        if decision not in DECISIONS:
            failed.append((raw, f"unknown decision {decision!r}")); continue
        if not raw or not is_inside(raw, root):
            failed.append((raw, "path outside root")); continue
        if any(is_inside(raw, g) for g in guards):
            failed.append((raw, "inside the workspace — the tool does not "
                                "ingest itself")); continue
        src = resolve(raw)
        if not src:
            failed.append((raw, "dangling symlink" if os.path.lexists(raw)
                           else "not found")); continue
        if not os.path.exists(src):
            failed.append((raw, "dangling symlink")); continue
        st = stat_or_none(src)
        if st is None:
            failed.append((raw, "stat failed")); continue

        ext = os.path.splitext(src)[1].lower()
        rel_src = nfc(os.path.relpath(src, root))
        base = {"pass_id": pass_name, "triage": e.get("triage") or e.get("level"),
                "decision": decision, "reason": e.get("why") or "",
                "size": st.st_size, "mtime": int(st.st_mtime),
                "desc": e.get("desc"), "tags": e.get("tags"),
                "date_doc": e.get("date_doc"), "provenance": "pass"}

        if decision in ("move", "rename"):
            dst_raw = e.get("dst")
            if not dst_raw:
                failed.append((raw, "no destination")); continue
            dst_abs = os.path.expanduser(dst_raw)
            if not os.path.isabs(dst_abs):
                dst_abs = os.path.join(root, dst_abs)
            if not is_inside(dst_abs, root):
                failed.append((raw, "destination outside root")); continue
            dst, why = free_destination(src, dst_abs)
            if not dst:
                failed.append((raw, why)); continue
            final = nfc(os.path.relpath(dst, root))
            try:                                # desc judged before the gesture
                rec = mem.record(**{**base, "path": final,
                                    "md5": file_md5(src, st.st_size),
                                    "ids": e.get("ids")})
            except ValueError as err:
                failed.append((raw, str(err))); continue
            if not execute:
                done.append((decision, raw, final)); memory_lines += 1; continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            st2 = stat_or_none(dst)             # re-stat: proof, not intent
            if not st2 or os.path.lexists(src):
                failed.append((raw, "move not confirmed at destination")); continue
            rec["size"], rec["mtime"] = st2.st_size, int(st2.st_mtime)
            enrich(dst, e.get("desc"), e.get("tags"), e, cfg)
            act(e, rec, RESULT[decision], final, decision, final)

        elif decision == "trash":
            # Nothing is binned until the config says what is protected: a
            # fresh install cannot lose a passport to a setting not yet written.
            if not cfg.get("sensitive"):
                failed.append((raw, "config declares no `sensitive:` — "
                                    "nothing is binned until it does")); continue
            text = own_text(src, ext)
            if not text and st.st_size > 1024 and e.get("reviewed") != "vision":
                failed.append((raw, "no text re-extracted from a non-trivial "
                                    "file — kept until reviewed with vision")); continue
            try:
                ids = extract.extract_ids(text or "", cfg) or {}
            except Exception:
                failed.append((raw, "id re-extraction failed — kept")); continue
            hit = sensitive_hit({"text": norm(text or ""), "rel": rel_src,
                                 "name": os.path.basename(src), "ext": ext,
                                 "ids": ids}, cfg)
            if hit:
                failed.append((raw, f"sensitive ({hit}) — kept")); continue
            try:
                rec = mem.record(**{**base, "path": rel_src,
                                    "md5": file_md5(src, st.st_size),
                                    "ids": ids or None})
            except ValueError as err:
                failed.append((raw, str(err))); continue
            if not execute:
                done.append(("trash", raw, "would go to the bin"))
                memory_lines += 1; continue
            try:
                where = to_bin(src, cfg, pass_name, execute)
            except OSError as err:
                failed.append((raw, str(err))); continue
            act(e, rec, "trashed", nfc(str(where)), "trash", where)

        else:                                   # tag, none
            try:
                rec = mem.record(**{**base, "path": rel_src, "md5": None,
                                    "ids": e.get("ids")})
            except ValueError as err:
                failed.append((raw, str(err))); continue
            shown = (",".join(e.get("tags") or []) if decision == "tag"
                     else e.get("why") or "")
            if not execute:
                done.append((decision, raw, shown)); memory_lines += 1; continue
            enrich(src, e.get("desc"), e.get("tags"), e, cfg)
            act(e, rec, RESULT[decision], rel_src, decision, shown)

    kinds = {}
    for kind, _, _ in done:
        kinds[kind] = kinds.get(kind, 0) + 1
    print(json.dumps({"mode": "execute" if execute else "dry-run",
                      "pass": pass_name, "applied": kinds,
                      "failed": len(failed), "skipped": skipped,
                      "memory_lines": memory_lines},
                     ensure_ascii=False, indent=1))
    for kind, a, b in done:
        print(f"  {kind:6} {nfc(a)}" + (f"  ->  {nfc(str(b))}" if b else ""))
    for a, why in failed:
        print(f"  FAIL   {nfc(a)}  <- {why}")
    if not execute:
        print("\nnothing was touched. re-run with --execute to apply.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
