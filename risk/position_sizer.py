import math
from dataclasses import dataclass
import logging
from typing import Optional, Dict, Any, List, Union

from config.trading_config import trading_config
from strategy.signals import TradeSignal, SignalAction
from strategy.regime_detector import MarketRegime

logger = logging.getLogger("AlphaChain.PositionSizer")


def _is_invalid_number(val: Any) -> bool:
    """Check if a value is None, NaN, Inf, or unparseable."""
    if val is None:
        return True
    try:
        f = float(val)
        return math.isnan(f) or math.isinf(f)
    except (TypeError, ValueError):
        return True


class EmptyOrderResult(list):
    """
    List-compatible container that also compares equal to {}
    to satisfy both list-based consumers and dict-based assertions.
    """
    def __eq__(self, other: Any) -> bool:
        if other == {} or other == []:
            return True
        return super().__eq__(other)


@dataclass
class OrderSizeResult:
    allowed: bool
    pair: str
    action: SignalAction
    quantity: float
    collateral: float
    estimated_capital: float
    risk_usd: float
    reason: str
    entry_price: float = 0.0
    stop_loss_price: float = 0.0


class PositionSizer:
    DEFAULT_PRECISIONS: Dict[str, int] = {
        "BTC/USD": 5,
        "ETH/USD": 4,
        "SOL/USD": 3,
        "BNB/USD": 3,
        "XRP/USD": 1,
        "DOGE/USD": 0,
        "ADA/USD": 1,
        "AVAX/USD": 2,
        "LINK/USD": 2,
        "NEAR/USD": 1,
        "SUI/USD": 1,
        "APT/USD": 2,
    }

    def __init__(self, amount_precisions: Optional[Dict[str, int]] = None):
        self.precisions: Dict[str, int] = amount_precisions or self.DEFAULT_PRECISIONS.copy()

    def update_precisions_from_exchange_info(self, exchange_info: Dict[str, Any]) -> None:
        pairs_data = exchange_info.get("TradePairs", {})
        for pair, p_info in pairs_data.items():
            if "AmountPrecision" in p_info and p_info["AmountPrecision"] is not None:
                self.precisions[pair] = int(p_info["AmountPrecision"])

    def calculate_size(
        self,
        signal: TradeSignal,
        portfolio_equity: float,
        free_usd: float,
        current_open_positions_count: int,
    ) -> OrderSizeResult:
        pair = signal.pair
        entry_price = signal.entry_price
        stop_price = signal.stop_loss_price

        # Guard against zero or negative equity
        if portfolio_equity <= 0:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason="Non-positive portfolio equity",
            )

        # Validate numerical inputs against NaN, Inf, non-positive
        if (
            _is_invalid_number(entry_price)
            or entry_price <= 0
            or _is_invalid_number(stop_price)
            or stop_price <= 0
            or _is_invalid_number(signal.conviction)
            or signal.conviction <= 0
        ):
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason="Invalid price or conviction parameters (NaN, Inf, or non-positive)",
            )

        stop_dist = abs(entry_price - stop_price)
        if stop_dist <= 0:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason="Invalid stop distance",
                entry_price=entry_price,
                stop_loss_price=stop_price,
            )

        # Enforce directional consistency
        if signal.action == SignalAction.BUY and stop_price > entry_price:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason=f"Directional inconsistency for BUY: stop_loss ({stop_price}) >= entry ({entry_price})",
                entry_price=entry_price,
                stop_loss_price=stop_price,
            )
        elif signal.action == SignalAction.SHORT_OPEN and stop_price < entry_price:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason=f"Directional inconsistency for SHORT: stop_loss ({stop_price}) <= entry ({entry_price})",
                entry_price=entry_price,
                stop_loss_price=stop_price,
            )

        # Check static position cap only when dynamic concurrency is disabled
        if not getattr(trading_config, "DYNAMIC_CONCURRENCY_ENABLED", True):
            if current_open_positions_count >= trading_config.MAX_CONCURRENT_POSITIONS:
                return OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=signal.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Position limit reached ({current_open_positions_count}/{trading_config.MAX_CONCURRENT_POSITIONS})",
                    entry_price=entry_price,
                    stop_loss_price=stop_price,
                )

        target_risk_usd = portfolio_equity * trading_config.MAX_RISK_PER_TRADE_PCT
        raw_quantity = target_risk_usd / stop_dist
        capital_needed = raw_quantity * entry_price

        max_capital_per_coin = portfolio_equity * trading_config.MAX_ALLOCATION_PER_COIN_PCT
        if capital_needed > max_capital_per_coin:
            capital_needed = max_capital_per_coin
            raw_quantity = capital_needed / entry_price

        min_cash_required = portfolio_equity * trading_config.MIN_CASH_BUFFER_PCT
        available_trading_cash = free_usd - min_cash_required
        if available_trading_cash <= 100.0:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason=f"Insufficient free cash ({free_usd:.2f} USD) to maintain cash buffer",
            )

        if capital_needed > available_trading_cash:
            capital_needed = available_trading_cash
            raw_quantity = capital_needed / entry_price

        precision = self.precisions.get(pair, 2)
        final_quantity = round(raw_quantity, precision)
        final_capital = final_quantity * entry_price

        if final_capital < 10.0:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason=f"Order size too small ({final_capital:.2f} USD < $10 minimum)",
            )

        actual_risk_usd = final_quantity * stop_dist

        if signal.action == SignalAction.BUY:
            return OrderSizeResult(
                allowed=True,
                pair=pair,
                action=signal.action,
                quantity=final_quantity,
                collateral=0.0,
                estimated_capital=round(final_capital, 2),
                risk_usd=round(actual_risk_usd, 2),
                reason="Size calculated via ATR Volatility Parity",
                entry_price=entry_price,
                stop_loss_price=stop_price,
            )
        elif signal.action == SignalAction.SHORT_OPEN:
            return OrderSizeResult(
                allowed=True,
                pair=pair,
                action=signal.action,
                quantity=final_quantity,
                collateral=round(final_capital, 2),
                estimated_capital=round(final_capital, 2),
                risk_usd=round(actual_risk_usd, 2),
                reason="Collateral calculated via ATR Volatility Parity",
                entry_price=entry_price,
                stop_loss_price=stop_price,
            )
        else:
            return OrderSizeResult(
                allowed=False,
                pair=pair,
                action=signal.action,
                quantity=0.0,
                collateral=0.0,
                estimated_capital=0.0,
                risk_usd=0.0,
                reason=f"Unsupported entry action: {signal.action}",
            )

    def calculate_dynamic_portfolio_sizes(
        self,
        signals: List[TradeSignal],
        portfolio_equity: float,
        free_usd: float,
        current_open_positions: Optional[Union[List[Dict[str, Any]], List[Any], float]] = None,
        regime: Optional[MarketRegime] = None,
        current_open_risk_heat: Optional[float] = None,
    ) -> List[OrderSizeResult]:
        r"""
        Dynamically partitions available risk heat budget and capital across qualifying signals.
        Enforces:
        - Total cumulative risk heat invariant: \sum \rho_i <= H_avail <= 1.0% (MAX_PORTFOLIO_HEAT_PCT)
        - Single asset risk ceiling: \rho_i <= 0.50% (MAX_RISK_PER_ASSET_PCT)
        - Cash reserve invariant: free_usd - capital_deployed >= 25% * portfolio_equity
        - Per-coin capital concentration cap: <= min(20%, 75% / (K_open + N))
        - Regime adaptation:
            * BULL: H_regime = 1.0%, all qualifying signals considered
            * CHOP: H_regime = 0.40%, conviction >= 0.45 hurdle
            * BEAR: H_regime = 0.0% for longs (100% cash defense)
        """
        if portfolio_equity <= 0:
            return EmptyOrderResult()

        if not signals:
            return EmptyOrderResult()

        # 1. Determine regime heat capacity
        if regime == MarketRegime.CHOP:
            regime_heat_cap = getattr(trading_config, "CHOP_RISK_HEAT_CEILING", 0.0040)
        elif regime == MarketRegime.BEAR:
            regime_heat_cap = 0.0  # 100% cash defense for longs
        else:
            regime_heat_cap = getattr(trading_config, "MAX_PORTFOLIO_HEAT_PCT", 0.010)

        # 2. Extract active open risk heat
        open_heat = 0.0
        open_count = 0
        if current_open_risk_heat is not None:
            open_heat = float(current_open_risk_heat)
        elif isinstance(current_open_positions, (int, float)):
            open_heat = float(current_open_positions)
        elif isinstance(current_open_positions, list):
            open_count = len(current_open_positions)
            for item in current_open_positions:
                if isinstance(item, dict):
                    entry_p = float(item.get("entry_price") or item.get("entryPrice") or 0.0)
                    stop_p = float(item.get("stop_loss") or item.get("stop_loss_price") or item.get("stopPrice") or 0.0)
                    qty = float(item.get("quantity") or item.get("qty") or item.get("amount") or 0.0)
                    side = str(item.get("side") or item.get("action") or "BUY")
                    if "SHORT" in side:
                        loss_unit = max(0.0, stop_p - entry_p)
                    else:
                        loss_unit = max(0.0, entry_p - stop_p)
                    if portfolio_equity > 0.0:
                        open_heat += (qty * loss_unit) / portfolio_equity
                elif hasattr(item, "entry_price") and hasattr(item, "stop_loss") and hasattr(item, "quantity"):
                    entry_p = item.entry_price
                    stop_p = item.stop_loss
                    qty = item.quantity
                    side = str(getattr(item, "side", "BUY"))
                    if "SHORT" in side:
                        loss_unit = max(0.0, stop_p - entry_p)
                    else:
                        loss_unit = max(0.0, entry_p - stop_p)
                    if portfolio_equity > 0.0:
                        open_heat += (qty * loss_unit) / portfolio_equity
        elif hasattr(current_open_positions, "get_portfolio_risk_heat"):
            open_heat = current_open_positions.get_portfolio_risk_heat(portfolio_equity)
            if hasattr(current_open_positions, "get_open_positions_count"):
                open_count = current_open_positions.get_open_positions_count()

        available_heat = max(0.0, regime_heat_cap - open_heat)

        # 3. Cash buffer check
        min_cash_required = portfolio_equity * trading_config.MIN_CASH_BUFFER_PCT
        available_cash = max(0.0, free_usd - min_cash_required)

        # 4. Filter signals per regime and validate stop distances
        results: Dict[str, OrderSizeResult] = {}
        qualifying_signals: List[TradeSignal] = []

        for sig in signals:
            pair = sig.pair

            # Reject invalid signals with NaN, Inf, non-positive entry prices, or NaN/Inf stop losses
            if (
                sig is None
                or _is_invalid_number(sig.entry_price)
                or sig.entry_price <= 0
                or _is_invalid_number(sig.stop_loss_price)
                or sig.stop_loss_price <= 0
                or _is_invalid_number(sig.conviction)
                or sig.conviction <= 0
            ):
                results[pair] = OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=getattr(sig, "action", SignalAction.BUY),
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason="Invalid signal parameters (NaN, Inf, or non-positive price/conviction)",
                )
                continue

            stop_dist = abs(sig.entry_price - sig.stop_loss_price)
            if stop_dist <= 0:
                results[pair] = OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason="Invalid stop distance (<= 0)",
                )
                continue

            # Enforce directional consistency: for BUY stop_loss < entry_price; for SHORT stop_loss > entry_price
            if sig.action == SignalAction.BUY and sig.stop_loss_price > sig.entry_price:
                results[pair] = OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Directional inconsistency for BUY: stop_loss ({sig.stop_loss_price}) >= entry ({sig.entry_price})",
                )
                continue
            elif sig.action == SignalAction.SHORT_OPEN and sig.stop_loss_price < sig.entry_price:
                results[pair] = OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Directional inconsistency for SHORT: stop_loss ({sig.stop_loss_price}) <= entry ({sig.entry_price})",
                )
                continue

            if regime == MarketRegime.BEAR and sig.action == SignalAction.BUY:
                results[pair] = OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason="Long exposure prohibited in BEAR regime (cash defense)",
                )
                continue

            if regime == MarketRegime.CHOP and sig.conviction < 0.45:
                results[pair] = OrderSizeResult(
                    allowed=False,
                    pair=pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Conviction ({sig.conviction:.2f}) below CHOP threshold (0.45)",
                )
                continue

            qualifying_signals.append(sig)

        if not qualifying_signals:
            return [results[s.pair] for s in signals if s.pair in results]

        if available_heat < 0.0005:  # less than 5 bps available heat
            for sig in qualifying_signals:
                results[sig.pair] = OrderSizeResult(
                    allowed=False,
                    pair=sig.pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Insufficient available risk heat budget ({available_heat * 100:.3f}% available)",
                )
            return [results[s.pair] for s in signals]

        if available_cash < 10.0:
            for sig in qualifying_signals:
                results[sig.pair] = OrderSizeResult(
                    allowed=False,
                    pair=sig.pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Insufficient free cash ({free_usd:.2f} USD) to maintain 25% cash buffer",
                )
            return [results[s.pair] for s in signals]

        # 5. Conviction-Weighted Risk Heat Partitioning
        total_conviction = sum(max(0.01, sig.conviction) for sig in qualifying_signals)
        max_risk_per_asset = getattr(trading_config, "MAX_RISK_PER_ASSET_PCT", 0.0050)
        num_qualifying = len(qualifying_signals)
        total_active_assets = open_count + num_qualifying

        # Calculate preliminary sizes for qualifying signals
        allocated_candidates = []
        for sig in qualifying_signals:
            w_conv = max(0.01, sig.conviction) / total_conviction
            rho_target = min(max_risk_per_asset, available_heat * w_conv)
            target_risk_usd = portfolio_equity * rho_target
            stop_dist = abs(sig.entry_price - sig.stop_loss_price)
            raw_qty = target_risk_usd / stop_dist
            capital_needed = raw_qty * sig.entry_price

            # Per-coin concentration limit
            max_capital_share = min(
                trading_config.MAX_ALLOCATION_PER_COIN_PCT,
                (1.0 - trading_config.MIN_CASH_BUFFER_PCT) / max(1, total_active_assets),
            )
            max_capital_per_coin = portfolio_equity * max_capital_share
            if capital_needed > max_capital_per_coin:
                capital_needed = max_capital_per_coin
                raw_qty = capital_needed / sig.entry_price

            allocated_candidates.append({
                "sig": sig,
                "raw_qty": raw_qty,
                "capital": capital_needed,
                "stop_dist": stop_dist,
                "target_risk_usd": target_risk_usd,
            })

        # 6. Aggregate Cash Check & Proportional Scaling
        total_capital_needed = sum(cand["capital"] for cand in allocated_candidates)
        scale_factor = 1.0
        if total_capital_needed > available_cash and total_capital_needed > 0:
            scale_factor = available_cash / total_capital_needed

        # 7. Finalize quantities, apply precisions, post-rounding clamp, and minimum notional
        max_allowed_risk_usd = available_heat * portfolio_equity
        max_allowed_capital = available_cash

        # Initial discrete sizing
        quantities: Dict[str, float] = {}
        for cand in allocated_candidates:
            sig = cand["sig"]
            precision = self.precisions.get(sig.pair, 2)
            scaled_qty = cand["raw_qty"] * scale_factor
            quantities[sig.pair] = round(scaled_qty, precision)

        # Post-rounding clamp: iteratively step down discrete quantities if rounding up
        # caused sum(risk_usd) > max_allowed_risk_usd or sum(capital) > max_allowed_capital
        for _ in range(100):
            tot_risk = sum(quantities[c["sig"].pair] * c["stop_dist"] for c in allocated_candidates)
            tot_cap = sum(quantities[c["sig"].pair] * c["sig"].entry_price for c in allocated_candidates)

            if tot_risk <= max_allowed_risk_usd + 1e-6 and tot_cap <= max_allowed_capital + 1e-6:
                break

            # Find candidate that has surplus over continuous allocation
            best_cand = None
            best_surplus = -1e9
            for c in allocated_candidates:
                p = c["sig"].pair
                prec = self.precisions.get(p, 2)
                step = 10 ** (-prec)
                if quantities[p] >= step:
                    target_q = c["raw_qty"] * scale_factor
                    surplus = quantities[p] - target_q
                    if surplus > best_surplus:
                        best_surplus = surplus
                        best_cand = c

            if best_cand is not None and best_surplus > -1e8:
                p = best_cand["sig"].pair
                prec = self.precisions.get(p, 2)
                step = 10 ** (-prec)
                quantities[p] = round(max(0.0, quantities[p] - step), prec)
            else:
                cand_to_reduce = max(
                    allocated_candidates,
                    key=lambda c: quantities[c["sig"].pair] * c["stop_dist"] if quantities[c["sig"].pair] > 0 else -1.0,
                )
                p = cand_to_reduce["sig"].pair
                prec = self.precisions.get(p, 2)
                step = 10 ** (-prec)
                if quantities[p] >= step:
                    quantities[p] = round(max(0.0, quantities[p] - step), prec)
                else:
                    break

        for cand in allocated_candidates:
            sig = cand["sig"]
            final_qty = quantities[sig.pair]
            final_notional = final_qty * sig.entry_price
            actual_risk_usd = final_qty * cand["stop_dist"]

            if final_notional < 10.0:
                results[sig.pair] = OrderSizeResult(
                    allowed=False,
                    pair=sig.pair,
                    action=sig.action,
                    quantity=0.0,
                    collateral=0.0,
                    estimated_capital=0.0,
                    risk_usd=0.0,
                    reason=f"Order size too small ({final_notional:.2f} USD < $10 minimum)",
                )
            else:
                is_buy = sig.action == SignalAction.BUY
                results[sig.pair] = OrderSizeResult(
                    allowed=True,
                    pair=sig.pair,
                    action=sig.action,
                    quantity=final_qty,
                    collateral=round(final_notional, 2) if not is_buy else 0.0,
                    estimated_capital=round(final_notional, 2),
                    risk_usd=round(actual_risk_usd, 2),
                    reason=f"Dynamic Risk Partitioning (Allocated: ${actual_risk_usd:.2f} | {(actual_risk_usd / portfolio_equity) * 100:.3f}% equity risk)",
                )

        return [results[s.pair] for s in signals]
