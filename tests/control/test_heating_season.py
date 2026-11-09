"""Tests for the heating season switch: TRVs idle out of season, heating ACs keep working."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import TargetTemps
from custom_components.roommind.control.mpc_controller import (
    MPCController,
    clear_command_cache,
    get_can_heat_cool,
)
from custom_components.roommind.control.thermal_model import RoomModelManager

from .conftest import build_hass, make_room


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_command_cache()
    yield
    clear_command_cache()


def build_trv_hass():
    hass = build_hass()
    climate_state = MagicMock()
    climate_state.state = "heat"
    climate_state.attributes = {"hvac_modes": ["off", "heat"], "min_temp": 4.0, "max_temp": 35.0, "temperature": None}
    hass.states.get = MagicMock(return_value=climate_state)
    return hass


def _climate_calls(hass, service):
    return [c[0][2] for c in hass.services.async_call.call_args_list if c[0][0] == "climate" and c[0][1] == service]


@pytest.mark.asyncio
async def test_heating_season_off_idles_trvs():
    """Out of season a TRV is idled, not commanded to heat."""
    hass = build_trv_hass()
    ctrl = MPCController(
        hass,
        make_room(),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={"heating_season": False},
        has_external_sensor=True,
    )
    await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=1.0, current_temp=19.0)
    assert _climate_calls(hass, "set_hvac_mode") == [{"entity_id": "climate.living_trv", "hvac_mode": "off"}]
    # turn-off also drops the setpoint to min_temp; no heat command is sent
    assert _climate_calls(hass, "set_temperature") == [{"entity_id": "climate.living_trv", "temperature": 4.0}]


@pytest.mark.asyncio
async def test_heating_season_on_by_default():
    hass = build_trv_hass()
    ctrl = MPCController(
        hass,
        make_room(),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=True,
    )
    await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=1.0, current_temp=19.0)
    assert _climate_calls(hass, "set_temperature") != []


def test_can_heat_drops_trvs_out_of_season():
    room = make_room()
    assert get_can_heat_cool(room, heating_season=True)[0] is True
    assert get_can_heat_cool(room, heating_season=False)[0] is False


def test_can_heat_keeps_heating_acs_out_of_season():
    """ACs that heat are unaffected — they are exactly what covers the off-season."""
    room = make_room(acs=["climate.ac"])
    assert get_can_heat_cool(room, acs_can_heat=True, heating_season=False)[0] is True
