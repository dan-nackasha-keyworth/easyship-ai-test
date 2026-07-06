# AI Inbound Triage Agent

A working demo build: an agent that classifies inbound customer
messages as **Service**, **Success**, or **Sales**, extracts key
information, scores its own confidence, drafts a response in the
company's voice, and routes to the correct queue. For messages it's
actually unsure about, a separate agentic step decides for itself
which live lookups are worth making before a person sees it. A human
always reviews before anything sends.

This is real, API-tested code, not a simulation. Nothing here is a
mockup of what the pipeline *would* do; every number quoted below comes
from an actual run against the Anthropic API. Configuration is
company-agnostic by design (see `config.py`) - this same pipeline was
built for a specific real company's Head of Client Services hiring
task, but that company's name and materials are deliberately not
included in this public repo (see "Data and security notes" below).
The illustrative company used throughout this repo, "Example Co.", is
fictional.

## Start here

| If you want... | Look at... |
|---|---|
| The architecture diagram | `deck/architecture_diagram.png` |
| An illustrative strategy deck (generic "Example Co." version) | `deck/Example Co. Strategy Deck.pptx` |
| Every prompt, verbatim, plus a plain-English glossary of each pipeline stage | `HOW_THE_AI_WORKS.md` |
| Live project status (what's built, what's outstanding, run history) | `build_status.html` (open in a browser) |
| **The actual results** - real, per-message reasoning, drafts, and confidence scores, no API key needed | `results/reference_run.html` (open in a browser) |
| The messy-mailbox channel test (see below) | `results/success_mailbox_run.html` (open in a browser) |

## Results at a glance

Most recent full run against the 100-message development set:

- **95% classification accuracy** - every miss landed on a message
  deliberately written to be ambiguous, and every one of
  those was independently self-flagged as low confidence
- **Sensitive-topic detection: 7/7 caught, 0 false positives**
  (refunds, GDPR requests, compliance/chargeback disputes - always
  routed to Service, never auto-resolved)
- **Retention-risk detection: 3/3 caught**, including soft language
  that never says "cancel" - plus 2 messages flagged beyond the
  narrow ground-truth definition (a GDPR deletion request, a
  chargeback threat). Each has a defensible business rationale on
  inspection, but it's an honest sign the retention-risk definition
  itself may need tightening or deliberately broadening once real usage
  data exists - not something to paper over
- **Reference-grounded drafting: 59/100 messages matched a real mock
  Help Centre/playbook article**, feeding the draft-quality confidence
  score (59 high / 19 low / 22 n/a for clarification requests) - see
  "Draft-quality confidence" in `HOW_THE_AI_WORKS.md`
- **Cost: $1.36 for 100 messages** (~1.4 cents/message, up from ~1
  cent before reference-grounded drafting added context to the drafting
  call), scaling to roughly $81/month at a representative real inbound
  volume for a company this size
- **Latency: median 7.6s, p95 15.3s** per message end-to-end (up to
  17s for the ~1-in-5 messages that trigger the agentic investigation
  step) - low-risk in production by design: the message would still
  sit in the normal inbox exactly as it does today, and the AI enriches
  it in the background rather than blocking a human from working the
  ticket manually. A slow or failed call just means that ticket's
  enrichment arrives late or not at all, which the pipeline already
  treats as a safe fallback (see error handling in
  `HOW_THE_AI_WORKS.md`)
- A held-out 20-message set exists and is deliberately **not** reported
  here - reserved for a single final validation pass, so these numbers
  can't be the result of unconsciously tuning prompts against the test
  data itself
