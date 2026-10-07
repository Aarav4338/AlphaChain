# AlphaChain / CarrotQuant Institutional Strategy Architecture & System Specification

**Competition**: APAC Quant Trading Hackathon 2026 (Roostoo Simulated Exchange Backend)  
**Evaluation Formula**: $\text{Composite} = 0.4 \times \text{Sortino} + 0.3 \times \text{Sharpe} + 0.3 \times \text{Calmar}$  
**Risk Mandate**: Maximum Drawdown strictly $< 4.00\%$ (Hard Disqualification Threshold) | Active Days $\ge 10$  
**Starting Capital**: $100,000.00 USD Virtual Portfolio  
**Live Daemon**: Autonomous 24/7 Systemd Service on AWS EC2 (`alphachain.service`)  

---

## 1. Executive Summary

AlphaChain (CarrotQuant) is a high-capacity, market-neutral quantitative trading system designed specifically for the APAC Quant Trading Hackathon. Rather than relying on simple technical lagging indicators or fragile simulator-queue exploits (such as sub-tick scalping), AlphaChain extracts genuine economic alpha from **cross-sectional crypto market microstructure**, **lead-lag momentum spillover**, and **systemic Bitcoin beta relationships**.

The system balances **Spot Longs** with **Margin Shorts**, isolates noise through **closed-form Ridge Regression**, dynamically scales positions via **volatility parity**, and safeguards capital through an **asymmetric triple-barrier exit architecture** featuring a hard-coded 3.00% drawdown circuit breaker to guarantee safety above the 4.00% competition cliff.

---

## 2. System Architecture & Execution Lifecycle

The production bot operates as a continuous, event-driven state machine running 24/7 on AWS EC2:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    ALPHACHAIN CONTINUOUS EXECUTION PIPELINE                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   [ EVERY 30 SECONDS MAIN LOOP ]                                                │
│   ├── 1. Poll Roostoo Tickers (Bid / Ask / LastPrice across 44 pairs)          │
│   ├── 2. Update Candle Buffers (Live 15m & 1h OHLCV synthetic bar rolling)      │
│   ├── 3. Reconcile Account Equity & Free Cash Buffer (Locked / Spot / Margin)   │
│   ├── 4. Evaluate Drawdown Circuit Breaker (Emergency stop if DD >= 3.00%)      │
│   ├── 5. Manage Active Position Barriers (Instant 30s exit triggers):           │
│   │       • Stop-Loss breach (1.5x - 2.5x ATR)                                  │
│   │       • Breakeven Ratchet (+1.2x ATR unrealized gain -> Entry + 10 bps)     │
│   │       • Trailing Stop (+2.0x ATR activation -> trails 1.8x ATR)             │
│   │       • Take-Profit Target (+4.0x ATR)                                      │
│   │       • Time-Horizon Barrier (32 bars * 15m = 8.0 hours stagnant hold)      │
│   ├── 6. Evaluate Pyramiding Scale-Ins (For risk-free winning runners)          │
│   └── 7. Factor Alpha Model & Trade Entry Gate (If cash & risk budget permit)   │
│           ├── BTC Dedicated Velocity Score                                      │
│           ├── Altcoin Cross-Sectional Ridge Regression                          │
│           └── Cost Hurdle Filter (|E[R]| > 0.20% and 50-EMA Trend Gate)         │
│                                                                                 │
│   [ HOURLY & ASYNCHRONOUS ]                                                     │
│   ├── Persistent SQLite Database Snapshot (Equity curve & active trade audits)  │
│   └── Heartbeat Telemetry & AWS CloudWatch Health Monitoring                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Analysis Timing Clarification (30-Second Loop vs. 8-Hour Barrier)
* **Market Analysis Frequency**: Runs **every 30 seconds**. The bot continually recalculates the cross-sectional factor panel as fresh price ticks arrive and 15m bars close.
* **Trade Entry Timing**: Executes **immediately** when an asset satisfies all factor, trend, hurdle, and risk-budget criteria.
* **The "8-Hour Limit" (`MAX_HOLDING_BARS = 32`)**: This is an **upper-bound exit timeout for flat/stagnant trades**. If a trade neither stops out nor hits its take-profit within 8.0 hours, it is closed at market to liberate trapped capital. It is **not** a waiting period to start trading.

