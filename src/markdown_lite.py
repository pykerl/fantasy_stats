"""A small Markdown -> HTML converter for the report prose.

The `markdown` package is used when installed; this fallback covers the subset
the report and the LLM column actually emit (headings, bold/italic, tables,
lists, blockquotes, rules, paragraphs) so the site never depends on it.
"""
from __future__ import annotations

import html
import re

_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"`([^`]+?)`"), r"<code>\1</code>"),
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r'<a href="\2">\1</a>'),
]


def to_html(text: str) -> str:
    try:
        import markdown  # noqa: PLC0415

        return markdown.markdown(text, extensions=["tables"])
    except ImportError:
        return _convert(text)


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    for pattern, replacement in _INLINE:
        out = pattern.sub(replacement, out)
    return out


def _convert(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("---") and set(stripped) == {"-"}:
            out.append("<hr>")
            index += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue

        if stripped.startswith(">"):
            block = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                block.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote><p>{_inline(' '.join(block))}</p></blockquote>")
            continue

        if stripped.startswith("|"):
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index].strip())
                index += 1
            out.append(_table(block))
            continue

        if re.match(r"^[-*]\s+", stripped):
            items = []
            while index < len(lines) and re.match(r"^[-*]\s+", lines[index].strip()):
                items.append(_inline(re.sub(r"^[-*]\s+", "", lines[index].strip())))
                index += 1
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            continue

        paragraph = []
        while index < len(lines) and lines[index].strip() and not _starts_block(lines[index].strip()):
            paragraph.append(lines[index].strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


def _starts_block(stripped: str) -> bool:
    return (
        stripped.startswith(("#", ">", "|", "- ", "* "))
        or (stripped.startswith("---") and set(stripped) == {"-"})
    )


def _table(block: list[str]) -> str:
    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    if len(block) < 2:
        return ""
    header = cells(block[0])
    body = [cells(row) for row in block[2:]]
    head_html = "".join(f"<th>{_inline(c)}</th>" for c in header)
    body_html = "".join(
        "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in body
    )
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"
