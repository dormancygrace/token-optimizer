# Token Optimizer — Savings Methodology

**Purpose.** This document defines exactly how every savings number Token Optimizer
reports is calculated: the data source, the formula, the assumptions, and the honesty
caveats. It is written to be defensible to a skeptical reader (e.g. a POC client deciding
what they gained). Nothing here is a marketing estimate; every number traces to the user's
own measured sessions priced at published rates.

Last verified: 2026-06-15. Pricing cross-checked against Anthropic's published rates.

### What changed (2026-06-15)

- **Retired the per-session era comparison.** The headline was an early-window-vs-recent-window
  per-session comparison, confounded by volume growth between the two periods. It is now a
  current-volume counterfactual: one volume, priced two ways (§3).
- **Removed the M4 outlier drop.** Heavy sessions are no longer winsorized or dropped. Their
  tokens are real volume that genuinely cost more at the baseline mix, so dropping them only
  suppressed real dollars.
- **Cache-write is now a routing lever.** Cache-creation tokens are priced at each arm's own
  mix (baseline in the counterfactual, actual in actual), not the same mix on both arms. This
  reverts the former same-mix choice, which dropped real Opus-rate cache-write savings.
- **Added the subagent pool and the compression add-back.** Two new non-overlapping pools were
  added so the headline is a true superset: subagent (sidechain) routing (pool 2) and the
  metered compression dollars repriced to the baseline input mix (pool 3).

---

## 0. Principles (the rules every number obeys)

1. **Three tiers, never summed across the wrong boundary.**
   - **Measured** — billable events Token Optimizer directly metered (a compression ran, a
     route changed). 100% attributable, conservative.
   - **Estimated** — counterfactuals grounded in the user's own behavior (a cohort, an
     observed repetition count). Each carries a sample size and, where applicable, a
     confidence label. Shown split out, never passed off as measured.
   - **Opportunity** — avoidable waste the user *could* reclaim but Token Optimizer does not
     yet prevent. Never folded into "what you're saving."
   Measured + Estimated may headline *together* only when the split is always visible.
   Opportunity is always a separate figure.
2. **Never fake a realized dollar.** A dollar is "realized/measured" only when (a) Token
   Optimizer intervened, (b) behavior changed, and (c) we measured the reduction against a
   baseline. Anything failing all three is Estimated or Opportunity.
3. **Grounded in the user's own data.** No shared default multipliers. Every baseline is the
   user's own measured history; every cohort is their own sessions.
4. **Conservative on every judgment call.** Where a choice exists, we pick the one that
   understates rather than inflates (documented per-element below).

---

## 1. Pricing rates (the foundation)

All costs are **API-equivalent** value: what the usage *would* cost at published per-token
API rates. On a flat subscription the user is not billed per token, so this is reclaimed
pay-as-you-go-equivalent value, not a refund.

Rates (per 1M tokens), tier `anthropic`, verified against Anthropic's published pricing
(2026-05-26 snapshot):

| Model | Input | Output | Cache read (0.1×) | Cache write 5m (1.25×) | Cache write 1h (2×) |
|---|---|---|---|---|---|
| Opus 4.x (4.8/4.7/4.6) | $5.00 | $25.00 | $0.50 | $6.25 | $10.00 |
| Sonnet 4.6 | $3.00 | $15.00 | $0.30 | $3.75 | $6.00 |
| Haiku 4.5 | $1.00 | $5.00 | $0.10 | $1.25 | $2.00 |

- The legacy $15/$75 Opus rate was **Opus 3** (retired). Using it would overstate ~3×.
- OpenAI/Codex and Gemini models use their own provider rate cards (`_get_model_cost`).
- Cache reads are priced at **0.1×** input and cache writes at **1.25×** (5-minute TTL,
  the common case) — exactly Anthropic's published cache multipliers.
- Implemented in `_get_model_cost(model, input, output, cache_read, cache_create, tier)`.

---

## 2. Per-session token decomposition

