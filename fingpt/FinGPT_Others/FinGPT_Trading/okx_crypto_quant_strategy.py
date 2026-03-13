"""OKX demo-trading quant strategy template for BTC/ETH + meme coins.

This script implements:
1) Asset split: 80% major coins (BTC/ETH), 20% meme coins.
2) Leverage rules:
   - BTC/ETH: default 2-3x, hard cap 5x.
   - Meme coins: default 5-10x, high-momentum mode 15-20x.
3) Position sizing + risk guardrails.
4) OKX simulated trading via ccxt (set exchange option "flag"="1").
5) Post-trade self-review and strategy optimization suggestions.

Disclaimer: educational use only; not financial advice.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import ccxt  # type: ignore
import numpy as np
import pandas as pd


@dataclass
class StrategyConfig:
    # Portfolio
    major_symbols: List[str] = field(default_factory=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT"])
    meme_symbols: List[str] = field(default_factory=lambda: ["DOGE/USDT:USDT", "PEPE/USDT:USDT"])
    major_weight: float = 0.80
    meme_weight: float = 0.20

    # Leverage constraints
    major_default_leverage: int = 3
    major_max_leverage: int = 5
    meme_default_leverage: int = 8
    meme_high_momentum_leverage: int = 15
    meme_extreme_momentum_leverage: int = 20
    meme_max_leverage: int = 20

    # Risk controls
    per_trade_risk_pct: float = 0.01   # 1% equity max risk per trade
    stop_loss_pct: float = 0.02        # 2% stop loss
    take_profit_pct: float = 0.04      # 4% take profit
    rebalance_interval_sec: int = 300
    ohlcv_timeframe: str = "1h"
    ohlcv_limit: int = 200

    # Runtime
    dry_run: bool = True
    review_log_path: str = "fingpt/FinGPT_Others/FinGPT_Trading/trade_review_log.jsonl"


class OKXCryptoQuantStrategy:
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.exchange = self._build_exchange()
        self.review_log = Path(self.config.review_log_path)
        self.review_log.parent.mkdir(parents=True, exist_ok=True)

    def _build_exchange(self):
        api_key = os.getenv("OKX_API_KEY", "")
        secret = os.getenv("OKX_SECRET", "")
        password = os.getenv("OKX_PASSWORD", "")

        return ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap",
                    "flag": "1",  # OKX demo trading
                },
            }
        )

    @staticmethod
    def _calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        df["ema_fast"] = close.ewm(span=20, adjust=False).mean()
        df["ema_slow"] = close.ewm(span=60, adjust=False).mean()

        delta = close.diff()
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        roll_up = pd.Series(gain).rolling(14).mean()
        roll_down = pd.Series(loss).rolling(14).mean()
        rs = roll_up / (roll_down + 1e-12)
        df["rsi"] = 100 - (100 / (1 + rs))

        df["ret"] = close.pct_change()
        df["volatility"] = df["ret"].rolling(24).std() * np.sqrt(24)
        return df

    def _fetch_ohlcv(self, symbol: str) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(
            symbol,
            timeframe=self.config.ohlcv_timeframe,
            limit=self.config.ohlcv_limit,
        )
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return self._calc_indicators(df)

    def _signal(self, df: pd.DataFrame, is_meme: bool) -> Tuple[str, float]:
        row = df.iloc[-1]
        trend_long = row["ema_fast"] > row["ema_slow"]
        trend_short = row["ema_fast"] < row["ema_slow"]

        momentum = abs(float(df["ret"].tail(6).sum()))

        if trend_long and row["rsi"] < 68:
            return "buy", momentum
        if trend_short and row["rsi"] > 32:
            return "sell", momentum

        # Meme coins can trade momentum breakout signals more aggressively.
        if is_meme:
            breakout_up = row["close"] > df["high"].tail(20).max() * 0.995
            breakout_down = row["close"] < df["low"].tail(20).min() * 1.005
            if breakout_up:
                return "buy", max(momentum, 0.06)
            if breakout_down:
                return "sell", max(momentum, 0.06)

        return "hold", momentum

    def _decide_leverage(self, is_meme: bool, momentum: float) -> int:
        if not is_meme:
            return min(self.config.major_default_leverage, self.config.major_max_leverage)

        if momentum >= 0.12:
            return self.config.meme_extreme_momentum_leverage
        if momentum >= 0.08:
            return self.config.meme_high_momentum_leverage
        return self.config.meme_default_leverage

    def _position_notional(self, equity: float, symbol: str, leverage: int, is_meme: bool) -> float:
        bucket_weight = self.config.meme_weight if is_meme else self.config.major_weight
        universe_size = len(self.config.meme_symbols) if is_meme else len(self.config.major_symbols)
        symbol_weight = bucket_weight / max(universe_size, 1)

        # risk-based cap
        max_risk_notional = equity * self.config.per_trade_risk_pct / self.config.stop_loss_pct
        target_notional = equity * symbol_weight * leverage
        return float(min(target_notional, max_risk_notional))

    def _set_leverage(self, symbol: str, leverage: int) -> None:
        if self.config.dry_run:
            return
        try:
            self.exchange.set_leverage(leverage, symbol)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] set_leverage failed for {symbol}: {exc}")

    def _place_order(self, symbol: str, side: str, notional: float, price: float) -> Dict:
        amount = max(notional / max(price, 1e-9), 0)
        if self.config.dry_run:
            return {
                "id": f"paper-{int(time.time()*1000)}",
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "status": "closed",
                "dry_run": True,
            }

        return self.exchange.create_order(symbol, "market", side, amount)

    def _self_review(self, trade: Dict, signal: str, momentum: float, is_meme: bool) -> Dict:
        issues = []
        optimizations = []

        if signal == "hold":
            issues.append("No trade executed; verify signal thresholds are not overly strict.")
            optimizations.append("Lower EMA/RSI trigger sensitivity for low-volatility regimes.")

        if is_meme and momentum < 0.03:
            issues.append("Meme coin momentum weak; leverage may be too high for current regime.")
            optimizations.append("Reduce meme leverage to 5-8x when momentum < 3%.")

        if trade.get("status") not in {"closed", "filled"}:
            issues.append("Order was not fully filled.")
            optimizations.append("Add retry logic and fallback order type.")

        if not issues:
            issues.append("No immediate execution issue detected.")
            optimizations.append("Continue monitoring rolling Sharpe and drawdown for adaptive tuning.")

        review = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade.get("id"),
            "symbol": trade.get("symbol"),
            "signal": signal,
            "momentum": momentum,
            "is_meme": is_meme,
            "issues": issues,
            "optimizations": optimizations,
        }

        with self.review_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(review, ensure_ascii=False) + "\n")

        return review

    def run_once(self, equity: float = 10_000.0) -> List[Dict]:
        results = []
        symbols = [(s, False) for s in self.config.major_symbols] + [(s, True) for s in self.config.meme_symbols]

        for symbol, is_meme in symbols:
            df = self._fetch_ohlcv(symbol)
            signal, momentum = self._signal(df, is_meme=is_meme)
            last_price = float(df.iloc[-1]["close"])

            leverage = self._decide_leverage(is_meme=is_meme, momentum=momentum)
            leverage = min(leverage, self.config.meme_max_leverage if is_meme else self.config.major_max_leverage)

            if signal == "hold":
                review = self._self_review(
                    trade={"id": None, "symbol": symbol, "status": "closed"},
                    signal=signal,
                    momentum=momentum,
                    is_meme=is_meme,
                )
                results.append({"symbol": symbol, "action": "hold", "review": review})
                continue

            self._set_leverage(symbol, leverage)
            notional = self._position_notional(equity=equity, symbol=symbol, leverage=leverage, is_meme=is_meme)
            trade = self._place_order(symbol=symbol, side=signal, notional=notional, price=last_price)
            review = self._self_review(trade=trade, signal=signal, momentum=momentum, is_meme=is_meme)

            results.append(
                {
                    "symbol": symbol,
                    "action": signal,
                    "is_meme": is_meme,
                    "leverage": leverage,
                    "price": last_price,
                    "notional": notional,
                    "trade": trade,
                    "review": review,
                }
            )

        return results


def main() -> None:
    cfg = StrategyConfig(dry_run=True)
    strategy = OKXCryptoQuantStrategy(cfg)
    output = strategy.run_once(equity=10_000)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
