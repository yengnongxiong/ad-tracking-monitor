"""Versioned prompt files: evals/message_match/prompts/v1.md, v2.md, ... (PRD §15).

A prompt file is the system prompt, then a line `<!-- user -->`, then the user message
template. The template uses $placeholders (string.Template, so JSON examples in the prompt
need no escaping). Page and ad text are untrusted, so values are HTML-escaped before they go
inside the template's <ad> and <page> tags: a page can't close the tag and add instructions.
"""

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template

from tagmonitor.checks.message_match import AdCopy, PageText

# server/src/tagmonitor/llm/prompts.py -> the repo root (/app in the Docker image).
PROMPTS_DIR = Path(__file__).resolve().parents[4] / "evals" / "message_match" / "prompts"
USER_MARKER = "<!-- user -->"
_VERSION = re.compile(r"^v\d+[a-z0-9-]*$")
PLACEHOLDERS = frozenset(
    {
        "ad_headline",
        "ad_primary_text",
        "ad_cta",
        "page_title",
        "page_meta_description",
        "page_h1",
        "page_above_fold_text",
        "page_buttons",
    }
)


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class Prompt:
    version: str
    system: str
    user_template: str
    # Part of the cache key, so editing a prompt file never serves answers to the old text.
    sha256: str

    def render_user(self, ad: AdCopy, page: PageText) -> str:
        values = {
            "ad_headline": ad.headline,
            "ad_primary_text": ad.primary_text,
            "ad_cta": ad.cta,
            "page_title": page.title,
            "page_meta_description": page.meta_description,
            "page_h1": "\n".join(page.h1),
            "page_above_fold_text": page.above_fold_text,
            "page_buttons": "\n".join(page.button_texts),
        }
        return Template(self.user_template).substitute(
            {key: _clean(value) for key, value in values.items()}
        )


def _clean(value: str | None) -> str:
    text = (value or "").strip()
    return html.escape(text, quote=False) if text else "(none)"


def load_prompt(version: str, prompts_dir: Path = PROMPTS_DIR) -> Prompt:
    if not _VERSION.match(version):
        raise PromptError(f"prompt versions look like v1, v2, ...; got {version!r}")
    path = prompts_dir / f"{version}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PromptError(f"no prompt file {path}") from None
    if raw.count(USER_MARKER) != 1:
        raise PromptError(f"{path} needs exactly one {USER_MARKER} line")
    system, user = (part.strip() for part in raw.split(USER_MARKER))
    used = {
        match.group("named") or match.group("braced")
        for match in Template.pattern.finditer(user)
        if match.group("named") or match.group("braced")
    }
    if unknown := used - PLACEHOLDERS:
        raise PromptError(f"{path} uses unknown placeholders: {', '.join(sorted(unknown))}")
    return Prompt(
        version=version,
        system=system,
        user_template=user,
        sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )


def available_versions(prompts_dir: Path = PROMPTS_DIR) -> list[str]:
    versions = [p.stem for p in prompts_dir.glob("v*.md") if _VERSION.match(p.stem)]
    return sorted(versions, key=lambda v: (int(re.sub(r"\D.*", "", v[1:]) or 0), v))
