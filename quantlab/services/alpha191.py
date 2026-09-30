"""国泰君安 Alpha191：能写进当前公式语言的落盘，其余只分类。

RANK 用当天截面百分位 ``cs_rank(0)``。VWAP 用 ``amount / vol``。
收益用后复权收盘的 ``pct_change(1)``。滚动窗口含当日，标准差和协方差用样本口径。
不补 Alpha36 缺失的相关窗口，也不把算不出的公式登记成因子。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alpha191Item:
    number: int
    kind: str
    formula: str = ""
    note: str = ""

    @property
    def field(self) -> str:
        return f"alpha191_{self.number:03d}"

    @property
    def name(self) -> str:
        return f"Alpha{self.number:03d}"

    @property
    def computable(self) -> bool:
        return bool(self.formula)


_PRICE = {
    2: "-(((hfq_close - hfq_low) - (hfq_high - hfq_close)) / (hfq_high - hfq_low)).diff(1)",
    7: "(((amount / vol - hfq_close).rolling_max(3).cs_rank(0) + (amount / vol - hfq_close).rolling_min(3).cs_rank(0)) * vol.diff(3).cs_rank(0))",
    8: "((((hfq_high + hfq_low) / 2) * 0.2 + (amount / vol) * 0.8).diff(4) * -1).cs_rank(0)",
    11: "((((hfq_close - hfq_low) - (hfq_high - hfq_close)) / (hfq_high - hfq_low)) * vol).rolling_sum(6)",
    14: "hfq_close - hfq_close.shift(5)",
    15: "hfq_open / hfq_close.shift(1) - 1",
    18: "hfq_close / hfq_close.shift(5)",
    20: "(hfq_close - hfq_close.shift(6)) / hfq_close.shift(6) * 100",
    29: "(hfq_close - hfq_close.shift(6)) / hfq_close.shift(6) * vol",
    31: "(hfq_close - hfq_close.rolling_mean(12)) / hfq_close.rolling_mean(12) * 100",
    33: "((-1 * hfq_low.rolling_min(5) + hfq_low.rolling_min(5).shift(5)) * ((hfq_close.pct_change(1).rolling_sum(240) - hfq_close.pct_change(1).rolling_sum(20)) / 220).cs_rank(0)) * vol.ts_rank(5)",
    34: "hfq_close.rolling_mean(12) / hfq_close",
    37: "-1 * ((hfq_open.rolling_sum(5) * hfq_close.pct_change(1).rolling_sum(5) - (hfq_open.rolling_sum(5) * hfq_close.pct_change(1).rolling_sum(5)).shift(10)).cs_rank(0))",
    41: "-1 * (amount / vol).diff(3).rolling_max(5).cs_rank(0)",
    46: "(hfq_close.rolling_mean(3) + hfq_close.rolling_mean(6) + hfq_close.rolling_mean(12) + hfq_close.rolling_mean(24)) / (4 * hfq_close)",
    60: "((((hfq_close - hfq_low) - (hfq_high - hfq_close)) / (hfq_high - hfq_low)) * vol).rolling_sum(20)",
    65: "hfq_close.rolling_mean(6) / hfq_close",
    66: "(hfq_close - hfq_close.rolling_mean(6)) / hfq_close.rolling_mean(6) * 100",
    70: "amount.rolling_std(6)",
    71: "(hfq_close - hfq_close.rolling_mean(24)) / hfq_close.rolling_mean(24) * 100",
    80: "(vol - vol.shift(5)) / vol.shift(5) * 100",
    85: "(vol / vol.rolling_mean(20)).ts_rank(20) * (-1 * hfq_close.diff(7)).ts_rank(8)",
    88: "(hfq_close - hfq_close.shift(20)) / hfq_close.shift(20) * 100",
    95: "amount.rolling_std(20)",
    97: "vol.rolling_std(10)",
    100: "vol.rolling_std(20)",
    106: "hfq_close - hfq_close.shift(20)",
    107: "-1 * (hfq_open - hfq_high.shift(1)).cs_rank(0) * (hfq_open - hfq_close.shift(1)).cs_rank(0) * (hfq_open - hfq_low.shift(1)).cs_rank(0)",
    114: "(((hfq_high - hfq_low) / (hfq_close.rolling_sum(5) / 5)).shift(2).cs_rank(0) * vol.cs_rank(0).cs_rank(0)) / (((hfq_high - hfq_low) / (hfq_close.rolling_sum(5) / 5)) / (amount / vol - hfq_close))",
    117: "vol.ts_rank(32) * (1 - (hfq_close + hfq_high - hfq_low).ts_rank(16)) * (1 - hfq_close.pct_change(1).ts_rank(32))",
    118: "(hfq_high - hfq_open).rolling_sum(20) / (hfq_open - hfq_low).rolling_sum(20) * 100",
    120: "(amount / vol - hfq_close).cs_rank(0) / (amount / vol + hfq_close).cs_rank(0)",
    126: "(hfq_close + hfq_high + hfq_low) / 3",
    132: "amount.rolling_mean(20)",
    134: "(hfq_close - hfq_close.shift(12)) / hfq_close.shift(12) * vol",
    142: "-1 * hfq_close.ts_rank(10).cs_rank(0) * hfq_close.diff(1).diff(1).cs_rank(0) * (vol / vol.rolling_mean(20)).ts_rank(5).cs_rank(0)",
    145: "(vol.rolling_mean(9) - vol.rolling_mean(26)) / vol.rolling_mean(12) * 100",
    150: "(hfq_close + hfq_high + hfq_low) / 3 * vol",
    153: "(hfq_close.rolling_mean(3) + hfq_close.rolling_mean(6) + hfq_close.rolling_mean(12) + hfq_close.rolling_mean(24)) / 4",
    163: "(-1 * hfq_close.pct_change(1) * vol.rolling_mean(20) * (amount / vol) * (hfq_high - hfq_close)).cs_rank(0)",
    168: "-1 * vol / vol.rolling_mean(20)",
    170: "((1 / hfq_close).cs_rank(0) * vol / vol.rolling_mean(20)) * ((hfq_high * (hfq_high - hfq_close).cs_rank(0)) / (hfq_high.rolling_sum(5) / 5)) - (amount / vol - (amount / vol).shift(5)).cs_rank(0)",
    178: "(hfq_close - hfq_close.shift(1)) / hfq_close.shift(1) * vol",
}

_CORR = {
    5: "-1 * vol.ts_rank(5).rolling_corr(hfq_high.ts_rank(5), 5).rolling_max(3)",
    16: "-1 * vol.cs_rank(0).rolling_corr((amount / vol).cs_rank(0), 5).cs_rank(0).rolling_max(5)",
    26: "(hfq_close.rolling_sum(7) / 7 - hfq_close) + (amount / vol).rolling_corr(hfq_close.shift(5), 230)",
    32: "-1 * hfq_high.cs_rank(0).rolling_corr(vol.cs_rank(0), 3).cs_rank(0).rolling_sum(3)",
    42: "-1 * hfq_high.rolling_std(10).cs_rank(0) * hfq_high.rolling_corr(vol, 10)",
    45: "(hfq_close * 0.6 + hfq_open * 0.4).diff(1).cs_rank(0) * (amount / vol).rolling_corr(vol.rolling_mean(150), 15).cs_rank(0)",
    62: "-1 * hfq_high.rolling_corr(vol.cs_rank(0), 5)",
    74: "(hfq_low * 0.35 + (amount / vol) * 0.65).rolling_sum(20).rolling_corr(vol.rolling_mean(40).rolling_sum(20), 7).cs_rank(0) + (amount / vol).cs_rank(0).rolling_corr(vol.cs_rank(0), 6).cs_rank(0)",
    83: "-1 * hfq_high.cs_rank(0).rolling_cov(vol.cs_rank(0), 5).cs_rank(0)",
    90: "-1 * (amount / vol).cs_rank(0).rolling_corr(vol.cs_rank(0), 5).cs_rank(0)",
    91: "-1 * (hfq_close - hfq_close.rolling_max(5)).cs_rank(0) * vol.rolling_mean(40).rolling_corr(hfq_low, 5).cs_rank(0)",
    99: "-1 * hfq_close.cs_rank(0).rolling_cov(vol.cs_rank(0), 5).cs_rank(0)",
    101: "((hfq_close.rolling_corr(vol.rolling_mean(30).rolling_sum(37), 15).cs_rank(0) < (hfq_high * 0.1 + (amount / vol) * 0.9).cs_rank(0).rolling_corr(vol.cs_rank(0), 11).cs_rank(0)) * -1)",
    104: "-1 * hfq_high.rolling_corr(vol, 5).diff(5) * hfq_close.rolling_std(20).cs_rank(0)",
    105: "-1 * hfq_open.cs_rank(0).rolling_corr(vol.cs_rank(0), 10)",
    113: "-1 * (hfq_close.shift(5).rolling_sum(20) / 20).cs_rank(0) * hfq_close.rolling_corr(vol, 2) * hfq_close.rolling_sum(5).rolling_corr(hfq_close.rolling_sum(20), 2).cs_rank(0)",
    123: "((((hfq_high + hfq_low) / 2).rolling_sum(20).rolling_corr(vol.rolling_mean(60).rolling_sum(20), 9).cs_rank(0) < hfq_low.rolling_corr(vol, 6).cs_rank(0)) * -1)",
    136: "-1 * hfq_close.pct_change(1).diff(3).cs_rank(0) * hfq_open.rolling_corr(vol, 10)",
    139: "-1 * hfq_open.rolling_corr(vol, 10)",
    141: "-1 * hfq_high.cs_rank(0).rolling_corr(vol.rolling_mean(15).cs_rank(0), 9).cs_rank(0)",
    148: "((hfq_open.rolling_corr(vol.rolling_mean(60).rolling_sum(9), 6).cs_rank(0) < (hfq_open - hfq_open.rolling_min(14)).cs_rank(0)) * -1)",
    154: "(((amount / vol - (amount / vol).rolling_min(16)) < (amount / vol).rolling_corr(vol.rolling_mean(180), 18)) * 1)",
    176: "((hfq_close - hfq_low.rolling_min(12)) / (hfq_high.rolling_max(12) - hfq_low.rolling_min(12))).cs_rank(0).rolling_corr(vol.cs_rank(0), 6)",
    179: "(amount / vol).rolling_corr(vol, 4).cs_rank(0) * hfq_low.cs_rank(0).rolling_corr(vol.rolling_mean(50).cs_rank(0), 12).cs_rank(0)",
    184: "(hfq_open - hfq_close).shift(1).rolling_corr(hfq_close, 200).cs_rank(0) + (hfq_open - hfq_close).cs_rank(0)",
    191: "vol.rolling_mean(20).rolling_corr(hfq_low, 5) + (hfq_high + hfq_low) / 2 - hfq_close",
}

_BLOCKED: dict[str, tuple[int, ...]] = {
    "缺对数": (1,),
    "缺符号": (6, 48),
    "缺绝对值": (12, 54, 76, 78, 189),
    "缺幂": (13, 17, 56, 108, 115, 121, 127, 131, 166, 171, 185),
    "缺两序列极值": (52, 110, 159, 161, 175),
    "缺条件": (
        3, 4, 10, 19, 38, 40, 43, 49, 50, 51, 55, 59, 69, 84, 86, 93, 94, 98,
        112, 128, 129, 137, 143, 167, 172, 180, 186, 187,
    ),
    "缺平滑": (
        9, 22, 23, 24, 27, 28, 47, 57, 63, 67, 68, 72, 79, 81, 82, 89, 96, 102,
        109, 111, 122, 135, 146, 151, 152, 155, 158, 160, 162, 164, 169, 173, 174, 188,
    ),
    "缺衰减": (25, 35, 39, 44, 61, 64, 73, 77, 87, 92, 119, 124, 125, 130, 138, 140, 156),
    "缺计数或回归": (21, 30, 53, 58, 75, 103, 116, 133, 144, 147, 149, 157, 165, 177, 181, 182, 183, 190),
    "公式缺相关窗口": (36,),
}

KIND_NOTE = {
    "价量": "四价、成交量、成交额，以及滚动、差分、截面排名可以写出。",
    "相关": "滚动相关或协方差。83 和 99 是协方差。",
    "缺对数": "要 LOG。Alpha1 里面还有相关。",
    "缺符号": "要 SIGN。",
    "缺绝对值": "要 ABS。Alpha54 里面还有相关。",
    "缺幂": "要乘方。56、108、115、121、131 里面还有相关。",
    "缺两序列极值": "两个序列逐日取最大或最小，不是滚动窗口极值。",
    "缺条件": "三元条件或按涨跌分段累加。49、55、112、129、172、186 还要绝对值。",
    "缺平滑": "SMA(n, m) 或 WMA，不是滚动简单平均。",
    "缺衰减": "要 DECAYLINEAR。35、39、44、61、64、73、77、92、119、125、130、138、140 里面还有相关。",
    "缺计数或回归": "COUNT、SUMIF、HIGHDAY、LOWDAY、REGBETA、SUMAC、PROD、FILTER 或指数基准。",
    "公式缺相关窗口": "粘贴的 Alpha36 里 CORR 没有窗口，不自行补。",
}


def _items() -> tuple[Alpha191Item, ...]:
    rows: list[Alpha191Item] = []
    for number, formula in _PRICE.items():
        rows.append(Alpha191Item(number, "价量", formula, KIND_NOTE["价量"]))
    for number, formula in _CORR.items():
        rows.append(Alpha191Item(number, "相关", formula, KIND_NOTE["相关"]))
    for kind, numbers in _BLOCKED.items():
        for number in numbers:
            rows.append(Alpha191Item(number, kind, "", KIND_NOTE[kind]))
    rows.sort(key=lambda item: item.number)
    return tuple(rows)


ALPHA191: tuple[Alpha191Item, ...] = _items()
_BY_NUMBER = {item.number: item for item in ALPHA191}


def alpha191_item(number: int) -> Alpha191Item:
    try:
        return _BY_NUMBER[number]
    except KeyError as error:
        raise KeyError(number) from error


def computable_alpha191() -> tuple[Alpha191Item, ...]:
    return tuple(item for item in ALPHA191 if item.computable)