Each session in `session_log` stores `input_tokens` (total billed input = fresh + cache_read
+ cache_write), `output_tokens`, `cache_create_5m_tokens`, `cache_create_1h_tokens`, and
`cache_hit_rate` (= cache_read / total billed input). We reconstruct the four billed classes
exactly (`_session_token_vector`):

```
cache_write (cw) = cache_create_5m + cache_create_1h
cache_read  (cr) = input_tokens × cache_hit_rate
fresh_input (fi) = max(0, input_tokens × (1 − cache_hit_rate) − cache_write)
output      (o)  = output_tokens
```

`cache_hit_rate` is clamped to [0,1]. This decomposition is exact (the three input classes
sum back to total billed input). **Why it matters:** in real Claude Code usage the bulk of
token volume (commonly 80%+, rising with session length) is cache-reads — the same prefix
(CLAUDE.md, skills, tool defs, conversation history) re-read on every turn. The dashboard reports
each user's own measured cache-hit rate. Cache-reads are cheap per token (0.1×) but enormous in volume, so they
dominate cost. The whole savings story is largely about cache-read *volume* falling.

**Per-session cost** (`_cost_per_session`): price the class vector at the era's REAL model
mix as a weighted average over EVERY model present. Each model's share is priced at its rate
card via `_get_model_cost`; an unpriced entry (an unknown or local model with no rate card) is
proxy-priced at the runtime-default rate rather than dropped or renormalized away. The
denominator is the TOTAL present share (not the priced share), so a tiny priced sliver can
never be inflated to represent the whole mix:
```
cost = ( Σ_model share_model × cost_model'(vec) ) / Σ_model share_model
       where cost_model'(vec) uses the model's own rate card if priced, else the runtime-default rate
```
This is provider-agnostic: an Anthropic era blends Opus/Sonnet/Haiku; a Codex era blends
gpt-5-codex / gpt-5.x / mini by their measured shares; an unrecognized model is estimated at
the runtime default. The same estimator is applied to both the before and after windows, so
the comparison is fair. If a mix has no priced models at all, the session is priced at a single
runtime-default model (Codex → gpt-5-codex, else Sonnet).

---

## 3. THE TRANSFORMATION (headline counterfactual)

**Claim:** *Your current activity, run the way it ran before Token Optimizer, would cost
$X/month more than it does now.*

The headline is a **current-volume counterfactual**, implemented in
`_estimate_before_after_savings`. Take the user's CURRENT 30-day billed volume, hold it
constant, and price it two ways:

- **ACTUAL** = the current model mix and current cache pattern (what it really cost).
- **COUNTERFACTUAL ("the old way")** = the SAME volume, priced at the PRE-TO baseline
  efficiency (mostly Opus, with the pre-TO cache pattern).

The transformation is `counterfactual − actual`. Because the volume is identical on both arms,
the gap is pure efficiency (model mix plus cache-hit), never confounded by workload growth.
This replaces the retired per-session era comparison, which compared an early window to a
recent window and was therefore confounded by volume change between the two periods.

### 3.1 The four pools (non-overlapping, summed to the headline)

The headline is the sum of three separate, non-overlapping pools (caching lives inside pool 1,
so there are four levers across three pools). They never share tokens, so the sum never
double-counts.

**Pool 1, Main routing + caching** (billed `session_log` volume, sidechains excluded).
The current window is aggregated into billed token classes via `_session_token_vector`:
`F` (fresh_input), `CR` (cache_read), `CW` (cache_write), `O` (output), with `total_in = F +
CR + CW`. The actual arm prices these at the current mix. The counterfactual redistributes
caching over the **fresh+cache_read pool** (`pool = total_in − CW`) at the baseline pool-hit
rate (`cf_CR = base_hit × pool`, `cf_F = pool − cf_CR`) and prices the pool at the baseline
mix. The invariant `cf_F + cf_CR + CW == total_in` holds exactly, so the counterfactual never
prices more volume than actually moved. **Cache-write is a routing lever:** cache-creation
tokens are billed at the writing model's rate (Opus $6.25 vs Sonnet $3.75 per MTok), so `CW`
is held constant in TOKEN count across both arms but priced at each arm's OWN mix (baseline mix
in the counterfactual, actual mix in actual), TTL-aware (1h writes at 2x, 5m at 1.25x). Caching
(the cache-hit redistribution) lives inside this pool. **No outlier drop:** there is no
winsorization and no heavy-session cap. A heavy session's tokens are real volume that genuinely
cost more at mostly-Opus, so dropping them is a definitional undercount. (The former M4 guard
that dropped sessions above 150x the median deleted roughly a quarter of real billed tokens on
the owner's data while leaving the efficiency rate flat, proving it only suppressed real
dollars. Corrupt-row protection lives in `_session_token_vector`'s clamps, so no row-level guard
is needed here.)

