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

## Weather: a forecast model, and an honest negative result

Settled-market calibration showed the fade-longshot edge is cleanest in
**weather** (thin tails, no blow-up risk) — so we built a real forecast model to
try to turn the blind bias into model-driven *value* betting:

- `weather/` pulls live daily-high forecasts from **Open-Meteo** (free, no key)
  across three numerical models (GFS, ECMWF, ICON), using the model mean as the
  estimate and the model spread (floored) as uncertainty.
- Each Kalshi temperature range is priced with a Normal CDF; probabilities over
  an event partition sum to 1 and peak at the forecast. The model is
  **well-calibrated** and matches the market on central ranges.

Run it: `python -m kalshi_bot weather`.

**But the live test did not find a real edge — and that is the important
result.** Two things surfaced:

1. **Same-day markets:** the apparent "edges" were huge (60–90c) because the
   market sees *intraday observations* the morning forecast does not — by
   afternoon the day's high is largely realized. The market isn't mispriced; our
   forecast is simply less informed. (Two bugs were fixed along the way: the
   weather date must come from the *ticker*, not the close time which is the next
   morning; and a liquidity gate is required so stale one-sided quotes don't
   produce phantom edges.)
2. **At 1-day lead**, model and market still disagree — but **disagreement is not
   an edge.** The market is a liquid aggregator that also uses public forecasts.
   A naive `Normal(3-model mean, sigma=2.5)` may well be *worse* than the market
   (wrong sigma, station microclimate, model bias). We have **no evidence the
   model beats the market**, so these are not tradeable signals.

**First verdict:** using the forecast to *beat the market wholesale* does not
work — the market sees the same public forecasts. So we ran the proper
historical backtest and found the right way to use it.

## The double edge: forecast-filtered fade at 72h lead

The fix is to use the forecast as a **filter on fade-longshot**, not as an
oracle — and to do it at a **longer lead** where the bias is strongest. We
backtested this on **315 settled weather markets** using, for each, the forecast
*as it stood 72h before* (Open-Meteo previous-runs archive — no look-ahead), the
market quote at that same lead, and the realized outcome:

| Strategy | n | ROI | win | t-stat |
|----------|---|-----|-----|--------|
| Unfiltered fade (YES ask ≤ 15¢) | 158 | **−1.0%** | 94% | −0.5 |
| Forecast-filtered (fade only if model ≤ 7%) | 38 | **+4.3%** | 100% | +7.0 |

The mechanism is visible and hard to fake: the filter **excluded 120 longshots,
of which 9 actually resolved YES** — exactly the cheap-but-live contracts a blind
fade would have lost ~95¢ on. At 72h lead, blind fading is breakeven-to-negative
because some longshots are genuinely live that far out; the forecast removes
precisely those, turning −1% into +4.3%.

This is the synthesis: a **historical** behavioral edge (favorite-longshot bias)
gated by a **live** information edge (the forecast), each covering the other's
weakness. Reproduce with `python -m kalshi_bot weather-backtest --lead-days 3`.

### Caveats (unchanged discipline)

- **Clustered outcomes** within city-days inflate the t-stat; treat it as
  directional. Effective sample is smaller than 315.
- **Threshold selection** (5–7%) is a mild overfitting risk, though results are
  robust across that band and the mechanism (drop live longshots) is principled.
- **Sigma/bias** are calibrated on the same period (mild look-ahead on the noise
  model, not the forecast itself).
- **Passive entry** assumed; crossing the spread lowers it. Execution matters.

So: a **real, mechanistically-sound edge** at 72h lead — the strongest weather
result so far. Next step before real money: forward-test it live (open
forecast-filtered fades at 72h lead, settle over the following days) to confirm
out-of-sample, exactly as we are doing for the election fade.

## Polymarket global weather (read-only; promising but unconfirmed)

Polymarket runs daily temperature markets for ~23 world cities (Seoul, Paris,
Tokyo, São Paulo, …) — far more than Kalshi's 7 US cities — all forecastable with
Open-Meteo. Trading needs crypto (out of scope), so this is read-only / paper.

Backtesting forecast-value betting (buy the side the forecast favours when it
differs from the market by an edge, 2-day lead) over **1,500 settled events / 23
cities / 339 sampled bins**:

| Edge | n | ROI | win | t-stat |
|------|---|-----|-----|--------|
| ≥3¢ | 261 | +6.5% | 33% | 1.06 |
| ≥8¢ | 158 | +8.3% | 41% | 1.25 |
| ≥15¢ | 71 | +5.8% | 46% | 0.56 |

ROI is **consistently positive (+5–8%) at every threshold**, but t-stats hover at
~1 — **not statistically significant**. These are high-variance value bets (33–46%
win rate: a losing 25% YES that pays 4× when right), so confirming a ~+6% edge to
t>2 would need thousands of samples. **Verdict: promising and consistent, but
unconfirmed.** Polymarket appears more efficient than Kalshi (sharper money); a
modest forecast edge may survive, but the evidence isn't conclusive. The clean
way to settle it is a paper forward test (no crypto needed) accumulating live
out-of-sample results over time.
