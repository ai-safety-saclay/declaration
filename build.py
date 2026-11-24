# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown>=3.6"]
# ///
"""Build the static site in French (/) and English (/en/).

Each page is templates/base.html wrapped around one templates/<page>.html.
Interface text comes from strings.toml; the call, the privacy policy and
the legal notice from DECLARATION[.en].md, CONFIDENTIALITE[.en].md and
MENTIONS-LEGALES[.en].md.

Signatories are the Proca actions served at $SIGNATORIES_URL (read with
$SIGNATORIES_TOKEN) when set, otherwise the sample data/signatories.json;
rejected signatures are left out. Their institution is looked up from the
e-mail domain in data/institutions.toml, and public comments are joined by
e-mail from the Grist form ($GRIST_API_KEY, $FORM_DOC_ID). Values in a local
.env file are used when the environment does not set them.
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
    "legal": {"fr": "mentions-legales/", "en": "en/legal-notice/"},
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


def load_dotenv() -> None:
    """Read KEY=value lines from .env, without overriding the environment."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def fetch_json(url: str, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "encadrer-ia-frontiere", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def tidy_name(name: str) -> str:
    """Capitalise a name typed all in upper or lower case: PERONNET → Peronnet."""
    name = " ".join(name.split())
    if name.isupper() or name.islower():
        name = re.sub(r"[^\W\d_]+", lambda m: m[0].capitalize(), name.lower())
    return name


def institution_for(email: str, institutions: dict[str, str]) -> str:
    """Institution of the e-mail domain or of its closest listed parent domain."""
    labels = email.rpartition("@")[2].lower().split(".")
    for i in range(len(labels) - 1):
        if (name := institutions.get(".".join(labels[i:]))):
            return name
    return ""


# Values the Proca widget may send for each role: the labels of its French
# and English role lists, and the ids of its former built-in list. Each role
# is shown with the label role_<key> of the page's language in strings.toml.
ROLES = {
    "student": ["Étudiant·e", "Student"],
    "phd": ["Doctorant·e", "PhD student", "PhD"],
    "researcher": ["Chercheur·euse", "Researcher"],
    "teacher": ["Enseignant·e (hors recherche)", "Teacher (non-research)"],
    "engineer": ["Ingénieur·e", "Engineer"],
    "head_of_institution": ["Chef·fe d'établissement", "Head of institution"],
    "other": ["Autre", "Other"],
}
normalise = lambda value: " ".join(value.replace("’", "'").casefold().split())
ROLE_BY_VALUE = {normalise(v): key for key, values in ROLES.items() for v in values}


def role_label(value: str, t: dict[str, str]) -> str:
    """Label of a role in the page's language; an unknown value is shown as sent."""
    key = ROLE_BY_VALUE.get(normalise(value))
    return t[f"role_{key}"] if key else value.strip()


def fetch_comments() -> dict[str, str]:
    """Public comments from the Grist form, by lower-cased e-mail; the latest wins."""
    key, doc = os.environ.get("GRIST_API_KEY"), os.environ.get("FORM_DOC_ID")
    if not (key and doc):
        return {}
    url = f"https://docs.getgrist.com/api/docs/{doc}/tables/Commentaires/records"
    comments = {}
    for record in fetch_json(url, {"Authorization": f"Bearer {key}"})["records"]:
        fields = record["fields"]
        email = (fields.get("Email") or "").strip().lower()
        comment = str(fields.get("Commentaire") or "").strip()
        if email and comment:
            comments[email] = comment
    return comments


def fetch_signatures(url: str, token: str) -> list[dict]:
    """Every row of the signatures endpoint, one page of 1000 at a time."""
    rows, page = [], 0
    while True:
        batch = fetch_json(f"{url}?page={page}&size=1000", {"x-api-token": token})
        rows += batch["rows"]
        if not batch["rows"] or len(rows) >= batch["total"]:
            return rows
        page += 1


def load_signatories() -> list[dict]:
    url, token = os.environ.get("SIGNATORIES_URL"), os.environ.get("SIGNATORIES_TOKEN")
    if not (url and token):
        return json.loads((ROOT / "data" / "signatories.json").read_text())
    institutions = tomllib.loads((ROOT / "data" / "institutions.toml").read_text())
    comments = fetch_comments()
    signatories, seen = [], set()
    for row in fetch_signatures(url, token):
        data = row["data"]
        action, contact = data["action"], data["contact"]
        email = (contact.get("email") or "").strip().lower()
        # Skip rejected and test signatures, and repeated signatures from the same address.
        if row.get("status") == "rejected" or action.get("testing") or not email or email in seen:
            continue
        seen.add(email)
        signatories.append({
            "firstname": tidy_name(contact.get("firstName") or ""),
            "name": tidy_name(contact.get("lastName") or ""),
            "role": (action.get("customFields") or {}).get("role") or "",
            "institution": institution_for(email, institutions),
            "comment": comments.get(email, ""),
            "featured": False,
        })
    return signatories


def render_signatory(s: dict, t: dict[str, str], with_comment: bool = False) -> str:
    role = role_label(s.get("role") or "", t)
    e = lambda k: html.escape((role if k == "role" else s.get(k)) or "")
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
        + (f'<span class="role">{e("role")}</span>' if role else "")
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
    load_dotenv()
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
        legal_title, legal = read_markdown(localized("MENTIONS-LEGALES", lang))
        raw = strings[lang]
        list_items = lambda full: "\n".join(render_signatory(s, raw, full) for s in ordered)

        pages = {
            "home": {
                "page_title": title,
                "declaration": declaration,
                "signatories": list_items(False),
                "footer": '<a href="{{href_privacy}}">{{t.privacy}}</a> · <a href="{{href_legal}}">{{t.legal}}</a>',
            },
            "signatories": {
                "page_title": f"{raw['signatories']} — {title}",
                "signatories": list_items(True),
            },
            "contact": {"page_title": f"{raw['contact_title']} — {title}"},
            "privacy": {
                "template": "policy",
                "page_title": f"{policy_title} — {title}",
                "doc_title": policy_title,
                "doc": policy,
            },
            "legal": {
                "template": "policy",
                "page_title": f"{legal_title} — {title}",
                "doc_title": legal_title,
                "doc": legal,
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
            template = page_ctx.get("template", name)
            ctx["main"] = render((TEMPLATES / f"{template}.html").read_text(), ctx)
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
