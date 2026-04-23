#!/usr/bin/env python3
"""Binance signal scanner with simulated buys and optional Telegram alerts.

Strategy adapted from the provided Pine Script:
- Filter symbols: 24h green and quote volume >= threshold
- Compute volume spike + breakout + bullish candle + cooldown
- Simulate BUY events (no real orders)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

BINANCE_FAPI = "https://fapi.binance.com"


@dataclasses.dataclass
class Config:
    timeframe: str = "15m"
    volume_length: int = 20
    volume_multiplier: float = 2.0
    breakout_length: int = 10
    cooldown_bars: int = 20
    min_quote_volume: float = 5_000_000
    quote_asset: str = "USDT"
    poll_seconds: int = 30


@dataclasses.dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class HttpClient:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def get_json(self, url: str, params: dict | None = None) -> list | dict:
        full_url = url
        if params:
            full_url = f"{url}?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(full_url, headers={"User-Agent": "volume-spike-sim/1.0"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "volume-spike-sim/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))


class BinanceClient:
    def __init__(self, timeout: int = 15):
        self.http = HttpClient(timeout=timeout)

    def get_futures_24h_tickers(self) -> List[dict]:
        data = self.http.get_json(f"{BINANCE_FAPI}/fapi/v1/ticker/24hr")
        return data if isinstance(data, list) else []

    def get_klines(self, symbol: str, interval: str, limit: int) -> List[Candle]:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        rows = self.http.get_json(f"{BINANCE_FAPI}/fapi/v1/klines", params=params)
        if not isinstance(rows, list):
            return []

        candles: List[Candle] = []
        for row in rows:
            candles.append(
                Candle(
                    open_time=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return candles


class TelegramNotifier:
    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        self.http = HttpClient(timeout=10)

    def send(self, text: str) -> None:
        if not self.enabled:
            logging.info("Telegram non configuré. Message: %s", text)
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text}
        try:
            self.http.post_json(url, payload)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Echec envoi Telegram: %s", exc)


def sma(values: List[float]) -> float:
    if not values:
        return math.nan
    return sum(values) / len(values)


def to_utc(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def get_filtered_pairs(client: BinanceClient, cfg: Config) -> List[str]:
    tickers = client.get_futures_24h_tickers()
    pairs: List[str] = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith(cfg.quote_asset):
            continue

        try:
            change = float(t["priceChangePercent"])
            quote_volume = float(t["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            continue

        if change > 0 and quote_volume >= cfg.min_quote_volume:
            pairs.append(symbol)

    return sorted(set(pairs))


def has_signal(candles: List[Candle], cfg: Config, last_signal_index: int | None) -> bool:
    min_needed = max(cfg.volume_length + 2, cfg.breakout_length + 2)
    if len(candles) < min_needed:
        return False

    current = candles[-1]
    previous_volumes = [c.volume for c in candles[-(cfg.volume_length + 1) : -1]]
    avg_volume = sma(previous_volumes)
    if math.isnan(avg_volume) or avg_volume <= 0:
        return False

    volume_spike = current.volume > avg_volume * cfg.volume_multiplier
    lookback_highs = [c.high for c in candles[-(cfg.breakout_length + 1) : -1]]
    price_breakout = current.close > max(lookback_highs)
    bullish_candle = current.close > current.open
    raw_signal = volume_spike and price_breakout and bullish_candle

    current_index = len(candles) - 1
    can_signal = last_signal_index is None or (current_index - last_signal_index > cfg.cooldown_bars)
    return raw_signal and can_signal


def run(cfg: Config) -> None:
    client = BinanceClient()
    notifier = TelegramNotifier(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
    )

    last_signal_by_symbol: Dict[str, int] = {}
    simulated_positions: Dict[str, dict] = {}

    while True:
        try:
            pairs = get_filtered_pairs(client, cfg)
            logging.info("Paires filtrées (vert + volume >= %.0f): %d", cfg.min_quote_volume, len(pairs))

            for symbol in pairs:
                kl_limit = max(cfg.volume_length, cfg.breakout_length) + 5
                candles = client.get_klines(symbol=symbol, interval=cfg.timeframe, limit=kl_limit)
                if not candles:
                    continue

                last_idx = last_signal_by_symbol.get(symbol)
                if has_signal(candles, cfg, last_idx):
                    c = candles[-1]
                    last_signal_by_symbol[symbol] = len(candles) - 1

                    simulated_positions[symbol] = {
                        "entry_price": c.close,
                        "entry_time": c.open_time,
                        "timeframe": cfg.timeframe,
                    }

                    msg = (
                        f"🚀 SIM BUY {symbol}\n"
                        f"TF: {cfg.timeframe}\n"
                        f"Entry: {c.close:.6f}\n"
                        f"Time: {to_utc(c.open_time)}"
                    )
                    logging.info(msg.replace("\n", " | "))
                    notifier.send(msg)

            logging.info("Positions simulées actives: %d", len(simulated_positions))
        except Exception as exc:  # noqa: BLE001
            logging.warning("Erreur réseau/API Binance ou inattendue: %s", exc)

        time.sleep(cfg.poll_seconds)


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Binance green pairs scanner + simulated buy")
    parser.add_argument("--timeframe", default="15m", help="Timeframe Binance (défaut: 15m)")
    parser.add_argument("--volume-length", type=int, default=20)
    parser.add_argument("--volume-multiplier", type=float, default=2.0)
    parser.add_argument("--breakout-length", type=int, default=10)
    parser.add_argument("--cooldown-bars", type=int, default=20)
    parser.add_argument("--min-quote-volume", type=float, default=5_000_000)
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()

    return Config(
        timeframe=args.timeframe,
        volume_length=args.volume_length,
        volume_multiplier=args.volume_multiplier,
        breakout_length=args.breakout_length,
        cooldown_bars=args.cooldown_bars,
        min_quote_volume=args.min_quote_volume,
        poll_seconds=args.poll_seconds,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    run(parse_args())
