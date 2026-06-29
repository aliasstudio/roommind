"""Tests for FanSpeedManager proportional fan-speed control and fan ladder helpers."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.roommind.const import TargetTemps
from custom_components.roommind.coordinator import RoomMindCoordinator
from custom_components.roommind.managers.fan_speed_manager import (
    CHANGE_MIN_S,
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
# FanSpeedManager — proportional law
# ---------------------------------------------------------------------------


def test_far_above_target_runs_max():
    m = FanSpeedManager()
    assert m.evaluate("ac", LADDER, deficit=2.0, power_fraction=1.0, now=1000) == "Strong"


def test_at_or_below_target_runs_min():
    m = FanSpeedManager()
    assert m.evaluate("ac", LADDER, deficit=-0.5, power_fraction=0.0, now=1000) == "Mute"


def test_proportional_midpoint():
    m = FanSpeedManager()
    # 0.5 of the full band, full pf -> middle of a 7-step ladder
    assert m.evaluate("ac", LADDER, deficit=0.5, power_fraction=1.0, now=1000) == "Medium"


def test_power_fraction_caps_speed():
    m = FanSpeedManager()
    # large error but the model only wants 1/3 power -> fan held to ~1/3 ladder
    assert m.evaluate("ac", LADDER, deficit=2.0, power_fraction=0.33, now=1000) == "Mid-low"


def test_rate_limited_between_changes():
    m = FanSpeedManager()
    m.evaluate("ac", LADDER, deficit=2.0, power_fraction=1.0, now=1000)  # Strong
    # demand drops, but a change was just made -> hold until the interval elapses
    assert m.evaluate("ac", LADDER, deficit=-1.0, power_fraction=0.0, now=1030) == "Strong"
    assert m.evaluate("ac", LADDER, deficit=-1.0, power_fraction=0.0, now=1000 + CHANGE_MIN_S + 1) == "Mute"


def test_bounds_cap_applied_immediately():
    m = FanSpeedManager()
    m._level["ac"] = LADDER.index("Strong")
    m._changed_at["ac"] = 1000
    # tightened max bound clamps down at once, ignoring the rate limit
    out = m.evaluate("ac", LADDER, deficit=2.0, power_fraction=1.0, min_idx=0, max_idx=1, now=1005)
    assert out == "Low"


def test_min_bound_respected():
    m = FanSpeedManager()
    out = m.evaluate("ac", LADDER, deficit=-2.0, power_fraction=0.0, min_idx=1, max_idx=6, now=5e6)
    assert out == "Low"  # cannot go below min_idx even at/below target


def test_empty_ladder_returns_none():
    m = FanSpeedManager()
    assert m.evaluate("ac", [], deficit=5.0, power_fraction=1.0, now=1e6) is None


def test_reset_clears_state():
    m = FanSpeedManager()
    m.evaluate("ac", LADDER, deficit=1.0, power_fraction=1.0, now=1000)
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


def test_resolve_proportional_in_cooling():
    fake = _fake_coordinator(LADDER_LABELS)
    room = {
        "devices": [
            {
                "entity_id": "climate.ac1",
                "type": "ac",
                "fan_control": "auto",
                "fan_min_mode": "Mute",
                "fan_max_mode": "High",
            }
        ]
    }
    # 2°C above target, full power -> top of the [Mute..High] band
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
    assert out == {"climate.ac1": "High"}


def test_resolve_setback_idle_below_target_goes_min():
    fake = _fake_coordinator(LADDER_LABELS)
    fake._fan_speed_manager._level["climate.ac1"] = LADDER.index("Mid-high")  # carried over from cooling
    room = {
        "devices": [
            {
                "entity_id": "climate.ac1",
                "type": "ac",
                "fan_control": "auto",
                "idle_action": "setback",
                "fan_min_mode": "Mute",
                "fan_max_mode": "Strong",
            }
        ]
    }
    out = _resolve(
        fake,
        room=room,
        mode="idle",
        current_temp=23.7,
        targets=TargetTemps(heat=24.0, cool=24.0),
        power_fraction=0.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {"climate.ac1": "Mute"}


def test_resolve_idle_off_no_override():
    fake = _fake_coordinator(LADDER_LABELS)
    # default idle_action is "off" -> device is off in idle, no fan to drive
    room = {"devices": [{"entity_id": "climate.ac1", "type": "ac", "fan_control": "auto"}]}
    out = _resolve(
        fake,
        room=room,
        mode="idle",
        current_temp=23.7,
        targets=TargetTemps(heat=24.0, cool=24.0),
        power_fraction=0.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
    )
    assert out == {}


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


def test_resolve_schedule_fan_caps_max_immediately():
    """A schedule fan_max_mode cap pulls a high level down at once."""
    fake = _fake_coordinator(LADDER_LABELS)
    fake._fan_speed_manager._level["climate.ac1"] = LADDER.index("Strong")  # currently maxed
    fake._fan_speed_manager._changed_at["climate.ac1"] = 0.0
    room = {
        "devices": [
            {
                "entity_id": "climate.ac1",
                "type": "ac",
                "fan_control": "auto",
                "fan_min_mode": "Mute",
                "fan_max_mode": "Strong",
            }
        ]
    }
    out = _resolve(
        fake,
        room=room,
        mode="cooling",
        current_temp=24.3,
        targets=TargetTemps(heat=20.0, cool=24.0),
        power_fraction=1.0,
        climate_active=True,
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
        schedule_fan={"fan_max_mode": "Low"},
    )
    assert out == {"climate.ac1": "Low"}


def test_resolve_schedule_fan_control_off_disables():
    """A schedule fan_control='off' suppresses the override for that slot."""
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
        window_open=False,
        force_off=False,
        cycling_eids=set(),
        compressor_forced_off=set(),
        schedule_fan={"fan_control": "off"},
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


# ---------------------------------------------------------------------------
# Coordinator._room_for_control (schedule idle override overlay)
# ---------------------------------------------------------------------------


def test_room_for_control_no_blocks_returns_same_object():
    room = {"devices": [{"entity_id": "climate.ac1", "type": "ac", "idle_action": "off"}]}
    assert RoomMindCoordinator._room_for_control(SimpleNamespace(), room, None) is room


def test_room_for_control_applies_schedule_idle():
    import time

    room = {
        "area_id": "spalnia",
        "devices": [
            {"entity_id": "climate.ac1", "type": "ac", "idle_action": "off"},
            {"entity_id": "climate.trv1", "type": "trv", "idle_action": "low"},
        ],
    }
    today = datetime.fromtimestamp(time.time()).strftime("%A").lower()
    blocks = {today: [{"from": "00:00:00", "to": "23:59:59", "data": {"idle_action": "fan_only", "idle_fan_mode": "Mute"}}]}
    out = RoomMindCoordinator._room_for_control(SimpleNamespace(), room, blocks)
    assert out is not room
    assert out["devices"][0]["idle_action"] == "fan_only"
    assert out["devices"][0]["idle_fan_mode"] == "Mute"
    assert out["devices"][1]["idle_action"] == "low"  # TRV unaffected
    assert room["devices"][0]["idle_action"] == "off"
