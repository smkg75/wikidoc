#!/usr/bin/env python3
"""Read bytes, return evidence — the one library that opens documents.

Three consumers need to read file content: collect.py (mass extraction),
apply.py (the sensitive probe re-reads text AND ids from disk, trusting
nothing in the working file), route.py --full-audit (confronting a text rule
with the whole disk). v1 duplicated this code and ended up with two notions
of text equality, the weaker one guarding the sensitive files.

Library only: imported, never executed. No CLI, no main().

WIKIDOC_MINIMAL=1 declares the pdf toolchain absent: pdf_text/pdf_render
return None — never "" — so callers mark needs_vision instead of mistaking
"could not read" for "read and empty".
"""
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import flatten, norm  # noqa: E402

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".html", ".htm",
            ".js", ".py", ".sh", ".yml", ".yaml", ".webloc", ".plist", ".css",
            ".less", ".scss", ".ts", ".tsx", ".jsx", ".sql", ".toml", ".ini",
            ".conf", ".svg", ".vcf", ".ics", ".eml"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".gif", ".webp", ".tif",
           ".tiff", ".bmp"}
ZIP_XML = {".docx": r"word/document\.xml", ".odt": r"content\.xml",
           ".pptx": r"ppt/slides/slide\d+\.xml", ".xlsx": r"xl/sharedStrings\.xml"}

BUILTIN_IDS = {
    # A French IBAN printed in groups of four ends on a 3-char tail
    # ("… 8130 050"): a groups-of-exactly-four pattern silently truncates it
    # and the mod-97 check then rejects a perfectly valid IBAN. Same shape as
    # config.example.yaml's pattern; the checksum does the real filtering.
    "iban": r"\b[A-Z]{2}\d{2}[ ]?[\dA-Z][\dA-Z ]{10,30}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b",
}
# What a name means when the config declares no `validate:` of its own.
DEFAULT_VALIDATE = {"siren": "luhn", "siret": "luhn", "iban": "iban"}

# Full date forms first, MM/YYYY last: at "18/12/1993" the engine consumes the
# whole date before the MM/YYYY branch can harvest the "12/1993" inside it.
RE_DATES = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b|"
    r"\b\d{1,2}(?:er|st|nd|rd|th)?\s+(?:jan(?:vier|uary)?|f[eé]v(?:rier)?|feb(?:ruary)?|"
    r"mar(?:s|ch)?|avr(?:il)?|apr(?:il)?|mai|may|juin|june?|juil(?:let)?|july?|"
    r"ao[uû]t|aug(?:ust)?|sep(?:t(?:embre|ember)?)?|oct(?:obre|ober)?|"
    r"nov(?:embre|ember)?|d[eé]c(?:embre|ember)?)\s+\d{4}\b|"
    r"\b(?:0[1-9]|1[0-2])/\d{4}\b", re.I)
RE_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _minimal():
    return os.environ.get("WIKIDOC_MINIMAL") == "1"


# ------------------------------------------------------------------ text ----
def clean(s):
    # Every kind of whitespace but a newline collapses to one space. PDF text
    # layers are full of thin and non-breaking spaces, and a SIREN printed as
    # "123 456 782" in justified text has to read as one number. No cap here:
    # truncation is collect.py's call, and it records `truncated` when it cuts.
    return re.sub(r"[^\S\n]+", " ", re.sub(r"\n{2,}", "\n", s)).strip()


VOWELS = "aeiouyàâäéèêëïîôöùûüœæ"


