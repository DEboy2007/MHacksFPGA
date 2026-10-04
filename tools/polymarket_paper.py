#!/usr/bin/env python3
"""Live Polymarket market-data paper trader.

This process never places a real order. It discovers one active binary market,
subscribes to the public CLOB market channel, and maintains local resting bid
and ask orders. A live trade that would take either local order is applied to
the existing LMSR model and the paper cash/inventory ledger.

Requires: pip install websocket-client
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "golden"))
from lmsr_mm import LmsrMM, load_table  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "MHacksFPGA/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def parse_json_field(value):
    return json.loads(value) if isinstance(value, str) else value


def discover_market():
    query = urllib.parse.urlencode({
        "active": "true", "closed": "false", "limit": "100",
        "order": "volume24hr", "ascending": "false",
    })
    markets = get_json(f"{GAMMA}?{query}")
    eligible = []
    for market in markets:
        outcomes = parse_json_field(market.get("outcomes", []))
        tokens = parse_json_field(market.get("clobTokenIds", []))
        if (len(outcomes) == 2 and len(tokens) == 2
                and market.get("enableOrderBook")
                and market.get("acceptingOrders")):
            market["_outcomes"] = outcomes
            market["_tokens"] = [str(token) for token in tokens]
            eligible.append(market)
    if not eligible:
        raise RuntimeError("no active binary order-book market found")
    return max(eligible, key=lambda item: float(item.get("volume24hr") or 0))


def fetch_book(token_id):
    return get_json(f"{CLOB}/book?{urllib.parse.urlencode({'token_id': token_id})}")


def levels(values):
    return {float(row["price"]): float(row["size"]) for row in values or []}


class PaperTrader:
    def __init__(self, market, cash_dollars, lb, ls, hs):
        self.market = market
        self.mm = LmsrMM(load_table(), lb=lb, ls=ls, hs=hs)
        self.cash_cents = round(cash_dollars * 100)
        self.inventory = 0
        self.bid_book = {}
        self.ask_book = {}
        self.last_trade = None
        self.fills = 0

    def quote(self):
        bid, ask = self.mm.quote()
        size = 1 << self.mm.ls
        return bid / 100 if bid else None, ask / 100 if ask else None, size

    def apply_book(self, book):
        self.bid_book = levels(book.get("bids"))
        self.ask_book = levels(book.get("asks"))
        self.match_resting_orders("book")

    def apply_price_changes(self, changes):
        for change in changes or []:
            price = float(change["price"])
            size = float(change.get("size", 0))
            side = str(change.get("side", "")).upper()
            target = self.bid_book if side == "BUY" else self.ask_book
            if size:
                target[price] = size
            else:
                target.pop(price, None)
        self.match_resting_orders("book")

    def apply_trade(self, price, size, source="trade"):
        self.last_trade = price
        bid, ask, quote_size = self.quote()
        qty = min(float(size), quote_size)
        if ask is not None and price >= ask:
            self.fill("buy_yes", ask, qty, source)
        elif bid is not None and price <= bid:
            self.fill("sell_yes", bid, qty, source)

    def match_resting_orders(self, source):
        bid, ask, quote_size = self.quote()
        if not self.bid_book or not self.ask_book:
            return
        best_bid = max(self.bid_book)
        best_ask = min(self.ask_book)
        if ask is not None and ask <= best_bid:
            qty = min(quote_size, self.bid_book[best_bid])
            self.bid_book[best_bid] -= qty
            if self.bid_book[best_bid] <= 0:
                del self.bid_book[best_bid]
            self.fill("buy_yes", ask, qty, f"{source}-cross")
        elif bid is not None and bid >= best_ask:
            qty = min(quote_size, self.ask_book[best_ask])
            self.ask_book[best_ask] -= qty
            if self.ask_book[best_ask] <= 0:
                del self.ask_book[best_ask]
            self.fill("sell_yes", bid, qty, f"{source}-cross")

    def fill(self, side, price, qty, source):
        qty = max(0, min(qty, 1 << self.mm.ls))
        if qty < 1:
            return
        cmd = 1 if side == "buy_yes" else 2
        reply = self.mm.handle(cmd, round(qty))
        if not reply or not reply[0] & 1:
            return
        cents = round(price * 100)
        if side == "buy_yes":
            self.cash_cents += cents * round(qty)
            self.inventory -= round(qty)
        else:
            self.cash_cents -= cents * round(qty)
            self.inventory += round(qty)
        self.fills += 1
        bid, ask, _ = self.quote()
        print(json.dumps({
            "type": "paper_fill", "source": source, "side": side,
            "price": price, "qty": round(qty), "bid": bid, "ask": ask,
            "cash": self.cash_cents / 100, "inventory_yes": self.inventory,
            "d": self.mm.d, "fills": self.fills,
        }), flush=True)


def run(args):
    market = discover_market()
    token = market["_tokens"][0]
    print(json.dumps({
        "type": "market", "id": market["id"], "condition_id": market["conditionId"],
        "question": market["question"], "outcomes": market["_outcomes"],
        "yes_token": token, "volume24hr": market.get("volume24hr"),
        "slug": market.get("slug"),
    }), flush=True)
    trader = PaperTrader(market, args.cash, args.lb, args.ls, args.hs)
    try:
        trader.apply_book(fetch_book(token))
    except Exception as exc:
        # Some CLOB deployments require the WebSocket snapshot even though
        # public trades remain available over HTTP.
        print(json.dumps({"type": "book_fallback", "error": str(exc)}),
              file=sys.stderr, flush=True)

    try:
        import websocket
    except ImportError as exc:
        raise SystemExit("install dependency first: python3 -m pip install websocket-client") from exc

    ws = websocket.create_connection(WS, timeout=30, origin="https://polymarket.com")
    ws.send(json.dumps({"assets_ids": market["_tokens"], "type": "market"}))
    print(json.dumps({"type": "connected", "channel": "market",
                      "tokens": market["_tokens"]}), flush=True)
    deadline = time.monotonic() + args.seconds if args.seconds else None
    try:
        while deadline is None or time.monotonic() < deadline:
            raw = ws.recv()
            if raw is None:
                break
            messages = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(messages, list):
                messages = [messages]
            for message in messages:
                event = message.get("event_type", message.get("type", ""))
                if event in ("book", "book_update"):
                    trader.apply_book(message)
                elif event in ("price_change", "price_changes"):
                    trader.apply_price_changes(message.get("price_changes", [message]))
                elif event in ("last_trade_price", "trade", "trades"):
                    trader.apply_trade(float(message["price"]),
                                       float(message.get("size", 1)))
    finally:
        ws.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cash", type=float, default=100_000_000,
                        help="starting paper cash in dollars")
    parser.add_argument("--lb", type=int, default=8)
    parser.add_argument("--ls", type=int, default=3)
    parser.add_argument("--hs", type=int, default=0)
    parser.add_argument("--seconds", type=float,
                        help="stop after this many seconds; default is continuous")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
