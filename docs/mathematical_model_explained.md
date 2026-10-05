# 📐 Mathematical Model: Formal Derivation, Assumptions, and Dynamic Risk Architecture

**Project**: AlphaChain Autonomous Quantitative Trading System (formerly CarrotQuant prototype)  
**Target Event**: APAC Quant Trading Hackathon 2026 (Sponsored by Susquehanna & AWS)  
**Implementation Files**:
- Strategy & Alpha Engine: [`strategy/linear_factor_model.py`](file:///c:/Projects/roostohacakthon/strategy/linear_factor_model.py), [`strategy/multi_regime_alpha.py`](file:///c:/Projects/roostohacakthon/strategy/multi_regime_alpha.py)
- Dynamic Risk & Capital Engine: [`risk/position_sizer.py`](file:///c:/Projects/roostohacakthon/risk/position_sizer.py), [`risk/stop_loss_manager.py`](file:///c:/Projects/roostohacakthon/risk/stop_loss_manager.py)
- Hardware Circuit Breaker: [`risk/circuit_breaker.py`](file:///c:/Projects/roostohacakthon/risk/circuit_breaker.py)

---

## 📑 Table of Contents
1. [The Real-World Context & Competition Constraints](#1-the-real-world-context--competition-constraints)
2. [Market Microstructure Assumptions (Why Crypto Behaves This Way)](#2-market-microstructure-assumptions-why-crypto-behaves-this-way)
3. [Complete Mathematical Formulation & Derivations](#3-complete-mathematical-formulation--derivations)
   - [3.1. Log Returns & Conditional Volatility](#31-log-returns--conditional-volatility)
   - [3.2. Bitcoin Beta Decomposition](#32-bitcoin-beta-decomposition)
   - [3.3. The 4 Orthogonalized Factors](#33-the-4-orthogonalized-factors)
   - [3.4. Cross-Sectional Normalization](#34-cross-sectional-normalization)
   - [3.5. Forward Return Target](#35-forward-return-target)
   - [3.6. Analytical Ridge Calibration (Closed-Form Solution)](#36-analytical-ridge-calibration-closed-form-solution)
   - [3.7. The Cost-Penalized Selection Hurdle](#37-the-cost-penalized-selection-hurdle)
   - [3.8. Noise-Resilient Asymmetric Risk Exits](#38-noise-resilient-asymmetric-risk-exits)
   - [3.9. Dedicated Bitcoin Alpha Engine: ATR-Normalized Trend Velocity & Momentum Vector](#39-dedicated-bitcoin-alpha-engine-atr-normalized-trend-velocity--momentum-vector)
   - [3.10. Dynamic Portfolio Concurrency & Cumulative Risk Heat Partitioning (R2 Dynamic Model)](#310-dynamic-portfolio-concurrency--cumulative-risk-heat-partitioning-r2-dynamic-model)
   - [3.11. Mathematical Drawdown Guarantee: Immunity to the 4.0% Disqualification Ceiling](#311-mathematical-drawdown-guarantee-immunity-to-the-40-disqualification-ceiling)
4. [How We Came Up With This Model (The Reasoning Trail)](#4-how-we-came-up-with-this-model-the-reasoning-trail)
5. [Why This Model and Not Others? (Comparative Analysis vs Top 10 Competitor Archetypes)](#5-why-this-model-and-not-others-comparative-analysis-vs-top-10-competitor-archetypes)
   - [vs. Generic Technical Indicators (RSI, MACD, EMA Cross)](#51-vs-generic-technical-indicators-rsi-macd-ema-cross)
   - [vs. Pure Statistical Arbitrage / Cointegration Pairs Trading](#52-vs-pure-statistical-arbitrage--cointegration-pairs-trading)
   - [vs. High-Frequency Market Making / Order-Queue Tick Scalping](#53-vs-high-frequency-market-making--order-queue-tick-scalping)
   - [vs. Deep Learning / LSTMs / Neural Networks](#54-vs-deep-learning--lstms--neural-networks)
   - [vs. Martingale / Grid Accumulators & Unhedged Concentrated Beta Holders](#55-vs-martingale--grid-accumulators--unhedged-concentrated-beta-holders)
6. [Failure Modes & The Escalation Path to Non-Linear ML](#6-failure-modes--the-escalation-path-to-non-linear-ml)
7. [Production Code Mapping & Invariant Verification Matrix](#7-production-code-mapping--invariant-verification-matrix)

---

## 1. The Real-World Context & Competition Constraints

To design an effective quantitative trading model, we must first mathematically define the operating boundaries of the competition:

| Parameter | Constraint Value | Mathematical Impact on Strategy |
| :--- | :--- | :--- |
| **Duration** | **14–15 Days** | Cannot use long-horizon macro trends (30-day moving averages have no statistical power). Trades must capture **4-hour to 24-hour alpha cycles**. |
| **Universe** | **12 Curated Liquid Pairs** | Cross-sectional universe is small ($N=12$). Covariance matrices are low-dimensional; factor ranks must be robust to small sample sizes. |
| **Capital & Leverage** | **\$100,000 USD / 1x Spot Long & 1x Short** | No leverage allowed. Sizing cannot rely on margin amplification; returns must come from high-quality directional bets. |
| **Transaction Fees** | **0.05% Maker / 0.10% Taker** | Round-trip friction is $\approx 0.10\%\text{--}0.20\%$. Any strategy executing $> 5$ trades/day will suffer severe fee drag unless expected return $\hat{\mu} \gg \text{Fee}$. |
| **API Rate Limit** | **$\le 30$ calls/minute** | High-Frequency Trading (sub-second order cancel/replace) is physically impossible. Model must rebalance on discrete hourly intervals. |
| **Evaluation Formula** | $\mathbf{0.4 \times \text{Sortino} + 0.3 \times \text{Sharpe} + 0.3 \times \text{Calmar}}$ | Downside deviation and drawdowns are penalized twice as heavily as upside volatility ($70\%$ downside weight). |
| **Drawdown Ceiling** | **$\ge 4.0\%$ = Disqualification** | Absolute hard stop. Strategy must mathematically guarantee drawdown $< 4.0\%$ under all adverse gap-shock scenarios. |

---

## 2. Market Microstructure Assumptions (Why Crypto Behaves This Way)

A quantitative model is only as good as its underlying economic assumptions. We make **four explicit assumptions** about crypto market microstructure:

### Assumption 1: Bitcoin is the Systematic Market Factor
* In traditional equity markets, the Fama-French market factor ($R_m - R_f$) explains market movements. In crypto, **Bitcoin ($\text{BTC}$) is the market factor**.
* Over $65\%$ of daily altcoin variance is directly explained by Bitcoin's price movement.
* **Deduction**: If an algorithm buys an altcoin simply because it went up, it is not generating alpha; it is taking on unhedged Bitcoin beta. True idiosyncratic alpha must be extracted from the residual:
  $$\epsilon_{i, t} = r_{i, t} - \beta_{i, t} \cdot r_{\text{BTC}, t}$$

### Assumption 2: Cross-Asset Information Transmission Lag (Lead-Lag)
* When institutional capital flows into or out of crypto, it executes on Bitcoin first (deepest liquidity, CME futures, ETF flows).
* Top-tier majors (`ETH`, `SOL`) react within minutes.
* Second-tier liquid altcoins (`NEAR`, `SUI`, `APT`, `DOGE`) experience an **information diffusion lag** of $15\text{ to }60\text{ minutes}$ as retail and secondary market makers re-hedge.
* **Deduction**: If Bitcoin surges in period $t$, but Altcoin $i$ has not yet matched its beta-expected move, there is positive latent pressure on Altcoin $i$ in period $t+1$.

### Assumption 3: Horizon-Dependent Autocorrelation
* At the **1-hour horizon**, price returns exhibit **negative autocorrelation** (mean reversion) due to temporary order-book liquidity depletion and bid-ask bounce.
* At the **4-hour to 24-hour horizon**, returns exhibit **positive autocorrelation** (momentum) due to retail FOMO, trend herding, and liquidation cascades.
* **Deduction**: The model must capture short-term reversal at $t-1$ while capturing medium-term momentum over $t-12$ to $t-1$.

### Assumption 4: The Transaction Cost Hurdle
* Because exchange fees are fixed ($0.05\%\text{--}0.10\%$), small statistical predictions (e.g. expected gain $+0.05\%$) have **negative expected value** after commissions.
* **Deduction**: Alpha signals must be sparse. The model should only allocate capital when the predicted return decisively exceeds the transaction friction threshold ($|\hat{\mu}_i| > c_{\text{hurdle}}$).

---

## 3. Complete Mathematical Formulation & Derivations

### 3.1. Log Returns & Conditional Volatility
Let $P_{i, t}$ denote the close price of asset $i$ at discrete hour $t$. The continuously compounded 1-hour log return is:
$$r_{i, t} = \ln\left(\frac{P_{i, t}}{P_{i, t-1}}\right)$$

Conditional asset volatility $\sigma_{i, t}$ is estimated via rolling sample standard deviation over a lookback window $H = 24\text{ hours}$:
$$\sigma_{i, t} = \sqrt{\frac{1}{H-1} \sum_{k=0}^{H-1} \left(r_{i, t-k} - \bar{r}_{i, t}\right)^2}$$

---

### 3.2. Bitcoin Beta Decomposition
To isolate asset-specific strength from broader market tides, we run a rolling Ordinary Least Squares (OLS) regression of asset return $r_{i, t}$ against Bitcoin return $r_{\text{BTC}, t}$ over lookback window $H = 24\text{ hours}$:

$$\beta_{i, t} = \frac{\sum_{k=0}^{H-1} \left(r_{i, t-k} - \bar{r}_i\right)\left(r_{\text{BTC}, t-k} - \bar{r}_{\text{BTC}}\right)}{\sum_{k=0}^{H-1} \left(r_{\text{BTC}, t-k} - \bar{r}_{\text{BTC}}\right)^2 + \delta}$$

Where $\delta = 10^{-8}$ prevents division by zero. We clip $\beta_{i, t} \in [0.2, 3.0]$ to enforce realistic bounds.

The **idiosyncratic residual return** $\epsilon_{i, t}$ is:
$$\epsilon_{i, t} = r_{i, t} - \beta_{i, t} \cdot r_{\text{BTC}, t}$$

* $\epsilon_{i, t} > 0$: The altcoin outperformed its beta-expected move.
* $\epsilon_{i, t} < 0$: The altcoin underperformed its beta-expected move.

---

### 3.3. The 4 Orthogonalized Factors

We construct four mathematically distinct factors $\mathbf{x}_{i, t} = [f_{i, t}^{\text{lag}}, f_{i, t}^{\text{mom}}, f_{i, t}^{\text{rev}}, f_{i, t}^{\text{flow}}]^T$:

#### Factor 1: Cross-Asset Lead-Lag Spillover ($f^{\text{lag}}$)
Measures the unabsorbed expected move from Bitcoin:
$$f_{i, t}^{\text{lag}} = \beta_{i, t} \cdot r_{\text{BTC}, t} - r_{i, t}$$
* If Bitcoin gained $2\%$ and the altcoin's beta is $1.5$, the expected altcoin move was $3\%$.
* If the altcoin only gained $1\%$, then $f_{i, t}^{\text{lag}} = 3\% - 1\% = +2\%$. The altcoin is lagging and expected to catch up.

#### Factor 2: Idiosyncratic Residual Momentum ($f^{\\text{mom}}$)
Measures cumulative idiosyncratic strength over a 12-hour window, standardized by volatility:
$$f_{i, t}^{\text{mom}} = \frac{\sum_{k=0}^{11} \epsilon_{i, t-k}}{\sigma_{i, t} \cdot \sqrt{12}}$$
* This captures persistent organic demand for an individual altcoin independent of whether Bitcoin is up or down.

#### Factor 3: Short-Term Mean Reversion ($f^{\text{rev}}$)
Captures 1-hour liquidity exhaustion bounces:
$$f_{i, t}^{\text{rev}} = -\frac{r_{i, t}}{\sigma_{i, t}}$$
* Assets that experienced an aggressive 1-hour spike are statistically prone to a pullback; assets that were dumped aggressively are prone to a relief bounce.

#### Factor 4: Volume Flow Imbalance ($f^{\text{flow}}$)
Quantifies whether volume is abnormally expanding in the direction of the move:
$$z_{V, i, t} = \frac{V_{i, t} - \bar{V}_{i, 20}}{\sigma_{V, i, 20}}$$
$$f_{i, t}^{\text{flow}} = z_{V, i, t} \cdot \text{sign}(r_{i, t})$$
* Institutional moves are accompanied by $z_V > 1.5$. High volume in an uptrend yields $f^{\text{flow}} > 0$; high volume in a downtrend yields $f^{\text{flow}} < 0$.

---

### 3.4. Cross-Sectional Normalization
To make factor values comparable across different assets and eliminate time-varying market-wide volatility, we normalize each factor cross-sectionally across all $N$ assets at each timestamp $t$:

$$\tilde{f}_{i, t}^{(k)} = \frac{f_{i, t}^{(k)} - \mu_t^{(k)}}{\sigma_t^{(k)} + \delta}$$

Where:
$$\mu_t^{(k)} = \frac{1}{N} \sum_{j=1}^N f_{j, t}^{(k)}, \quad \sigma_t^{(k)} = \sqrt{\frac{1}{N-1} \sum_{j=1}^N \left(f_{j, t}^{(k)} - \mu_t^{(k)}\right)^2}$$

This guarantees that for every factor, the universe distribution has **zero mean ($\mu = 0$) and unit variance ($\sigma = 1$)**.

---

### 3.5. Forward Return Target
The predictive target is the forward $h$-step log return (where $h = 4\text{ hours}$):
$$y_{i, t} = \ln\left(\frac{P_{i, t+h}}{P_{i, t}}\right)$$

We predict forward returns linearly:
$$\hat{y}_{i, t} = \mathbf{x}_{i, t}^T \boldsymbol{\beta} = \beta_1 \tilde{f}_{i, t}^{\text{lag}} + \beta_2 \tilde{f}_{i, t}^{\text{mom}} + \beta_3 \tilde{f}_{i, t}^{\text{rev}} + \beta_4 \tilde{f}_{i, t}^{\text{flow}}$$

---

### 3.6. Analytical Ridge Calibration (Closed-Form Solution)

Rather than hand-picking indicator weights (e.g. guessing `+0.25` or `+0.30`), the parameter vector $\boldsymbol{\beta} \in \mathbb{R}^4$ is **calibrated analytically from rolling historical panel data**.

Let $\mathbf{X} \in \mathbb{R}^{M \times 4}$ be the historical feature matrix stacked across all assets over the last $W = 72\text{ hours}$, and $\mathbf{y} \in \mathbb{R}^M$ be the corresponding vector of forward returns.

To prevent collinearity between altcoin factors and penalize large coefficients, we formulate the optimization problem with an **$L_2$ Ridge Penalty** ($\lambda > 0$):

$$\min_{\boldsymbol{\beta}} \mathcal{L}(\boldsymbol{\beta}) = \frac{1}{2} \|\mathbf{y} - \mathbf{X}\boldsymbol{\beta}\|_2^2 + \frac{\lambda}{2} \|\boldsymbol{\beta}\|_2^2$$

#### Analytical Derivation:
Take the matrix gradient with respect to $\boldsymbol{\beta}$ and set it to zero:
$$\nabla_{\boldsymbol{\beta}} \mathcal{L} = -\mathbf{X}^T (\mathbf{y} - \mathbf{X}\boldsymbol{\beta}) + \lambda \boldsymbol{\beta} = 0$$
$$-\mathbf{X}^T \mathbf{y} + \mathbf{X}^T \mathbf{X} \boldsymbol{\beta} + \lambda \mathbf{I} \boldsymbol{\beta} = 0$$
$$\left(\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I}\right) \boldsymbol{\beta} = \mathbf{X}^T \mathbf{y}$$

Because $\mathbf{X}^T \mathbf{X}$ is symmetric positive semi-definite and $\lambda > 0$, the matrix $(\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I})$ is strictly positive definite and invertible. The **exact, unique global analytical solution** is:

$$\hat{\boldsymbol{\beta}} = \left(\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I}\right)^{-1} \mathbf{X}^T \mathbf{y}$$

#### Model Health Diagnostics:
At each calibration, the model calculates:
1. **Coefficient of Determination ($R^2$)**:
   $$R^2 = 1 - \frac{\sum_{m=1}^M (y_m - \hat{y}_m)^2}{\sum_{m=1}^M (y_m - \bar{y})^2}$$
2. **Factor $t$-Statistics**:
   $$\text{Cov}(\hat{\boldsymbol{\beta}}) = \hat{\sigma}_{\epsilon}^2 \left(\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I}\right)^{-1}, \quad t_k = \frac{\hat{\beta}_k}{\sqrt{\text{Cov}(\hat{\boldsymbol{\beta}})_{kk}}}$$
3. **Rank Information Coefficient ($IC$)**:
   $$IC = \text{SpearmanCorrelation}(\hat{\mathbf{y}}, \mathbf{y})$$

---

### 3.7. The Cost-Penalized Selection Hurdle

In live execution, expected return predictions $\hat{\mu}_{i, t} = \mathbf{x}_{i, t}^T \hat{\boldsymbol{\beta}}$ are evaluated against a **Cost Hurdle ($c_{\text{hurdle}} = 0.0020$ or $20\text{ bps}$)**.

Let $t_{\text{exit}, i}$ denote the timestamp of the last closed trade on asset $i$. A signal is generated if and only if:

$$\text{Signal}(i, t) = \begin{cases}
\text{BUY (Long)} & \text{if } \hat{\mu}_{i, t} > +c_{\text{hurdle}} \quad \text{and} \quad (t - t_{\text{exit}, i}) \ge \tau_{\text{cooldown}} \\
\text{SHORT\_OPEN} & \text{if } \hat{\mu}_{i, t} < -c_{\text{hurdle}} \quad \text{and} \quad (t - t_{\text{exit}, i}) \ge \tau_{\text{cooldown}} \\
\text{HOLD / CASH} & \text{otherwise}
\end{cases}$$

Where:
* $c_{\text{hurdle}} = 0.0020$: Requires the model to predict at least $+0.20\%$ forward return to justify the $0.10\%$ round-trip maker commission.
* $\tau_{\text{cooldown}} = 16\text{ bars (4 hours)}$: Enforces a structural cooling period after any position closes, making "revenge trading" or immediate re-entry churn mathematically impossible.

---

### 3.8. Noise-Resilient Asymmetric Risk Exits

Our previous backtest failed because stop losses were placed at $1.5 \times \text{ATR}$, which is inside the random 15-minute noise band. 

In the updated model:
1. **Initial Stop Loss Distance**: Set at $2.5 \times \text{ATR}$ (or a minimum $2.0\%$ price distance):
   $$\text{StopDist}_{i, t} = \max\left(2.5 \times \text{ATR}_{i, t}, \, 0.02 \times P_{i, t}\right)$$
   $$\text{StopLoss}_{\text{Long}} = P_{\text{entry}} - \text{StopDist}_{i, t}$$
   $$\text{StopLoss}_{\text{Short}} = P_{\text{entry}} + \text{StopDist}_{i, t}$$
2. **Profit Target**: Placed at exactly **$2:1$ reward-to-risk ratio**:
   $$\text{Target}_{\text{Long}} = P_{\text{entry}} + \left(2.0 \times \text{StopDist}_{i, t}\right) = P_{\text{entry}} + 5.0 \times \text{ATR}_{i, t}$$
   $$\text{Target}_{\text{Short}} = P_{\text{entry}} - \left(2.0 \times \text{StopDist}_{i, t}\right) = P_{\text{entry}} - 5.0 \times \text{ATR}_{i, t}$$
3. **Dynamic Trailing Ratchet**: Once price advances by $+1.2 \times \text{ATR}$ in favor of the trade (`BREAKEVEN_ACTIVATION_ATR_MULT = 1.2`):
   - The stop loss automatically ratchets up to $P_{\text{entry}} \times 1.001$ (**break-even + 10 bps friction coverage**).
   - Once price advances to $+2.0 \times \text{ATR}$ (`TRAILING_ACTIVATION_ATR_MULT = 2.0`), the stop trails the highest price reached by $2.5 \times \text{ATR}$, locking in accumulated profits.

---

### 3.9. Dedicated Bitcoin Alpha Engine: ATR-Normalized Trend Velocity & Momentum Vector

#### The Cross-Sectional Distortion Problem:
In standard cross-sectional multi-factor models, factor normalization is performed across all $N$ assets simultaneously:
$$\tilde{f}_{i, t} = \frac{f_{i, t} - \mu_t}{\sigma_t + \delta}$$
Because high-beta altcoins (e.g., `DOGE`, `SUI`, `AVAX`) exhibit $2\times\text{ to }4\times$ greater return dispersion and volatility than Bitcoin (`BTC`), cross-sectional standardization forces Bitcoin's standardized factor values to cluster tightly around zero ($\tilde{f}_{\text{BTC}} \approx 0$). Consequently:
$$|\hat{\mu}_{\text{BTC}}| = |\mathbf{x}_{\text{BTC}}^T \hat{\boldsymbol{\beta}}| \le c_{\text{hurdle}} = 0.0020$$
Bitcoin's expected return rarely breached the $20\text{ bps}$ cost hurdle, causing the portfolio engine to starve Bitcoin of capital despite sustained macro bull breakouts.

#### Algorithmic Resolution: Standalone Velocity & Momentum Scoring:
AlphaChain decouples Bitcoin alpha generation from cross-sectional altcoin standardizations, implementing dedicated macro trend factors directly in `strategy/linear_factor_model.py`:

1. **ATR-Normalized Trend Velocity ($v_{\text{BTC}}$)**:
   Quantifies the momentum velocity of short-term vs medium-term exponential moving averages relative to true market noise:
   $$v_{\text{BTC}} = \frac{\text{EMA}_{12}(P_{\text{BTC}}) - \text{EMA}_{26}(P_{\text{BTC}})}{\text{ATR}_{14}(P_{\text{BTC}})}$$
   Where $\text{ATR}_{14}(P_{\text{BTC}})$ is computed via rolling Wilder's True Range over 14 hourly bars:
   $$\text{TR}_t = \max\left(H_t - L_t, \, |H_t - C_{t-1}|, \, |L_t - C_{t-1}|\right)$$
   $$\text{ATR}_{14} = \frac{1}{14} \sum_{k=0}^{13} \text{TR}_{t-k}$$

2. **Multi-Horizon Volatility-Standardized Momentum Vector ($m_{\text{BTC}}$)**:
   Blends log returns across $4\text{h}$, $12\text{h}$, and $24\text{h}$ horizons, scaled by rolling 24-hour return volatility $\sigma_{24\text{h}}$:
   $$r_{4\text{h}} = \ln\left(\frac{P_t}{P_{t-4}}\right), \quad r_{12\text{h}} = \ln\left(\frac{P_t}{P_{t-12}}\right), \quad r_{24\text{h}} = \ln\left(\frac{P_t}{P_{t-24}}\right)$$
   $$m_{\text{BTC}} = 0.50 \cdot \left(\frac{r_{4\text{h}}}{\sigma_{24\text{h}}}\right) + 0.30 \cdot \left(\frac{r_{12\text{h}}}{\sigma_{24\text{h}} \cdot \sqrt{3}}\right) + 0.20 \cdot \left(\frac{r_{24\text{h}}}{\sigma_{24\text{h}} \cdot \sqrt{6}}\right)$$

3. **Standalone TradeSignal Generation & Conviction Calibration**:
   A dedicated long `TradeSignal` for `BTC/USD` is generated when:
   $$v_{\text{BTC}} > 0.0 \quad \land \quad m_{\text{BTC}} > 0.20 \quad \land \quad P_{\text{BTC}} > \text{EMA}_{50}(P_{\text{BTC}})$$
   The directional conviction $c_{\text{BTC}} \in [0.20, 1.0]$ is computed as:
   $$c_{\text{BTC}} = \text{clip}\left(\frac{v_{\text{BTC}} + m_{\text{BTC}}}{2.0}, \, 0.20, \, 1.0\right)$$
   This signal is directly dispatched to the dynamic position sizer, allowing Bitcoin to compete on equal footing with altcoins for portfolio capital.

---

### 3.10. Dynamic Portfolio Concurrency & Cumulative Risk Heat Partitioning (R2 Dynamic Model)

#### Mathematical Invariant Definition:
Rather than imposing an arbitrary integer slot limit (`MAX_CONCURRENT_POSITIONS = 3`), AlphaChain's dynamic risk engine (`risk/position_sizer.py`) scales concurrency to any number of qualifying assets $K \in [1, N]$ subject to four continuous mathematical invariants:

1. **Total Cumulative Portfolio Risk Heat Invariant**:
   $$\mathcal{H}_{\text{portfolio}} = \sum_{i \in \mathcal{P}_{\text{open}}} \rho_i \le \mathcal{H}_{\text{regime}} \le \mathcal{H}_{\max} = 1.0\% \quad (0.0100)$$
2. **Individual Asset Risk Ceiling**:
   $$\rho_i \le \rho_{\max} = 0.50\% \quad (0.0050) \quad \forall i$$
3. **Cash Reserve Invariant**:
   $$\text{Free Cash Reserve} \ge 0.25 \times E_t \implies C_{\text{avail}} = \max\left(0.0, \, \text{free\_usd} - 0.25 \times E_t\right)$$
4. **Per-Coin Capital Concentration Bound**:
   $$\text{Capital}_i \le E_t \cdot \min\left(0.20, \, \frac{1.0 - 0.25}{\max(1, K_{\text{open}} + K_{\text{new}})}\right)$$

#### Live Downside Risk Heat Tracking:
At any discrete timestamp $t$, the active downside risk heat $\mathcal{H}_{\text{open}}$ is computed across all currently held positions $\mathcal{P}$:
$$\mathcal{H}_{\text{open}} = \sum_{j \in \mathcal{P}_{\text{long}}} \frac{Q_j \cdot \max(0, P_{\text{entry}, j} - P_{\text{stop}, j})}{E_t} + \sum_{j \in \mathcal{P}_{\text{short}}} \frac{Q_j \cdot \max(0, P_{\text{stop}, j} - P_{\text{entry}, j})}{E_t}$$

* **Breakeven Risk Heat Collapse**: When an open position advances by $+1.2\times\text{ATR}$, its stop loss is moved to $P_{\text{entry}} \times 1.001$. As a consequence:
  $$\max(0, P_{\text{entry}} - P_{\text{stop}}) = 0 \implies \rho_j = 0.0$$
  This position's risk heat collapses to zero, instantly unlocking available risk heat $\mathcal{H}_{\text{avail}}$ for incoming trade candidates while the profitable position continues trailing.

#### Available Risk Heat Calculation Across Regimes:
The available risk heat budget $\mathcal{H}_{\text{avail}}$ is:
$$\mathcal{H}_{\text{avail}} = \max(0.0, \, \mathcal{H}_{\text{regime}} - \mathcal{H}_{\text{open}})$$
Where $\mathcal{H}_{\text{regime}}$ is conditioned on macro regime:
$$\mathcal{H}_{\text{regime}} = \begin{cases}
1.0\% \quad (0.0100) & \text{if Regime} = \text{BULL} \\
0.40\% \quad (0.0040) & \text{if Regime} = \text{CHOP} \quad (\text{requires conviction } c_i \ge 0.45) \\
0.0\% \quad (0.0000) & \text{if Regime} = \text{BEAR (100\% cash defense for longs)}
\end{cases}$$

#### Conviction-Weighted Risk Partitioning:
Let $\mathcal{S}_t = \{s_1, s_2, \dots, s_K\}$ denote the set of qualifying signals passing regime filters. The normalized conviction weight $w_i$ is:
$$w_i = \frac{\max(0.01, c_i)}{\sum_{k=1}^K \max(0.01, c_k)}$$

The allocated target risk fraction $\rho_i^*$ and nominal risk dollars are:
$$\rho_i^* = \min\left(\rho_{\max}, \, \mathcal{H}_{\text{avail}} \cdot w_i\right)$$
$$\text{RiskUSD}_i = E_t \cdot \rho_i^*$$

The raw order quantity and nominal capital required are:
$$Q_{\text{raw}, i} = \frac{\text{RiskUSD}_i}{|P_{\text{entry}, i} - P_{\text{stop}, i}|}$$
$$\text{Capital}_{\text{raw}, i} = Q_{\text{raw}, i} \cdot P_{\text{entry}, i}$$

Applying per-coin capital concentration bounds:
$$\text{Capital}_i^* = \min\left(\text{Capital}_{\text{raw}, i}, \, E_t \cdot \min\left(0.20, \, \frac{0.75}{\max(1, K_{\text{open}} + K)}\right)\right)$$
$$Q_i^* = \frac{\text{Capital}_i^*}{P_{\text{entry}, i}}$$

#### Aggregate Cash Scaling Factor:
To preserve the $25\%$ cash reserve under all market scenarios, the total capital required $\sum_{i=1}^K \text{Capital}_i^*$ is compared against available cash $C_{\text{avail}}$:
$$\alpha = \min\left(1.0, \, \frac{C_{\text{avail}}}{\sum_{i=1}^K \text{Capital}_i^*}\right)$$
$$Q_{\text{final}, i} = \text{round}\left(Q_i^* \cdot \alpha, \, \text{precision}_i\right)$$
If $Q_{\text{final}, i} \cdot P_{\text{entry}, i} < \$10.0$ USD (Roostoo minimum notional), the order is safely rejected.

---

### 3.11. Mathematical Drawdown Guarantee: Immunity to the 4.0% Disqualification Ceiling

#### Theorem (Absolute Portfolio Drawdown Bound Under Simultaneous Flash-Crash)
*Let a portfolio of equity $E_t$ hold any number of open positions $K \ge 1$ sized according to Section 3.10. Suppose an instantaneous macro flash crash triggers all active stop-loss orders simultaneously, subject to adverse gap slippage $\delta_{\text{slip}} \le 10\text{ bps}$ ($0.10\%$). The maximum possible portfolio drawdown $\mathcal{D}_{\max}$ satisfies:*

$$\mathcal{D}_{\max} \le 1.075\% \ll 4.00\%$$

*preserving a guaranteed minimum safety margin of $2.925\%$ ($73.1\%$ relative buffer) below Roostoo's 4.0% disqualification ceiling.*

#### Formal Derivation and Proof:
1. **Downside Risk Sizing Invariant**:
   For each position $i \in \{1, \dots, K\}$, nominal downside price distance is $\Delta P_i = P_{\text{entry}, i} - P_{\text{stop}, i}$. By definition of the dynamic position sizer:
   $$\text{Risk}_{\text{nominal}, i} = Q_i \cdot \Delta P_i = \rho_i \cdot E_t$$
   The aggregate portfolio risk heat invariant establishes:
   $$\sum_{i=1}^K \rho_i \le \mathcal{H}_{\max} = 0.0100 \quad (1.00\%)$$

2. **Total Capital Exposure Constraint**:
   The cash reserve invariant guarantees:
   $$\text{Free Cash} \ge 0.25 \times E_t \implies \sum_{i=1}^K \text{Capital}_i \le 0.75 \times E_t$$
   where $\text{Capital}_i = Q_i \cdot P_{\text{entry}, i}$.

3. **Catastrophic Execution Loss**:
   Under adverse gap conditions, each stop order executes with slippage $\delta_{\text{slip}} = 0.0010$ ($10\text{ bps}$) beyond $P_{\text{stop}, i}$:
   $$P_{\text{exit}, i} = P_{\text{stop}, i} - \delta_{\text{slip}} \cdot P_{\text{entry}, i}$$
   The aggregate portfolio loss $\Delta E$ across all $K$ closed positions is:
   $$\Delta E = \sum_{i=1}^K Q_i \cdot (P_{\text{entry}, i} - P_{\text{exit}, i}) = \sum_{i=1}^K Q_i \cdot \left(\Delta P_i + \delta_{\text{slip}} \cdot P_{\text{entry}, i}\right)$$
   $$\Delta E = \sum_{i=1}^K \left(Q_i \cdot \Delta P_i\right) + \delta_{\text{slip}} \sum_{i=1}^K \left(Q_i \cdot P_{\text{entry}, i}\right)$$
   $$\Delta E = \sum_{i=1}^K \text{Risk}_{\text{nominal}, i} + \delta_{\text{slip}} \sum_{i=1}^K \text{Capital}_i$$

4. **Normalized Portfolio Drawdown Bound**:
   Dividing through by total portfolio equity $E_t$:
   $$\mathcal{D}_{\max} = \frac{\Delta E}{E_t} = \sum_{i=1}^K \rho_i + \delta_{\text{slip}} \sum_{i=1}^K \left(\frac{\text{Capital}_i}{E_t}\right)$$
   Substituting the established invariants $\sum_{i=1}^K \rho_i \le 0.0100$ and $\sum_{i=1}^K \frac{\text{Capital}_i}{E_t} \le 0.75$:
   $$\mathcal{D}_{\max} \le 0.0100 + (0.0010 \times 0.75) = 0.0100 + 0.00075 = \mathbf{0.01075 = 1.075\%}$$

5. **Margin of Safety Against Disqualification**:
   $$\text{Safety Margin} = \text{Disqualification Ceiling} - \mathcal{D}_{\max} = 4.00\% - 1.075\% = \mathbf{2.925\%}$$
   $$\text{Relative Safety Buffer} = \frac{2.925\%}{4.00\%} = \mathbf{73.1\%}$$
   **Q.E.D.**

---

## 4. How We Came Up With This Model (The Reasoning Trail)

Here is the exact progression of quantitative reasoning that led to this specific model:

```
[ Step 1: Diagnose the Failure ]
Observed: 43 trades in 2 days, profit factor 0.51, return -1.71%.
Root Cause: Over-trading noise and tight stops inside 15m wicks.
               │
               ▼
[ Step 2: Establish the Trade Horizon ]
Constraint: 15-day competition, 12 coins, 0.10% round-trip fee.
Deduction: Optimal holding period is 4 to 24 hours. Target trade frequency is 1 to 3 trades per day.
               │
               ▼
[ Step 3: Identify the Real Inefficiency ]
Observation: Altcoins do not move independently; they are levered beta to BTC with transmission lags.
Deduction: Must decompose returns into Beta + Residual, and quantify the Lead-Lag transmission.
               │
               ▼
[ Step 4: Formulate Analytical Calibration ]
Problem: Guessing factor weights (+0.25, +0.30) is subjective and overfits.
Solution: Solve for weights analytically using Ridge Regression: β̂ = (X'X + λI)⁻¹ X'y.
               │
               ▼
[ Step 5: Enforce Friction & Noise Filtering ]
Problem: If expected return is +0.03%, trading it loses money to fees.
Solution: Set cost hurdle |μ̂| > 20 bps and cooldown τ = 4h. Set stops at 2.5x ATR.
```

---

## 5. Why This Model and Not Others? (Comparative Analysis vs Top 10 Competitor Archetypes)

### 5.1. vs. Generic Technical Indicators (RSI, MACD, EMA Cross — Archetype #9)
* **The Flaw of Indicators**: Indicators are **descriptive lagging statistics**, not models. They have no predictive error term, no calibration, no beta isolation, and no concept of transaction costs. In range-bound chop, moving average crosses suffer severe whipsaws buying tops and selling bottoms.
* **Why Our Model Wins**: Our model isolates beta, calibrates factor weights via Ridge regression, conditions exposure on market regime (contracting risk to $0.40\%$ in chop and $0.0\%$ in bear), and explicitly evaluates expected return against transaction fees before executing.

### 5.2. vs. Pure Statistical Arbitrage / Cointegration Pairs Trading (Archetype #5)
* **The Flaw in Crypto**: Classical pairs trading assumes two assets share a stationary cointegrating vector: $S_t = \ln P_A - \gamma \ln P_B \sim I(0)$. 
* Over a **15-day window**, crypto cointegration relationships are notoriously unstable (non-stationary). If Solana launches a major upgrade, `SOL/ETH` does not mean-revert—it trends away permanently, causing classical pairs trading to take catastrophic drawdown. Furthermore, executing two legs doubles round-trip transaction costs to $20\text{--}40\text{ bps}$.
* **Why Our Model Wins**: Our model does not assume static cointegration. It models dynamic lead-lag spillover over rolling 24-hour windows and operates on directional single-asset legs sized by risk heat.

### 5.3. vs. High-Frequency Market Making / Order-Queue Tick Scalping (Archetype #2)
* **The Flaw on Roostoo**: In naive mock exchange matching engines, limit orders placed at `MaxBid`/`MinAsk` are immediately filled on touch without modeling queue depth, time-priority FIFO queues, or adverse selection. Competitors game this by executing 200+ micro-trades/day.
* **Why It Fails in Reality**: In real crypto exchange order books (e.g. Binance/Bybit), passive orders sit behind millions in HFT queue depth. Limit orders only fill when aggressive institutional flow overruns the book (adverse selection), leading to negative expected value:
  $$E[\text{PnL}_{\text{trade}}] = \frac{\text{Spread}}{2} - \text{Fee} - \text{AdverseDrift} < 0$$
* **Why Judges Disqualify It**:
  1. *Rule Screen 1 Violation*: Fails the mandatory rule: "No market-making and arbitrage strategies, only directional" (Slide 17).
  2. *Technical Audit Screen 4*: Susquehanna and Flow Traders judges inspect git repositories and trade logs. Strategies relying on un-fillable simulator queue quirks are rejected for lack of economic alpha.
* **Why Our Model Wins**: Our model operates on discrete 1-hour intervals with low turnover (1.5–3.0 trades/day), capturing macro information diffusion and using maker-first execution with genuine directional edge.

### 5.4. vs. Deep Learning / LSTMs / Neural Networks (Archetype #10)
* **The Flaw in Short Competitions**: Deep learning models have $10^4\text{--}10^6$ parameters. In a 15-day tournament with small sample sizes, deep neural networks suffer from **severe variance (overfitting)**: they memorize past noise and collapse out-of-sample. Furthermore, neural nets are black boxes that cannot be defended in an academic pitch deck to Susquehanna quants.
* **Why Our Model Wins**: Ridge regression has only 4 parameters, solved in closed form with analytical global convexity. It cannot diverge, cannot suffer vanishing gradients, and produces clean $t$-statistics and $R^2$ metrics.

### 5.5. vs. Martingale / Grid Accumulators & Unhedged Concentrated Beta Holders (Archetypes #4 & #7)
* **The Flaw of Martingale Grids**: Grid accumulators double down on falling assets. A single $-6\%$ trending liquidation cascade exhausts cash and trips the $4.0\%$ drawdown disqualification ceiling.
* **The Flaw of Unhedged YOLO Bets**: Holding 100% in a single token (`SOL` or `SUI`) violates diversification and either fails the 10-day active execution mandate or gets liquidated during standard crypto volatility.
* **Why Our Model Wins**: Dynamic risk heat budgeting guarantees that simultaneous stop-outs across all held positions can never exceed $1.075\%$ portfolio drawdown, maintaining absolute immunity to the $4.0\%$ ceiling.

---

## 6. Failure Modes & The Escalation Path to Non-Linear ML

Quantitative discipline requires identifying in advance **where the linear model could fail**:

### Potential Failure Modes of the Linear Model:
1. **Regime Structural Break**: If Bitcoin enters a vertical liquidation cascade (e.g. FTX-style $-20\%$ crash in 2 hours), linear beta estimates break down because correlations spike to $+1.0$.
   - *Defense*: The 15-minute flash-crash filter freezes new entries if BTC drops $> 0.60\%$, and the $4.0\%$ Portfolio Drawdown Circuit Breaker liquidates all positions to USD cash and pauses trading.
2. **Non-Linear Interaction**: If volume flow is only predictive when volatility is compressed (an interaction effect $f^{\text{flow}} \times f^{\text{vol\_squeeze}}$), linear regression treats them independently.

### The Non-Linear ML Escalation Path (Stage 3):
If empirical diagnostics during live testing show that the linear model's Information Coefficient drops below $IC < 0.02$, we will escalate to:
1. **Model**: **LightGBM Regressor** with max depth $D=3$ and severe regularization.
2. **Validation Scheme**: **Purged & Embargoed Group Time-Series Cross-Validation** (Marcos López de Prado methodology) to prevent information leakage across overlapping forward return windows.
3. **Target**: Directional probability classifier $P(r > c_{\text{hurdle}} \mid \mathbf{X})$.

---

## 7. Production Code Mapping & Invariant Verification Matrix

Every mathematical equation in this specification is implemented in production code and verified via automated test suites:

| Mathematical Concept / Equation | Specification Section | Production File & Method | Verification Test Case |
| :--- | :---: | :--- | :--- |
| **Analytical Ridge Calibration**<br>$\hat{\boldsymbol{\beta}} = (\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I})^{-1} \mathbf{X}^T \mathbf{y}$ | §3.6 | `strategy/linear_factor_model.py`<br>`LinearFactorModel.calibrate_weights()` | `tests/test_linear_model.py`<br>`test_calibrate_weights_positive_definite` |
| **Dedicated BTC Trend Velocity**<br>$v_{\text{BTC}} = \frac{\text{EMA}_{12} - \text{EMA}_{26}}{\text{ATR}_{14}}$ | §3.9 | `strategy/linear_factor_model.py`<br>`compute_btc_velocity_score()` | `tests/test_dynamic_risk_engine.py`<br>`test_btc_trend_velocity_and_allocation` |
| **Dedicated BTC Momentum Vector**<br>$m_{\text{BTC}} = \sum w_h \frac{r_h}{\sigma \sqrt{h}}$ | §3.9 | `strategy/linear_factor_model.py`<br>`compute_btc_velocity_score()` | `tests/test_linear_model.py`<br>`test_btc_momentum_vector_horizons` |
| **Active Risk Heat Tracking**<br>$\mathcal{H}_{\text{open}} = \sum \frac{Q_j \max(0, P_{\text{entry}} - P_{\text{stop}})}{E_t}$ | §3.10 | `risk/stop_loss_manager.py`<br>`get_portfolio_risk_heat()` | `tests/test_dynamic_risk_engine.py`<br>`test_cumulative_risk_heat_invariant` |
| **Breakeven Risk Heat Collapse**<br>$P_{\text{stop}} \ge P_{\text{entry}} \implies \rho_j = 0$ | §3.10 | `risk/stop_loss_manager.py`<br>`update_price()` (Barrier A) | `tests/test_dynamic_risk_engine.py`<br>`test_breakeven_stop_frees_risk_heat` |
| **Conviction Risk Heat Partitioning**<br>$\rho_i^* = \min(\rho_{\max}, \mathcal{H}_{\text{avail}} \cdot w_i)$ | §3.10 | `risk/position_sizer.py`<br>`calculate_dynamic_portfolio_sizes()` | `tests/test_dynamic_risk_engine.py`<br>`test_dynamic_concurrency_scaling_2_assets`<br>`test_dynamic_concurrency_scaling_6_assets` |
| **Cash Reserve Invariant**<br>$\text{Free Cash} \ge 25\% \times E_t$ | §3.10 | `risk/position_sizer.py`<br>`calculate_dynamic_portfolio_sizes()` | `tests/test_dynamic_risk_engine.py`<br>`test_cash_reserve_invariant_25_pct` |
| **Mathematical Drawdown Guarantee**<br>$\mathcal{D}_{\max} \le 1.075\% \ll 4.00\%$ | §3.11 | `risk/position_sizer.py`<br>`calculate_dynamic_portfolio_sizes()` | `tests/test_dynamic_risk_engine.py`<br>`test_simultaneous_stop_out_drawdown_guarantee` |
| **Regime-Conditional Defense**<br>$\mathcal{H}_{\text{CHOP}} \le 0.40\%, \mathcal{H}_{\text{BEAR}} = 0.0\%$ | §3.10 | `strategy/multi_regime_alpha.py`<br>`generate_signals()` | `tests/test_dynamic_risk_engine.py`<br>`test_regime_contraction_to_cash_defense` |
| **Hardware Circuit Breaker**<br>Peak Drawdown $\ge 4.0\% \implies$ Halt | §1.0 | `risk/circuit_breaker.py`<br>`CircuitBreaker.check_drawdown()` | `tests/test_risk.py`<br>`test_circuit_breaker_trips_at_4pct` |

---

*Document maintained for the AlphaChain Autonomous Quantitative Trading System. Verified against live production code and automated invariant test suites.*
