"""journal.py 的計算與記錄邏輯測試。不連網、不碰 ~/stock-journal：資料夾、同步、取價、大盤、風控都換成假的。

  python -m unittest discover -s ~/.claude/skills/stock-journal/tests -v
"""
import contextlib, io, sys, tempfile, unittest
from argparse import Namespace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.argv = ["journal"]
import journal as j  # noqa: E402

FEES = {"tw_fee_rate": 0.001425, "tw_fee_discount": 0.6, "tw_fee_min": 20, "tw_tax_stock": 0.003,
        "tw_tax_etf": 0.001, "tw_tax_daytrade": 0.0015, "us_fee_per_trade": 0, "us_fee_rate": 0}


class FakeRisk:
    """stock-risk 的替身：1R＝1,500,000 × 1% ＝ 15,000 元，美元匯率固定 30。"""
    CFG = {"capital": 1_500_000, "risk_pct": 1.0}

    @staticmethod
    def fx(code):
        return 1.0 if str(code).isdigit() else 30.0


def row(**kw):
    """一筆交易（Series），沒給的欄位是 NaN。"""
    base = {c: None for c in j.COLS}
    base.update({"id": 1, "code": "2303", "name": "聯電", "entry_date": "2026-09-18", "entry_price": 155.0, "qty": 1.0,
                 "thesis_type": "技術突破", "thesis": "t", "falsify": "f", "status": "open", "plan_days": 10})
    base.update(kw)
    s = pd.Series(base)
    for c in j.NUM_COLS:
        s[c] = pd.to_numeric(s[c], errors="coerce")
    return s


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._saved = {k: getattr(j, k) for k in ("LOCAL", "CSV", "sync", "_risk", "_bench_ret", "prices")}
        self._cfg = dict(j.CFG)
        j.LOCAL, j.CSV = d, d / "trades.csv"
        j.sync = lambda *a, **k: None
        j._risk = lambda: FakeRisk
        j._bench_ret = lambda code, start, end: 1.0
        j.CFG["fees"] = FEES
        j.CFG["falsify_grace_days"] = 2

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(j, k, v)
        j.CFG.clear(); j.CFG.update(self._cfg)
        self.tmp.cleanup()

    def write(self, *rows):
        j.save(pd.DataFrame([r.to_dict() for r in rows], columns=j.COLS))

    def quiet(self, fn, *a):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*a)
        return buf.getvalue()


class TradeCostTest(JournalTest):
    def test_tw_stock_matches_broker_record(self):
        # #18 新唐實際對帳：137.5 → 132，1 張，含成本 −6,125（手續費 117＋112、證交稅 396）
        tc = j.trade_cost(row(code="4919", entry_price=137.5), 132.0, "2026-09-24")
        self.assertEqual(tc["cost"], 625)
        self.assertEqual(tc["net"], -6125)

    def test_tw_stock_fees_floor_and_tax(self):
        tc = j.trade_cost(row(entry_price=100.0), 110.0, "2026-09-25")
        self.assertEqual(tc["cost"], 85 + 94 + 330)   # floor(100000×0.000855)、floor(110000×0.000855)、110000×0.3%
        self.assertEqual(tc["net"], 10000 - 509)
        self.assertEqual(tc["net_pct"], 9.49)

    def test_odd_lot_uses_minimum_fee(self):
        tc = j.trade_cost(row(entry_price=100.0, qty=0.01), 110.0, "2026-09-25")
        self.assertEqual(tc["cost"], 20 + 20 + 3)

    def test_etf_tax_rate(self):
        tc = j.trade_cost(row(code="0050", entry_price=100.0), 100.0, "2026-09-25")
        self.assertEqual(tc["cost"], 85 + 85 + 100)   # ETF 證交稅 0.1%

    def test_daytrade_tax_rate(self):
        tc = j.trade_cost(row(entry_price=100.0, entry_date="2026-09-25"), 100.0, "2026-09-25")
        self.assertEqual(tc["cost"], 85 + 85 + 150)   # 同日進出 0.15%

    def test_us_default_no_fees(self):
        tc = j.trade_cost(row(code="SMH", entry_price=500.0, qty=2), 510.0, "2026-09-25")
        self.assertEqual(tc["cost"], 0)
        self.assertEqual(tc["net"], 20.0)


