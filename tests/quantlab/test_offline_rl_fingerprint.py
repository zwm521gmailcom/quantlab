from quantlab.services.offline_rl.fingerprint import config_fingerprint, is_offline_rl_result


def test_fingerprint_ignores_name_and_dates_but_keeps_rules():
    a = {
        "name": "2019 · 袖套A",
        "factor_versions": [{"field": "sleeve_p60_c075", "factor_id": "f1", "version_id": "v1"}],
        "top_n": 4,
        "weighting": "equal",
        "holding_days": 2,
        "rebalance_every": 1,
        "open_when_benchmark_gt_ma200": True,
        "test": {"filter": {"expressions": ["st_status == 0"]}, "date_from": "2019-01-02", "date_to": "2019-12-31"},
        "train": {"filter": {"expressions": ["st_status == 0"]}},
        "model": {"kind": "factor_rank"},
    }
    b = dict(a)
    b["name"] = "2020 · 袖套A"
    b["test"] = dict(a["test"], date_from="2020-01-02", date_to="2020-12-31")
    assert config_fingerprint(a) == config_fingerprint(b)
    c = dict(a)
    c["top_n"] = 5
    assert config_fingerprint(a) != config_fingerprint(c)
    assert is_offline_rl_result({"kind": "offline_rl"}) is True
    assert is_offline_rl_result({"kind": "factor_rank"}) is False