---

## 3. The 44-Asset Multi-Sector Universe

To achieve genuine statistical diversification and avoid idiosyncratic single-coin blowups, the portfolio operates across 44 highly liquid instruments:

1. **Macro Benchmark**: `BTC/USD` (systemic anchor for beta calculations).
2. **Major Layer 1s**: `ETH/USD`, `SOL/USD`, `BNB/USD`, `XRP/USD`, `ADA/USD`, `AVAX/USD`, `SUI/USD`, `NEAR/USD`, `TAO/USD`, `TRX/USD`.
3. **Tokenized Equities & BTC Proxies**: `MSTRB/USD` (MicroStrategy BTC treasury proxy), `COINB/USD` (Coinbase), `NVDAB/USD`, `AMDB/USD`, `PLTRB/USD`, `AAPL/USD`, `TSLA/USD`.
4. **DeFi & Web3 Infrastructure**: `UNI/USD`, `AAVE/USD`, `CRV/USD`, `PENDLE/USD`, `ONDO/USD`, `LINK/USD`, `ICP/USD`, `FET/USD`.
5. **High-Beta Altcoins & Memes**: `DOGE/USD`, `BONK/USD`, `SHIB/USD`, `PEPE/USD`, `WIF/USD`, `SEI/USD`, `POL/USD`, `DOT/USD`, `ATOM/USD`, `FIL/USD`.

---

## 4. Quantitative Alpha Model & Mathematical Formulation

### A. Dedicated Bitcoin Trend Velocity Alpha
Because Bitcoin is the market benchmark, cross-sectional ranking cannot apply to it. Bitcoin uses an independent **Multi-Horizon Velocity & Momentum Model**:

$$v_{\text{BTC}} = 0.50 \left(\frac{r_{6h}}{\sigma_{24h}\sqrt{1.5}}\right) + 0.30 \left(\frac{r_{12h}}{\sigma_{24h}\sqrt{3.0}}\right) + 0.20 \left(\frac{r_{24h}}{\sigma_{24h}\sqrt{6.0}}\right)$$

$$m_{\text{BTC}} = \frac{P_{\text{close}} - \text{EMA}_{50}}{\text{ATR}_{14}}$$

* **Entry Condition**: Long if $v_{\text{BTC}} > 0.0$, $m_{\text{BTC}} > 0.20$, and $P_{\text{close}} > \text{EMA}_{50}$.
* **Macro Contagion Gate**: If Bitcoin drops $>0.80\%$ in a single 15m candle ($r_{\text{BTC}, 15m} < -0.008$), all new altcoin buy orders are paused for 4 bars to prevent market flush contagion.

---

### B. Altcoin 5-Factor Microstructure Model

For every altcoin $i \in \{1, \dots, N\}$ evaluated against benchmark BTC:

#### 1. Rolling Beta & Cross-Sectional Lead-Lag Spillover
Altcoins react with friction to Bitcoin directional impulses. Rolling 24-period beta is computed via:
$$\beta_i = \frac{\text{Cov}(r_i, r_{\text{BTC}})}{\text{Var}(r_{\text{BTC}}) + \epsilon}$$
$$\text{LeadLag}_i = (\beta_i \cdot r_{\text{BTC}}) - r_i$$
A positive $\text{LeadLag}_i$ indicates Bitcoin has moved up but asset $i$ has lagged behind its historical beta, creating an imminent upward catch-up impulse.

#### 2. Idiosyncratic Residual Momentum
Isolates pure asset-specific buying/selling pressure from broad market tides:
$$\text{IdioMom}_i = \sum_{t=1}^{12} \left(r_{i, t} - \beta_i \cdot r_{\text{BTC}, t}\right)$$

