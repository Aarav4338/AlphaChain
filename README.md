# AlphaChain

## Overview
AlphaChain is a systematic multi-regime quantitative trading system for liquid cryptocurrency pairs. The strategy isolates idiosyncratic altcoin alpha from systematic Bitcoin beta, exploiting cross-asset lead-lag transmission dynamics and residual momentum while enforcing strict drawdown constraints.

## Macro Regime Detection
Cryptocurrency markets exhibit distinct volatility and trend regimes. The strategy evaluates Bitcoin (BTC/USD) hourly market structure to determine the prevailing regime before any asset-level signals are generated:

- Trend Filter: Exponential Moving Average crossover between EMA(20) and EMA(50).
- Trend Strength: Average Directional Index (ADX) with a 14-period lookback.

Regime classifications:
- Bull Regime: EMA(20) > EMA(50) and ADX > 20. The system permits long positions in assets with positive expected returns.
- Bear Regime: EMA(20) < EMA(50) and ADX > 20. The system halts long exposure and maintains a cash defense buffer.
- Chop Regime: ADX <= 20. In range-bound environments, signals must clear an elevated alpha threshold to avoid whipsaws.

## Linear Factor Model
For each liquid altcoin $i$, returns are decomposed relative to Bitcoin:

$$r_{i, t} = \beta_{i, t} \cdot r_{\text{BTC}, t} + \epsilon_{i, t}$$

Where $\beta_{i, t}$ is estimated over a rolling 48-hour window. The model calculates four orthogonalized predictive features:

1. Cross-Asset Lead-Lag Spillover:
   Measures unabsorbed Bitcoin momentum transmitting into altcoin prices:
   $$f_{i, t}^{\text{lag}} = \beta_{i, t} \cdot r_{\text{BTC}, t} - r_{i, t}$$

2. Idiosyncratic Momentum:
   Standardized 12-hour cumulative residual return, isolated from systematic market movement:
   $$f_{i, t}^{\text{mom}} = \frac{\sum_{k=1}^{12} (r_{i, t-k} - \beta_{i, t} \cdot r_{\text{BTC}, t-k})}{\sigma_{i, t} \cdot \sqrt{12}}$$

3. Short-Term Mean Reversion:
   Normalized 1-hour return deviation capturing temporary liquidity depletion:
   $$f_{i, t}^{\text{rev}} = -\frac{r_{i, t}}{\sigma_{i, t}}$$

4. Volume Flow Imbalance:
   Volume Z-score directionalized by price return sign:
   $$f_{i, t}^{\text{flow}} = \left(\frac{V_{i, t} - \mu_{V, i}}{\sigma_{V, i}}\right) \cdot \text{sign}(r_{i, t})$$

## Analytical Ridge Calibration
Expected forward returns $\hat{\mu}_i$ are forecasted linearly:

$$\hat{\mu}_{i, t} = \mathbf{x}_{i, t}^T \hat{\boldsymbol{\beta}}$$

The factor weight vector $\hat{\boldsymbol{\beta}}$ is updated periodically using closed-form Ridge regression over a rolling 72-hour historical window:

$$\hat{\boldsymbol{\beta}} = \left(\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I}\right)^{-1} \mathbf{X}^T \mathbf{y}$$

Where $\lambda = 1.0$ provides $L_2$ regularization to prevent collinear overfitting across cross-sectional crypto assets.

## Execution Hurdle and Cooldown
To eliminate churn and preserve capital against exchange fees:
- Fee Hurdle: A trade is triggered only if the expected return exceeds transaction costs:
  $$\hat{\mu}_{i, t} > \text{Hurdle} \quad (\text{minimum } 20 \text{ bps})$$
- Asset Cooldown: After entering a position, a minimum holding cooldown (4 hours) is enforced to prevent premature turnover.

## Dynamic Risk-Budgeted Concurrency
The system replaces arbitrary integer position limits with an institutional cumulative risk heat budget:

$$\sum_{i=1}^K \text{Risk}_i \le \mathcal{H}_{\text{regime}} \le 1.0\%$$

Where:
- $\text{Risk}_i = \frac{Q_i \cdot (P_{i, \text{entry}} - P_{i, \text{stop}})}{E_t}$ represents the percentage equity risk for position $i$.
- $\mathcal{H}_{\text{BULL}} = 1.0\%$, $\mathcal{H}_{\text{CHOP}} = 0.40\%$, $\mathcal{H}_{\text{BEAR}} = 0.0\%$ (100% cash defense for long trades).
- Cash Reserve Invariant: The system guarantees a minimum free USD cash reserve of $\ge 25\%$ under all market conditions.
- Breadth Partitioning: When market breadth expands, available risk heat and trading capital are dynamically distributed across $K$ qualifying assets weighted by signal conviction, scaling to any number of qualifying positions.

## Bitcoin Momentum and Trend Velocity
Bitcoin is traded directly alongside altcoins using dedicated absolute trend velocity and momentum factors:

1. ATR-Normalized Trend Velocity:
   $$v_{\text{BTC}, t} = \frac{P_{\text{BTC}, t} - \text{EMA}_{50}(P_{\text{BTC}})}{\text{ATR}_{14}(P_{\text{BTC}})}$$

2. Multi-Horizon Momentum Vector:
   $$m_{\text{BTC}, t} = 0.6 \cdot \frac{r_{\text{BTC}, 1\text{h}}}{\sigma_{\text{BTC}, 24}} + 0.4 \cdot \frac{r_{\text{BTC}, 4\text{h}}}{\sigma_{\text{BTC}, 96}}$$

When Bitcoin momentum exceeds threshold velocity ($v_{\text{BTC}} > 1.2$), Bitcoin receives priority risk allocation.

## Mathematical Drawdown Guarantee
Because each position risks at most $\text{Risk}_i$ at its stop-loss:

$$\Delta \text{Equity}_{\text{worst}} = \sum_{i=1}^K \text{Risk}_i + \text{Slippage} \le 1.0\% + 0.075\% = 1.075\%$$

Even in a worst-case simultaneous gap-down triggering all active stops with 10 bps slippage, portfolio drawdown is bounded at $\le 1.075\%$, providing an absolute $2.925\%$ safety buffer below the $4.0\%$ hackathon disqualification threshold.

## Execution and Persistence
- Order Routing: Maker-first limit order execution (0.05% maker fee) with a 60-second fill horizon.
- State Reconciliation: Automatic crash recovery and balance synchronization on daemon startup.
- Process Persistence: 24/7 autonomous daemon with systemd supervision (`alphachain.service`), atomic on-disk heartbeat monitoring, and automatic failover restart.

