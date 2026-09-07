"""Stable identifier validation shared by Python entry points."""

from __future__ import annotations

import re


_RUN_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-\d{4}$")


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or _RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("run_id must match YYYYMMDD-HHMMSS-NNNN")
    return run_id
