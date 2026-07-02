import json

from kalshi_bot.hermes_tools import TOOL_REGISTRY, tool_manifest


def test_registry_and_manifest_agree():
    manifest = tool_manifest()
    assert len(manifest) == len(TOOL_REGISTRY) >= 12
    for spec in manifest:
        assert spec["name"] in TOOL_REGISTRY
        assert spec["description"]              # every tool documents itself
        assert isinstance(spec["parameters"], list)
    json.dumps(manifest)                        # manifest is JSON-serializable


def test_offline_tools_return_json_dicts():
    indices = TOOL_REGISTRY["compute_degree_day_indices"]([[80, 60], [90, 70]])
    assert indices == {"days": 2, "hdd": 0.0, "cdd": 20.0, "cat": 150.0, "base": 65.0}
    json.dumps(indices)

    prob = TOOL_REGISTRY["temperature_range_probability"](76, 77, 77.0, 2.5)
    assert 0 < prob["probability"] < 1

    fee = TOOL_REGISTRY["kalshi_trading_fee"](50)
    assert fee == {"fee_cents": 2}

    value = TOOL_REGISTRY["evaluate_binary_value"](0.02, 10, 12)
    assert value["bet"]["side"] == "no"
    json.dumps(value)


def test_no_edge_returns_none_bet():
    out = TOOL_REGISTRY["evaluate_binary_value"](0.30, 29, 31)
    assert out["bet"] is None and "reason" in out


def test_tools_never_raise():
    # Bad input must come back as an error dict, not an exception.
    out = TOOL_REGISTRY["compute_degree_day_indices"]("not-a-list")
    assert "error" in out
    out2 = TOOL_REGISTRY["temperature_range_probability"](None, None, "bad", None)
    assert "error" in out2


def test_scorecard_reads_real_ledgers():
    out = TOOL_REGISTRY["validation_scorecard"]()
    assert "strategies" in out and len(out["strategies"]) >= 3
    for s in out["strategies"]:
        assert set(s) >= {"label", "open", "settled", "roi_pct", "verdict"}
    json.dumps(out)
