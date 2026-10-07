# 🏛️ CarrotQuant / AlphaChain Core Project Mandates & Architecture Charter

**Document Status**: IMMUTABLE USER MANDATES  
**Authored By**: Project Owner Directive  
**Scope**: Binding across all autonomous agents, subagents, modeling modules, and production pipelines.

---

## 1. Core Mission: "Playing to Win, Not Playing Not to Lose"

The foundational directive of this project is to **win the APAC Quant Trading Hackathon 2026**.  
We are not participating merely to survive or avoid disqualification; we are competing to top the leaderboard by maximizing the official evaluation formula:

$$\text{Composite Score} = 0.4 \times \text{Sortino} + 0.3 \times \text{Sharpe} + 0.3 \times \text{Calmar}$$

### Non-Negotiable Tenet:
* **Return ($R_p$) is the primary numerator in all three evaluation metrics.**  
* Without meaningful capital appreciation, Sortino, Sharpe, and Calmar collapse to zero or negative values.
* Defensive safety rails exist solely to enable **confident, aggressive offense**—never to induce timidity, capital paralysis, or cash hoarding.

---

## 2. Dynamic Parameterization Over Hardcoded Constants

Static, brittle, hardcoded thresholds (e.g., rigid magic numbers for position counts, fixed point distances, or static stop multipliers) are fundamentally prohibited in core strategy logic. The system must be **dynamically parameterized across all dimensions**:

### A. Volatility-Adaptive Barriers
* Stop losses, breakeven thresholds, trailing stops, and profit targets must scale dynamically with rolling volatility ($\text{ATR}_{14}$, rolling standard deviation $\sigma_t$, and volatility regime), adapting whether market conditions are compressed or expanding.

### B. Regime-Adaptive Concurrency & Capital Allocation
* Position concurrency and capital deployment must dynamically expand during high-conviction directional regimes (Bull / Strong Trend) and dynamically contract during low-signal chop.
* Capital deployment must scale with **model conviction and signal strength**, rather than being artificially restricted to arbitrary static caps.

### C. Adaptive Hurdle & Threshold Scaling
* Execution hurdles ($|E[R]| > \text{Cost}$) and entry thresholds must adapt dynamically to rolling bid-ask spreads, fee tier structures, and rolling volatility, ensuring the model only takes trades with positive mathematical expectation net of friction.

---

## 3. Reinforcement Learning (RL) & Adaptive Online Optimization

The system architecture must support continuous, adaptive learning and progression toward machine learning / RL-driven decision making:

### A. Closed-Form Online Calibration to Reinforcement Learning
* **Current Stage (Adaptive Factor Calibration)**: Analytical Ridge Regression ($\mathbf{w}^* = (\mathbf{X}^T\mathbf{X} + \lambda\mathbf{I})^{-1}\mathbf{X}^T\mathbf{y}$) updating factor loadings continuously across rolling walk-forward windows without lookahead bias.
* **Escalation Stage (RL & Contextual Bandits)**: Sizing, dynamic stop-loss adjustments, and entry gates must incorporate policy optimization (reinforcement learning / contextual bandits / online gradient adaptation) where the reward function directly aligns with the competition objective:
  $$R_t = \Delta \text{Equity}_t - \gamma \cdot \text{DownsideRisk}_t$$
* The model must learn which asset classes, volatility environments, and cross-sectional factor combinations produce the highest realized Sortino and Sharpe ratios.

### B. Machine Learning Non-Linearity
* Beyond linear factor combinations, non-linear feature interactions (gradient-boosted trees, directional probability classifiers $P(r > \text{cost} \mid \mathbf{X})$) must be integrated to capture asymmetric payoff regimes in crypto microstructure.

---

## 4. Cross-Sectional Market Microstructure Edge

The strategy's edge must originate from genuine economic market anomalies, **never from simulator exploitation** (such as artificial 1-tick queue scalping):

1. **Cross-Asset Lead-Lag Inefficiencies**: Exploiting the structural time lag between Bitcoin directional impulses and altcoin beta reactions ($\text{LeadLag}_i = \beta_i \cdot r_{\text{BTC}} - r_i$).
2. **Idiosyncratic Residual Momentum**: Decomposing asset returns into macro market beta versus pure asset-specific buying/selling pressure ($\epsilon_i = r_i - \beta_i \cdot r_{\text{BTC}}$).
3. **Volume Flow Imbalance**: Quantifying institutional participation via signed volume surges conditioned on order flow direction.
4. **Market-Neutral Long/Short Hedging**: Generating orthogonal alpha by longing undervalued relative outperformers while shorting overextended relative underperformers, isolating pure spread alpha from market direction.

---

## 5. Asymmetric Dynamic Compounding (Pyramiding & Ratchets)

The system must aggressively compound winning trades while mathematically cutting losses:

* **Breakeven Ratchets on Winners**: Once a position demonstrates initial trend traction, the stop loss must dynamically ratchet to entry or small profit, rendering the trade mathematically risk-free.
* **Trend Pyramiding (Scaling into Winners)**: When an active position is profitable and its stop loss is safely locked in the green, the system must dynamically scale into the trend (adding tranches) to compound winning runners without expanding initial portfolio risk.
* **Dynamic Trailing Exits**: Trailing stops must dynamically trail behind high-water marks, giving runners breathing room during parabolic expansions while locking in banked profits.

---

## 6. Hard Safety Boundaries vs. Operational Freedom

There is a fundamental distinction between an **absolute safety boundary** and **operational trading freedom**:

* **The Absolute Boundary**: Maximum Drawdown strictly $< 4.00\%$ (the hard competition disqualification cliff).
* **The Automated Guard**: A dedicated emergency circuit breaker hard-coded at **3.00% max drawdown** ($97,000 equity) that automatically liquidates to USD cash if breached, guaranteeing that the 4.00% cliff is never violated under any circumstance.
* **Operational Freedom Above the Guard**: Above the 3.00% threshold, the trading engine possesses **full operational freedom** to aggressively deploy capital, run high-conviction positions, and pursue high-return opportunities without being throttled by artificial paralysis.

---

## 7. Universe Integrity & Quality Standards

* **Asset Universe**: The strategy operates across high-liquidity, institutional crypto pairs (Layer 1s, DeFi infrastructure, Web3 protocols) and tokenized US equities.
* **Ban on Micro-Cent Meme Coins**: Illiquid or ultra-low decimal meme coins with distorted pricing dynamics (`BONK`, `SHIB`, `PEPE`, `WIF`) are excluded to preserve execution quality and statistical validity.

---

## 8. Continuous Autonomous Production Standards

* **24/7 AWS EC2 Execution**: The live daemon runs autonomously on AWS EC2 under systemd (`alphachain.service`), operating continuously on `main`.
* **State Recovery & Persistence**: Embedded SQLite ledger (`data/trading.db`) maintains complete order audit trails, hourly equity snapshots, and active position states for instant crash recovery.
* **Zero Breaking Changes**: All core algorithmic updates must maintain full test suite integrity (`pytest -v`), preserving system invariants while continually pushing for higher performance.
