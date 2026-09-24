"""Execution uses exact-unit, date-matched official data without fallback."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from src.v4_execution import (normalize_official_rows, parse_official_day,
                              resolve_execution_prices, read_execution_table)


def record():
    return dict(date="2024-10-01", symbol="2330.TW", source="TWSE_OFFICIAL",
                volume=2000, trading_value=201000, open=99, high=103, low=98, close=102)


def payload(market):
    fields = (["證券代號", "開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"]
              if market == "TWSE" else
              ["代號", "開盤", "最高", "最低", "收盤", "成交股數", "成交金額(元)"])
    return {"date": "20241001", "tables": [{"fields": fields,
            "data": [["2330", "99", "103", "98", "102", "2,000", "201,000"]]}]}


class ExecutionTests(unittest.TestCase):
    def test_official_average_and_missing_never_fallback(self):
        table = normalize_official_rows([record()])
        prices = resolve_execution_prices(table, "2024-10-01", ["2330.TW", "6488.TWO"],
                                          "official_average", {"6488.TWO": 100})
        self.assertEqual(prices["2330.TW"], 100.5)
        self.assertTrue(np.isnan(prices["6488.TWO"]))
        proxy = resolve_execution_prices(table, "2024-10-01", ["2330.TW"], "open_proxy",
                                         {"2330.TW": 99})
        self.assertEqual(proxy.iloc[0], 99)
        with self.assertRaises(ValueError):
            resolve_execution_prices(table, "2024-10-01", [], "open_proxy")

    def test_invalid_official_values_fail_closed(self):
        for field, value in [("volume", 0), ("volume", -1), ("trading_value", None),
                             ("trading_value", np.inf), ("source", "Yahoo")]:
            row = record()
            row[field] = value
            table = normalize_official_rows([row])
            self.assertFalse(table.official_execution_available.iloc[0])
            self.assertTrue(np.isnan(table.average_execution_price.iloc[0]))
            self.assertTrue(table.quality_flags.iloc[0])

    def test_exact_units_and_market_routing(self):
        for market, suffix in [("TWSE", ".TW"), ("TPEx", ".TWO")]:
            frame = parse_official_day(payload(market), market, "2024-10-01")
            self.assertEqual(frame.symbol.iloc[0], "2330" + suffix)
            self.assertEqual(frame.volume.iloc[0], 2000)
            self.assertEqual(frame.trading_value.iloc[0], 201000)
            self.assertEqual(frame.average_execution_price.iloc[0], 100.5)

    def test_wrong_date_and_unknown_units_rejected(self):
        with self.assertRaisesRegex(ValueError, "date"):
            parse_official_day(payload("TWSE"), "TWSE", "2024-10-02")
        data = payload("TPEx")
        data["tables"][0]["fields"][-2] = "成交仟股"
        with self.assertRaisesRegex(ValueError, "exact-unit"):
            parse_official_day(data, "TPEx", "2024-10-01")

    def test_duplicate_quotes_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            normalize_official_rows([record(), record()])

    def test_cached_average_not_trusted(self):
        with tempfile.TemporaryDirectory() as directory:
            table = normalize_official_rows([record()])
            table["average_execution_price"] = 0.01
            path = Path(directory) / "data.csv"
            table.to_csv(path, index=False)
            self.assertEqual(read_execution_table(path).average_execution_price.iloc[0], 100.5)

    def test_invalid_proxy_prices_unavailable(self):
        result = resolve_execution_prices(None, "2024-10-01", ["a", "b"], "open_proxy",
                                          {"a": -1, "b": np.inf})
        self.assertTrue(result.isna().all())

    def test_empty_day_and_empty_cache_unavailable(self):
        for table in [None, normalize_official_rows([]), normalize_official_rows([record()])]:
            prices = resolve_execution_prices(table, "2024-10-02", ["2330.TW"], "official_average")
            self.assertTrue(prices.isna().all())

    def test_tpex_management_table_does_not_ambiguate_mainboard(self):
        data = payload("TPEx")
        data["tables"][0]["title"] = "上櫃股票行情"
        data["tables"].append({"title": "管理股票", "fields": data["tables"][0]["fields"], "data": []})
        self.assertEqual(len(parse_official_day(data, "TPEx", "2024-10-01")), 1)

    def test_wrong_market_source_blocked(self):
        row = record()
        row['source'] = 'TPEX_OFFICIAL'
        frame = normalize_official_rows([row])
        self.assertFalse(frame.official_execution_available.iloc[0])
        self.assertIn('WRONG_OFFICIAL_MARKET', frame.quality_flags.iloc[0])

    def test_roster_filter_retains_only_requested_symbol(self):
        frame = parse_official_day(payload('TWSE'), 'TWSE', '2024-10-01', symbols={'6488.TWO'})
        self.assertTrue(frame.empty)
