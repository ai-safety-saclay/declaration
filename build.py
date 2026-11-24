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
e-mail from the Grist form ($GRIST_API_KEY, $FORM_DOC_ID). Signatures under
the same name are merged; merged names are listed in the GitHub Actions job
summary. Values in a local
.env file are used when the environment does not set them.
"""

import html
import json
import os
import re
import shutil
import tomllib
import unicodedata
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
    "teacher_researcher": ["Enseignant·e-chercheur·euse", "Teacher-researcher", "Faculty member"],
    "teacher": ["Enseignant·e", "Teacher", "Enseignant·e (hors recherche)", "Teacher (non-research)"],
    "engineer": ["Ingénieur·e", "Engineer"],
    "head_of_institution": ["Chef·fe d'établissement", "Directeur·ice d'établissement", "Head of institution"],
    "other": ["Autre", "Other"],
}
normalise = lambda value: " ".join(value.replace("’", "'").casefold().split())
ROLE_BY_VALUE = {normalise(v): key for key, values in ROLES.items() for v in values}
# Order of the roles in the home page preview; roles not listed come last.
PREVIEW_ORDER = ["head_of_institution", "researcher", "teacher_researcher", "teacher", "phd", "student", "engineer"]


def preview_rank(s: dict) -> tuple[bool, int]:
    """Featured signatories first, then by role in PREVIEW_ORDER."""
    key = ROLE_BY_VALUE.get(normalise(s.get("role") or ""))
    rank = PREVIEW_ORDER.index(key) if key in PREVIEW_ORDER else len(PREVIEW_ORDER)
    return (not s.get("featured"), rank)


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


def name_key(s: dict) -> str:
    """First and last name, ignoring case, accents and spacing."""
    text = unicodedata.normalize("NFKD", f"{s['firstname']} {s['name']}")
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).casefold().split())


def merge_by_name(signatories: list[dict]) -> tuple[list[dict], list[tuple[dict, int]]]:
    """One entry per name: the first signature, with the role, institution and
    comment of a later one filling any of those it lacks (e.g. institution from
    an academic address, comment given with a personal one). Also returns each
    merged entry with its number of signatures."""
    merged: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for s in signatories:
        key = name_key(s)
        if key not in merged:
            merged[key], counts[key] = dict(s), 1
            continue
        counts[key] += 1
        kept = merged[key]
        for field in ("role", "institution", "comment"):
            kept[field] = kept.get(field) or s.get(field) or ""
        kept["featured"] = kept.get("featured") or s.get("featured")
    duplicates = [(merged[k], n) for k, n in counts.items() if n > 1]
    return list(merged.values()), duplicates


def report_duplicates(duplicates: list[tuple[dict, int]]) -> None:
    """List merged names in the GitHub Actions job summary (and the log).
    The repository is public: names only, never e-mail addresses."""
    if not duplicates:
        return
    rows = [
        f"| {s['firstname']} {s['name']} | {n} | {s.get('institution') or '—'} | {'oui' if s.get('comment') else 'non'} |"
        for s, n in duplicates
    ]
    table = "\n".join([
        f"### Signataires fusionnés ({len(duplicates)})",
        "",
        "Plusieurs signatures au même nom, affichées une seule fois.",
        "",
        "| Nom | Signatures | Établissement retenu | Commentaire |",
        "|---|---:|---|---|",
        *rows,
    ])
    print(table)
    if (summary := os.environ.get("GITHUB_STEP_SUMMARY")):
        with open(summary, "a") as f:
            f.write(table + "\n")


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
    signatories, duplicates = merge_by_name(signatories)
    report_duplicates(duplicates)
    return signatories


def render_signatory(s: dict, t: dict[str, str], with_comment: bool = False,
                     inst_ids: dict[str, str] | None = None) -> str:
    role = role_label(s.get("role") or "", t)
    e = lambda k: html.escape((role if k == "role" else s.get(k)) or "")
    featured = " featured" if s.get("featured") else ""
    comment = ""
    if with_comment and s.get("comment"):
        # Below the name on phones, beside it on wide screens (style.css).
        comment = f"<blockquote>{t['quote_open']}{e('comment')}{t['quote_close']}</blockquote>"
    inst = (inst_ids or {}).get(s.get("institution") or "")
    return (
        f'<li class="signatory{featured}"' + (f' data-inst="{inst}"' if inst else "") + ">"
        f'<div class="who"><p class="name">{e("firstname")} {e("name")}</p>'
        + '<p class="meta">'
        + (f'<span class="role">{e("role")}</span>' if role else "")
        + (f'<span class="institution">{e("institution")}</span>' if s.get("institution") else "")
        + "</p></div>"
        + f"{comment}</li>"
    )


def institution_filter(signatories: list[dict], t: dict[str, str]) -> tuple[dict[str, str], str]:
    """Id of each institution, and the radio buttons that dim everyone else.

    One CSS rule per institution, generated here, uses :has() to dim the
    other signatories while its radio button is checked; a small script only
    resets to "all" on a click outside or Escape. Institutions with the most
    signatories come first.
    """
    counts: dict[str, int] = {}
    for s in signatories:
        if (name := s.get("institution")):
            counts[name] = counts.get(name, 0) + 1
    names = sorted(counts, key=lambda n: (-counts[n], n))
    ids = {name: f"inst-{i}" for i, name in enumerate(names, 1)}
    if not names:
        return ids, ""
    button = lambda id, label, n, checked="": (
        f'<input type="radio" name="inst" id="{id}"{checked}>'
        f'<label for="{id}">{label} <span class="n">{n}</span></label>'
    )
    buttons = [button("inst-all", t["filter_all"], len(signatories), " checked")]
    buttons += [button(ids[n], html.escape(n), counts[n]) for n in names]
    rules = "\n".join(
        f'.signatories-page:has(#{id}:checked) .signatory:not([data-inst="{id}"]) {{ opacity: 0.2; }}'
        for id in ids.values()
    )
    return ids, (
        f'<div class="inst-filter" role="radiogroup" aria-label="{t["filter_label"]}">'
        + "".join(buttons) + f"</div>\n<style>\n{rules}\n</style>\n"
        # Clicking outside the buttons, or Escape, goes back to "all".
        + "<script>\n"
        "  const reset = () => { document.getElementById('inst-all').checked = true; };\n"
        "  document.addEventListener('click', e => { if (!e.target.closest('.inst-filter')) reset(); });\n"
        "  document.addEventListener('keydown', e => { if (e.key === 'Escape') reset(); });\n"
        "</script>"
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
        inst_ids, inst_filter = institution_filter(signatories, raw)
        list_items = lambda full, rows=ordered: "\n".join(render_signatory(s, raw, full, inst_ids) for s in rows)

        pages = {
            "home": {
                "page_title": title,
                "declaration": declaration,
                "signatories": list_items(False, sorted(signatories, key=preview_rank)),
                "footer": '<a href="{{href_privacy}}">{{t.privacy}}</a> · <a href="{{href_legal}}">{{t.legal}}</a>',
            },
            "signatories": {
                "page_title": f"{raw['signatories']} — {title}",
                "signatories": list_items(True),
                "inst_filter": inst_filter,
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
