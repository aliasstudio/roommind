"""Louver position held per running direction, from the device setting or the schedule block."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.control.mpc_controller import MPCController, clear_command_cache
from custom_components.roommind.control.thermal_model import RoomModelManager

from .conftest import build_hass, make_room

AC = "climate.ac"
SWING_MODES = ["Top fix", "Middle fix", "Lower fix", "Not set"]


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_command_cache()
    yield
    clear_command_cache()


def _controller(hvac_state: str, swing_mode: str, **device):
    hass = build_hass()
    state = MagicMock()
    state.state = hvac_state
    state.attributes = {"swing_mode": swing_mode, "swing_modes": SWING_MODES, "hvac_modes": ["off", "heat", "cool"]}
    hass.states.get = MagicMock(return_value=state)
    room = make_room(thermostats=[], acs=[AC])
    room["devices"][0].update(device)
    ctrl = MPCController(hass, room, model_manager=RoomModelManager(), settings={}, has_external_sensor=True)
    return hass, ctrl


def _swing_calls(hass) -> list[str]:
    return [c[0][2]["swing_mode"] for c in hass.services.async_call.call_args_list if c[0][1] == "set_swing_mode"]


@pytest.mark.asyncio
async def test_heating_unit_gets_its_heating_position_back():
    hass, ctrl = _controller("heat", "Not set", heat_swing_mode="Lower fix", cool_swing_mode="Top fix")
    await ctrl.async_apply_swing_modes(None, set())
    assert _swing_calls(hass) == ["Lower fix"]


@pytest.mark.asyncio
async def test_cooling_unit_uses_the_cooling_position():
    hass, ctrl = _controller("cool", "Not set", heat_swing_mode="Lower fix", cool_swing_mode="Top fix")
    await ctrl.async_apply_swing_modes(None, set())
    assert _swing_calls(hass) == ["Top fix"]


@pytest.mark.asyncio
async def test_schedule_block_wins_over_the_device_setting():
    hass, ctrl = _controller("heat", "Not set", heat_swing_mode="Lower fix")
    await ctrl.async_apply_swing_modes({"heat_swing_mode": "Middle fix"}, set())
    assert _swing_calls(hass) == ["Middle fix"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hvac_state", "swing_mode", "device", "exclude"),
    [
        ("off", "Not set", {"heat_swing_mode": "Lower fix"}, set()),  # a louver command could switch it on
        ("heat", "Lower fix", {"heat_swing_mode": "Lower fix"}, set()),  # already there
        ("heat", "Not set", {}, set()),  # nothing configured
        ("heat", "Not set", {"heat_swing_mode": "Bottom fix"}, set()),  # the unit has no such position
        ("heat", "Not set", {"heat_swing_mode": "Lower fix"}, {AC}),  # coil dry or valve protection owns it
        ("dry", "Not set", {"cool_swing_mode": "Top fix"}, set()),  # dehumidifying keeps its own airflow
    ],
)
async def test_louver_is_left_alone(hvac_state, swing_mode, device, exclude):
    hass, ctrl = _controller(hvac_state, swing_mode, **device)
    await ctrl.async_apply_swing_modes(None, exclude)
    assert _swing_calls(hass) == []
