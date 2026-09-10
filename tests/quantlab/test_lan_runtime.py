from datetime import datetime

from quantlab.services.lan_runtime import should_run_market_sync_0400


def test_0400_market_sync_runs_once_in_the_hour_and_skips_if_missed() -> None:
    day = datetime(2026, 9, 10, 4, 5)
    assert should_run_market_sync_0400(True, day, None) is True
    assert should_run_market_sync_0400(True, day, day.date()) is False
    assert should_run_market_sync_0400(False, day, None) is False
    assert should_run_market_sync_0400(True, datetime(2026, 9, 10, 3, 59), None) is False
    assert should_run_market_sync_0400(True, datetime(2026, 9, 10, 5, 0), None) is False
    assert should_run_market_sync_0400(True, day, datetime(2026, 9, 9).date()) is True
