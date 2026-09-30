"""Materialize money-flow ratios from raw/moneyflow onto a sidecar parquet."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from quantlab.config import posix_relative
from quantlab.repositories.factors import FactorRepository
from quantlab.services.canonical_factor_pack import pack_entity_id
from quantlab.services.factor_manual import finite_factor_values
from quantlab.services.index_membership import amount_yuan


MONEYFLOW_SIDECAR_NAME = "moneyflow_pack_factors.parquet"
MONEYFLOW_FIELDS = (
    "mf_net_ratio",
    "mf_net_ratio_cs_rank",
    "mf_net_ratio_5",
    "mf_net_ratio_5_cs_rank",
)
MONEYFLOW_NAMES = {
    "mf_net_ratio": "净流入占比",
    "mf_net_ratio_cs_rank": "净流入占比截面排名",
    "mf_net_ratio_5": "5 日累计净流入占比",
    "mf_net_ratio_5_cs_rank": "5 日累计净流入占比截面排名",
}
MONEYFLOW_FORMULAS = {
    "mf_net_ratio": "net_mf_amount / (amount_yuan / 10000)",
    "mf_net_ratio_cs_rank": "(net_mf_amount / (amount_yuan / 10000)).cs_rank(0)",
    "mf_net_ratio_5": "net_mf_amount.rolling_sum(5) / amount_yuan.rolling_sum(5) * 10000",
    "mf_net_ratio_5_cs_rank": "(net_mf_amount.rolling_sum(5) / amount_yuan.rolling_sum(5) * 10000).cs_rank(0)",
}


def default_moneyflow_sidecar_path(canonical_path: Path | str) -> Path:
    return Path(canonical_path).expanduser().resolve().parent / "derived" / MONEYFLOW_SIDECAR_NAME


def _compact_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace("-", "", regex=False).str.replace(".", "", regex=False).str[:8]


def _cs_rank(values: pd.Series, dates: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    return numeric.groupby(dates, sort=False).rank(pct=True)


def _load_moneyflow_net(raw_root: Path | str) -> pd.DataFrame:
    path = Path(raw_root) / "moneyflow"
    if not path.is_dir():
        raise ValueError("缺少个股资金流向 raw/moneyflow，请先在数据中心下载。")
    dataset = ds.dataset(path, format="parquet")
    names = set(dataset.schema.names)
    missing = [name for name in ("ts_code", "trade_date", "net_mf_amount") if name not in names]
    if missing:
        raise ValueError(f"raw/moneyflow 缺少字段 {', '.join(missing)}")
    frame = dataset.to_table(columns=["ts_code", "trade_date", "net_mf_amount"]).to_pandas()
    if frame.empty:
        raise ValueError("raw/moneyflow 没有可用行")
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["trade_date"] = _compact_date(frame["trade_date"])
    frame["net_mf_amount"] = pd.to_numeric(frame["net_mf_amount"], errors="coerce")
    return frame.drop_duplicates(["ts_code", "trade_date"], keep="last")


def materialize_moneyflow_factors(
    canonical_path: Path | str,
    raw_root: Path | str,
    output_path: Path | str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    canonical = Path(canonical_path).expanduser().resolve()
    if not canonical.is_file():
        raise ValueError("找不到 canonical.parquet")
    names = set(pq.ParquetFile(canonical).schema_arrow.names)
    needed = [name for name in ("ts_code", "trade_date", "amount") if name in names]
    if len(needed) < 3:
        raise ValueError("canonical.parquet 缺少 ts_code/trade_date/amount")
    if progress is not None:
        progress("moneyflow", 1, 2)
    market = pq.read_table(canonical, columns=needed).to_pandas()
    market["ts_code"] = market["ts_code"].astype(str)
    market["trade_date"] = _compact_date(market["trade_date"])
    flow = _load_moneyflow_net(raw_root)
    merged = market.merge(flow, on=["ts_code", "trade_date"], how="left")
    order = merged.sort_values(["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    amount_wan = amount_yuan(order["amount"]) / 10_000.0
    amount_wan = pd.to_numeric(amount_wan, errors="coerce").where(lambda values: values != 0)
    net = pd.to_numeric(order["net_mf_amount"], errors="coerce")
    ratio = finite_factor_values(net / amount_wan)
    grouped = order.groupby("ts_code", sort=False)
    net_5 = grouped["net_mf_amount"].transform(
        lambda values: pd.to_numeric(values, errors="coerce").rolling(5, min_periods=5).sum()
    )
    wan_5 = amount_wan.groupby(order["ts_code"], sort=False).transform(
        lambda values: values.rolling(5, min_periods=5).sum()
    )
    ratio_5 = finite_factor_values(net_5 / wan_5.where(lambda values: values != 0))
    dates = order["trade_date"]
    if progress is not None:
        progress("ranks", 2, 2)
    payload = {
        "ts_code": order["ts_code"].to_numpy(),
        "trade_date": dates.to_numpy(),
        "mf_net_ratio": pd.to_numeric(ratio, errors="coerce").to_numpy(dtype="float64"),
        "mf_net_ratio_cs_rank": pd.to_numeric(_cs_rank(ratio, dates), errors="coerce").to_numpy(
            dtype="float64"
        ),
        "mf_net_ratio_5": pd.to_numeric(ratio_5, errors="coerce").to_numpy(dtype="float64"),
        "mf_net_ratio_5_cs_rank": pd.to_numeric(_cs_rank(ratio_5, dates), errors="coerce").to_numpy(
            dtype="float64"
        ),
    }
    output = Path(output_path) if output_path else default_moneyflow_sidecar_path(canonical)
    output.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(payload)
    tmp = output.with_name(output.name + ".next")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(output)
    return {
        "path": posix_relative(output, canonical.parent),
        "rows": int(table.num_rows),
        "fields": list(MONEYFLOW_FIELDS),
    }


def _definition(
    field: str,
    *,
    dataset_id: str,
    dataset_version_id: str,
) -> dict[str, Any]:
    formula = MONEYFLOW_FORMULAS[field]
    return {
        "entity_id": pack_entity_id(field),
        "version_id": "v1",
        "name": MONEYFLOW_NAMES[field],
        "category": "资金流向",
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "formula": formula,
        "input_fields": ["amount"],
        "source": "moneyflow",
        "direction": "positive",
        "frequency": "daily",
        "missing_policy": "drop",
        "pit_policy": "as-of trade_date",
        "pit_lineage": {
            "rule": "as-of trade_date",
            "snapshot": f"dataset:{dataset_id}:{dataset_version_id}",
            "window_mode": "per_instrument_observation",
        },
        "upstream_factor_versions": [],
        "origin": "import",
        "author": "moneyflow_factors",
        "code_hash": "sha256:" + hashlib.sha256(formula.encode()).hexdigest(),
        "quality_status": "passed",
        "quality": {"moneyflow": True},
    }


def register_moneyflow_factors(
    factors: FactorRepository,
    *,
    dataset_id: str = "ds_canonical_market",
    dataset_version_id: str = "current",
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for field in MONEYFLOW_FIELDS:
        entity_id = pack_entity_id(field)
        existing = factors.get(entity_id, "v1")
        if existing is not None and existing["status"] == "published":
            items.append(
                {
                    "field": field,
                    "entity_id": entity_id,
                    "status": "skipped",
                    "reason": "already_published",
                }
            )
            continue
        if existing is None:
            saved = factors.import_definition(
                _definition(field, dataset_id=dataset_id, dataset_version_id=dataset_version_id)
            )
        else:
            saved = factors.update_draft(
                entity_id,
                "v1",
                {"quality_status": "passed", "quality": {"moneyflow": True}},
            )
        published = (
            factors.publish_verified_pack(entity_id, "v1")
            if saved["status"] != "published"
            else saved
        )
        items.append(
            {
                "field": field,
                "entity_id": published["entity_id"],
                "status": "published",
            }
        )
    return {
        "items": items,
        "published_count": sum(1 for item in items if item.get("status") == "published"),
        "skipped_count": sum(1 for item in items if item.get("status") == "skipped"),
        "failed_count": sum(1 for item in items if item.get("status") == "failed"),
    }