**Pool 2, Subagent (sidechain) routing** (`_subagent_pool_savings`).
Subagents run in sidechain transcripts (`isSidechain:true`), are NOT stored as `session_log`
rows (the collector only walks top-level project JSONL), and were therefore entirely excluded
from pool 1. They are real spend that Token Optimizer routes (a CLAUDE.md that routes subagents
to Haiku/Sonnet; pre-TO they would have run on Opus). The pool scans sidechain JSONL for the
window, aggregates each subagent's billed token classes per model, prices the actual arm at each
subagent's real model and the counterfactual at the baseline mix, and credits
`max(0, counterfactual − actual)`. These are different transcripts from pool 1, so there is no
token overlap. **Platform note:** this pool is a documented gap on platforms without
Claude-style sidechains (see the TS parity note in §3.4).

**Pool 3, Compression add-back (volume reduction)** (the `_get_savings_summary` metered
dollars repriced).
Token Optimizer REMOVES tokens from context (tool archiving, structure-map skeletons,
resume-lean, checkpoint restore, delta reads). Those removed tokens are real volume the old way
would have kept re-reading and paying for. They are NOT in `session_log` (already removed before
billing), so this pool is **disjoint from pool 1 AND from the caching lever** (which only
redistributes billed fresh+cache_read tokens). The method takes the directly-METERED
removed-token dollars from `savings_events` and reprices them to the baseline INPUT mix
(`baseline_input_rate / current_input_rate`): the mostly-Opus old way would have paid more per
re-read than today's mix did. The actual cost for this pool is 0 (the tokens were never billed),
so the whole repriced value is transformation. The metered figure is the proven floor; the
reprice multiplier is the only estimated step and is disclosed.

```
transformation        = pool1_transformation + pool2_transformation + pool3_addback
counterfactual (hdln) = pool1_counterfactual + pool2_counterfactual + pool3_addback
transformation_pct    = transformation / counterfactual(hdln)
```

The percentage is taken over the combined counterfactual so it reflects the full spend base
being transformed.

### 3.2 The baseline (the PRE-TO efficiency anchor), `_compute_baseline_state`

The baseline supplies the pre-TO efficiency anchors (the model mix and the cache-hit pattern)
ONLY. It does NOT supply volume; volume comes entirely from the current window and is held
constant across both arms. The baseline is a typical pre-optimization session measured from the
user's own earliest real sessions, captured once and frozen:

- **Window:** skip the first install-day (`_BASELINE_ONBOARDING_DAYS = 1`), then the next
  `_BASELINE_EARLY_WINDOW_DAYS = 30` days.
- **Cache-hit pattern (`base_hit`):** the baseline session's cache-read share over the
  fresh+cache_read pool (`baseline cache_read / (baseline fresh + baseline cache_read)`). If the
  baseline session has no usable pool, `base_hit` falls back to the current pool-hit, which
  neutralizes the caching lever (conservative).
- **Frozen + versioned:** stored in `baseline_state.json`, atomic write, `version` field.
  Captured once so the anchor is stable run-to-run.
- **New users:** the baseline freezes once the 30-day window has elapsed AND
  ≥ `_BASELINE_MIN_STABLE_SESSIONS` (30) sessions exist; until then the transformation is hidden
  (no fake number). **Existing users** (installed before any baseline was captured): the same
  computation runs over their earliest real sessions and freezes now, which is why it works for
  POC clients with prior history.

