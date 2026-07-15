"""Tests for dehumidify_manager.py — RH-driven AC dry mode."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.roommind.managers.dehumidify_manager import DehumidifyManager

AC = "climate.bedroom_ac"


def _ac_state(hvac_modes=("off", "cool", "dry", "fan_only"), state="cool", **attrs):
    st = MagicMock()
    st.state = state
    st.attributes = {"hvac_modes": list(hvac_modes), **attrs}
    return st


@pytest.fixture
def dm(hass):
    hass.states.get = MagicMock(return_value=_ac_state())
    hass.services.async_call = AsyncMock()
    return DehumidifyManager(hass)


def _settings(**overrides):
    s = {"dehumidify_enabled": True, "dehumidify_humidity_threshold": 60.0}
    s.update(overrides)
    return s


# --- activation ---


def test_activates_above_threshold(dm):
    result = dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    assert result.active is True
    assert dm.is_active("bedroom") is True


def test_stays_off_below_threshold(dm):
    result = dm.evaluate("bedroom", _settings(), current_humidity=55.0, ac_eids=[AC])
    assert result.active is False


def test_disabled_never_activates(dm):
    settings = _settings(dehumidify_enabled=False)
    assert dm.evaluate("bedroom", settings, current_humidity=90.0, ac_eids=[AC]).active is False


def test_missing_humidity_does_not_activate(dm):
    assert dm.evaluate("bedroom", _settings(), current_humidity=None, ac_eids=[AC]).active is False


def test_custom_threshold_is_respected(dm):
    settings = _settings(dehumidify_humidity_threshold=70.0)
    assert dm.evaluate("bedroom", settings, current_humidity=65.0, ac_eids=[AC]).active is False
    assert dm.evaluate("bedroom", settings, current_humidity=71.0, ac_eids=[AC]).active is True


# --- capability gate ---


def test_ac_without_dry_mode_never_activates(dm, hass):
    hass.states.get = MagicMock(return_value=_ac_state(hvac_modes=("off", "cool", "heat")))
    assert dm.evaluate("bedroom", _settings(), current_humidity=90.0, ac_eids=[AC]).active is False


def test_room_without_acs_never_activates(dm):
    assert dm.evaluate("bedroom", _settings(), current_humidity=90.0, ac_eids=[]).active is False


def test_unavailable_ac_never_activates(dm, hass):
    hass.states.get = MagicMock(return_value=None)
    assert dm.evaluate("bedroom", _settings(), current_humidity=90.0, ac_eids=[AC]).active is False


# --- hysteresis + minimum run ---


def test_stays_active_inside_hysteresis_band(dm):
    """RH below the threshold but inside the band must not deactivate."""
    dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    dm._active_since["bedroom"] -= 3600  # min run satisfied
    # threshold 60, hysteresis 3 → must fall to <= 57 to clear
    assert dm.evaluate("bedroom", _settings(), current_humidity=58.5, ac_eids=[AC]).active is True


def test_deactivates_below_hysteresis_band(dm):
    dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    dm._active_since["bedroom"] -= 3600
    assert dm.evaluate("bedroom", _settings(), current_humidity=56.0, ac_eids=[AC]).active is False
    assert dm.is_active("bedroom") is False


def test_min_run_holds_mode_despite_dry_air(dm):
    """Compressor protection: don't stop right after starting."""
    dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    assert dm.evaluate("bedroom", _settings(), current_humidity=30.0, ac_eids=[AC]).active is True


def test_disabling_clears_active_state(dm):
    dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    dm.evaluate("bedroom", _settings(dehumidify_enabled=False), current_humidity=62.0, ac_eids=[AC])
    assert dm.is_active("bedroom") is False


def test_losing_dry_capability_clears_active_state(dm, hass):
    """An AC that stops reporting 'dry' (e.g. went unavailable) drops the mode."""
    dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    hass.states.get = MagicMock(return_value=None)
    assert dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC]).active is False


# --- apply ---


@pytest.mark.asyncio
async def test_apply_sends_dry_mode(dm, hass):
    await dm.async_apply("bedroom", [AC])
    hass.services.async_call.assert_awaited_once()
    args = hass.services.async_call.await_args.args
    assert args[0] == "climate"
    assert args[1] == "set_hvac_mode"
    assert args[2] == {"entity_id": AC, "hvac_mode": "dry"}


@pytest.mark.asyncio
async def test_apply_never_sends_fan_mode(dm, hass):
    """Fan speed is locked while drying — we must not touch it."""
    await dm.async_apply("bedroom", [AC])
    for call in hass.services.async_call.await_args_list:
        assert call.args[1] != "set_fan_mode"


@pytest.mark.asyncio
async def test_apply_skips_device_already_dry(dm, hass):
    hass.states.get = MagicMock(return_value=_ac_state(state="dry"))
    await dm.async_apply("bedroom", [AC])
    hass.services.async_call.assert_not_awaited()


# --- target temperature while drying ---


def _temp_calls(hass):
    return [c for c in hass.services.async_call.await_args_list if c.args[1] == "set_temperature"]


@pytest.mark.asyncio
async def test_apply_sends_scheduled_target(dm, hass):
    await dm.async_apply("bedroom", [AC], 24.0)
    calls = _temp_calls(hass)
    assert len(calls) == 1
    assert calls[0].args[2] == {"entity_id": AC, "temperature": 24.0}


@pytest.mark.asyncio
async def test_apply_without_target_leaves_setpoint_alone(dm, hass):
    await dm.async_apply("bedroom", [AC], None)
    assert _temp_calls(hass) == []


@pytest.mark.asyncio
async def test_target_is_clamped_to_device_limits(dm, hass):
    hass.states.get = MagicMock(return_value=_ac_state(min_temp=18.0, max_temp=30.0))
    await dm.async_apply("bedroom", [AC], 16.0)
    assert _temp_calls(hass)[0].args[2]["temperature"] == 18.0


@pytest.mark.asyncio
async def test_target_already_set_is_not_resent(dm, hass):
    """Don't spam the AC with the setpoint it already holds."""
    hass.states.get = MagicMock(return_value=_ac_state(state="dry", temperature=24.0))
    await dm.async_apply("bedroom", [AC], 24.0)
    assert _temp_calls(hass) == []


@pytest.mark.asyncio
async def test_target_survives_rejected_setpoint(dm, hass):
    """Some ACs refuse a setpoint in dry mode — that must not raise."""
    hass.services.async_call = AsyncMock(side_effect=Exception("not supported in dry"))
    await dm.async_apply("bedroom", [AC], 24.0)


@pytest.mark.asyncio
async def test_no_target_sent_when_mode_switch_fails(dm, hass):
    """If the AC never made it into dry, don't push a setpoint at it."""
    hass.services.async_call = AsyncMock(side_effect=Exception("offline"))
    await dm.async_apply("bedroom", [AC], 24.0)
    assert _temp_calls(hass) == []


@pytest.mark.asyncio
async def test_apply_skips_incapable_device(dm, hass):
    hass.states.get = MagicMock(return_value=_ac_state(hvac_modes=("off", "cool")))
    await dm.async_apply("bedroom", [AC])
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_survives_service_failure(dm, hass):
    hass.services.async_call = AsyncMock(side_effect=Exception("device offline"))
    await dm.async_apply("bedroom", [AC])  # must not raise


def test_remove_room_clears_state(dm):
    dm.evaluate("bedroom", _settings(), current_humidity=62.0, ac_eids=[AC])
    dm.remove_room("bedroom")
    assert dm.is_active("bedroom") is False
