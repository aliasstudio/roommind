"""Tests for custom_components.roommind.utils.device_utils."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roommind.utils.device_utils import (
    COIL_DRY_FAN_MODE_KEEP,
    DEFAULT_COIL_DRY_FAN_MODE,
    DEFAULT_COIL_DRY_MINUTES,
    SETPOINT_MODE_PROPORTIONAL,
    VALID_DEVICE_TYPES,
    VALID_HEATING_SYSTEM_TYPES,
    build_rooms_devices_map,
    devices_to_legacy,
    ensure_room_has_devices,
    get_ac_eids,
    get_all_entity_ids,
    get_coil_dry_config,
    get_cool_setpoint_floor,
    get_dehumidify_target,
    get_device_by_eid,
    get_direct_setpoint_eids,
    get_entity_ids_by_type,
    get_idle_action,
    get_room_heating_system_type,
    get_setpoint_offset,
    get_setpoint_offsets,
    get_trv_eids,
    has_reliable_hvac_modes,
    is_ac_type,
    is_trv_type,
    legacy_to_devices,
    migrate_heat_pump_devices,
    room_contributes_to_group,
    scale_cool_floor_by_airflow,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_valid_device_types():
    assert VALID_DEVICE_TYPES == {"trv", "ac"}


def test_device_role_auto_constant():
    from custom_components.roommind.utils.device_utils import DEVICE_ROLE_AUTO

    assert DEVICE_ROLE_AUTO == "auto"


def test_valid_heating_system_types():
    assert VALID_HEATING_SYSTEM_TYPES == {"", "radiator", "underfloor"}


# ---------------------------------------------------------------------------
# legacy_to_devices
# ---------------------------------------------------------------------------


def test_legacy_to_devices_basic():
    devices = legacy_to_devices(
        ["climate.trv1", "climate.trv2"],
        ["climate.ac1"],
    )
    assert len(devices) == 3
    assert devices[0] == {
        "entity_id": "climate.trv1",
        "type": "trv",
        "role": "auto",
        "heating_system_type": "",
        "idle_action": "off",
        "idle_fan_mode": "low",
        "active_fan_mode": "",
        "setpoint_mode": "proportional",
        "coil_dry": "inherit",
        "coil_dry_minutes": 0,
        "coil_dry_mode": "",
        "coil_dry_fan_mode": "",
    }
    assert devices[2]["type"] == "ac"
    assert devices[2]["heating_system_type"] == ""


def test_legacy_to_devices_heating_system_type_transferred_to_trvs():
    devices = legacy_to_devices(
        ["climate.trv1"],
        ["climate.ac1"],
        heating_system_type="underfloor",
    )
    assert devices[0]["heating_system_type"] == "underfloor"
    assert devices[1]["heating_system_type"] == ""


def test_legacy_to_devices_empty_lists():
    assert legacy_to_devices([], []) == []


# ---------------------------------------------------------------------------
# devices_to_legacy
# ---------------------------------------------------------------------------


def test_devices_to_legacy_basic():
    devices = [
        {"entity_id": "climate.trv1", "type": "trv"},
        {"entity_id": "climate.ac1", "type": "ac"},
    ]
    thermostats, acs = devices_to_legacy(devices)
    assert thermostats == ["climate.trv1"]
    assert acs == ["climate.ac1"]


def test_devices_to_legacy_unknown_type_skipped():
    """Unknown device types are logged and skipped, not silently added to acs."""
    devices = [{"entity_id": "climate.mystery", "type": "unknown_thing"}]
    thermostats, acs = devices_to_legacy(devices)
    assert thermostats == []
    assert acs == []


def test_devices_to_legacy_missing_entity_id_skipped():
    """Devices without entity_id are logged and skipped."""
    devices = [{"type": "trv"}, {"entity_id": "climate.ok", "type": "ac"}]
    thermostats, acs = devices_to_legacy(devices)
    assert thermostats == []
    assert acs == ["climate.ok"]


def test_round_trip_legacy_devices_legacy():
    original_thermostats = ["climate.trv1", "climate.trv2"]
    original_acs = ["climate.ac1"]
    devices = legacy_to_devices(original_thermostats, original_acs, "radiator")
    thermostats, acs = devices_to_legacy(devices)
    assert thermostats == original_thermostats
    assert acs == original_acs


# ---------------------------------------------------------------------------
# ensure_room_has_devices
# ---------------------------------------------------------------------------


def test_ensure_room_has_devices_migration_from_legacy():
    room = {
        "thermostats": ["climate.trv1"],
        "acs": ["climate.ac1"],
        "heating_system_type": "radiator",
    }
    result = ensure_room_has_devices(room)
    assert result is room  # mutates in place
    assert len(room["devices"]) == 2
    assert room["devices"][0]["type"] == "trv"
    assert room["devices"][0]["heating_system_type"] == "radiator"
    assert room["devices"][1]["type"] == "ac"
    assert room["thermostats"] == ["climate.trv1"]
    assert room["acs"] == ["climate.ac1"]
    assert room["heating_system_type"] == "radiator"


def test_ensure_room_has_devices_idempotent():
    room = {
        "thermostats": ["climate.trv1"],
        "acs": [],
        "heating_system_type": "underfloor",
    }
    ensure_room_has_devices(room)
    devices_snapshot = list(room["devices"])
    ensure_room_has_devices(room)
    assert room["devices"] == devices_snapshot


def test_ensure_room_has_devices_consistent_devices_regenerates_legacy():
    """When devices match legacy, devices remain source of truth."""
    room = {
        "devices": [
            {"entity_id": "climate.trv1", "type": "trv", "role": "auto", "heating_system_type": "radiator"},
            {"entity_id": "climate.ac1", "type": "ac", "role": "auto", "heating_system_type": ""},
        ],
        "thermostats": ["climate.trv1"],
        "acs": ["climate.ac1"],
        "heating_system_type": "stale",
    }
    ensure_room_has_devices(room)
    assert room["thermostats"] == ["climate.trv1"]
    assert room["acs"] == ["climate.ac1"]
    assert room["heating_system_type"] == "radiator"


def test_ensure_room_has_devices_downgrade_recovery():
    """When legacy fields were edited during downgrade, prefer legacy over stale devices."""
    room = {
        "devices": [
            {"entity_id": "climate.old_trv", "type": "trv", "role": "auto", "heating_system_type": ""},
        ],
        "thermostats": ["climate.new_trv"],
        "acs": ["climate.new_ac"],
        "heating_system_type": "radiator",
    }
    ensure_room_has_devices(room)
    # Legacy was edited during downgrade -> devices re-generated from legacy
    assert len(room["devices"]) == 2
    assert room["devices"][0]["entity_id"] == "climate.new_trv"
    assert room["devices"][0]["type"] == "trv"
    assert room["devices"][0]["heating_system_type"] == "radiator"
    assert room["devices"][1]["entity_id"] == "climate.new_ac"
    assert room["devices"][1]["type"] == "ac"
    assert room["thermostats"] == ["climate.new_trv"]
    assert room["acs"] == ["climate.new_ac"]


def test_ensure_room_has_devices_derives_heating_system_type():
    room = {
        "devices": [
            {"entity_id": "climate.trv1", "type": "trv", "role": "auto", "heating_system_type": "underfloor"},
            {"entity_id": "climate.trv2", "type": "trv", "role": "auto", "heating_system_type": "radiator"},
        ],
        "thermostats": ["climate.trv1", "climate.trv2"],
        "acs": [],
    }
    ensure_room_has_devices(room)
    assert room["heating_system_type"] == "underfloor"


def test_ensure_room_has_devices_empty_room():
    room = {}
    ensure_room_has_devices(room)
    assert room["devices"] == []
    assert room["thermostats"] == []
    assert room["acs"] == []
    assert room["heating_system_type"] == ""


# ---------------------------------------------------------------------------
# get_room_heating_system_type
# ---------------------------------------------------------------------------


def test_get_room_heating_system_type_single():
    devices = [{"type": "trv", "heating_system_type": "radiator"}]
    assert get_room_heating_system_type(devices) == "radiator"


def test_get_room_heating_system_type_mixed_underfloor_wins():
    devices = [
        {"type": "trv", "heating_system_type": "radiator"},
        {"type": "trv", "heating_system_type": "underfloor"},
    ]
    assert get_room_heating_system_type(devices) == "underfloor"


def test_get_room_heating_system_type_only_trvs_considered():
    devices = [
        {"type": "ac", "heating_system_type": "underfloor"},
        {"type": "trv", "heating_system_type": "radiator"},
    ]
    assert get_room_heating_system_type(devices) == "radiator"


def test_get_room_heating_system_type_empty():
    assert get_room_heating_system_type([]) == ""


def test_get_room_heating_system_type_no_trvs():
    devices = [{"type": "ac", "heating_system_type": "underfloor"}]
    assert get_room_heating_system_type(devices) == ""


# ---------------------------------------------------------------------------
# get_all_entity_ids / get_trv_eids / get_ac_eids
# ---------------------------------------------------------------------------

_MIXED_DEVICES = [
    {"entity_id": "climate.trv1", "type": "trv"},
    {"entity_id": "climate.ac1", "type": "ac"},
    {"entity_id": "climate.ac2", "type": "ac"},
]


def test_get_all_entity_ids():
    # TRVs first for deterministic ordering, then others
    assert get_all_entity_ids(_MIXED_DEVICES) == [
        "climate.trv1",
        "climate.ac1",
        "climate.ac2",
    ]


def test_get_all_entity_ids_trvs_first():
    """Even when ACs come first in the array, TRVs are returned first."""
    devices = [
        {"entity_id": "climate.ac1", "type": "ac"},
        {"entity_id": "climate.trv1", "type": "trv"},
        {"entity_id": "climate.ac2", "type": "ac"},
    ]
    assert get_all_entity_ids(devices) == [
        "climate.trv1",
        "climate.ac1",
        "climate.ac2",
    ]


def test_get_all_entity_ids_skips_missing_entity_id():
    devices = [{"type": "trv"}, {"entity_id": "climate.ok", "type": "ac"}]
    assert get_all_entity_ids(devices) == ["climate.ok"]


def test_get_trv_eids():
    assert get_trv_eids(_MIXED_DEVICES) == ["climate.trv1"]


def test_get_ac_eids():
    assert get_ac_eids(_MIXED_DEVICES) == ["climate.ac1", "climate.ac2"]


def test_get_entity_ids_by_type_multi():
    result = get_entity_ids_by_type(_MIXED_DEVICES, "trv", "ac")
    assert result == ["climate.trv1", "climate.ac1", "climate.ac2"]


def test_get_entity_ids_by_type_no_match():
    assert get_entity_ids_by_type(_MIXED_DEVICES, "nonexistent") == []


# ---------------------------------------------------------------------------
# get_device_by_eid
# ---------------------------------------------------------------------------


def test_get_device_by_eid_found():
    device = get_device_by_eid(_MIXED_DEVICES, "climate.ac1")
    assert device is not None
    assert device["type"] == "ac"


def test_get_device_by_eid_not_found():
    assert get_device_by_eid(_MIXED_DEVICES, "climate.nope") is None


# ---------------------------------------------------------------------------
# is_trv_type / is_ac_type
# ---------------------------------------------------------------------------


def test_is_trv_type_true():
    assert is_trv_type({"type": "trv"}) is True


def test_is_trv_type_false():
    assert is_trv_type({"type": "ac"}) is False


def test_is_ac_type_ac():
    assert is_ac_type({"type": "ac"}) is True


def test_is_ac_type_trv():
    assert is_ac_type({"type": "trv"}) is False


# ---------------------------------------------------------------------------
# migrate_heat_pump_devices
# ---------------------------------------------------------------------------


def test_migrate_heat_pump_devices():
    devices = [
        {"entity_id": "climate.hp1", "type": "heat_pump", "role": "auto"},
        {"entity_id": "climate.trv1", "type": "trv", "role": "auto"},
        {"entity_id": "climate.ac1", "type": "ac", "role": "auto"},
    ]
    result = migrate_heat_pump_devices(devices)
    assert result is True
    assert devices[0]["type"] == "ac"
    assert devices[1]["type"] == "trv"
    assert devices[2]["type"] == "ac"


def test_migrate_heat_pump_devices_no_change():
    devices = [{"entity_id": "climate.trv1", "type": "trv"}]
    result = migrate_heat_pump_devices(devices)
    assert result is False


# ---------------------------------------------------------------------------
# get_idle_action
# ---------------------------------------------------------------------------


def test_legacy_to_devices_includes_idle_defaults():
    """legacy_to_devices produces devices with idle_action='off' and idle_fan_mode='low'."""
    devices = legacy_to_devices(["climate.x"], [])
    assert len(devices) == 1
    assert devices[0]["idle_action"] == "off"
    assert devices[0]["idle_fan_mode"] == "low"


def test_get_idle_action_defaults():
    """Empty devices list returns default ('off', 'low')."""
    action, fan_mode = get_idle_action([], "climate.nonexistent")
    assert action == "off"
    assert fan_mode == "low"


def test_get_idle_action_configured():
    """Device with idle_action='fan_only' and idle_fan_mode='auto' returns configured values."""
    devices = [
        {
            "entity_id": "climate.ac1",
            "type": "ac",
            "role": "auto",
            "idle_action": "fan_only",
            "idle_fan_mode": "auto",
        }
    ]
    action, fan_mode = get_idle_action(devices, "climate.ac1")
    assert action == "fan_only"
    assert fan_mode == "auto"


def test_build_rooms_devices_map_basic():
    """Maps every entity_id to its room's devices list across all rooms."""
    rooms = {
        "living_room": {
            "devices": [
                {"entity_id": "climate.trv1", "type": "trv"},
                {"entity_id": "climate.ac1", "type": "ac"},
            ],
        },
        "bedroom": {
            "devices": [
                {"entity_id": "climate.trv2", "type": "trv"},
            ],
        },
    }
    mapping = build_rooms_devices_map(rooms)
    assert set(mapping) == {"climate.trv1", "climate.ac1", "climate.trv2"}
    # Entities from the same room share the same devices list reference
    assert mapping["climate.trv1"] is mapping["climate.ac1"]
    assert mapping["climate.trv2"] is not mapping["climate.trv1"]


