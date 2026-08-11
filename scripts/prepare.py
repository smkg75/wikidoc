#!/usr/bin/env python3
"""Pick the backlog and put the evidence on the bench.

Selection walks the root with `scandir` and compares `(path, size, mtime)`
against the ledger — a file whose stat line is already recorded is not hashed at
all. Only what looks new gets a md5, which is what makes a pass over 70 000
files cheap.

Then, per file: text of page 1, a PNG render when a PDF carries no text layer,
identifier regexes, and byte-identical duplicate detection by size collision.

Usage: prepare.py [LIMIT]     (default: batch_size from config, else 300)
"""
import json
import os
import re
import sys
import zipfile
from datetime import datetime
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ledger import (Ledger, file_md5, is_inside, nfc,  # noqa: E402
                    require_config, self_ingestion_guard)

TEXT_CAP = 1500
MINIMAL = os.environ.get("WIKIDOC_MINIMAL") == "1"

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".html", ".htm",
            ".js", ".py", ".sh", ".yml", ".yaml", ".webloc", ".plist", ".css",
            ".less", ".scss", ".ts", ".tsx", ".jsx", ".sql", ".toml", ".ini",
            ".conf", ".svg", ".vcf", ".ics", ".eml"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".gif", ".webp", ".tif",
           ".tiff", ".bmp"}
ZIP_XML = {".docx": r"word/document\.xml", ".odt": r"content\.xml",
           ".pptx": r"ppt/slides/slide\d+\.xml", ".xlsx": r"xl/sharedStrings\.xml"}

DEDUP_MIN_SIZE = 4096      # tiny files collide by accident
DEDUP_MAX_GROUP = 25       # a crowded size bucket says nothing

CHUNK_MAX = 40             # past this an agent skims or times out
CHUNK_IMG_MAX = 24         # reading an image costs ~10x reading supplied text
CHUNK_SPLIT_AT = 20        # below this, keep a folder together

BUILTIN_IDS = {
    "iban": r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b",
}
RE_DATES = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b|"
    r"\b\d{1,2}(?:er|st|nd|rd|th)?\s+(?:jan(?:vier|uary)?|f[eé]v(?:rier)?|feb(?:ruary)?|"
    r"mar(?:s|ch)?|avr(?:il)?|apr(?:il)?|mai|may|juin|june?|juil(?:let)?|july?|"
    r"ao[uû]t|aug(?:ust)?|sep(?:t(?:embre|ember)?)?|oct(?:obre|ober)?|"
    r"nov(?:embre|ember)?|d[eé]c(?:embre|ember)?)\s+\d{4}\b", re.I)
RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")


# ------------------------------------------------------------- selection ----
def excluded(rel, patterns):
    rel = "/" + rel.replace(os.sep, "/")
    return any(fnmatch(rel, p if p.startswith(("/", "*")) else "*/" + p)
               for p in patterns)


def walk_backlog(cfg, led, limit):
    """Files without a matching ledger stat line, in a stable order.

    Sizes of everything scanned come back too: duplicate detection needs the
    whole corpus, not just the batch.
    """
    root, guards = cfg["root"], self_ingestion_guard(cfg)
    excl = cfg["exclude"]
    backlog, sizes, scanned, skipped = [], {}, 0, 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if any(is_inside(dirpath, g) for g in guards):
            dirnames[:] = []
            continue
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = sorted(d for d in dirnames
                             if not excluded(os.path.join(rel_dir, d), excl))
        for fn in sorted(filenames):
            if fn == ".DS_Store":
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root)
            if excluded(rel, excl):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            scanned += 1
            sizes.setdefault(st.st_size, []).append(p)
            if led.seen_stat(p, st.st_size, int(st.st_mtime)):
                skipped += 1
                continue
            if len(backlog) < limit:
                backlog.append((p, st.st_size, int(st.st_mtime)))
    return backlog, sizes, scanned, skipped


