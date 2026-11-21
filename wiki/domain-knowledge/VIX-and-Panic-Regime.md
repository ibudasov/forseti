# VIX and Panic Regime (`vix_panic_regime`)

## What VIX is

**VIX** (the **CBOE Volatility Index**, ticker `^VIX`) is the stock market's **"fear gauge."**

- It measures the market's expectation of **30-day annualized volatility** for the **S&P 500**, calculated from S&P 500 index option prices.
- Values represent **annualized percentage points**:

| VIX range | Market regime |
| --- | --- |
| `< 20` | Calm, low-volatility market |
| `20 – 30` | Elevated uncertainty / choppy market |
| `> 30` | High stress / panic regime (sharp selloffs, wide swings, macro shocks) |

---

## How VIX impacts Forseti's pipeline logic

VIX is a **market-wide** input (not per-ticker). Forseti ingests daily `^VIX` closes via `yfinance` (`app.ingestion.run --source vix` / `forseti-daily-refresh`) and evaluates it across the 6-step deterministic pipeline and the ADK agent layer:

| VIX value | Stage impacted | Exact effect on Forseti |
| --- | --- | --- |
| `VIX > 30` | **Step 2: Hard Vetoes** (`app/services/vetoes.py`) | Triggers `vix_panic_regime`. **Short-circuits the checklist and risk math**: forces `decision = "no_trade"`, `confidence = 0.0`, `checklist_score = null`, and `null` trade levels (`entry_range`, `stop_loss`, `take_profit`, `position_size_eur`). |
| `25 <= VIX <= 30` | **Step 3: Checklist Scoring** (`app/services/checklist.py`) | No veto, but **does not earn** the `+1` calm-market point (max effective score becomes `10/11` instead of `11/11`). |
| `VIX < 25` | **Step 3: Checklist Scoring** | Awards **`+1` point** toward the 11-point checklist (`score >= 8` -> `trade`, `5–7` -> `watchlist`, `<= 4` -> `no_trade`). |
| `VIX` missing (`None`) | **Step 2 & Step 3** | Does not trigger `vix_panic_regime` (`vix_close is not None` guard) and scores `0` for the `VIX < 25` checklist rule. |

### Downstream impact on Confidence, Risk Math, and Agents (ADR 008)

1. **Checklist & Confidence:** When `VIX` is between `25` and `30`, losing the `+1` checklist point can push a borderline `score = 8` (`trade`) down to `7` (`watchlist`) or `5` (`watchlist`) down to `4` (`no_trade`), and lowers baseline `confidence = (score / 11) - 0.05 * warnings`.
2. **Risk Math:** When `VIX > 30`, `vix_panic_regime` skips Step 5 (Risk Math) completely—no entry, stop-loss, take-profit, or position size is calculated.
3. **Agentic Layer (ADR 008):** Because agents are **downgrade-only**:
   - When `VIX > 30` forces `no_trade` (`0.0`), no agent (`fundamental_analyst`, `technical_analyst`, `decision_synthesizer`, `critic_guardrail`) can upgrade it.
   - `technical_analyst` skips its LLM call with `hard_blocker_present` when a hard veto is active.
   - `decision_synthesizer` and `critic_guardrail` preserve the deterministic `no_trade` outcome and `vix_panic_regime` reason.

---

## Why `/screening` can show all `no_trade`

Because `^VIX` is shared across all 35 active tickers, whenever the latest ingested `^VIX` close exceeds `30`, `GET /screening` returns `trade: 0`, `watchlist: 0`, and `no_trade: 35` with `rule_id: "vix_panic_regime"`. This is intentional capital-preservation behavior, not an ingestion or API failure.
