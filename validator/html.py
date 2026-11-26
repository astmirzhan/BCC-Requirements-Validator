"""Standalone HTML version of the Markdown report (no extra dependencies).

Handles exactly the Markdown subset that report.py produces: headings, tables, blockquotes,
bullet and numbered lists, bold, italics, inline code, horizontal rules and paragraphs.
"""

from __future__ import annotations

import html
import re

CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#e5e5ea;--soft:#f5f5f7;
--crit:#d70015;--crit-bg:#fff0f1;--major:#c35a00;--major-bg:#fff6eb;--minor:#8a6d00;--minor-bg:#fffbe6}
@media (prefers-color-scheme:dark){:root{--bg:#111113;--fg:#f2f2f7;--muted:#a1a1a6;--line:#2c2c2e;
--soft:#1c1c1e;--crit:#ff6961;--crit-bg:#2a1214;--major:#ffb340;--major-bg:#2a1f0f;--minor:#ffd60a;--minor-bg:#26230d}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:28px;line-height:1.2;margin:0 0 8px;text-wrap:balance}
h2{font-size:21px;margin:48px 0 12px;padding-top:16px;border-top:1px solid var(--line)}
h3{font-size:16px;margin:28px 0 8px}
p{margin:8px 0}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13.5px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600}
td.c{text-align:center}
blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid var(--line);background:var(--soft);
border-radius:0 6px 6px 0;color:var(--fg)}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--soft);padding:1px 4px;border-radius:4px}
ul,ol{padding-left:22px;margin:8px 0}
li{margin:3px 0}
hr{border:0;border-top:1px solid var(--line);margin:32px 0 12px}
em{color:var(--muted)}
.finding{border:1px solid var(--line);border-left-width:4px;border-radius:8px;padding:10px 14px;margin:12px 0}
.finding.critical{border-left-color:var(--crit);background:var(--crit-bg)}
.finding.major{border-left-color:var(--major);background:var(--major-bg)}
.finding.minor{border-left-color:var(--minor);background:var(--minor-bg)}
.finding>p:first-child{margin-top:0;font-size:13.5px}
.finding blockquote{background:var(--bg)}
nav{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);z-index:1}
nav div{max-width:1080px;margin:0 auto;padding:10px 24px;display:flex;gap:16px;flex-wrap:wrap;font-size:13px}
nav a{color:var(--muted);text-decoration:none}
nav a:hover{color:var(--fg)}
"""

SEVERITY_CLASS = {"🔴": "critical", "🟠": "major", "🟡": "minor"}


def markdown_to_html(markdown: str, title: str) -> str:
    lines = markdown.split("\n")
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    i = 0
    in_finding = False

    def close_finding():
        nonlocal in_finding
        if in_finding:
            out.append("</div>")
            in_finding = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_finding()
            level = len(heading.group(1))
            text = heading.group(2)
            anchor = f"s{len(toc) + 1}"
            if level == 2:
                toc.append((anchor, re.sub(r"^\d+\.\s*", "", text)))
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue

        if stripped == "---":
            close_finding()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith("|"):
            close_finding()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            out.append(_table(block))
            continue

        if stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(block))}</blockquote>")
            continue

        if re.match(r"^(-|\d+\.)\s+", stripped):
            ordered = bool(re.match(r"^\d+\.", stripped))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and re.match(r"^(-|\d+\.)\s+", lines[i].strip()):
                items.append(re.sub(r"^(-|\d+\.)\s+", "", lines[i].strip()))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(it)}</li>" for it in items) + f"</{tag}>")
            continue

        # A finding header starts with **D-xx** · 🔴 …
        if re.match(r"^\*\*D-\d+\*\*", stripped):
            close_finding()
            severity = next((cls for icon, cls in SEVERITY_CLASS.items() if icon in stripped), "minor")
            finding_id = re.match(r"^\*\*(D-\d+)\*\*", stripped).group(1)
            out.append(f'<div class="finding {severity}" id="{finding_id}">')
            in_finding = True
            out.append(f"<p>{_inline(stripped)}</p>")
            i += 1
            continue

        out.append(f"<p>{_inline(stripped)}</p>")
        i += 1

    close_finding()
    nav = "".join(f'<a href="#{a}">{html.escape(t)}</a>' for a, t in toc)
    return (
        "<!doctype html>\n<html lang=\"ru\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"<nav><div>{nav}</div></nav><main>{''.join(out)}</main></body></html>\n"
    )


def _table(rows: list[str]) -> str:
    cells = [_split_row(r) for r in rows]
    header, body = cells[0], [c for c in cells[1:] if not all(re.fullmatch(r":?-+:?", x) for x in c)]
    aligns = []
    if len(cells) > 1 and all(re.fullmatch(r":?-+:?", x) for x in cells[1]):
        aligns = ["c" if x.startswith(":") and x.endswith(":") else "" for x in cells[1]]
    head = "".join(f"<th>{_inline(h)}</th>" for h in header)
    rows_html = []
    for row in body:
        tds = []
        for n, cell in enumerate(row):
            cls = f' class="{aligns[n]}"' if n < len(aligns) and aligns[n] else ""
            tds.append(f"<td{cls}>{_inline(cell)}</td>")
        rows_html.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"


def _split_row(row: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", row.strip().strip("|"))
    return [p.strip().replace("\\|", "|") for p in parts]


def _inline(text: str) -> str:
    text = html.escape(text.replace("\\|", "|"), quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![*\w])\*([^*]+)\*(?![*\w])", r"<em>\1</em>", text)
    return _link_findings(text)


def _link_findings(text: str) -> str:
    return re.sub(r"(?<![\w\"#-])(D-\d{2,})(?![\w-])", r'<a href="#\1">\1</a>', text)
