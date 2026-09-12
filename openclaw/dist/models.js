"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.EXPENSIVE_MODELS = void 0;
exports.totalTokens = totalTokens;
exports.billableTokens = billableTokens;
function totalTokens(t) {
    return t.input + t.output + t.cacheRead + t.cacheWrite;
}
/**
 * Billable token basis = fresh_input + cache_create + output, EXCLUDING
 * cache_read. Mirrors measure.py's `model_usage` (line 9019: `billable = u["inp"]
 * + u["cc"] + u["out"]`), which is the canonical SPENT / model_mix basis.
 * cache_read is a discounted reuse class, not a fresh billable unit, so it is
 * excluded from the "what you spent" total to match the HTML dashboards.
 */
function billableTokens(t) {
    return t.input + t.cacheWrite + t.output;
}
/** Models considered expensive (should not be used for heartbeat/cron tasks). */
exports.EXPENSIVE_MODELS = new Set([
    "fable", "opus", "sonnet", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.4", "gpt-5.2", "gpt-5", "gpt-4.1",
    "gpt-4o", "o3", "o3-pro", "gemini-3-pro", "gemini-2.5-pro", "grok-4",
]);
//# sourceMappingURL=models.js.map