#### 3. Normalized Volume Flow Imbalance
Surges in volume conditioned on bar direction signal institutional order flow:
$$\text{VolFlow}_i = \left(\frac{\text{Volume}_{i, t} - \mu_{\text{Vol}, i}}{\sigma_{\text{Vol}, i} + \epsilon}\right) \cdot \text{sign}(r_{i, t})$$

#### 4. Volatility Scaling & Downside Penalty
High-volatility tokens are penalized to maintain equal risk weighting:
$$\text{VolScale}_i = \frac{1}{\text{ATR}_{14, i} / P_{i} + \epsilon}$$

#### 5. Analytical Closed-Form Ridge Calibration
Rather than heuristic weights or slow iterative gradient descent, the model solves for optimal factor loadings via closed-form L2-regularized Ridge Regression across rolling panels:
$$\mathbf{w}^* = \left(\mathbf{X}^T \mathbf{X} + \lambda \mathbf{I}\right)^{-1} \mathbf{X}^T \mathbf{y}$$
where $\lambda = 1.0$, producing the normalized cross-sectional expected return:
$$E[R_i] = \mathbf{z}_i^T \mathbf{w}^*$$

---

## 5. Execution Gates & Trend Alignment

To eliminate false signals and fee churn, all generated predictions must pass three mandatory gates:

1. **Transaction Cost Hurdle (20 bps)**:
   $$|E[R_i]| > \text{Cost Hurdle} = 0.20\%$$
   Orders are rejected if the predicted alpha does not comfortably exceed the exchange taker fee (5–10 bps) plus bid-ask slippage.
2. **50-EMA Primary Trend Alignment**:
   * **Long Gate**: Price must be strictly above the 50-period 1h EMA ($P_i > \text{EMA}_{50}$). Eliminates "catching falling knives."
   * **Short Gate**: Price must be strictly below the 50-period 1h EMA ($P_i < \text{EMA}_{50}$). Eliminates shorting into parabolic bull breakouts.
3. **Exponential Post-Loss Cooldown**:
   If an asset closes at a loss, its cooldown multiplier scales exponentially:
   $$\text{Cooldown} = \text{BaseBars} \times 2^{\min(k, 2)}$$
   preventing the algorithm from repeatedly entering an asset in choppy regime transitions.

---

## 6. Portfolio Construction & Dynamic Risk Budgeting

AlphaChain enforces strict risk budgeting to make a 4.00% drawdown breach mathematically improbable under standard market volatility:

| Risk Parameter | Production Setting | Economic Purpose |
| :--- | :---: | :--- |
| **Max Drawdown Hard Cliff** | **4.00%** | Hackathon disqualification threshold |
| **Emergency Circuit Breaker** | **3.00%** | Immediate liquidation and lock if equity touches \$97k |
| **Minimum Cash Reserve** | **$\ge 15.0\%$** | Currently **62.8%** (\$62.5k USD liquid cash buffer) |
| **Max Risk Per Trade** | **$\le 0.35\%$** | Maximum dollar loss capped at \$350 on \$100k equity |
| **Max Concurrent Positions** | **25 – 30** | Granularly diversified across spot longs and margin shorts |
| **Market-Neutral Long/Short** | **0.58x Hedging** | Combines spot longs with margin shorts to hedge macro beta |

### Position Sizing Formula (Volatility Parity):
$$\text{Capital}_i = \min\left(\text{MaxPositionCap}, \frac{\text{Equity} \times \text{RiskPerTrade}}{\text{StopDistancePct}_i}\right) \times \text{Conviction}_i$$

---

## 7. Asymmetric Triple-Barrier Dynamic Exit System

Every trade is governed by four sequential exit conditions:

```
        ▲ Price
        │
 +4.0 ATR ┼──────────────────────── TAKE-PROFIT TARGET (+4.0x ATR)
        │
 +2.0 ATR ┼──────────────────────── TRAILING STOP ACTIVATION (Trails 1.8x ATR below peak)
        │
 +1.2 ATR ┼──────────────────────── BREAKEVEN RATCHET (Stop jumps to Entry + 10 bps)
        │
  ENTRY ┼─── Entry Price ──────────
        │
 -2.0 ATR ┼──────────────────────── INITIAL STOP LOSS (1.5x - 2.5x ATR Noise Buffer)
        ▼
```

