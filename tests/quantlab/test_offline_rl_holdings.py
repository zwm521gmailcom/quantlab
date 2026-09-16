from quantlab.services.offline_rl.holdings import daily_holdings_from_trades


def test_holdings_span_buy_to_sell_exclusive():
    trades = [
        {
            "instrument": "600000.SH",
            "buy_date": "20180103",
            "sell_date": "20180105",
            "status": "filled",
        },
        {
            "instrument": "000001.SZ",
            "buy_date": "20180104",
            "sell_date": "20180105",
            "status": "filled",
        },
    ]
    dates = ["20180102", "20180103", "20180104", "20180105"]
    held = daily_holdings_from_trades(trades, dates)
    assert held["20180102"] == set()
    assert held["20180103"] == {"600000.SH"}
    assert held["20180104"] == {"600000.SH", "000001.SZ"}
    assert held["20180105"] == set()
