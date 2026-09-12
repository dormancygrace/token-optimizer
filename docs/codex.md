# Token Optimizer for Codex

Status: supported

**Your AI is getting dumber and you can't see it.**

*Find the ghost tokens. Survive compaction. Track the quality decay.*

Token Optimizer for Codex audits local Codex context usage, tracks real session token/cost data from Codex JSONL logs, and installs a balanced hook profile for quality tracking and session continuity. Pure Python stdlib, zero dependencies, zero telemetry.

## Status

Token Optimizer supports Codex with a Codex-native adapter. Core audit, coaching, dashboard, cost tracking, continuity, and fleet scanning work today. Some Claude Code mechanisms need separate Codex adapters or telemetry, as documented in the [Feature Parity](#feature-parity) table below.

## Install

**Recommended (marketplace, auto-updates on startup):**

```bash
codex plugin marketplace add alexgreensh/token-optimizer
```

Then in the Codex TUI: `/plugins` and install Token Optimizer.

> Auto-update: Codex auto-upgrades Git-backed marketplaces on startup via `git ls-remote`. Manual upgrade: `codex plugin marketplace upgrade`.

**After install, set up hooks globally (one-time):**

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py codex-install --profile balanced --enable-bash-compression
```

Run this from the installed plugin directory. On Windows PowerShell, set `$env:TOKEN_OPTIMIZER_RUNTIME="codex"` first, then run the Python command without the POSIX environment prefix.

This installs native OS-specific hooks to `~/.codex/hooks.json` for all projects. Codex must review new or changed hook commands before executing them. The Codex manifest disables the inherited Claude Bash hook bundle to prevent duplicate, incompatible handlers. For per-project overrides, use `--project "$PWD"` instead.

The recommended `balanced` profile installs:

- `SessionStart` for session recovery context.
- `UserPromptSubmit` for prompt-quality and loop nudges.
- `Stop` for throttled dashboard refresh and continuity checkpointing.
- `PreCompact` and `Interrupt` for checkpoints; `PostCompact` for context refresh.
- `SubagentStart` and `SubagentStop` for task-specific tracking.
- Optional `PreToolUse` command compression with `--enable-bash-compression`.
- Codex compact prompt guidance in `~/.codex/config.toml`.

### Hook profiles

| Profile | What it installs | Noise level |
|---------|-----------------|-------------|
| `balanced` | SessionStart, UserPromptSubmit, Stop, compaction/interrupt and subagent events | 8 events |
| `quiet` | Stop only | 1 event |
| `telemetry` | Stop + PostToolUse | Tool-result telemetry |
| `aggressive` (installer default) | Balanced + PostToolUse | Full telemetry |

Command compression is a separate opt-in on every profile. It rewrites eligible inspection commands through Codex `updatedInput`, executes once, preserves failures and warnings, and links to the full original output. Write-capable commands and outputs above 8 MiB pass through unchanged.

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py codex-install --profile quiet
```

## Usage

Inside Codex, invoke Token Optimizer conversationally:

- **"Run Token Optimizer"** -- status, setup, and safest next fix
- **"Run Token Coach"** -- make this project more token-efficient
- **"Run Fleet Auditor"** -- cross-system audit including Codex sessions
- **"Show the dashboard"** -- analytics dashboard

## Uninstall

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py codex-install --uninstall
```

This reverses exactly what `codex-install` wrote:

- Strips Token Optimizer hook groups from `~/.codex/hooks.json` (your own
  hooks and other tools' hooks are left intact).
- Removes the `# BEGIN/END token-optimizer compact prompt` managed block and
  the prompt file (`~/.codex/token-optimizer/codex-compact-prompt.md`) from
  `~/.codex/config.toml`.
- Removes the `# BEGIN/END token-optimizer status line` `[tui]` block, and
  uncomments any `status_line`/`terminal_title` settings Token Optimizer
  commented out on a `--force` install. A `[tui]` header Token Optimizer
  added is dropped only if the table is left empty; user-authored `[tui]`
  content is never touched.

Add `--dry-run` to preview without writing. The uninstall is idempotent;
running it on a clean config is a no-op.

Then remove the marketplace plugin via the Codex TUI (`/plugins`) or:

```bash
codex plugin marketplace remove alexgreensh/token-optimizer
```

Codex session/trends data (`~/.codex/token-optimizer/`) is left in place by
design. To purge it too:

```bash
rm -rf ~/.codex/token-optimizer
```

### CLI commands

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py report
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py coach
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py quality current
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py dashboard
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py codex-doctor --project "$PWD"
```

## Dashboard

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py dashboard
```

### Bookmarkable URL (recommended)

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py setup-daemon
```

This installs a tiny local web server that starts at login and serves the dashboard at:

```
http://localhost:24843/token-optimizer
```

Bookmark it. It auto-updates after every session. Runs on macOS (launchd), Linux (systemd --user), and Windows (Task Scheduler). Port 24843 is Codex-specific (Claude Code uses 24842, so both can run side by side). Remove anytime with `setup-daemon --uninstall`. The URL only resolves after `setup-daemon` has been run; until then it returns ERR_CONNECTION_REFUSED.

For LAN access on a headless box, set `TOKEN_OPTIMIZER_DASHBOARD_HOST=0.0.0.0` before running `setup-daemon`. The daemon runs under the service manager with an empty environment, so the chosen host is persisted to a `dashboard-host` file and re-read at startup. The setting is per-runtime (each of Claude/Codex/Hermes/Copilot/Cursor persists its own). It is sticky across re-runs (and version-bump auto-regen) until you change it or run `setup-daemon --uninstall`. Allowed values: `127.0.0.1`, `localhost`, `0.0.0.0`. In network mode the dashboard is view-only for LAN visitors: the token endpoint is loopback-locked, so toggles work only from the machine running the daemon (LAN visitors can view but cannot fetch the token that gates mutations).

### File fallback

The dashboard file location is install-dependent. Run `TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py dashboard` and read the path on the `  Dashboard: ` line; that is the file to open. A common Codex location is `~/.codex/_backups/token-optimizer/dashboard.html` (legacy non-plugin layout), but do not assume it, read it from the command output.

Auto-refreshes via the balanced Stop hook after each session. Works without the daemon, just harder to reach.

## Feature Parity

### What's the same

These features work identically on Claude Code and Codex:

| Feature | Details |
|---------|---------|
| v6 dual-score quality scoring | Resource Health plus Session Efficiency, using context headroom and observed waste. These are resource heuristics, not measured model accuracy. |
| Quality grades | S/A/B/C/D/F grades in dashboard, coach, CLI, and status line |
| Session continuity | Checkpoints preserve decisions, files, errors, and next step across compaction and session boundaries |
| Dashboard | Single-file HTML with per-turn token breakdown, cache analysis, cost tracking, quality overlays. Codex-native paths and copy |
| Fleet Auditor | Cross-system scanning across Claude Code, Codex, and custom transcript setups. Use the OpenClaw dashboard for OpenClaw runs |
| Token Coach | Conversational coaching adapted for AGENTS.md, Codex memories, intelligence levels, reasoning effort |
| Waste detectors | 11 detectors: PDF ingestion, web search overhead, retry churn, tool cascade, looping, overpowered model, weak model, bad decomposition, wasteful thinking, output waste, cache instability |
| Cost tracking | Per-turn API-equivalent costs with GPT-5.6 Sol/Terra/Luna, GPT-5.5/5.4/5.4-Mini/5.3-Codex/5.2 pricing |
| Memory/config audit | AGENTS.md audit (vs CLAUDE.md), Codex memories audit, skills/plugin/MCP inventory |
| Setup repair | `codex-doctor` with 20 readiness checks, guided hook install, compact prompt setup |
| Zero dependencies | Pure Python stdlib. No pip install, no telemetry |

### What's different

Codex and Claude Code have different hook surfaces, so some features work differently:

| Feature | Claude Code | Codex | Why |
|---------|------------|-------|-----|
| Config file | `CLAUDE.md` | `AGENTS.md` | Different platforms |
| Memory system | `MEMORY.md` + project memory dirs | `~/.codex/memories/**/*.md` | Different storage |
| Model routing advice | Opus/Sonnet/Haiku per-agent routing | Intelligence levels (Low/Medium/High/Extra High) + model selection (GPT-5.6 Sol/Terra/Luna, GPT-5.5, 5.4, 5.4-Mini, 5.3-Codex, 5.2) | Different model families |
| Hook install | Auto via plugin, 8 hook events | `codex-install` command (global by default), native Windows/POSIX launchers | Explicit OS-specific setup |
| Compact lifecycle | PreCompact + PostCompact hooks capture/restore | PreCompact checkpoints, PostCompact refresh, SessionStart recovery | Native Codex lifecycle events |
| Tool result archive | PostToolUse archives immediately per tool call | Stop-time backfill from JSONL (balanced), or PostToolUse (telemetry profile) | Different timing |
| Dashboard refresh | SessionEnd hook + daemon at `localhost:24842` | Stop hook + daemon at `localhost:24843` | Both support bookmarkable URL via `setup-daemon` |
| Plugin install | `/plugin marketplace add alexgreensh/token-optimizer` | `codex plugin marketplace add alexgreensh/token-optimizer` | Same concept, different CLI |
| Auto-update | Claude Code marketplace auto-update | Codex marketplace `git ls-remote` on startup | Both work |

### Remaining adapter and telemetry limits

These mechanisms are not claimed as active Codex features:

| Feature | Claude Code | Codex | Blocker |
|---------|------------|-------|---------|
| Delta read substitution | PreToolUse Read returns diff instead of full file | Not active | No Codex adapter for arbitrary file-read commands/tools |
| Structure-map substitution | PreToolUse Read returns AST skeleton for re-reads | Not active | Same blocker |
| Cache-write TTL breakdowns | Full 1h/5m cache-write split visible | Cached input shown, no TTL split | Codex logs don't expose cache-write TTL fields |
| StopFailure recovery | Dedicated hook fires on crash/timeout | Stop + Interrupt checkpoints + compact prompt | No StopFailure hook in Codex |
| Skill usage telemetry | Per-skill invocation tracking from trends | Partial, limited log signals | Codex logs don't expose all skill invocation events |

## Codex Models and Pricing

Model availability, reasoning options and effective context limits come from the local Codex model catalog, including Astra, Sol, Terra, Luna, Daybreak, GPT-5.5 and Spark when available. Actual task-reported limits take precedence. Unknown prices remain unavailable.

Large transcripts are indexed incrementally from the beginning with bounded memory. Pending files are revisited even without new writes. Incomplete or malformed data is labeled explicitly. Cached input and reasoning are inclusive subsets, not added twice.

Optional API-price comparisons for supported historical models:

| Model | Input ($/1M) | Cached ($/1M) | Output ($/1M) |
|-------|-------------|---------------|---------------|
| GPT-5.6 Sol | $4.00 | $0.40 | $20.00 |
| GPT-5.6 Terra | $2.00 | $0.20 | $12.00 |
| GPT-5.6 Luna | $0.20 | $0.02 | $1.20 |
| GPT-5.5 | $5.00 | $0.50 | $30.00 |
| GPT-5.4 | $2.50 | $0.25 | $15.00 |
| GPT-5.4-Mini | $0.75 | $0.075 | $4.50 |
| GPT-5.3-Codex | $1.75 | $0.175 | $14.00 |
| GPT-5.2 | $1.75 | $0.175 | $14.00 |

Prices are API-equivalent estimates sourced from OpenAI API pricing; they do not represent ChatGPT subscription credits or plan limits. Dashboard shows per-turn costs using the model detected from session logs.

## Verify Setup

After installing or upgrading Codex support:

```bash
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py report
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py dashboard --quiet
TOKEN_OPTIMIZER_RUNTIME=codex python3 skills/token-optimizer/scripts/measure.py codex-doctor --project "$PWD"
```

Expected health is `codex-doctor` with `0 FAIL`.

## Requirements

- Python 3.9+
- Codex CLI or Codex Desktop
- macOS, Linux, or Windows
- Zero runtime dependencies (pure Python stdlib)

## License

Same as the parent project: [PolyForm Noncommercial 1.0.0](../LICENSE).

---

Created by [Alex Greenshpun](https://linkedin.com/in/alexgreensh).