def test_build_rooms_devices_map_empty_and_missing_fields():
    """Handles empty rooms, missing devices key, and devices without entity_id."""
    rooms = {
        "empty": {},
        "no_devices": {"devices": []},
        "bad_entry": {"devices": [{"type": "trv"}, {"entity_id": "climate.ok", "type": "trv"}]},
    }
    mapping = build_rooms_devices_map(rooms)
    assert mapping == {"climate.ok": rooms["bad_entry"]["devices"]}


# ---------------------------------------------------------------------------
# has_reliable_hvac_modes
# ---------------------------------------------------------------------------


class TestHasReliableHvacModes:
    """Unit tests for has_reliable_hvac_modes."""

    def test_none_state(self):
        assert has_reliable_hvac_modes(None) is False

    def test_device_on_with_active_modes_reliable(self):
        """Device with active modes in list is reliable regardless of state."""
        state = MagicMock()
        state.state = "heat"
        state.attributes = {"hvac_modes": ["heat"]}
        assert has_reliable_hvac_modes(state) is True

    def test_device_fan_only_no_active_modes_unreliable(self):
        """Device in fan_only with no active modes is unreliable (#100)."""
        state = MagicMock()
        state.state = "fan_only"
        state.attributes = {"hvac_modes": ["off", "fan_only"]}
        assert has_reliable_hvac_modes(state) is False

    def test_device_on_without_active_modes_unreliable(self):
        """Device in heat state but modes list has no active modes (broken integration)."""
        state = MagicMock()
        state.state = "heat"
        state.attributes = {"hvac_modes": ["off", "fan_only"]}
        assert has_reliable_hvac_modes(state) is False

    def test_device_fan_only_with_active_modes_reliable(self):
        """Device in fan_only with active modes in list is reliable."""
        state = MagicMock()
        state.state = "fan_only"
        state.attributes = {"hvac_modes": ["off", "heat", "cool", "fan_only"]}
        assert has_reliable_hvac_modes(state) is True

    def test_device_off_with_active_modes_reliable(self):
        """Off device with heat+cool in modes is reliable."""
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": ["off", "heat", "cool"]}
        assert has_reliable_hvac_modes(state) is True

    def test_device_off_cool_only_reliable(self):
        """Off device with 'cool' is reliable (genuinely cooling-only AC)."""
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": ["off", "cool"]}
        assert has_reliable_hvac_modes(state) is True

    def test_device_off_no_active_modes_unreliable(self):
        """Off device with only 'off' and 'fan_only' is unreliable (#100)."""
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": ["off", "fan_only"]}
        assert has_reliable_hvac_modes(state) is False

    def test_device_off_empty_modes_unreliable(self):
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": []}
        assert has_reliable_hvac_modes(state) is False

    def test_device_off_no_modes_attr_unreliable(self):
        state = MagicMock()
        state.state = "off"
        state.attributes = {}
        assert has_reliable_hvac_modes(state) is False

    def test_device_off_only_off_unreliable(self):
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": ["off"]}
        assert has_reliable_hvac_modes(state) is False

    def test_device_off_with_auto_reliable(self):
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": ["off", "auto"]}
        assert has_reliable_hvac_modes(state) is True

    def test_device_off_modes_none_unreliable(self):
        """hvac_modes=None should not crash (defensive against bad integrations)."""
        state = MagicMock()
        state.state = "off"
        state.attributes = {"hvac_modes": None}
        assert has_reliable_hvac_modes(state) is False


