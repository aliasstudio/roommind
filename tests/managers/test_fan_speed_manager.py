"""Tests for FanSpeedManager auto fan-speed escalation and fan ladder helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.roommind.const import TargetTemps
from custom_components.roommind.coordinator import RoomMindCoordinator
from custom_components.roommind.managers.fan_speed_manager import (
    DWELL_DEESCALATE_S,
    DWELL_ESCALATE_S,
    FanSpeedManager,
)
from custom_components.roommind.utils.device_utils import get_fan_control, order_fan_modes

LADDER_LABELS = ["Strong", "High", "Mid-high", "Medium", "Mid-low", "Low", "Mute", "Auto"]
LADDER = ["Mute", "Low", "Mid-low", "Medium", "Mid-high", "High", "Strong"]


# ---------------------------------------------------------------------------
# order_fan_modes / get_fan_control
# ---------------------------------------------------------------------------


def test_order_fan_modes_orders_weak_to_strong_and_drops_auto():
    assert order_fan_modes(LADDER_LABELS) == LADDER


def test_order_fan_modes_excludes_ungraded():
    assert order_fan_modes(["Auto", "Diffuse", "Natural"]) == []


def test_order_fan_modes_unknown_labels_dropped():
    assert order_fan_modes(["low", "weirdmode", "high"]) == ["low", "high"]


def test_get_fan_control_defaults():
    assert get_fan_control([], "climate.x") == ("off", "", "")


def test_get_fan_control_reads_config_and_sanitizes():
    devices = [
        {"entity_id": "climate.a", "type": "ac", "fan_control": "auto", "fan_min_mode": "Mute", "fan_max_mode": "High"},
        {"entity_id": "climate.b", "type": "ac", "fan_control": "bogus"},
    ]
    assert get_fan_control(devices, "climate.a") == ("auto", "Mute", "High")
    # invalid mode falls back to "off"
    assert get_fan_control(devices, "climate.b") == ("off", "", "")


# ---------------------------------------------------------------------------
# FanSpeedManager
# ---------------------------------------------------------------------------


def test_idle_returns_none_but_keeps_level():
    m = FanSpeedManager()
    assert m.evaluate("ac", LADDER, active=False, deficit=2.0, power_fraction=1.0, now=1000) is None
    assert m.current_level("ac") == 0


def test_escalates_when_warm_and_full_power():
    m = FanSpeedManager()
    # first active call: dwell huge (changed_at=0) -> steps Mute -> Low
    assert m.evaluate("ac", LADDER, active=True, deficit=1.0, power_fraction=1.0, now=1000) == "Low"
    # immediately again: dwell ~0 -> holds
    assert m.evaluate("ac", LADDER, active=True, deficit=1.0, power_fraction=1.0, now=1001) == "Low"
    # after escalate dwell -> steps again
    nxt = 1001 + DWELL_ESCALATE_S
    assert m.evaluate("ac", LADDER, active=True, deficit=1.0, power_fraction=1.0, now=nxt) == "Mid-low"


def test_no_escalation_when_demand_low():
    m = FanSpeedManager()
    # full deficit but low power_fraction -> not capacity-limited -> hold at min
    assert m.evaluate("ac", LADDER, active=True, deficit=1.0, power_fraction=0.5, now=1e6) == "Mute"


def test_deescalates_when_cold_or_low_demand():
    m = FanSpeedManager()
    m._level["ac"] = 3  # Medium
    # cold room, sustained -> step down
    out = m.evaluate("ac", LADDER, active=True, deficit=-1.0, power_fraction=0.1, now=1e6)
    assert out == "Mid-low"


def test_deescalation_respects_dwell():
    m = FanSpeedManager()
    m._level["ac"] = 3
    m._changed_at["ac"] = 1000
    # only DWELL_ESCALATE elapsed (< DWELL_DEESCALATE) -> hold
    too_soon = 1000 + DWELL_ESCALATE_S
    assert m.evaluate("ac", LADDER, active=True, deficit=-1.0, power_fraction=0.1, now=too_soon) == "Medium"
    ok = 1000 + DWELL_DEESCALATE_S
    assert m.evaluate("ac", LADDER, active=True, deficit=-1.0, power_fraction=0.1, now=ok) == "Mid-low"


def test_bounds_clamp_level():
    m = FanSpeedManager()
    m._level["ac"] = 5
    # max_idx caps escalation
    out = m.evaluate("ac", LADDER, active=True, deficit=5.0, power_fraction=1.0, min_idx=1, max_idx=2, now=1e6)
    assert out == "Mid-low"  # clamped down into [1,2] then held at 2


def test_empty_ladder_returns_none():
    m = FanSpeedManager()
    assert m.evaluate("ac", [], active=True, deficit=5.0, power_fraction=1.0, now=1e6) is None


def test_reset_clears_state():
    m = FanSpeedManager()
    m.evaluate("ac", LADDER, active=True, deficit=1.0, power_fraction=1.0, now=1000)
    m.reset("ac")
    assert m.current_level("ac") is None


# ---------------------------------------------------------------------------
# Coordinator._resolve_fan_overrides
# ---------------------------------------------------------------------------


def _fake_coordinator(fan_modes):
    state = MagicMock()
    state.attributes = {"fan_modes": fan_modes}
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=state)
    return SimpleNamespace(hass=hass, _fan_speed_manager=FanSpeedManager())


def _resolve(fake, **kw):
    return RoomMindCoordinator._resolve_fan_overrides(fake, **kw)


def test_resolve_auto_escalates_in_cooling():
    fake = _fake_coordinator(LADDER_LABELS)
    room = {
        "devices": [
            {"entity_id": "climate.ac1", "type": "ac", "fan_control": "auto", "fan_min_mode": "Mute", "fan_max_mode": "High"}
        ]
    }
    out = _resolve(
        fake,
        room=room,
        mode="cooling",
        current_temp=25.0,
        targets=TargetTemps(heat=20.0, cool=23.0),
        power_fraction=1.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {"climate.ac1": "Low"}


def test_resolve_off_returns_empty_and_resets():
    fake = _fake_coordinator(LADDER_LABELS)
    room = {"devices": [{"entity_id": "climate.ac1", "type": "ac", "fan_control": "off"}]}
    out = _resolve(
        fake,
        room=room,
        mode="cooling",
        current_temp=25.0,
        targets=TargetTemps(heat=20.0, cool=23.0),
        power_fraction=1.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {}


def test_resolve_window_open_no_override():
    fake = _fake_coordinator(LADDER_LABELS)
    room = {"devices": [{"entity_id": "climate.ac1", "type": "ac", "fan_control": "auto"}]}
    out = _resolve(
        fake,
        room=room,
        mode="cooling",
        current_temp=25.0,
        targets=TargetTemps(heat=20.0, cool=23.0),
        power_fraction=1.0,
        climate_active=True,
        window_open=True,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {}


def test_resolve_idle_no_override():
    fake = _fake_coordinator(LADDER_LABELS)
    room = {"devices": [{"entity_id": "climate.ac1", "type": "ac", "fan_control": "auto"}]}
    out = _resolve(
        fake,
        room=room,
        mode="idle",
        current_temp=25.0,
        targets=TargetTemps(heat=20.0, cool=23.0),
        power_fraction=0.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {}


def test_resolve_no_ladder_skips():
    fake = _fake_coordinator(["Auto"])  # no graded speeds
    room = {"devices": [{"entity_id": "climate.ac1", "type": "ac", "fan_control": "auto"}]}
    out = _resolve(
        fake,
        room=room,
        mode="cooling",
        current_temp=25.0,
        targets=TargetTemps(heat=20.0, cool=23.0),
        power_fraction=1.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {}
