#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bitget 永续合约监控（指定币种）
BTC ETH ZEC MU SNDK SKHYNIX
明确提示当前可以买入 / 当前可以做空
"""

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "ZECUSDT",
    "MUUSDT",
    "SNDKUSDT",
    "SKHYNIXUSDT",
]

SCORE_S = 88
SCORE_A = 75
MIN_SCORE_PUSH = 75

def send_discord(embeds=None):
    if not DISCORD_WEBHOOK:
        print("未配置 DISCORD_WEBHOOK")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"embeds": embeds}, timeout=12)
        print(f"Discord状态: {r.status_code}")
    except Exception as e:
        print(f"Discord失败: {e}")

def calc_ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def calc_rsi(s, p=14):
    d = s.diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = -d.where(d < 0, 0).rolling(p).mean()
    rs = g / l
    return 100 - (100 / (1 + rs))

def calc_macd(s):
    ema12 = calc_ema(s, 12)
    ema26 = calc_ema(s, 26)
    macd = ema12 - ema26
    signal = calc_ema(macd, 9)
    hist = macd - signal
    return macd, signal, hist

def calc_atr(df, p=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def get_klines(symbol, granularity="1H", limit=100):
    try:
        r = requests.get(
            "https://api.bitget.com/api/v2/mix/market/candles",
            params={
                "symbol": symbol,
                "productType": "USDT-FUTURES",
                "granularity": granularity,
                "limit": str(limit)
            },
            timeout=10
        )
        data = r.json()
        if data.get("code") != "00000":
            print(f"{symbol} {granularity} 接口错误: {data.get('msg')}")
            return None
        rows = data.get("data", [])
        if len(rows) < 50:
            return None
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "base_vol", "quote_vol"])
        for col in ["open", "high", "low", "close", "base_vol"]:
            df[col] = pd.to_numeric(df[col])
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"{symbol} 获取K线失败: {e}")
        return None

def analyze_tf(df):
    if df is None or len(df) < 60:
        return None

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["base_vol"]

    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    ema200 = calc_ema(close, 200)
    rsi = calc_rsi(close)
    _, _, hist = calc_macd(close)
    atr = calc_atr(df)

    last = float(close.iloc[-1])
    rsi_val = float(rsi.iloc[-1])
    atr_val = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else last * 0.02

    score = 50
    reasons = []
    direction = "中性"

    if last > ema20.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1]:
        score += 22
        reasons.append("EMA多头排列")
        direction = "多头"
    elif last < ema20.iloc[-1] < ema50.iloc[-1] < ema200.iloc[-1]:
        score += 22
        reasons.append("EMA空头排列")
        direction = "空头"
    elif last > ema20.iloc[-1] > ema50.iloc[-1]:
        score += 12
        reasons.append("EMA20/50多头")
        direction = "多头"
    elif last < ema20.iloc[-1] < ema50.iloc[-1]:
        score += 12
        reasons.append("EMA20/50空头")
        direction = "空头"

    if hist.iloc[-1] > 0 and hist.iloc[-2] <= 0:
        score += 15
        reasons.append("MACD金叉")
        direction = "多头"
    elif hist.iloc[-1] < 0 and hist.iloc[-2] >= 0:
        score += 15
        reasons.append("MACD死叉")
        direction = "空头"

    if rsi_val < 30 and direction == "多头":
        score += 12
        reasons.append(f"RSI超卖({rsi_val:.1f})")
    elif rsi_val > 70 and direction == "空头":
        score += 12
        reasons.append(f"RSI超买({rsi_val:.1f})")

    vol_ma = volume.rolling(20).mean().iloc[-1]
    if volume.iloc[-1] > vol_ma * 1.5:
        score += 8
        reasons.append("放量")

    return {
        "score": min(int(score), 100),
        "direction": direction,
        "reasons": reasons,
        "price": last,
        "rsi": rsi_val,
        "atr": atr_val,
        "support": round(float(low.iloc[-20:].min()), 4),
        "resistance": round(float(high.iloc[-20:].max()), 4),
    }

def analyze_symbol(symbol):
    r1 = analyze_tf(get_klines(symbol, "1H", 100))
    r4 = analyze_tf(get_klines(symbol, "4H", 80))
    r1d = analyze_tf(get_klines(symbol, "1D", 60))

    if not r1:
        return None

    total = r1["score"]
    reasons = r1["reasons"][:]
    dirs = [r1["direction"]]

    if r4:
        total = int(total * 0.5 + r4["score"] * 0.3)
        dirs.append(r4["direction"])
        if r4["direction"] == r1["direction"] and r1["direction"] != "中性":
            total += 8
            reasons.append("4H同向")
        elif r4["direction"] not in ("中性", r1["direction"]):
            total -= 10
            reasons.append("4H反向")

    if r1d:
        total = int(total * 0.7 + r1d["score"] * 0.3)
        dirs.append(r1d["direction"])
        if r1d["direction"] == r1["direction"] and r1["direction"] != "中性":
            total += 10
            reasons.append("日线同向")
        elif r1d["direction"] not in ("中性", r1["direction"]):
            total -= 12
            reasons.append("日线反向")

    total = max(0, min(100, total))
    final_dir = "多头" if dirs.count("多头") >= 2 else "空头" if dirs.count("空头") >= 2 else "中性"

    signal = None
    if total >= SCORE_A and final_dir == "多头":
        signal = "买入"
    elif total >= SCORE_A and final_dir == "空头":
        signal = "做空"

    if not signal:
        return None

    price = r1["price"]
    atr = r1["atr"]
    support = r1["support"]
    resistance = r1["resistance"]

    if signal == "买入":
        entry_low = support
        entry_high = round((support + price) / 2, 4)
        stop = round(support - atr * 1.0, 4)
        action = f"**当前可以买入**\n建议区间：{entry_low} - {entry_high}\n止损参考：{stop}"
    else:
        entry_low = round((price + resistance) / 2, 4)
        entry_high = resistance
        stop = round(resistance + atr * 1.0, 4)
        action = f"**当前可以做空**\n建议区间：{entry_low} - {entry_high}\n止损参考：{stop}"

    return {
        "symbol": symbol,
        "score": total,
        "grade": "S" if total >= SCORE_S else "A",
        "signal": signal,
        "price": price,
        "reasons": reasons[:5],
        "action": action,
        "support": support,
        "resistance": resistance,
        "rsi": r1["rsi"],
    }

def main():
    print(f"Bitget指定币种监控开始 {datetime.now(timezone(timedelta(hours=8)))}")
    results = []

    for symbol in SYMBOLS:
        print(f"正在分析 {symbol} ...")
        res = analyze_symbol(symbol)
        if res and res["score"] >= MIN_SCORE_PUSH:
            results.append(res)
            print(f"  → {res['grade']}级 {res['signal']} 评分{res['score']}")
        else:
            print(f"  → 无高分信号")

    if not results:
        print("本次无符合条件的机会")
        return

    results.sort(key=lambda x: x["score"], reverse=True)

    for r in results:
        color = 0x00C853 if r["signal"] == "买入" else 0xD50000
        title = f"{'🟢' if r['grade']=='S' else '🟡'} {r['grade']}级 | {r['symbol']} | {r['signal']}"

        embed = {
            "title": title,
            "color": color,
            "fields": [
                {"name": "当前价格", "value": str(r["price"]), "inline": True},
                {"name": "评分", "value": str(r["score"]), "inline": True},
                {"name": "RSI", "value": f"{r['rsi']:.1f}", "inline": True},
                {"name": "支撑 / 压力", "value": f"{r['support']} / {r['resistance']}", "inline": False},
                {"name": "原因", "value": "\n".join([f"• {x}" for x in r["reasons"]]) or "多周期共振", "inline": False},
                {"name": "操作建议", "value": r["action"], "inline": False},
            ],
            "footer": {"text": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")},
        }
        send_discord([embed])
        time.sleep(0.4)

if __name__ == "__main__":
    main()
