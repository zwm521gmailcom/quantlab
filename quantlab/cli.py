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
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    init_db = subparsers.add_parser("init-db")
    _add_root_arguments(init_db)
    snapshot = subparsers.add_parser("snapshot-data-baseline")
    _add_root_arguments(snapshot)
    verify = subparsers.add_parser("verify-data-baseline")
    _add_root_arguments(verify)
    args = parser.parse_args(argv)
    explicit_roots = {
        name: value
        for name in ("project_root", "data_root", "calibration_root", "runtime_root")
        if (value := getattr(args, name, None)) is not None
    }
    settings = Settings(port=getattr(args, "port", 8765), **explicit_roots)
    if args.command == "serve":
        if args.host != "127.0.0.1":
            parser.error("QuantLab only accepts host 127.0.0.1")
        import uvicorn

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0
    if args.command == "init-db":
        database = Database(settings.database_path)
        database.initialize()
        registered = DatasetCatalog(settings, database).register_configured()
        print(f"initialized {settings.database_path} ({len(registered)} datasets)")
        return 0
    baseline = settings.runtime_root / "baselines/authoritative-data.json"
    if args.command == "verify-data-baseline":
        ok = verify_authoritative_data(settings, baseline)
        print("verified" if ok else "changed")
        return 0 if ok else 1
    snapshot_authoritative_data(settings, baseline)
    print(f"created {baseline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
