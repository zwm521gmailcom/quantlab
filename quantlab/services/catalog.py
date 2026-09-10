"""Read-only registration and lightweight publication of local datasets."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from quantlab.config import Settings
from quantlab.repositories.database import Database


_CONFIG_PATH = Path(__file__).parents[1] / "config/datasets.json"
_HASH_CHUNK_SIZE = 1024 * 1024


def _sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _aggregate_file_fingerprint(root: Path, paths: list[Path]) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    missing: list[str] = []
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        if not path.is_file():
            missing.append(relative)
            digest.update(b"missing")
        else:
            digest.update(_sha256_file(path).encode())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}", missing


def _metadata_hash(path: Path) -> str:
    if path.suffix == ".parquet":
        parquet = pq.ParquetFile(path)
        metadata = parquet.metadata
        payload = {
            "rows": metadata.num_rows,
            "row_groups": metadata.num_row_groups,
            "fields": parquet.schema_arrow.names,
        }
        return _sha256_bytes(json.dumps(payload, sort_keys=True).encode())
    return _sha256_bytes(path.read_bytes())


def _date_stat_value(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parquet_date_range(parquet: pq.ParquetFile) -> tuple[str | None, str | None]:
    names = parquet.schema_arrow.names
    date_column = next((name for name in ("trade_date", "date") if name in names), None)
    if date_column is None:
        return None, None
    column_index = names.index(date_column)
    minimums: list[str] = []
    maximums: list[str] = []
    for row_group_index in range(parquet.metadata.num_row_groups):
        statistics = parquet.metadata.row_group(row_group_index).column(column_index).statistics
        if statistics is None or not statistics.has_min_max:
            continue
        minimums.append(_date_stat_value(statistics.min))
        maximums.append(_date_stat_value(statistics.max))
    if not minimums:
        return None, None
    return min(minimums), max(maximums)


def _parquet_info(path: Path) -> dict[str, Any]:
    parquet = pq.ParquetFile(path)
    metadata = parquet.metadata
    date_min, date_max = _parquet_date_range(parquet)
    return {
        "row_count": metadata.num_rows,
        "fields": parquet.schema_arrow.names,
        "metadata_hash": _metadata_hash(path),
        "date_min": date_min,
        "date_max": date_max,
    }


def snapshot_authoritative_data(settings: Settings, output: Path | str) -> Path:
    """Record fingerprints and Parquet footer metadata, never copy source files."""

    payload = authoritative_data_snapshot(settings)
    target = settings.require_baseline_path(output)
    if target.exists():
        raise FileExistsError(f"baseline already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def authoritative_data_snapshot(settings: Settings) -> dict[str, Any]:
    roots = {"data": settings.data_root, "calibration": settings.calibration_root, "raw": settings.raw_root}
    records: list[dict[str, Any]] = []
    for root_name, root in roots.items():
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            item: dict[str, Any] = {
                "root": root_name,
                "relative_path": relative,
                "size_bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
            if path.name in {"manifest.json", "CURRENT"} or path.suffix == ".json":
                item["content_hash"] = _sha256_bytes(path.read_bytes())
            elif path.suffix == ".parquet":
                item["footer"] = _parquet_info(path)
            records.append(item)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "roots": {name: settings.display_path(root) for name, root in roots.items()},
        "files": records,
    }


def verify_authoritative_data(settings: Settings, baseline: Path | str) -> bool:
    target = settings.require_baseline_path(baseline)
    expected = json.loads(target.read_text(encoding="utf-8"))
    actual = authoritative_data_snapshot(settings)
    expected_files = expected.get("files", [])
    actual_files = actual.get("files", [])
    expected_roots = {
        name: settings.display_path(path)
        for name, path in (expected.get("roots") or {}).items()
    }
    return expected_roots == actual.get("roots") and expected_files == actual_files


class DatasetCatalog:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self._raw_cache: dict[tuple[object, ...], tuple[tuple[object, ...], list[dict[str, Any]]]] = {}

    def _configured(self) -> list[dict[str, Any]]:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))

    def _entry_metadata(self, entry: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(metadata)
        metadata["category"] = entry["category"]
        metadata["path_alias"] = entry["path_alias"]
        return metadata

    def register_configured(self) -> list[dict[str, Any]]:
        registered: list[dict[str, Any]] = []
        for entry in self._configured():
            path = self._resolve_configured_path(entry["path"])
            if not path.exists():
                self._mark_existing_needs_review(entry)
                continue
            try:
                item = self._register_entry(entry, path)
            except FileNotFoundError:
                self._mark_existing_needs_review(entry)
                continue
            registered.append(item)
        return registered

    def _mark_existing_needs_review(self, entry: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE dataset_versions SET quality_status = 'needs_review' "
                "WHERE entity_id = ? AND version_id = ?",
                (entry["entity_id"], entry.get("version_id", "current")),
            )

    def _resolve_configured_path(self, configured_path: str) -> Path:
        relative = Path(configured_path)
        if relative.parts and relative.parts[0] in {
            "tushare_migration_data",
            "tushare_migration_calibration",
        }:
            return (self.settings.data_root / Path(*relative.parts[1:])).resolve()
        return (self.settings.data_root / relative).resolve()

    def _register_entry(self, entry: dict[str, Any], path: Path) -> dict[str, Any]:
        self.settings.require_read_path(path)
        kind = entry["kind"]
        version_id = entry.get("version_id", "current")
        fields: list[str] = []
        row_count: int | None = None
        date_min: str | None = None
        date_max: str | None = None
        manifest_hash: str | None = None
        metadata: dict[str, Any] = self._entry_metadata(entry, {"kind": kind})
        version_path = path
        if kind == "versioned_manifest":
            current_path = path / "CURRENT"
            current = current_path.read_text(encoding="utf-8").strip()
            locked_version = entry["version_id"]
            version_id = locked_version
            version_path = path / "versions" / locked_version
            manifest_path = version_path / "manifest.json"
            if not manifest_path.exists():
                raise FileNotFoundError(f"dataset manifest not found: {self.settings.display_path(manifest_path)}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            partition_names = manifest.get("partitions")
            if isinstance(partition_names, list):
                partition_paths = [version_path / name for name in partition_names]
            else:
                partition_paths = sorted(version_path.glob("year=*/part.parquet"))
            partition_fingerprint, missing_partitions = _aggregate_file_fingerprint(
                version_path, partition_paths
            )
            locked_manifest_hash = _sha256_file(manifest_path)
            content_fingerprint = _sha256_bytes(
                f"{locked_manifest_hash}\0{partition_fingerprint}".encode()
            )
            fields = list(manifest.get("schema", []))
            if not fields:
                partition = next((candidate for candidate in partition_paths if candidate.is_file()), None)
                if partition is not None:
                    fields = list(_parquet_info(partition)["fields"])
            row_count = manifest.get("row_count")
            date_min = manifest.get("date_min")
            date_max = manifest.get("date_max")
            if date_min is None or date_max is None:
                ranges = [_parquet_info(partition) for partition in partition_paths if partition.is_file()]
                date_values_min = [item["date_min"] for item in ranges if item["date_min"] is not None]
                date_values_max = [item["date_max"] for item in ranges if item["date_max"] is not None]
                date_min = min(date_values_min, default=None)
                date_max = max(date_values_max, default=None)
            manifest_hash = locked_manifest_hash
            metadata.update(
                {
                    "current": current,
                    "locked_version": locked_version,
                    "manifest_path": str(manifest_path),
                    "locked_manifest_hash": locked_manifest_hash,
                    "partition_fingerprint": partition_fingerprint,
                    "content_fingerprint": content_fingerprint,
                    "missing_partitions": missing_partitions,
                }
            )
            if missing_partitions:
                metadata["quality_reason"] = "declared partition files are missing"
                quality_status = "needs_review"
            elif current != locked_version:
                metadata["quality_reason"] = "CURRENT differs from locked version"
                quality_status = "needs_review"
            else:
                quality_status = "passed"
        elif kind == "parquet":
            info = _parquet_info(path)
            fields = info["fields"]
            row_count = info["row_count"]
            date_min = info["date_min"]
            date_max = info["date_max"]
            metadata.update(info)
            manifest_hash = _sha256_file(path)
            metadata["content_fingerprint"] = manifest_hash
            quality_status = "passed"
        else:
            parquet_files = sorted(path.glob("*.parquet"))
            fields = []
            row_count = 0
            for parquet_path in parquet_files:
                info = _parquet_info(parquet_path)
                fields.extend(field for field in info["fields"] if field not in fields)
                row_count += info["row_count"]
                if info["date_min"] is not None:
                    date_min = info["date_min"] if date_min is None else min(date_min, info["date_min"])
                if info["date_max"] is not None:
                    date_max = info["date_max"] if date_max is None else max(date_max, info["date_max"])
            metadata["file_count"] = len(parquet_files)
            manifest_hash, missing_files = _aggregate_file_fingerprint(path, parquet_files)
            metadata["content_fingerprint"] = manifest_hash
            metadata["missing_files"] = missing_files
            quality_status = "passed" if parquet_files else "warning"
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO datasets(entity_id, name, status) VALUES (?, ?, 'published') "
                "ON CONFLICT(entity_id) DO UPDATE SET name=excluded.name",
                (entry["entity_id"], entry["name"]),
            )
            stored_hash = manifest_hash
            if path.is_file() and not stored_hash:
                stored_hash = _metadata_hash(path)
            existing = connection.execute(
                "SELECT manifest_hash, metadata_json FROM dataset_versions WHERE entity_id = ? AND version_id = ?",
                (entry["entity_id"], version_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO dataset_versions(entity_id, version_id, path, row_count, fields_json, date_min, date_max, manifest_hash, metadata_json, status, quality_status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?)",
                    (
                        entry["entity_id"],
                        version_id,
                        self.settings.store_path(version_path),
                        row_count,
                        json.dumps(fields, ensure_ascii=False),
                        date_min,
                        date_max,
                        stored_hash,
                        json.dumps(metadata, ensure_ascii=False),
                        quality_status,
                    ),
                )
            else:
                if existing["manifest_hash"] != stored_hash:
                    quality_status = "needs_review"
                existing_metadata = json.loads(existing["metadata_json"])
                if existing_metadata.get("content_fingerprint") != metadata.get(
                    "content_fingerprint"
                ):
                    quality_status = "needs_review"
                connection.execute(
                    "UPDATE dataset_versions SET quality_status = ?, path = ? WHERE entity_id = ? AND version_id = ?",
                    (quality_status, self.settings.store_path(version_path), entry["entity_id"], version_id),
                )
        return {
            "entity_id": entry["entity_id"],
            "name": entry["name"],
            "version_id": version_id,
            "fields": fields,
            "row_count": row_count,
            "date_min": date_min,
            "date_max": date_max,
            "quality_status": quality_status,
        }

    def public_items(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT d.entity_id, d.name, d.status, v.version_id, v.row_count, v.fields_json, v.date_min, v.date_max, v.quality_status, v.metadata_json "
                "FROM datasets d JOIN dataset_versions v ON v.entity_id=d.entity_id ORDER BY d.entity_id, v.version_id"
            ).fetchall()
        return [
            self._public_item(
                {
                "entity_id": row["entity_id"],
                "name": row["name"],
                "status": row["status"],
                "version_id": row["version_id"],
                "row_count": row["row_count"],
                "fields": json.loads(row["fields_json"]),
                "date_min": row["date_min"],
                "date_max": row["date_max"],
                "quality_status": row["quality_status"],
                "metadata": json.loads(row["metadata_json"]),
                }
            )
            for row in rows
        ]



    _RAW_INTERFACE_DOCS = {
        "adj_factor": 28,
        "daily": 27,
        "daily_basic": 32,
        "index_basic": 94,
        "index_daily": 95,
        "index_weight": 96,
        "stk_limit": 183,
        "stock_basic": 25,
        "suspend_d": 214,
        "trade_cal": 26,
    }

    _RAW_INTERFACE_NAMES = {
        "adj_factor": "复权因子",
        "daily": "A股日线行情",
        "daily_basic": "每日指标",
        "index_basic": "指数基本信息",
        "index_daily": "指数日线行情",
        "index_weight": "指数成分和权重",
        "stk_limit": "涨跌停价格",
        "stock_basic": "股票基础信息",
        "suspend_d": "每日停复牌信息",
        "trade_cal": "交易日历",
    }
    ASSET_CLASS_LABELS = {
        "cn_a": "A股",
        "hk": "港股",
        "us": "美股",
        "crypto": "数字货币",
    }
    _RAW_INTERFACE_ASSET_CLASS: dict[str, str] = {}


    def _resolve_raw_root(self) -> Path:
        root = self.settings.raw_root
        override_file = self.settings.runtime_root / "config/raw_path.json"
        if override_file.is_file():
            try:
                override = json.loads(override_file.read_text(encoding="utf-8"))
                candidate = self.settings.resolve_user_path(override.get("path", ""))
                if candidate.is_dir():
                    root = candidate
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
        return root

    def _raw_file_date_range(self, files: list[Path]) -> tuple[str | None, str | None]:
        stem_dates: list[str] = []
        for file in files:
            stem = file.stem
            if len(stem) == 8 and stem.isdigit():
                stem_dates.append(stem)
        if stem_dates:
            return min(stem_dates), max(stem_dates)
        minimums: list[str] = []
        maximums: list[str] = []
        for file in files:
            try:
                info = _parquet_info(file)
            except OSError:
                continue
            if info["date_min"] is not None:
                minimums.append(str(info["date_min"]))
            if info["date_max"] is not None:
                maximums.append(str(info["date_max"]))
        return (min(minimums) if minimums else None, max(maximums) if maximums else None)

    def _build_raw_item(self, interface_name: str, files: list[Path]) -> dict[str, Any]:
        fields: list[str] = []
        total_rows = 0
        total_size_bytes = 0
        if files:
            try:
                fields = list(pq.ParquetFile(files[0]).schema_arrow.names)
            except OSError:
                pass
            for file in files:
                total_size_bytes += file.stat().st_size
                try:
                    total_rows += pq.ParquetFile(file).metadata.num_rows
                except OSError:
                    pass
        date_min, date_max = self._raw_file_date_range(files) if files else (None, None)
        asset_class = self._asset_class_for(interface_name)
        return {
            "entity_id": f"raw_{interface_name}",
            "name": interface_name,
            "name_cn": self._RAW_INTERFACE_NAMES.get(interface_name, interface_name),
            "tushare_url": f"https://tushare.pro/document/2?doc_id={self._RAW_INTERFACE_DOCS.get(interface_name, '')}",
            "status": "published",
            "version_id": "raw",
            "row_count": total_rows,
            "total_size_bytes": total_size_bytes,
            "file_count": len(files),
            "fields": fields,
            "date_min": date_min,
            "date_max": date_max,
            "quality_status": "passed" if files else "warning",
            "category": "raw",
            "asset_class": asset_class,
            "asset_class_label": self.ASSET_CLASS_LABELS[asset_class],
            "path_alias": f"raw/{interface_name}",
            "quality_reason": None if files else "尚未下载任何文件",
        }

    @classmethod
    def _asset_class_for(cls, interface_name: str) -> str:
        code = cls._RAW_INTERFACE_ASSET_CLASS.get(interface_name, "cn_a")
        if code not in cls.ASSET_CLASS_LABELS:
            return "cn_a"
        return code

    def _raw_signature(self, root: Path) -> tuple[object, ...]:
        entries = []
        seen: set[str] = set()
        for interface_name in sorted(self._RAW_INTERFACE_NAMES):
            interface_path = root / interface_name
            files = sorted(interface_path.glob("*.parquet")) if interface_path.is_dir() else []
            entries.append(
                (interface_name, tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files))
            )
            seen.add(interface_name)
        if root.is_dir():
            for interface_path in sorted(root.iterdir()):
                if not interface_path.is_dir() or interface_path.name in seen:
                    continue
                files = sorted(interface_path.glob("*.parquet"))
                entries.append(
                    (interface_path.name, tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size) for path in files))
                )
        return (str(root), tuple(entries))

    def raw_items(self) -> list[dict[str, Any]]:
        root = self._resolve_raw_root()
        signature = self._raw_signature(root)
        if signature in self._raw_cache:
            return list(self._raw_cache[signature][1])
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for interface_name in sorted(self._RAW_INTERFACE_NAMES):
            interface_path = root / interface_name
            files = sorted(interface_path.glob("*.parquet")) if interface_path.is_dir() else []
            items.append(self._build_raw_item(interface_name, files))
            seen.add(interface_name)
        if root.is_dir():
            for interface_path in sorted(root.iterdir()):
                if not interface_path.is_dir() or interface_path.name in seen:
                    continue
                files = sorted(interface_path.glob("*.parquet"))
                if not files:
                    continue
                items.append(self._build_raw_item(interface_path.name, files))
        self._raw_cache[signature] = (signature, list(items))
        return items

    def raw_interface_files(self, interface_name: str, limit: int = 20) -> dict[str, Any]:
        root = self._resolve_raw_root()
        if interface_name not in self._RAW_INTERFACE_NAMES and not (root / interface_name).is_dir():
            raise ValueError(f"raw interface not found: {interface_name}")
        interface_path = root / str(interface_name)
        if not interface_path.is_dir():
            return {
                "interface": interface_name,
                "name_cn": self._RAW_INTERFACE_NAMES.get(interface_name, interface_name),
                "total": 0,
                "items": [],
            }
        files = [path for path in interface_path.glob("*.parquet") if path.is_file()]
        files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
        items = []
        for path in files[: max(1, min(int(limit), 200))]:
            stat = path.stat()
            items.append({
                "file_name": path.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "relative_path": f"raw/{interface_name}/{path.name}",
            })
        return {"interface": interface_name, "name_cn": self._RAW_INTERFACE_NAMES.get(interface_name, interface_name), "total": len(files), "items": items}

    def quality_summary(self) -> dict[str, Any]:
        """Return lightweight quality checks from registered metadata only."""
        items = []
        for item in self.public_items():
            fields = item.get("fields") or []
            row_count = item.get("row_count")
            date_min, date_max = item.get("date_min"), item.get("date_max")
            path_alias = item.get("path_alias") or ""
            quality_status = item.get("quality_status") or "unknown"
            checks = [
                {
                    "name": "已登记版本",
                    "passed": True,
                    "detail": item.get("version_id") or "未登记",
                },
                {
                    "name": "Schema 已登记",
                    "passed": bool(fields),
                    "detail": f"{len(fields)} 个字段" if fields else "无字段摘要",
                },
                {
                    "name": "行数与日期覆盖",
                    "passed": bool(row_count) and bool(date_min and date_max),
                    "detail": f"{row_count or 0} 行 · {date_min or '—'} 至 {date_max or '—'}",
                },
                {
                    "name": "质量状态",
                    "passed": quality_status == "passed",
                    "detail": quality_status
                    + (f"（{item['quality_reason']}）" if item.get("quality_reason") else ""),
                },
                {
                    "name": "路径别名安全",
                    "passed": bool(path_alias)
                    and not Path(path_alias).is_absolute()
                    and ".." not in Path(path_alias).parts,
                    "detail": path_alias or "未登记别名",
                },
            ]
            items.append(
                {
                    "entity_id": item.get("entity_id"),
                    "name": item.get("name"),
                    "version_id": item.get("version_id"),
                    "category": item.get("category"),
                    "quality_status": quality_status,
                    "checks": checks,
                }
            )
        return {"items": items}

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        metadata = item.pop("metadata", {})
        item["category"] = metadata.get("category", "unknown")
        path_alias = metadata.get("path_alias", "")
        if (
            not isinstance(path_alias, str)
            or not path_alias
            or Path(path_alias).is_absolute()
            or ".." in Path(path_alias).parts
        ):
            path_alias = ""
        item["path_alias"] = path_alias
        item["quality_reason"] = metadata.get("quality_reason")
        return item

    def filtered_items(
        self,
        *,
        category: str | None = None,
        status: str | None = None,
        quality_status: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        query: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        items = self.public_items()
        query = query.strip().lower() if query else None
        if category:
            items = [item for item in items if item["category"] == category]
        if status:
            items = [item for item in items if item["status"] == status]
        if quality_status:
            items = [item for item in items if item["quality_status"] == quality_status]
        if date_from:
            items = [item for item in items if item["date_max"] and item["date_max"] >= date_from]
        if date_to:
            items = [item for item in items if item["date_min"] and item["date_min"] <= date_to]
        if query:
            items = [
                item for item in items
                if query in item["entity_id"].lower() or query in item["name"].lower()
            ]
        total = len(items)
        start = (page - 1) * page_size
        return {"items": items[start : start + page_size], "page": page, "page_size": page_size, "total": total}

    def versions(self, entity_id: str) -> list[dict[str, Any]]:
        return [item for item in self.public_items() if item["entity_id"] == entity_id]

    def quality_alerts(self) -> list[dict[str, Any]]:
        return [
            {
                "entity_id": item["entity_id"],
                "version_id": item["version_id"],
                "quality_status": item["quality_status"],
                "message": item["quality_reason"] or f"质量状态为 {item['quality_status']}",
            }
            for item in self.public_items()
            if item["quality_status"] != "passed"
        ]

    def rescan(self) -> dict[str, Any]:
        audit_id = f"scan-{uuid.uuid4().hex}"
        started_at = datetime.now(timezone.utc).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO dataset_scan_audits(audit_id, audit_type, status, started_at) VALUES (?, 'manual_rescan', 'running', ?)",
                (audit_id, started_at),
            )
        try:
            items = self.register_configured()
            configured_versions = [
                (entry["entity_id"], entry.get("version_id", "current"))
                for entry in self._configured()
            ]
            clauses = " OR ".join("(entity_id = ? AND version_id = ?)" for _ in configured_versions)
            with self.database.connect() as connection:
                changed_count = connection.execute(
                    f"SELECT COUNT(*) FROM dataset_versions WHERE quality_status = 'needs_review' AND ({clauses})",
                    [value for pair in configured_versions for value in pair],
                ).fetchone()[0]
            completed_at = datetime.now(timezone.utc).isoformat()
            summary = {"dataset_count": len(items), "needs_review_count": changed_count}
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE dataset_scan_audits SET status='completed', completed_at=?, changed_count=?, summary_json=? WHERE audit_id=?",
                    (completed_at, changed_count, json.dumps(summary), audit_id),
                )
            return {"audit_id": audit_id, "status": "completed", "changed_count": changed_count, "summary": summary}
        except Exception:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE dataset_scan_audits SET status='failed', completed_at=? WHERE audit_id=?",
                    (datetime.now(timezone.utc).isoformat(), audit_id),
                )
            raise

    def scan_audits(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT audit_id, audit_type, status, started_at, completed_at, changed_count, summary_json "
                "FROM dataset_scan_audits ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "audit_id": row["audit_id"],
                "audit_type": row["audit_type"],
                "status": row["status"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "changed_count": row["changed_count"],
                "summary": json.loads(row["summary_json"]),
            }
            for row in rows
        ]

    def get_public(self, entity_id: str, version_id: str | None = None) -> dict[str, Any] | None:
        items = [item for item in self.public_items() if item["entity_id"] == entity_id]
        if version_id is not None:
            items = [item for item in items if item["version_id"] == version_id]
        return items[0] if items else None
