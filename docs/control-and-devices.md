# Control And Device Guide

This page explains RoomMind's control settings and related device options.

## What Priority Does

In `Settings -> Control -> Priority`, the slider balances comfort against runtime/energy use for MPC.

- Toward `Comfort`: RoomMind reacts earlier and works harder to stay close to the target temperature.
- Toward `Efficiency`: RoomMind allows more drift around the target to reduce heating/cooling runtime.

This setting does **not** change your schedule targets, overrides, comfort temperature, or eco temperature. It only changes how aggressively MPC tries to reach and hold those targets.

## Thermostat vs Climate Device

Both options are Home Assistant `climate.*` entities, but RoomMind treats them differently:

- `Thermostat`: a radiator thermostat / TRV style device.
- `Climate Device`: an AC, heat pump, or other climate entity used for cooling or forced-air heating.

In practice:

- Choose `Thermostat` for radiator valves and similar heating-only valve devices.
- Choose `Climate Device` for ACs, minisplits, heat pumps, and other self-contained HVAC units.

## Full Control vs Managed

An external room temperature sensor is the key split:

- `Full Control`: RoomMind uses the external sensor as the room truth and can actively shape device output.
- `Managed`: without an external room sensor, RoomMind sends target temperatures but the device mostly regulates itself using its own internal sensor.

This matters for the options below.

## Setpoint Mode: Proportional vs Direct

`Setpoint mode` is relevant for thermostat/TRV devices in `Full Control` rooms.

### Proportional

RoomMind calculates the required heating power, then sends a boosted device setpoint to achieve roughly that output.

Example:

- room target is `21°C`
- more heat is needed
- RoomMind may send `26-28°C` to the TRV to force the valve open harder

Best for:

- radiator valves / TRVs
- devices that need an exaggerated setpoint to actually deliver heat

### Direct

RoomMind sends the real target temperature and lets the device regulate itself.

Best for:

- space heaters
- pellet stoves
- devices with their own thermostat logic that should stay in control internally

## Idle Behavior: Off, Fan Only, Setback

`When idle` applies to `Climate Device` entries.

### Turn off

RoomMind turns the device off, or falls back to the device's minimum/off-like behavior if true off is not supported.

### Fan only

RoomMind keeps the device running in fan mode without active heating/cooling.

Useful when you want:

- air circulation
- less harsh on/off transitions

### Setback

RoomMind keeps the current HVAC mode active, but moves the target away from the room target:

- heating setback = `heat target - 2°C`
- cooling setback = `cool target + 2°C`

This lets the device back off instead of shutting off completely.

Important:

- the setback offset is currently fixed at `2°C`
- it is **not configurable** in the current UI

## Idle Behavior for Thermostats: Off, Low

`When idle` also applies to `Thermostat` / TRV entries, with different options.

### Turn off

RoomMind sends the TRV to its `off` state.

### Low

RoomMind keeps the TRV in its current heating mode but lowers the setpoint to the device's minimum temperature.

Useful for battery-powered Zigbee TRVs that enter deep sleep when set to `off` and then stop reacting to commands. `Low` keeps the valve responsive while effectively stopping heating.

## When "Turn off devices" Overrides `When idle`

`When idle` describes what a device should do while the room simply has no heating or cooling demand. It does **not** apply when you explicitly shut a room down via:

- `Settings → Control → Action when schedule is off` set to `Turn off devices`
- `Settings → Presence → Action when away` set to `Turn off devices`

In those cases RoomMind turns the devices off even if `When idle` is set to `Fan only` or `Setback`. Otherwise an AC would keep circulating air after the schedule ended.

The single exception is `Low` on thermostats: it stays active because the affected TRVs stop responding after being set to `off`. Lowering the setpoint to the device minimum already stops all heat output.

## Fan Speed Control (AC)

For `Climate Device` / AC entries with selectable fan speeds, `Fan control` decides how RoomMind drives the fan **while actively heating or cooling**:

- **Off** — leave the fan under the device/firmware's own control (legacy behavior).
- **Fixed** — always force one speed (e.g. `High` so cool air reaches a distant floor sensor).
- **Auto** — adapt the speed to demand. RoomMind walks a ladder of the device's speeds (weakest → strongest, the device's own `Auto` is excluded): it steps **up** when the optimizer is asking for near-full power yet the room still hasn't reached target, and steps **down** (toward the quietest speed) when demand is low or the room is already at/below target. `Min`/`Max fan speed` bound the ladder — e.g. minimum `Mute` for a quiet night and maximum `High` to cap noise.

Auto escalation reacts faster than it backs off, to avoid the fan hunting. The speed RoomMind chooses while idle is governed separately by the idle `Fan speed` setting.

## Schedule-Driven Idle Behavior

The idle action can be overridden per schedule slot, so a room can idle differently by time of day (for example fully **off** overnight for silence, **fan only** during the day for circulation).

Add an `idle_action` key (and optionally `idle_fan_mode`) to a schedule block's additional data in the Home Assistant schedule helper — the same place per-block `heat_temperature` / `cool_temperature` are set:

- `idle_action`: `off`, `fan_only`, `setback`, or `low`
- `idle_fan_mode` (optional): the fan speed to use when `idle_action` is `fan_only`

When the active block carries an `idle_action`, it replaces each device's configured idle behavior **only where the action is valid for that device type** (e.g. `fan_only` applies to ACs, `low` applies to TRVs); other devices keep their own configuration. Blocks without an `idle_action` key leave the per-device idle settings unchanged.

### Capping fan speed by time of day

The same schedule block data can cap or steer the **auto fan control** so active cooling stays quiet at night while still allowed to ramp up during the day:

- `fan_max_mode`: strongest speed the auto ladder may use during this block (e.g. `Low` overnight)
- `fan_min_mode`: weakest speed during this block
- `fan_control`: `off` / `fixed` / `auto` to switch the strategy for this block

These override the device's `Fan control` settings while the block is active. A tighter `fan_max_mode` takes effect immediately — if the fan had already escalated to a higher speed, it drops to the cap on the next cycle rather than waiting for the usual step-down delay. The device must be in (or switched by the schedule to) `auto` for the bounds to apply.

Example — silent night, lively day:

```yaml
- from: "23:00:00"
  to: "07:00:00"
  data:
    temperature: 24
    fan_max_mode: "Low"     # cool quietly while sleeping
- from: "07:00:00"
  to: "23:00:00"
  data:
    temperature: 23
    fan_max_mode: "Strong"  # full power available by day
```

## Smart Source Selection

`Smart source selection` only appears when a room has:

- at least one `Thermostat` / TRV
- at least one `Climate Device` / AC
- an external temperature sensor

In that case RoomMind can decide which source should heat:

- TRV / boiler side
- AC / heat pump side
- or both, when the gap is large

It uses temperature gap and outdoor conditions to make that choice.
