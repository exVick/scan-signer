# scan-signer

Put your signature on a scanned PDF without printing, signing and scanning it all over again.

Tell scan-signer which words are printed under the signature line, for example "Signature of Tenant" or "Unterschrift des Mieters". It reads the scan, finds those words and the line above them, and places your signature on the line. If you prefer to pick the spot yourself, you can do that too.

Everything happens on your own computer. Your documents and your signature are never uploaded anywhere.

## Where this comes from

scan-signer grew out of [auto-signer](https://github.com/Cynsar-Capital/auto-signer) by Cynsar Capital. Many thanks to its author for the idea and the starting point. auto-signer places signatures next to words it finds in a PDF's text layer. Scanned documents have no text layer, though, so I wrote this tool for them. It is a new implementation and does not contain auto-signer's code.

What scan-signer adds:

- **Works on scans.** It reads the page image with OCR (Tesseract), so no text layer is needed.
- **Finds the line for you.** It looks for the printed line just above your clue text, even when the scan is slightly tilted or a little noisy.
- **Picks the right spot.** It skips your clue words when they also appear in the body text, and it ignores a neighbouring signature line on the same height.
- **German and English** out of the box, with tolerance for small reading mistakes. Umlauts and ß work.
- **Keeps your signature's proportions** and sizes it to fit the line.
- **Manual mode with a millimetre grid** for anything automatic detection can't handle.
- **Signs only the page you ask for** and leaves your original file untouched.

## Installation

You need Python 3.10 or newer and Tesseract with the languages you want. On Ubuntu or Kubuntu:

```bash
sudo apt install tesseract-ocr tesseract-ocr-deu    # deu = German, English is included
```

Then install scan-signer into its own environment:

```bash
git clone <your repository URL> scan-signer
cd scan-signer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Whenever you open a new terminal, run `source .venv/bin/activate` inside the folder first.

## How to use it

### Automatic: give it a clue

```bash
scan-signer "contract.pdf" -s "signature.png" --below "Unterschrift des Mieters" --page 3
```

- `-s`: your signature image. A PNG with a transparent background looks best. A white background would cover part of the page.
- `--below`: a few words printed under the signature line. A distinctive part of the label is enough.
- `--page`: the page to sign, counting from 1. Leave it out and every page is searched.

It tells you what it found and writes `contract_signed.pdf` next to the original:

```
Match 2: "Unterschrift des Mieters" on page 3 at 44 mm across, 219 mm down
Using match 2 (choose another with --match N).
Signature line found on page 3: 24-90 mm across, 217 mm down.
Wrote contract_signed.pdf
```

Please open the result and give it a quick look before you send it.

### Manual: pick the spot yourself

First make a copy of the document with a millimetre grid on it:

```bash
scan-signer "contract.pdf" --grid
```

Open `contract_grid.pdf` and read off where the top-left corner of the signature should go. Red numbers are millimetres across, blue numbers are millimetres down. Then sign:

```bash
scan-signer "contract.pdf" -s "signature.png" --page 2 --x 30 --y 240 --width 50
```

### Handy options

| Option | What it does |
|---|---|
| `--width 45` | Signature width in millimetres |
| `--match 1` | Use another match when the clue appears more than once |
| `-o out.pdf` | Choose the output file name |
| `--lang eng` | OCR language(s); the default is `deu+eng` |

To sign several places, run it again on the output of the previous run:

```bash
scan-signer contract.pdf -s me.png --below "Signature of Tenant" -o step1.pdf
scan-signer step1.pdf -s landlord.png --below "Signature of Landlord" -o final.pdf
```

### When something doesn't work

- **"Could not find the text"**: try a shorter clue, such as `"Mieters"`. Check that its language is installed and included in `--lang`.
- **No line found**: dotted lines and boxes are not detected yet. The signature is then placed just above the clue text. Use manual mode if that isn't right.
- **Wrong spot**: look at the list of matches it prints and pick another with `--match`.

## Running the tests

```bash
pip install -e ".[dev]"
pytest
```

The tests create their own fake scanned documents, so no real files are needed.

## A note on signatures

An image of a signature is not a qualified electronic signature. It's fine for many everyday documents, but if a contract needs a legally binding electronic signature, please check what the other party accepts.

## License

MIT, see [LICENSE](LICENSE).
