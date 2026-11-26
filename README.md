# BCC Requirements Validator

An AI agent that performs a **static review of a requirements document before development**. It reads the document, splits it into individual requirements, checks every requirement and the whole set against ISO/IEC/IEEE 29148:2018 (5.2.5, 5.2.6) and IEEE Std 830-1998 (4.3), and writes a report in Russian. Each defect in the report quotes the exact place in the document.

Built for the BCC-HUB x MNU FinTech Hackathon ([task](https://github.com/BCC-HUB/ai-requirements-validator-task)).

## Quick start

```bash
git clone https://github.com/astmirzhan/BCC-Requirements-Validator.git
cd BCC-Requirements-Validator
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then put your key into .env
python -m validator examples/requirements.md reports/report.md
```

The report is written to the path given as the second argument (`reports/report.md` above).

Requirements: Python 3.10+ (tested on 3.12), internet access for a cloud model.

## Getting an LLM key

The default model is **Google Gemini** on the free tier, no card needed:

1. Open [aistudio.google.com](https://aistudio.google.com/) and sign in with a Google account.
2. Click **Get API key** → **Create API key**.
3. Paste it into `.env`: `GEMINI_API_KEY=...`

`.env` is listed in `.gitignore`, so the key is never committed. `.env.example` holds the same settings without a key.

## Model

| Setting | Value |
| :--- | :--- |
| Provider | Google Gemini API (free tier) |
| Main model | `gemini-3.8-flash` (`GEMINI_MODEL`) |
| Fallbacks | `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.5-flash-lite` (`GEMINI_FALLBACK_MODELS`) |
| Temperature | 0 |

The free tier has small daily quotas and the models are sometimes overloaded (HTTP 503). The agent then retries and moves to the next fallback model on its own. The report passport records which model handled each step.

A run makes **4 model calls**, and answers are cached in `.cache/llm/`, so running again on the same document costs nothing. Use `--no-cache` to force fresh calls and `--model <name>` to override the model for one run.

### Other providers and a local model

Any OpenAI-compatible API works through `.env`:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5-mini
```

For a bank, internal requirements often may not leave the company network. The same agent runs on a **local model through [Ollama](https://ollama.com/)**, and the document never leaves the computer:

```bash
ollama pull qwen2.5:14b
# .env
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:14b
```

## Usage

```
python -m validator <requirements file> <report file> [--model NAME] [--no-cache]
```

Input formats: **PDF** (text layer), **DOCX**, **Markdown**, **TXT**.

## How it works

```
document ─► loader ─► numbered lines
                         │
     ┌───────────────────┼─────────────────────────────┐
     ▼                   ▼                             ▼
 rules (no LLM)    LLM 1: slicing             (rule findings are passed
 vague words,      requirements + quotes       to the model as "already
 empty sections,         │                     found", so it looks for
 units, HTTP,            ▼                     other defects)
 statuses …        LLM 2: each requirement
                   9 criteria, ISO 29148 5.2.5
                         │
                         ▼
                   LLM 3: conflicts
                   acceptance cases replayed against rules,
                   pairs of rules on event sequences
                         │
                         ▼
                   LLM 4: whole set
                   ISO 29148 5.2.6 + IEEE 830 4.3
                         │
                         ▼
        quote verification + merging duplicates ─► report.md + report.html
```

1. **Loader** (`validator/loader.py`) turns PDF, DOCX or Markdown into numbered lines. Every reference in the report is a line number (and a page for PDF).
2. **Rules** (`validator/rules.py`) are deterministic checks that never hallucinate: vague wording («быстро», «в течение», «и т. п.»), empty sections and fields, placeholders, unfilled checklists and approval tables, mixed currency units, GET used for write operations, response codes used but not described, Content-Type without a body, missing document version, missing rollback plan.
3. **Slicing** (LLM call 1) extracts every requirement with its document id (or an assigned `R-xx`), section, type and an exact quote.
4. **Per-requirement check** (LLM call 2) scores each requirement on the nine ISO 29148 5.2.5 characteristics: necessary, appropriate, unambiguous, complete, singular, feasible, verifiable, correct, conforming.
5. **Conflict search** (LLM call 3) replays every acceptance case through the rules, tests pairs of rules on event sequences and cross-checks API fields, units and status codes. This is how contradictions such as "at most one push a month" versus "push regardless of history" are caught, each with the scenario that breaks them.
6. **Whole-set check** (LLM call 4) covers completeness, consistency, feasibility, comprehensibility, validatability (ISO 29148 5.2.6) and ranking, modifiability, traceability (IEEE 830 4.3).
7. **Quote verification** (`validator/quotes.py`): every quote the model returns is looked up in the document. Findings whose quote is not there are dropped from the summary and listed in an appendix, so the report contains no claims without a place in the text. Findings about the same defect (same lines, or lines the texts name, and similar wording) are merged into one.
8. **Report** (`validator/report.py`) follows section 6 of the task. Next to the Markdown file the agent writes an HTML version (`report.html`) with severity colors and clickable defect ids (`validator/html.py`).

## Report contents

1. Run passport: document, SHA-256, date, model per step, agent version and commit.
2. How the agent sliced the requirements: report id, document id, section, short wording, quote with line.
3. Check of each requirement: a criteria matrix plus every defect with quote, problem and recommendation.
4. Check of the whole set.
5. Automatic checks without a model.
6. Summary: number of requirements, number of violations by severity, list of critical defects.
7. Verdict: whether development and testing can start, and what to fix first.

Severity levels: 🔴 critical (cannot be implemented or tested unambiguously), 🟠 major, 🟡 minor. If any critical defect is found, the verdict is "no" regardless of the model's opinion.

## Project layout

```
validator/
  __main__.py   CLI entry point
  loader.py     PDF / DOCX / Markdown reading
  llm.py        provider-agnostic client: JSON, retries, fallbacks, cache, call log
  criteria.py   ISO 29148 / IEEE 830 criteria
  prompts.py    prompts for the four LLM steps
  rules.py      deterministic checks
  quotes.py     quote verification
  pipeline.py   review pipeline
  report.py     Markdown report
  html.py       HTML version of the report
examples/requirements.md   document from the organizers
examples/requirements.pdf  the same document as PDF
reports/report.md, .html   report produced by this agent
```

Every model call is logged to `logs/llm_calls.jsonl` (prompt, answer, model, time) for auditing. The folder is not committed.