# ---------------------------------------------------------------------------
# get_direct_setpoint_eids
# ---------------------------------------------------------------------------


class TestGetDirectSetpointEids:
    def test_mixed_devices(self):
        devices = [
            {"entity_id": "climate.trv1", "type": "trv", "setpoint_mode": "proportional"},
            {"entity_id": "climate.heater", "type": "trv", "setpoint_mode": "direct"},
            {"entity_id": "climate.ac1", "type": "ac", "setpoint_mode": "direct"},
        ]
        result = get_direct_setpoint_eids(devices)
        assert result == {"climate.heater", "climate.ac1"}

    def test_all_proportional(self):
        devices = [
            {"entity_id": "climate.trv1", "type": "trv", "setpoint_mode": "proportional"},
        ]
        assert get_direct_setpoint_eids(devices) == set()

    def test_missing_field_defaults_to_not_direct(self):
        """Devices without setpoint_mode field are NOT in direct set."""
        devices = [
            {"entity_id": "climate.trv1", "type": "trv"},
        ]
        assert get_direct_setpoint_eids(devices) == set()

    def test_empty_devices(self):
        assert get_direct_setpoint_eids([]) == set()

    def test_missing_entity_id_skipped(self):
        devices = [
            {"type": "trv", "setpoint_mode": "direct"},
            {"entity_id": "climate.ok", "type": "trv", "setpoint_mode": "direct"},
        ]
        assert get_direct_setpoint_eids(devices) == {"climate.ok"}