def chunk(paths):
    out, cur, cur_parent, cur_img = [], [], None, 0
    for p in paths:
        parent = os.path.dirname(p)
        if cur and (len(cur) >= CHUNK_MAX or cur_img >= CHUNK_IMG_MAX
                    or (parent != cur_parent and len(cur) >= CHUNK_SPLIT_AT)):
            out.append(cur)
            cur, cur_img = [], 0
        cur.append(p)
        cur_img += os.path.splitext(p)[1].lower() in IMG_EXT
        cur_parent = parent
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------ extraction ----
def clean(s):
    # Every kind of whitespace but a newline collapses to one space. PDF text
    # layers are full of thin and non-breaking spaces, and a SIREN printed as
    # "917 963 183" in justified text has to read as one number.
    return re.sub(r"[^\S\n]+", " ", re.sub(r"\n{2,}", "\n", s)).strip()[:TEXT_CAP]


def flatten(s):
    """One line, single spaces — the form every pattern is matched against.

    Extractors hand back layout, not prose: pypdf routinely returns a newline
    between every fragment, so a SIREN printed as `917 963 183` arrives with a
    newline inside it and a two-word phrase never matches. The readable text
    keeps its shape for the agent; matching happens on this.
    """
    return re.sub(r"\s+", " ", s or "").strip()


VOWELS = "aeiouyàâäéèêëïîôöùûüœæ"


def looks_like_prose(text):
    """Is this language, or is it the extractor's debris?

    A PDF with a broken Type 3 font hands back glyph indices — `/32 /33 /29` —
    and a mis-encoded one hands back `ÅÁgkIYIr<[GgIkZ<h`. Both are long enough to
    pass any length check, so the document is filed as "has text" and its real
    content is never read by anyone. That is how a lease gets classified on
    nothing at all.

    Counting letters does not separate them: mojibake is mostly letters. Counting
    plausible *words* does — a word has a vowel and does not stack four
    consonants in a row.
    """
    words = re.findall(r"[^\W\d_]{3,}", text or "", re.UNICODE)
    if len(words) < 4:
        return False
    plausible = 0
    for w in words:
        low = w.casefold()
        if not any(c in VOWELS for c in low):
            continue                                   # no vowel, no word
        if re.search(rf"[^{VOWELS}\W\d_]{{4,}}", low, re.UNICODE):
            continue                                   # four consonants in a row
        if re.search(r"[^\W\d_A-ZÀ-Þ][A-ZÀ-Þ]", w):
            continue        # a capital mid-word: `ÅÁgkIYIr`, not a word
        plausible += 1
    return plausible / len(words) >= 0.6


