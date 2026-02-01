#!/usr/bin/env python3
import os
import time
import json
import argparse
import random
from typing import Any, Optional
import requests

# ========================
# GLOBAL PnL TRACKERS
# ========================
COST_BASIS = {}     # stores average entry prices per symbol
REALIZED_PNL = 0.0  # total realized PnL from closed trades


# ----------------------------
# Helpers
# ----------------------------
def build_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key, "Content-Type": "application/json"}

def _raise_for_api_error(resp: requests.Response) -> None:
    if 200 <= resp.status_code < 300:
        return
    try:
        data = resp.json()
        detail = data.get("detail") if isinstance(data, dict) else None
    except Exception:
        detail = None
    msg = f"HTTP {resp.status_code}"
    if detail:
        msg += f": {detail}"
    raise RuntimeError(msg)


def api_get(base_url: str, path: str, api_key: str, params: Optional[dict[str, Any]] = None) -> Any:
    url = f"{base_url}{path}"
    resp = requests.get(url, headers=build_headers(api_key), params=params, timeout=15)
    _raise_for_api_error(resp)
    return resp.json()


def api_post(base_url: str, path: str, api_key: str, body: dict[str, Any]) -> Any:
    url = f"{base_url}{path}"
    resp = requests.post(url, headers=build_headers(api_key), data=json.dumps(body), timeout=10)
    if not (200 <= resp.status_code < 300):
        try:
            err = resp.json().get("detail", "")
        except Exception:
            err = resp.text
        raise RuntimeError(f"HTTP {resp.status_code}: {err}")
    return resp.json()


def place_order(api_url: str, api_key: str, order: dict[str, Any]) -> None:
    """Send a single order to the API."""
    try:
        res = api_post(api_url, "/api/v1/orders", api_key, order)
        print(
            f"[OK] {order['side'].upper():4} {order['symbol']:5} "
            f"{order['quantity']:>3} @ {order.get('price', 'MKT')} ({order['order_type']})"
        )

        # ---- Update cost basis & realized PnL ----
        sym = order["symbol"]
        qty = order["quantity"]
        px = order.get("price", 0)
        side = order["side"].lower()

        global COST_BASIS, REALIZED_PNL
        prev_cost = COST_BASIS.get(sym, px)

        if side == "buy":
            # simple running average for buys
            COST_BASIS[sym] = (prev_cost + px) / 2
        elif side == "sell":
            # realized PnL = (sell price - cost) * quantity
            profit = (px - prev_cost) * qty
            REALIZED_PNL += profit
            print(f"[REALIZED] {sym}: +{profit:.2f} → Total Realized={REALIZED_PNL:.2f}")

    except Exception as e:
        print(f"[ERR] Failed order for {order['symbol']}: {e}")


def list_symbols(base_url: str, api_key: str) -> None:
    data = api_get(base_url, "/api/v1/symbols", api_key)
    symbols = data.get("symbols", [])
    if not symbols:
        print("No symbols available.")
        return
    print("Available symbols:")
    for row in symbols:
        print(f"  - {row.get('symbol')}\t{row.get('name')}")

def list_open_orders(base_url: str, api_key: str, symbol: Optional[str] = None) -> None:
    params: dict[str, Any] = {}
    if symbol:
        params["symbol"] = symbol
    data = api_get(base_url, "/api/v1/orders/open", api_key, params=params)
    orders = data.get("orders", [])
    if not orders:
        print("No open orders.")
        return
    print("Open orders:")
    for o in orders:
        price_str = f" @ {o['price']}" if o.get("price") is not None else ""
        print(
            f"  - {o['order_id']} | {o['symbol']} {o['side']} {o['quantity']} {o['order_type']}{price_str} | {o['status']}"
        )

