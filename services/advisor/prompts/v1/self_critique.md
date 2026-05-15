# Weekly Self-Critique Task

You are reviewing your own recommendations from the past week. You will receive:
1. Aggregate metrics (win rate, avg return, Sharpe, etc.)
2. A list of recent recommendation samples with their actual 5-day outcomes.

Produce structured feedback for prompt improvement.

## Response JSON Schema

```json
{
  "summary": "<one paragraph: how did the advisor perform overall, weakest area>",
  "patterns_found": [
    {
      "pattern": "<short description of a recurring failure or success pattern>",
      "evidence_ids": [<sample ids>],
      "suggested_prompt_change": "<concrete edit to apply to morning_brief.md or intraday_check.md>"
    }
  ],
  "calibration_note": "<is confidence well-calibrated? are 70%+ recs winning, are <60% losing?>",
  "next_week_focus": "<one specific behavior to test next week>"
}
```

Be concrete and self-critical. If a recommendation failed because the price geometry was technically valid but the catalyst was actually a known earnings sell-the-news pattern, say so. If reasoning over-relied on score_breakdown and ignored news, say so.

Respond with ONLY the JSON object (optionally wrapped in ```json fences).
