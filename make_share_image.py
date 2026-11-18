# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10"]
# ///
"""Draw the link-preview image (Twitter, WhatsApp…) into static/share.png.

The title of DECLARATION.md on two lines, the partner logos underneath.
Run by hand when the title or the logos change; the result is committed.
"""

import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
OUT = ROOT / "static" / "share.png"
W, H = 1200, 630
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/sourceserif4/SourceSerif4%5Bopsz,wght%5D.ttf"
LOGOS = ["piaf.png", "logo-tuxae.png", "logo-telecom-ia.png", "logo-automatants.png", "logo-data-ensta.png"]


def two_lines(title: str) -> list[str]:
    """Split before " pour " when present, else at the space nearest the middle."""
    if " pour " in title:
        a, b = title.split(" pour ", 1)
        return [a, "pour " + b]
    words = title.split()
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(title) / 2))
    return [" ".join(words[:best]), " ".join(words[best:])]


def main() -> None:
    title = (ROOT / "DECLARATION.md").read_text().split("\n", 1)[0].removeprefix("# ").strip()
    font_path = Path(tempfile.gettempdir()) / "SourceSerif4-var.ttf"
    if not font_path.exists():
        urllib.request.urlretrieve(FONT_URL, font_path)
    font = ImageFont.truetype(str(font_path), 76)
    font.set_variation_by_axes([600, 60])  # wght (SemiBold), opsz

    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    lines = two_lines(title)
    y = 120
    for line in lines:
        draw.text((W / 2, y), line, font=font, fill="#111", anchor="mt")
        y += 96
    draw.line([(W / 2 - 60, y + 20), (W / 2 + 60, y + 20)], fill="#111", width=3)

    # Logos in a row, all scaled to the same height.
    height, gap = 120, 56
    logos = []
    for name in LOGOS:
        logo = Image.open(ROOT / "static" / "logos" / name).convert("RGBA")
        logos.append(logo.resize((round(logo.width * height / logo.height), height), Image.LANCZOS))
    x = (W - sum(l.width for l in logos) - gap * (len(logos) - 1)) // 2
    for logo in logos:
        img.paste(logo, (x, H - 90 - height), logo)
        x += logo.width + gap

    img.save(OUT, optimize=True)
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
