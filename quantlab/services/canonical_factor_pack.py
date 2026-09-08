"""Canonical wide-table factor pack: compute, verify, and publish."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.repositories.factors import FactorRepository
from quantlab.services.factor_calculation import FactorCalculationService
from quantlab.services.factor_manual import ExpressionError, parse_expression


DEFAULT_DATASET_ID = "ds_canonical_market"
DEFAULT_DATASET_VERSION_ID = "current"
PACK_MARKETS = ("SH", "SZ")
MIN_EFFECTIVE_DAYS = 5
DEFAULT_COVERAGE_FLOOR = 0.95
VALUATION_COVERAGE_FLOOR = 0.60


@dataclass(frozen=True)
class PackFactor:
    field: str
    name: str
    formula: str
    direction: str
    category: str
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR
    min_effective_days: int = MIN_EFFECTIVE_DAYS


def pack_entity_id(field: str) -> str:
    return f"factor_{field}"


def last_year_window(date_max: str | None) -> tuple[str, str]:
    text = str(date_max or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError("dataset date_max is required")
    return f"{text[:4]}0101", text


def verify_pack_run(summary: dict[str, Any], spec: PackFactor) -> tuple[bool, str]:
    if summary.get("error"):
        return False, str(summary["error"])
    if str(summary.get("status") or "") == "failed":
        return False, str(summary.get("error_message") or "calculation failed")
    days = summary.get("effective_days")
    if days is None or int(days) < spec.min_effective_days:
        return False, f"effective_days {days} < {spec.min_effective_days}"
    coverage = summary.get("coverage")
    if coverage is None or float(coverage) < spec.coverage_floor:
        return False, f"coverage {coverage} < {spec.coverage_floor}"
    return True, "passed"


def _spec(
    field: str,
    name: str,
    formula: str,
    direction: str,
    category: str,
    coverage_floor: float = DEFAULT_COVERAGE_FLOOR,
) -> PackFactor:
    return PackFactor(
        field=field,
        name=name,
        formula=formula,
        direction=direction,
        category=category,
        coverage_floor=coverage_floor,
    )


CANONICAL_FACTOR_PACK: tuple[PackFactor, ...] = (
    _spec("momentum_1", "1 日涨跌幅", "hfq_close.pct_change(1)", "negative", "动量"),
    _spec("momentum_10", "10 日动量", "hfq_close.pct_change(10)", "positive", "动量"),
    _spec("momentum_20", "20 日动量", "hfq_close.pct_change(20)", "positive", "动量"),
    _spec("momentum_60", "60 日动量", "hfq_close.pct_change(60)", "positive", "动量"),
    _spec("close_bias_20", "20 日均线偏离", "hfq_close.rolling_bias(20)", "positive", "技术"),
    _spec("close_bias_60", "60 日均线偏离", "hfq_close.rolling_bias(60)", "positive", "技术"),
    _spec("close_zscore_20", "20 日价格标准化", "hfq_close.ts_zscore(20)", "positive", "技术"),
    _spec("close_zscore_60", "60 日价格标准化", "hfq_close.ts_zscore(60)", "positive", "技术"),
    _spec("close_ts_rank_20", "20 日价格分位", "hfq_close.ts_rank(20)", "positive", "技术"),
    _spec("close_ts_rank_60", "60 日价格分位", "hfq_close.ts_rank(60)", "positive", "技术"),
    _spec("amount_zscore_20", "20 日成交额标准化", "amount.ts_zscore(20)", "positive", "换手"),
    _spec("vol_mean_20", "20 日均量", "vol.rolling_mean(20)", "positive", "换手"),
    _spec("amount_cs_rank", "成交额截面排名", "amount.cs_rank(0)", "positive", "换手"),
    _spec("turn_cs_rank", "换手率截面排名", "turn.cs_rank(0)", "positive", "换手"),
    _spec("pe_ttm_cs_rank", "市盈率截面排名", "pe_ttm.cs_rank(0)", "negative", "估值", VALUATION_COVERAGE_FLOOR),
    _spec("float_mv_cs_rank", "流通市值截面排名", "float_market_cap.cs_rank(0)", "negative", "规模"),
    _spec("total_mv_cs_rank", "总市值截面排名", "total_market_cap.cs_rank(0)", "negative", "规模"),
    _spec(
        "div_yield_cs_rank",
        "股息率截面排名",
        "dividend_yield_ratio.cs_rank(0)",
        "positive",
        "估值",
        VALUATION_COVERAGE_FLOOR,
    ),
    _spec("intraday_range", "日振幅", "(hfq_high - hfq_low) / hfq_close", "positive", "技术"),
    _spec("overnight_ret", "隔夜收益", "hfq_open / hfq_close.shift(1) - 1", "positive", "动量"),
    _spec(
        "close_location",
        "收盘位置",
        "(hfq_close - hfq_low) / (hfq_high - hfq_low)",
        "positive",
        "技术",
    ),
    _spec("trade_vwap", "成交均价", "amount / vol", "positive", "换手"),
)

_PACK_BY_FIELD = {item.field: item for item in CANONICAL_FACTOR_PACK}


class CanonicalFactorPackService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        factors: FactorRepository,
        calculator: FactorCalculationService | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.factors = factors
        self.calculator = calculator or FactorCalculationService(settings, database)

    def catalog(self) -> dict[str, Any]:
        items = []
        for spec in CANONICAL_FACTOR_PACK:
            entity_id = pack_entity_id(spec.field)
            record = self.factors.get(entity_id, "v1")
            items.append(
                {
                    **asdict(spec),
                    "entity_id": entity_id,
                    "present": record is not None,
                    "status": None if record is None else record["status"],
                    "quality_status": None if record is None else record["quality_status"],
                }
            )
        return {"items": items, "count": len(items)}

    def ingest(
        self,
        *,
        dataset_id: str = DEFAULT_DATASET_ID,
        dataset_version_id: str = DEFAULT_DATASET_VERSION_ID,
        field: str | None = None,
    ) -> dict[str, Any]:
        if field:
            item = self.ingest_one(
                field,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
            )
            return {"items": [item], **_counts([item])}
        items = [
            self.ingest_one(
                spec.field,
                dataset_id=dataset_id,
                dataset_version_id=dataset_version_id,
            )
            for spec in CANONICAL_FACTOR_PACK
        ]
        return {"items": items, **_counts(items)}

    def ingest_one(
        self,
        field: str,
        *,
        dataset_id: str = DEFAULT_DATASET_ID,
        dataset_version_id: str = DEFAULT_DATASET_VERSION_ID,
    ) -> dict[str, Any]:
        spec = _PACK_BY_FIELD.get(str(field or "").strip())
        if spec is None:
            raise ValueError(f"unknown pack field: {field}")
        entity_id = pack_entity_id(spec.field)
        existing = self.factors.get(entity_id, "v1")
        if existing is not None and existing["status"] == "published":
            return {
                "field": spec.field,
                "entity_id": entity_id,
                "status": "skipped",
                "reason": "already_published",
            }
        if existing is not None and existing["status"] not in {"draft", "validated"}:
            return {
                "field": spec.field,
                "entity_id": entity_id,
                "status": "skipped",
                "reason": f"status_{existing['status']}",
            }
        bounds = self._published_dataset(dataset_id, dataset_version_id)
        try:
            parsed = parse_expression(spec.formula, set(bounds["fields"]))
        except ExpressionError as error:
            return {
                "field": spec.field,
                "entity_id": entity_id,
                "status": "failed",
                "reason": str(error),
            }
        if existing is None:
            saved = self.factors.import_definition(
                _definition(spec, dataset_id, dataset_version_id, parsed.input_fields)
            )
        else:
            saved = existing
        date_from, date_to = last_year_window(bounds["date_max"])
        try:
            calculation = self.calculator.run(
                entity_id,
                version_id="v1",
                date_from=date_from,
                date_to=date_to,
                markets=list(PACK_MARKETS),
            )
        except (ValueError, ExpressionError) as error:
            return {
                "field": spec.field,
                "entity_id": entity_id,
                "status": "failed",
                "reason": str(error),
                "date_from": date_from,
                "date_to": date_to,
            }
        ok, reason = verify_pack_run(calculation, spec)
        quality = {
            "pack_verified": True,
            "coverage": calculation.get("coverage"),
            "ic_mean": calculation.get("ic_mean"),
            "effective_days": calculation.get("effective_days"),
            "date_from": date_from,
            "date_to": date_to,
            "reason": reason,
        }
        if saved["status"] == "draft":
            self.factors.update_draft(
                entity_id,
                "v1",
                {
                    "quality_status": "passed" if ok else "failed",
                    "quality": quality,
                },
            )
        if not ok:
            return {
                "field": spec.field,
                "entity_id": entity_id,
                "status": "failed",
                "reason": reason,
                "date_from": date_from,
                "date_to": date_to,
                "coverage": calculation.get("coverage"),
                "ic_mean": calculation.get("ic_mean"),
                "calculation_id": calculation.get("calculation_id"),
            }
        if saved["status"] != "published":
            published = self.factors.publish_verified_pack(entity_id, "v1")
        else:
            published = saved
        return {
            "field": spec.field,
            "entity_id": published["entity_id"],
            "status": "published",
            "reason": reason,
            "date_from": date_from,
            "date_to": date_to,
            "coverage": calculation.get("coverage"),
            "ic_mean": calculation.get("ic_mean"),
            "calculation_id": calculation.get("calculation_id"),
        }

    def _published_dataset(self, dataset_id: str, version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT dv.fields_json, dv.date_max, dv.status, d.status AS dataset_status "
                "FROM dataset_versions dv JOIN datasets d ON d.entity_id=dv.entity_id "
                "WHERE dv.entity_id=? AND dv.version_id=?",
                (dataset_id, version_id),
            ).fetchone()
        if (
            row is None
            or row["dataset_status"] != "published"
            or row["status"] != "published"
        ):
            raise ValueError("published dataset version is required")
        fields = json.loads(row["fields_json"] or "[]")
        return {"fields": list(fields), "date_max": row["date_max"]}


def _definition(
    spec: PackFactor,
    dataset_id: str,
    dataset_version_id: str,
    input_fields: list[str],
) -> dict[str, Any]:
    formula = spec.formula
    return {
        "entity_id": pack_entity_id(spec.field),
        "version_id": "v1",
        "name": spec.name,
        "category": spec.category,
        "dataset_id": dataset_id,
        "dataset_version_id": dataset_version_id,
        "formula": formula,
        "input_fields": input_fields,
        "source": "canonical_expression",
        "direction": spec.direction,
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
        "author": "canonical_factor_pack",
        "code_hash": "sha256:" + hashlib.sha256(formula.encode()).hexdigest(),
        "quality_status": "needs_review",
        "quality": {"pack": True},
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "published_count": sum(1 for item in items if item.get("status") == "published"),
        "skipped_count": sum(1 for item in items if item.get("status") == "skipped"),
        "failed_count": sum(1 for item in items if item.get("status") == "failed"),
    }
