"""Place a signature image on a scanned PDF (no text layer needed).

Two ways to choose the spot:
  * Automatic: --below "text" reads the scan (OCR), finds that text and the
    signature line just above it, and signs on the line.
  * Manual: --x/--y in millimetres measured from the TOP-LEFT corner of the
    page, which is how you would measure on paper. Use --grid to find them.

Examples:
    scan-signer contract.pdf -s signature.png --below "Signature of tenant"
    scan-signer contract.pdf --grid
    scan-signer contract.pdf -s signature.png --page 2 --x 30 --y 240 --width 50
"""
import argparse
import re
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytesseract
from PIL import Image
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.lib.colors import Color
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

OCR_DPI = 200
PX_PER_MM = OCR_DPI / 25.4
SUPPORTED_LANGS = {"bul": "Bulgarian", "deu": "German", "eng": "English"}

# Cyrillic letters that look like Latin ones. OCR reading several languages at once
# often swaps them, so both the clue and the scanned words are compared in Latin form.
LOOKALIKES = str.maketrans("аеорсухкмтнвіј", "aeopcyxkmthbij")


def _overlay(width_pt, height_pt, draw):
    """Build a one-page PDF of the given size with `draw(canvas)` on it."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width_pt, height_pt))
    draw(c)
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def _merge_onto(page, overlay_page):
    # Shift the overlay if the page box does not start at (0, 0).
    box = page.mediabox
    if box.left or box.bottom:
        page.merge_transformed_page(overlay_page, Transformation().translate(box.left, box.bottom))
    else:
        page.merge_page(overlay_page)


def make_grid(input_pdf, output_pdf):
    """Write a copy of the PDF with a 10 mm grid and labels on every page."""
    writer = PdfWriter(clone_from=PdfReader(input_pdf))
    for page in writer.pages:
        page.transfer_rotation_to_content()
        w, h = float(page.mediabox.width), float(page.mediabox.height)

        def draw(c):
            c.setFont("Helvetica", 6)
            for i in range(0, int(w / mm) + 1, 10):
                c.setStrokeColor(Color(1, 0, 0, alpha=0.6 if i % 50 == 0 else 0.25))
                c.line(i * mm, 0, i * mm, h)
                c.setFillColor(Color(1, 0, 0))
                c.drawString(i * mm + 1, h - 8, str(i))
            for j in range(0, int(h / mm) + 1, 10):
                c.setStrokeColor(Color(0, 0, 1, alpha=0.6 if j % 50 == 0 else 0.25))
                c.line(0, h - j * mm, w, h - j * mm)
                c.setFillColor(Color(0, 0, 1))
                c.drawString(2, h - j * mm - 7, str(j))

        _merge_onto(page, _overlay(w, h, draw))
    with open(output_pdf, "wb") as f:
        writer.write(f)


def _norm(word):
    return re.sub(r"\W", "", word.lower()).translate(LOOKALIKES)


def resolve_lang(lang):
    """Return the OCR language string to use, checking that its languages are installed.

    With no `lang`, every installed language out of Bulgarian, German and English is used.
    """
    installed = set(pytesseract.get_languages())
    if lang is None:
        langs = [code for code in SUPPORTED_LANGS if code in installed]
        if not langs:
            raise SystemExit("No OCR language is installed. Install one, e.g.: "
                             "sudo apt install tesseract-ocr-eng tesseract-ocr-deu tesseract-ocr-bul")
        return "+".join(langs)
    missing = [code for code in lang.split("+") if code not in installed]
    if missing:
        raise SystemExit(f"OCR language(s) not installed: {', '.join(missing)}. "
                         f"Install with: sudo apt install {' '.join('tesseract-ocr-' + m for m in missing)}")
    return lang


def _find_clue(img, clue, lang):
    """Return pixel boxes (left, top, right, bottom) of every place the clue text appears."""
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
    words = [
        (data["text"][i], data["left"][i], data["top"][i], data["width"][i], data["height"][i],
         (data["block_num"][i], data["par_num"][i], data["line_num"][i]))
        for i in range(len(data["text"])) if _norm(data["text"][i])
    ]
    target = [_norm(w) for w in clue.split() if _norm(w)]
    boxes = []
    for i in range(len(words) - len(target) + 1):
        group = words[i:i + len(target)]
        if len({w[5] for w in group}) != 1:  # all words must be on the same text line
            continue
        # Tolerate small OCR mistakes, e.g. "Signatnre" for "Signature".
        if all(SequenceMatcher(None, _norm(w[0]), t).ratio() >= 0.8 for w, t in zip(group, target)):
            boxes.append((min(w[1] for w in group), min(w[2] for w in group),
                          max(w[1] + w[3] for w in group), max(w[2] + w[4] for w in group)))
    return boxes


def _find_line_above(img, clue_box):
    """Find the nearest long horizontal line above the clue. Returns (left, right, y) in pixels or None."""
    gray = np.asarray(img.convert("L"))
    left, top, right, _ = clue_box
    x0 = max(0, int(left - 40 * PX_PER_MM))
    x1 = min(gray.shape[1], int(right + 120 * PX_PER_MM))
    y0 = max(0, int(top - 30 * PX_PER_MM))
    dark = gray[y0:top, x0:x1] < 140

    # Smear vertically by ~3 mm so a tilted scanned line still shows up in one row.
    band = max(1, int(3 * PX_PER_MM))
    smeared = np.array([dark[r:r + band].any(axis=0) for r in range(max(0, dark.shape[0] - band))])

    def longest_run(r):
        cols = np.flatnonzero(smeared[r])
        best = None
        # Split into runs, allowing tiny gaps from scan noise.
        for run in np.split(cols, np.flatnonzero(np.diff(cols) > 0.6 * PX_PER_MM) + 1):
            length = run[-1] - run[0] + 1 if len(run) else 0
            # Text rows never form a long, mostly solid run; a printed line does.
            # It must also sit over the clue, not over a neighbouring signature block.
            over_clue = len(run) and x0 + run[0] <= right and x0 + run[-1] >= left
            if over_clue and len(run) / max(length, 1) > 0.8 and (best is None or length > best[1] - best[0]):
                best = (run[0], run[-1])
        return best

    min_len = 25 * PX_PER_MM
    for r in range(len(smeared) - 1, -1, -1):  # bottom-up: the line closest to the clue wins
        run = longest_run(r)
        if run is None or run[1] - run[0] < min_len:
            continue
        # The first hit only touches the edge of the line; the full line is within the next band of rows.
        candidates = [(run, r)] + [(longest_run(q), q) for q in range(r - 1, max(-1, r - band), -1)]
        (start, end), r = max(((c, q) for c, q in candidates if c), key=lambda cq: cq[0][1] - cq[0][0])
        rows = np.flatnonzero(dark[r:r + band, start:end + 1].any(axis=1))
        return x0 + start, x0 + end, y0 + r + rows.mean()
    return None


def find_spot(input_pdf, clue, page_no, match, lang=None):
    """OCR the page(s) and return (page_no, line_left_mm, line_right_mm, line_y_mm, found_line)."""
    lang = resolve_lang(lang)
    pdf = pdfium.PdfDocument(input_pdf)
    pages = range(len(pdf)) if page_no is None else [page_no - 1 if page_no > 0 else len(pdf) - 1]
    hits = []
    for index in pages:
        img = pdf[index].render(scale=OCR_DPI / 72).to_pil()
        for box in _find_clue(img, clue, lang):
            hits.append((index, box, img))
    if not hits:
        raise SystemExit(f'Could not find the text "{clue}". Try a shorter or different clue, or use --x/--y.')

    for n, (index, box, _) in enumerate(hits, 1):
        print(f'Match {n}: "{clue}" on page {index + 1} at {box[0] / PX_PER_MM:.0f} mm across, '
              f'{box[1] / PX_PER_MM:.0f} mm down')
    if match is None:
        # Prefer the first match that has a signature line above it (skips the same words in body text).
        lines = [_find_line_above(img, box) for _, box, img in hits]
        match = next((n for n, line in enumerate(lines, 1) if line), 1)
        line = lines[match - 1]
    elif 1 <= match <= len(hits):
        line = _find_line_above(hits[match - 1][2], hits[match - 1][1])
    else:
        raise SystemExit(f"--match {match} does not exist; there are {len(hits)} match(es).")
    if len(hits) > 1:
        print(f"Using match {match} (choose another with --match N).")

    index, box, img = hits[match - 1]
    if line is None:
        # No line found: sign directly above the clue text instead.
        left, top, right, _ = box
        return index + 1, left / PX_PER_MM, max(right / PX_PER_MM, left / PX_PER_MM + 50), top / PX_PER_MM - 1, False
    left, right, y = line
    return index + 1, left / PX_PER_MM, right / PX_PER_MM, y / PX_PER_MM, True


def sign(input_pdf, output_pdf, signature, page_no, x_mm, y_mm, width_mm):
    """Draw `signature` on page `page_no` (1-based, -1 = last) with its top-left at (x_mm, y_mm)."""
    reader = PdfReader(input_pdf)
    writer = PdfWriter(clone_from=reader)
    index = len(writer.pages) - 1 if page_no == -1 else page_no - 1
    if not 0 <= index < len(writer.pages):
        raise SystemExit(f"Page {page_no} does not exist; the PDF has {len(writer.pages)} page(s).")

    page = writer.pages[index]
    page.transfer_rotation_to_content()
    w, h = float(page.mediabox.width), float(page.mediabox.height)

    with Image.open(signature) as img:
        aspect = img.height / img.width
    sig_w = width_mm * mm
    sig_h = sig_w * aspect  # keep the signature's proportions
    x = x_mm * mm
    y = h - y_mm * mm - sig_h  # PDF y runs bottom-up

    _merge_onto(page, _overlay(w, h, lambda c: c.drawImage(str(signature), x, y, sig_w, sig_h, mask="auto")))
    with open(output_pdf, "wb") as f:
        writer.write(f)


def main():
    p = argparse.ArgumentParser(prog="scan-signer", description="Overlay a signature image on a scanned PDF.")
    p.add_argument("input_pdf", type=Path)
    p.add_argument("-o", "--output", type=Path, help="Output PDF (default: <input>_signed.pdf or <input>_grid.pdf).")
    p.add_argument("--grid", action="store_true", help="Only write a copy with a mm grid, to find coordinates.")
    p.add_argument("-s", "--signature", type=Path, help="Signature image (PNG with transparent background is best).")
    p.add_argument("--below", metavar="TEXT", help="Find the signature line just above this printed text.")
    p.add_argument("--match", type=int, help="With --below: which match to use if the text appears more than once "
                                             "(default: the first one with a line above it).")
    p.add_argument("--lang", help="With --below: OCR language(s), e.g. bul, deu, eng or bul+eng "
                                  "(default: every installed one of bul, deu and eng).")
    p.add_argument("--page", type=int, help="Page number, starting at 1, -1 = last "
                                            "(default: last page; with --below, all pages are searched).")
    p.add_argument("--x", type=float, help="Left edge of the signature, mm from the page's left edge.")
    p.add_argument("--y", type=float, help="Top edge of the signature, mm from the page's top edge.")
    p.add_argument("--width", type=float, help="Signature width in mm (default: 50, or fitted to the line with --below).")
    args = p.parse_args()

    if args.grid:
        out = args.output or args.input_pdf.with_name(args.input_pdf.stem + "_grid.pdf")
        make_grid(args.input_pdf, out)
        print(f"Wrote {out}")
        return

    if args.signature is None:
        p.error("signing needs --signature")
    out = args.output or args.input_pdf.with_name(args.input_pdf.stem + "_signed.pdf")

    if args.below:
        page_no, left, right, line_y, found_line = find_spot(args.input_pdf, args.below, args.page, args.match, args.lang)
        width = args.width or min(0.8 * (right - left), 60)
        with Image.open(args.signature) as img:
            height = width * img.height / img.width
        x = left + (right - left - width) / 2  # centred on the line
        # Let the bottom of the signature dip slightly below the line, like a real one.
        y = line_y - 0.85 * height if found_line else line_y - height
        if found_line:
            print(f"Signature line found on page {page_no}: {left:.0f}-{right:.0f} mm across, {line_y:.0f} mm down.")
        else:
            print("No signature line found above the text; placing the signature right above the text instead.")
        sign(args.input_pdf, out, args.signature, page_no, x, y, width)
    else:
        if args.x is None or args.y is None:
            p.error("signing needs --below TEXT, or --x and --y (run with --grid first to find x/y)")
        sign(args.input_pdf, out, args.signature, args.page or -1, args.x, args.y, args.width or 50)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
