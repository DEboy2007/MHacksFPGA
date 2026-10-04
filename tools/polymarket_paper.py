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
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "golden"))
from lmsr_mm import CMD_REFERENCE, LmsrMM, load_table  # noqa: E402

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com"
WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "MHacksFPGA/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def parse_json_field(value):
    return json.loads(value) if isinstance(value, str) else value


def discover_market(slug=None):
    """Pick the market to follow: the one named by `slug`, otherwise the
    busiest binary market whose price is in a range our whole-cent quotes can
    do something with (a market at 0.3 cents would just have its quotes pulled)."""
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
            mapped = {str(outcome).strip().upper(): str(token)
                      for outcome, token in zip(outcomes, tokens)}
            if "YES" not in mapped or "NO" not in mapped:
                continue
            market["_tokens"] = mapped
            eligible.append(market)
    if not eligible:
        raise RuntimeError("no active binary order-book market found")
    if slug:
        named = [m for m in eligible if m.get("slug") == slug]
        if not named:
            raise RuntimeError(f"market {slug!r} is not among the 100 busiest binary markets")
        return named[0]

    def quotable(market):
        try:
            bid, ask = float(market.get("bestBid")), float(market.get("bestAsk"))
        except (TypeError, ValueError):
            return False
        return 0.15 <= (bid + ask) / 2 <= 0.85 and ask - bid <= 0.05

    preferred = [m for m in eligible if quotable(m)] or eligible
    return max(preferred, key=lambda item: float(item.get("volume24hr") or 0))


def fetch_book(token_id):
    return get_json(f"{CLOB}/book?{urllib.parse.urlencode({'token_id': token_id})}")


def levels(values):
    return {float(row["price"]): float(row["size"]) for row in values or []}

@dataclass
class MarketState:
    best_bid: float | None = None
    best_bid_size: float = 0.0
    best_ask: float | None = None
    best_ask_size: float = 0.0
    last_trade: float | None = None
    updated_at: float = 0.0

@dataclass
class AssetBook:
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    state: MarketState = field(default_factory=MarketState)

    def refresh(self):
        bid = max(self.bids, default=None)
        ask = min(self.asks, default=None)
        self.state.best_bid = bid
        self.state.best_bid_size = self.bids.get(bid, 0.0) if bid is not None else 0.0
        self.state.best_ask = ask
        self.state.best_ask_size = self.asks.get(ask, 0.0) if ask is not None else 0.0
        self.state.updated_at = time.time()


