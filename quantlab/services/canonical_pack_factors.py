"""Materialize canonical pack formulas into a sidecar parquet for backtests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantlab.config import posix_relative
from quantlab.services.canonical_factor_pack import CANONICAL_FACTOR_PACK, alpha191_pack_specs
from quantlab.services.factor_manual import _eval, finite_factor_values, parse_expression
from quantlab.services.moneyflow_factors import MONEYFLOW_FIELDS, MONEYFLOW_SIDECAR_NAME


PACK_SIDECAR_NAME = "canonical_pack_factors.parquet"
QLIB_PICK_SIDECAR_NAME = "qlib_pick_factors.parquet"
COMPOSITE_SIDECAR_NAME = "composite_pack_factors.parquet"
PACK_FIELDS = tuple(item.field for item in CANONICAL_FACTOR_PACK)
_PACK_FORMULAS = {item.field: item.formula for item in CANONICAL_FACTOR_PACK}
_ATTACHABLE_FIELDS = set(_PACK_FORMULAS) | {item.field for item in alpha191_pack_specs()}
COMPOSITE_FORMULAS = {
    "sleeve_mv_div": "total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)",
    "sleeve_price_mv_div": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_mom_mv": "momentum_10.cs_rank(0) + total_market_cap.cs_rank(0)",
    "sleeve_mom_price": "momentum_10.cs_rank(0) + hfq_close.ts_rank(20)",
    "sleeve_mom_mv_div": (
        "momentum_10.cs_rank(0) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_mom_price_mv_div": (
        "momentum_10.cs_rank(0) + hfq_close.ts_rank(20) + "
        "total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price_mv_div_vol": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + vol_mean_20.cs_rank(0)"
    ),
    "sleeve_price_mv_div_range": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + intraday_range.cs_rank(0)"
    ),
    "sleeve_price60_mv_div": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price_float_div": (
        "hfq_close.ts_rank(20) + float_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price_mv_div_cheap": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - pe_ttm.cs_rank(0))"
    ),
    "sleeve_price_mv_div_quiet": (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - turn.cs_rank(0))"
    ),
    "sleeve_price_small_div": (
        "hfq_close.ts_rank(20) + (1 - total_market_cap.cs_rank(0)) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price60_mv": "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0)",
    "sleeve_price60_div": "hfq_close.ts_rank(60) + dividend_yield_ratio.cs_rank(0)",
    "sleeve_price60_mv_div_cheap": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - pe_ttm.cs_rank(0))"
    ),
    "sleeve_price60_mv_div_quiet": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - turn.cs_rank(0))"
    ),
    "sleeve_price60_mv_div_cheap_quiet": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + (1 - pe_ttm.cs_rank(0)) + (1 - turn.cs_rank(0))"
    ),
    "sleeve_price60_mv_div0": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + 0 * dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price60_mv_div25": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + 0.25 * dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_price60_mv_div50": (
        "hfq_close.ts_rank(60) + total_market_cap.cs_rank(0) + 0.5 * dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_zscore60_mv_div": (
        "close_zscore_60.cs_rank(0) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
    "sleeve_bias60_mv_div": (
        "close_bias_60.cs_rank(0) + total_market_cap.cs_rank(0) + dividend_yield_ratio.cs_rank(0)"
    ),
}


def _coef_text(coef: float) -> str:
    if float(coef).is_integer():
        return str(int(coef))
    return f"{coef:g}"


def _weighted_term(coef: float, expr: str) -> str:
    if coef == 0:
        return f"0 * {expr}"
    if coef == 1:
        return expr
    return f"{_coef_text(coef)} * {expr}"


def price60_cheap_formula(price: float, size: float, div: float, cheap: float) -> str:
    return " + ".join(
        (
            _weighted_term(price, "hfq_close.ts_rank(60)"),
            _weighted_term(size, "total_market_cap.cs_rank(0)"),
            _weighted_term(div, "dividend_yield_ratio.cs_rank(0)"),
            _weighted_term(cheap, "(1 - pe_ttm.cs_rank(0))"),
        )
    )


# field, display name, price60, size, dividend, cheap
PRICE60_CHEAP_WEIGHTS: tuple[tuple[str, str, float, float, float, float], ...] = (
    ("sleeve_p60_c025", "价格60加低估值 · 低估值0.25", 1, 1, 1, 0.25),
    ("sleeve_p60_c050", "价格60加低估值 · 低估值0.5", 1, 1, 1, 0.5),
    ("sleeve_p60_c075", "价格60加低估值 · 低估值0.75", 1, 1, 1, 0.75),
    ("sleeve_p60_c125", "价格60加低估值 · 低估值1.25", 1, 1, 1, 1.25),
    ("sleeve_p60_c150", "价格60加低估值 · 低估值1.5", 1, 1, 1, 1.5),
    ("sleeve_p60_c175", "价格60加低估值 · 低估值1.75", 1, 1, 1, 1.75),
    ("sleeve_p60_c200", "价格60加低估值 · 低估值2", 1, 1, 1, 2),
    ("sleeve_p60_c300", "价格60加低估值 · 低估值3", 1, 1, 1, 3),
    ("sleeve_p60_d000", "价格60加低估值 · 股息0", 1, 1, 0, 1),
    ("sleeve_p60_d025", "价格60加低估值 · 股息0.25", 1, 1, 0.25, 1),
    ("sleeve_p60_d050", "价格60加低估值 · 股息0.5", 1, 1, 0.5, 1),
    ("sleeve_p60_d075", "价格60加低估值 · 股息0.75", 1, 1, 0.75, 1),
    ("sleeve_p60_d150", "价格60加低估值 · 股息1.5", 1, 1, 1.5, 1),
    ("sleeve_p60_d200", "价格60加低估值 · 股息2", 1, 1, 2, 1),
    ("sleeve_p60_p050", "价格60加低估值 · 价格0.5", 0.5, 1, 1, 1),
    ("sleeve_p60_p150", "价格60加低估值 · 价格1.5", 1.5, 1, 1, 1),
    ("sleeve_p60_p200", "价格60加低估值 · 价格2", 2, 1, 1, 1),
    ("sleeve_p60_s050", "价格60加低估值 · 规模0.5", 1, 0.5, 1, 1),
    ("sleeve_p60_s150", "价格60加低估值 · 规模1.5", 1, 1.5, 1, 1),
    ("sleeve_p60_s200", "价格60加低估值 · 规模2", 1, 2, 1, 1),
    ("sleeve_p60_d050_c200", "价格60加低估值 · 股息0.5低估值2", 1, 1, 0.5, 2),
    ("sleeve_p60_d200_c050", "价格60加低估值 · 股息2低估值0.5", 1, 1, 2, 0.5),
    ("sleeve_p60_s050_c200", "价格60加低估值 · 规模0.5低估值2", 1, 0.5, 1, 2),
    ("sleeve_p60_p050_c200", "价格60加低估值 · 价格0.5低估值2", 0.5, 1, 1, 2),
    ("sleeve_p60_p200_c050", "价格60加低估值 · 价格2低估值0.5", 2, 1, 1, 0.5),
    ("sleeve_p60_d000_c200", "价格60加低估值 · 股息0低估值2", 1, 1, 0, 2),
)

# 在低估值0.75 上叠加 10 日动量，用来测 2019/2021 风格轮动能不能用动量腿对冲。
C075_MOM_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c075_mom025", "低估值0.75加动量0.25", 0.25),
    ("sleeve_c075_mom050", "低估值0.75加动量0.5", 0.5),
    ("sleeve_c075_mom100", "低估值0.75加动量1", 1.0),
)

# 规模权重插在 1 与 0.5 之间，其余与低估值0.75 相同。
C075_SIZE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c075_s075", "低估值0.75 · 规模0.75", 0.75),
)

# 价格权重插在 1 与已测的 1.5 之间，低估值仍是 0.75。p150 那条廉价腿是 1，和 c075 不是同一条。
C075_PRICE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c075_p125", "低估值0.75 · 价格1.25", 1.25),
)

# 在已过线的股息0.75（廉价腿已是 1）上再加重低估值。
D075_CHEAP_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_d075_c125", "股息0.75 · 低估值1.25", 1.25),
    ("sleeve_d075_c150", "股息0.75 · 低估值1.5", 1.5),
)

# 钉在低估值1.5、股息1 上，扫规模、股息微调和低换手，不再动廉价腿。
C150_SIZE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_s075", "低估值1.5 · 规模0.75", 0.75),
    ("sleeve_c150_s125", "低估值1.5 · 规模1.25", 1.25),
)
C150_DIV_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_d125", "低估值1.5 · 股息1.25", 1.25),
)
C150_QUIET_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_q025", "低估值1.5 · 低换手0.25", 0.25),
    ("sleeve_c150_q050", "低估值1.5 · 低换手0.5", 0.5),
)

# 钉在低估值1.5 上，动价格腿和未测过的软权重；不再扫规模/股息/换手。
C150_PRICE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_p075", "低估值1.5 · 价格0.75", 0.75),
    ("sleeve_c150_p125", "低估值1.5 · 价格1.25", 1.25),
)
C150_RANGE_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_r025", "低估值1.5 · 低振幅0.25", 0.25),
    ("sleeve_c150_r050", "低估值1.5 · 低振幅0.5", 0.5),
)
C150_AMOUNT_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_a025", "低估值1.5 · 成交额0.25", 0.25),
    ("sleeve_c150_a050", "低估值1.5 · 成交额0.5", 0.5),
)
C150_OVERNIGHT_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_o025", "低估值1.5 · 隔夜0.25", 0.25),
    ("sleeve_c150_o050", "低估值1.5 · 隔夜0.5", 0.5),
)
C150_LOCATION_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_loc025", "低估值1.5 · 收盘位置0.25", 0.25),
    ("sleeve_c150_loc050", "低估值1.5 · 收盘位置0.5", 0.5),
)
C150_SHAPE_BLENDS: tuple[tuple[str, str, str], ...] = (
    ("sleeve_c150_z", "低估值1.5 · 价格改zscore60", "zscore"),
    ("sleeve_c150_b", "低估值1.5 · 价格改bias60", "bias"),
)

# 排序形态和股票池：不改低估值1.5 四条权重。
C150_P20 = ("sleeve_c150_p20", "低估值1.5 · 价格20日")
C150_FLOAT = ("sleeve_c150_float", "低估值1.5 · 流通市值排序")
C150_AZ_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_az025", "低估值1.5 · 成交额zscore0.25", 0.25),
    ("sleeve_c150_az050", "低估值1.5 · 成交额zscore0.5", 0.5),
)
C150_BOARD_BLENDS: tuple[tuple[str, str, str], ...] = (
    ("sleeve_c150_n688", "低估值1.5 · 排除科创板", "star"),
    ("sleeve_c150_ncyb", "低估值1.5 · 排除创业板", "cyb"),
    ("sleeve_c150_main", "低估值1.5 · 仅主板", "main"),
)
C150_P000 = ("sleeve_c150_p000", "低估值1.5 · 去掉价格腿")
C150_S000 = ("sleeve_c150_s000", "低估值1.5 · 去掉规模排序")
C150_D075 = ("sleeve_c150_d075", "低估值1.5 · 股息0.75")
C150_P120 = ("sleeve_c150_p120", "低估值1.5 · 价格120日")
C150_P252 = ("sleeve_c150_p252", "低估值1.5 · 价格252日")
C150_HV_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_hv025", "低估值1.5 · 高成交量0.25", 0.25),
    ("sleeve_c150_hv050", "低估值1.5 · 高成交量0.5", 0.5),
)
C150_AZPOS = ("sleeve_c150_azpos", "低估值1.5 · 成交额高于自身20日")
C150_MA_BLENDS: tuple[tuple[str, str, str], ...] = (
    ("sleeve_c150_m60", "低估值1.5 · 个股60日均线", "bias60"),
    ("sleeve_c150_m20", "低估值1.5 · 个股20日均线", "bias20"),
)
C150_REV_MOM: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_rm025", "低估值1.5 · 反转动量0.25", 0.25),
    ("sleeve_c150_rm050", "低估值1.5 · 反转动量0.5", 0.5),
)
C150_FADE_GAP: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_fg025", "低估值1.5 · 淡化隔夜0.25", 0.25),
    ("sleeve_c150_fg050", "低估值1.5 · 淡化隔夜0.5", 0.5),
)
C150_TREND_BLENDS: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_tr025", "低估值1.5 · 均线偏离0.25", 0.25),
    ("sleeve_c150_tr050", "低估值1.5 · 均线偏离0.5", 0.5),
)
C150_INST = ("sleeve_c150_inst", "低估值1.5 · 高量低换手")
C150_RM60 = ("sleeve_c150_rm60", "低估值1.5 · 反转60日动量0.25")
C150_LOWLOC = ("sleeve_c150_ll025", "低估值1.5 · 收盘偏低下0.25")
C150_UNIVERSE: tuple[tuple[str, str, str], ...] = (
    ("sleeve_c150_hs300", "低估值1.5 · 仅沪深300", "hs300"),
    ("sleeve_c150_csi800", "低估值1.5 · 沪深300或中证500", "csi800"),
)
C150_CSI500 = ("sleeve_c150_csi500", "低估值1.5 · 仅中证500")
C150_CSI800_W: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_csi800_w025", "低估值1.5 · 300或500 · 指数权重0.25", 0.25),
    ("sleeve_c150_csi800_w050", "低估值1.5 · 300或500 · 指数权重0.5", 0.5),
)
C150_CSI800_WINV = ("sleeve_c150_csi800_winv", "低估值1.5 · 300或500 · 淡化指数权重0.25")
C150_CSI800_STABLE = ("sleeve_c150_csi800_st", "低估值1.5 · 300或500连续两期")
RS60_ST = ("sleeve_rs60_st", "连续两期300或500 · 60日涨幅")
RS120_ST = ("sleeve_rs120_st", "连续两期300或500 · 120日涨幅")
BRK60_ST = ("sleeve_brk60_st", "连续两期300或500 · 60日价格分位")
BRK120_ST = ("sleeve_brk120_st", "连续两期300或500 · 120日价格分位")
QTURN_ST = ("sleeve_qturn_st", "连续两期300或500 · 低换手")
QVOL_ST = ("sleeve_qvol_st", "连续两期300或500 · 低波动")
QMIX_ST = ("sleeve_qmix_st", "连续两期300或500 · 低换手加低波动")
C150_CSI800_S5 = ("sleeve_c150_csi800_s5", "低估值1.5 · 300或500 · 中证500加分0.25")
C150_ST_CSPE = ("sleeve_c150_st_cspe", "连续两期300或500 · 池内重排估值")
C150_ST_CSDV = ("sleeve_c150_st_csdv", "连续两期300或500 · 池内重排估值和股息")
C150_ST_CSALL = ("sleeve_c150_st_csall", "连续两期300或500 · 池内重排估值股息规模")
C150_ST_S000 = ("sleeve_c150_st_s000", "连续两期300或500 · 去掉规模")
C150_ST_CSPE0 = ("sleeve_c150_st_cspe0", "连续两期300或500 · 池内估值且去掉规模")
C150_CSPE = ("sleeve_c150_cspe", "300或500 · 池内重排估值")
C150_ST_W005 = ("sleeve_c150_st_w005", "连续两期300或500 · 指数权重≥0.05")
C150_ST_W010 = ("sleeve_c150_st_w010", "连续两期300或500 · 指数权重≥0.10")
C150_ST_W_NEW: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_w003", "连续两期300或500 · 指数权重≥0.03", 0.03),
    ("sleeve_c150_st_w004", "连续两期300或500 · 指数权重≥0.04", 0.04),
    ("sleeve_c150_st_w006", "连续两期300或500 · 指数权重≥0.06", 0.06),
    ("sleeve_c150_st_w007", "连续两期300或500 · 指数权重≥0.07", 0.07),
    ("sleeve_c150_st_w008", "连续两期300或500 · 指数权重≥0.08", 0.08),
)
C150_ST_W2 = ("sleeve_c150_st_w2", "连续两期300或500 · 两期权重都≥0.05")
C150_ST_WP: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_wp05", "连续两期300或500 · 去掉权重最低5%", 0.05),
    ("sleeve_c150_st_wp10", "连续两期300或500 · 去掉权重最低10%", 0.10),
)
C150_ST_A510 = ("sleeve_c150_st_a510", "连续两期 · 300权重≥0.05且500权重≥0.10")
C150_ST_A605 = ("sleeve_c150_st_a605", "连续两期 · 300权重≥0.06且500权重≥0.05")
C150_ST_A_LADDER: tuple[tuple[str, str, float, float], ...] = (
    ("sleeve_c150_st_a705", "连续两期 · 300权重≥0.07且500权重≥0.05", 0.07, 0.05),
    ("sleeve_c150_st_a755", "连续两期 · 300权重≥0.075且500权重≥0.05", 0.075, 0.05),
    ("sleeve_c150_st_a805", "连续两期 · 300权重≥0.08且500权重≥0.05", 0.08, 0.05),
    ("sleeve_c150_st_a855", "连续两期 · 300权重≥0.085且500权重≥0.05", 0.085, 0.05),
    ("sleeve_c150_st_a905", "连续两期 · 300权重≥0.09且500权重≥0.05", 0.09, 0.05),
)
C150_ST_A_W2: tuple[tuple[str, str, float, float], ...] = (
    ("sleeve_c150_st_a605w2", "连续两期 · 300≥0.06且500≥0.05 · 两期权重都过线", 0.06, 0.05),
    ("sleeve_c150_st_a705w2", "连续两期 · 300≥0.07且500≥0.05 · 两期权重都过线", 0.07, 0.05),
    ("sleeve_c150_st_a805w2", "连续两期 · 300≥0.08且500≥0.05 · 两期权重都过线", 0.08, 0.05),
)
C150_ST_HQ: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_hq10", "连续两期 · 沪深300去掉权重最低10%且500≥0.05", 0.10),
    ("sleeve_c150_st_hq20", "连续两期 · 沪深300去掉权重最低20%且500≥0.05", 0.20),
)
C150_ST_W2M0 = ("sleeve_c150_st_w2m0", "当前最高两期过线 · 60日动量为正")
C150_ST_W2M: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_w2m10", "当前最高两期过线 · 去掉60日动量最低10%", 0.10),
    ("sleeve_c150_st_w2m20", "当前最高两期过线 · 去掉60日动量最低20%", 0.20),
    ("sleeve_c150_st_w2m30", "当前最高两期过线 · 去掉60日动量最低30%", 0.30),
)
C150_ST_W2R: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_w2r20", "当前最高两期过线 · 去掉振幅最低20%", 0.20),
    ("sleeve_c150_st_w2r50", "当前最高两期过线 · 去掉振幅最低50%", 0.50),
)
C150_ST_W2T: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_w2t20", "当前最高两期过线 · 去掉换手最低20%", 0.20),
    ("sleeve_c150_st_w2t50", "当前最高两期过线 · 去掉换手最低50%", 0.50),
)
C150_ST_W2L80 = ("sleeve_c150_st_w2l80", "当前最高两期过线 · 收盘位置≤0.8")
C150_ST_W2L90 = ("sleeve_c150_st_w2l90", "当前最高两期过线 · 收盘位置≤0.9")
C150_ST_W2L50 = ("sleeve_c150_st_w2l50", "当前最高两期过线 · 收盘位置低于当日中位")
C150_ST_W2_TILT: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_w2tu025", "当前最高两期过线 · 换手0.25", "turn", 0.25),
    ("sleeve_c150_st_w2tu050", "当前最高两期过线 · 换手0.50", "turn", 0.50),
    ("sleeve_c150_st_w2rg025", "当前最高两期过线 · 振幅0.25", "range", 0.25),
    ("sleeve_c150_st_w2rg050", "当前最高两期过线 · 振幅0.50", "range", 0.50),
    ("sleeve_c150_st_w2lc025", "当前最高两期过线 · 低收盘位置0.25", "lowloc", 0.25),
    ("sleeve_c150_st_w2lc050", "当前最高两期过线 · 低收盘位置0.50", "lowloc", 0.50),
    ("sleeve_c150_st_w2rv025", "当前最高两期过线 · 10日反转0.25", "rev10", 0.25),
    ("sleeve_c150_st_w2rv050", "当前最高两期过线 · 10日反转0.50", "rev10", 0.50),
    ("sleeve_c150_st_w2dv025", "当前最高两期过线 · 淡化股息0.25", "fadediv", 0.25),
    ("sleeve_c150_st_w2az025", "当前最高两期过线 · 成交额偏高0.25", "amtz", 0.25),
    ("sleeve_c150_st_w2mo025", "当前最高两期过线 · 60日动量0.25", "mom60", 0.25),
)
C150_ST_W2_MICRO: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_w2dv005", "当前最高两期过线 · 淡化股息0.05", "fadediv", 0.05),
    ("sleeve_c150_st_w2dv010", "当前最高两期过线 · 淡化股息0.10", "fadediv", 0.10),
    ("sleeve_c150_st_w2rv005", "当前最高两期过线 · 10日反转0.05", "rev10", 0.05),
    ("sleeve_c150_st_w2rv010", "当前最高两期过线 · 10日反转0.10", "rev10", 0.10),
    ("sleeve_c150_st_w2lc005", "当前最高两期过线 · 低收盘位置0.05", "lowloc", 0.05),
    ("sleeve_c150_st_w2lc010", "当前最高两期过线 · 低收盘位置0.10", "lowloc", 0.10),
    ("sleeve_c150_st_w2qt005", "当前最高两期过线 · 低换手0.05", "quiet", 0.05),
    ("sleeve_c150_st_w2qt010", "当前最高两期过线 · 低换手0.10", "quiet", 0.10),
    ("sleeve_c150_st_w2fg005", "当前最高两期过线 · 淡化隔夜0.05", "fadegap", 0.05),
    ("sleeve_c150_st_w2iw005", "当前最高两期过线 · 指数权重0.05", "idxw", 0.05),
    ("sleeve_c150_st_w2iw010", "当前最高两期过线 · 指数权重0.10", "idxw", 0.10),
)
C150_ST_W2_LEG: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_w2dv02", "当前最高两期过线 · 淡化股息0.02", "fadediv", 0.02),
    ("sleeve_c150_st_w2dv03", "当前最高两期过线 · 淡化股息0.03", "fadediv", 0.03),
    ("sleeve_c150_st_w2ed005", "当前最高两期过线 · 股息加0.05", "div", 0.05),
    ("sleeve_c150_st_w2ed010", "当前最高两期过线 · 股息加0.10", "div", 0.10),
    ("sleeve_c150_st_w2ed015", "当前最高两期过线 · 股息加0.15", "div", 0.15),
    ("sleeve_c150_st_w2ch005", "当前最高两期过线 · 低估值加0.05", "cheap", 0.05),
    ("sleeve_c150_st_w2ch010", "当前最高两期过线 · 低估值加0.10", "cheap", 0.10),
    ("sleeve_c150_st_w2pr005", "当前最高两期过线 · 价格加0.05", "price", 0.05),
    ("sleeve_c150_st_w2pr010", "当前最高两期过线 · 价格加0.10", "price", 0.10),
    ("sleeve_c150_st_w2sz005", "当前最高两期过线 · 规模加0.05", "size", 0.05),
    ("sleeve_c150_st_w2fs005", "当前最高两期过线 · 淡化规模0.05", "fadesize", 0.05),
)
C150_ST_W2_PEAK: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_w2d025", "当前最高两期过线 · 淡化股息0.025", "fadediv", 0.025),
    ("sleeve_c150_st_w2d028", "当前最高两期过线 · 淡化股息0.028", "fadediv", 0.028),
    ("sleeve_c150_st_w2d032", "当前最高两期过线 · 淡化股息0.032", "fadediv", 0.032),
    ("sleeve_c150_st_w2d035", "当前最高两期过线 · 淡化股息0.035", "fadediv", 0.035),
    ("sleeve_c150_st_w2d036", "当前最高两期过线 · 淡化股息0.036", "fadediv", 0.036),
    ("sleeve_c150_st_w2d037", "当前最高两期过线 · 淡化股息0.037", "fadediv", 0.037),
    ("sleeve_c150_st_w2d038", "当前最高两期过线 · 淡化股息0.038", "fadediv", 0.038),
    ("sleeve_c150_st_w2d039", "当前最高两期过线 · 淡化股息0.039", "fadediv", 0.039),
    ("sleeve_c150_st_w2d040", "当前最高两期过线 · 淡化股息0.040", "fadediv", 0.040),
    ("sleeve_c150_st_w2d041", "当前最高两期过线 · 淡化股息0.041", "fadediv", 0.041),
    ("sleeve_c150_st_w2d042", "当前最高两期过线 · 淡化股息0.042", "fadediv", 0.042),
    ("sleeve_c150_st_w2d043", "当前最高两期过线 · 淡化股息0.043", "fadediv", 0.043),
    ("sleeve_c150_st_w2d045", "当前最高两期过线 · 淡化股息0.045", "fadediv", 0.045),
)
C150_ST_DV03_TILT: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_d3sz02", "当前最高淡化股息0.03 · 规模加0.02", "size", 0.02),
    ("sleeve_c150_st_d3sz03", "当前最高淡化股息0.03 · 规模加0.03", "size", 0.03),
    ("sleeve_c150_st_d3ch02", "当前最高淡化股息0.03 · 低估值加0.02", "cheap", 0.02),
    ("sleeve_c150_st_d3pr02", "当前最高淡化股息0.03 · 价格加0.02", "price", 0.02),
    ("sleeve_c150_st_d3ed02", "当前最高淡化股息0.03 · 股息加0.02", "div", 0.02),
    ("sleeve_c150_st_d3fs02", "当前最高淡化股息0.03 · 淡化规模0.02", "fadesize", 0.02),
    ("sleeve_c150_st_d3ch005", "当前最高淡化股息0.03 · 低估值加0.005", "cheap", 0.005),
    ("sleeve_c150_st_d3pr005", "当前最高淡化股息0.03 · 价格加0.005", "price", 0.005),
    ("sleeve_c150_st_d3ed005", "当前最高淡化股息0.03 · 股息加0.005", "div", 0.005),
    ("sleeve_c150_st_d3sz005", "当前最高淡化股息0.03 · 规模加0.005", "size", 0.005),
    ("sleeve_c150_st_d3chm005", "当前最高淡化股息0.03 · 低估值减0.005", "cheap", -0.005),
    ("sleeve_c150_st_d3prm005", "当前最高淡化股息0.03 · 价格减0.005", "price", -0.005),
    ("sleeve_c150_st_d3edm005", "当前最高淡化股息0.03 · 股息减0.005", "div", -0.005),
    ("sleeve_c150_st_d3szm005", "当前最高淡化股息0.03 · 规模减0.005", "size", -0.005),
    ("sleeve_c150_st_d3qt005", "当前最高淡化股息0.03 · 低换手加0.005", "quiet", 0.005),
    ("sleeve_c150_st_d3qt006", "当前最高淡化股息0.03 · 低换手加0.006", "quiet", 0.006),
    ("sleeve_c150_st_d3qt007", "当前最高淡化股息0.03 · 低换手加0.007", "quiet", 0.007),
    ("sleeve_c150_st_d3qt008", "当前最高淡化股息0.03 · 低换手加0.008", "quiet", 0.008),
    ("sleeve_c150_st_d3qt010", "当前最高淡化股息0.03 · 低换手加0.010", "quiet", 0.010),
    ("sleeve_c150_st_d3qt015", "当前最高淡化股息0.03 · 低换手加0.015", "quiet", 0.015),
    ("sleeve_c150_st_d3qt020", "当前最高淡化股息0.03 · 低换手加0.020", "quiet", 0.020),
    ("sleeve_c150_st_d3qt025", "当前最高淡化股息0.03 · 低换手加0.025", "quiet", 0.025),
    ("sleeve_c150_st_d3qt030", "当前最高淡化股息0.03 · 低换手加0.030", "quiet", 0.030),
    ("sleeve_c150_st_d3rv005", "当前最高淡化股息0.03 · 10日反转加0.005", "rev10", 0.005),
    ("sleeve_c150_st_d3mo005", "当前最高淡化股息0.03 · 60日动量加0.005", "mom60", 0.005),
    ("sleeve_c150_st_d3az005", "当前最高淡化股息0.03 · 成交额偏高加0.005", "amtz", 0.005),
    ("sleeve_c150_st_d3fg005", "当前最高淡化股息0.03 · 淡化隔夜加0.005", "fadegap", 0.005),
    ("sleeve_c150_st_d3iw005", "当前最高淡化股息0.03 · 指数权重加0.005", "idxw", 0.005),
    ("sleeve_c150_st_d3tu005", "当前最高淡化股息0.03 · 换手加0.005", "turn", 0.005),
    ("sleeve_c150_st_d3lc005", "当前最高淡化股息0.03 · 低收盘位置加0.005", "lowloc", 0.005),
    ("sleeve_c150_st_d3fs005", "当前最高淡化股息0.03 · 淡化规模加0.005", "fadesize", 0.005),
)
C150_ST_DV03_CSRANK: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_d3vl025", "当前最高淡化股息0.03 · 淡化均量截面0.25", "fadevolcs", 0.25),
    ("sleeve_c150_st_d3m1025", "当前最高淡化股息0.03 · 10日动量截面0.25", "mom10cs", 0.25),
    ("sleeve_c150_st_d3fb025", "当前最高淡化股息0.03 · 淡化偏离截面0.25", "fadebiascs", 0.25),
)
C150_ST_QT005_TILT: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_q5chm005", "当前最高低换手0.005 · 低估值减0.005", "cheap", -0.005),
    ("sleeve_c150_st_q5ch005", "当前最高低换手0.005 · 低估值加0.005", "cheap", 0.005),
    ("sleeve_c150_st_q5prm005", "当前最高低换手0.005 · 价格减0.005", "price", -0.005),
    ("sleeve_c150_st_q5szm005", "当前最高低换手0.005 · 规模减0.005", "size", -0.005),
    ("sleeve_c150_st_q5sz005", "当前最高低换手0.005 · 规模加0.005", "size", 0.005),
    ("sleeve_c150_st_q5mo005", "当前最高低换手0.005 · 60日动量加0.005", "mom60", 0.005),
    ("sleeve_c150_st_q5iw005", "当前最高低换手0.005 · 指数权重加0.005", "idxw", 0.005),
    ("sleeve_c150_st_q5rv005", "当前最高低换手0.005 · 10日反转加0.005", "rev10", 0.005),
    ("sleeve_c150_st_q5lc005", "当前最高低换手0.005 · 低收盘位置加0.005", "lowloc", 0.005),
    ("sleeve_c150_st_q5fs005", "当前最高低换手0.005 · 淡化规模加0.005", "fadesize", 0.005),
)
C150_ST_FADE_QT005: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_d028qt005", "当前最高淡化股息0.028 · 低换手加0.005", 0.028),
    ("sleeve_c150_st_d032qt005", "当前最高淡化股息0.032 · 低换手加0.005", 0.032),
    ("sleeve_c150_st_d035qt005", "当前最高淡化股息0.035 · 低换手加0.005", 0.035),
    ("sleeve_c150_st_d040qt005", "当前最高淡化股息0.040 · 低换手加0.005", 0.040),
)
C150_ST_DV03_SHAPE: tuple[tuple[str, str, str, float], ...] = (
    ("sleeve_c150_st_d3hi60", "当前最高两期过线 · 仅高股息淡化0.60", "hi", 0.60),
    ("sleeve_c150_st_d3hi70", "当前最高两期过线 · 仅高股息淡化0.70", "hi", 0.70),
    ("sleeve_c150_st_d3hi80", "当前最高两期过线 · 仅高股息淡化0.80", "hi", 0.80),
    ("sleeve_c150_st_d3lo40", "当前最高两期过线 · 仅低股息淡化0.40", "lo", 0.40),
    ("sleeve_c150_st_d3cl04", "当前最高两期过线 · 淡化股息截断0.40", "clip", 0.40),
    ("sleeve_c150_st_d3cl06", "当前最高两期过线 · 淡化股息截断0.60", "clip", 0.60),
    ("sleeve_c150_st_d3sq", "当前最高两期过线 · 淡化股息平方", "sq", 0.0),
    ("sleeve_c150_st_d3rt", "当前最高两期过线 · 淡化股息开方", "sqrt", 0.0),
)
C150_ST_T: tuple[tuple[str, str, float], ...] = (
    ("sleeve_c150_st_t025", "连续两期300或500 · 成分年限0.25", 0.25),
    ("sleeve_c150_st_t050", "连续两期300或500 · 成分年限0.50", 0.50),
)
C150_CSI800_POOL_FIELDS: tuple[str, ...] = (
    C150_ST_CSPE[0],
    C150_ST_CSDV[0],
    C150_ST_CSALL[0],
    C150_ST_S000[0],
    C150_ST_CSPE0[0],
    C150_CSPE[0],
    C150_ST_W005[0],
    C150_ST_W010[0],
    *(item[0] for item in C150_ST_W_NEW),
    C150_ST_W2[0],
    *(item[0] for item in C150_ST_WP),
    C150_ST_A510[0],
    C150_ST_A605[0],
    *(item[0] for item in C150_ST_A_LADDER),
    *(item[0] for item in C150_ST_A_W2),
    *(item[0] for item in C150_ST_HQ),
    C150_ST_W2M0[0],
    *(item[0] for item in C150_ST_W2M),
    *(item[0] for item in C150_ST_W2R),
    *(item[0] for item in C150_ST_W2T),
    C150_ST_W2L80[0],
    C150_ST_W2L90[0],
    C150_ST_W2L50[0],
    *(item[0] for item in C150_ST_W2_TILT),
    *(item[0] for item in C150_ST_W2_MICRO),
    *(item[0] for item in C150_ST_W2_LEG),
    *(item[0] for item in C150_ST_W2_PEAK),
    *(item[0] for item in C150_ST_DV03_TILT),
    *(item[0] for item in C150_ST_DV03_CSRANK),
    *(item[0] for item in C150_ST_QT005_TILT),
    *(item[0] for item in C150_ST_FADE_QT005),
    *(item[0] for item in C150_ST_DV03_SHAPE),
    *(item[0] for item in C150_ST_T),
)


def c075_mom_formula(mom: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 0.75)
        + " + "
        + _weighted_term(mom, "momentum_10.cs_rank(0)")
    )


def c150_quiet_formula(quiet: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(quiet, "(1 - turn.cs_rank(0))")
    )


def c150_range_formula(weight: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(weight, "(1 - intraday_range.cs_rank(0))")
    )


def c150_amount_formula(weight: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(weight, "amount.cs_rank(0)")
    )


def c150_overnight_formula(weight: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(weight, "overnight_ret.cs_rank(0)")
    )


def c150_location_formula(weight: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(weight, "close_location.cs_rank(0)")
    )


def c150_shape_formula(shape: str) -> str:
    price_term = "close_zscore_60.cs_rank(0)" if shape == "zscore" else "close_bias_60.cs_rank(0)"
    return " + ".join(
        (
            price_term,
            "total_market_cap.cs_rank(0)",
            "dividend_yield_ratio.cs_rank(0)",
            "1.5 * (1 - pe_ttm.cs_rank(0))",
        )
    )


def c150_p20_formula() -> str:
    return (
        "hfq_close.ts_rank(20) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + 1.5 * (1 - pe_ttm.cs_rank(0))"
    )


def c150_float_formula() -> str:
    return (
        "hfq_close.ts_rank(60) + float_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + 1.5 * (1 - pe_ttm.cs_rank(0))"
    )


def c150_az_formula(weight: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(weight, "amount_zscore_20.cs_rank(0)")
    )


def c150_hv_formula(weight: float) -> str:
    return (
        price60_cheap_formula(1, 1, 1, 1.5)
        + " + "
        + _weighted_term(weight, "vol.cs_rank(0)")
    )


def c150_lookback_formula(window: int) -> str:
    return (
        f"hfq_close.ts_rank({int(window)}) + total_market_cap.cs_rank(0) + "
        "dividend_yield_ratio.cs_rank(0) + 1.5 * (1 - pe_ttm.cs_rank(0))"
    )


def _board_drop_mask(codes: pd.Series, kind: str) -> pd.Series:
    symbol = codes.astype(str).str.split(".").str[0]
    is_star = symbol.str.startswith("688")
    is_cyb = symbol.str.startswith("300") | symbol.str.startswith("301")
    if kind == "star":
        return is_star
    if kind == "cyb":
        return is_cyb
    return is_star | is_cyb


def _default_index_weight_dir(sidecar: Path) -> Path:
    return sidecar.parent.parent / "raw" / "index_weight"


def _snap_lookup(dates: pd.Series, snaps: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    unique_dates = sorted({str(item) for item in _compact_date(dates).tolist()})
    date_to_snap: dict[str, str] = {}
    date_to_prev: dict[str, str] = {}
    index = 0
    last = ""
    previous = ""
    for day in unique_dates:
        while index < len(snaps) and snaps[index] <= day:
            previous = last
            last = snaps[index]
            index += 1
        date_to_snap[day] = last if last and last <= day else ""
        date_to_prev[day] = previous if previous and previous <= day else ""
    return date_to_snap, date_to_prev


def _asof_index_join(
    codes: pd.Series,
    dates: pd.Series,
    weight_path: Path,
    snap_by_date: dict[str, str] | None = None,
) -> pd.DataFrame | None:
    if not weight_path.is_file():
        return None
    available = set(pq.ParquetFile(weight_path).schema_arrow.names)
    columns = [name for name in ("con_code", "trade_date", "weight") if name in available]
    if "con_code" not in columns or "trade_date" not in columns:
        return None
    weights = pq.read_table(weight_path, columns=columns).to_pandas()
    weights["_code"] = weights["con_code"].astype(str)
    weights["_snap"] = _compact_date(weights["trade_date"])
    if "weight" in weights.columns:
        weights["_weight"] = pd.to_numeric(weights["weight"], errors="coerce")
    else:
        weights["_weight"] = pd.NA
    snaps = sorted({str(item) for item in weights["_snap"].tolist() if str(item)})
    if not snaps:
        return None
    if snap_by_date is None:
        snap_by_date, _previous = _snap_lookup(dates, snaps)
    compact = _compact_date(dates)
    mapped = pd.DataFrame(
        {
            "_row": codes.index,
            "_code": codes.astype(str).to_numpy(),
            "_snap": compact.map(snap_by_date).fillna("").astype(str).to_numpy(),
        }
    )
    members = (
        weights[["_code", "_snap", "_weight"]]
        .drop_duplicates(["_code", "_snap"], keep="last")
        .assign(_in=1)
    )
    merged = mapped.merge(members, on=["_code", "_snap"], how="left")
    grouped = merged.groupby("_row", sort=False)
    out = pd.DataFrame(
        {
            "_in": grouped["_in"].max().eq(1),
            "_weight": grouped["_weight"].mean(),
        }
    )
    return out.reindex(codes.index)


def _asof_member_mask(codes: pd.Series, dates: pd.Series, weight_path: Path) -> pd.Series | None:
    joined = _asof_index_join(codes, dates, weight_path)
    if joined is None:
        return None
    return joined["_in"].fillna(False).astype(bool)


def _asof_index_weight(codes: pd.Series, dates: pd.Series, weight_path: Path) -> pd.Series | None:
    joined = _asof_index_join(codes, dates, weight_path)
    if joined is None:
        return None
    return pd.to_numeric(joined["_weight"], errors="coerce")


def _asof_prev_member_mask(codes: pd.Series, dates: pd.Series, *weight_paths: Path) -> pd.Series | None:
    snaps: list[str] = []
    usable: list[Path] = []
    for path in weight_paths:
        if not path.is_file():
            continue
        usable.append(path)
        table = pq.read_table(path, columns=["trade_date"]).to_pandas()
        snaps.extend(str(item) for item in _compact_date(table["trade_date"]).tolist())
    snaps = sorted({item for item in snaps if item})
    if not usable or not snaps:
        return None
    _current, prev_map = _snap_lookup(dates, snaps)
    masks: list[pd.Series] = []
    for path in usable:
        joined = _asof_index_join(codes, dates, path, snap_by_date=prev_map)
        if joined is not None:
            masks.append(joined["_in"].fillna(False).astype(bool))
    if not masks:
        return None
    out = masks[0]
    for extra in masks[1:]:
        out = out | extra
    return out


def _asof_prev_index_weight(codes: pd.Series, dates: pd.Series, *weight_paths: Path) -> pd.Series | None:
    snaps: list[str] = []
    usable: list[Path] = []
    for path in weight_paths:
        if not path.is_file():
            continue
        usable.append(path)
        table = pq.read_table(path, columns=["trade_date"]).to_pandas()
        snaps.extend(str(item) for item in _compact_date(table["trade_date"]).tolist())
    snaps = sorted({item for item in snaps if item})
    if not usable or not snaps:
        return None
    _current, prev_map = _snap_lookup(dates, snaps)
    weights: list[pd.Series] = []
    for path in usable:
        joined = _asof_index_join(codes, dates, path, snap_by_date=prev_map)
        if joined is not None:
            weights.append(pd.to_numeric(joined["_weight"], errors="coerce"))
    if not weights:
        return None
    out = weights[0]
    for extra in weights[1:]:
        out = out.fillna(extra)
    return out


def _cs_quantile_where(values: pd.Series, dates: pd.Series, mask: pd.Series, q: float) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").where(mask.fillna(False))
    return numeric.groupby(dates, sort=False).transform(
        lambda part: part.quantile(q) if part.notna().any() else float("nan")
    )


def _cs_rank_where(values: pd.Series, dates: pd.Series, mask: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").where(mask.fillna(False))
    return numeric.groupby(dates, sort=False).rank(pct=True)


def _consecutive_membership_tenure(
    codes: pd.Series,
    dates: pd.Series,
    *weight_paths: Path,
) -> pd.Series | None:
    usable = [path for path in weight_paths if path.is_file()]
    if not usable:
        return None
    frames: list[pd.DataFrame] = []
    for path in usable:
        table = pq.read_table(path, columns=["con_code", "trade_date"]).to_pandas()
        frames.append(
            pd.DataFrame(
                {
                    "_code": table["con_code"].astype(str),
                    "_snap": _compact_date(table["trade_date"]),
                }
            )
        )
    members = pd.concat(frames, ignore_index=True).drop_duplicates(["_code", "_snap"])
    snaps = sorted({str(item) for item in members["_snap"].tolist() if str(item)})
    if not snaps:
        return None
    present: dict[str, set[str]] = {snap: set() for snap in snaps}
    for code, snap in zip(members["_code"].tolist(), members["_snap"].tolist()):
        present[str(snap)].add(str(code))
    tenure_rows: dict[str, list[object]] = {"_code": [], "_snap": [], "_tenure": []}
    prev_ten: dict[str, int] = {}
    prev_snap: str | None = None
    for snap in snaps:
        cur_ten: dict[str, int] = {}
        for code in present[snap]:
            cur_ten[code] = prev_ten[code] + 1 if prev_snap is not None and code in prev_ten else 1
        for code, value in cur_ten.items():
            tenure_rows["_code"].append(code)
            tenure_rows["_snap"].append(snap)
            tenure_rows["_tenure"].append(value)
        prev_snap = snap
        prev_ten = cur_ten
    snap_by_date, _previous = _snap_lookup(dates, snaps)
    compact = _compact_date(dates)
    mapped = pd.DataFrame(
        {
            "_row": codes.index,
            "_code": codes.astype(str).to_numpy(),
            "_snap": compact.map(snap_by_date).fillna("").astype(str).to_numpy(),
        }
    )
    merged = mapped.merge(pd.DataFrame(tenure_rows), on=["_code", "_snap"], how="left")
    out = merged.groupby("_row", sort=False)["_tenure"].max()
    return pd.to_numeric(out.reindex(codes.index), errors="coerce")


COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(price, size, div, cheap)
        for field, _name, price, size, div, cheap in PRICE60_CHEAP_WEIGHTS
    }
)
COMPOSITE_FORMULAS.update(
    {field: c075_mom_formula(mom) for field, _name, mom in C075_MOM_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(1, size, 1, 0.75)
        for field, _name, size in C075_SIZE_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(price, 1, 1, 0.75)
        for field, _name, price in C075_PRICE_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(1, 1, 0.75, cheap)
        for field, _name, cheap in D075_CHEAP_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(1, size, 1, 1.5)
        for field, _name, size in C150_SIZE_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(1, 1, div, 1.5)
        for field, _name, div in C150_DIV_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {field: c150_quiet_formula(quiet) for field, _name, quiet in C150_QUIET_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {
        field: price60_cheap_formula(price, 1, 1, 1.5)
        for field, _name, price in C150_PRICE_BLENDS
    }
)
COMPOSITE_FORMULAS.update(
    {field: c150_range_formula(weight) for field, _name, weight in C150_RANGE_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {field: c150_amount_formula(weight) for field, _name, weight in C150_AMOUNT_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {field: c150_overnight_formula(weight) for field, _name, weight in C150_OVERNIGHT_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {field: c150_location_formula(weight) for field, _name, weight in C150_LOCATION_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {field: c150_shape_formula(shape) for field, _name, shape in C150_SHAPE_BLENDS}
)
COMPOSITE_FORMULAS[C150_P20[0]] = c150_p20_formula()
COMPOSITE_FORMULAS[C150_FLOAT[0]] = c150_float_formula()
COMPOSITE_FORMULAS.update(
    {field: c150_az_formula(weight) for field, _name, weight in C150_AZ_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind in C150_BOARD_BLENDS}
)
COMPOSITE_FORMULAS[C150_P000[0]] = price60_cheap_formula(0, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_S000[0]] = price60_cheap_formula(1, 0, 1, 1.5)
COMPOSITE_FORMULAS[C150_D075[0]] = price60_cheap_formula(1, 1, 0.75, 1.5)
COMPOSITE_FORMULAS[C150_P120[0]] = c150_lookback_formula(120)
COMPOSITE_FORMULAS[C150_P252[0]] = c150_lookback_formula(252)
COMPOSITE_FORMULAS.update(
    {field: c150_hv_formula(weight) for field, _name, weight in C150_HV_BLENDS}
)
COMPOSITE_FORMULAS[C150_AZPOS[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind in C150_MA_BLENDS}
)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _weight in C150_REV_MOM}
)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _weight in C150_FADE_GAP}
)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _weight in C150_TREND_BLENDS}
)
COMPOSITE_FORMULAS[C150_INST[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_RM60[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_LOWLOC[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind in C150_UNIVERSE}
)
COMPOSITE_FORMULAS[C150_CSI500[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _weight in C150_CSI800_W}
)
COMPOSITE_FORMULAS[C150_CSI800_WINV[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_CSI800_STABLE[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[RS60_ST[0]] = "hfq_close.pct_change(60)"
COMPOSITE_FORMULAS[RS120_ST[0]] = "hfq_close.pct_change(120)"
COMPOSITE_FORMULAS[BRK60_ST[0]] = "hfq_close.ts_rank(60)"
COMPOSITE_FORMULAS[BRK120_ST[0]] = "hfq_close.ts_rank(120)"
COMPOSITE_FORMULAS[QTURN_ST[0]] = "turn.cs_rank(0)"
COMPOSITE_FORMULAS[QVOL_ST[0]] = "hfq_close.pct_change(1).rolling_std(20)"
COMPOSITE_FORMULAS[QMIX_ST[0]] = "turn.cs_rank(0)"
COMPOSITE_FORMULAS[C150_CSI800_S5[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_CSPE[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_CSDV[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_CSALL[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_S000[0]] = price60_cheap_formula(1, 0, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_CSPE0[0]] = price60_cheap_formula(1, 0, 1, 1.5)
COMPOSITE_FORMULAS[C150_CSPE[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_W005[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_W010[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _thr in C150_ST_W_NEW})
COMPOSITE_FORMULAS[C150_ST_W2[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _q in C150_ST_WP})
COMPOSITE_FORMULAS[C150_ST_A510[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_A605[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _thr300, _thr500 in C150_ST_A_LADDER}
)
COMPOSITE_FORMULAS.update(
    {field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _thr300, _thr500 in C150_ST_A_W2}
)
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _q in C150_ST_HQ})
COMPOSITE_FORMULAS[C150_ST_W2M0[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _q in C150_ST_W2M})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _q in C150_ST_W2R})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _q in C150_ST_W2T})
COMPOSITE_FORMULAS[C150_ST_W2L80[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_W2L90[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS[C150_ST_W2L50[0]] = price60_cheap_formula(1, 1, 1, 1.5)
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_W2_TILT})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_W2_MICRO})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_W2_LEG})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_W2_PEAK})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_DV03_TILT})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_DV03_CSRANK})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_QT005_TILT})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _w in C150_ST_FADE_QT005})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _kind, _w in C150_ST_DV03_SHAPE})
COMPOSITE_FORMULAS.update({field: price60_cheap_formula(1, 1, 1, 1.5) for field, _name, _weight in C150_ST_T})
COMPOSITE_FIELDS = tuple(COMPOSITE_FORMULAS)
_COMPOSITE_SOURCE_COLUMNS = (
    "total_mv_cs_rank",
    "div_yield_cs_rank",
    "close_ts_rank_20",
    "close_ts_rank_60",
    "close_ts_rank_120",
    "close_ts_rank_252",
    "close_zscore_60",
    "close_bias_20",
    "close_bias_60",
    "float_mv_cs_rank",
    "momentum_10",
    "momentum_60",
    "momentum_120",
    "volatility_20",
    "vol_mean_20",
    "intraday_range",
    "pe_ttm_cs_rank",
    "turn_cs_rank",
    "amount_cs_rank",
    "overnight_ret",
    "close_location",
    "amount_zscore_20",
    "vol_mean_20_cs_rank",
    "close_bias_20_cs_rank",
    "momentum_10_cs_rank",
)


def default_sidecar_path(canonical_path: Path | str) -> Path:
    return Path(canonical_path).expanduser().resolve().parent / "derived" / PACK_SIDECAR_NAME


def default_composite_sidecar_path(canonical_path: Path | str) -> Path:
    return Path(canonical_path).expanduser().resolve().parent / "derived" / COMPOSITE_SIDECAR_NAME


def _display_output_path(output: Path, base: Path) -> str:
    return posix_relative(output, base)


def _compact_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace("-", "", regex=False).str.replace(".", "", regex=False).str[:8]


def _needed_fields(
    frame: pd.DataFrame, refs: list[Any] | None, allowed: set[str]
) -> list[str]:
    needed: list[str] = []
    columns = set(getattr(frame, "columns", ()))
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or item.get("factor_id") or "").strip()
        if field.startswith("factor_"):
            field = field.removeprefix("factor_")
        if field in allowed and field not in columns and field not in needed:
            needed.append(field)
    return needed


def _needed_pack_fields(frame: pd.DataFrame, refs: list[Any] | None) -> list[str]:
    return _needed_fields(frame, refs, _ATTACHABLE_FIELDS)


def _merge_sidecar(
    frame: pd.DataFrame, needed: list[str], sidecar: Path, missing_message: str
) -> pd.DataFrame:
    if not needed:
        return frame
    if not sidecar.is_file():
        raise ValueError(missing_message)
    names = set(pq.ParquetFile(sidecar).schema_arrow.names)
    missing = [field for field in needed if field not in names]
    if missing:
        raise ValueError(f"旁路因子文件缺少字段 {', '.join(missing)}。")
    code_key = "ts_code" if "ts_code" in names else "instrument"
    date_key = "trade_date" if "trade_date" in names else "date"
    extra = pq.read_table(sidecar, columns=[code_key, date_key, *needed]).to_pandas()
    extra["_code"] = extra[code_key].astype(str)
    extra["_date"] = _compact_date(extra[date_key])
    result = frame.copy()
    left_code = result["instrument"] if "instrument" in result.columns else result["ts_code"]
    left_date = result["date"] if "date" in result.columns else result["trade_date"]
    result["_code"] = left_code.astype(str)
    result["_date"] = _compact_date(left_date)
    merged = result.merge(extra[["_code", "_date", *needed]], on=["_code", "_date"], how="left")
    return merged.drop(columns=["_code", "_date"])


def attach_pack_factor_columns(
    frame: pd.DataFrame,
    refs: list[Any] | None,
    sidecar_path: Path | str,
) -> pd.DataFrame:
    if frame is None or getattr(frame, "empty", True):
        return frame
    sidecar = Path(sidecar_path)
    result = _merge_sidecar(
        frame,
        _needed_fields(frame, refs, _ATTACHABLE_FIELDS),
        sidecar,
        "可算因子还没有写入旁路文件。请先补全因子计算。",
    )
    composite_needed = _needed_fields(result, refs, set(COMPOSITE_FORMULAS))
    if composite_needed:
        result = _merge_sidecar(
            result,
            composite_needed,
            sidecar.parent / COMPOSITE_SIDECAR_NAME,
            "截面合成因子还没有写入旁路文件。请先生成 composite_pack_factors.parquet。",
        )
    moneyflow_needed = _needed_fields(result, refs, set(MONEYFLOW_FIELDS))
    if moneyflow_needed:
        result = _merge_sidecar(
            result,
            moneyflow_needed,
            sidecar.parent / MONEYFLOW_SIDECAR_NAME,
            "资金流向因子还没有写入旁路文件。请先生成 moneyflow_pack_factors.parquet。",
        )
    qlib_sidecar = sidecar.parent / QLIB_PICK_SIDECAR_NAME
    if qlib_sidecar.is_file():
        names = set(pq.ParquetFile(qlib_sidecar).schema_arrow.names)
        qlib_needed = _needed_fields(
            result,
            refs,
            names - {"ts_code", "trade_date", "instrument", "date"},
        )
        if qlib_needed:
            result = _merge_sidecar(
                result,
                qlib_needed,
                qlib_sidecar,
                "qlib 挖因子还没有写入旁路文件。",
            )
    return result


def _cs_rank(values: pd.Series, dates: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return numeric.groupby(dates, sort=False).rank(pct=True)


def materialize_composite_pack_factors(
    pack_sidecar_path: Path | str,
    index_weight_dir: Path | str | None = None,
) -> dict[str, Any]:
    sidecar = Path(pack_sidecar_path).expanduser().resolve()
    if not sidecar.is_file():
        raise ValueError("找不到可算因子旁路文件")
    names = set(pq.ParquetFile(sidecar).schema_arrow.names)
    missing = [field for field in _COMPOSITE_SOURCE_COLUMNS if field not in names]
    if missing:
        raise ValueError(f"旁路因子文件缺少字段 {', '.join(missing)}。")
    code_key = "ts_code" if "ts_code" in names else "instrument"
    date_key = "trade_date" if "trade_date" in names else "date"
    frame = pq.read_table(
        sidecar,
        columns=[code_key, date_key, *_COMPOSITE_SOURCE_COLUMNS],
    ).to_pandas()
    dates = frame[date_key]
    mv = pd.to_numeric(frame["total_mv_cs_rank"], errors="coerce")
    div = pd.to_numeric(frame["div_yield_cs_rank"], errors="coerce")
    price = pd.to_numeric(frame["close_ts_rank_20"], errors="coerce")
    price60 = pd.to_numeric(frame["close_ts_rank_60"], errors="coerce")
    price120 = pd.to_numeric(frame["close_ts_rank_120"], errors="coerce")
    price252 = pd.to_numeric(frame["close_ts_rank_252"], errors="coerce")
    float_mv = pd.to_numeric(frame["float_mv_cs_rank"], errors="coerce")
    cheap = 1.0 - pd.to_numeric(frame["pe_ttm_cs_rank"], errors="coerce")
    quiet = 1.0 - pd.to_numeric(frame["turn_cs_rank"], errors="coerce")
    small = 1.0 - mv
    mom = _cs_rank(frame["momentum_10"], dates)
    mom60 = _cs_rank(frame["momentum_60"], dates)
    vol = _cs_rank(frame["vol_mean_20"], dates)
    rng = _cs_rank(frame["intraday_range"], dates)
    zscore60 = _cs_rank(frame["close_zscore_60"], dates)
    bias60_rank = _cs_rank(frame["close_bias_60"], dates)
    amt = pd.to_numeric(frame["amount_cs_rank"], errors="coerce")
    overnight = _cs_rank(frame["overnight_ret"], dates)
    loc = _cs_rank(frame["close_location"], dates)
    amt_z = _cs_rank(frame["amount_zscore_20"], dates)
    amt_z_raw = pd.to_numeric(frame["amount_zscore_20"], errors="coerce")
    bias20_raw = pd.to_numeric(frame["close_bias_20"], errors="coerce")
    bias60_raw = pd.to_numeric(frame["close_bias_60"], errors="coerce")
    columns: dict[str, Any] = {
            "ts_code": frame[code_key].astype(str).to_numpy(),
            "trade_date": _compact_date(dates).to_numpy(),
            "sleeve_mv_div": (mv + div).to_numpy(dtype="float64"),
            "sleeve_price_mv_div": (price + mv + div).to_numpy(dtype="float64"),
            "sleeve_mom_mv": (mom + mv).to_numpy(dtype="float64"),
            "sleeve_mom_price": (mom + price).to_numpy(dtype="float64"),
            "sleeve_mom_mv_div": (mom + mv + div).to_numpy(dtype="float64"),
            "sleeve_mom_price_mv_div": (mom + price + mv + div).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_vol": (price + mv + div + vol).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_range": (price + mv + div + rng).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div": (price60 + mv + div).to_numpy(dtype="float64"),
            "sleeve_price_float_div": (price + float_mv + div).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_cheap": (price + mv + div + cheap).to_numpy(dtype="float64"),
            "sleeve_price_mv_div_quiet": (price + mv + div + quiet).to_numpy(dtype="float64"),
            "sleeve_price_small_div": (price + small + div).to_numpy(dtype="float64"),
            "sleeve_price60_mv": (price60 + mv).to_numpy(dtype="float64"),
            "sleeve_price60_div": (price60 + div).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div_cheap": (price60 + mv + div + cheap).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div_quiet": (price60 + mv + div + quiet).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div_cheap_quiet": (price60 + mv + div + cheap + quiet).to_numpy(
                dtype="float64"
            ),
            "sleeve_price60_mv_div0": (price60 + mv + 0.0 * div).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div25": (price60 + mv + 0.25 * div).to_numpy(dtype="float64"),
            "sleeve_price60_mv_div50": (price60 + mv + 0.5 * div).to_numpy(dtype="float64"),
            "sleeve_zscore60_mv_div": (zscore60 + mv + div).to_numpy(dtype="float64"),
            "sleeve_bias60_mv_div": (bias60_rank + mv + div).to_numpy(dtype="float64"),
        }
    for field, _name, price_w, size_w, div_w, cheap_w in PRICE60_CHEAP_WEIGHTS:
        columns[field] = (price_w * price60 + size_w * mv + div_w * div + cheap_w * cheap).to_numpy(
            dtype="float64"
        )
    base_c075 = price60 + mv + div + 0.75 * cheap
    for field, _name, mom_w in C075_MOM_BLENDS:
        columns[field] = (base_c075 + mom_w * mom).to_numpy(dtype="float64")
    for field, _name, size_w in C075_SIZE_BLENDS:
        columns[field] = (price60 + size_w * mv + div + 0.75 * cheap).to_numpy(dtype="float64")
    for field, _name, price_w in C075_PRICE_BLENDS:
        columns[field] = (price_w * price60 + mv + div + 0.75 * cheap).to_numpy(dtype="float64")
    for field, _name, cheap_w in D075_CHEAP_BLENDS:
        columns[field] = (price60 + mv + 0.75 * div + cheap_w * cheap).to_numpy(dtype="float64")
    for field, _name, size_w in C150_SIZE_BLENDS:
        columns[field] = (price60 + size_w * mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    for field, _name, div_w in C150_DIV_BLENDS:
        columns[field] = (price60 + mv + div_w * div + 1.5 * cheap).to_numpy(dtype="float64")
    for field, _name, quiet_w in C150_QUIET_BLENDS:
        columns[field] = (price60 + mv + div + 1.5 * cheap + quiet_w * quiet).to_numpy(dtype="float64")
    for field, _name, price_w in C150_PRICE_BLENDS:
        columns[field] = (price_w * price60 + mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    base_c150 = price60 + mv + div + 1.5 * cheap
    for field, _name, weight in C150_RANGE_BLENDS:
        columns[field] = (base_c150 + weight * (1.0 - rng)).to_numpy(dtype="float64")
    for field, _name, weight in C150_AMOUNT_BLENDS:
        columns[field] = (base_c150 + weight * amt).to_numpy(dtype="float64")
    for field, _name, weight in C150_OVERNIGHT_BLENDS:
        columns[field] = (base_c150 + weight * overnight).to_numpy(dtype="float64")
    for field, _name, weight in C150_LOCATION_BLENDS:
        columns[field] = (base_c150 + weight * loc).to_numpy(dtype="float64")
    for field, _name, shape in C150_SHAPE_BLENDS:
        price_shape = zscore60 if shape == "zscore" else bias60_rank
        columns[field] = (price_shape + mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    columns[C150_P20[0]] = (price + mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    columns[C150_FLOAT[0]] = (price60 + float_mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    for field, _name, weight in C150_AZ_BLENDS:
        columns[field] = (base_c150 + weight * amt_z).to_numpy(dtype="float64")
    codes = frame[code_key].astype(str)
    for field, _name, kind in C150_BOARD_BLENDS:
        masked = base_c150.where(~_board_drop_mask(codes, kind))
        columns[field] = masked.to_numpy(dtype="float64")
    columns[C150_P000[0]] = (mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    columns[C150_S000[0]] = (price60 + div + 1.5 * cheap).to_numpy(dtype="float64")
    columns[C150_D075[0]] = (price60 + mv + 0.75 * div + 1.5 * cheap).to_numpy(dtype="float64")
    columns[C150_P120[0]] = (price120 + mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    columns[C150_P252[0]] = (price252 + mv + div + 1.5 * cheap).to_numpy(dtype="float64")
    for field, _name, weight in C150_HV_BLENDS:
        columns[field] = (base_c150 + weight * vol).to_numpy(dtype="float64")
    columns[C150_AZPOS[0]] = base_c150.where(amt_z_raw > 0).to_numpy(dtype="float64")
    ma_bias = {"bias20": bias20_raw, "bias60": bias60_raw}
    for field, _name, kind in C150_MA_BLENDS:
        columns[field] = base_c150.where(ma_bias[kind] > 0).to_numpy(dtype="float64")
    for field, _name, weight in C150_REV_MOM:
        columns[field] = (base_c150 + weight * (1.0 - mom)).to_numpy(dtype="float64")
    for field, _name, weight in C150_FADE_GAP:
        columns[field] = (base_c150 + weight * (1.0 - overnight)).to_numpy(dtype="float64")
    for field, _name, weight in C150_TREND_BLENDS:
        columns[field] = (base_c150 + weight * bias60_rank).to_numpy(dtype="float64")
    columns[C150_INST[0]] = (base_c150 + 0.25 * vol + 0.25 * quiet).to_numpy(dtype="float64")
    columns[C150_RM60[0]] = (base_c150 + 0.25 * (1.0 - mom60)).to_numpy(dtype="float64")
    columns[C150_LOWLOC[0]] = (base_c150 + 0.25 * (1.0 - loc)).to_numpy(dtype="float64")
    weight_dir = Path(index_weight_dir) if index_weight_dir else _default_index_weight_dir(sidecar)
    in_300 = _asof_member_mask(codes, dates, weight_dir / "index_weight_000300_SH.parquet")
    in_500 = _asof_member_mask(codes, dates, weight_dir / "index_weight_000905_SH.parquet")
    if in_300 is None and in_500 is None:
        in_800 = None
    elif in_300 is None:
        in_800 = in_500.fillna(False)
    elif in_500 is None:
        in_800 = in_300.fillna(False)
    else:
        in_800 = in_300.fillna(False) | in_500.fillna(False)
    universe_masks = {"hs300": in_300, "csi800": in_800}
    for field, _name, kind in C150_UNIVERSE:
        mask = universe_masks[kind]
        if mask is None:
            columns[field] = base_c150.to_numpy(dtype="float64")
        else:
            columns[field] = base_c150.where(mask.astype(bool)).to_numpy(dtype="float64")
    csi800_base = pd.Series(columns[C150_UNIVERSE[1][0]], index=codes.index)
    if in_500 is None:
        columns[C150_CSI500[0]] = base_c150.to_numpy(dtype="float64")
    else:
        columns[C150_CSI500[0]] = base_c150.where(in_500.astype(bool)).to_numpy(dtype="float64")
    w300 = _asof_index_weight(codes, dates, weight_dir / "index_weight_000300_SH.parquet")
    w500 = _asof_index_weight(codes, dates, weight_dir / "index_weight_000905_SH.parquet")
    if w300 is None and w500 is None:
        index_w = None
    elif w300 is None:
        index_w = w500
    elif w500 is None:
        index_w = w300
    else:
        index_w = w300.fillna(w500)
    if index_w is None or in_800 is None:
        for field, _name, _weight in C150_CSI800_W:
            columns[field] = csi800_base.to_numpy(dtype="float64")
        columns[C150_CSI800_WINV[0]] = csi800_base.to_numpy(dtype="float64")
    else:
        weight_rank = _cs_rank(index_w.where(in_800.astype(bool)), dates)
        for field, _name, weight in C150_CSI800_W:
            columns[field] = (csi800_base + weight * weight_rank).to_numpy(dtype="float64")
        columns[C150_CSI800_WINV[0]] = (csi800_base + 0.25 * (1.0 - weight_rank)).to_numpy(
            dtype="float64"
        )
    prev_800 = _asof_prev_member_mask(
        codes,
        dates,
        weight_dir / "index_weight_000300_SH.parquet",
        weight_dir / "index_weight_000905_SH.parquet",
    )
    if in_800 is None or prev_800 is None:
        columns[C150_CSI800_STABLE[0]] = csi800_base.to_numpy(dtype="float64")
    else:
        columns[C150_CSI800_STABLE[0]] = csi800_base.where(
            in_800.astype(bool) & prev_800.astype(bool)
        ).to_numpy(dtype="float64")
    if in_800 is None or in_300 is None or in_500 is None:
        columns[C150_CSI800_S5[0]] = csi800_base.to_numpy(dtype="float64")
    else:
        only_500 = in_500.astype(bool) & ~in_300.astype(bool)
        columns[C150_CSI800_S5[0]] = (csi800_base + 0.25 * only_500.astype("float64")).to_numpy(
            dtype="float64"
        )
    if in_800 is None:
        for field in C150_CSI800_POOL_FIELDS:
            columns[field] = base_c150.to_numpy(dtype="float64")
    else:
        in_800_bool = in_800.astype(bool)
        cheap_800 = 1.0 - _cs_rank_where(frame["pe_ttm_cs_rank"], dates, in_800_bool)
        columns[C150_CSPE[0]] = (price60 + mv + div + 1.5 * cheap_800).where(in_800_bool).to_numpy(
            dtype="float64"
        )
        st_base = pd.Series(columns[C150_CSI800_STABLE[0]], index=codes.index)
        if prev_800 is None:
            for field in C150_CSI800_POOL_FIELDS:
                if field != C150_CSPE[0]:
                    columns[field] = base_c150.to_numpy(dtype="float64")
        else:
            st_mask = in_800_bool & prev_800.astype(bool)
            cheap_st = 1.0 - _cs_rank_where(frame["pe_ttm_cs_rank"], dates, st_mask)
            div_st = _cs_rank_where(frame["div_yield_cs_rank"], dates, st_mask)
            mv_st = _cs_rank_where(frame["total_mv_cs_rank"], dates, st_mask)
            columns[C150_ST_CSPE[0]] = (price60 + mv + div + 1.5 * cheap_st).where(st_mask).to_numpy(
                dtype="float64"
            )
            columns[C150_ST_CSDV[0]] = (price60 + mv + div_st + 1.5 * cheap_st).where(st_mask).to_numpy(
                dtype="float64"
            )
            columns[C150_ST_CSALL[0]] = (
                (price60 + mv_st + div_st + 1.5 * cheap_st).where(st_mask).to_numpy(dtype="float64")
            )
            columns[C150_ST_S000[0]] = (price60 + div + 1.5 * cheap).where(st_mask).to_numpy(
                dtype="float64"
            )
            columns[C150_ST_CSPE0[0]] = (price60 + div + 1.5 * cheap_st).where(st_mask).to_numpy(
                dtype="float64"
            )
            if index_w is None:
                columns[C150_ST_W005[0]] = st_base.to_numpy(dtype="float64")
                columns[C150_ST_W010[0]] = st_base.to_numpy(dtype="float64")
                for field, _name, _thr in C150_ST_W_NEW:
                    columns[field] = st_base.to_numpy(dtype="float64")
                columns[C150_ST_W2[0]] = st_base.to_numpy(dtype="float64")
                for field, _name, _q in C150_ST_WP:
                    columns[field] = st_base.to_numpy(dtype="float64")
                columns[C150_ST_A510[0]] = st_base.to_numpy(dtype="float64")
                columns[C150_ST_A605[0]] = st_base.to_numpy(dtype="float64")
                for field, _name, _thr300, _thr500 in C150_ST_A_LADDER:
                    columns[field] = st_base.to_numpy(dtype="float64")
                for field, _name, _thr300, _thr500 in C150_ST_A_W2:
                    columns[field] = st_base.to_numpy(dtype="float64")
                for field, _name, _q in C150_ST_HQ:
                    columns[field] = st_base.to_numpy(dtype="float64")
            else:
                weight = pd.to_numeric(index_w, errors="coerce")
                floors: tuple[tuple[str, float], ...] = (
                    (C150_ST_W005[0], 0.05),
                    (C150_ST_W010[0], 0.10),
                    *((field, thr) for field, _name, thr in C150_ST_W_NEW),
                )
                for field, thr in floors:
                    columns[field] = st_base.where(weight >= thr).to_numpy(dtype="float64")
                prev_w = _asof_prev_index_weight(
                    codes,
                    dates,
                    weight_dir / "index_weight_000300_SH.parquet",
                    weight_dir / "index_weight_000905_SH.parquet",
                )
                if prev_w is None:
                    prev_weight = None
                    columns[C150_ST_W2[0]] = st_base.where(weight >= 0.05).to_numpy(dtype="float64")
                else:
                    prev_weight = pd.to_numeric(prev_w, errors="coerce")
                    columns[C150_ST_W2[0]] = st_base.where(
                        (weight >= 0.05) & (prev_weight >= 0.05)
                    ).to_numpy(dtype="float64")
                for field, _name, q in C150_ST_WP:
                    cut = _cs_quantile_where(weight, dates, st_mask, q)
                    columns[field] = st_base.where(weight >= cut).to_numpy(dtype="float64")
                asymm: tuple[tuple[str, float, float], ...] = (
                    (C150_ST_A510[0], 0.05, 0.10),
                    (C150_ST_A605[0], 0.06, 0.05),
                    *((field, thr300, thr500) for field, _name, thr300, thr500 in C150_ST_A_LADDER),
                )
                if in_300 is None:
                    for field, thr300, _thr500 in asymm:
                        columns[field] = st_base.where(weight >= thr300).to_numpy(dtype="float64")
                    for field, _name, thr300, _thr500 in C150_ST_A_W2:
                        current = weight >= thr300
                        if prev_weight is None:
                            columns[field] = st_base.where(current).to_numpy(dtype="float64")
                        else:
                            columns[field] = st_base.where(
                                current & (prev_weight >= 0.05)
                            ).to_numpy(dtype="float64")
                    for field, _name, q in C150_ST_HQ:
                        cut = _cs_quantile_where(weight, dates, st_mask, q)
                        columns[field] = st_base.where(weight >= cut).to_numpy(dtype="float64")
                else:
                    hs300 = in_300.astype(bool)
                    for field, thr300, thr500 in asymm:
                        columns[field] = st_base.where(
                            (hs300 & (weight >= thr300)) | (~hs300 & (weight >= thr500))
                        ).to_numpy(dtype="float64")
                    for field, _name, thr300, thr500 in C150_ST_A_W2:
                        current = (hs300 & (weight >= thr300)) | (~hs300 & (weight >= thr500))
                        if prev_weight is None:
                            columns[field] = st_base.where(current).to_numpy(dtype="float64")
                        else:
                            columns[field] = st_base.where(
                                current & (prev_weight >= 0.05)
                            ).to_numpy(dtype="float64")
                    for field, _name, q in C150_ST_HQ:
                        cut = _cs_quantile_where(weight, dates, hs300, q)
                        columns[field] = st_base.where(
                            (hs300 & (weight >= cut)) | (~hs300 & (weight >= 0.05))
                        ).to_numpy(dtype="float64")
            a605w2 = pd.Series(columns[C150_ST_A_W2[0][0]], index=codes.index)
            mom60 = pd.to_numeric(frame["momentum_60"], errors="coerce")
            rng = pd.to_numeric(frame["intraday_range"], errors="coerce")
            turn = pd.to_numeric(frame["turn_cs_rank"], errors="coerce")
            loc = pd.to_numeric(frame["close_location"], errors="coerce")
            columns[C150_ST_W2M0[0]] = a605w2.where(mom60 > 0).to_numpy(dtype="float64")
            for field, _name, q in C150_ST_W2M:
                cut = _cs_quantile_where(mom60, dates, st_mask, q)
                columns[field] = a605w2.where(mom60 >= cut).to_numpy(dtype="float64")
            for field, _name, q in C150_ST_W2R:
                cut = _cs_quantile_where(rng, dates, st_mask, q)
                columns[field] = a605w2.where(rng >= cut).to_numpy(dtype="float64")
            for field, _name, q in C150_ST_W2T:
                cut = _cs_quantile_where(turn, dates, st_mask, q)
                columns[field] = a605w2.where(turn >= cut).to_numpy(dtype="float64")
            columns[C150_ST_W2L80[0]] = a605w2.where(loc <= 0.8).to_numpy(dtype="float64")
            columns[C150_ST_W2L90[0]] = a605w2.where(loc <= 0.9).to_numpy(dtype="float64")
            loc_cut = _cs_quantile_where(loc, dates, st_mask, 0.50)
            columns[C150_ST_W2L50[0]] = a605w2.where(loc <= loc_cut).to_numpy(dtype="float64")
            mom10 = pd.to_numeric(frame["momentum_10"], errors="coerce")
            div_raw = pd.to_numeric(frame["div_yield_cs_rank"], errors="coerce")
            amt_z_raw = pd.to_numeric(frame["amount_zscore_20"], errors="coerce")
            overnight_raw = pd.to_numeric(frame["overnight_ret"], errors="coerce")
            turn_st = _cs_rank_where(turn, dates, st_mask)
            pe_raw = pd.to_numeric(frame["pe_ttm_cs_rank"], errors="coerce")
            price_raw = pd.to_numeric(frame["close_ts_rank_60"], errors="coerce")
            size_raw = pd.to_numeric(frame["total_mv_cs_rank"], errors="coerce")
            div_st = _cs_rank_where(div_raw, dates, st_mask)
            size_st = _cs_rank_where(size_raw, dates, st_mask)
            tilt_src = {
                "turn": turn_st,
                "range": _cs_rank_where(rng, dates, st_mask),
                "lowloc": 1.0 - _cs_rank_where(loc, dates, st_mask),
                "rev10": 1.0 - _cs_rank_where(mom10, dates, st_mask),
                "fadediv": 1.0 - div_st,
                "amtz": _cs_rank_where(amt_z_raw, dates, st_mask),
                "mom60": _cs_rank_where(mom60, dates, st_mask),
                "quiet": 1.0 - turn_st,
                "fadegap": 1.0 - _cs_rank_where(overnight_raw, dates, st_mask),
                "idxw": (
                    _cs_rank_where(weight, dates, st_mask)
                    if index_w is not None
                    else pd.Series(0.0, index=a605w2.index)
                ),
                "div": div_st,
                "cheap": 1.0 - _cs_rank_where(pe_raw, dates, st_mask),
                "price": _cs_rank_where(price_raw, dates, st_mask),
                "size": size_st,
                "fadesize": 1.0 - size_st,
            }
            for field, _name, kind, tilt_w in C150_ST_W2_TILT:
                columns[field] = (a605w2 + tilt_w * tilt_src[kind]).to_numpy(dtype="float64")
            for field, _name, kind, tilt_w in C150_ST_W2_MICRO:
                columns[field] = (a605w2 + tilt_w * tilt_src[kind]).to_numpy(dtype="float64")
            for field, _name, kind, tilt_w in C150_ST_W2_LEG:
                columns[field] = (a605w2 + tilt_w * tilt_src[kind]).to_numpy(dtype="float64")
            for field, _name, kind, tilt_w in C150_ST_W2_PEAK:
                columns[field] = (a605w2 + tilt_w * tilt_src[kind]).to_numpy(dtype="float64")
            dv03 = a605w2 + 0.03 * tilt_src["fadediv"]
            for field, _name, kind, tilt_w in C150_ST_DV03_TILT:
                columns[field] = (dv03 + tilt_w * tilt_src[kind]).to_numpy(dtype="float64")
            csrank_src = {
                "fadevolcs": 1.0 - pd.to_numeric(frame["vol_mean_20_cs_rank"], errors="coerce"),
                "mom10cs": pd.to_numeric(frame["momentum_10_cs_rank"], errors="coerce"),
                "fadebiascs": 1.0 - pd.to_numeric(frame["close_bias_20_cs_rank"], errors="coerce"),
            }
            for field, _name, kind, tilt_w in C150_ST_DV03_CSRANK:
                columns[field] = (dv03 + tilt_w * csrank_src[kind]).to_numpy(dtype="float64")
            qt005 = dv03 + 0.005 * tilt_src["quiet"]
            for field, _name, kind, tilt_w in C150_ST_QT005_TILT:
                columns[field] = (qt005 + tilt_w * tilt_src[kind]).to_numpy(dtype="float64")
            fadediv = tilt_src["fadediv"]
            for field, _name, fade_w in C150_ST_FADE_QT005:
                columns[field] = (a605w2 + fade_w * fadediv + 0.005 * tilt_src["quiet"]).to_numpy(dtype="float64")
            div_st = tilt_src["div"]
            for field, _name, mode, param in C150_ST_DV03_SHAPE:
                if mode == "hi":
                    term = fadediv.where(div_st >= param, other=0)
                elif mode == "lo":
                    term = fadediv.where(div_st <= param, other=0)
                elif mode == "clip":
                    term = fadediv.clip(upper=param)
                elif mode == "sq":
                    term = fadediv.clip(lower=0).pow(2)
                elif mode == "sqrt":
                    term = fadediv.clip(lower=0).pow(0.5)
                else:
                    raise ValueError(mode)
                columns[field] = (a605w2 + 0.03 * term).to_numpy(dtype="float64")
            tenure = _consecutive_membership_tenure(
                codes,
                dates,
                weight_dir / "index_weight_000300_SH.parquet",
                weight_dir / "index_weight_000905_SH.parquet",
            )
            if tenure is None:
                for field, _name, _weight in C150_ST_T:
                    columns[field] = st_base.to_numpy(dtype="float64")
            else:
                tenure_rank = _cs_rank_where(tenure, dates, st_mask)
                for field, _name, weight in C150_ST_T:
                    columns[field] = (st_base + weight * tenure_rank).to_numpy(dtype="float64")
    if in_800 is None or prev_800 is None:
        st_rs_mask = pd.Series(False, index=frame.index)
    else:
        st_rs_mask = in_800.astype(bool) & prev_800.astype(bool)
    mom60_raw = pd.to_numeric(frame["momentum_60"], errors="coerce")
    mom120_raw = pd.to_numeric(frame["momentum_120"], errors="coerce")
    columns[RS60_ST[0]] = mom60_raw.where(st_rs_mask).to_numpy(dtype="float64")
    columns[RS120_ST[0]] = mom120_raw.where(st_rs_mask).to_numpy(dtype="float64")
    brk60_raw = pd.to_numeric(frame["close_ts_rank_60"], errors="coerce")
    brk120_raw = pd.to_numeric(frame["close_ts_rank_120"], errors="coerce")
    columns[BRK60_ST[0]] = brk60_raw.where(st_rs_mask).to_numpy(dtype="float64")
    columns[BRK120_ST[0]] = brk120_raw.where(st_rs_mask).to_numpy(dtype="float64")
    qturn_raw = 1.0 - pd.to_numeric(frame["turn_cs_rank"], errors="coerce")
    qvol_raw = 1.0 - _cs_rank(frame["volatility_20"], dates)
    columns[QTURN_ST[0]] = qturn_raw.where(st_rs_mask).to_numpy(dtype="float64")
    columns[QVOL_ST[0]] = qvol_raw.where(st_rs_mask).to_numpy(dtype="float64")
    columns[QMIX_ST[0]] = (qturn_raw + qvol_raw).where(st_rs_mask).to_numpy(dtype="float64")
    table = pa.table(columns)
    output = sidecar.with_name(COMPOSITE_SIDECAR_NAME)
    tmp = output.with_name(output.name + ".next")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(output)
    return {
        "path": _display_output_path(output, sidecar.parent.parent),
        "rows": int(table.num_rows),
        "fields": list(COMPOSITE_FIELDS),
    }


def materialize_canonical_pack_factors(
    canonical_path: Path | str,
    output_path: Path | str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    canonical = Path(canonical_path).expanduser().resolve()
    if not canonical.is_file():
        raise ValueError("找不到 canonical.parquet")
    output = Path(output_path) if output_path else default_sidecar_path(canonical)
    output.parent.mkdir(parents=True, exist_ok=True)
    names = set(pq.ParquetFile(canonical).schema_arrow.names)
    inputs: list[str] = []
    for spec in CANONICAL_FACTOR_PACK:
        parsed = parse_expression(spec.formula, names | {"instrument", "date", "trade_date", "ts_code"})
        for field in parsed.input_fields:
            if field in names and field not in inputs:
                inputs.append(field)
    columns = [name for name in ("ts_code", "trade_date", *inputs) if name in names]
    frame = pq.read_table(canonical, columns=columns).to_pandas()
    if "ts_code" not in frame.columns or "trade_date" not in frame.columns:
        raise ValueError("canonical.parquet 缺少 ts_code/trade_date")
    frame["instrument"] = frame["ts_code"].astype(str)
    frame["date"] = _compact_date(frame["trade_date"])
    frame = frame.sort_values(["instrument", "date"], kind="mergesort").reset_index(drop=True)
    available = set(frame.columns)
    payload: dict[str, Any] = {
        "ts_code": frame["instrument"].to_numpy(),
        "trade_date": frame["date"].to_numpy(),
    }
    total = len(CANONICAL_FACTOR_PACK)
    for index, spec in enumerate(CANONICAL_FACTOR_PACK, start=1):
        if progress is not None:
            progress(spec.field, index, total)
        parsed = parse_expression(spec.formula, available)
        values = finite_factor_values(_eval(parsed.tree, frame))
        payload[spec.field] = pd.to_numeric(values, errors="coerce").to_numpy(dtype="float64")
    table = pa.table(payload)
    tmp = output.with_name(output.name + ".next")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(output)
    return {
        "path": _display_output_path(output, canonical.parent),
        "rows": int(table.num_rows),
        "fields": list(PACK_FIELDS),
    }


def append_missing_canonical_pack_factors(
    canonical_path: Path | str,
    output_path: Path | str | None = None,
    fields: list[str] | tuple[str, ...] | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    specs: tuple[Any, ...] | list[Any] | None = None,
) -> dict[str, Any]:
    canonical = Path(canonical_path).expanduser().resolve()
    if not canonical.is_file():
        raise ValueError("找不到 canonical.parquet")
    output = Path(output_path) if output_path else default_sidecar_path(canonical)
    if not output.is_file() and specs is None:
        result = materialize_canonical_pack_factors(canonical, output, progress)
        added = list(fields) if fields else list(PACK_FIELDS)
        return {**result, "added": added}
    if not output.is_file():
        materialize_canonical_pack_factors(canonical, output, progress)
    existing_names = set(pq.ParquetFile(output).schema_arrow.names)
    catalog = list(CANONICAL_FACTOR_PACK if specs is None else specs)
    by_field = {spec.field: spec for spec in catalog}
    if fields is None:
        wanted = [spec for spec in catalog if spec.field not in existing_names]
    else:
        wanted = []
        for name in fields:
            spec = by_field.get(str(name).strip())
            if spec is None:
                raise ValueError(f"unknown pack field: {name}")
            if spec.field not in existing_names:
                wanted.append(spec)
    if not wanted:
        return {
            "path": _display_output_path(output, canonical.parent),
            "rows": int(pq.ParquetFile(output).metadata.num_rows),
            "fields": [name for name in pq.ParquetFile(output).schema_arrow.names],
            "added": [],
        }
    names = set(pq.ParquetFile(canonical).schema_arrow.names)
    inputs: list[str] = []
    for spec in wanted:
        parsed = parse_expression(spec.formula, names | {"instrument", "date", "trade_date", "ts_code"})
        for field in parsed.input_fields:
            if field in names and field not in inputs:
                inputs.append(field)
    columns = [name for name in ("ts_code", "trade_date", *inputs) if name in names]
    frame = pq.read_table(canonical, columns=columns).to_pandas()
    if "ts_code" not in frame.columns or "trade_date" not in frame.columns:
        raise ValueError("canonical.parquet 缺少 ts_code/trade_date")
    frame["instrument"] = frame["ts_code"].astype(str)
    frame["date"] = _compact_date(frame["trade_date"])
    frame = frame.sort_values(["instrument", "date"], kind="mergesort").reset_index(drop=True)
    available = set(frame.columns)
    extra: dict[str, Any] = {
        "_code": frame["instrument"].to_numpy(),
        "_date": frame["date"].to_numpy(),
    }
    total = len(wanted)
    for index, spec in enumerate(wanted, start=1):
        if progress is not None:
            progress(spec.field, index, total)
        parsed = parse_expression(spec.formula, available)
        values = finite_factor_values(_eval(parsed.tree, frame))
        extra[spec.field] = pd.to_numeric(values, errors="coerce").to_numpy(dtype="float64")
    computed = pd.DataFrame(extra)
    current = pq.read_table(output).to_pandas()
    code_key = "ts_code" if "ts_code" in current.columns else "instrument"
    date_key = "trade_date" if "trade_date" in current.columns else "date"
    current["_code"] = current[code_key].astype(str)
    current["_date"] = _compact_date(current[date_key])
    merged = current.merge(computed, on=["_code", "_date"], how="left")
    merged = merged.drop(columns=["_code", "_date"])
    table = pa.table(merged)
    tmp = output.with_name(output.name + ".next")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(output)
    return {
        "path": _display_output_path(output, canonical.parent),
        "rows": int(table.num_rows),
        "fields": list(table.schema.names),
        "added": [spec.field for spec in wanted],
    }


def append_alpha191_factors(
    canonical_path: Path | str,
    output_path: Path | str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    return append_missing_canonical_pack_factors(
        canonical_path,
        output_path,
        progress=progress,
        specs=alpha191_pack_specs(),
    )