# ----------------------------
# Market Data Helpers
# ----------------------------
def _to_quote(row: dict[str, Any]) -> dict[str, float]:
    bid = float(row.get("bid") or row.get("best_bid") or row.get("bid_price") or 0)
    ask = float(row.get("ask") or row.get("best_ask") or row.get("ask_price") or 0)
    last = float(row.get("last") or row.get("last_price") or row.get("trade_price") or 0)
    mid = (bid + ask) / 2.0 if bid and ask else (last or bid or ask or 0.0)
    return {"bid": bid, "ask": ask, "mid": mid, "last": last}

def get_quote(base_url: str, api_key: str, symbol: str) -> dict[str, float]:
    """Fetch bid/ask/last price for one symbol"""
    data = api_get(base_url, f"/api/v1/quotes/{symbol}", api_key)
    q = data.get("quote", data)
    bid = q.get("bid", 0)
    ask = q.get("ask", 0)
    last = q.get("last", 0)
    mid = (bid + ask) / 2 if bid and ask else last
    return {"bid": bid, "ask": ask, "mid": mid, "last": last}

def get_quotes(base_url: str, api_key: str, symbols: list[str]) -> dict[str, dict[str, float]]:
    """Mocked quotes for offline testing."""
    out = {}
    for s in symbols:
        fair = 100.0 + random.uniform(-5, 5)
        out[s] = {"bid": fair - 0.1, "ask": fair + 0.1, "mid": fair, "last": fair}
    return out



# def get_quotes(base_url: str, api_key: str, symbols: list[str]) -> dict[str, dict[str, float]]:
#     """Fetch quotes for multiple symbols"""
#     data = api_get(base_url, "/api/v1/quotes", api_key)
#     out = {}
#     for q in data.get("quotes", []):
#         sym = q["symbol"]
#         if sym in symbols:
#             bid = q.get("bid", 0)
#             ask = q.get("ask", 0)
#             last = q.get("last", 0)
#             mid = (bid + ask) / 2 if bid and ask else last
#             out[sym] = {"bid": bid, "ask": ask, "mid": mid, "last": last}
#     return out
# ----------------------------
# Position and Risk Helpers
# ----------------------------
def get_positions(base_url: str, api_key: str) -> dict[str, int]:
    """Return your current positions per symbol."""
    try:
        data = api_get(base_url, "/api/v1/positions", api_key)
        return {row["symbol"]: int(row["quantity"]) for row in data.get("positions", [])}
    except Exception:
        return {}  # fallback if endpoint not available


def within_limits(positions: dict[str, int], deltas: dict[str, int],
                  max_pos: int, max_gross: int) -> bool:
    """Check if new trades stay within risk limits."""
    gross = 0
    for sym, delta in deltas.items():
        new_qty = positions.get(sym, 0) + delta
        if abs(new_qty) > max_pos:
            return False
        gross += abs(new_qty)
    return gross <= max_gross
def calc_pnl(api_url: str, api_key: str, symbols: list[str],
             cost_basis: dict[str, float], positions: dict[str, int]) -> float:
    """Compute current unrealized PnL for all open positions."""
    quotes = get_quotes(api_url, api_key, symbols)
    pnl = 0.0
    for sym in symbols:
        pos = positions.get(sym, 0)
        if pos == 0:
            continue
        price = quotes[sym]["mid"]
        cost = cost_basis.get(sym, price)
        pnl += pos * (price - cost)
    return pnl

# ----------------------------
# Order Builders
# ----------------------------
def mkt(symbol: str, qty: int, side: str) -> dict[str, any]:
    return {"symbol": symbol, "side": side, "order_type": "market", "quantity": abs(qty)}

def lmt(symbol: str, qty: int, side: str, price: float) -> dict[str, any]:
    return {"symbol": symbol, "side": side, "order_type": "limit", "quantity": abs(qty), "price": price}
# ----------------------------
# Market-making logic
# ----------------------------
def generate_fair_values(symbols: list[str]) -> dict[str, float]:
    fair = {}
    for s in symbols:
        val = input(f"Enter starting fair value for {s}: ").strip()
        try:
            fair[s] = float(val)
        except ValueError:
            fair[s] = 100.0  # fallback default
    return fair


