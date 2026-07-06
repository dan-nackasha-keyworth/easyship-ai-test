# Pipeline & Prompts Reference

This document exists so nothing in the build is a black box. It defines
every stage in plain English, then reproduces the *actual* text of
every real prompt in the system - not a summary or a paraphrase. If a
sentence in here doesn't match `pipeline.py`, the code is the source of
truth and this file is stale and needs updating.

## How many prompts define this system?

Exactly three - the real system prompts sent to the Claude API at
runtime, embedded in `pipeline.py`. These are what the deployed
pipeline actually says to the model on every message, reproduced in
full below.

The build plan document (kept private, not in this public repo, since
it names the real company this was built for) is separate from this:
it's the design and decision record for how the build was put together
- rationale, open items, what's next - not something the running
pipeline ever reads or sends to the model. When asked "what does the
AI actually see," the answer is the three prompts below, full stop.

---

## Pipeline stage glossary

Each stage below is a distinct step in a fixed, code-orchestrated
sequence (a **workflow**, not an agent deciding its own steps) - see
Architecture in the build plan for why that distinction matters. The
one exception, the agentic investigation step, is marked as such.

| Stage | What it means, concretely |
|---|---|
| **Classify + Extract** | One Haiku 4.5 API call. Reads the raw message text and returns a single JSON object: which of Service/Success/Sales it best fits, any other categories that are also plausible, whether it's actually contradictory, the order/account reference if any, a short issue-type label, sentiment, urgency, and several boolean/array flags (expansion intent, retention-risk language, sensitive-topic matches, matched keywords). Ground truth is never shown to the model - only the message text and generic instructions. |
| **Confidence scoring** | Not an LLM call - pure Python arithmetic over the extraction output (`score_confidence` in `pipeline.py`). An additive/subtractive rubric (see table below) produces a 0-100 score and a high/medium/low band. Deliberately rule-based rather than asking the model "how confident are you 0-100" - every point is independently checkable, not an opaque number the model made up. |
| **Routing / guardrails** | Pure Python (`determine_queue`). Decides the *queue* (who owns the message) from the extraction + confidence, applying overrides in a fixed priority order (see table below). Includes a deterministic account-size lookup (`is_large_account`) and a regex-based formal-request check (`is_formal_close_cancel`) - both plain Python, no extra API call. |
| **Multi-team loop-in** | Part of `determine_queue`. When a second team has an independent signal (not just uncertainty about the *same* decision), that team is added to a `loop_in` list rather than taking ownership - a single primary owner is always kept. |
| **Enterprise AE routing** | Part of `determine_queue`. A Sales-category message with a stated shipment volume in the top band(s) (config's `enterprise_ae_volume_bands`) gets a `sales_handling_path` of "Enterprise AE" instead of "Standard Sales" - a handling-path distinction within Sales, not a 5th top-level queue. |
| **Health/expansion flag** | Pure Python (`health_expansion_flag`). A lightweight, explicitly-caveated "this message mentions growth/expansion" note, shown whenever Success has visibility (owner or looped in). Not a verified account-health score. |
| **Reference retrieval** | Pure Python (`find_matching_article`), no API call. Before drafting, looks up the queue's mock Help Centre/playbook content (`data/help_centre_articles.json`, `success_playbook.json`, `sales_playbook.json`) by keyword overlap against the extraction's `matched_keywords`/`issue_type`. Whether a match was found feeds both the draft prompt and the draft-quality confidence score below. |
| **Drafting** | One Sonnet 5 API call (`draft_response`). Writes a short reply for human review, grounded in the matched reference article if one was found, reading brand guidelines fresh from a JSON file on every call. If it's Service with a missing reference, the draft is a clarification request instead of a guess at resolution. If the queue is Team Lead Triage, the draft says so explicitly rather than presenting a guess as a decision. |
| **Draft-quality confidence** | Not an LLM call - pure Python (`score_draft_confidence`), distinct from the routing confidence score above. Answers "is this specific draft likely good enough to send," not "did this land in the right queue." See the dedicated section below. |
| **Agentic investigation** (the only agentic step) | One-to-four Sonnet 5 API calls in a tool-use loop (`investigate_uncertain_message`), triggered only for low-confidence messages. Unlike every step above, the model itself decides which of three read-only tools (if any) to call - two account/order lookups plus a Help Centre search - based on what's actually in the message, not a prescribed sequence. Output is advisory text for the human reviewer; it can never send, modify, or action anything. |

### Confidence rubric (exact signals, from `score_confidence`)

| Signal | Effect |
|---|---|
| Account/order reference present | +35 |
| Single fitting category, no hedging | +35 |
| Category-specific terminology matched | +15 |
| Sentiment/urgency stated unambiguously (not "mixed") | +15 |
| Contradictory category signals | -40 |
| No reference where the category normally expects one | -30 |
| Message very short/generic (<8 words) | -20 |
| Multiple categories plausible (no outright contradiction) | -15 |

Clamped to 0-100. Bands: high >= 80, medium >= 50, low < 50 (config).

### Routing priority order (exact, from `determine_queue`)

1. Sensitive topic present -> queue = Service, unconditionally (never downgraded by low confidence).
2. Else retention-risk language **and** a formal close-account/cancel-shipment request (regex match, see below) -> queue = Service (Support keeps ownership of routine account-lifecycle requests); Success is looped in only if the account is large.
3. Else retention-risk language (softer language, not a formal request) -> queue = Success, unconditionally.
4. Else contradictory signals -> queue = **Team Lead Triage**, not Success (see "Why not Success by default" below).
5. Else confidence score <= Team Lead Triage floor (20/100) -> queue = Team Lead Triage.
6. Else -> queue = the model's predicted category.

Separately, if the queue ends up Sales, a stated shipment volume in the
top band(s) sets `sales_handling_path` to "Enterprise AE" instead of
"Standard Sales" - a handling-path distinction within Sales, not a
routing-priority step.

Whenever the queue differs from the model's raw predicted category, that
raw category is looped in for context rather than lost.

#### Why contradictory signals don't default to Success

Earlier versions of this build routed any contradictory-signals message
straight to Success, on the reasoning that "escalation" needed a home.
That was a real design mistake, caught in review: it made Success a
dumping ground for ambiguous *technical* escalations that Support
should own, and would push Success toward being reactive (handling
overflow triage) rather than proactive (owning real Success work). It
now routes to Team Lead Triage - a Support-side escalation point - and
Success only gets looped in through the same content-driven signals any
other queue would trigger (an expansion mention, a Success category
alternative), not merely because the signals were ambiguous.

#### Why formal close/cancel requests don't default to Success either

"Close Account" and "Cancel a Shipment" are 2 of the 8 real Help Centre
support-form categories - a customer explicitly using that form is
asking for a routine account-lifecycle action, not necessarily opening
a relationship conversation. Routing every one of these to Success by
default would have the same problem as above: Success drowning in
routine admin work regardless of account size. Support keeps ownership;
Success is looped in only when the account is large (`arr_band` in
config's `large_account_arr_bands`) - the retention stakes are high
enough there to warrant proactive visibility. Softer language ("we'll
have to look at other providers") isn't a formal request and keeps the
original behavior: Success owns it directly, since that genuinely is a
relationship conversation. The formal-request check (`is_formal_close_
cancel`) is a plain regex against the raw message text, not an LLM
call - deliberately auditable rather than another judgement call.

---

## Prompt 1 of 3: `classify_and_extract` (model: claude-haiku-4-5)

Purpose: the single call that produces the structured extraction every
later stage depends on. Runs on every message, no exceptions.

The system prompt is assembled per-message from config + an optional
entry-channel block. Below is the exact template with the config-driven
parts shown as `{...}` (the literal current values are in
`config.py`):

```
You classify inbound customer messages for {company_name},
a shipping software company, into exactly one of:
{categories}.

Service = support/logistics issues (shipping, tracking, labels,
customs, billing problems, refunds, compliance).
Success = existing customer wanting a business review, renewal
discussion, or to grow/expand their usage.
Sales = a prospect or existing customer asking about pricing,
plans, or signing up for something new.

[Entry channel prior - only included if entry_channel is known. Two
variants depending on which channel:]

[If Support or Sales:]
Entry channel prior: this message was submitted via {company_name}'s
{entry_channel} inbound form. There is no dedicated Success form -
Support-channel messages are usually Service issues, Sales-channel
messages are usually Sales, but existing customers often use whichever
form is in front of them (e.g. an existing account asking about
upgrading frequently goes through the Sales form even though the real
need is a Success conversation). Treat entry channel as a helpful
prior, never as a determining factor - if the message content clearly
points to a different category, trust the content over the channel.

[If Success:]
Entry channel prior: this message was submitted via {company_name}'s
Success mailbox. Unlike the Support and Sales forms, this is a plain,
unstructured inbox with no form fields guiding what gets sent there -
in practice it fills up with a genuine mix of Service, Success, and
Sales content, because nothing about the channel itself signals which
one a message is. Treat this channel as carrying essentially no
predictive value for category - do not lean on it at all, and classify
based on message content alone as you would with no entry channel
provided.

The Success mailbox is a deliberate modeling assumption, not a real
{company_name} intake point: there is no dedicated Success form in
reality, so this stands in for what actually happens instead - a
shared, unstructured inbox (e.g. a "success@" address CSMs hand out
ad hoc) that ends up carrying a genuine mix of all three categories.
Validated with a 15-message test batch (`data/sample_messages.json`,
`split: "success_mailbox"`, `entry_channel: "Success"`): 15/15 (100%)
correctly classified by content alone, confirming the model isn't
biased toward "Success" just because of where a message arrived. Run
with `python batch_runner.py --split success_mailbox`.

Reference terminology per category (a hint, not an exhaustive list):
- Service: hs code, customs, tracking, label, shipment, order,
  delivery, duty, tax, courier, package, parcel, refund, chargeback,
  compliance, gdpr
- Success: qbr, ebr, renewal, expand, expansion, scale, scaling, grow,
  growth, upgrade, review, account health, onboarding, enterprise,
  new warehouse, new brand
- Sales: pricing, price, plan, demo, trial, quote, discount, compare,
  comparison, sign up, signing up, new customer, setup fee,
  contract terms

retention_risk_language: set this true for explicit close/cancel
account or shipment requests, phrases like: close account, cancel
account, cancel my account, cancel a shipment, cancel shipment,
downgrade, switching to a competitor, leaving example co, and also for
softer but real language about leaving or switching providers even
without a formal cancellation request. This exists so retention risk
routes correctly regardless of how it's phrased.

sensitive_topic_flags is a NARROW field. Only use terms from this exact
list, and only when clearly present: refund, chargeback, compliance,
customs seizure, rate dispute, overcharge, billing dispute, gdpr, data
request, legal. Match the FULL concept, not a substring - the word
'customs' appearing anywhere does NOT mean 'customs seizure' applies;
that term means customs authorities are actively holding, confiscating,
or refusing to release the goods right now - not a routine customs
question, a delay, a documentation request, or a shipment that has
already been returned to sender. A return-to-sender caused by a
paperwork or documentation mismatch is a completed logistics outcome to
troubleshoot, not an active seizure or compliance hold - do not flag it
even though customs was involved in causing it. Likewise
'overcharge'/'rate dispute' require the customer actually disputing a
charge as wrong, not just asking about or being surprised by a
duty/rate amount. A message being urgent, negative, or about a real
logistics problem (a late shipment, a tracking issue, a stuck customs
clearance, a shipment returned to sender, an HS code question, a rate
discrepancy to be looked into) is NOT by itself a sensitive topic -
leave this field empty unless one of the listed topics specifically and
fully applies. Examples that should NOT be flagged: 'my package is
stuck in customs, what documents do I need' (routine hold, still in
progress, no confiscation), 'the rate charged doesn't match the quote,
please check' (routine discrepancy, not a dispute), 'my order was
returned to sender because the customs paperwork didn't match the
contents, what went wrong' (routine documentation mismatch - the goods
were sent back, not confiscated or held). Examples that SHOULD be
flagged: 'I want a refund', 'I'm disputing this charge as wrong and
want it reversed', 'this is a GDPR data request'.

Be honest about ambiguity: if a message clearly fits more than one
category, say so via category_alternatives and contradictory_signals
rather than forcing false confidence.
```

**Note:** this quoted block is abbreviated for readability - the real
prompt in `pipeline.py` includes additional worked examples (e.g. the
distinction between a neutrally-reported duplicate charge and one the
customer is objecting to) and runs at `temperature=0` for deterministic
extraction. `pipeline.py` is the source of truth; treat this doc as a
plain-English orientation to it, not a byte-exact mirror.

The message itself is sent as the (only) user-turn content, with no
ground truth attached. The response is constrained to a strict JSON
schema (`output_config: {"format": {"type": "json_schema", ...}}`) - see
`EXTRACTION_SCHEMA` in `pipeline.py` for the full field list.

**Revision history on this prompt (why it reads the way it does):**
- v1 flagged "tracking" as a sensitive topic (a bug); fixed by requiring
  exact list terms only.
- v2 still substring-matched "customs" -> "customs seizure" and
  "overcharge" too loosely (9 false positives on the first full run);
  fixed by adding explicit negative/positive examples.
- v3 (5 July) added the "return to sender" negative example after a
  fresh false positive on `msg_017` - see below.
- v4 (6 July) narrowed 'overcharge'/'compliance' further after a fresh
  independent 100-message batch caught 4 new false positives; also
  found this narrowing had a false-negative side effect on `msg_085` (a
  genuinely-disputed duplicate charge, wrongly no longer flagged) - a
  worked contrast between the two similar-looking "charged twice"
  examples was added to fix it, and the call switched to
  `temperature=0` since the bug reproduced inconsistently at the
  default temperature, which made it hard to tell a real regression
  from sampling noise.

---

## Prompt 2 of 3: `draft_response` (model: claude-sonnet-5)

Purpose: writes the actual reply draft a human reviews before sending.
Never called for messages where a prior step failed; always produces a
draft, never a sent message.

Three variants of the *instruction* line depending on routing outcome,
then a shared brand-guidelines block appended when
`data/brand_guidelines.json` is present (read fresh on every call, not
cached):

```
[If Service and a reference is required but missing:]
Key information is missing (no order/account reference). Draft a
brief, polite reply asking the customer for that specific missing
detail. Do not attempt to resolve the issue.

[If routed to Team Lead Triage:]
Draft a brief, helpful reply addressing: {issue_type}. This message's
queue assignment is uncertain and pending manual review by a team lead,
so treat {category} as a best guess only, not a confirmed team. This
is a draft for human review before sending, not a final answer.

[Otherwise:]
Draft a brief, helpful reply for the {queue} team to send this
customer, addressing: {issue_type}. This is a draft for human review
before sending, not a final answer.
```

Reference block (appended when `find_matching_article` finds a matching
Help Centre/playbook article for this queue - see the "Reference
retrieval" glossary row above):

```
Relevant reference material found for this message ("{article title}"):
{article answer}
Ground your reply in this - reuse its substance in your own words
rather than inventing an answer, but don't just paste it verbatim if
the customer's specific situation needs a more tailored response.
```

Brand block (appended whenever the guidelines file loads successfully):

```
Brand guidelines for {company_name} (follow these exactly):
Tone: {tone}
Voice principles:
{voice_principles, one per line}
Never use these words/phrases: {banned_words_or_phrases}
Formatting: {formatting}
Sign off with: {sign_off}

Separately, avoid AI-isms - words and patterns that read as
AI-generated rather than human-written:
Never use these words: {avoid_ai_isms.banned_words}
Never use these phrases: {avoid_ai_isms.banned_phrases}
Style rules:
{avoid_ai_isms.style_rules, one per line}
```

Full system prompt is: `You draft short customer-support replies for
{company_name}. Keep it to 2-4 sentences unless technical detail
requires more, no filler.` + the instruction + the reference block +
the brand block. The user-turn content is `Original message:
{text}\n\nInstruction: {instruction}`. `thinking` is explicitly
disabled for this call - a short drafting task doesn't benefit from
extended reasoning, and leaving it on would only inflate cost.

---

## Draft-quality confidence (the second, distinct confidence score)

The routing confidence score (`score_confidence`, see the rubric table
above) answers one question: *did this message land in the right
queue?* It says nothing about whether the drafted reply itself is any
good - a message can be routed perfectly and still get a weak,
generic, unaided answer, or land in an uncertain queue and still
happen to get a well-grounded draft. Conflating the two would hide
that distinction, so `score_draft_confidence` is a second, separate
score answering: *is this specific draft likely good enough to send?*

This was raised directly as a design gap: how do you assess draft
quality without a real usage baseline to check against? An LLM rating
its own answer's quality would just be asking the model to grade its
own homework - not a verifiable signal. The answer implemented here is
rule-based, the same philosophy as the routing confidence score: **was
this draft actually grounded in a real, matched reference article, or
is it the model's own unaided attempt?**

```
if needs_clarification:      band = "n/a"   (no answer was attempted)
elif queue == "Team Lead Triage":  band = "low"    (queue itself unconfirmed)
elif a reference article matched:  band = "high"   (grounded in known-correct source material)
else:                              band = "low"    (fully generative, nothing to check it against)
```

The intuition: a human agent who looks up the right Help Centre article
before replying is more likely to be accurate than one answering from
memory. If the customer asks "how do I do X" and the Help Centre says
"do X by A, B, C" and the draft reproduces A, B, C in its own words,
that's verifiably higher-confidence than a draft with nothing behind
it. This is deliberately honest about what a prototype without real
usage data can and can't verify - it doesn't claim to assess semantic
correctness, only whether grounding was possible at all. Getting a more
precise draft-quality signal (e.g. checking how closely the draft
actually matches the retrieved article, not just whether one was found)
is exactly the kind of thing that should be recalibrated once this runs
against real inbound messages and real outcomes - see the confidence
rubric caveat below.

---

## Prompt 3 of 3: `investigate_uncertain_message` (model: claude-sonnet-5, agentic)

Purpose: the only agentic component. Triggered only when
`confidence["band"]` is in `investigation_trigger_bands` (currently
`["low"]`) - never on the full batch.

System prompt (fixed, no per-message templating):

```
You are helping a human support reviewer triage an uncertain customer
message. You have three read-only tools available: two account/order
lookups and a Help Centre search. Decide for yourself which, if any,
are worth calling, based on what the message actually contains - do
not call a lookup tool with a reference you are guessing at or
inventing, and do not search the Help Centre with a query unrelated to
what's actually being asked. If the message has no usable reference,
say so plainly rather than calling a tool anyway. When you are done,
write a short (2-3 sentence) note for the human reviewer summarising
what you found and what it means for handling this message.
```

User turn: `Message: {text}\n\nExtracted account/order reference (if
any): {reference or "none found"}`.

Three tools available (`INVESTIGATION_TOOLS` in `pipeline.py`):

- **`lookup_order_status(order_reference)`** - live shipment/tracking
  status (carrier, last scan, destination). Returns `not_found` if the
  reference doesn't exist.
- **`lookup_account_context(order_reference)`** - account-level context
  (plan tier, account age, recent ticket volume, ARR band) for the same
  reference. Returns `not_found` if there's no account on file.
- **`search_help_centre(query)`** - free-text search over the mock Help
  Centre articles (`data/help_centre_articles.json`), returning the
  best-matching article's title and answer, or `not_found`. Added after
  reviewing whether two tools were enough (see "Why not just always
  call both/all tools" below) - a real search capability makes the
  agentic judgement call meaningfully richer than choosing between two
  account-data lookups alone.

All three are backed by synthetic mock data - read-only, no write
capability exists at all. Hard cap of 4 iterations (`max_iterations`).
The model decides for itself, per message, which (if any) of the three
tools are worth calling - this is the one place in the whole build
where the model chooses its own next action rather than following a
fixed sequence, which is what makes it agentic rather than another
workflow step.

#### Why not just always call both (now all three) tools?

This was a fair challenge to the original two-tool design, and the
honest answer has two parts. First, on cost/latency: always calling
every available tool regardless of relevance isn't free - each tool
call is a round trip in the same iterative loop, so calling three tools
on every low-confidence message (rather than the one or two that are
actually relevant) would add real latency and cost across the ~1-in-5
messages that reach this step, for no accuracy benefit on the messages
where a given tool has nothing useful to return (e.g. calling
`lookup_account_context` on a message with no account reference at all
just returns `not_found` and wastes a turn). Second, and more
importantly: forcing a fixed "call everything" sequence would turn this
back into a workflow, not an agent - the entire point of this being the
one agentic step in the build is that the model exercises judgement
about *which* lookups are worth making based on what the message
actually says, the same judgement a human reviewer exercises before
looking things up. If the answer were "just always call both/all",
there'd be no decision left for the model to make, and this step
wouldn't be meaningfully different from a fixed Python function calling
three APIs unconditionally.

That said, the critique that two tools was a thin set to be making a
real judgement call over was fair - with only two, "decide which to
call" was a fairly low-stakes choice. Adding `search_help_centre` as a
third, qualitatively different tool (a content search, not another
account-data lookup) makes the judgement genuinely richer: the model
now has to decide not just "do I have a usable reference" but "is this
a known, documented kind of problem worth searching for, or an
account-specific question that needs account data instead" - a more
defensible test of real agentic judgement than choosing between two
near-identical lookups.

---

## Error handling and latency (why neither is a production risk)

In production, this pipeline runs as a background enrichment step, not
a blocking one: a new message would still land in the normal
helpdesk/CRM inbox exactly as it does today, visible and workable by a
human immediately. The AI call happens asynchronously and writes its
output (category, confidence, draft, flags) onto the ticket once it
finishes - it never has to complete before a human can pick up and
work the ticket manually.

That means a slow response (the build's own measured p95 is under 12
seconds, but a real deployment should assume worse under load) or a
failed API call (rate limit, timeout, partial outage) has a bounded,
safe failure mode: that one ticket's enrichment simply arrives late or
not at all, and a human handles it exactly as they would have without
the AI at all. Nothing blocks, nothing silently mis-routes, and no
message is ever auto-sent - every draft still waits for human review
regardless of how the AI call went. `batch_runner.py` and
`opus_comparison.py` set explicit `timeout=60.0` and `max_retries=3`
on the API client (rather than relying on SDK defaults) so transient
failures retry automatically before falling back to "no enrichment
yet" rather than raising.

---

## What is NOT a prompt

For completeness, since the question "how many prompts built this" is
worth answering precisely: `score_confidence`, `determine_queue`,
`health_expansion_flag`, and `score_draft_confidence` make no API calls
at all - they are plain Python functions operating on the structured
output of Prompt 1 (plus, for routing, a deterministic lookup against
mock account data). No prompt exists for them because none is needed;
the whole point of extracting structured fields first is that routing
logic can then be ordinary, auditable code instead of another opaque
model judgement call.

---

## Design decisions, open questions, and pushback

Honest answers to a round of direct design feedback, including
pushback where warranted rather than agreement for its own sake.

**The confidence rubric needs real recalibration, not just tuning.**
Every weight in `score_confidence`'s rubric (see the table above) was
chosen by looking at the score distribution on this build's own
synthetic dev set - a reasonable starting point, but not a substitute
for someone with real experience of actual inbound messages setting
these weights. In a real Phase 1 rollout, this rubric should be
treated as a first draft that gets materially rewritten once a
support/CS lead with real message-pattern experience reviews a few
weeks of real routing outcomes against it - which signals actually
predicted a wrong queue, which didn't, and what's missing entirely.
Presenting the current weights as more than a reasonable starting
guess would overstate what a 220-message synthetic test set can prove.

**Cybersecurity/attachment scanning - deferred, not built, on purpose.**
Raised as a question: should this build scan attachments for threats?
No, and this is a case for pushback rather than a "sure, add it":
attachment/malware scanning is a specialized security capability
(file-type sniffing, sandboxed detonation, signature and heuristic
threat detection) that a support-routing AI team shouldn't be
homebrewing inside a triage pipeline. The right architectural answer is
integrating a dedicated, purpose-built scanning service (an existing
email-security gateway, or a dedicated attachment-scanning API) ahead
of this pipeline, not building detection logic here. `sensitive_topics`
in `config.py` does include cyber-incident language (data breach,
cyberattack, unauthorized access, hacked) so a message *describing* a
security incident is still caught and routed to Service - that's a
text-classification problem this pipeline is already built for. Actual
attachment/file scanning is a distinct, deferred capability and is
documented here as a placeholder for a future version, not silently
skipped.

**"Most issues probably land in All Other Queries" is an unconfirmed
hypothesis, not a fact.** It's a reasonable guess - broad catch-all
categories on real support forms usually do absorb a disproportionate
share of volume - but this build has no real usage data to confirm it,
and presenting it as settled would be exactly the kind of unverified
claim this project has tried hard to avoid elsewhere. It should be
treated as a testable prediction for the first weeks of real Phase 1
data, not a planning assumption.

**The 8 real Help Centre categories are referenced, not deeply modeled
with their per-category structured fields.** `config.py`'s
`help_centre_categories` list and the mock Help Centre articles reflect
the real category set, but only one category's exact field-level
structure was ever confirmed (Labels, via a screenshot) - the other
7 categories' live pages weren't fetchable during this build (403s on
direct requests). Modeling all 8 categories' exact structured fields
(the shared base fields - email, Customer ID, a shipment identifier,
Description, Attachments - are known; the category-specific FAQ
suggestions per category are not, beyond Labels) would need either
direct access to the real form or a company representative confirming
the other 7, and is scoped as a next step rather than guessed at here.

**Team Lead Triage's likely evolution: a Critical Response Center.** As
this queue's *rate* is expected to fall over time (see `config.py`'s
comment on the confidence floor), its *composition* should shift too -
from "everything the model couldn't confidently place" toward
specifically the harder, higher-stakes cases that genuinely warrant
senior judgement. A natural next step is formalizing this into a
Critical Response Center staffed by senior Support Principal(s) with a
dedicated playbook for bigger issues (large-account escalations, novel
problem types, anything touching multiple teams) - a more deliberate,
resourced version of what Team Lead Triage does informally today, not
a new headcount ask on top of it.

**Support tiering maps loosely onto this build's queues, but isn't a
clean match.** A conventional L1/L2/L3 model maps roughly as: Service
queue ~ L1 (first-line triage and routine resolution), Team Lead
Triage ~ L2 (escalated, needs a more senior read), and a future
Critical Response Center (above) would be the closest analogue to L3
(specialist/high-stakes handling). This is offered as a plausible
frame for thinking about future org design, not as a claim that this
build's 4 queues already implement a formal tiering system - they
don't, and forcing the L1/L2/L3 label onto them prematurely would
overstate how deliberately tiered the current design actually is.
