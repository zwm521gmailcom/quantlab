"""Command-line entry points for the local QuantLab service."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from quantlab.api.app import create_app
from quantlab.config import Settings
from quantlab.repositories.database import Database
from quantlab.services.catalog import DatasetCatalog, snapshot_authoritative_data, verify_authoritative_data


def _add_root_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root")
    parser.add_argument("--data-root")
    parser.add_argument("--calibration-root")
    parser.add_argument("--runtime-root")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="quantlab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    _add_root_arguments(serve)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, help="页面端口，默认 8765，或读取 instance.json")
    serve.add_argument("--lan-port", dest="lan_port", type=int, help="局域网同步端口，默认 8766，须与页面端口不同")
    serve.add_argument("--asset", help="本实例资产：a_share（A股）或 crypto（数字货币）")
    init_db = subparsers.add_parser("init-db")
    _add_root_arguments(init_db)
    snapshot = subparsers.add_parser("snapshot-data-baseline")
    _add_root_arguments(snapshot)
    verify = subparsers.add_parser("verify-data-baseline")
    _add_root_arguments(verify)
    materialize = subparsers.add_parser("materialize-pack-factors")
    _add_root_arguments(materialize)
    materialize_moneyflow = subparsers.add_parser("materialize-moneyflow-factors")
    _add_root_arguments(materialize_moneyflow)
    register_pack = subparsers.add_parser("register-pack-factors")
    _add_root_arguments(register_pack)
    register_pack.add_argument("--field", action="append", dest="fields")
    register_moneyflow = subparsers.add_parser("register-moneyflow-factors")
    _add_root_arguments(register_moneyflow)
    register_alpha = subparsers.add_parser("register-alpha191-factors")
    _add_root_arguments(register_alpha)
    append_alpha = subparsers.add_parser("append-alpha191-factors")
    _add_root_arguments(append_alpha)
    args = parser.parse_args(argv)
    explicit_roots = {
        name: value
        for name in ("project_root", "data_root", "calibration_root", "runtime_root")
        if (value := getattr(args, name, None)) is not None
    }
    extra: dict[str, object] = {}
    if args.command == "serve":
        extra["host"] = args.host
        if args.port is not None:
            extra["port"] = args.port
        if getattr(args, "lan_port", None) is not None:
            extra["lan_port"] = args.lan_port
        if getattr(args, "asset", None):
            extra["asset"] = args.asset
    try:
        settings = Settings(**explicit_roots, **extra)
    except ValueError as error:
        parser.error(str(error))
    if args.command == "serve":
        import uvicorn

        from quantlab.services.lan_runtime import start_lan_sidecar

        app = create_app(settings)
        start_lan_sidecar(app)
        uvicorn.run(app, host=settings.host, port=settings.port)
        return 0
    if args.command == "init-db":
        database = Database(settings.database_path)
        database.initialize()
        registered = DatasetCatalog(settings, database).register_configured()
        print(f"initialized {settings.display_path(settings.database_path)} ({len(registered)} datasets)")
        return 0
    if args.command == "materialize-pack-factors":
        from quantlab.services.canonical_pack_factors import materialize_canonical_pack_factors

        result = materialize_canonical_pack_factors(
            settings.data_root / "canonical.parquet",
            progress=lambda field, index, total: print(f"{index}/{total} {field}", flush=True),
        )
        print(
            f"wrote {settings.display_path(settings.data_root / result['path'])} "
            f"({result['rows']} rows, {len(result['fields'])} fields)"
        )
        return 0
    if args.command == "materialize-moneyflow-factors":
        from quantlab.services.moneyflow_factors import materialize_moneyflow_factors

        result = materialize_moneyflow_factors(
            settings.data_root / "canonical.parquet",
            settings.raw_root,
            progress=lambda field, index, total: print(f"{index}/{total} {field}", flush=True),
        )
        print(
            f"wrote {settings.display_path(settings.data_root / result['path'])} "
            f"({result['rows']} rows, {len(result['fields'])} fields)"
        )
        return 0
    if args.command == "register-pack-factors":
        from quantlab.repositories.factors import FactorRepository
        from quantlab.services.canonical_factor_pack import register_canonical_pack_factors

        database = Database(settings.database_path)
        result = register_canonical_pack_factors(
            FactorRepository(settings, database),
            fields=args.fields,
        )
        print(
            f"published {result['published_count']}, skipped {result['skipped_count']}, "
            f"failed {result['failed_count']}"
        )
        return 0 if result["failed_count"] == 0 else 1
    if args.command == "register-moneyflow-factors":
        from quantlab.repositories.factors import FactorRepository
        from quantlab.services.moneyflow_factors import register_moneyflow_factors

        database = Database(settings.database_path)
        result = register_moneyflow_factors(FactorRepository(settings, database))
        print(
            f"published {result['published_count']}, skipped {result['skipped_count']}, "
            f"failed {result['failed_count']}"
        )
        return 0 if result["failed_count"] == 0 else 1
    if args.command == "register-alpha191-factors":
        from quantlab.repositories.factors import FactorRepository
        from quantlab.services.canonical_factor_pack import register_alpha191_factors

        database = Database(settings.database_path)
        result = register_alpha191_factors(FactorRepository(settings, database))
        print(
            f"published {result['published_count']}, skipped {result['skipped_count']}, "
            f"failed {result['failed_count']}"
        )
        return 0 if result["failed_count"] == 0 else 1
    if args.command == "append-alpha191-factors":
        from quantlab.services.canonical_pack_factors import append_alpha191_factors

        result = append_alpha191_factors(
            settings.data_root / "canonical.parquet",
            progress=lambda field, index, total: print(f"{index}/{total} {field}", flush=True),
        )
        print(
            f"wrote {settings.display_path(settings.data_root / result['path'])} "
            f"({result['rows']} rows, added {len(result['added'])})"
        )
        return 0
    baseline = settings.runtime_root / "baselines/authoritative-data.json"
    if args.command == "verify-data-baseline":
        ok = verify_authoritative_data(settings, baseline)
        print("verified" if ok else "changed")
        return 0 if ok else 1
    snapshot_authoritative_data(settings, baseline)
    print(f"created {settings.display_path(baseline)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