class RMultipleTest(JournalTest):
    def test_trade_r_uses_stop_init(self):
        self.assertEqual(j._r_mult(row(stop_init=153.0), 154.0), -0.5)
        self.assertEqual(j._r_mult(row(stop_init=153.0), 159.0), 2.0)

    def test_trade_r_none_without_valid_stop(self):
        self.assertIsNone(j._r_mult(row(stop_init=None), 150.0))
        self.assertIsNone(j._r_mult(row(stop_init=160.0), 150.0))   # 停損在進場價之上

    def test_net_r(self):
        # 新唐：R＝137.5−131.5＝6 元 × 1000 股＝6,000；淨損 −6,125 → −1.02R
        self.assertEqual(j._net_r(row(entry_price=137.5, stop_init=131.5), -6125), -1.02)

    def test_account_one_r(self):
        self.assertEqual(j._acct_1r(), 15000)

    def test_account_r_at_price_includes_cost(self):
        r = row(entry_price=155.0, stop_init=153.0)
        # 149 出場：−6,000 −（手續費 132＋127、證交稅 447）＝ −6,706 → −0.45R（帳戶）
        self.assertEqual(j._acct_r_at(r, 149.0, 15000), -0.45)

    def test_account_r_converts_usd(self):
        r = row(code="SMH", entry_price=500.0, qty=10)
        self.assertEqual(j._acct_r_at(r, 550.0, 15000), 1.0)   # +500 USD × 30 ＝ 15,000 元

    def test_account_r_longterm_base(self):
        r = row(code="0050", entry_price=43.97, qty=1)
        # 長期持股從 base 111.35 起算，不是成本 43.97
        self.assertAlmostEqual(j._acct_r_at(r, 112.4, 15000, base=111.35), round((1050 - 95 - 95 - 112) / 15000, 2))


class FalsifyLevelTest(JournalTest):
    def test_fixed_and_ma_take_higher(self):
        hist = pd.Series([150, 152, 154, 156, 158.0])
        self.assertEqual(j._falsify_level(row(falsify_price=149.0, falsify_ma=5), hist), 154.0)
        self.assertEqual(j._falsify_level(row(falsify_price=160.0, falsify_ma=5), hist), 160.0)

    def test_ma_only_and_none(self):
        hist = pd.Series([10.0, 20.0, 30.0])
        self.assertEqual(j._falsify_level(row(falsify_ma=3), hist), 20.0)
        self.assertIsNone(j._falsify_level(row(falsify_ma=5), hist))   # 資料不足 5 根
        self.assertIsNone(j._falsify_level(row(), hist))


class CloseTest(JournalTest):
    def close(self, reason, **kw):
        self.write(row(stop_init=153.0, **kw))
        self.quiet(j.cmd_close, Namespace(id=1, price=150.0, reason=reason, note=None, date="2026-09-25"))
        return j.load().iloc[0]

    def test_falsify_exit_sets_hit_date(self):
        r = self.close("證偽")
        self.assertEqual(r["falsify_hit_date"], "2026-09-25")
        self.assertEqual(r["status"], "closed")

    def test_trail_exit_sets_hit_date(self):
        self.assertEqual(self.close("停利")["falsify_hit_date"], "2026-09-25")

    def test_existing_hit_date_kept(self):
        self.assertEqual(self.close("證偽", falsify_hit_date="2026-09-24")["falsify_hit_date"], "2026-09-24")

    def test_other_reason_no_hit_date(self):
        self.assertTrue(pd.isna(self.close("其他")["falsify_hit_date"]))

    def test_close_stores_costs(self):
        r = self.close("證偽")
        self.assertEqual(r["exit_price"], 150.0)
        self.assertEqual(r["pnl_pct"], round((150 / 155 - 1) * 100, 2))
        self.assertEqual(r["net_pnl_amt"], j.trade_cost(row(), 150.0, "2026-09-25")["net"])

    def test_close_twice_refused(self):
        self.write(row(status="closed"))
        with self.assertRaises(SystemExit):
            j.cmd_close(Namespace(id=1, price=150.0, reason="證偽", note=None, date=None))


