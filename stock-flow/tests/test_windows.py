"""flowlib.group_windows（族群多週期流向）的測試。不連網：STOCK_CACHE 指到暫存資料夾、自己寫假快取。

  python -m unittest discover -s ~/.claude/skills/stock-flow/tests -v
"""
import os, sys, tempfile, unittest
from pathlib import Path

import pandas as pd

TMP = tempfile.mkdtemp()
os.environ["STOCK_CACHE"] = TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import flowlib as fl  # noqa: E402

GROUPS = {"甲": ["1111"], "乙": ["2222"]}


def write_day(d8: str, t1: float, t2: float, inst1: float | None, inst2: float | None, close: float = 100.0):
    """一天的收盤快取：甲=1111、乙=2222、其他=9999（成交值固定 100）；inst 單位：張。"""
    pd.DataFrame({"code": ["1111", "2222", "9999"], "close": [close] * 3,
                  "turnover_M": [t1, t2, 100.0]}).to_csv(fl.CACHE / f"eod_{d8}.csv", index=False)
    if inst1 is not None:
        pd.DataFrame({"code": ["1111", "2222"], "foreign_lots": [inst1, inst2], "trust_lots": [0, 0],
                      "dealer_lots": [0, 0]}).to_csv(fl.inst_path(d8), index=False)


class GroupWindowsTest(unittest.TestCase):
    def setUp(self):
        for f in fl.CACHE.glob("*.csv"):
            f.unlink()

    def dates(self, n):
        return [f"{d:%Y%m%d}" for d in pd.bdate_range("2026-08-03", periods=n)]

    def test_inflow_and_outflow(self):
        ds = self.dates(25)
        for k, d in enumerate(ds):
            late = k >= 20                      # 最後 5 天：甲成交放大且法人買，乙萎縮且法人賣
            write_day(d, 50 if late else 20, 10 if late else 40, 100 if late else 0, -100 if late else 0)
        w = fl.group_windows(GROUPS, windows=(3, 5), base=20).set_index("group")
        self.assertEqual(w.attrs["upto"], ds[-1])
        self.assertEqual(w.loc["甲", "flow_3d"], "流入")
        self.assertEqual(w.loc["乙", "flow_5d"], "流出")
        # 甲：近 5 日佔比 50/160=31.25%，前段 20/160=12.5% → +18.75pp；法人 5 日 × 100 張 × 100 元 = 50M
        self.assertAlmostEqual(w.loc["甲", "share_5d"], 18.75, places=2)
        self.assertEqual(w.loc["甲", "inst_5d"], 50)

    def test_insufficient_history_is_nan(self):
        for d in self.dates(4):
            write_day(d, 20, 40, 0, 0)
        w = fl.group_windows(GROUPS, windows=(3, 10), base=20).set_index("group")
        self.assertTrue(pd.isna(w.loc["甲", "share_10d"]))     # 不到 10 日
        self.assertTrue(pd.isna(w.loc["甲", "share_3d"]))      # 前段只有 1 日 < min_base
        self.assertEqual(w.loc["甲", "inst_3d"], 0)
        self.assertEqual(fl.windows_cell(w.loc["甲"], 10), "資料不足")

    def test_missing_inst_day_makes_window_nan(self):
        ds = self.dates(10)
        for k, d in enumerate(ds):
            write_day(d, 20, 40, None if k == 8 else 10, 10)   # 倒數第 2 天沒法人快取
        w = fl.group_windows(GROUPS, windows=(3,), base=5).set_index("group")
        self.assertTrue(pd.isna(w.loc["甲", "inst_3d"]))
        self.assertEqual(w.loc["甲", "flow_3d"], "-")

    def test_falls_back_to_group_cache(self):
        ds = self.dates(8)
        for d in ds:
            write_day(d, 20, 40, None, None)
            pd.DataFrame({"group": ["甲", "乙"], "inst_M": [7, -3]}).to_csv(fl.CACHE / f"eod_groups_{d}.csv", index=False)
        w = fl.group_windows(GROUPS, windows=(3,), base=5).set_index("group")
        self.assertEqual(w.loc["甲", "inst_3d"], 21)
        self.assertEqual(w.loc["乙", "inst_3d"], -9)


if __name__ == "__main__":
    unittest.main()
