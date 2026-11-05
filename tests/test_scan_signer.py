import shutil
import sys
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from scan_signer import cli
from scan_signer.cli import find_spot, make_grid, sign


def _have_tesseract_langs(*langs):
    if shutil.which("tesseract") is None:
        return False
    import pytesseract
    return set(langs) <= set(pytesseract.get_languages())


needs_ocr = pytest.mark.skipif(not _have_tesseract_langs("deu", "eng"),
                               reason="Tesseract with German and English is not installed")
needs_bulgarian = pytest.mark.skipif(not _have_tesseract_langs("bul"),
                                     reason="Tesseract's Bulgarian language pack is not installed")

CYRILLIC_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]


def _cyrillic_font(size):
    for path in CYRILLIC_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    pytest.skip("No font with Cyrillic letters found")


def _to_scan(img, rng):
    """Tilt, blur and add noise so a clean drawing looks like a scanned page."""
    img = img.rotate(0.8, fillcolor=242, resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.8))
    a = np.asarray(img).astype(int) + rng.normal(0, 10, img.size[::-1]).astype(int)
    return Image.fromarray(np.clip(a, 0, 255).astype("uint8")).convert("RGB")


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    """A tilted, slightly noisy 3-page 'scan' with no text layer.

    Pages 2 and 3 have two signature lines side by side; page 3 also mentions
    the tenant's clue in the body text. Page 3 has an unlined "Zeuge" label.
    """
    path = tmp_path_factory.mktemp("scan") / "vertrag.pdf"
    rng = np.random.default_rng(0)
    pages = []
    for p in range(3):
        img = Image.new("L", (1700, 2200), 242)
        d = ImageDraw.Draw(img)
        label = ImageFont.load_default(size=30)
        body = ImageFont.load_default(size=26)
        d.text((150, 150), f"MIETVERTRAG - Seite {p + 1}", fill=20, font=ImageFont.load_default(size=44))
        for i in range(20):
            text = ("Die Größe der Wohnung beträgt 72 m². Unterschrift des Mieters folgt."
                    if i == 3 and p == 2 else "Der Mieter verpflichtet sich, die Räume pfleglich zu behandeln.")
            d.text((150, 260 + i * 48), text, fill=40, font=body)
        if p > 0:
            d.line((180, 1700, 700, 1700), fill=10, width=3)
            d.text((180, 1715), "Ort, Datum, Unterschrift des Mieters", fill=10, font=label)
            d.line((950, 1700, 1500, 1700), fill=10, width=3)
            d.text((950, 1715), "Unterschrift der Vermieterin", fill=10, font=label)
        if p == 2:
            d.text((180, 1950), "Zeuge", fill=10, font=label)
        pages.append(_to_scan(img, rng))
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=200)
    return path


@pytest.fixture(scope="module")
def bulgarian_scan(tmp_path_factory):
    """A 2-page Bulgarian 'scan': page 2 has buyer and seller lines side by side,
    and the buyer's clue also appears in the body text above them."""
    path = tmp_path_factory.mktemp("scan_bg") / "dogovor.pdf"
    rng = np.random.default_rng(1)
    label, body = _cyrillic_font(30), _cyrillic_font(26)
    pages = []
    for p in range(2):
        img = Image.new("L", (1700, 2200), 242)
        d = ImageDraw.Draw(img)
        d.text((150, 150), f"ДОГОВОР ЗА ПОКУПКО-ПРОДАЖБА - страница {p + 1}", fill=20, font=_cyrillic_font(40))
        for i in range(20):
            text = ("Договорът влиза в сила след подпис на купувача и продавача."
                    if i == 4 and p == 1 else "Страните се съгласяват с всички условия на настоящия договор.")
            d.text((150, 260 + i * 48), text, fill=40, font=body)
        if p == 1:
            d.line((180, 1700, 700, 1700), fill=10, width=3)
            d.text((180, 1715), "Дата и подпис на купувача", fill=10, font=label)
            d.line((950, 1700, 1500, 1700), fill=10, width=3)
            d.text((950, 1715), "Подпис на продавача", fill=10, font=label)
        pages.append(_to_scan(img, rng))
    pages[0].save(path, save_all=True, append_images=pages[1:], resolution=200)
    return path


@pytest.fixture(scope="module")
def dotted_scan(tmp_path_factory):
    """A 'scan' with four kinds of dotted signature lines, two side by side per row, under body
    text that has dots of its own. "Datum" sits right under that body text with no line at all."""
    path = tmp_path_factory.mktemp("scan_dots") / "dotted.pdf"
    img = Image.new("L", (1700, 2200), 242)
    d = ImageDraw.Draw(img)
    for i in range(16):
        d.text((150, 200 + i * 48), "Die Vertragsparteien bestätigen die Richtigkeit aller Angaben, i. d. R. ...",
               fill=40, font=_cyrillic_font(26))
    d.text((180, 980), "Datum", fill=10, font=_cyrillic_font(30))
    for x, y, dots, label in DOTTED_ROWS:
        d.text((x, y), dots, fill=10, font=_cyrillic_font(30))
        d.text((x, y + 60), label, fill=10, font=_cyrillic_font(30))
    _to_scan(img, np.random.default_rng(2)).save(path, resolution=200)
    return path


@pytest.fixture(scope="module")
def signature(tmp_path_factory):
    path = tmp_path_factory.mktemp("sig") / "signature.png"
    img = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(20, 150), (200, 40), (380, 160), (580, 50)], fill=(10, 30, 120, 255), width=8)
    img.save(path)
    return path