def update_fair_values(fair: dict[str, float], drift_std: float = 0.5) -> None:
    for s in fair:
        fair[s] = round(fair[s] + random.gauss(0, drift_std), 2)

def make_bid_ask_orders(symbol: str, fair_value: float) -> list[dict[str, Any]]:
    spread = random.uniform(0.1, 0.6)
    qty = random.randint(25, 50)
    bid_px = round(fair_value - spread / 2, 2)
    ask_px = round(fair_value + spread / 2, 2)
    return [
        {"symbol": symbol, "side": "buy", "order_type": "limit", "quantity": qty, "price": bid_px},
        {"symbol": symbol, "side": "sell", "order_type": "limit", "quantity": qty, "price": ask_px},
    ]

def run_mm(api_url: str, api_key: str, symbols: list[str], poll_s: float = 0.5):
    fair = generate_fair_values(symbols)
    print("Initial fair values:", fair)
    while True:
        update_fair_values(fair, drift_std=0.3)
        for sym in symbols:
            for o in make_bid_ask_orders(sym, fair[sym]):
                place_order(api_url, api_key, o)
            print(f"[{sym}] fair={fair[sym]:.2f}")
            time.sleep(0.2)

        # ----- Print current PnL every cycle -----
        positions = get_positions(api_url, api_key)
        unrealized = calc_pnl(api_url, api_key, symbols, COST_BASIS, positions)
        print(f"[PnL] Unrealized={unrealized:.2f} | Realized={REALIZED_PNL:.2f} | Total={unrealized + REALIZED_PNL:.2f}")
        print("------------------------------------------------")

        time.sleep(poll_s)


# =========================================
# ===== MOVING AVERAGE STRATEGY ===========
# =========================================

# ---- HYPERPARAMETERS (edit anytime) ----
MA_SYMBOL = "AAA"         # which symbol to trade
MA_SHORT_WINDOW = 5       # how many ticks for short moving avg
MA_LONG_WINDOW = 20       # how many ticks for long moving avg
MA_QUANTITY = 20          # shares per trade
MA_POLL_INTERVAL = 0.5    # seconds between quote checks
MA_MAX_POS = 200          # max shares per symbol
MA_MAX_GROSS = 400        # total exposure limit
MA_COOLDOWN = 3           # seconds after trade before next one
MA_USE_MARKET_ORDERS = True   # True = market, False = limit

# =========================================
# === STRATEGY PRESETS YOU CAN SWITCH ====
# =========================================

def set_strategy(mode: str):
    global MA_SHORT_WINDOW, MA_LONG_WINDOW, MA_QUANTITY, MA_COOLDOWN, MA_USE_MARKET_ORDERS

    if mode == "volatile":
        # Choppy / volatile market
        MA_SHORT_WINDOW = 15
        MA_LONG_WINDOW = 50
        MA_QUANTITY = 10
        MA_COOLDOWN = 5
        MA_USE_MARKET_ORDERS = False
        print("[SETUP] Strategy = Volatile (Limit orders, cautious)")

    elif mode == "trending":
        # Smooth trending market
        MA_SHORT_WINDOW = 3
        MA_LONG_WINDOW = 10
        MA_QUANTITY = 30
        MA_COOLDOWN = 1
        MA_USE_MARKET_ORDERS = True
        print("[SETUP] Strategy = Trending (Aggressive, Market orders)")

    elif mode == "illiquid":
        # Thin liquidity, wider spreads
        MA_SHORT_WINDOW = 5
        MA_LONG_WINDOW = 20
        MA_QUANTITY = 15
        MA_COOLDOWN = 4
        MA_USE_MARKET_ORDERS = False
        print("[SETUP] Strategy = Illiquid (Limit orders, careful)")

    else:
        print("[SETUP] Unknown mode, keeping defaults.")