class PostExitTest(JournalTest):
    def setUp(self):
        super().setUp()
        self.exit_day = pd.Timestamp(date.today() - timedelta(days=20))
        days = pd.bdate_range(self.exit_day - timedelta(days=5), periods=25)
        closes = pd.Series([100.0 + i for i in range(len(days))], index=days)
        self.after = closes[closes.index > self.exit_day]
        j.prices = lambda codes: {c: {"close": float(closes.iloc[-1]), "date": str(days[-1].date()), "hist": closes} for c in codes}

    def test_fills_day5_day10_and_max(self):
        df = pd.DataFrame([row(status="closed", exit_date=str(self.exit_day.date()), exit_price=100.0, exit_reason="情緒").to_dict()])
        for c in j.NUM_COLS: df[c] = pd.to_numeric(df[c], errors="coerce")
        lines = j._post_exit(df)
        r = df.iloc[0]
        self.assertEqual(r["post5_pct"], round((self.after.iloc[4] / 100 - 1) * 100, 2))
        self.assertEqual(r["post10_pct"], round((self.after.iloc[9] / 100 - 1) * 100, 2))
        self.assertEqual(r["post_max_pct"], round((self.after.iloc[:10].max() / 100 - 1) * 100, 2))
        self.assertEqual(len(lines), 2)   # 第 5 日、第 10 日各一行

    def test_skips_finished_and_open(self):
        done = row(id=1, status="closed", exit_date=str(self.exit_day.date()), exit_price=100.0, post10_pct=5.0)
        op = row(id=2, status="open")
        df = pd.DataFrame([done.to_dict(), op.to_dict()])
        for c in j.NUM_COLS: df[c] = pd.to_numeric(df[c], errors="coerce")
        self.assertEqual(j._post_exit(df), [])


class ReviewTest(JournalTest):
    def test_review_stats_and_open_positions(self):
        self.write(
            row(id=1, code="7828", entry_price=2097.0, qty=0.01, stop_init=1990.0, status="closed", exit_date="2026-09-22",
                exit_price=1955.0, exit_reason="證偽", pnl_pct=-6.77, held_days=2, falsify_hit_date="2026-09-22", max_up_pct=1.3),
            row(id=2, code="8150", entry_price=102.0, stop_init=94.0, status="closed", exit_date="2026-09-23",
                exit_price=109.5, exit_reason="情緒", pnl_pct=7.35, held_days=1, max_up_pct=13.7),
            row(id=3, code="2303", entry_price=155.0, stop_init=153.0, status="open", last_price=154.0,
                falsify_price=149.0, falsify_level=149.0),
            row(id=4, code="0050", entry_price=108.21, qty=3, stop_init=106.15, status="open", last_price=112.4,
                falsify_price=108.21, trail_stop=110.9, falsify_level=110.9, thesis_type="事件"),
            row(id=5, code="2330", entry_price=900.0, status="open", last_price=1000.0, thesis_type="長期持有"),
        )
        out = self.quiet(j.cmd_review, Namespace(months=None, longterm=False))
        self.assertIn("證偽條件觸發 1 筆，1 筆在 2 日內出場（紀律率 100%）", out)
        self.assertIn("帳戶 R（1R＝15,000 元，含成本）", out)
        self.assertIn("## 未平倉交易單", out)
        self.assertNotIn("## 長期持有", out)
        self.assertNotIn("| 2330", out.split("## 未平倉交易單")[1].split("## 逐筆")[0])   # 長期不列入交易單
        # 0050 融資：停利 110.9 高於證偽 108.21 → 先觸發停利
        open_tbl = out.split("## 未平倉交易單")[1]
        line = next(l for l in open_tbl.splitlines() if "| 0050" in l or "|    4 |" in l)
        self.assertIn("停利", line)
        self.assertTrue((j.LOCAL / f"review_{date.today():%Y%m}.md").exists())

    def test_review_longterm_flag(self):
        self.write(row(id=5, code="2330", entry_price=900.0, status="open", last_price=1000.0, thesis_type="長期持有", base_price=950.0),
                   row(id=6, code="2303", entry_price=100.0, status="closed", exit_date="2026-09-22", exit_price=101.0,
                       exit_reason="其他", pnl_pct=1.0, held_days=1, max_up_pct=1.0))
        out = self.quiet(j.cmd_review, Namespace(months=None, longterm=True))
        lt = out.split("## 長期持有")[1]
        self.assertIn("950", lt)   # 從 base_price 起算


if __name__ == "__main__":
    unittest.main()
