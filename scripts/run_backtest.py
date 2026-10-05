"""
CarrotQuant Backtesting & Strategy Performance Simulation Engine.
Replays historical crypto market data through CarrotQuant's 8-dimensional alpha strategy,
dynamic trailing stop manager, and ATR volatility parity position sizer.
Computes the exact APAC Quant Trading Hackathon evaluation metrics:
- Raw Cumulative Return (%)
- Annualized Sharpe Ratio
- Sortino Ratio (Downside deviation only)
- Calmar Ratio (Annualized Return / Max Drawdown)
- Official Hackathon Composite Score: 0.4 * Sortino + 0.3 * Sharpe + 0.3 * Calmar
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Safe UTF-8 encoding for Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from config.trading_config import trading_config
from client.binance_fetcher import BinanceFetcher
from data.candle_manager import CandleManager
from strategy.regime_detector import RegimeDetector
from strategy.multi_regime_alpha import MultiRegimeAlphaStrategy
from strategy.signals import SignalAction, TradeSignal
from risk.position_sizer import PositionSizer
from risk.stop_loss_manager import StopLossManager, ExitSignal
from risk.circuit_breaker import CircuitBreaker, CircuitBreakerStatus


@dataclass
class BacktestMetrics:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    cumulative_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    hackathon_composite_score: float
    final_equity: float


class BacktestEngine:
    """Historical event-driven backtesting engine for CarrotQuant."""

    def __init__(
        self,
        pairs: Optional[List[str]] = None,
        initial_capital: float = 100000.0,
        fee_rate: float = 0.0005,  # 0.05% maker fee baseline
    ):
        self.pairs: List[str] = pairs or trading_config.ASSET_UNIVERSE
        self.initial_capital: float = initial_capital
        self.fee_rate: float = fee_rate
        self.binance_fetcher = BinanceFetcher()

    def fetch_historical_data(
        self, limit_15m: int = 500, limit_1h: int = 150
    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
        """Fetch 15m and 1h historical candles from Binance for the universe."""
        print(f"📥 Fetching historical candles ({limit_15m} x 15m, {limit_1h} x 1h) across {len(self.pairs)} pairs from Binance...")
        data_15m = {}
        data_1h = {}
        for pair in self.pairs:
            df_15m = self.binance_fetcher.fetch_klines(pair, interval="15m", limit=limit_15m)
            df_1h = self.binance_fetcher.fetch_klines(pair, interval="1h", limit=limit_1h)
            if df_15m is not None and not df_15m.empty and df_1h is not None and not df_1h.empty:
                data_15m[pair] = df_15m
                data_1h[pair] = df_1h
            else:
                print(f"⚠️ Warning: Missing data for {pair}")
        return data_15m, data_1h

    def run_simulation(
        self, data_15m: Dict[str, pd.DataFrame], data_1h: Dict[str, pd.DataFrame]
    ) -> Tuple[pd.DataFrame, BacktestMetrics]:
        """
        Run candle-by-candle simulation.
        Returns equity curve DataFrame and BacktestMetrics.
        """
        candle_manager = CandleManager(max_buffer_size=400)
        regime_detector = RegimeDetector()
        strategy = MultiRegimeAlphaStrategy(regime_detector)
        sizer = PositionSizer()
        stop_loss_manager = StopLossManager()
        circuit_breaker = CircuitBreaker(initial_equity=self.initial_capital)

        # Pre-index 1h candles by timestamp for realistic point-in-time lookup
        d1h_ts_map = {p: df.sort_values("timestamp").reset_index(drop=True) for p, df in data_1h.items()}

        # Find common time index for 15m bars
        timestamps = sorted(
            list(
                set.intersection(
                    *[set(df["timestamp"].dt.tz_convert("UTC")) for df in data_15m.values()]
                )
            )
        )

        warmup_bars = 40
        if len(timestamps) <= warmup_bars:
            raise ValueError(f"Insufficient historical data: {len(timestamps)} bars (need > {warmup_bars})")

        equity = self.initial_capital
        free_usd = self.initial_capital
        trade_pnls: List[float] = []
        executed_trades: List[Dict] = []
        equity_records = []

        print(f"🚀 Running simulation over {len(timestamps) - warmup_bars} bars...")

        for t_idx in range(warmup_bars, len(timestamps)):
            ts = timestamps[t_idx]

            # 1. Provide exact point-in-time candles up to ts (no lookahead, real volume)
            for pair, df in data_15m.items():
                sub_15 = df[df["timestamp"] <= ts].tail(100)
                candle_manager.initialize_pair_history(pair, "15m", sub_15)

            for pair, df_1h_full in d1h_ts_map.items():
                sub_1h = df_1h_full[df_1h_full["timestamp"] <= ts].tail(100)
                candle_manager.initialize_pair_history(pair, "1h", sub_1h)

            # 2. Check Exits on open positions (Trailing Stops / Profit Targets)
            open_pairs = list(stop_loss_manager.positions.keys())
            for pair in open_pairs:
                pos = stop_loss_manager.positions.get(pair)
                if not pos:
                    continue
                curr_p = candle_manager.get_latest_price(pair) or pos.entry_price
                exit_sig = stop_loss_manager.update_price(pair, curr_p, current_bar=t_idx)

                if exit_sig:
                    # Calculate realistic execution fill price
                    if "Stop" in exit_sig.reason:
                        # Stop order triggered at stop_loss level with 0.1% taker slippage
                        if pos.side == SignalAction.BUY:
                            fill_p = pos.stop_loss * (1.0 - 0.001)
                        else:
                            fill_p = pos.stop_loss * (1.0 + 0.001)
                    elif "Take Profit" in exit_sig.reason:
                        fill_p = pos.take_profit
                    elif "Time Horizon Barrier" in exit_sig.reason:
                        fill_p = curr_p * (1.0 - 0.001 if pos.side == SignalAction.BUY else 1.0 + 0.001)
                    else:
                        fill_p = curr_p

                    # Calculate realized PnL and commissions
                    if pos.side == SignalAction.BUY:
                        pnl = (fill_p - pos.entry_price) * pos.quantity
                        fee = (fill_p * pos.quantity) * self.fee_rate
                        net_pnl = pnl - fee
                        free_usd += (pos.quantity * fill_p) - fee
                    else:  # SHORT_OPEN
                        pnl = (pos.entry_price - fill_p) * pos.quantity
                        fee = (fill_p * pos.quantity) * self.fee_rate
                        net_pnl = pnl - fee
                        free_usd += pos.collateral + net_pnl

                    trade_pnls.append(net_pnl)
                    pct_move = ((fill_p - pos.entry_price) / pos.entry_price) * 100 if pos.side == SignalAction.BUY else ((pos.entry_price - fill_p) / pos.entry_price) * 100
                    executed_trades.append({
                        "trade_id": len(executed_trades) + 1,
                        "pair": pair,
                        "side": pos.side.name,
                        "entry_time": getattr(pos, "entry_time_str", f"Bar {t_idx}"),
                        "exit_time": str(ts)[:19],
                        "capital": float(pos.quantity * pos.entry_price),
                        "quantity": float(pos.quantity),
                        "entry_price": float(pos.entry_price),
                        "exit_price": float(fill_p),
                        "pct_move": float(pct_move),
                        "gross_pnl": float(pnl),
                        "fees": float(fee),
                        "net_pnl": float(net_pnl),
                        "exit_reason": exit_sig.reason,
                    })
                    stop_loss_manager.remove_position(pair)
                    strategy.record_exit(pair, t_idx, net_pnl=net_pnl)

            # 3. Compute live equity
            pos_val = 0.0
            for pair, pos in stop_loss_manager.positions.items():
                curr_p = candle_manager.get_latest_price(pair) or pos.entry_price
                if pos.side == SignalAction.BUY:
                    pos_val += pos.quantity * curr_p
                else:
                    unrealized = (pos.entry_price - curr_p) * pos.quantity
                    pos_val += max(0.0, pos.collateral + unrealized)

            equity = free_usd + pos_val
            equity_records.append({"timestamp": ts, "equity": equity})

            # 4. Check Circuit Breaker (using simulated candle timestamp)
            cb_status = circuit_breaker.update_equity(equity, current_time=ts.timestamp())
            if cb_status == CircuitBreakerStatus.TRIPPED:
                # Emergency cash liquidation
                for pair, pos in list(stop_loss_manager.positions.items()):
                    curr_p = candle_manager.get_latest_price(pair) or pos.entry_price
                    fee = (curr_p * pos.quantity) * self.fee_rate
                    if pos.side == SignalAction.BUY:
                        pnl = (curr_p - pos.entry_price) * pos.quantity
                        net_pnl = pnl - fee
                        free_usd += (pos.quantity * curr_p) - fee
                    else:
                        pnl = (pos.entry_price - curr_p) * pos.quantity
                        net_pnl = pnl - fee
                        free_usd += pos.collateral + net_pnl
                    trade_pnls.append(net_pnl)
                    pct_move = ((curr_p - pos.entry_price) / pos.entry_price) * 100 if pos.side == SignalAction.BUY else ((pos.entry_price - curr_p) / pos.entry_price) * 100
                    executed_trades.append({
                        "trade_id": len(executed_trades) + 1,
                        "pair": pair,
                        "side": pos.side.name,
                        "entry_time": getattr(pos, "entry_time_str", f"Bar {t_idx}"),
                        "exit_time": str(ts)[:19],
                        "capital": float(pos.quantity * pos.entry_price),
                        "quantity": float(pos.quantity),
                        "entry_price": float(pos.entry_price),
                        "exit_price": float(curr_p),
                        "pct_move": float(pct_move),
                        "gross_pnl": float(pnl),
                        "fees": float(fee),
                        "net_pnl": float(net_pnl),
                        "exit_reason": "Circuit Breaker Emergency Liquidation",
                    })
                    stop_loss_manager.remove_position(pair)
                    strategy.record_exit(pair, t_idx, net_pnl=net_pnl)
                circuit_breaker.acknowledge_liquidation()

            # 5. Look for New Entries
            if circuit_breaker.is_trading_allowed():
                signals = strategy.generate_signals(
                    candle_manager,
                    active_pairs=list(stop_loss_manager.positions.keys()),
                    current_bar_index=t_idx,
                )
                if signals:
                    signals.sort(key=lambda s: s.conviction, reverse=True)

                    if getattr(trading_config, "DYNAMIC_CONCURRENCY_ENABLED", True):
                        # Dynamic portfolio concurrency & risk heat partitioning
                        regime_res = regime_detector.detect_regime(candle_manager)
                        current_heat = stop_loss_manager.get_portfolio_risk_heat(equity)
                        sized_orders = sizer.calculate_dynamic_portfolio_sizes(
                            signals=signals,
                            portfolio_equity=equity,
                            free_usd=free_usd,
                            current_open_risk_heat=current_heat,
                            regime=regime_res.regime,
                        )

                        for order in sized_orders:
                            if not order.allowed:
                                continue
                            sig = next((s for s in signals if s.pair == order.pair), None)
                            if sig is None:
                                continue

                            cost = order.estimated_capital
                            fee = cost * self.fee_rate
                            if free_usd >= (cost + fee):
                                free_usd -= (cost + fee)
                                stop_loss_manager.register_position(
                                    pair=sig.pair,
                                    side=sig.action,
                                    entry_price=sig.entry_price,
                                    quantity=order.quantity,
                                    collateral=order.collateral if sig.action == SignalAction.SHORT_OPEN else 0.0,
                                    stop_loss=sig.stop_loss_price,
                                    take_profit=sig.take_profit_price,
                                    atr=sig.atr,
                                    entry_bar=t_idx,
                                )
                                pos = stop_loss_manager.positions.get(sig.pair)
                                if pos:
                                    pos.entry_time_str = str(ts)[:19]
                    else:
                        # Legacy static slot capping fallback
                        current_open = stop_loss_manager.get_open_positions_count()
                        slots = trading_config.MAX_CONCURRENT_POSITIONS - current_open
                        if slots > 0:
                            for sig in signals[:slots]:
                                size_res = sizer.calculate_size(
                                    signal=sig,
                                    portfolio_equity=equity,
                                    free_usd=free_usd,
                                    current_open_positions_count=stop_loss_manager.get_open_positions_count(),
                                )
                                if size_res.allowed:
                                    cost = size_res.estimated_capital
                                    fee = cost * self.fee_rate
                                    if free_usd >= (cost + fee):
                                        free_usd -= (cost + fee)
                                        stop_loss_manager.register_position(
                                            pair=sig.pair,
                                            side=sig.action,
                                            entry_price=sig.entry_price,
                                            quantity=size_res.quantity,
                                            collateral=size_res.collateral if sig.action == SignalAction.SHORT_OPEN else 0.0,
                                            stop_loss=sig.stop_loss_price,
                                            take_profit=sig.take_profit_price,
                                            atr=sig.atr,
                                            entry_bar=t_idx,
                                        )
                                        pos = stop_loss_manager.positions.get(sig.pair)
                                        if pos:
                                            pos.entry_time_str = str(ts)[:19]

        # Simulation complete, close any lingering positions at final bar
        final_ts = timestamps[-1]
        for pair, pos in list(stop_loss_manager.positions.items()):
            curr_p = candle_manager.get_latest_price(pair) or pos.entry_price
            fee = (curr_p * pos.quantity) * self.fee_rate
            if pos.side == SignalAction.BUY:
                pnl = (curr_p - pos.entry_price) * pos.quantity
                net_pnl = pnl - fee
                free_usd += (pos.quantity * curr_p) - fee
            else:
                pnl = (pos.entry_price - curr_p) * pos.quantity
                net_pnl = pnl - fee
                free_usd += pos.collateral + net_pnl
            trade_pnls.append(net_pnl)
            pct_move = ((curr_p - pos.entry_price) / pos.entry_price) * 100 if pos.side == SignalAction.BUY else ((pos.entry_price - curr_p) / pos.entry_price) * 100
            executed_trades.append({
                "trade_id": len(executed_trades) + 1,
                "pair": pair,
                "side": pos.side.name,
                "entry_time": getattr(pos, "entry_time_str", "Early Simulation"),
                "exit_time": str(final_ts)[:19],
                "capital": float(pos.quantity * pos.entry_price),
                "quantity": float(pos.quantity),
                "entry_price": float(pos.entry_price),
                "exit_price": float(curr_p),
                "pct_move": float(pct_move),
                "gross_pnl": float(pnl),
                "fees": float(fee),
                "net_pnl": float(net_pnl),
                "exit_reason": "End of Simulation Close",
            })
            stop_loss_manager.remove_position(pair)

        final_equity = free_usd
        equity_records.append({"timestamp": final_ts, "equity": final_equity})
        equity_df = pd.DataFrame(equity_records)

        # Compute Metrics
        metrics = self._calculate_metrics(equity_df, trade_pnls)
        return equity_df, metrics, executed_trades

    def _calculate_metrics(self, equity_df: pd.DataFrame, trade_pnls: List[float]) -> BacktestMetrics:
        """Calculate Sharpe, Sortino, Calmar, and Hackathon Composite Score."""
        equity_series = equity_df["equity"]
        initial_eq = self.initial_capital
        final_eq = equity_series.iloc[-1]
        cum_ret_pct = ((final_eq - initial_eq) / initial_eq) * 100.0

        # Bar returns
        returns = equity_series.pct_change().dropna()

        # Assuming 15m bars -> 4 * 24 * 365 = 35040 periods per year
        annualization_factor = np.sqrt(35040)

        # 1. Sharpe Ratio
        mean_ret = returns.mean()
        std_ret = returns.std()
        sharpe = float((mean_ret / std_ret) * annualization_factor) if std_ret > 1e-8 else 0.0

        # 2. Sortino Ratio (downside deviation only)
        downside_returns = returns[returns < 0.0]
        downside_std = downside_returns.std()
        sortino = float((mean_ret / downside_std) * annualization_factor) if downside_std > 1e-8 else 0.0

        # 3. Maximum Drawdown & Calmar Ratio
        rolling_max = equity_series.cummax()
        drawdowns = (rolling_max - equity_series) / rolling_max
        max_dd = float(drawdowns.max())

        # Annualized return
        n_bars = len(returns)
        n_years = n_bars / 35040 if n_bars > 0 else 1.0
        annualized_ret = float(((final_eq / initial_eq) ** (1.0 / max(0.01, n_years))) - 1.0)
        calmar = float(annualized_ret / max_dd) if max_dd > 1e-6 else 0.0

        # 4. Hackathon Composite Score: 0.4 * Sortino + 0.3 * Sharpe + 0.3 * Calmar
        composite_score = (0.4 * sortino) + (0.3 * sharpe) + (0.3 * calmar)

        # Trades stats
        total_trades = len(trade_pnls)
        winning = [p for p in trade_pnls if p > 0]
        losing = [p for p in trade_pnls if p < 0]
        win_rate = (len(winning) / total_trades * 100.0) if total_trades > 0 else 0.0
        gross_profit = sum(winning) if winning else 0.0
        gross_loss = abs(sum(losing)) if losing else 1e-8
        profit_factor = gross_profit / gross_loss

        return BacktestMetrics(
            total_trades=total_trades,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            cumulative_return_pct=cum_ret_pct,
            max_drawdown_pct=max_dd * 100.0,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            hackathon_composite_score=composite_score,
            final_equity=final_eq,
        )


def main():
    print("=" * 70)
    print("🥕 CarrotQuant Strategy Backtester & Performance Analyzer")
    print("=" * 70)

    engine = BacktestEngine(
        pairs=[
            "BTC/USD", "ETH/USD", "SOL/USD", "BNB/USD", "XRP/USD", "DOGE/USD",
            "ADA/USD", "AVAX/USD", "LINK/USD", "NEAR/USD", "SUI/USD", "APT/USD"
        ],
        initial_capital=100000.0,
    )

    try:
        # 1480 15m candles = 15.4 days of historical data (1440 active bars + 40 warmup bars)
        data_15m, data_1h = engine.fetch_historical_data(limit_15m=1480, limit_1h=400)
        if not data_15m or len(data_15m) < 2:
            print("❌ Unable to fetch sufficient historical data. Exiting.")
            return

        equity_df, metrics, executed_trades = engine.run_simulation(data_15m, data_1h)

        print("\n" + "=" * 70)
        print("📊 15-DAY BACKTEST PERFORMANCE SUMMARY")
        print("=" * 70)
        print(f"  Starting Capital:            $100,000.00 USD")
        print(f"  Final Equity:                ${metrics.final_equity:,.2f} USD")
        print(f"  Cumulative Return:           {metrics.cumulative_return_pct:+.2f}%")
        print(f"  Max Drawdown:                {metrics.max_drawdown_pct:.2f}%")
        print("-" * 70)
        print(f"  Sharpe Ratio:                {metrics.sharpe_ratio:.3f}")
        print(f"  Sortino Ratio:               {metrics.sortino_ratio:.3f}")
        print(f"  Calmar Ratio:                {metrics.calmar_ratio:.3f}")
        print("*" * 70)
        print(f"  🏆 HACKATHON COMPOSITE SCORE: {metrics.hackathon_composite_score:.3f}")
        print("  Formula: 0.4*Sortino + 0.3*Sharpe + 0.3*Calmar")
        print("*" * 70)
        print(f"  Total Trades:                {metrics.total_trades}")
        print(f"  Win Rate:                    {metrics.win_rate_pct:.1f}% ({metrics.winning_trades}W / {metrics.losing_trades}L)")
        print(f"  Profit Factor:               {metrics.profit_factor:.2f}")
        print("=" * 70)

        if executed_trades:
            print("\n" + "=" * 95)
            print(f"{'#':<3} {'PAIR':<10} {'SIDE':<6} {'CAPITAL':<10} {'ENTRY':<10} {'EXIT':<10} {'MOVE%':<8} {'NET PNL':<11} {'EXIT REASON':<25}")
            print("=" * 95)
            for t in executed_trades:
                print(f"{t['trade_id']:<3} {t['pair']:<10} {t['side']:<6} ${t['capital']:<9.0f} {t['entry_price']:<10.4f} {t['exit_price']:<10.4f} {t['pct_move']:>+6.2f}% ${t['net_pnl']:>+9.2f}  {t['exit_reason']:<25}")
            print("=" * 95)

            # Export to json
            import json
            out_path = Path("docs/backtest_trade_ledger_15d.json")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(executed_trades, f, indent=2)
            print(f"💾 Trade ledger exported to: {out_path}")

    except Exception as e:
        print(f"❌ Error during backtesting: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