def run_etf_arb(api_url: str, api_key: str, symbols: list[str],
                edge_bps: float = 6.0,  # min mispricing to trade
                qty: int = 10,
                poll_s: float = 0.4,
                max_pos: int = 500,
                max_gross: int = 1200):
    assert "ETF" in symbols, "ETF symbol required for etf_arb."
    legs = [s for s in symbols if s != "ETF"]
    print("[etf_arb] running… legs:", legs)

    while True:
        quotes = get_quotes(api_url, api_key, symbols)
        etf = quotes["ETF"]["mid"]
        comp_sum = sum(quotes[s]["mid"] for s in legs)
        if etf <= 0 or comp_sum <= 0:
            time.sleep(poll_s); continue

        spread = etf - comp_sum
        edge = abs(spread) / ((etf + comp_sum) / 2.0) * 1e4  # bps
        pos = get_positions(api_url, api_key)

        if edge >= edge_bps:
            if spread > 0:
                deltas = {"ETF": -qty, **{s: +qty for s in legs}}
            else:
                deltas = {"ETF": +qty, **{s: -qty for s in legs}}

            if not within_limits(pos, deltas, max_pos, max_gross):
                print("[etf_arb] skip: limits"); time.sleep(poll_s); continue

            print(f"[etf_arb] etf={etf:.2f} sum={comp_sum:.2f} spread={spread:.3f} ({edge:.1f}bps)")
            # Use limits at current top-of-book to reduce slippage
            orders = []
            etf_side = "sell" if deltas["ETF"] < 0 else "buy"
            etf_px = quotes["ETF"]["bid"] if etf_side == "sell" else quotes["ETF"]["ask"]
            orders.append(lmt("ETF", abs(deltas["ETF"]), etf_side, etf_px))
            for s in legs:
                side = "buy" if deltas[s] > 0 else "sell"
                px = quotes[s]["ask"] if side == "buy" else quotes[s]["bid"]
                orders.append(lmt(s, abs(deltas[s]), side, px))
            for o in orders: place_order(api_url, api_key, o)

        time.sleep(poll_s)

def run_tracking_error(api_url: str, api_key: str, symbols: list[str],
                       tol_pct: float = 0.20,  # % deviation from 1.00
                       qty: int = 10,
                       poll_s: float = 0.5,
                       max_pos: int = 500,
                       max_gross: int = 1200):
    assert "ETF" in symbols, "ETF symbol required for tracking."
    legs = [s for s in symbols if s != "ETF"]
    print("[tracking] running… legs:", legs)

    while True:
        quotes = get_quotes(api_url, api_key, symbols)
        etf = quotes["ETF"]["mid"]
        comp_sum = sum(quotes[s]["mid"] for s in legs)
        if etf <= 0 or comp_sum <= 0:
            time.sleep(poll_s); continue

        ratio = etf / comp_sum
        dev_pct = (ratio - 1.0) * 100.0
        pos = get_positions(api_url, api_key)

        if abs(dev_pct) >= tol_pct:
            if dev_pct > 0:
                deltas = {"ETF": -qty, **{s: +qty for s in legs}}
            else:
                deltas = {"ETF": +qty, **{s: -qty for s in legs}}

            if not within_limits(pos, deltas, max_pos, max_gross):
                print("[tracking] skip: limits"); time.sleep(poll_s); continue

            print(f"[tracking] ratio={ratio:.4f} (dev={dev_pct:.2f}%) → rebalance")
            # Quick realign with market orders
            orders = []
            orders.append(mkt("ETF", abs(deltas["ETF"]), "sell" if deltas["ETF"] < 0 else "buy"))
            for s in legs:
                side = "buy" if deltas[s] > 0 else "sell"
                orders.append(mkt(s, abs(deltas[s]), side))
            for o in orders: place_order(api_url, api_key, o)

        time.sleep(poll_s)