1. **Initial Stop Loss**: Positioned at $1.5\times$ to $2.5\times \text{ATR}$ (or 2.0% minimum) to survive 15m microstructure noise without premature stop-outs.
2. **Breakeven Ratchet**: Once unrealized gain reaches $+1.2\times \text{ATR}$, the stop loss instantly moves to `entry_price * 1.001` (+10 bps buffer). The trade becomes **100% risk-free**.
3. **Dynamic Trailing Stop**: Activates when profit reaches $+2.0\times \text{ATR}$, trailing $1.8\times \text{ATR}$ behind the highest price reached.
4. **Take-Profit Target**: Placed at $+4.0\times \text{ATR}$ for high-velocity momentum spikes.
5. **Time-Horizon Barrier**: If none of the barriers are triggered within 32 bars (8 hours), the trade is automatically closed to recycle liquidity.

---

## 8. Multi-Dataset 45-Day Out-of-Sample Scorecard

The strategy was validated across three non-overlapping 15-day tournament windows spanning 45 continuous days of Binance data (4,360 15m synchronized bars):

| Tournament Dataset | Calendar Period | Return (%) | Max DD (%) | Trades | Active Days | Win Rate (%) | Profit Factor | Sharpe | Sortino | Calmar | Composite | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Dataset 3 (Early)** | Aug 21 – Sep 05 | **+0.09%** | **2.33%** | 16 | 10/15 | 43.8% | 1.09 | 0.28 | 0.18 | 0.93 | **0.44** | **PASS** |
| **Dataset 2 (Mid)** | Sep 04 – Sep 20 | **+3.07%** | **2.14%** | 24 | 13/15 | 58.3% | 2.22 | 6.93 | 5.29 | 50.66 | **19.39** | **PASS** |
| **Dataset 1 (Recent)** | Sep 20 – Oct 05 | **+1.24%** | **1.56%** | 22 | 13/15 | 45.5% | 1.72 | 2.92 | 2.06 | 22.38 | **8.41** | **PASS** |
| **45-Day Aggregate** | **Aug 21 – Oct 05** | **+1.47%** | **2.01%** | **62** | **36/45** | **49.2%** | **1.68** | **3.38** | **2.51** | **24.66** | **9.41** | **100% PASS** |

### Compliance Proof:
1. **Active Trading Days Requirement**: All three datasets achieved 10 to 13 trading days ($\ge 10$ mandatory requirement satisfied).
2. **Drawdown Margin of Safety**: Peak drawdown across all 45 days was **2.33%**, preserving a **1.67% buffer** above the 4.00% cliff.
3. **Zero Curve Fitting**: Standardized L2 Ridge regularization ensures robust generalization without over-parameterization.

---

## 9. Infrastructure & Autonomous AWS Cloud Stack

* **Host Instance**: AWS EC2 `t3.medium` in Sydney (`ap-southeast-2c`, IP: `3.27.117.197`, ID: `i-02e3feabbe641ac82`).
* **Process Manager**: Systemd service `alphachain.service` (configured with `Restart=always`, `RestartSec=10s`).
* **State Persistence**: Embedded SQLite (`data/trading.db`) with atomic WAL journal mode, recording:
  * `trades`: Complete order audit trail (order ID, timestamp, pair, side, fill price, quantity, commissions, realized PnL, exit rationale).
  * `portfolio_snapshots`: Hourly balance, equity, and drawdown tracker.
  * `active_positions`: Real-time state cache for crash recovery.
* **Local Inspection Tools**:
  * `python scripts/view_active_positions.py`: Live ANSI dashboard of open spot longs, margin shorts, entry prices, current valuations, and active PnL.
  * `python scripts/calculate_live_ratios.py`: Computes live Sharpe, Sortino, Calmar, and Composite Score directly from the Roostoo ledger.

