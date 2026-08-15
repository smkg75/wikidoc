#!/usr/bin/env python3
"""All of the macOS in this project, in one file, and always best-effort.

On macOS a document can carry what the agent understood of it: a Finder comment
that Spotlight indexes, Finder tags with their colours, and a `com.wikidoc.*`
namespace readable with `xattr -l`. `mdfind 'kMDItemFinderComment == "*rent*"cd'`
then answers from the files themselves.

Everywhere else — and whenever any of this fails — the import raises nothing and
the pass stays correct: the ledger already holds the same facts. This file is
enrichment, never the record.

Usage: enrich_macos.py <file> [--desc TEXT] [--tags a,b] [--date 2024-03-01]
"""
import ctypes
import json
import os
import plistlib
import subprocess
import sys
import unicodedata

DARWIN = sys.platform == "darwin"
COMMENT = b"com.apple.metadata:kMDItemFinderComment"
TAGS = "com.apple.metadata:_kMDItemUserTags"
NS = "com.wikidoc."
META_KEYS = ("desc", "date_doc", "ids", "lu", "md5")

_libc = None
if DARWIN:
    try:
        _libc = ctypes.CDLL("/usr/lib/libSystem.dylib", use_errno=True)
    except OSError:
        _libc = None


def available():
    return bool(_libc)


def nfc(s):
    return unicodedata.normalize("NFC", str(s))


# ------------------------------------------------------------------ xattr ---
def _get(path, attr):
    if not _libc:
        return None
    b = path.encode()
    n = _libc.getxattr(b, attr, None, 0, 0, 0)
    if n < 0:
        return None
    buf = ctypes.create_string_buffer(n)
    n = _libc.getxattr(b, attr, buf, n, 0, 0)
    return buf.raw[:n] if n >= 0 else None


def _set(path, attr, raw):
    if not _libc:
        return False
    return _libc.setxattr(path.encode(), attr, raw, len(raw), 0, 0) == 0


# --------------------------------------------------------------- comment ---
GENERIC_PREFIXES = ("image ", "photo ", "video ", "vidéo ", "fichier ", "document ",
                    "capture ", "screenshot ")


def is_generic(text):
    """A summary that describes the format instead of the document."""
    t = nfc(text).strip().casefold()
    return len(t) < 25 or (t.startswith(GENERIC_PREFIXES) and len(t) < 60)


def set_comment(path, text):
    """Post the summary — and leave any existing comment alone."""
    if not (_libc and text) or is_generic(text):
        return False
    if _get(path, COMMENT) is not None:
        return False
    raw = plistlib.dumps(nfc(text).strip(), fmt=plistlib.FMT_BINARY)
    return _set(path, COMMENT, raw)


# ------------------------------------------------------------------ tags ---
def read_tags(path):
    r = subprocess.run(["xattr", "-px", TAGS, path], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    try:
        return plistlib.loads(bytes.fromhex("".join(r.stdout.split())))
    except Exception:
        return []


def add_tags(path, tags, colors=None):
    """Merge into what is already there. Colours come from config `tags:`."""
    if not (_libc and tags):
        return 0
    colors = colors or {}
    current = read_tags(path)
    have = {nfc(t.split("\n")[0]).casefold() for t in current}
    added = []
    for t in tags:
        name = nfc(str(t).split("\n")[0])
        if name.casefold() in have:
            continue
        color = colors.get(name)
        added.append(f"{name}\n{color}" if color else name)
        have.add(name.casefold())
    if not added:
        return 0
    hexdata = plistlib.dumps(current + added, fmt=plistlib.FMT_BINARY).hex()
    r = subprocess.run(["xattr", "-wx", TAGS, hexdata, path], capture_output=True)
    return len(added) if r.returncode == 0 else 0


# ------------------------------------------------------------------ meta ---
def set_meta(path, meta):
    n = 0
    for key in META_KEYS:
        val = (meta or {}).get(key)
        if val in (None, "", [], {}):
            continue
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False)
        raw = nfc(val).strip().encode()
        if _set(path, (NS + key.replace("_", "-")).encode(), raw):
            n += 1
    return n


def reindex(paths):
    if not (_libc and paths):
        return
    paths = [p for p in paths if p]
    for i in range(0, len(paths), 100):
        subprocess.run(["mdimport"] + paths[i:i + 100], capture_output=True)


# ------------------------------------------------------------------- api ---
def enrich(path, desc=None, tags=None, meta=None, tag_colors=None):
    """One call per file. Returns what landed; raises nothing."""
    if tag_colors:      # warn, never fail: a tag outside the config taxonomy
        for t in tags or []:
            name = nfc(str(t).split("\n")[0])
            if name not in tag_colors:
                print(f"warning: tag {name!r} is not in the config tag taxonomy",
                      file=sys.stderr)
    if not (_libc and os.path.exists(path)):
        return {}
    out = {}
    try:
        if set_comment(path, desc):
            out["comment"] = True
        n = add_tags(path, tags or [], tag_colors)
        if n:
            out["tags"] = n
        n = set_meta(path, {**(meta or {}), "desc": desc})
        if n:
            out["meta"] = n
        if out:
            reindex([path])
    except Exception:
        pass
    return out


def _cli():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    args = {sys.argv[i]: sys.argv[i + 1] for i in range(2, len(sys.argv) - 1, 2)}
    print(json.dumps({
        "darwin": DARWIN, "usable": available(),
        "result": enrich(path, desc=args.get("--desc"),
                         tags=[t for t in (args.get("--tags") or "").split(",") if t],
                         meta={"date_doc": args.get("--date")}),
    }, ensure_ascii=False))


if __name__ == "__main__":
    _cli()
