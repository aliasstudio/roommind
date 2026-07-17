"""Coordinator-level tests for active dehumidification (AC dry mode).

Dehumidification takes the AC away from the MPC while it runs, so the two
things that must hold here are that the normal command path stands down, and
that the EKF is told to skip the period — a dry AC cools the room as a side
effect, and training on that as "idle" would teach the model that the room
cools on its own.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import (
    SAMPLE_ROOM,
    _create_coordinator,
    _make_store_mock,
    make_mock_states_get,
)

AC = "climate.living_room_ac"

DEHUMIDIFY_ROOM = {
    **SAMPLE_ROOM,
    "thermostats": [],
    "acs": [AC],
    "devices": [{"entity_id": AC, "type": "ac", "role": "auto", "heating_system_type": ""}],
    "climate_mode": "cool_only",
}

DEHUMIDIFY_SETTINGS = {"dehumidify_enabled": True, "dehumidify_humidity_threshold": 60.0}


def _states_get(humidity="65.0", hvac_modes=("off", "cool", "dry", "fan_only"), ac_state="cool", fan_mode="low"):
    return make_mock_states_get(
        temp="24.0",
        humidity=humidity,
        outdoor_temp="25.0",
        extra={AC: (ac_state, {"hvac_modes": list(hvac_modes), "fan_modes": ["low", "high"], "fan_mode": fan_mode})},
    )


async def _run(hass, mock_config_entry, states_get, room=None, settings=None):
    """Run one coordinator cycle; return (room_state, training_kwargs, services)."""
    room = room or DEHUMIDIFY_ROOM
    store = _make_store_mock(
        {room["area_id"]: room},
        settings=DEHUMIDIFY_SETTINGS if settings is None else settings,
    )
    hass.data = {"roommind": {"store": store}}
    hass.states.get = MagicMock(side_effect=states_get)
    hass.services.async_call = AsyncMock()

    coordinator = _create_coordinator(hass, mock_config_entry)
    training_mock = MagicMock()
    coordinator._ekf_training.process = training_mock

    data = await coordinator._async_update_data()

    training_kwargs = training_mock.call_args.kwargs if training_mock.called else None
    return data["rooms"][room["area_id"]], training_kwargs, hass.services.async_call


def _calls(services, service_name):
    return [c for c in services.await_args_list if c.args[1] == service_name]


class TestDehumidifyActivation:
    @pytest.mark.asyncio
    async def test_humid_room_goes_into_dry_mode(self, hass, mock_config_entry):
        state, _, services = await _run(hass, mock_config_entry, _states_get(humidity="65.0"))

        assert state["dehumidifying"] is True
        dry = [c for c in _calls(services, "set_hvac_mode") if c.args[2].get("hvac_mode") == "dry"]
        assert len(dry) == 1
        assert dry[0].args[2]["entity_id"] == AC

    @pytest.mark.asyncio
    async def test_dry_room_stays_on_the_normal_path(self, hass, mock_config_entry):
        state, _, services = await _run(hass, mock_config_entry, _states_get(humidity="50.0"))

        assert state["dehumidifying"] is False
        assert not [c for c in _calls(services, "set_hvac_mode") if c.args[2].get("hvac_mode") == "dry"]

    @pytest.mark.asyncio
    async def test_ac_without_dry_support_is_left_alone(self, hass, mock_config_entry):
        state, _, services = await _run(
            hass,
            mock_config_entry,
            _states_get(humidity="65.0", hvac_modes=("off", "cool")),
        )

        assert state["dehumidifying"] is False
        assert not [c for c in _calls(services, "set_hvac_mode") if c.args[2].get("hvac_mode") == "dry"]

    @pytest.mark.asyncio
    async def test_disabled_setting_ignores_humidity(self, hass, mock_config_entry):
        state, _, services = await _run(
            hass,
            mock_config_entry,
            _states_get(humidity="90.0"),
            settings={"dehumidify_enabled": False},
        )

        assert state["dehumidifying"] is False
        assert not [c for c in _calls(services, "set_hvac_mode") if c.args[2].get("hvac_mode") == "dry"]


class TestDehumidifyOwnsTheDevice:
    @pytest.mark.asyncio
    async def test_fan_not_capped_when_no_max_configured(self, hass, mock_config_entry):
        """With no fan_max_mode configured, the AC keeps its own dry fan."""
        _, _, services = await _run(hass, mock_config_entry, _states_get(humidity="65.0"))

        assert _calls(services, "set_fan_mode") == []

    @pytest.mark.asyncio
    async def test_dry_fan_capped_to_device_fan_max_mode(self, hass, mock_config_entry):
        """A device fan_max_mode caps the dry-mode fan so it isn't loud/cold."""
        room = {
            **DEHUMIDIFY_ROOM,
            "devices": [
                {
                    "entity_id": AC,
                    "type": "ac",
                    "role": "auto",
                    "fan_control": "auto",
                    "fan_max_mode": "low",
                }
            ],
        }
        _, _, services = await _run(
            hass, mock_config_entry, _states_get(humidity="65.0", fan_mode="high"), room=room
        )

        fan = _calls(services, "set_fan_mode")
        assert len(fan) == 1
        assert fan[0].args[2] == {"entity_id": AC, "fan_mode": "low"}

    @pytest.mark.asyncio
    async def test_mode_reads_idle_so_mpc_intent_does_not_leak(self, hass, mock_config_entry):
        state, _, _ = await _run(hass, mock_config_entry, _states_get(humidity="65.0"))

        assert state["mode"] == "idle"
        assert state["heating_power"] == 0

    @pytest.mark.asyncio
    async def test_ac_dries_toward_the_scheduled_target(self, hass, mock_config_entry):
        """The scheduled cool target reaches the AC — not the MPC's boost setpoint."""
        _, _, services = await _run(hass, mock_config_entry, _states_get(humidity="65.0"))

        temps = _calls(services, "set_temperature")
        assert len(temps) == 1
        # comfort_cool from SAMPLE_ROOM, not min_temp
        assert temps[0].args[2] == {"entity_id": AC, "temperature": 24.0}


class TestDehumidifyTraining:
    @pytest.mark.asyncio
    async def test_ekf_is_told_to_skip_the_dry_period(self, hass, mock_config_entry):
        _, training, _ = await _run(hass, mock_config_entry, _states_get(humidity="65.0"))

        assert training is not None
        assert training["dehumidifying"] is True

    @pytest.mark.asyncio
    async def test_normal_period_trains_as_usual(self, hass, mock_config_entry):
        _, training, _ = await _run(hass, mock_config_entry, _states_get(humidity="50.0"))

        assert training is not None
        assert training["dehumidifying"] is False