def run_pairs(api_url: str, api_key: str, symbols: list[str],
              pair: tuple[str, str] = ("AAA", "BBB"),
              lookback: int = 30,
              z_entry: float = 2.0,
              z_exit: float = 0.5,
              qty: int = 10,
              poll_s: float = 0.5,
              max_pos: int = 500,
              max_gross: int = 1200):
    a, b = pair
    assert a in symbols and b in symbols, "pair symbols must be in --symbols"
    print(f"[pairs] running… pair=({a},{b}) L={lookback} z_in={z_entry} z_out={z_exit}")

    hist: list[float] = []
    current_side: Optional[str] = None  # "shortA_longB" or "longA_shortB"

    while True:
        quotes = get_quotes(api_url, api_key, [a, b])
        pa, pb = quotes[a]["mid"], quotes[b]["mid"]
        if pa <= 0 or pb <= 0:
            time.sleep(poll_s); continue

        d = pa - pb
        hist.append(d)
        if len(hist) > lookback:
            hist.pop(0)

        if len(hist) < 3:
            time.sleep(poll_s); continue

        mu = sum(hist) / len(hist)
        var = sum((x - mu) ** 2 for x in hist) / max(1, len(hist) - 1)
        sigma = var ** 0.5
        z = (d - mu) / sigma if sigma > 1e-8 else 0.0

        pos = get_positions(api_url, api_key)

        # Entry
        if current_side is None and abs(z) >= z_entry:
            if z > 0:
                deltas = {a: -qty, b: +qty}; side = "shortA_longB"
            else:
                deltas = {a: +qty, b: -qty}; side = "longA_shortB"

            if within_limits(pos, deltas, max_pos, max_gross):
                print(f"[pairs] ENTER {side} z={z:.2f} (d={d:.3f}, μ={mu:.3f}, σ={sigma:.3f})")
                for sym, delta in deltas.items():
                    place_order(api_url, api_key,
                                mkt(sym, abs(delta), "buy" if delta > 0 else "sell"))
                current_side = side
            else:
                print("[pairs] skip entry: limits")

        # Exit
        elif current_side is not None and abs(z) <= z_exit:
            if current_side == "shortA_longB":
                deltas = {a: +qty, b: -qty}
            else:
                deltas = {a: -qty, b: +qty}

            if within_limits(pos, deltas, max_pos, max_gross):
                print(f"[pairs] EXIT {current_side} z={z:.2f}")
                for sym, delta in deltas.items():
                    place_order(api_url, api_key,
                                mkt(sym, abs(delta), "buy" if delta > 0 else "sell"))
                current_side = None
            else:
                print("[pairs] skip exit: limits")

        time.sleep(poll_s)


