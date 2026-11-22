# Rule F1 timing note (docs/MEME.md section 16.1 item 6)

Date: 2026-11-22. Written before any real price series was forecast; the only series read so far are the synthetic ones below.

- Run: `research/forecast/timing.py`, 50 synthetic random walks of 288 steps (seed 16), batch of one, 48-step horizon,
  quantiles 0.2 and 0.5, `amazon/chronos-2` at the revision in `MODEL_REVISION`, CPU (12 torch threads), after one warm-up call.
  Other jobs (a CoinGecko download and a GeckoTerminal candle download, both light) were running.
- s (median seconds per forecast) = 0.0284 (mean 0.0285, max 0.0312).
- N = tokens in the input copy that are at least 24 h old x 7 days x 96 = 442 x 7 x 96 = 297,024. The 442 is the count of tokens in the
  copied `discoveries.jsonl` whose first sighting had `age_h` >= 24 (a token count only, no price). 232 of them are fetched
  (age >= 24 h and max liquidity >= $25k); the rule uses the larger 442 on purpose.
- s x N = 8,443 s = 2.3 h, at or under the 12 h cap.

**Decision: decision points every 15 minutes, for F1, P0-eligible and C-persist.**

decision_step_minutes: 15
