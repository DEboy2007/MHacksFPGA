import unittest

from polymarket_paper import AssetBook, PaperTrader


class PolymarketStateTests(unittest.TestCase):
    def market(self):
        return {"_tokens": {"YES": "yes-id", "NO": "no-id"}}

    def trader(self):
        return PaperTrader(self.market(), 1000, 8, 3, 0)

    def test_books_are_separate_and_yes_is_label_selected(self):
        trader = self.trader()
        trader.apply_book("yes-id", {"bids": [{"price": "0.40", "size": "8"}],
                                     "asks": [{"price": "0.60", "size": "5"}]})
        trader.apply_book("no-id", {"bids": [{"price": "0.10", "size": "9"}],
                                    "asks": [{"price": "0.90", "size": "7"}]})
        self.assertEqual(trader.yes_asset, "yes-id")
        self.assertEqual(trader.books["yes-id"].state.best_bid, 0.40)
        self.assertEqual(trader.books["no-id"].state.best_bid, 0.10)
        self.assertEqual(trader.mm.reference, 50)

    def test_reference_updates_without_changing_inventory(self):
        trader = self.trader()
        trader.apply_book("yes-id", {"bids": [{"price": "0.62", "size": "8"}],
                                     "asks": [{"price": "0.64", "size": "8"}]})
        self.assertEqual(trader.mm.reference, 63)
        self.assertEqual(trader.mm.d, 0)

    def test_reference_protocol_shifts_quotes_without_inventory_change(self):
        trader = self.trader()
        before = trader.mm.quote()
        reply = trader.mm.handle(6, 60)
        self.assertTrue(reply[0] & 1)
        self.assertEqual(trader.mm.d, 0)
        self.assertEqual(trader.mm.quote(), (before[0] + 10, before[1] + 10))

    def test_public_trade_does_not_fill_mm(self):
        trader = self.trader()
        trader.apply_book("yes-id", {"bids": [{"price": "0.40", "size": "8"}],
                                     "asks": [{"price": "0.60", "size": "8"}]})
        before = (trader.mm.d, trader.mm.fills)
        trader.apply_trade("yes-id", 0.50, 8)
        self.assertEqual((trader.mm.d, trader.mm.fills), before)

    def test_stale_feed_suspends_book_execution(self):
        trader = self.trader()
        trader.stale = True
        trader.apply_book("yes-id", {"bids": [{"price": "0.90", "size": "8"}],
                                     "asks": [{"price": "0.50", "size": "8"}]})
        trader.stale = True
        trader.match_resting_orders("stale")
        self.assertEqual(trader.mm.fills, 0)

    def test_book_cross_can_fill_only_simulated_resting_order(self):
        trader = self.trader()
        trader.stale = False
        trader.apply_book("yes-id", {"bids": [{"price": "0.90", "size": "8"}],
                                     "asks": [{"price": "0.50", "size": "8"}]})
        self.assertEqual(trader.mm.fills, 1)


if __name__ == "__main__":
    unittest.main()
