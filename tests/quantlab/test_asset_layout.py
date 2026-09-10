from pytest import raises

from quantlab.services.asset_layout import directory_plan, normalize_asset_class


def test_directory_plan_keeps_a_share_paths_and_nests_future_markets() -> None:
    plan = directory_plan(data_root="data", raw_root="data/raw", runtime_root="quantlab_runtime")
    codes = [item["code"] for item in plan["asset_classes"]]
    assert codes == ["cn_a", "hk", "us", "crypto"]
    a_share = plan["asset_classes"][0]
    assert a_share["label"] == "A股"
    assert a_share["current"] is True
    assert a_share["paths"]["canonical"] == "data/canonical.parquet"
    assert a_share["paths"]["derived"] == "data/derived/"
    assert a_share["paths"]["raw"] == "data/raw/"
    assert a_share["paths"]["factors_runtime"] == "quantlab_runtime/factors/"
    hong_kong = next(item for item in plan["asset_classes"] if item["code"] == "hk")
    assert hong_kong["current"] is False
    assert hong_kong["paths"]["canonical"] == "data/hk/canonical.parquet"
    assert hong_kong["paths"]["derived"] == "data/hk/derived/"
    assert hong_kong["paths"]["raw"] == "data/hk/raw/"
    assert hong_kong["paths"]["factors_runtime"] == "quantlab_runtime/factors/hk/"
    assert plan["rules"] == [
        "A股沿用现有路径，不搬家。",
        "因子必须自带资产分类；唯一键是（资产分类, 因子ID, 版本）。",
        "计算和回测不得混用不同资产分类。",
        "港股、美股、数字货币以后落在 data/{分类}/ 下，不要写入现有 A 股 parquet。",
        "局域网只同步 data/，不同步 quantlab_runtime/factors/。",
    ]
    overridden = directory_plan(data_root="data", raw_root="other/raw", runtime_root="quantlab_runtime")
    assert overridden["asset_classes"][0]["paths"]["raw"] == "other/raw/"
    assert overridden["asset_classes"][1]["paths"]["raw"] == "data/hk/raw/"


def test_normalize_asset_class_defaults_to_a_share_and_rejects_unknown() -> None:
    assert normalize_asset_class(None) == "cn_a"
    assert normalize_asset_class("") == "cn_a"
    assert normalize_asset_class("hk") == "hk"
    with raises(ValueError, match="asset_class"):
        normalize_asset_class("gold")