def _render(pdf_path):
    return [np.asarray(page.render(scale=0.5).to_pil().convert("L")) for page in pdfium.PdfDocument(pdf_path)]


# (x px, y px, dotted line, clue) at 200 dpi; the expected extents in mm are measured from the drawing.
DOTTED_ROWS = [
    (180, 1150, "…" * 20, "Unterschrift des Käufers"),
    (950, 1150, ". " * 24, "Unterschrift des Verkäufers"),
    (180, 1500, "." * 70, "Подпис на купувача"),
    (950, 1500, "- " * 22, "Signature of Guarantor"),
]
DOTTED_EXPECTED = {
    "Unterschrift des Käufers": (22.9, 99.1, 149.4),
    "Unterschrift des Verkäufers": (120.6, 178.8, 149.4),
    "Подпис на купувача": (22.9, 107.6, 193.8),
    "Signature of Guarantor": (120.6, 177.5, 193.5),
}

# The lines are drawn at 180-700 px and 950-1500 px across, 1700 px down, at 200 dpi.
TENANT_LINE = (22.9, 88.9)
LANDLORD_LINE = (120.7, 190.5)


@needs_ocr
def test_finds_the_line_above_the_clue_on_the_given_page(scan):
    page, left, right, y, found = find_spot(scan, "Unterschrift der Vermieterin", 2, None, "deu+eng")
    assert (page, found) == (2, True)
    assert left == pytest.approx(LANDLORD_LINE[0], abs=4)
    assert right == pytest.approx(LANDLORD_LINE[1], abs=4)
    assert y == pytest.approx(216, abs=4)


@needs_ocr
def test_skips_body_text_and_ignores_the_neighbouring_line(scan):
    page, left, right, _, found = find_spot(scan, "Unterschrift des Mieters", 3, None, "deu+eng")
    assert (page, found) == (3, True)
    assert left == pytest.approx(TENANT_LINE[0], abs=4)
    assert right == pytest.approx(TENANT_LINE[1], abs=4)


@needs_ocr
def test_falls_back_to_above_the_text_when_there_is_no_line(scan):
    *_, found = find_spot(scan, "Zeuge", 3, None, "deu+eng")
    assert found is False


@needs_ocr
def test_reports_missing_clue(scan):
    with pytest.raises(SystemExit, match="Could not find"):
        find_spot(scan, "Bürgschaft", None, None, "deu+eng")


@needs_bulgarian
def test_bulgarian_clue_finds_its_own_line(bulgarian_scan):
    page, left, right, _, found = find_spot(bulgarian_scan, "подпис на купувача", 2, None)
    assert (page, found) == (2, True)
    assert left == pytest.approx(TENANT_LINE[0], abs=4)
    assert right == pytest.approx(TENANT_LINE[1], abs=4)

    page, left, right, _, found = find_spot(bulgarian_scan, "Подпис на продавача", None, None)
    assert (page, found) == (2, True)
    assert left == pytest.approx(LANDLORD_LINE[0], abs=4)


@needs_ocr
@needs_bulgarian
@pytest.mark.parametrize("clue", list(DOTTED_EXPECTED))
def test_dotted_lines_are_found(dotted_scan, clue):
    page, left, right, y, found = find_spot(dotted_scan, clue, 1, None)
    assert (page, found) == (1, True)
    exp_left, exp_right, exp_y = DOTTED_EXPECTED[clue]
    assert left == pytest.approx(exp_left, abs=4)
    assert right == pytest.approx(exp_right, abs=4)
    assert y == pytest.approx(exp_y, abs=3)


@needs_ocr
def test_dots_in_body_text_are_not_a_line(dotted_scan):
    *_, found = find_spot(dotted_scan, "Datum", 1, None, "deu+eng")
    assert found is False


def test_cyrillic_lookalikes_compare_equal():
    # "Подпис" typed in Cyrillic and the same word with OCR's Latin o and c.
    assert cli._norm("Подпис:") == cli._norm("Пoдпиc")


def test_missing_language_is_reported():
    with pytest.raises(SystemExit, match="tesseract-ocr-xxx"):
        cli.resolve_lang("eng+xxx")


def test_sign_changes_only_the_chosen_page(scan, signature, tmp_path):
    out = tmp_path / "signed.pdf"
    sign(scan, out, signature, 2, 40, 180, 50)
    before, after = _render(scan), _render(out)
    assert len(after) == 3
    assert np.array_equal(before[0], after[0])
    assert np.array_equal(before[2], after[2])
    assert not np.array_equal(before[1], after[1])


def test_sign_rejects_missing_page(scan, signature, tmp_path):
    with pytest.raises(SystemExit, match="does not exist"):
        sign(scan, tmp_path / "x.pdf", signature, 9, 10, 10, 50)


def test_grid_keeps_every_page(scan, tmp_path):
    out = tmp_path / "grid.pdf"
    make_grid(scan, out)
    before, after = _render(scan), _render(out)
    assert len(after) == 3
    assert all(not np.array_equal(b, a) for b, a in zip(before, after))


@needs_ocr
def test_command_line_end_to_end(scan, signature, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cli.pdf"
    monkeypatch.setattr(sys, "argv", ["scan-signer", str(scan), "-s", str(signature),
                                      "--below", "Unterschrift des Mieters", "--page", "3", "-o", str(out)])
    cli.main()
    assert "Signature line found on page 3" in capsys.readouterr().out
    before, after = _render(scan), _render(out)
    assert np.array_equal(before[1], after[1])
    assert not np.array_equal(before[2], after[2])