# ---------------------------------------------------------------------------
# legacy_to_devices includes setpoint_mode
# ---------------------------------------------------------------------------


class TestLegacyToDevicesSetpointMode:
    def test_trv_gets_proportional(self):
        devices = legacy_to_devices(["climate.trv1"], [])
        assert devices[0]["setpoint_mode"] == SETPOINT_MODE_PROPORTIONAL

    def test_ac_gets_proportional(self):
        devices = legacy_to_devices([], ["climate.ac1"])
        assert devices[0]["setpoint_mode"] == SETPOINT_MODE_PROPORTIONAL


# ---------------------------------------------------------------------------
# room_contributes_to_group
# ---------------------------------------------------------------------------


class TestRoomContributesToGroup:
    """Tests for the orchestration-aware master-demand filter (#168)."""

    TRV = {"entity_id": "climate.trv1", "type": "trv"}
    AC = {"entity_id": "climate.ac1", "type": "ac"}

    def test_none_always_contributes(self):
        assert room_contributes_to_group([self.TRV], {"climate.trv1"}, None) is True

    def test_none_with_empty_devices(self):
        assert room_contributes_to_group([], set(), None) is True

    def test_literal_none_string_returns_false(self):
        assert room_contributes_to_group([self.TRV], {"climate.trv1"}, "none") is False

    def test_both_always_contributes(self):
        assert room_contributes_to_group([self.TRV], {"climate.trv1"}, "both") is True

    def test_both_even_without_member_match(self):
        # "both" short-circuits before type-checking.
        assert room_contributes_to_group([self.TRV], set(), "both") is True

    def test_primary_with_trv_member_in_group(self):
        assert room_contributes_to_group([self.TRV, self.AC], {"climate.trv1"}, "primary") is True

    def test_primary_with_only_ac_member(self):
        # Only AC is in the compressor group -> not relevant for "primary".
        assert room_contributes_to_group([self.TRV, self.AC], {"climate.ac1"}, "primary") is False

    def test_primary_without_any_member(self):
        assert room_contributes_to_group([self.TRV], set(), "primary") is False

    def test_secondary_with_ac_member_in_group(self):
        assert room_contributes_to_group([self.TRV, self.AC], {"climate.ac1"}, "secondary") is True

    def test_secondary_with_only_trv_member(self):
        assert room_contributes_to_group([self.TRV, self.AC], {"climate.trv1"}, "secondary") is False

    def test_mixed_group_primary_trv_matches(self):
        assert (
            room_contributes_to_group(
                [self.TRV, self.AC],
                {"climate.trv1", "climate.ac1"},
                "primary",
            )
            is True
        )

    def test_mixed_group_secondary_ac_matches(self):
        assert (
            room_contributes_to_group(
                [self.TRV, self.AC],
                {"climate.trv1", "climate.ac1"},
                "secondary",
            )
            is True
        )

    def test_device_without_type_field_primary(self):
        # Defensive: devices without "type" must not accidentally match.
        devices = [{"entity_id": "climate.x"}]
        assert room_contributes_to_group(devices, {"climate.x"}, "primary") is False

    def test_unknown_active_sources_value(self):
        # Fail-safe: unknown orchestration state -> master stays idle.
        assert room_contributes_to_group([self.TRV], {"climate.trv1"}, "invalid") is False


