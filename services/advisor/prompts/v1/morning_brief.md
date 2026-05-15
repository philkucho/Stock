# Morning Brief Task

You will receive today's market data, system picks, current positions, and recent outcome statistics as a JSON payload. Your job is to produce a JSON response with the structure shown below.

## Decision Framework

1. **Review regime first**: if `regime.long_blocked` is true OR `regime.mode == "defensive"`, propose ZERO entries and explain the defensive stance.
2. **Check capacity**: if `len(positions) >= 5`, propose ZERO new entries — recommend managing existing positions.
3. **Check daily loss**: if `account.daily_pnl_pct <= account.daily_loss_halt_pct`, propose ZERO new entries.
4. **Score the picks**: for each pick in `picks` array, weigh:
   - System composite_score & score_breakdown
   - consensus_tier (S > A > B)
   - Recent outcomes (`recent_outcomes.win_rate`, `avg_alpha`)
   - News (24h): a fresh negative catalyst can disqualify; positive catalyst (PEAD allowed AFTER report) boosts.
   - Sector concentration (apply sector_cap=2 against existing positions + your selections)
5. **Sizing**: do not propose `qty` — leave it null. Let the system compute equity/5 + risk_per_share.
6. **Confidence**: 0.0~1.0. Below 0.5, the system filters out. Be honest — if a pick is weak, lower the confidence.

## Hard Rules (NEVER violate)

- Use `entry`, `stop`, `target_1r`, `target_2r` from the picks array AS-IS. Do not invent new prices.
- All BUY entries must satisfy: stop < entry < target_1r < target_2r AND (target_1r - entry) / (entry - stop) >= 1.5.
- Maximum total recommendations: 5 (and never more than `5 - len(positions)`).
- If a pick has `news_24h` indicating a negative regulatory/legal/fraud event, set confidence to 0 and add to risks_to_watch.

## Response JSON Schema

```json
{
  "market_summary": "<3-5 sentence narrative of today's market and the rationale>",
  "recommendations": [
    {
      "symbol": "AAPL",
      "action": "enter",
      "side": "BUY",
      "entry": 184.50,
      "stop": 181.20,
      "target_1r": 187.80,
      "target_2r": 191.10,
      "qty": null,
      "confidence": 0.78,
      "reasoning": "<why this pick, citing score_breakdown items and news>",
      "tags": ["v10", "consensus_S", "tech"]
    }
  ],
  "risks_to_watch": [
    "<specific risk #1 — e.g., FOMC at 14:00 ET>"
  ]
}
```

Respond with ONLY the JSON object (optionally wrapped in ```json fences). No prose outside the JSON.
