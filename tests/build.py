#!/usr/bin/env python3
"""Build the booby-trapped corpus the verification agents run the pipeline on.

The corpus is generated rather than committed, because half of what it has to
contain cannot survive a git checkout: a file called `CON.pdf` is unwritable on
Windows, `Icon\\r` confuses archive tools, a dangling symlink is garbage-collected
by checkout tools, and two names differing only by case collapse into one on a
case-insensitive volume. Generating them means the traps are real on the machine
under test, and the manifest records which ones the filesystem actually accepted.

Every trap is one that has bitten before, or that will bite on a platform this
tool claims to support. All data is invented; identifiers are fictitious but
pass their validators (Luhn, mod-97) so the pipeline treats them as real.

Usage: build.py <target-dir>     prints the manifest as JSON
See TRAPS.md for what correct handling of each trap looks like.
"""
import json
import os
import shutil
import sys
import unicodedata
import zipfile

# Invented, validator-passing. SIREN/SIRET pass Luhn; IBAN passes mod-97.
SIREN_A = "842931073"           # OHMEA CONSEIL (invented company)
SIRET_B = "53169420600024"      # AGENCE LUNARIA (invented landlord)
IBAN = "FR76 1234 5678 9000 0246 8130 050"

# 5x7 bitmap font, only the glyphs the scan needs. Two hex digits per row.
FONT = {c: bytes.fromhex(h) for c, h in {
    "A": "0E11111F111111", "C": "0E11101010110E", "D": "1E11111111111E",
    "E": "1F10101E10101F", "G": "0E11101711110F", "H": "1111111F111111",
    "L": "1010101010101F", "M": "111B1515111111", "N": "11191513111111",
    "O": "0E11111111110E", "P": "1E11111E101010", "R": "1E11111E141211",
    "T": "1F040404040404", "0": "0E11131519110E", "1": "040C040404040E",
    "2": "0E11010204081F", "6": "0608101E11110E", "7": "1F010204080808",
    "/": "01020204080810",
}.items()}


