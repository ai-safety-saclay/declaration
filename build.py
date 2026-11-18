# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown>=3.6"]
# ///
"""Build the static site in French (/) and English (/en/).

Each page is templates/base.html wrapped around one templates/<page>.html.
Interface text comes from strings.toml; the call and the privacy policy
from DECLARATION[.en].md and CONFIDENTIALITE[.en].md.

Signatories are fetched from $SIGNATORIES_URL when set, otherwise read
from data/signatories.json. They are shared by both languages.
"""

import html
import json
import os
import re
import shutil
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import markdown

ROOT = Path(__file__).parent
OUT = ROOT / "_site"
TEMPLATES = ROOT / "templates"
LANGS = ("fr", "en")
# Absolute address of the site, for canonical links, link previews and the
# sitemap. Override with $SITE_URL (e.g. to test on another host).
SITE_URL = os.environ.get("SITE_URL", "https://encadrer-ia-frontiere.fr/").rstrip("/") + "/"
OG_LOCALES = {"fr": "fr_FR", "en": "en_GB"}

# Output path of each page, per language. Paths are relative to the site
# root so the site works under a sub-path (GitHub Pages project sites).
PATHS = {
    "home": {"fr": "", "en": "en/"},
    "signatories": {"fr": "signataires/", "en": "en/signatories/"},
    "contact": {"fr": "contact/", "en": "en/contact/"},
    "privacy": {"fr": "confidentialite/", "en": "en/privacy/"},
}


def localized(stem: str, lang: str) -> Path:
    """DECLARATION.md for French, DECLARATION.en.md for English."""
    return ROOT / (f"{stem}.md" if lang == "fr" else f"{stem}.{lang}.md")


def read_markdown(path: Path) -> tuple[str, str]:
    """Split a Markdown file into its "# title" line and rendered body."""
    title, text = path.read_text().split("\n", 1)
    md = markdown.Markdown(extensions=["footnotes", "smarty"])
    return html.escape(title.removeprefix("# ").strip()), md.convert(text)


def render(template: str, ctx: dict[str, str]) -> str:
    """Replace each {{key}}; an unknown key is an error, not a silent blank."""
    def sub(m: re.Match) -> str:
        if m.group(1) not in ctx:
            raise KeyError(f"no value for {{{{{m.group(1)}}}}}")
        return ctx[m.group(1)]
    return re.sub(r"\{\{([\w.]+)\}\}", sub, template)


def load_signatories() -> list[dict]:
    url = os.environ.get("SIGNATORIES_URL")
    if url:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.load(resp)
    return json.loads((ROOT / "data" / "signatories.json").read_text())


def render_signatory(s: dict, t: dict[str, str], with_comment: bool = False) -> str:
    e = lambda k: html.escape(s.get(k) or "")
    featured = " featured" if s.get("featured") else ""
    comment = ""
    if with_comment and s.get("comment"):
        quote = f"<blockquote>{t['quote_open']}{e('comment')}{t['quote_close']}</blockquote>"
        # Two copies, toggled by CSS: shown beside the name on wide screens,
        # behind a quote-mark toggle (<details>) on phones.
        comment = (
            f'<div class="comment-wide">{quote}</div>'
            f'<details class="comment-narrow"><summary aria-label="{t["comment"]}">“</summary>{quote}</details>'
        )
    return (
        f'<li class="signatory{featured}">'
        f'<div class="who"><p class="name">{e("firstname")} {e("name")}</p>'
        + '<p class="meta">'
        + (f'<span class="role">{e("role")}</span>' if s.get("role") else "")
        + (f'<span class="institution">{e("institution")}</span>' if s.get("institution") else "")
        + "</p></div>"
        + f"{comment}</li>"
    )


def write_sitemap(now: datetime) -> None:
    """One <url> per page and language, each listing its translations."""
    entries = []
    for paths in PATHS.values():
        alternates = "".join(
            f'\n    <xhtml:link rel="alternate" hreflang="{lg}" href="{SITE_URL + paths[lg]}"/>'
            for lg in LANGS
        )
        for lang in LANGS:
            entries.append(
                f"  <url>\n    <loc>{SITE_URL + paths[lang]}</loc>"
                f"\n    <lastmod>{now:%Y-%m-%d}</lastmod>{alternates}\n  </url>"
            )
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(entries) + "\n</urlset>\n"
    )


def main() -> None:
    strings = tomllib.loads((ROOT / "strings.toml").read_text())
    signatories = load_signatories()
    # Notable signatories first; sort is stable so the API order is kept otherwise.
    ordered = sorted(signatories, key=lambda s: not s.get("featured"))
    now = datetime.now(timezone.utc)
    base = (TEMPLATES / "base.html").read_text()

    shutil.rmtree(OUT, ignore_errors=True)
    shutil.copytree(ROOT / "static", OUT)

    for lang in LANGS:
        title, declaration = read_markdown(localized("DECLARATION", lang))
        policy_title, policy = read_markdown(localized("CONFIDENTIALITE", lang))
        raw = strings[lang]
        list_items = lambda full: "\n".join(render_signatory(s, raw, full) for s in ordered)

        pages = {
            "home": {
                "page_title": title,
                "declaration": declaration,
                "signatories": list_items(False),
                "footer": '<a href="{{href_privacy}}">{{t.privacy}}</a>',
            },
            "signatories": {
                "page_title": f"{raw['signatories']} — {title}",
                "signatories": list_items(True),
            },
            "contact": {"page_title": f"{raw['contact_title']} — {title}"},
            "privacy": {
                "page_title": f"{policy_title} — {title}",
                "policy_title": policy_title,
                "policy": policy,
            },
        }

        for name, page_ctx in pages.items():
            path = PATHS[name][lang]
            url = lambda target=name, lg=lang: SITE_URL + PATHS[target][lg]
            root = "../" * path.count("/")
            href = lambda target, lg=lang: root + PATHS[target][lg] or "./"
            ctx = {
                "lang": lang,
                "root": root,
                "title": title,
                "count": str(len(signatories)),
                "updated": now.strftime(raw["date_format"]),
                "description": html.escape(raw["description"]),
                "url": url(),
                "url_fr": url(name, "fr"),
                "url_en": url(name, "en"),
                "share_image": SITE_URL + "share.png",
                "og_locale": OG_LOCALES[lang],
                "og_locale_alt": OG_LOCALES["en" if lang == "fr" else "fr"],
                "href_fr": href(name, "fr"),
                "href_en": href(name, "en"),
                "current_fr": ' aria-current="true"' if lang == "fr" else "",
                "current_en": ' aria-current="true"' if lang == "en" else "",
                **{f"href_{p}": href(p) for p in PATHS},
                "back_bar": "" if name == "home" else
                    '<nav class="back-bar"><a href="{{href_home}}">{{t.back}}</a></nav>',
                "footer": '<a href="{{href_home}}">{{t.back}}</a>',
                **page_ctx,
            }
            # Strings may themselves hold {{root}}, {{href_*}}…
            ctx |= {f"t.{k}": render(v, ctx) for k, v in raw.items()}
            ctx["main"] = render((TEMPLATES / f"{name}.html").read_text(), ctx)
            ctx["back_bar"] = render(ctx["back_bar"], ctx)
            ctx["footer"] = render(ctx["footer"], ctx)
            out = OUT / path / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render(base, ctx))

    write_sitemap(now)
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n")
    (OUT / "CNAME").write_text(SITE_URL.split("//", 1)[1].split("/", 1)[0] + "\n")

    print(f"Built {OUT} ({', '.join(LANGS)}) with {len(signatories)} signatories")


if __name__ == "__main__":
    main()
