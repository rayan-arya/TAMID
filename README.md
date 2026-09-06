# TAMID Quantitative Research

Quantitative work from my time as a **Quantitative Analyst with the TAMID Group** at the University of Miami. Equity analysis, options pricing, strategy backtesting, and a live competition trading bot.

---

## `CTC/`: Cornell Trading Competition bot

A market-making bot that trades against the competition's REST exchange API.

- **Quoting.** Maintains a fair value per symbol, drifts it on a random walk between ticks, and posts a two-sided limit quote around it with a randomized spread, so the bot is not trivially predictable to anything watching the book.
- **Risk.** `within_limits` checks every proposed order batch against current positions before anything is sent, so a fill can never push the book past the competition's position limits.
- **PnL.** Tracks average cost basis per symbol and accumulates realized PnL as positions close, then marks the remainder to mid for an unrealized figure.
- **Failing fast.** A pre-flight check hits `/api/v1/symbols` before the loop starts, because discovering a bad base URL or API key thirty seconds into a timed round is an expensive way to learn.
- **Offline mode.** `get_quotes` has a mocked implementation alongside the live one, so the strategy loop can be developed and tested without the exchange being up.

`ogbot.py` is the earlier, simpler version, kept for comparison. Credentials come from `CTC_API_KEY` and `CTC_API_URL` in the environment, never from the source.

```bash
export CTC_API_KEY=...
python CTC/bot.py --symbol AAPL --qty 10
```

## `SemesterProj2025/`: probability cone strategy

A volatility-band strategy and its backtest. For a given reference date, it estimates annualized volatility from a trailing window of returns and projects upper and lower price bands out over a horizon:

```
band = P0 * exp( ± k * sigma * sqrt(t / 252) )
```

The backtest walks the series day by day, opens and closes positions as price crosses the cone, and records the trades. Parameterized on window length and the sigma multiplier `k`, so both can be swept. Results push out to Excel through `xlwings` for the parts of the analysis that live in a spreadsheet.

## `hw0&1/`, `HW02/`: coursework

Chapter exercises from the TAMID quantitative track. `HW02` is the options work: Black-Scholes pricing and the full Greek set written out from the calculus rather than imported, plus an implied-volatility solver using Newton-Raphson with vega as the derivative, guarded against near-zero and NaN vega and clamped to a sane sigma range.

---

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install pandas numpy scipy matplotlib yfinance requests xlwings jupyter
jupyter lab
```

## Note on history

This repository previously tracked its entire virtualenv: 20,018 files, which is almost certainly why it never pushed successfully. The venv is now untracked and ignored. Older commits still contain it, so a full clone is heavier than the working tree suggests.
