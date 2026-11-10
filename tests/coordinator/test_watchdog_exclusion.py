"""A room excluded from the effectiveness watchdog is never judged and loses a standing alert."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.roommind.const import WATCHDOG_GRACE_S, WATCHDOG_NOTIFICATION_ID_PREFIX

from .conftest import _create_coordinator

AREA = "balkon"
ROOM = {"area_id": AREA, "devices": [{"entity_id": "climate.balkon_trv", "type": "trv"}]}
MODULE = "custom_components.roommind.coordinator"


async def _stall(coordinator, settings: dict, clock: list[float]) -> None:
    """Full-power heating that never moves the room, long enough for two watchdog verdicts."""
    for _ in range(3):
        await coordinator._check_mode_effectiveness(
            area_id=AREA,
            room=ROOM,
            settings=settings,
            mode="heating",
            power_fraction=1.0,
            current_temp=20.7,
            target_temp=23.0,
            controllable=True,
        )
        clock[0] += WATCHDOG_GRACE_S + 1


@pytest.mark.asyncio
async def test_excluded_room_raises_no_alert(hass, mock_config_entry):
    coordinator = _create_coordinator(hass, mock_config_entry)
    clock = [1000.0]
    with (
        patch("custom_components.roommind.managers.effectiveness_watchdog.time.time", lambda: clock[0]),
        patch(f"{MODULE}.async_create_notification") as notify,
    ):
        await _stall(coordinator, {"effectiveness_watchdog_excluded_rooms": [AREA]}, clock)
        assert notify.call_count == 0
        assert AREA not in coordinator._watchdog_alerted

        await _stall(coordinator, {}, clock)
        assert AREA in coordinator._watchdog_alerted


@pytest.mark.asyncio
async def test_excluding_a_room_takes_its_alert_down(hass, mock_config_entry):
    coordinator = _create_coordinator(hass, mock_config_entry)
    coordinator._watchdog_alerted.add(AREA)
    with patch(f"{MODULE}.async_dismiss_notification") as dismiss:
        await coordinator._check_mode_effectiveness(
            area_id=AREA,
            room=ROOM,
            settings={"effectiveness_watchdog_excluded_rooms": [AREA]},
            mode="heating",
            power_fraction=1.0,
            current_temp=20.7,
            target_temp=23.0,
            controllable=True,
        )
    dismiss.assert_called_once_with(hass, WATCHDOG_NOTIFICATION_ID_PREFIX + AREA)
    assert AREA not in coordinator._watchdog_alerted