class PaperTrader:
    def __init__(self, market, cash_dollars, lb, ls, hs, stale_after=30.0):
        self.market = market
        self.mm = LmsrMM(load_table(), lb=lb, ls=ls, hs=hs)
        self.cash_cents = round(cash_dollars * 100)
        self.inventory = 0
        self.books = {asset: AssetBook() for asset in market["_tokens"].values()}
        self.yes_asset = market["_tokens"]["YES"]
        self.fills = 0
        self.stale = True
        self.stale_after = stale_after
        self.last_feed_update = 0.0

    def quote(self):
        bid, ask = self.mm.quote()
        size = 1 << self.mm.ls
        return bid / 100 if bid else None, ask / 100 if ask else None, size

    def apply_book(self, asset_id, book):
        asset = self.books.setdefault(asset_id, AssetBook())
        was_stale = self.stale
        asset.bids = levels(book.get("bids"))
        asset.asks = levels(book.get("asks"))
        asset.refresh()
        self.stale = False
        self.last_feed_update = time.monotonic()
        if asset_id == self.yes_asset:
            self.update_reference()
        if not was_stale:
            self.match_resting_orders("book")

    def apply_price_changes(self, asset_id, changes):
        asset = self.books.setdefault(asset_id, AssetBook())
        for change in changes or []:
            price = float(change["price"])
            size = float(change.get("size", 0))
            side = str(change.get("side", "")).upper()
            target = asset.bids if side == "BUY" else asset.asks
            if size:
                target[price] = size
            else:
                target.pop(price, None)
        asset.refresh()
        if asset_id == self.yes_asset:
            self.update_reference()
        self.last_feed_update = time.monotonic()
        self.match_resting_orders("book")

    def apply_trade(self, asset_id, price, size):
        asset = self.books.setdefault(asset_id, AssetBook())
        asset.state.last_trade = price
        asset.state.updated_at = time.time()

    def update_reference(self):
        state = self.books[self.yes_asset].state
        if state.best_bid is None or state.best_ask is None:
            self.stale = True
            return
        midpoint_cents = int(((state.best_bid + state.best_ask) * 50) // 1)
        midpoint_cents = max(0, min(100, midpoint_cents))
        self.mm.handle(CMD_REFERENCE, midpoint_cents)

    def emit_yes_state(self):
        state = self.books[self.yes_asset].state
        if state.best_bid is None or state.best_ask is None:
            print("STALE", flush=True)
            return
        print("FRESH", flush=True)
        print("BOOK %.8f %.8f %.8f %.8f" %
              (state.best_bid, state.best_bid_size,
               state.best_ask, state.best_ask_size), flush=True)

    def match_resting_orders(self, source):
        bid, ask, quote_size = self.quote()
        yes = self.books[self.yes_asset]
        if not yes.bids or not yes.asks or self.stale:
            return
        best_bid = max(yes.bids)
        best_ask = min(yes.asks)
        if ask is not None and ask <= best_bid:
            qty = min(quote_size, yes.bids[best_bid])
            yes.bids[best_bid] -= qty
            if yes.bids[best_bid] <= 0:
                del yes.bids[best_bid]
            self.fill("buy_yes", ask, qty, f"{source}-cross")
        elif bid is not None and bid >= best_ask:
            qty = min(quote_size, yes.asks[best_ask])
            yes.asks[best_ask] -= qty
            if yes.asks[best_ask] <= 0:
                del yes.asks[best_ask]
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
    market = discover_market(args.market)
    token = market["_tokens"]["YES"]
    print(json.dumps({
        "type": "market", "id": market["id"], "condition_id": market["conditionId"],
        "question": market["question"], "outcomes": market["_outcomes"],
        "yes_token": token, "no_token": market["_tokens"]["NO"],
        "volume24hr": market.get("volume24hr"),
        "slug": market.get("slug"),
    }), flush=True)
    trader = PaperTrader(market, args.cash, args.lb, args.ls, args.hs,
                         args.stale_after)
    try:
        trader.apply_book(token, fetch_book(token))
        if args.exchange_source:
            trader.emit_yes_state()
    except Exception as exc:
        # Some CLOB deployments require the WebSocket snapshot even though
        # public trades remain available over HTTP.
        print(json.dumps({"type": "book_fallback", "error": str(exc)}),
              file=sys.stderr, flush=True)

    try:
        import websocket
    except ImportError as exc:
        raise SystemExit("install dependency first: python3 -m pip install websocket-client") from exc

    deadline = time.monotonic() + args.seconds if args.seconds else None
    reconnects = 0
    while deadline is None or time.monotonic() < deadline:
        ws = None
        try:
            ws = websocket.create_connection(WS, timeout=5,
                                             origin="https://polymarket.com")
            ws.send(json.dumps({"assets_ids": list(market["_tokens"].values()),
                                "type": "market"}))
            reconnects = 0
            print(json.dumps({"type": "connected", "channel": "market",
                              "tokens": market["_tokens"]}), flush=True)
            while deadline is None or time.monotonic() < deadline:
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    age = time.monotonic() - trader.last_feed_update
                    if age >= trader.stale_after:
                        if not trader.stale:
                            trader.stale = True
                            print(json.dumps({"type": "feed_stale",
                                              "age": age}), flush=True)
                        raise ConnectionError("stale Polymarket feed")
                    continue
                if raw is None:
                    raise ConnectionError("Polymarket stream closed")
                messages = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(messages, list):
                    messages = [messages]
                for message in messages:
                    event = message.get("event_type", message.get("type", ""))
                    changes = message.get("price_changes", [])
                    asset_id = str(message.get("asset_id", message.get("asset", "")))
                    if event in ("book", "book_update"):
                        trader.apply_book(asset_id, message)
                    elif event in ("price_change", "price_changes"):
                        for change in changes or [message]:
                            change_asset = str(change.get("asset_id", asset_id))
                            trader.apply_price_changes(change_asset, [change])
                    elif event in ("last_trade_price", "trade", "trades"):
                        trader.apply_trade(asset_id, float(message["price"]),
                                           float(message.get("size", 1)))
                    if args.exchange_source and (
                            event in ("book", "book_update", "price_change",
                                      "price_changes")):
                        trader.emit_yes_state()
        except Exception as exc:
            reconnects += 1
            trader.stale = True
            if args.exchange_source:
                print("STALE", flush=True)
            print(json.dumps({"type": "feed_disconnected", "error": str(exc),
                              "attempt": reconnects}), file=sys.stderr, flush=True)
            if deadline is not None and time.monotonic() >= deadline:
                break
            if reconnects > args.max_reconnects:
                raise RuntimeError("Polymarket feed did not recover") from exc
            time.sleep(min(2 ** min(reconnects, 5), 15))
            try:
                trader.apply_book(token, fetch_book(token))
            except Exception as snapshot_exc:
                print(json.dumps({"type": "snapshot_resync_failed",
                                  "error": str(snapshot_exc)}),
                      file=sys.stderr, flush=True)
        finally:
            if ws is not None:
                ws.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cash", type=float, default=100_000_000,
                        help="starting paper cash in dollars")
    parser.add_argument("--lb", type=int, default=8)
    parser.add_argument("--ls", type=int, default=3)
    parser.add_argument("--hs", type=int, default=0)
    parser.add_argument("--market", metavar="SLUG",
                        help="follow this market (the last part of its polymarket.com URL) "
                             "instead of the busiest one priced between 15 and 85 cents")
    parser.add_argument("--seconds", type=float,
                        help="stop after this many seconds; default is continuous")
    parser.add_argument("--stale-after", type=float, default=30.0,
                        help="suspend execution after this many seconds without a book update")
    parser.add_argument("--max-reconnects", type=int, default=10)
    parser.add_argument("--exchange-source", action="store_true",
                        help="emit normalized BOOK/STALE lines for exchange --source")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