# ------------------------------------------------------------------- pdf ----
def _pdf(path, lines, ops=None):
    """A minimal, well-formed PDF. No lines and no ops = an empty scan page."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    objs = []
    objs.append(b"<</Type/Catalog/Pages 2 0 R>>")
    objs.append(b"<</Type/Pages/Kids[3 0 R]/Count 1>>")
    objs.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
                b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>")
    if ops:
        body = ops
    elif lines:
        body = b"BT /F1 12 Tf\n" + b"\n".join(
            b"1 0 0 1 40 %d Tm (%s) Tj" % (780 - 18 * i,
                                           l.encode("ascii", "replace")
                                            .replace(b"(", b"[").replace(b")", b"]"))
            for i, l in enumerate(lines)) + b"\nET\n"
    else:
        body = b"0.9 0.9 0.9 rg 40 40 500 760 re f\n"   # a grey rectangle: a scan
    objs.append(b"<</Length %d>>stream\n" % len(body) + body + b"endstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")

    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj" % i + o + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    with open(path, "wb") as f:
        f.write(out)


def _scan_pdf(path, lines):
    """Text drawn as filled rectangles from the bitmap font: readable pixels,
    NO text layer. Extraction returns nothing; only vision can read it."""
    ops = ["0.93 0.93 0.93 rg 30 30 535 782 re f", "0.1 0.1 0.1 rg"]
    y = 760
    for line in lines:
        x = 60
        for ch in line:
            rows = FONT.get(ch, b"\0" * 7)
            for r, row in enumerate(rows):
                for c in range(5):
                    if row >> (4 - c) & 1:
                        ops.append("%d %d 4 4 re" % (x + c * 4, y - r * 4))
            x += 24
        y -= 48
    _pdf(path, None, ops=("\n".join(ops) + "\nf\n").encode())


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build(target):
    target = os.path.abspath(os.path.expanduser(target))
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    files, traps = [], {}

    def add(rel, triage, why, rule=None):
        files.append({"path": rel, "triage": triage, "why": why, "rule": rule})

    # -- ordinary documents, so the rules have something to bite on ----------
    # Rent receipts whose ONLY date is MM/YYYY: doc_year must come from it,
    # or {doc_year} destinations never resolve (pinned v1 bug).
    for i in range(1, 6):
        rel = f"Docs/Loyer/quittance-{i:02d}.pdf"
        _pdf(os.path.join(target, rel),
             ["QUITTANCE DE LOYER", f"Periode : {i:02d}/2025",
              "Montant : 780,00 EUR", "Bailleur : AGENCE LUNARIA"])
        add(rel, "route", "rent-receipt rule; doc_year only from MM/YYYY",
            "rent-receipt")

    # Same receipt, no date anywhere: {doc_year} unresolved -> never routed.
    _pdf(os.path.join(target, "Docs/Loyer/quittance-sans-date.pdf"),
         ["QUITTANCE DE LOYER", "Montant : 780,00 EUR",
          "Bailleur : AGENCE LUNARIA"])
    add("Docs/Loyer/quittance-sans-date.pdf", "propose",
        "destination variable unresolved: doc_year; never undated/")

    for i in range(1, 4):
        rel = f"Docs/Factures/facture-2025-{i:03d}.pdf"
        _pdf(os.path.join(target, rel),
             ["FACTURE", f"Numero : 2025-{i:03d}", f"SIREN {SIREN_A}",
              "OHMEA CONSEIL", "Total TTC : 1 240,00 EUR", "Date : 12/03/2025"])
        add(rel, "route", "invoice rule, entity resolved by Luhn-valid SIREN",
            "invoice")

    # Accented content vs unaccented rule pattern: norm() must bridge them.
    write(os.path.join(target, "Docs/Loyer/quittance-accents.txt"),
          "Quittance de loyer\nPériode : 06/2025\nMontant : 780,00 EUR\n")
    add("Docs/Loyer/quittance-accents.txt", "route",
        "accented text must match an unaccented rule pattern", "rent-receipt")

    # -- sensitive: guarded ahead of any rule --------------------------------
    for rel, body in (
        ("Perso/passeport-scan.txt",
         "PASSEPORT\nNumero 24XY98765\nTitulaire : M. EXEMPLE\n"),
        ("Perso/rib.txt",
         f"RELEVE D'IDENTITE BANCAIRE\nIBAN {IBAN}\nTitulaire : M. EXEMPLE\n"),
        ("Perso/analyses.txt",
         "RESULTATS D'ANALYSES\nLaboratoire Imaginaire\nPatient : M. EXEMPLE\n"),
        ("Docs/Paie/bulletin-03-2025.txt",
         f"BULLETIN DE PAIE\nSalarie : M. EXEMPLE\nSIRET {SIRET_B}\n"
         "Periode : 03/2025\nNet a payer : 2 100,00 EUR\n"),
    ):
        write(os.path.join(target, rel), body)
        add(rel, "propose", "sensitive guard: never routed, never auto-trashed")

    # -- residual: nothing to reason on --------------------------------------
    for i in range(1, 3):
        rel = f"Docs/Scans/scan-{i:02d}.pdf"
        _pdf(os.path.join(target, rel), [])
        add(rel, "residual", "PDF with no text layer -> needs_vision + render")
    for i in range(1, 3):
        rel = f"Docs/Scans/photo-{i:02d}.jpg"
        os.makedirs(os.path.dirname(os.path.join(target, rel)), exist_ok=True)
        with open(os.path.join(target, rel), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + bytes([i]) * 2048)  # JPEG magic only
        add(rel, "residual", "image: only an agent can open it")
    write(os.path.join(target, "Docs/notes.txt"),
          "Reunion jeudi. Rien de plus.\n")
    add("Docs/notes.txt", "residual", "text, but no rule matches")
    for i in range(1, 3):
        rel = f"Docs/vieille-lettre-{i:02d}.doc"
        os.makedirs(os.path.dirname(os.path.join(target, rel)), exist_ok=True)
        with open(os.path.join(target, rel), "wb") as f:      # distinct bytes, so
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"       # the duplicate guard
                    + bytes([i]) * 4096)                      # stays out of the way
        add(rel, "residual", "legacy Word binary: no reader, no crash")

    # ======================= the traps ======================================
    # NFD in the name (macOS stores decomposed, everyone types composed)
    nfd = unicodedata.normalize("NFD", "Traps/facture-électricité-août.txt")
    write(os.path.join(target, nfd),
          "FACTURE ELECTRICITE\nCOMPAGNIE OHMEA\nMontant 84,20 EUR\n")
    add(nfd, "route", "NFD filename: NFC everywhere or memory paths split",
        "invoice")
    traps["nfd_name"] = True

    # two names differing by case alone
    write(os.path.join(target, "Traps/Casse-Test.txt"), "premier fichier\n")
    write(os.path.join(target, "Traps/casse-test.txt"), "second fichier\n")
    entries = os.listdir(os.path.join(target, "Traps"))
    traps["case_only_names_distinct"] = len(
        [e for e in entries if e.casefold() == "casse-test.txt"]) == 2
    for e in entries:
        if e.casefold() == "casse-test.txt":
            add(f"Traps/{e}", "residual", "case-only sibling")

    # byte-identical duplicates UNDER 4096 bytes — v1's DEDUP_MIN_SIZE hid
    # exactly this size class; the fixture that "proved" dedup was padded.
    dup = ("ATTESTATION D'ASSURANCE HABITATION\n"
           "Assureur : MUTUELLE DU PHARE\nContrat : 2025-4417\n")
    write(os.path.join(target, "Traps/attestation.txt"), dup)
    write(os.path.join(target, "Docs/attestation-copie.txt"), dup)
    add("Traps/attestation.txt", "propose",
        "byte-identical duplicate, ~100 bytes: no size threshold")
    add("Docs/attestation-copie.txt", "propose",
        "byte-identical duplicate, ~100 bytes: no size threshold")
    traps["small_byte_identical_duplicate"] = True

    # same text, different bytes — the re-download trap (invented issuer)
    base = "FACTURE ELECTRICITE\nCOMPAGNIE OHMEA\nMontant 84,20 EUR\n"
    write(os.path.join(target, "Traps/ohmea-v1.txt"), base + "\n")
    write(os.path.join(target, "Traps/ohmea-v2.txt"), base + "\n\n\n")
    add("Traps/ohmea-v1.txt", "route",
        "same text, different bytes: NOT a duplicate", "invoice")
    add("Traps/ohmea-v2.txt", "route",
        "same text, different bytes: NOT a duplicate", "invoice")
    traps["same_text_different_bytes"] = True

    # typographic apostrophe in the path
    apo = "Traps/Dossier d’archive/note d’honoraires.txt"
    write(os.path.join(target, apo),
          "NOTE D'HONORAIRES\nOHMEA CONSEIL\nMontant 450,00 EUR\n")
    add(apo, "route", "typographic apostrophe in the path, routed on content",
        "invoice")
    traps["typographic_apostrophe"] = True

    # zip whose entry names are cp437, without the UTF-8 flag
    zpath = os.path.join(target, "Traps/archive-cp437.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        info = zipfile.ZipInfo("donnée.txt".encode("utf-8").decode("cp437"))
        z.writestr(info, "contenu")
    add("Traps/archive-cp437.zip", "residual", "cp437 zip entry names")
    traps["cp437_zip"] = True

    # a file literally called Icon\r
    try:
        icon = os.path.join(target, "Traps", "Icon\r")
        with open(icon, "wb") as f:
            f.write(b"")
        traps["icon_cr"] = os.path.exists(icon)
        if traps["icon_cr"]:
            add("Traps/Icon\r", "residual", "carriage return in the filename")
    except OSError:
        traps["icon_cr"] = False

    # a name Windows reserves
    try:
        _pdf(os.path.join(target, "Traps/CON.pdf"), ["RESERVED NAME TEST"])
        traps["windows_reserved_name"] = True
        add("Traps/CON.pdf", "residual", "Windows-reserved name")
    except OSError:
        traps["windows_reserved_name"] = False

    # a path past 260 characters
    deep = os.path.join(target, "Traps",
                        *[f"level-{i:02d}-padding-segment" for i in range(9)])
    try:
        os.makedirs(deep, exist_ok=True)
        long_rel = os.path.relpath(os.path.join(deep, "document-profond.txt"),
                                   target)
        write(os.path.join(target, long_rel), "DOCUMENT PROFOND\nRien.\n")
        traps["path_over_260"] = len(os.path.join(target, long_rel)) > 260
        add(long_rel, "residual", "path longer than 260 characters")
    except OSError:
        traps["path_over_260"] = False

    # zero-byte and huge-name files
    write(os.path.join(target, "Traps/vide.txt"), "")
    add("Traps/vide.txt", "residual", "zero bytes")
    long_name = "Traps/" + "n" * 200 + ".txt"
    try:
        write(os.path.join(target, long_name), "nom tres long\n")
        add(long_name, "residual", "200-character filename")
        traps["long_filename"] = True
    except OSError:
        traps["long_filename"] = False

    # -- v2 traps ------------------------------------------------------------
    # a symlink whose target does not exist: lexists() but not exists()
    try:
        link = os.path.join(target, "Traps/lien-mort.pdf")
        os.symlink("cible-disparue.pdf", link)
        traps["dangling_symlink"] = (os.path.lexists(link)
                                     and not os.path.exists(link))
        add("Traps/lien-mort.pdf", "residual",
            "dangling symlink: guarded stat, failed entry, pass continues")
    except OSError:
        traps["dangling_symlink"] = False

    # an image-only sensitive scan: a prescription readable by vision alone.
    # Pixels spell it out; extraction returns nothing.
    _scan_pdf(os.path.join(target, "Inbox/scan-0042.pdf"),
              ["ORDONNANCE", "DR MARCHAND", "12/07/2026", "PARACETAMOL 1G"])
    add("Inbox/scan-0042.pdf", "propose",
        "image-only prescription: needs_vision, then sensitive guard")
    traps["image_only_sensitive"] = True

    # an inbox folder: a Desktop-like dump. Files here are proposed even when
    # a rule matches — an inbox is emptied by decision, never silently.
    _pdf(os.path.join(target, "Inbox/quittance-avril.pdf"),
         ["QUITTANCE DE LOYER", "Periode : 04/2025",
          "Montant : 780,00 EUR", "Bailleur : AGENCE LUNARIA"])
    add("Inbox/quittance-avril.pdf", "propose",
        "inbox guard beats a matching rule: propose, never a silent route")
    write(os.path.join(target, "Inbox/liste-courses.txt"),
          "pain, cafe, ampoules\n")
    add("Inbox/liste-courses.txt", "propose", "inbox file, nothing matches")
    traps["inbox_folder"] = True

    # an already-filed folder: files sitting at the destination their rule
    # renders. Nothing to move; the shadow comparison counts them as agreed.
    for i in (1, 2):
        rel = f"Archive/Loyer/quittances-2025/quittance-{i + 10:02d}.pdf"
        _pdf(os.path.join(target, rel),
             ["QUITTANCE DE LOYER", f"Periode : {i:02d}/2025",
              "Montant : 780,00 EUR", "Bailleur : AGENCE LUNARIA"])
        add(rel, "route", "already at the rule's destination: no move, agreed",
            "rent-receipt")
    traps["already_filed"] = True

    # two entities named with equal strength and NO shared identifier: no
    # arithmetic can pick a winner, only reading can.
    write(os.path.join(target, "Docs/contrat-cadre.txt"),
          "CONTRAT CADRE DE PRESTATION\n"
          "Entre SOCIETE HORIZON BLEU, prestataire,\n"
          "et CABINET MERIDIEN, client.\nFait le 05/02/2025\n")
    add("Docs/contrat-cadre.txt", "propose",
        "two entities, no identifier, equal strength-2 evidence: entity tie")
    traps["two_entity_no_id"] = True

    # the manifest lives beside the corpus, never inside it: a file in the
    # root would be one more thing to ingest
    return {"root": target, "files": files, "traps": traps,
            "count": len(files)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(json.dumps(build(sys.argv[1]), ensure_ascii=False, indent=1))
