"""Tests for direct valve regulation (opening-degree TRVs)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.roommind.const import TargetTemps
from custom_components.roommind.control.mpc_controller import (
    MPCController,
    async_idle_device,
    clear_command_cache,
    get_can_heat_cool,
)
from custom_components.roommind.control.thermal_model import RoomModelManager
from custom_components.roommind.control.valve_regulation import (
    ValveConfig,
    calculate_opening_degree,
    clear_valve_cache,
    get_valve_config,
)

from .conftest import build_hass, make_room

OPENING = "number.living_trv_valve_opening_degree"
CLOSING = "number.living_trv_valve_closing_degree"


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_valve_cache()
    clear_command_cache()
    yield
    clear_valve_cache()
    clear_command_cache()


def make_valve_room(**valve_overrides):
    room = make_room()
    room["devices"][0].update(
        {
            "valve_opening_entity": OPENING,
            "valve_closing_entity": CLOSING,
            **valve_overrides,
        }
    )
    return room


def build_valve_hass():
    """hass whose number entities report a full 0-100 range."""
    hass = build_hass()
    number_state = MagicMock()
    number_state.attributes = {"min": 0, "max": 100}

    climate_state = MagicMock()
    climate_state.state = "heat"
    climate_state.attributes = {"hvac_modes": ["off", "heat"], "min_temp": 4.0, "max_temp": 35.0, "temperature": None}

    hass.states.get = MagicMock(
        side_effect=lambda eid: number_state if eid.startswith("number.") else climate_state
    )
    return hass


def number_calls(hass):
    return [c[0][2] for c in hass.services.async_call.call_args_list if c[0][0] == "number"]


CONFIG = ValveConfig(
    opening_entity=OPENING,
    closing_entity=CLOSING,
    min_opening=10,
    max_opening=100,
    opening_threshold=5,
    deadband=5,
)


@pytest.mark.parametrize(
    ("demand", "expected"),
    [
        (0, 0),
        (4, 0),  # below threshold -> shut
        (5, 10),  # at threshold -> min opening
        (52.5, 55),  # midpoint of the usable range
        (100, 100),
        (140, 100),  # clamped
    ],
)
def test_calculate_opening_degree(demand, expected):
    assert calculate_opening_degree(demand, CONFIG) == expected


def test_calculate_opening_degree_respects_capped_max():
    config = ValveConfig(OPENING, None, min_opening=20, max_opening=80, opening_threshold=0, deadband=5)
    assert calculate_opening_degree(100, config) == 80
    assert calculate_opening_degree(50, config) == 50


def test_get_valve_config_absent_without_opening_entity():
    assert get_valve_config(make_room()["devices"], "climate.living_trv") is None


def test_get_valve_config_falls_back_when_min_exceeds_max():
    devices = make_valve_room(valve_min_opening=90, valve_max_opening=50, valve_opening_threshold=7)["devices"]
    config = get_valve_config(devices, "climate.living_trv")
    assert config.min_opening == 7


@pytest.mark.asyncio
async def test_heating_writes_opening_degree_not_proportional_setpoint():
    """The valve position carries the power fraction; the head is parked at boost."""
    hass = build_valve_hass()
    ctrl = MPCController(
        hass,
        make_valve_room(),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=True,
    )
    await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=0.5, current_temp=19.0)

    assert number_calls(hass) == [
        {"entity_id": OPENING, "value": 53},
        {"entity_id": CLOSING, "value": 47},
    ]
    temps = [
        c[0][2]["temperature"]
        for c in hass.services.async_call.call_args_list
        if c[0][0] == "climate" and c[0][1] == "set_temperature"
    ]
    assert temps == [30]  # boost, not the 19 + 0.5 * (30 - 19) proportional value


@pytest.mark.asyncio
async def test_heating_without_external_sensor_keeps_setpoint_control():
    hass = build_valve_hass()
    ctrl = MPCController(
        hass,
        make_valve_room(),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=False,
    )
    await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=0.5, current_temp=19.0)
    assert number_calls(hass) == []


@pytest.mark.asyncio
async def test_deadband_suppresses_small_changes_but_not_full_close():
    hass = build_valve_hass()
    room = make_valve_room()
    for fraction in (0.5, 0.52):
        ctrl = MPCController(
            hass,
            room,
            model_manager=RoomModelManager(),
            outdoor_temp=5.0,
            settings={},
            has_external_sensor=True,
        )
        await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=fraction, current_temp=19.0)
    assert len(number_calls(hass)) == 2  # second cycle moved only 2%, skipped

    await async_idle_device(hass, "climate.living_trv", room["devices"], targets=TargetTemps(heat=21.0))
    assert number_calls(hass)[-2:] == [
        {"entity_id": OPENING, "value": 0},
        {"entity_id": CLOSING, "value": 100},
    ]


@pytest.mark.asyncio
async def test_idle_closes_valve():
    hass = build_valve_hass()
    room = make_valve_room()
    await async_idle_device(hass, "climate.living_trv", room["devices"], targets=TargetTemps(heat=21.0))
    assert number_calls(hass) == [
        {"entity_id": OPENING, "value": 0},
        {"entity_id": CLOSING, "value": 100},
    ]


@pytest.mark.asyncio
async def test_opening_degree_clamped_to_entity_range():
    hass = build_valve_hass()
    capped = MagicMock()
    capped.attributes = {"min": 0, "max": 60}
    climate_state = MagicMock()
    climate_state.state = "heat"
    climate_state.attributes = {"hvac_modes": ["off", "heat"], "min_temp": 4.0, "max_temp": 35.0, "temperature": None}
    hass.states.get = MagicMock(side_effect=lambda eid: capped if eid.startswith("number.") else climate_state)

    ctrl = MPCController(
        hass,
        make_valve_room(valve_closing_entity=""),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={},
        has_external_sensor=True,
    )
    await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=1.0, current_temp=19.0)
    assert number_calls(hass) == [{"entity_id": OPENING, "value": 60}]


# --- Heating season -----------------------------------------------------


def _climate_calls(hass, service):
    return [c[0][2] for c in hass.services.async_call.call_args_list if c[0][0] == "climate" and c[0][1] == service]


@pytest.mark.asyncio
async def test_heating_season_off_idles_trvs():
    """Out of season a TRV is idled, not commanded to heat."""
    hass = build_valve_hass()
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
async def test_heating_season_off_closes_regulated_valve():
    hass = build_valve_hass()
    ctrl = MPCController(
        hass,
        make_valve_room(),
        model_manager=RoomModelManager(),
        outdoor_temp=5.0,
        settings={"heating_season": False},
        has_external_sensor=True,
    )
    await ctrl.async_apply("heating", TargetTemps(heat=21.0), power_fraction=1.0, current_temp=19.0)
    assert number_calls(hass) == [
        {"entity_id": OPENING, "value": 0},
        {"entity_id": CLOSING, "value": 100},
    ]


@pytest.mark.asyncio
async def test_heating_season_on_by_default():
    hass = build_valve_hass()
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