def run_moving_avg(api_url: str, api_key: str, symbols: list[str]):
    """
    Simple Moving Average Crossover Strategy.
    If short-term average > long-term average → go long.
    If short-term average < long-term average → go short.
    """
    symbol = MA_SYMBOL if MA_SYMBOL in symbols else symbols[0]
    print(f"[moving_avg] Running on {symbol} | short={MA_SHORT_WINDOW}, long={MA_LONG_WINDOW}")

    prices: list[float] = []
    current_position = 0  # +1 = long, -1 = short, 0 = flat

    while True:
        # 1️⃣ Fetch latest price
        quote = get_quote(api_url, api_key, symbol)
        mid = quote["mid"]
        if not mid or mid <= 0:
            time.sleep(MA_POLL_INTERVAL)
            continue

        # 2️⃣ Store new price
        prices.append(mid)
        if len(prices) > MA_LONG_WINDOW:
            prices.pop(0)

        # 3️⃣ Only trade after enough data collected
        if len(prices) < MA_LONG_WINDOW:
            print(f"[moving_avg] collecting data... ({len(prices)}/{MA_LONG_WINDOW})")
            time.sleep(MA_POLL_INTERVAL)
            continue

        # 4️⃣ Compute averages
        short_avg = sum(prices[-MA_SHORT_WINDOW:]) / MA_SHORT_WINDOW
        long_avg = sum(prices[-MA_LONG_WINDOW:]) / MA_LONG_WINDOW
        diff = short_avg - long_avg

        # 5️⃣ Fetch current positions
        pos = get_positions(api_url, api_key)
        pos_qty = pos.get(symbol, 0)

        # 6️⃣ Decide signal
        if diff > 0 and current_position <= 0:
            # Bullish crossover → go long
            side = "buy"
            qty = MA_QUANTITY if current_position == 0 else 2 * MA_QUANTITY
            if within_limits(pos, {symbol: qty}, MA_MAX_POS, MA_MAX_GROSS):
                order = mkt(symbol, qty, side) if MA_USE_MARKET_ORDERS else lmt(symbol, qty, side, quote["ask"])
                place_order(api_url, api_key, order)
                current_position = 1
                print(f"[moving_avg] LONG signal | short={short_avg:.2f} > long={long_avg:.2f}")
                time.sleep(MA_COOLDOWN)
        elif diff < 0 and current_position >= 0:
            # Bearish crossover → go short
            side = "sell"
            qty = MA_QUANTITY if current_position == 0 else 2 * MA_QUANTITY
            if within_limits(pos, {symbol: -qty}, MA_MAX_POS, MA_MAX_GROSS):
                order = mkt(symbol, qty, side) if MA_USE_MARKET_ORDERS else lmt(symbol, qty, side, quote["bid"])
                place_order(api_url, api_key, order)
                current_position = -1
                print(f"[moving_avg] SHORT signal | short={short_avg:.2f} < long={long_avg:.2f}")
                time.sleep(MA_COOLDOWN)
        else:
            print(f"[moving_avg] holding | short={short_avg:.2f}, long={long_avg:.2f}, pos={current_position}")

        time.sleep(MA_POLL_INTERVAL)

# ----------------------------
# Entry point
# ----------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Automated Market Maker for CTC API")
    parser.add_argument("--api-url", default=os.environ.get("CTC_API_URL", "http://cornelltradingcompetition.org"))
    parser.add_argument("--api-key", default=os.environ.get("CTC_API_KEY") or os.environ.get("X_API_KEY"))
    parser.add_argument("--symbols", default="AAA,BBB,CCC,ETF", help="Comma-separated list of symbols")
    parser.add_argument("--loop", action="store_true", help="Continuously place orders")
    parser.add_argument("--mode", choices=["mm", "etf_arb", "tracking", "pairs", "moving_avg"], default="mm")
    parser.add_argument("--pair", default="AAA,BBB", help="For --mode pairs: e.g. AAA,CCC")


    return parser.parse_args()


def main():
    args = parse_args()
    api_key = args.api_key or input("Enter API key: ").strip()
    if not api_key:
        print("API key required.")
        return 1

    # turn "AAA,BBB,CCC,ETF" into a clean list
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # Ask user to choose strategy tuning before run
    if args.mode == "moving_avg":
        print("Choose strategy preset (volatile / trending / illiquid):")
        choice = input("> ").strip().lower()
        set_strategy(choice)


    # Decide which strategy to run based on --mode flag
    if args.mode == "mm":
        run_mm(args.api_url, api_key, symbols)
    elif args.mode == "etf_arb":
        run_etf_arb(args.api_url, api_key, symbols)
    elif args.mode == "tracking":
        run_tracking_error(args.api_url, api_key, symbols)
    elif args.mode == "pairs":
        a, b = [x.strip().upper() for x in args.pair.split(",")]
        run_pairs(args.api_url, api_key, symbols, pair=(a, b))
    elif args.mode == "moving_avg":
        run_moving_avg(args.api_url, api_key, symbols)
    else:
        print(f"Unknown mode: {args.mode}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())