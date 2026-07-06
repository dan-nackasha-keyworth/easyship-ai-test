"""
Builds the Word doc code appendix by reading the actual source files
straight off disk and assembling them into fenced-code-block markdown,
then converting to .docx via pandoc. Guarantees the appendix is a
byte-exact copy of what's really in the repo - never retyped by hand,
so it can't drift from the real code.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
OUT_MD = ROOT / "code_appendix.md"
OUT_DOCX = ROOT / "Example Co. AI - Code Appendix.docx"

PY_FILES = [
    "config.py",
    "pipeline.py",
    "batch_runner.py",
    "dashboard.py",
    "opus_comparison.py",
    "preview_server.py",
    "run_eval.py",
]
JSON_FILES = [
    "data/brand_guidelines.json",
    "data/mock_backend.json",
    "data/help_centre_articles.json",
    "data/success_playbook.json",
    "data/sales_playbook.json",
]

INTRO = """# AI Build - Code Appendix

This appendix contains the full, real source code for the working AI
build - not simulated, not pseudocode. Every file below is copied
directly from the project repository at generation time. Company
configuration in this public version is the fictional "Example Co."
(see `config.py`); the real, company-named submission materials this
build was originally created for are delivered privately and are not
in this repo.

**Repo link:** https://github.com/dan-nackasha-keyworth/ExampleCo-AI-test

**Companion document:** `HOW_THE_AI_WORKS.md` (in the same repository)
gives a plain-English glossary of every pipeline stage and the literal
text of all three prompts, if reading prose first is more useful than
reading code first.

## How to run this

1. Install dependencies: `pip install anthropic python-dotenv`
2. Create a `.env` file in the project root containing
   `ANTHROPIC_API_KEY=sk-ant-...` (never committed - `.gitignore`
   excludes it)
3. Run the full dev-set batch: `python batch_runner.py --split dev`
4. Build the results dashboard from the output it just wrote:
   `python dashboard.py`
5. Open the generated `outputs/run_*.html` file in a browser

Every run is read-only against the sample data and writes to a new,
timestamped output file - nothing is ever mutated in place, so the
batch can be re-run as many times as needed without losing a prior
result.

## Files in this appendix

"""

FOOTER = """
## Sample data (excerpt)

The full test set is 120 messages (100 dev / 20 held-out), each with a
ground-truth category label, entry channel, and edge-case metadata
where relevant. Reproduced here is a short excerpt to show the schema;
the complete file lives at `data/sample_messages.json` in the repo.

```json
{sample}
```
"""


def lang_fence(path):
    return "python" if path.endswith(".py") else "json"


def main():
    parts = [INTRO]
    for f in PY_FILES:
        parts.append(f"- `{f}`\n")
    for f in JSON_FILES:
        parts.append(f"- `{f}`\n")
    parts.append("- `data/sample_messages.json` (excerpt - full file in repo)\n")

    for f in PY_FILES:
        code = (ROOT / f).read_text(encoding="utf-8")
        parts.append(f"\n## `{f}`\n\n```{lang_fence(f)}\n{code}\n```\n")

    for f in JSON_FILES:
        code = (ROOT / f).read_text(encoding="utf-8")
        parts.append(f"\n## `{f}`\n\n```{lang_fence(f)}\n{code}\n```\n")

    with open(ROOT / "data" / "sample_messages.json", encoding="utf-8") as fh:
        messages = json.load(fh)
    sample = json.dumps(messages[:3], indent=2, ensure_ascii=False)
    parts.append(FOOTER.format(sample=sample))

    content = "".join(parts)
    OUT_MD.write_text(content, encoding="utf-8")
    print(f"Wrote {OUT_MD} ({len(content)} chars)")

    subprocess.run(
        ["pandoc", str(OUT_MD), "-o", str(OUT_DOCX), "--standalone"],
        check=True,
    )
    print(f"Wrote {OUT_DOCX}")


if __name__ == "__main__":
    main()