- **Messy-mailbox channel test: 15/15 (100%) correct** - a separate
  15-message batch modeling a fictional, unstructured "Success mailbox"
  entry channel (assumed, since there's no real dedicated Success form)
  that's deliberately a genuine mix of Service, Success, and Sales
  content. Confirms the model classifies on message content and doesn't
  get biased toward "Success" just because of which inbox a message
  arrived through. See `results/success_mailbox_run.html`

## What's actually in the build

- **Classify + extract** (Haiku 4.5) - one call per message
- **Confidence scoring** - a transparent, rule-based rubric (not an
  LLM-generated percentage)
- **Routing + guardrails** - sensitive topics and retention risk always
  win regardless of confidence; a 4th "Team Lead Triage" queue for
  messages the system is actually unsure about, rather than a forced
  guess; formal close-account/cancel-shipment requests stay
  Support-owned by default, looping in Success only for large accounts;
  ambiguous (contradictory-signal) messages escalate to Team Lead
  Triage rather than defaulting to Success
- **Enterprise AE routing** - a stated shipment volume on a Sales
  message in the top band(s) gets a distinct "Enterprise AE" handling
  path, mirroring the real Sales form's volume field
- **Multi-team loop-in** - a single primary owner is always kept, with
  any other team that has an independent signal (e.g. an
  expansion mention inside a support ticket) looped in rather than
  left blind
- **Reference-grounded, brand-guided drafting** (Sonnet 5) - looks up a
  matching mock Help Centre/playbook article for the queue before
  drafting, reuses its substance rather than inventing an answer, and
  reads brand tone/voice rules fresh on every call including an
  explicit rule set against AI-sounding language
- **Draft-quality confidence** - a second, distinct confidence score
  from routing confidence: was this specific draft grounded in a real
  reference article (verifiable) rather than a fully generative
  attempt, as a rule-based proxy for "is this good enough to send"
- **Agentic investigation** - the only agentic component: for
  low-confidence messages, the model itself decides which of three
  read-only tools (order status, account context, Help Centre search)
  are worth making before a human reviews it

Full detail, including the literal prompt text for every one of these
steps, is in `HOW_THE_AI_WORKS.md`.

## How to run it

```bash
pip install anthropic python-dotenv

# create a .env file in this directory containing:
# ANTHROPIC_API_KEY=sk-ant-...

python batch_runner.py --split dev      # runs the 100-message dev set
python dashboard.py                      # builds a results dashboard from the latest run
```

Open the generated `outputs/run_*.html` file in a browser. Every run
writes to a new, timestamped file - nothing is ever mutated in place,
so it's safe to re-run as many times as you like.

To run a single message or a small subset instead of the full batch:

```bash
python batch_runner.py --ids msg_001 msg_002
```

## Repo structure

```
config.py                 Company-specific configuration (routing model, thresholds, models used)
pipeline.py                The generic pipeline: classify, extract, score, route, draft, investigate
batch_runner.py             CLI batch runner with live progress + cost/accuracy stats
dashboard.py                Generates the results dashboard from a run file
opus_comparison.py          Opus 4.8 vs Haiku 4.5 comparison script
preview_server.py           Restricted local file server for viewing outputs
run_eval.py                 Eval-as-CI regression suite (known-answer test cases, hard assertions)
data/
  sample_messages.json      239 synthetic test messages (100 dev / 20 held-out / 100 fresh-check / 15 messy-mailbox / 4 routing-fix-check), ground-truth labeled
  brand_guidelines.json      Illustrative brand tone/voice/anti-AI-isms rules
  mock_backend.json          Synthetic order/account records for the agentic investigation tools
  help_centre_articles.json  Mock Support/Service knowledge base, keyword-matched for grounded drafting
  success_playbook.json      Mock Customer Success playbook, same retrieval pattern
  sales_playbook.json        Mock Sales playbook, same retrieval pattern
deck/
  build_deck.py                   Script that generates an illustrative strategy deck
  architecture_diagram.png/.html  Architecture diagram
HOW_THE_AI_WORKS.md        Full pipeline glossary + literal text of every real prompt
build_status.html            Live project status tracker
results/
  reference_run.html           The current reference run's dashboard - real output, browsable
                                  with no API key needed
  reference_run.json           The same run's raw per-message data
```

The real strategy deck, build plan, and code appendix built for the
actual hiring task are not included in this public repo - they name
the real company and were delivered directly and privately to the
people who requested them, not published here.

`outputs/` is git-ignored - it's created locally when you run the
batch runner yourself, not shipped pre-populated in this repo, so what
you see when you run it is real and freshly generated. `results/` is
the one deliberate exception: a single committed copy of the current
reference run, so anyone browsing the repo can see real results
without running anything themselves.

## Data and security notes

All 220 test messages are synthetic - no real company or customer
data is used anywhere in this build. `.env` (holding the API key) is
git-ignored and was never committed. Nothing in this pipeline sends a
message, writes to a real CRM/helpdesk, or takes any action beyond
drafting a reply for human review - every run is read-only against the
test data and side-effect-free by construction. This repo intentionally
does not name the real company it was originally built for, or any
real individuals - see `config.py` for the fictional "Example Co."
stand-in used throughout.

---

Dan Nackasha-Keyworth - a reusable demo build, originally created for
a Head of Client Services-style hiring task.