# ---------------------------------------------------------------------------
# apply_idle_override — schedule-driven idle action overlay
# ---------------------------------------------------------------------------


class TestApplyIdleOverride:
    AC = {"entity_id": "climate.ac1", "type": "ac", "idle_action": "off", "idle_fan_mode": "low"}
    TRV = {"entity_id": "climate.trv1", "type": "trv", "idle_action": "low"}

    def test_none_override_returns_same_object(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        devices = [self.AC, self.TRV]
        assert apply_idle_override(devices, None) is devices
        assert apply_idle_override(devices, {}) is devices

    def test_fan_only_applies_to_ac_not_trv(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        out = apply_idle_override(
            [self.AC, self.TRV],
            {"ac": {"idle_action": "fan_only", "idle_fan_mode": "Mute"}, "trv": {"idle_action": "fan_only"}},
        )
        assert out[0]["idle_action"] == "fan_only"
        assert out[0]["idle_fan_mode"] == "Mute"
        # fan_only is not applicable to a TRV -> keeps its configured action
        assert out[1]["idle_action"] == "low"

    def test_off_applies_to_both(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        out = apply_idle_override(
            [self.AC, self.TRV], {"ac": {"idle_action": "off"}, "trv": {"idle_action": "off"}}
        )
        assert out[0]["idle_action"] == "off"
        assert out[1]["idle_action"] == "off"

    def test_low_applies_to_trv_not_ac(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        out = apply_idle_override(
            [self.AC, self.TRV], {"ac": {"idle_action": "low"}, "trv": {"idle_action": "low"}}
        )
        assert out[0]["idle_action"] == "off"  # AC unchanged (low not applicable)
        assert out[1]["idle_action"] == "low"

    def test_per_type_overrides_are_independent(self):
        """The AC can go off overnight while the valve only drops to low."""
        from custom_components.roommind.utils.device_utils import apply_idle_override

        out = apply_idle_override(
            [self.AC, self.TRV], {"ac": {"idle_action": "off"}, "trv": {"idle_action": "low"}}
        )
        assert out[0]["idle_action"] == "off"
        assert out[1]["idle_action"] == "low"

    def test_type_without_override_keeps_configured_action(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        out = apply_idle_override([self.AC, self.TRV], {"trv": {"idle_action": "setback"}})
        assert out[0]["idle_action"] == "off"  # AC untouched
        assert out[1]["idle_action"] == "setback"

    def test_does_not_mutate_input(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        ac = dict(self.AC)
        apply_idle_override([ac], {"ac": {"idle_action": "fan_only", "idle_fan_mode": "High"}})
        assert ac["idle_action"] == "off"
        assert ac["idle_fan_mode"] == "low"

    def test_fan_mode_only_set_for_fan_only_action(self):
        from custom_components.roommind.utils.device_utils import apply_idle_override

        # setback action: idle_fan_mode should NOT be injected
        out = apply_idle_override([self.AC], {"ac": {"idle_action": "setback", "idle_fan_mode": "High"}})
        assert out[0]["idle_action"] == "setback"
        assert out[0]["idle_fan_mode"] == "low"  # unchanged


# ---------------------------------------------------------------------------
# fan_efficiency
# ---------------------------------------------------------------------------


class TestFanEfficiency:
    """Fan-speed effectiveness multiplier (Phase 1.1)."""

    # Bedroom AC ladder as reported by HA (unordered); order_fan_modes sorts it
    # weakest -> strongest: Mute, Low, Mid-low, Medium, Mid-high, High, Strong.
    LADDER = ["Strong", "High", "Mid-high", "Medium", "Mid-low", "Low", "Mute", "Auto"]

    def test_weakest_maps_to_min(self):
        from custom_components.roommind.utils.device_utils import FAN_EFFICIENCY_MIN, fan_efficiency

        assert fan_efficiency("Mute", self.LADDER) == FAN_EFFICIENCY_MIN

    def test_strongest_maps_to_one(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        assert fan_efficiency("Strong", self.LADDER) == 1.0

    def test_monotonic_increasing_along_ladder(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        order = ["Mute", "Low", "Mid-low", "Medium", "Mid-high", "High", "Strong"]
        vals = [fan_efficiency(m, self.LADDER) for m in order]
        assert vals == sorted(vals)
        assert len(set(vals)) == len(vals)  # strictly increasing

    def test_midpoint_interpolates(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        # Medium is index 3 of 7 (0..6) -> 0.5 + 0.5*(3/6) = 0.75
        assert fan_efficiency("Medium", self.LADDER) == 0.75

    def test_ungraded_mode_returns_one(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        # "Auto" is excluded from the ladder -> neutral (no scaling)
        assert fan_efficiency("Auto", self.LADDER) == 1.0

    def test_empty_fan_mode_returns_one(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        assert fan_efficiency("", self.LADDER) == 1.0

    def test_single_speed_ladder_returns_one(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        # Fewer than two graded speeds -> can't reason about strength
        assert fan_efficiency("Low", ["Low", "Auto"]) == 1.0

    def test_mode_not_in_ladder_returns_one(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        assert fan_efficiency("Nonexistent", self.LADDER) == 1.0

    def test_custom_min_efficiency(self):
        from custom_components.roommind.utils.device_utils import fan_efficiency

        assert fan_efficiency("Mute", self.LADDER, min_efficiency=0.3) == 0.3


class TestCoolSetpointFloor:
    """Anti-draft AC cooling setpoint floor."""

    LADDER = ["Strong", "High", "Medium", "Low", "Mute"]

    def test_unset_floor_reads_as_none(self):
        devices = [{"entity_id": "climate.ac", "type": "ac"}]
        assert get_cool_setpoint_floor(devices, "climate.ac") is None

    def test_blank_floor_reads_as_none(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "cool_setpoint_floor": ""}]
        assert get_cool_setpoint_floor(devices, "climate.ac") is None

    def test_numeric_floor_is_parsed(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "cool_setpoint_floor": "20.5"}]
        assert get_cool_setpoint_floor(devices, "climate.ac") == 20.5

    def test_floor_applies_in_full_at_quietest_speed(self):
        assert scale_cool_floor_by_airflow(20.0, 16.0, "Mute", self.LADDER) == 20.0

    def test_floor_collapses_to_device_min_at_top_of_ladder(self):
        assert scale_cool_floor_by_airflow(20.0, 16.0, "Strong", self.LADDER) == 16.0

    def test_floor_interpolates_across_the_ladder(self):
        # "Medium" sits at the ladder midpoint → half the floor's lift above min.
        assert scale_cool_floor_by_airflow(20.0, 16.0, "Medium", self.LADDER) == 18.0

    def test_floor_below_device_min_is_ignored(self):
        assert scale_cool_floor_by_airflow(14.0, 16.0, "Mute", self.LADDER) == 16.0

    def test_ungraded_fan_mode_keeps_the_full_floor(self):
        # "Auto" is dropped from the graded ladder; airflow is unknown, so stay safe.
        assert scale_cool_floor_by_airflow(20.0, 16.0, "Auto", self.LADDER) == 20.0

    def test_missing_fan_mode_keeps_the_full_floor(self):
        assert scale_cool_floor_by_airflow(20.0, 16.0, None, self.LADDER) == 20.0


class TestDehumidifyTarget:
    """Per-device dry-mode setpoint override."""

    def test_unset_target_reads_as_none(self):
        devices = [{"entity_id": "climate.ac", "type": "ac"}]
        assert get_dehumidify_target(devices, "climate.ac") is None

    def test_blank_target_reads_as_none(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "dehumidify_target": ""}]
        assert get_dehumidify_target(devices, "climate.ac") is None

    def test_numeric_target_is_parsed(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "dehumidify_target": "19"}]
        assert get_dehumidify_target(devices, "climate.ac") == 19.0

    def test_unknown_device_reads_as_none(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "dehumidify_target": "19"}]
        assert get_dehumidify_target(devices, "climate.other") is None


class TestRoomContributesToGroupWithAcPrimary:
    """Role -> device type mapping follows the room's heat_source_primary."""

    TRV = {"entity_id": "climate.trv1", "type": "trv"}
    AC = {"entity_id": "climate.ac1", "type": "ac"}

    def test_primary_means_the_ac_when_it_leads(self):
        devices = [self.TRV, self.AC]
        assert room_contributes_to_group(devices, {"climate.ac1"}, "primary", ac_is_primary=True) is True
        assert room_contributes_to_group(devices, {"climate.trv1"}, "primary", ac_is_primary=True) is False

    def test_secondary_means_the_trv_when_the_ac_leads(self):
        devices = [self.TRV, self.AC]
        assert room_contributes_to_group(devices, {"climate.trv1"}, "secondary", ac_is_primary=True) is True
        assert room_contributes_to_group(devices, {"climate.ac1"}, "secondary", ac_is_primary=True) is False


def _ac(**overrides):
    """AC device dict with coil dry defaults."""
    dev = {"entity_id": "climate.ac", "type": "ac", "role": "auto"}
    dev.update(overrides)
    return [dev]


def test_coil_dry_config_disabled_by_default():
    """Master switch off and device on inherit -> disabled."""
    cfg = get_coil_dry_config(_ac(), "climate.ac", {})
    assert cfg.enabled is False
    assert cfg.minutes == DEFAULT_COIL_DRY_MINUTES
    assert cfg.mode == "fan_only"
    assert cfg.fan_mode == DEFAULT_COIL_DRY_FAN_MODE
    assert cfg.min_cooling_minutes == 10
    assert cfg.drain_minutes == 0


def test_coil_dry_config_inherits_global_enable():
    cfg = get_coil_dry_config(_ac(), "climate.ac", {"coil_dry_enabled": True})
    assert cfg.enabled is True


def test_coil_dry_config_device_on_overrides_disabled_global():
    """Per-device 'on' wins over a disabled master switch."""
    cfg = get_coil_dry_config(_ac(coil_dry="on"), "climate.ac", {"coil_dry_enabled": False})
    assert cfg.enabled is True


def test_coil_dry_config_device_off_overrides_enabled_global():
    cfg = get_coil_dry_config(_ac(coil_dry="off"), "climate.ac", {"coil_dry_enabled": True})
    assert cfg.enabled is False


def test_coil_dry_config_device_minutes_zero_inherits():
    """0 means inherit, not 'zero minutes'."""
    cfg = get_coil_dry_config(_ac(coil_dry_minutes=0), "climate.ac", {"coil_dry_minutes": 30})
    assert cfg.minutes == 30


def test_coil_dry_config_device_minutes_override():
    cfg = get_coil_dry_config(_ac(coil_dry_minutes=5), "climate.ac", {"coil_dry_minutes": 30})
    assert cfg.minutes == 5


def test_coil_dry_config_device_mode_empty_inherits():
    cfg = get_coil_dry_config(_ac(coil_dry_mode=""), "climate.ac", {"coil_dry_mode": "dry"})
    assert cfg.mode == "dry"


def test_coil_dry_config_device_mode_override():
    cfg = get_coil_dry_config(_ac(coil_dry_mode="fan_only"), "climate.ac", {"coil_dry_mode": "dry"})
    assert cfg.mode == "fan_only"


def test_coil_dry_config_fan_mode_device_override():
    """Explicit per-device fan mode wins over the global setting."""
    cfg = get_coil_dry_config(_ac(coil_dry_fan_mode="high"), "climate.ac", {"coil_dry_fan_mode": "low"})
    assert cfg.fan_mode == "high"


def test_coil_dry_config_fan_mode_keep_sentinel():
    """__keep__ resolves to "" = send no set_fan_mode at all."""
    cfg = get_coil_dry_config(
        _ac(coil_dry_fan_mode=COIL_DRY_FAN_MODE_KEEP), "climate.ac", {"coil_dry_fan_mode": "high"}
    )
    assert cfg.fan_mode == ""


def test_coil_dry_config_fan_mode_empty_inherits_global():
    cfg = get_coil_dry_config(_ac(coil_dry_fan_mode=""), "climate.ac", {"coil_dry_fan_mode": "high"})
    assert cfg.fan_mode == "high"


def test_coil_dry_config_global_fan_mode_empty_means_keep():
    cfg = get_coil_dry_config(_ac(), "climate.ac", {"coil_dry_fan_mode": ""})
    assert cfg.fan_mode == ""


def test_coil_dry_config_unknown_entity_falls_back_to_global():
    """No matching device -> global settings only, never a crash."""
    cfg = get_coil_dry_config(_ac(), "climate.other", {"coil_dry_enabled": True})
    assert cfg.enabled is True


def test_legacy_to_devices_sets_coil_dry_defaults():
    from custom_components.roommind.utils.device_utils import legacy_to_devices

    devices = legacy_to_devices([], ["climate.ac"])
    assert devices[0]["coil_dry"] == "inherit"
    assert devices[0]["coil_dry_minutes"] == 0
    assert devices[0]["coil_dry_mode"] == ""
    assert devices[0]["coil_dry_fan_mode"] == ""


# ---------------------------------------------------------------------------
# get_setpoint_offset / get_setpoint_offsets (#369)
# ---------------------------------------------------------------------------


class TestGetSetpointOffsets:
    def test_direct_device_with_offset(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "setpoint_mode": "direct", "setpoint_offset": 1.5}]
        assert get_setpoint_offset(devices, "climate.ac") == 1.5
        assert get_setpoint_offsets(devices) == {"climate.ac": 1.5}

    def test_negative_offset(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "setpoint_mode": "direct", "setpoint_offset": -2.0}]
        assert get_setpoint_offset(devices, "climate.ac") == -2.0

    def test_missing_field_is_zero(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "setpoint_mode": "direct"}]
        assert get_setpoint_offset(devices, "climate.ac") == 0.0
        assert get_setpoint_offsets(devices) == {}

    def test_proportional_device_ignores_offset(self):
        devices = [{"entity_id": "climate.ac", "type": "ac", "setpoint_mode": "proportional", "setpoint_offset": 2.0}]
        assert get_setpoint_offset(devices, "climate.ac") == 0.0
        assert get_setpoint_offsets(devices) == {}

    def test_unknown_device_is_zero(self):
        assert get_setpoint_offset([], "climate.ghost") == 0.0

    def test_invalid_values_are_zero(self):
        for bad in ("abc", None, float("nan")):
            devices = [{"entity_id": "climate.ac", "setpoint_mode": "direct", "setpoint_offset": bad}]
            assert get_setpoint_offset(devices, "climate.ac") == 0.0

    def test_out_of_range_is_clamped(self):
        devices = [
            {"entity_id": "climate.a", "setpoint_mode": "direct", "setpoint_offset": 99},
            {"entity_id": "climate.b", "setpoint_mode": "direct", "setpoint_offset": -99},
        ]
        assert get_setpoint_offsets(devices) == {"climate.a": 5.0, "climate.b": -5.0}

    def test_device_without_entity_id_skipped(self):
        devices = [{"setpoint_mode": "direct", "setpoint_offset": 1.0}]
        assert get_setpoint_offsets(devices) == {}
