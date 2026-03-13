"""Conservative BTC 4H long-only strategy with OKX demo integration.

This revision keeps the previous framework (config + logging + post-trade self-review),
but replaces the high-turnover multi-asset core with a stricter BTC-only trading logic:
- Universe: BTC perpetual only (default)
- Timeframe: 4H
- Direction: long-only
- Filters: ADX + MA slope + Donchian breakout
- Risk: ATR stop, staged take-profit, leverage cap

Disclaimer: educational use only; not financial advice.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import ccxt  # type: ignore
import numpy as np
import pandas as pd


@dataclass
class StrategyConfig:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "4h"
    ohlcv_limit: int = 400

    # leverage/risk
    leverage: int = 3
    max_leverage: int = 5
    risk_per_trade: float = 0.01
    max_position_value_ratio: float = 0.80

    # filters
    adx_period: int = 14
    adx_min: float = 20.0
    ma_fast: int = 20
    ma_slow: int = 60
    donchian_window: int = 20

    # exits
    atr_period: int = 14
    atr_stop_mult: float = 2.0
    tp1_atr_mult: float = 1.5
    tp2_atr_mult: float = 3.0
    tp1_reduce_ratio: float = 0.5

    # ops
    dry_run: bool = True
    min_bars_between_entries: int = 3
    review_log_path: str = "fingpt/FinGPT_Others/FinGPT_Trading/trade_review_log.jsonl"


class OKXBTC4HLongOnlyStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.exchange = self._build_exchange()
        self.review_log = Path(self.config.review_log_path)
        self.review_log.parent.mkdir(parents=True, exist_ok=True)

    def _build_exchange(self):
        return ccxt.okx(
            {
                "apiKey": os.getenv("OKX_API_KEY", ""),
                "secret": os.getenv("OKX_SECRET", ""),
                "password": os.getenv("OKX_PASSWORD", ""),
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap",
                    "flag": "1",  # OKX demo
                },
            }
        )

    @staticmethod
    def _adx(df: pd.DataFrame, n: int) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        up_move = high.diff()
        down_move = -low.diff()

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(n).mean()
        plus_di = 100 * pd.Series(plus_dm, index=df.index).rolling(n).mean() / (atr + 1e-12)
        minus_di = 100 * pd.Series(minus_dm, index=df.index).rolling(n).mean() / (atr + 1e-12)
        dx = ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)) * 100
        return dx.rolling(n).mean()

    def _features(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ma_fast"] = df["close"].rolling(self.config.ma_fast).mean()
        df["ma_slow"] = df["close"].rolling(self.config.ma_slow).mean()
        df["ma_slow_slope"] = df["ma_slow"].diff(3)

        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df["atr"] = tr.rolling(self.config.atr_period).mean()
        df["adx"] = self._adx(df, self.config.adx_period)

        df["donchian_high_prev"] = df["high"].rolling(self.config.donchian_window).max().shift(1)
        df["donchian_low_prev"] = df["low"].rolling(self.config.donchian_window).min().shift(1)
        return df

    def _fetch_ohlcv(self) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(
            self.config.symbol,
            timeframe=self.config.timeframe,
            limit=self.config.ohlcv_limit,
        )
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return self._features(df)

    def _entry_signal(self, df: pd.DataFrame) -> Dict:
        row = df.iloc[-1]
        valid = (
            (row["close"] > row["donchian_high_prev"])
            and (row["ma_fast"] > row["ma_slow"])
            and (row["ma_slow_slope"] > 0)
            and (row["adx"] >= self.config.adx_min)
        )
        return {
            "signal": "buy" if valid else "hold",
            "reason": {
                "breakout": bool(row["close"] > row["donchian_high_prev"]),
                "ma_trend": bool(row["ma_fast"] > row["ma_slow"]),
                "ma_slope_pos": bool(row["ma_slow_slope"] > 0),
                "adx_ok": bool(row["adx"] >= self.config.adx_min),
            },
        }

    def _position_notional(self, equity: float, price: float, atr: float) -> float:
        leverage = min(self.config.leverage, self.config.max_leverage)
        risk_cap = equity * self.config.risk_per_trade
        stop_distance = max(atr * self.config.atr_stop_mult, price * 0.003)
        qty_by_risk = risk_cap / max(stop_distance, 1e-9)
        notional_by_risk = qty_by_risk * price

        max_notional = equity * self.config.max_position_value_ratio * leverage
        return float(min(notional_by_risk, max_notional))

    def _set_leverage(self, leverage: int) -> None:
        if self.config.dry_run:
            return
        self.exchange.set_leverage(leverage, self.config.symbol)

    def _order(self, side: str, amount: float) -> Dict:
        if self.config.dry_run:
            return {
                "id": f"paper-{int(time.time()*1000)}",
                "symbol": self.config.symbol,
                "side": side,
                "amount": amount,
                "status": "closed",
                "dry_run": True,
            }
        return self.exchange.create_order(self.config.symbol, "market", side, amount)

    def _self_review(self, signal: str, meta: Dict, trade: Dict | None) -> Dict:
        issues: List[str] = []
        optimizations: List[str] = []

        if signal == "hold":
            failed = [k for k, v in meta.get("reason", {}).items() if not v]
            issues.append(f"Entry filtered out by: {', '.join(failed) if failed else 'unknown'}")
            optimizations.append("No trade is expected in noisy regime; keep BTC 4H long-only discipline.")

        if trade and trade.get("status") not in {"closed", "filled"}:
            issues.append("Order not fully filled.")
            optimizations.append("Add fallback order type and partial-fill handling.")

        if not issues:
            issues.append("No immediate issue detected.")
            optimizations.append("Track rolling PF/DD and tune ADX/Donchian thresholds with walk-forward validation.")

        review = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": self.config.symbol,
            "signal": signal,
            "meta": meta,
            "issues": issues,
            "optimizations": optimizations,
        }
        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(review, ensure_ascii=False) + "\n")
        return review

    def run_once(self, equity: float = 10_000.0) -> Dict:
        df = self._fetch_ohlcv()
        signal_meta = self._entry_signal(df)
        signal = signal_meta["signal"]

        last = df.iloc[-1]
        price = float(last["close"])
        atr = float(last["atr"])

        if signal == "hold":
            review = self._self_review(signal=signal, meta=signal_meta, trade=None)
            return {"symbol": self.config.symbol, "action": "hold", "review": review}

        leverage = min(self.config.leverage, self.config.max_leverage)
        self._set_leverage(leverage)

        notional = self._position_notional(equity=equity, price=price, atr=atr)
        amount = notional / max(price, 1e-9)
        trade = self._order("buy", amount)

        stop_loss = price - self.config.atr_stop_mult * atr
        tp1 = price + self.config.tp1_atr_mult * atr
        tp2 = price + self.config.tp2_atr_mult * atr

        review = self._self_review(signal=signal, meta=signal_meta, trade=trade)
        return {
            "symbol": self.config.symbol,
            "action": "buy",
            "timeframe": self.config.timeframe,
            "direction": "long_only",
            "leverage": leverage,
            "price": price,
            "atr": atr,
            "notional": notional,
            "amount": amount,
            "risk_plan": {
                "stop_loss": stop_loss,
                "tp1": tp1,
                "tp2": tp2,
                "tp1_reduce_ratio": self.config.tp1_reduce_ratio,
            },
            "trade": trade,
            "review": review,
        }


def main() -> None:
    cfg = StrategyConfig(dry_run=True)
    strategy = OKXBTC4HLongOnlyStrategy(cfg)
    result = strategy.run_once(equity=10_000)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
