# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown>=3.6"]
# ///
"""Build the static site: DECLARATION.md + signatories -> _site/index.html.

Signatories are fetched from $SIGNATORIES_URL when set, otherwise read
from data/signatories.json.
"""

import html
import json
import os
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import markdown

ROOT = Path(__file__).parent
OUT = ROOT / "_site"


def load_signatories() -> list[dict]:
    url = os.environ.get("SIGNATORIES_URL")
    if url:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.load(resp)
    return json.loads((ROOT / "data" / "signatories.json").read_text())


def render_signatory(s: dict) -> str:
    e = lambda k: html.escape(s.get(k) or "")
    comment = f'<blockquote>« {e("comment")} »</blockquote>' if s.get("comment") else ""
    featured = " featured" if s.get("featured") else ""
    return (
        f'<li class="signatory{featured}">'
        f'<p><span class="name">{e("firstname")} {e("name")}</span>, '
        f'<span class="institution">{e("institution")}</span></p>'
        f'<p class="role">{e("role")}</p>'
        f"{comment}</li>"
    )


def main() -> None:
    # The first "# ..." line is the page title, shown in the header.
    title, text = (ROOT / "DECLARATION.md").read_text().split("\n", 1)
    title = html.escape(title.removeprefix("# ").strip())
    md = markdown.Markdown(extensions=["footnotes", "smarty"])
    declaration = md.convert(text)

    signatories = load_signatories()
    # Notable signatories first; sort is stable so the API order is kept otherwise.
    ordered = sorted(signatories, key=lambda s: not s.get("featured"))
    page = (ROOT / "template.html").read_text()
    page = (
        page.replace("{{title}}", title)
        .replace("{{declaration}}", declaration)
        .replace("{{count}}", str(len(signatories)))
        .replace("{{signatories}}", "\n".join(map(render_signatory, ordered)))
        .replace("{{updated}}", datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"))
    )

    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(ROOT / "static", OUT)
    (OUT / "index.html").write_text(page)
    print(f"Built {OUT / 'index.html'} with {len(signatories)} signatories")


if __name__ == "__main__":
    main()
