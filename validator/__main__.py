"""CLI: python -m validator <requirements file> <report file>."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from . import __version__
from .llm import LLMClient, LLMConfig, LLMError
from .loader import DocumentError, load_document
from .pipeline import run_review
from .html import markdown_to_html
from .report import render_markdown, started_now


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty", "--tags"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or "нет git"
    except (OSError, subprocess.SubprocessError):
        return "нет git"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m validator",
        description="ИИ-агент статической проверки требований (ISO/IEC/IEEE 29148, IEEE 830).",
    )
    parser.add_argument("input", help="файл требований: .pdf, .docx, .md, .txt")
    parser.add_argument("output", help="куда записать отчёт (.md)")
    parser.add_argument("--model", help="модель вместо указанной в .env")
    parser.add_argument("--no-cache", action="store_true", help="не использовать кэш ответов модели")
    args = parser.parse_args(argv)

    load_dotenv()
    started = started_now()
    t0 = time.monotonic()
    try:
        document = load_document(args.input)
        llm = LLMClient(LLMConfig.from_env(model_override=args.model, use_cache=not args.no_cache))
        print(f"Документ: {document.title} ({len(document.lines)} строк). Модель: {llm.label}")
        review = run_review(document, llm)
    except (DocumentError, LLMError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    meta = {
        "started": started,
        "model": llm.label,
        "step_models": llm.step_models,
        "agent_version": __version__,
        "commit": git_commit(),
        "llm_calls": llm.calls,
        "cache_hits": llm.cache_hits,
        "seconds": time.monotonic() - t0,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(review, meta)
    output.write_text(markdown, encoding="utf-8")
    html_path = output.with_suffix(".html")
    if html_path != output:
        html_path.write_text(markdown_to_html(markdown, f"Отчёт: {document.title}"), encoding="utf-8")

    critical = sum(1 for f in review.findings if f.severity == "critical")
    print(
        f"Готово: требований {len(review.requirements)}, нарушений {len(review.findings)} "
        f"(критичных {critical}). Отчёт: {output} (+ {html_path.name})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
