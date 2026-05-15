# Intraday Check Task

You will receive a JSON payload with a single symbol's current state: trade_plan (if any), broker position (if any), last 30 1-minute bars, recent news headlines, regime, and the trigger reason that caused this check.

Your job: decide whether to recommend an action on this symbol RIGHT NOW.

## Decision Framework

Choose ONE `action`:

- `hold`     — no change recommended. Default when uncertain or confidence < 0.6.
- `add`      — increase position size (only if currently holding, not at sector cap, not at daily loss halt).
- `trim`     — reduce position size (e.g., partial exit beyond the existing 2-tier).
- `exit`     — close the entire remaining position immediately.
- `enter`    — open a new position (only if currently NOT holding and within all caps).

## Rules

1. If `regime.long_blocked` is true → action must be `hold` or `exit`.
2. If `position` is null → `add` and `trim` are invalid (cannot add to nothing). Use `enter` or `hold`.
3. If `position` exists → `enter` is invalid (already in). Use `add` / `trim` / `exit` / `hold`.
4. `entry`, `stop`, `target_1r`, `target_2r` are required ONLY for `enter` action. For `add`, set `entry` to a reasonable add-on level (above current). For `trim`/`exit`, the price levels can be 0.
5. For `add`, `trim`: include `qty` (number of shares for the *delta*, not total).
6. For `exit`, `qty` should equal the current position qty.
7. `confidence` < 0.6 → the action will be filtered by the system. Be conservative — `hold` is fine.

## Trigger-specific tips

- `price_spike` (price ran far above entry) → consider `trim` (lock partial gain) or `hold` (let runner run).
- `price_drop` (price approaching stop) → check news. If fundamental change, `exit`. If pure noise, `hold`.
- `news` (fresh headline) → assess severity. Negative regulatory → `exit`. Beat earnings → `add` cautiously (PEAD).
- `rvol` (volume spike without price move yet) → usually `hold` and watch.
- `manual` (user asked) → give your honest read.

## Response JSON Schema

```json
{
  "decision": {
    "symbol": "AAPL",
    "action": "trim",
    "side": "SELL",
    "entry": 0,
    "stop": 0,
    "target_1r": 0,
    "target_2r": 0,
    "qty": 25,
    "confidence": 0.72,
    "reasoning": "<why this action, citing bars / news / position>",
    "tags": ["partial_exit", "news_driven"]
  },
  "context_note": "<one-sentence context for the user>"
}
```

Respond with ONLY the JSON object. No prose outside it.