def looks_textual(path):
    """Sniff instead of shelling out to `file` — same answer, works everywhere."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    if not head or b"\0" in head:
        return False
    printable = sum(1 for b in head if 9 <= b <= 13 or 32 <= b < 127 or b >= 160)
    return printable / len(head) > 0.9


def read_text_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return clean(f.read(8000))
    except OSError:
        return ""


def zip_text(path, pattern, cap=3):
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if re.fullmatch(pattern, n))[:cap]
            return clean(" ".join(
                re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "replace"))
                for n in names))
    except Exception:
        return ""


def pdf_text(path):
    if MINIMAL:
        return ""
    try:
        import logging

        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)   # malformed xrefs are
        r = PdfReader(path)                                  # noise, not news
        if not r.pages:
            return ""
        return clean(r.pages[0].extract_text() or "")
    except Exception:
        return ""


def _write_png(path, buf, w, h, channels, stride):
    """Encode RGB/RGBA bytes as PNG with the standard library alone.

    pypdfium2 hands back a raw bitmap and would otherwise need Pillow just to
    save it — a heavy dependency for twenty lines of zlib and struct.
    """
    import binascii
    import struct
    import zlib
    raw = bytearray()
    for y in range(h):
        row = buf[y * stride:y * stride + w * channels]
        raw += b"\0" + bytes(row)     # filter type 0, one byte per scanline

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8,
                                           6 if channels == 4 else 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


def pdf_render(path, out_png):
    if MINIMAL:
        return False
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(path)
        if len(doc) == 0:
            return False
        bmp = doc[0].render(scale=100 / 72, rev_byteorder=True)
        if bmp.n_channels not in (3, 4):
            return False
        _write_png(out_png, bmp.buffer, bmp.width, bmp.height,
                   bmp.n_channels, bmp.stride)
        return os.path.exists(out_png)
    except Exception:
        return False


def rtf_text(path):
    try:
        from striprtf.striprtf import rtf_to_text
        with open(path, encoding="utf-8", errors="replace") as f:
            return clean(rtf_to_text(f.read(200000)))
    except Exception:
        return ""


def normalise_id(value):
    """Strip the separators a number is printed with — and nothing else.

    A SIREN reads `917 963 183` or `917.963.183` and has to become one token.
    An address does not: stripping dots from `agence@syndic-example.fr` produced
    `agence@syndic-examplefr`, an identifier that matches nothing and misleads whoever
    reads it. Only all-digit values are squeezed.
    """
    squeezed = re.sub(r"[ .]", "", value)
    return squeezed if squeezed.isdigit() else re.sub(r"\s+", "", value)


def extract_ids(text, patterns):
    out, rest = {}, text
    for name, rx in patterns:
        found = list(dict.fromkeys(
            normalise_id(m.group()) for m in rx.finditer(rest)))[:5]
        if found:
            out[name] = found
            rest = rx.sub(" ", rest)   # longest patterns run first, so a SIRET
    return out                          # is not also harvested as a SIREN


def prep_file(path, size, mtime, render_path, id_patterns):
    ext = os.path.splitext(path)[1].lower()
    e = {"path": path, "ext": ext or "?", "size": size, "mtime": mtime,
         "size_kb": round(size / 1024)}
    text = ""
    if ext == ".pdf":
        text = pdf_text(path)
        # length is not enough: a broken font yields plenty of characters and no
        # language at all, and that PDF would then be filed on nothing
        if len(text) < 40 or not looks_like_prose(text):
            garbled = len(text) >= 40
            text = ""
            if pdf_render(path, render_path):
                e["render"] = render_path
                if garbled:
                    e["text_unreadable"] = "extractor returned no language — rendered instead"
            else:
                e["opaque"] = ("pdf-text-layer-garbled" if garbled
                               else "pdf-without-text-layer")
    elif ext in ZIP_XML:
        text = zip_text(path, ZIP_XML[ext])
        if text and not looks_like_prose(text):
            text = ""
            e["opaque"] = "office-text-garbled"
        elif not text:
            e["opaque"] = "office-container-unreadable"
    elif ext == ".rtf":
        text = rtf_text(path) or (read_text_file(path) if looks_textual(path) else "")
    elif ext == ".doc":
        e["opaque"] = "legacy-word-binary"     # no reader: goes to the residual lane
    elif ext in IMG_EXT:
        e["image"] = True                      # the agent opens it
    elif ext in TEXT_EXT:
        text = read_text_file(path)
    elif looks_textual(path):
        text = read_text_file(path)
    else:
        e["opaque"] = "binary"
    if text:
        e["text"] = text
        flat = flatten(text)
        ids = extract_ids(flat, id_patterns)
        if ids:
            e["ids"] = ids
        dates = list(dict.fromkeys(m.group() for m in RE_DATES.finditer(flat)))[:5]
        if dates:
            e["dates"] = dates
            # Only ever from a recognised date. Scanning the whole text for a
            # 19xx/20xx number harvests street numbers ("2031 Store Street"),
            # card expiries and CSV amounts, and a destination built on
            # `{doc_year}` then creates a folder called 2086.
            years = [int(y.group()) for y in RE_YEAR.finditer(" ".join(dates))]
            years = [y for y in years if 1900 <= y <= datetime.now().year + 1]
            if years:
                e["doc_year"] = max(years)
    return e


# ------------------------------------------------------------------ main ----
def main():
    cfg = require_config()
    led = Ledger(root=cfg["root"])
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else int(cfg.get("batch_size", 300))

    ws = cfg["workspace"]
    batch = os.path.join(ws, "batch")
    prep_dir, render_dir, log_dir = (os.path.join(batch, "prep"),
                                     os.path.join(batch, "prep", "renders"),
                                     os.path.join(batch, "logs"))
    for d in (prep_dir, render_dir, log_dir):
        os.makedirs(d, exist_ok=True)

    id_patterns = [(k, re.compile(v)) for k, v in
                   sorted({**BUILTIN_IDS, **cfg.get("identifiers", {})}.items(),
                          key=lambda kv: -len(kv[1]))]

    backlog, sizes, scanned, skipped = walk_backlog(cfg, led, limit)
    stat_of = {p: (s, m) for p, s, m in backlog}
    chunks = chunk([p for p, _, _ in backlog])

    stats = {"scanned": scanned, "already_in_ledger": skipped,
             "batch": len(backlog), "chunks": len(chunks), "text": 0, "renders": 0,
             "images": 0, "opaque": 0, "duplicates": 0, "known_content": 0}
    chunk_files, log = [], []
    idx = 0
    for n, paths in enumerate(chunks, 1):
        entries = []
        for p in paths:
            size, mtime = stat_of[p]
            e = prep_file(p, size, mtime,
                          os.path.join(render_dir, f"{n:03d}-{idx:02d}.png"),
                          id_patterns)
            idx += 1

            # identity is (size, md5): a size collision is the only reason to hash
            group = sizes.get(size, [])
            if size >= DEDUP_MIN_SIZE and 2 <= len(group) <= DEDUP_MAX_GROUP:
                me = file_md5(p, size)
                if me:
                    e["md5"] = me
                    twins = [q for q in group
                             if q != p and file_md5(q, size) == me]
                    if twins:
                        e["duplicate_of"] = twins[:3]
                        stats["duplicates"] += 1
            if "md5" not in e:
                e["md5"] = file_md5(p, size)
            # same content already recorded under another name: a move, not new work
            if e.get("md5") and led.seen_md5(e["md5"]):
                prev = led.seen_md5(e["md5"])
                e["known_as"] = prev.get("path")
                e["known_desc"] = prev.get("desc")
                stats["known_content"] += 1

            if e.get("text"):
                stats["text"] += 1
            elif e.get("render"):
                stats["renders"] += 1
            elif e.get("image"):
                stats["images"] += 1
            else:
                stats["opaque"] += 1
            entries.append(e)
            log.append(f"[{n:03d}] {nfc(p)} ext={e['ext']} "
                       f"text={len(e.get('text', ''))}c render={'y' if e.get('render') else '-'} "
                       f"ids={e.get('ids', {})}"
                       + (f" DUPLICATE_OF={e['duplicate_of']}" if e.get("duplicate_of") else "")
                       + (f" KNOWN_AS={e['known_as']}" if e.get("known_as") else ""))

        cf = os.path.join(batch, f"chunk-{n:03d}.txt")
        with open(cf, "w", encoding="utf-8") as f:
            f.write("\n".join(paths) + "\n")
        pf = os.path.join(prep_dir, f"chunk-{n:03d}.json")
        with open(pf, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=0)
        parents = {os.path.dirname(x) for x in paths}
        common = os.path.commonpath(list(parents)) if len(parents) > 1 else parents.pop()
        chunk_files.append({"dir": common, "file": cf, "prep": pf, "count": len(paths)})

    stats["sum_chunks"] = sum(c["count"] for c in chunk_files)
    summary = {**stats, "chunkFiles": chunk_files}
    with open(os.path.join(log_dir, "prepare.log"), "w", encoding="utf-8") as f:
        # the counts step 1 is judged on go in the log too: stdout scrolls away,
        # and re-running the script to see them again is what the log exists to avoid
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n\n")
        f.write("\n".join(log) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"detail -> {log_dir}/prepare.log", file=sys.stderr)


if __name__ == "__main__":
    main()