def looks_like_prose(text):
    """Is this language, or is it the extractor's debris?

    A PDF with a broken Type 3 font hands back glyph indices — `/32 /33 /29` —
    and a mis-encoded one hands back `ÅÁgkIYIr<[GgIkZ<h`. Both are long enough
    to pass any length check, so the document is filed as "has text" and its
    real content is never read by anyone. That is how a lease gets classified
    on nothing at all.

    Counting letters does not separate them: mojibake is mostly letters.
    Counting plausible *words* does — a word has a vowel and does not stack
    four consonants in a row.
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
    """Text of the XML members matching `pattern` — covers docx/odt/pptx/xlsx
    via the ZIP_XML table."""
    try:
        with zipfile.ZipFile(path) as z:
            names = sorted(n for n in z.namelist() if re.fullmatch(pattern, n))[:cap]
            return clean(" ".join(
                re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "replace"))
                for n in names))
    except Exception:
        return ""


def rtf_text(path):
    try:
        from striprtf.striprtf import rtf_to_text
        with open(path, encoding="utf-8", errors="replace") as f:
            return clean(rtf_to_text(f.read(200000)))
    except Exception:
        return ""


# ------------------------------------------------------------------- pdf ----
def pdf_text(path):
    """Page-1 text. None = could not read (minimal mode, pypdf missing, parser
    blew up) — distinct from "" (parsed fine, no text layer). Callers mark
    needs_vision on None instead of filing on an emptiness nobody verified."""
    if _minimal():
        return None
    try:
        import logging

        from pypdf import PdfReader
        logging.getLogger("pypdf").setLevel(logging.ERROR)   # malformed xrefs are
        r = PdfReader(path)                                  # noise, not news
        if not r.pages:
            return ""
        return clean(r.pages[0].extract_text() or "")
    except Exception:
        return None


def pdf_pages(path):
    """Page count, or None when it cannot be known."""
    if _minimal():
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(path).pages)
    except Exception:
        return None


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


def pdf_render(path, out_png, page=1):
    """Render one page (1-based) to PNG. True on success, False on failure,
    None when rendering is unavailable (minimal mode, pypdfium2 missing)."""
    if _minimal():
        return None
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None
    try:
        doc = pdfium.PdfDocument(path)
        if not 1 <= page <= len(doc):
            return False
        bmp = doc[page - 1].render(scale=100 / 72, rev_byteorder=True)
        if bmp.n_channels not in (3, 4):
            return False
        _write_png(out_png, bmp.buffer, bmp.width, bmp.height,
                   bmp.n_channels, bmp.stride)
        return os.path.exists(out_png)
    except Exception:
        return False


def image_render(path, out_png):
    """HEIC and friends to PNG via `sips`. Best effort, macOS only — anything
    but a confirmed output file is False. -Z bounds the long side so a phone
    photo does not become a 30 MB render."""
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["sips", "-s", "format", "png", "-Z", "1600",
                            path, "--out", out_png],
                           capture_output=True, timeout=30)
        return r.returncode == 0 and os.path.exists(out_png)
    except Exception:
        return False


# ------------------------------------------------------------ identifiers ----
def normalise_id(value):
    """Strip the separators a number is printed with — and nothing else.

    A SIREN reads `123 456 782` or `123.456.782` and has to become one token.
    An address does not: stripping dots from `billing@acme-example.com` would
    produce `billing@acme-examplecom`, an identifier that matches nothing and
    misleads whoever reads it. Only all-digit values are squeezed.
    """
    squeezed = re.sub(r"[ .]", "", value)
    return squeezed if squeezed.isdigit() else re.sub(r"\s+", "", value)


def luhn_ok(value):
    if not value.isdigit() or len(value) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(value)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def iban_ok(value):
    v = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", v):
        return False
    # mod-97: move the first four chars to the end, letters become 10..35
    digits = "".join(str(int(c, 36)) for c in v[4:] + v[:4])
    return int(digits) % 97 == 1


_CHECKS = {"luhn": luhn_ok, "iban": iban_ok}


def extract_ids(text, cfg):
    """Identifiers found AND validated in `text`. Any 9 digits pass a SIREN
    regex — a phone number, half an amount — so a pattern's checksum decides:
    a candidate failing its check is NOT an identifier. Config entries are
    `{pattern:, validate: luhn|iban|none}` (a bare string gets the default
    check for its name). Longest pattern runs first and its validated matches
    are blanked, so a SIRET is not also harvested as a SIREN."""
    specs = {**BUILTIN_IDS, **(cfg.get("identifiers") or {})}
    patterns = []
    for name, spec in specs.items():
        if isinstance(spec, dict):
            pattern = spec.get("pattern", "")
            check = spec.get("validate", DEFAULT_VALIDATE.get(name, "none"))
        else:
            pattern, check = spec, DEFAULT_VALIDATE.get(name, "none")
        if pattern:
            patterns.append((name, re.compile(pattern), _CHECKS.get(check)))
    patterns.sort(key=lambda t: -len(t[1].pattern))

    out, rest = {}, flatten(text or "")
    for name, rx, check in patterns:
        found, spans = [], []
        for m in rx.finditer(rest):
            value = normalise_id(m.group())
            if check and not check(value):
                continue                  # matched the shape, failed the checksum
            if value not in found:
                found.append(value)
            spans.append(m.span())
        if found:
            out[name] = found[:5]
            buf = list(rest)              # blank only VALIDATED spans: an invalid
            for a, b in spans:            # candidate stays visible to the next,
                buf[a:b] = " " * (b - a)  # shorter pattern
            rest = "".join(buf)
    return out


# ------------------------------------------------------------------ dates ----
def extract_dates(text):
    """Recognised date strings, deduplicated, first five. Forms: ISO,
    numeric d/m/y variants, spelled month (fr/en), MM/YYYY."""
    return list(dict.fromkeys(
        m.group() for m in RE_DATES.finditer(flatten(text or ""))))[:5]


def doc_year(dates):
    """Only ever from a recognised date. Scanning whole text for a 19xx/20xx
    number harvests street numbers ("2045 Main Street"), card expiries and CSV
    amounts, and a destination built on `{doc_year}` then creates a folder
    called 2086. Bounded 1900..next year; None when nothing qualifies."""
    years = [int(m.group()) for m in RE_YEAR.finditer(" ".join(dates or []))]
    years = [y for y in years if 1900 <= y <= datetime.now().year + 1]
    return max(years) if years else None


# ------------------------------------------------------- condition engine ----
# One evaluator for every config condition — rules, entities, and above all
# `sensitive:`. It lives in the library because two STEPS need it (route.py
# for triage, apply.py for the trash probe) and steps may not import steps;
# a second, smaller copy in apply.py is how v1's weakest matcher ended up
# guarding the most sensitive files.

def _squeeze(v):
    return re.sub(r"[ .]", "", str(v))


def _id_values(e):
    return {_squeeze(v) for vals in (e.get("ids") or {}).values() for v in vals}


def _text_of(e):
    """The text as one normalised line — layout and accents are not evidence.

    Cached per entry, since every condition of every rule asks for it.
    """
    if "_flat" not in e:
        e["_flat"] = norm(e.get("text") or "")
    return e["_flat"]


_ext_in = lambda e, v: e.get("ext") in [str(x).lower() for x in v]  # noqa: E731

COND = {
    "text_contains_any": lambda e, v: any(norm(str(x)) in _text_of(e) for x in v),
    "text_contains_all": lambda e, v: all(norm(str(x)) in _text_of(e) for x in v),
    "text_matches": lambda e, v: bool(re.search(v, _text_of(e), re.I)),
    "ids_any": lambda e, v: bool(_id_values(e) & {_squeeze(x) for x in v}),
    "id_kind_present": lambda e, v: any(k in (e.get("ids") or {}) for k in v),
    "has_text": lambda e, v: bool(e.get("text")) is bool(v),
    "doc_year_in": lambda e, v: e.get("doc_year") in [int(x) for x in v],
    "ext": _ext_in,
    "ext_in": _ext_in,
    "name_matches": lambda e, v: bool(re.search(v, norm(os.path.basename(e["path"])), re.I)),
    "path_under": lambda e, v: any(fnmatch(norm("/" + e["_rel"].replace(os.sep, "/")),
                                           norm((p if p.startswith(("/", "*")) else "*/" + p)
                                                .rstrip("/") + "*"))
                                   for p in v),
    "size_gt": lambda e, v: e.get("size", 0) > int(v),
    "size_lt": lambda e, v: e.get("size", 0) < int(v),
}


def matches(cond, e):
    if not isinstance(cond, dict) or not cond:
        return False           # an empty condition proves nothing about anything
    for key, val in cond.items():
        if key == "all":
            if not all(matches(c, e) for c in val):
                return False
        elif key == "any":
            if not any(matches(c, e) for c in val):
                return False
        elif key == "not":
            if matches(val, e):
                return False
        elif key in COND:
            if not COND[key](e, val):
                return False
        else:
            return False
    return True


def sensitive_hit(e, cfg):
    """Which sensitive tripwire fired, named precisely enough to act on.

    Full grammar everywhere: the flat keys, and `rules:` entries with
    all/any/not. The entry needs `path`, `_rel`, `ext`, `text`, `ids`."""
    s = cfg.get("sensitive") or {}
    for key in ("text_contains_any", "name_matches", "path_under", "ext",
                "ext_in", "id_kind_present"):
        v = s.get(key)
        if v is None or not COND[key](e, v):
            continue
        if isinstance(v, (list, tuple)):
            for x in v:
                if COND[key](e, [x]):
                    return f"{key}: {x}"
        return f"{key}: {v}"
    for rule in s.get("rules", []):
        if matches(rule, e):
            return rule.get("id", "sensitive-rule")
    return None