### 3.3 The baseline mix (gating, never fabricated Opus)

The before-arm model mix is resolved by `_estimate_before_after_savings` per these rules:

- **Anthropic user with a measured frozen baseline → trust it.** A real `opus_share` from the
  frozen baseline is used as-is (the owner's case: Anthropic + frozen ≈ 0.95 Opus).
- **Anthropic user, no baseline, one-time consent (`_opus_floor_consented`) → 0.95 Opus owner
  default.** Gated so a new Anthropic user who never ran Opus is not handed a fabricated 0.95
  baseline that over-counts the routing lever.
- **Anthropic user, no baseline, no consent → price the before-arm at the user's OWN current
  mix.** No fabricated Opus; the routing lever falls to the caching lever alone until a baseline
  is measured or consent is given.
- **Non-Anthropic (Codex / Hermes / Copilot / OpenClaw / OpenCode) → ALWAYS the user's own
  measured mix.** Opus they never ran is never injected.

### 3.4 Attribution, the mechanism and its limits

The footprint decline is what Token Optimizer is built to produce: it surfaces context-quality
decline and cache-drop risk (status bar, quality score, nudges), routes subagents to lighter
models, and removes re-read volume from context. The counterfactual ("had I kept running at
mostly Opus, eaten cache misses, and run subagents on Opus") is the avoided cost.

Stated honestly: this is a single-user before/after on the user's own data, not a controlled
experiment. We cannot rigorously separate Token Optimizer's effect from background factors
(model-version cost changes, a slower vs an intense month, the user's own skill growth). We
present it as a strong attribution, not a proven cause.

**TS parity note.** A parallel effort ports this methodology to OpenClaw / OpenCode
(TypeScript). On those platforms the subagent pool (pool 2) is a **documented platform gap**:
they have no Claude-style sidechain transcripts, so pool 2 reads zero there rather than being
estimated. Non-Anthropic platforms always price the baseline at the user's own measured mix
(§3.3), never a fabricated Opus share.

### 3.5 Honesty caveats (surfaced with the number)

These are documented here and surfaced in the dashboard's "How we work this number out"
explainer beside the figure.

- **Counterfactual, not a bill.** It prices the volume the user actually moved this period at
  the pre-TO efficiency. On a flat subscription it is reclaimed pay-as-you-go-equivalent value,
  not money refunded.
- **Volume is held constant, so workload growth does not inflate it.** Both arms price the
  identical billed volume; only efficiency (model mix, cache-hit) and metered removals differ.
  This is the specific flaw the retired per-session era comparison had and this method does not.
- **The compression reprice is the one estimated step in pool 3.** The metered removed-token
  dollars are the proven floor; multiplying by `baseline_input_rate / current_input_rate` is the
  estimated reprice, disclosed as such.
- **Some is workflow choice.** Most of the decline is genuine efficiency driven by Token
  Optimizer's signals; a portion reflects the user choosing leaner workflows.

### 3.6 Worked example (one user's 30-day data, not universal)

These are the owner's own live numbers over 30 days. Every user's figures differ; this is one
data point, shown to make the pool arithmetic concrete.

| Component | Monthly |
|---|---|
| Pool 1, main routing + caching | $1,077 |
| Pool 2, subagent routing | $741 |
| Pool 3, compression add-back | $59 |
| **Headline transformation** | **$1,878/mo (~17.8%)** |

Combined actual ≈ $8,693/mo; combined counterfactual ≈ $10,572/mo. Inside the breakdown the
levers are routing $1,025, subagent_routing $741, context_compression $59, context_rereads $52
(caching lives inside pool 1). Baseline Opus share 0.95, current Opus share 0.60.

---

## 4. The breakdown ("Where it comes from") — `breakdown` in `_estimate_before_after_savings`

A decomposition of the monthly headline into the four levers that produced it. The two levers
inside pool 1 (main routing + caching) are a sequential waterfall: morph the counterfactual
footprint into the actual one lever at a time. Routing is credited first (the baseline
footprint, including cache-write, repriced from the baseline mix to today's mix), then caching
at today's mix; the two unrounded steps telescope exactly to the main-pool transformation. The
subagent and compression pools are added as their own levers so the four lines sum to the
combined headline.

Because cache-write is now a routing lever (priced at each arm's own mix), the `routing` line
captures the cache-write mix-delta too; there is no separate `structural` cache-write line.

| Lever (`key`) | Pool | What it is |
|---|---|---|
| `routing` | 1 | Cost removed by the model-mix shift on the billed footprint, including cache-write at the writing model's rate (e.g. Anthropic 95%→60% Opus, or Codex moving off a pricier GPT tier). A negative value means the mix moved to costlier models. |
| `context_rereads` | 1 | Cost removed by the cache-read volume redistribution (the caching lever) |
| `subagent_routing` | 2 | Cost removed by routing subagents to lighter models vs the baseline mix |
| `context_compression` | 3 | The directly-metered tokens Token Optimizer removed from context, repriced at the baseline input mix |

`waterfall_index` preserves the causal order for machine consumers; the list is sorted
largest-first for display. A **negative** lever means that class grew (a cost increase); it is
labelled with a "(added cost)" phrasing so a "-$X" line never reads as a saving. When the main
pool clamps to zero (net-negative main efficiency), the two pool-1 levers read zero and the
subagent and compression pools carry the headline.

---

## 5. Measured / realized tier (directly metered)

The measured tier is what Token Optimizer directly metered: realized model routing plus runtime
compression events. It is **not added on top of the headline transformation**. Two relationships
hold:

- **Measured routing overlaps the transformation's routing lever.** The realized routing figure
  is the proven slice of the same model-mix shift the headline already prices in pool 1. It is
  reported as a floor on that lever, never summed with it.
- **Measured compression is the proven floor of pool 3.** The runtime compression events are the
  exact metered dollars the compression add-back reprices into the headline. The measured figure
  is the floor; the headline reprices it to the baseline input mix (the one estimated step).

For the owner's 30-day data, the measured tier is **$311/mo** ($260 realized routing + $51
compression). The headline transformation ($1,878/mo) is a superset that also prices the
counterfactual routing on the full footprint, the subagent pool, and the repriced compression.
This is one user's data; every user's figures differ.

### 5.1 Model routing — `_compute_model_routing_savings` (realized portion)
Compare the **current** model mix against the install-era baseline (snapshot `model_mix`, else
earliest `model_daily` window). REALIZED = current token volume priced at the baseline mix,
minus actual current cost. This is the rare pillar that shows a genuine realized win when the
user moved tokens off Opus (e.g. 95% → 60%). Rates blend each model's input + output $/MTok by
the **measured** output fraction (`SUM(output)/SUM(input+output)`); pricing at the input rate
alone understated routing 1.5–2× because output is up to 5× input. POTENTIAL (a conservative
share of remaining Opus routed to Sonnet at the rate delta) is reported in the Opportunity tier.
This realized figure overlaps the headline's pool-1 routing lever; it is the proven slice, never
added to it.

### 5.2 Runtime compression events (measured)
Logged compression events (delta reads, quality nudges, loop output compression, bash output
compression, tool-result archiving). These are billed-event-grounded reductions Token Optimizer
performed; summed into the measured total. They are the **proven floor of pool 3** (the
compression add-back): the headline reprices these same metered dollars to the baseline input
mix, so the measured figure and the headline contribution are the floor and the repriced value
of one quantity, never two separate savings.

### 5.3 Structural prefix — `structural_detail`
Measured against the install snapshot (`snapshot_before.json`): tokens trimmed from the
per-turn prefix (CLAUDE.md, skills, MCP, MEMORY.md) below the captured baseline, priced at the
input rate and compounded across the window. Reads $0 (not negative) when nothing is trimmed
below baseline yet.

### 5.4 Progressive disclosure (tool-archive) — `_progressive_disclosure_summary`
When a large tool result is replaced by a pointer (`archive_result.py`), the net tokens that
stayed collapsed are a measured win. Re-expansions (`expand_archived`) log a debit that is
netted against the original credit (floored at 0) so a re-popped result never over-credits.

---

## 6. Estimated tier (counterfactual, cohort- or count-grounded)

Each carries a sample size; cohort estimators carry a confidence label and a minimum-sample
gate so a thin cohort never shows a number. None is ever summed into the measured total.

### 6.1 Uncaptured runtime — `_estimate_uncaptured_runtime`
Compression that runs inside sub-agent dispatches is not attributed back to the parent session,
so it never lands in `compression_events`. Estimated as measured per-session runtime savings ×
sub-agent dispatch count × **0.5** attribution haircut. Labelled estimated, shown separately.

### 6.2 Loop prevention — `_estimate_behavioral_savings`
When loop detection fires it compresses the repeated output (counted as runtime) AND stops a
runaway loop that would have burned more iterations. The avoided continuation is never billed.
We do **not** fabricate a multiplier. The avoided continuation is estimated as one more
equivalent looping span: the measured looped token volume multiplied by a continuation factor of
1.0 (a deliberate floor, since a loop caught after N repeats would plausibly have run at least
one comparable span more before another guard or the context limit stopped it). The observed
repetition `count=N` is recorded and shown alongside for transparency; it does **not** scale the
dollar figure.

### 6.3 Contamination-exit (heeded-nudge cohort) — `_estimate_contamination_exit_savings`
The flagship behavioral estimate, built on a **natural control group**: sessions where a
quality nudge fired and the user acted (compacted/cleared) = HEEDED, vs fired-but-ignored. The
two cohorts differ only in whether the nudge was heeded, so the delta in per-session rework
signal (stale-read waste, §7.1) is the mess a heeded session avoided. Reported with both cohort
sizes + a confidence label, gated behind a minimum sample.

### 6.4 Continuity handover — `_estimate_handover_rerun_savings`
Same cohort method applied to continuity: sessions that resumed via a restored checkpoint vs
those that did not, comparing the per-session rework signal. A lower figure for restored
sessions is the rework a handover avoided. Estimated tier; selection bias possible (restored
sessions may differ), so shown with sample sizes, never as a hard number.

---

## 7. Opportunity tier (reclaimable, NOT realized)

Shown as a separate "could save" figure. These count waste that *already happened* or value
*still on the table* — Token Optimizer does not yet prevent them, so counting them as savings
would claim money that was actually spent.

### 7.1 Reclaimable stale reads — `_estimate_stale_reads_reclaimable`
Sums `session_log.stale_waste_tokens`: reads that slipped through (re-read after write, or
far-distance stale) and were billed. Priced at the input rate; reports the contributing session
count as a sample size. Reclaimable by avoiding redundant re-reads, `.contextignore`, or
compacting.

### 7.2 Cache drops — `_estimate_cache_drop_savings`
When a session idles past the cache TTL, the prefix expires and the next turn re-pays a full
cache-write. Sessions whose max call-gap exceeds the TTL almost certainly ate ≥1 reload.
Estimated as `drops × per_session_prefix × 5m-write-rate`. Shown in **tokens** (provider-neutral;
the dollar value is Anthropic-specific because OpenAI/Codex cache writes are free). Reclaimable
by compacting before breaks or using the 1h cache.

### 7.3 Output waste — `_estimate_output_waste`
Full-file Writes that could have been Edits re-emit the whole file at the output rate (the
priciest class, $25/MTok on Opus). Estimated as a conservative share of Write calls × a typical
per-rewrite output delta, priced at the output rate. A coaching opportunity (use Edit over
Write), never a forced cap.

### 7.4 Model-routing potential — `_compute_model_routing_savings` (potential portion)
A conservative share (`_ROUTABLE_OPUS_FRACTION`, default 0.3) of remaining Opus tokens routed
to Sonnet at the rate delta — the routing opportunity still on the table.

---

## 8. Cumulative since install — `_savings_since_install`
The full merged savings recomputed over the whole window since the install date so the measured
event tiers sum from day one. Split into measured vs estimated. Opportunity items (cache drop,
output waste) are **excluded** here — counting them would claim money that was spent.

---

## 9. Context Quality Score (referenced by the cohort estimators)
A 0–100 score from six JSONL-derived signals: stale reads, bloated tool results, duplicate
reads, compaction depth, decision density, agent efficiency. Averaged across sessions, with a
rolling window and fill warnings so the score reflects current context health rather than
diluting over a long session. It is the signal whose decline drives the quality nudges that the
contamination-exit cohort (§6.3) measures.

---

## 10. Known limitations (stated, not hidden)
- The transformation is a **counterfactual**, not a billed amount (§3.5), and an attribution,
  not a controlled experiment (§3.4).
- Cohort estimators (§6.3/6.4) can carry **selection bias**; labelled estimated, shown with
  sample sizes.
- Cache-drop dollars are **Anthropic-specific**; shown in tokens for provider neutrality.
- Pre-install waste is **not retroactively measurable** for cohort signals that began logging
  at install, those read 0 on historical sessions and populate forward only.
- **Sub-agent token attribution.** A session's `input_tokens` includes its sub-agents' input,
  but `cache_hit_rate` is the parent session's only. For a session with heavy sub-agent use,
  the decomposition applies the parent hit rate to the combined input, attributing some
  sub-agent tokens to the cheap cache-read class. This is directionally conservative for a
  single era but, if sub-agent usage grew after install, can slightly overstate the delta.
  Bounded by the sub-agent share of tokens; a candidate for a future per-class sub-agent split.
- **Winsorization on small windows.** Capping the top 1% means, for a window with very few
  sessions, only the single heaviest session is capped. For a sparse user (around the 30-session
  minimum) who had several pathological bulk-op sessions in their first 30 days, the cap may not
  neutralize all of them, so the "before" can read modestly high. For a normally active user the
  30-day window holds hundreds of sessions and a few spikes are diluted to near-nothing. The
  per-session mean is most stable on month-scale windows, which is why the window, not the cap,
  is the primary stabilizer.
- **Short after-windows are not extrapolated.** A monthly figure is only shown once at least a
  week of post-baseline activity exists, so a one-day burst is never blown up into a month.
- The baseline's earliest window is already slightly post-install, so it is a **conservative**
  proxy for the true pre-TO era.

---

## Appendix — POC usage
For a client proof-of-concept: install Token Optimizer, let the 30-day baseline window accrue
(or, for a client with prior history, the baseline is estimated from their earliest real
sessions immediately), then read the transformation + measured tiers. The baseline is the
client's *own* sessions at their *own* pre-optimization mix, frozen, so the gain is defensible
as "your activity, your old way, vs now." Always present it with the §3.5 caveats — the honesty
is the credibility.


## Action savings and context history (v5.13.10)

The action card combines three separately labelled amounts for the selected period:

1. Initial logged savings, including output, setup and realized routing. Some dollar rates are allocated from the recorded session model mix.
2. Modeled repeat reads from `counted_reread`, for removals made in that period. Only `reread_usd` is added: initial removals already occur in the logged total. An event without a transcript retains its logged value but gets no invented repeat reads.
3. Other estimates, including lean resumes, concise-output nudges and the existing behavioral estimators. Opportunities remain excluded.

Repeat-read value is attributed to the removal date. Later benefit belongs to that removal until the recorded compaction, even across a date boundary. This is an action-period view, not exact per-turn accrual inside a calendar interval. The method assumes the untrimmed context would otherwise have stayed until that compaction. Stored dollar values retain their recorded valuation rates.

The lifetime **Context savings** card includes initial context removals and modeled repeat reads only. It shows the same ledger's period subtotal for comparison. It excludes output, setup, routing and other estimates, so it is narrower than the action card. Historical marker entries may use estimated removal sizes. Previously transcript-verified records survive transcript rotation. Duplicate `delta_read` telemetry is counted only through `savings_events`.

The transformation estimate overlaps with these action totals and must not be added to them. Its displayed percentage is the reduction between the same actual and counterfactual costs used for its dollar headline. API calls remain a proxy for workload, not a measure of task quality or difficulty.
