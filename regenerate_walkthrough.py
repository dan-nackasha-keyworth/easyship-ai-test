"""
Regenerates a small custom dashboard for specific message IDs, optionally
overriding the company name at runtime (via --company-name) rather than
editing config.py - lets you produce a differently-branded version of the
demo dashboard for private use without ever writing a real company name
into any tracked file. Pass an --out path that's already gitignored if
the override name is sensitive.

Usage:
    python regenerate_walkthrough.py --company-name "Acme Corp" --out results/acme_walkthrough_run
"""

import argparse
import json
import subprocess
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import pipeline
from config import CONFIG as BASE_CONFIG
from batch_runner import run_batch, compute_stats

DEFAULT_IDS = ["msg_002", "msg_042", "msg_083", "msg_120", "msg_118"]


def main():
    parser = argparse.ArgumentParser(description="Regenerate a custom dashboard, optionally with a company-name override.")
    parser.add_argument("--ids", nargs="+", default=DEFAULT_IDS, help="Message IDs to run.")
    parser.add_argument("--company-name", default=None, help="Override CONFIG['company_name'] for this run only.")
    parser.add_argument("--out", default="results/custom_walkthrough_run", help="Output path (without extension).")
    args = parser.parse_args()

    config = dict(BASE_CONFIG)
    if args.company_name:
        old_name = BASE_CONFIG["company_name"]
        config["company_name"] = args.company_name
        # retention_risk_signals includes a "leaving <company>" phrase - keep it in sync
        config["retention_risk_signals"] = [
            s.replace(old_name.lower(), args.company_name.lower())
            if old_name.lower() in s else s
            for s in BASE_CONFIG["retention_risk_signals"]
        ]
        # brand_guidelines.json has its own hardcoded sign-off/brand-name-usage
        # strings, read fresh at draft time, independent of config.py - patch
        # the loader in-memory so drafts sign off with the override name too.
        import json as _json
        original_load = pipeline.load_brand_guidelines

        def patched_load_brand_guidelines(_orig=original_load, _old=old_name, _new=args.company_name):
            brand = _orig()
            text = _json.dumps(brand).replace(_old, _new)
            return _json.loads(text)

        pipeline.load_brand_guidelines = patched_load_brand_guidelines

    load_dotenv()
    client = anthropic.Anthropic(max_retries=3, timeout=60.0)

    base_dir = Path(__file__).parent
    with open(base_dir / "data" / "sample_messages.json", encoding="utf-8") as f:
        all_messages = json.load(f)
    by_id = {m["id"]: m for m in all_messages}
    messages = [by_id[i] for i in args.ids]

    results = run_batch(messages, client, config)

    stats = compute_stats(results, config["categories"])
    out_json = base_dir / f"{args.out}.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"stats": stats, "results": results}, f, indent=2)
    print(f"\nWrote {out_json}")

    subprocess.run(["python", str(base_dir / "dashboard.py"), str(out_json)], check=True)


if __name__ == "__main__":
    main()
