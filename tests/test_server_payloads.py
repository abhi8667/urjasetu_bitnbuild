"""Server payload contracts used by the digital twin."""
from __future__ import annotations

from server.simulation import build_simulation


def test_battery_dispatch_checkpoint_has_same_transformer_recipients():
    sim = build_simulation(days=2)
    target = sim.scene["scenario_blocks"]["battery_dispatch"]
    assert target is not None

    block = next(item for item in sim.blocks if item["block"] == target)
    dispatches = block["battery"]["dispatches"]
    assert dispatches

    transformer_of = {house["id"]: house["transformer"] for house in sim.scene["houses"]}
    discharged_by_source = {
        house_id: state["battery_discharged_kwh"]
        for house_id, state in block["houses"].items()
        if state["battery_discharged_kwh"] > 0
    }
    allocated_by_source: dict[str, float] = {}
    for dispatch in dispatches:
        assert dispatch["from"] != dispatch["to"]
        assert transformer_of[dispatch["from"]] == transformer_of[dispatch["to"]]
        assert dispatch["transformer_id"] == transformer_of[dispatch["from"]]
        allocated_by_source[dispatch["from"]] = (
            allocated_by_source.get(dispatch["from"], 0.0) + dispatch["kwh"])

    for source, discharged in discharged_by_source.items():
        assert abs(allocated_by_source[source] - discharged) <= 0.0003
