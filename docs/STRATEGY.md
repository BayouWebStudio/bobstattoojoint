# Finding a strategy with a real edge

This documents the search for a tradeable edge on Kalshi, what was found, and —
importantly — the limits of that edge. Everything here was measured on **real,
public Kalshi data** and is reproducible with `python -m kalshi_bot calibrate`.

## Hypothesis (chosen before looking at data)

The **favorite-longshot bias (FLB)** is one of the most replicated anomalies in
betting and prediction markets: low-probability "longshot" contracts are
systematically *overpriced*, and heavy favorites slightly *underpriced*. We test
whether it holds on Kalshi and, if so, whether it is profitable after costs.

Choosing a hypothesis with strong prior evidence — rather than data-dredging for
any pattern — is what keeps this from being overfitting.

## Method

1. Collect **settled** markets (known YES/NO outcome) from liquid, multi-candidate
   series (elections, primaries, etc.) that span the full 1–99¢ range.
2. For each, take the **mid-life** quote from daily candlesticks as the entry
   price (avoiding the price's convergence to 0/1 near settlement).
3. Build a **calibration table**: does a contract priced at X% resolve YES X% of
   the time?
4. **Backtest** buying NO on YES longshots and holding to settlement, with
   Kalshi's real fee schedule (`ceil(0.07·C·P·(1−P))`).

## Results (108 settled markets, a representative run)

Calibration — realized YES frequency vs. the implied price:

| Entry band | n  | implied | realized | gap   |
|------------|----|---------|----------|-------|
| 0–9¢       | 68 | 5%      | 0.0%     | −5.0  |
| 10–19¢     | 15 | 15%     | 6.7%     | −8.3  |
| 80–89¢     | 5  | 85%     | 80.0%    | −5.0  |
| 90–99¢     | 13 | 95%     | 100.0%   | +5.0  |

The longshot bands resolve YES **less** often than priced (overpriced); the top
favorite band resolves **more** often (underpriced). Textbook FLB.

Fade-longshot backtest (buy NO on YES ask ≤ 15¢, hold to settlement, after fees):

| Entry model        | ROI    | EV / market | win rate | t-stat |
|--------------------|--------|-------------|----------|--------|
| Passive (mid)      | +3.5%  | +3.36¢      | ~100%    | +8.7   |
| Aggressive (cross) | +2.5%  | +2.44¢      | ~98%     | +7.9   |

**The edge is real but execution-sensitive.** Crossing the spread on every entry
nearly halves it; with the spread it's ~+2.5%, passive ~+3.5%. The deeper the
longshot (≤10–15¢), the cleaner the edge; by ≤20¢ it fades into noise.

## Honest caveats

- **Correlated outcomes.** The markets cluster within a handful of events (many
  candidates per election). The effective independent sample is far smaller than
  the market count, so the t-stats **overstate** significance. Treat them as
  directional, not as a 99.9% confidence claim.
- **Skew / rare large losses.** You win ~3–5¢ on ~98% of trades and lose ~95¢ on
  the rare longshot that hits. EV is positive, but a single unexpected winner
  wipes out many small gains. **Size positions small** and diversify across
  uncorrelated events.
- **Capacity.** Deep-longshot books are thin. The edge exists at small size; it
  does not scale to large capital.
- **Survivorship / data coverage.** Only markets with enough candlestick history
  enter the sample, tilting toward more-liquid contracts.

## What this means

The FLB is a *behavioral* edge, not a free arbitrage, which is why it persists
rather than being instantly competed away. It is modest (~2–4% ROI), risky
(skewed), and capacity-limited — a real edge, but one to deploy with discipline:
passive entry, small size, broad diversification, and a hard loss cap.

It is implemented as `strategy/fade_longshot.py` and can be paper-traded through
the engine like any other strategy. Reproduce the analysis with:

```bash
python -m kalshi_bot calibrate --max-yes-ask 15 --entry mid
```
