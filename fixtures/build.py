#!/usr/bin/env python3
"""Build the booby-trapped corpus verify.py runs against.

The corpus is generated rather than committed, because half of what it has to
contain cannot survive a git checkout: a file called `CON.pdf` is unwritable on
Windows, `Icon\\r` confuses archive tools, and two names differing only by case
collapse into one on a case-insensitive volume. Generating them means the traps
are real on the machine under test, and the manifest records which ones the
filesystem actually accepted.

Every trap here is one that has bitten before, or that will bite on a platform
this tool claims to support.

Usage: build.py <target-dir>     prints the manifest as JSON
"""
import json
import os
import shutil
import sys
import unicodedata
import zipfile

SIREN_A, SIREN_B = "123456789", "987654321"


# ------------------------------------------------------------------- pdf ----
def _pdf(path, lines):
    """A minimal, well-formed PDF. No lines = a page with no text layer."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    objs = []
    objs.append(b"<</Type/Catalog/Pages 2 0 R>>")
    objs.append(b"<</Type/Pages/Kids[3 0 R]/Count 1>>")
    objs.append(b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]"
                b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>")
    if lines:
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


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def build(target):
    target = os.path.abspath(os.path.expanduser(target))
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target)
    files, traps = [], {}

    def add(rel, lane, why, rule=None):
        files.append({"path": rel, "lane": lane, "why": why, "rule": rule})

    # -- ordinary documents, so the rules have something to bite on ----------
    for i in range(1, 13):
        rel = f"Housing/rent-receipt-{i:02d}.pdf"
        _pdf(os.path.join(target, rel),
             ["QUITTANCE DE LOYER - rent receipt", f"Periode : {i:02d}/2024",
              "Montant : 780,00 EUR", "Bailleur : Agence Test"])
        add(rel, "route", "rule rent-receipt on extracted text", "rent-receipt")

    for i in range(1, 9):
        rel = f"Company/invoice-2024-{i:03d}.pdf"
        _pdf(os.path.join(target, rel),
             ["FACTURE", f"Numero : 2024-{i:03d}", f"SIREN {SIREN_A}",
              "Total TTC : 1 240,00 EUR", "Date : 12/03/2024"])
        add(rel, "route", "rule invoice, entity resolved by SIREN", "invoice")

    # un document qui nomme DEUX entites avec la meme force de preuve : personne
    # ne gagne, la lecture tranche
    write(os.path.join(target, "Company/cession-contract.txt"),
          f"CONTRAT DE CESSION D'ACTIF\nEntre ACMECORP SIREN {SIREN_A}\n"
          f"et REPRENEUR SIREN {SIREN_B}\nFait a Paris le 12/03/2024\n")
    add("Company/cession-contract.txt", "propose",
        "two entities, evidence of equal strength")

    # deux documents en texte brut : ils routent meme sans les wheels PDF, donc
    # la resolution d'entite reste testable en mode minimal
    write(os.path.join(target, "Housing/rent-receipt-plain.txt"),
          "QUITTANCE DE LOYER\nPeriode : 07/2024\nMontant : 780,00 EUR\n")
    add("Housing/rent-receipt-plain.txt", "route",
        "entity with no identifier, resolved on the text", "rent-receipt")
    write(os.path.join(target, "Company/invoice-plain.txt"),
          f"FACTURE\nNumero : 2024-999\nSIREN {SIREN_A}\nTotal TTC : 500,00 EUR\n")
    add("Company/invoice-plain.txt", "route",
        "entity with identifiers, resolved on the SIREN", "invoice")

    for i in range(1, 5):
        rel = f"Company/payslip-2024-{i:02d}.txt"
        write(os.path.join(target, rel),
              f"BULLETIN DE PAIE\nSalarie : Test\nSIRET {SIREN_B}00012\n"
              f"Periode : {i:02d}/2024\nNet a payer : 2 100,00 EUR\n")
        add(rel, "propose", "payslip is sensitive: never routes automatically")

    # -- sensitive: guarded ahead of any rule --------------------------------
    for rel, body in (
        ("Personal/Identity/passport-scan.txt",
         "PASSEPORT\nRepublique Francaise\nNumero 12AB34567\nTitulaire : Test\n"),
        ("Personal/Identity/bank-details.txt",
         "RELEVE D'IDENTITE BANCAIRE\nIBAN FR76 3000 1007 9412 3456 7890 185\n"),
        ("Personal/Health/lab-results.txt",
         "RESULTATS D'ANALYSES\nLaboratoire Test\nPatient : Test\n"),
    ):
        write(os.path.join(target, rel), body)
        add(rel, "propose", "sensitive guard")

    # -- residual: nothing to reason on --------------------------------------
    for i in range(1, 7):
        rel = f"Scans/scan-{i:02d}.pdf"
        _pdf(os.path.join(target, rel), [])
        add(rel, "residual", "PDF with no text layer -> render or opaque")
    for i in range(1, 5):
        rel = f"Scans/photo-{i:02d}.jpg"
        os.makedirs(os.path.dirname(os.path.join(target, rel)), exist_ok=True)
        with open(os.path.join(target, rel), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 2048)   # JPEG magic, no pixels
        add(rel, "residual", "image: the agent opens it")
    write(os.path.join(target, "Misc/notes.txt"), "Reunion jeudi. Rien de plus.\n")
    add("Misc/notes.txt", "residual", "text, but no rule matches")

    # -- legacy .doc: goes to the residual lane without crashing -------------
    for i in range(1, 5):
        rel = f"Misc/old-letter-{i:02d}.doc"
        os.makedirs(os.path.dirname(os.path.join(target, rel)), exist_ok=True)
        with open(os.path.join(target, rel), "wb") as f:      # distinct bytes, so
            f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"       # the duplicate guard
                    + bytes([i]) * 4096)                      # stays out of the way
        add(rel, "residual", "legacy Word binary: no reader, no crash")

    # ======================= the traps ======================================
    # NFD in the name (macOS stores decomposed, everyone types composed)
    nfd = unicodedata.normalize("NFD", "Traps/facture-électricité-août.txt")
    write(os.path.join(target, nfd), "FACTURE Electricite\nMontant 84,20 EUR\n")
    add(nfd, "route", "NFD filename", "invoice-generic")
    traps["nfd_name"] = True

    # two names differing by case alone
    write(os.path.join(target, "Traps/Casse-Test.txt"), "premier fichier\n")
    write(os.path.join(target, "Traps/casse-test.txt"), "second fichier\n")
    entries = os.listdir(os.path.join(target, "Traps"))
    traps["case_only_names_distinct"] = len([e for e in entries
                                             if e.casefold() == "casse-test.txt"]) == 2
    for e in entries:
        if e.casefold() == "casse-test.txt":
            add(f"Traps/{e}", "residual", "case-only sibling")

    # byte-identical duplicate, in two different folders
    dup = "CONTRAT DE PRESTATION\nEntre les parties\n" + "x" * 5000
    write(os.path.join(target, "Traps/contract.txt"), dup)
    write(os.path.join(target, "Company/contract-copy.txt"), dup)
    add("Traps/contract.txt", "propose", "byte-identical duplicate")
    add("Company/contract-copy.txt", "propose", "byte-identical duplicate")
    traps["byte_identical_duplicate"] = True

    # same text, different bytes — the EDF re-download trap
    base = "FACTURE ELECTRICITE\nClient 4471120\nMontant 84,20 EUR\n"
    write(os.path.join(target, "Traps/edf-v1.txt"), base + "\n")
    write(os.path.join(target, "Traps/edf-v2.txt"), base + "\n\n\n")
    add("Traps/edf-v1.txt", "route", "same text, different bytes: NOT a duplicate",
        "invoice-generic")
    add("Traps/edf-v2.txt", "route", "same text, different bytes: NOT a duplicate",
        "invoice-generic")
    traps["same_text_different_bytes"] = True

    # typographic apostrophe in the path
    apo = "Traps/Dossier d’archive/note d’honoraires.txt"
    write(os.path.join(target, apo), "NOTE D'HONORAIRES\nMontant 450,00 EUR\n")
    add(apo, "route", "typographic apostrophe in the path, still routed on content",
        "invoice-generic")
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
    deep = os.path.join(target, "Traps", *[f"level-{i:02d}-padding-segment" for i in range(9)])
    try:
        os.makedirs(deep, exist_ok=True)
        long_rel = os.path.relpath(os.path.join(deep, "deep-document.txt"), target)
        write(os.path.join(target, long_rel), "DOCUMENT PROFOND\nRien de special.\n")
        traps["path_over_260"] = len(os.path.join(target, long_rel)) > 260
        add(long_rel, "residual", "path longer than 260 characters")
    except OSError:
        traps["path_over_260"] = False

    # zero-byte and huge-name files
    write(os.path.join(target, "Traps/empty.txt"), "")
    add("Traps/empty.txt", "residual", "zero bytes")
    long_name = "Traps/" + "n" * 200 + ".txt"
    try:
        write(os.path.join(target, long_name), "nom tres long\n")
        add(long_name, "residual", "200-character filename")
        traps["long_filename"] = True
    except OSError:
        traps["long_filename"] = False

    # the manifest lives beside the corpus, never inside it: a file in the root
    # would be one more thing to ingest
    return {"root": target, "files": files, "traps": traps, "count": len(files)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(json.dumps(build(sys.argv[1]), ensure_ascii=False, indent=1))
