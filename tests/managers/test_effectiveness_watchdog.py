"""Tests for EffectivenessWatchdog."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.roommind.const import WATCHDOG_GRACE_S
from custom_components.roommind.managers.effectiveness_watchdog import (
    EffectivenessWatchdog,
    WatchdogVerdict,
)

MODULE = "custom_components.roommind.managers.effectiveness_watchdog.time"


def _run(
    watchdog: EffectivenessWatchdog,
    at: float,
    temp: float,
    mode: str = "cooling",
    target: float | None = None,
) -> WatchdogVerdict:
    if target is None:
        target = 20.0 if mode == "cooling" else 28.0
    with patch(MODULE) as mock_time:
        mock_time.time.return_value = at
        return watchdog.evaluate("bedroom", mode, temp, 1.0, target, controllable=True)


def test_first_cycle_starts_an_episode():
    watchdog = EffectivenessWatchdog()
    assert _run(watchdog, 1000.0, 24.5) is WatchdogVerdict.OK


def test_no_verdict_before_the_grace_period():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S - 1, 24.5) is WatchdogVerdict.OK


def test_stalled_cooling_asks_for_a_power_cycle():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.6) is WatchdogVerdict.REARM


def test_stall_surviving_the_power_cycle_alerts_once():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.6)
    second = 1000.0 + 2 * WATCHDOG_GRACE_S + 1
    assert _run(watchdog, second, 24.7) is WatchdogVerdict.ALERT
    assert _run(watchdog, second + WATCHDOG_GRACE_S + 1, 24.8) is WatchdogVerdict.OK


def test_a_responding_room_never_fires():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.2) is WatchdogVerdict.OK


def test_heating_progress_is_measured_upwards():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 20.0, mode="heating")
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 20.4, mode="heating") is WatchdogVerdict.OK
    # Progress re-baselines the episode, so the stall is measured from there.
    assert _run(watchdog, 1000.0 + 2 * WATCHDOG_GRACE_S, 20.4, mode="heating") is WatchdogVerdict.REARM


def test_a_throttled_device_resets_the_episode():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    with patch(MODULE) as mock_time:
        mock_time.time.return_value = 1000.0 + WATCHDOG_GRACE_S
        assert watchdog.evaluate("bedroom", "cooling", 24.6, 0.3, 20.0, controllable=True) is WatchdogVerdict.OK
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S + 1, 24.6) is WatchdogVerdict.OK


def test_an_open_window_resets_the_episode():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    with patch(MODULE) as mock_time:
        mock_time.time.return_value = 1000.0 + WATCHDOG_GRACE_S
        assert watchdog.evaluate("bedroom", "cooling", 24.6, 1.0, 20.0, controllable=False) is WatchdogVerdict.OK
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S + 1, 24.6) is WatchdogVerdict.OK


def test_switching_mode_restarts_the_measurement():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    _run(watchdog, 1000.0 + 60, 24.5, mode="heating")
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.5, mode="heating") is WatchdogVerdict.OK


def test_missing_sensor_reading_resets_the_episode():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    with patch(MODULE) as mock_time:
        mock_time.time.return_value = 1000.0 + 60
        assert watchdog.evaluate("bedroom", "cooling", None, 1.0, 20.0, controllable=True) is WatchdogVerdict.OK
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.6) is WatchdogVerdict.OK


def test_get_state_reports_the_stall():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.5)
    _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.6)
    with patch(MODULE) as mock_time:
        mock_time.time.return_value = 1000.0 + WATCHDOG_GRACE_S + 120
        state = watchdog.get_state("bedroom", 24.6)
    assert state.stalled is True
    assert state.stalled_for_s == 120
    assert state.progress_c == 0.0


def test_room_holding_near_target_is_not_a_stall():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 24.3, target=24.0)
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 24.3, target=24.0) is WatchdogVerdict.OK
    assert _run(watchdog, 1000.0 + 3 * WATCHDOG_GRACE_S, 24.3, target=24.0) is WatchdogVerdict.OK


def test_heating_past_target_is_not_a_stall():
    watchdog = EffectivenessWatchdog()
    _run(watchdog, 1000.0, 21.2, mode="heating", target=21.0)
    assert _run(watchdog, 1000.0 + WATCHDOG_GRACE_S, 21.2, mode="heating", target=21.0) is WatchdogVerdict.OK
