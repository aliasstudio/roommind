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

On an AC the same mechanism runs in reverse: at full cooling demand RoomMind sends the device's `min_temp` (typically `16°C`), which is what drives an inverter's compressor to full frequency. The setpoint is a power command, not a comfort target — the room target is enforced by RoomMind against the external sensor.

#### Min AC setpoint (anti-draft)

Optional, per AC device. Pinning an inverter to its coldest setpoint while the fan crawls drives the evaporator coil deep and makes the supply air feel icy, without buying much capacity — the airflow is the bottleneck, not the compressor. This setting is the warmest cooling setpoint RoomMind will send **at the quietest fan speed**; it relaxes linearly toward the device minimum as the fan ramps up, so daytime capacity and `Turbo` are unaffected.

Leave it empty to always allow the device minimum. The floor never pushes the setpoint above the room's own target.

#### Dry mode target

Optional, per AC device. While a room is actively dehumidifying (see `Active dehumidification` below), RoomMind normally sends the room's scheduled cooling target as the AC's setpoint. Some units barely dry when that setpoint sits close to the current room temperature — their dry-mode logic treats it as a hysteresis reference rather than a hard target, so drying stalls without the compressor working the coil.

This setting overrides the setpoint sent specifically while drying, independent of the room's schedule and the anti-draft floor above. Leave it empty to keep using the room's scheduled cooling target.

### Valve opening degree

The third setpoint mode, offered for TRVs only. Some heads expose their valve position as a `number` entity — the Sonoff TRVZB (firmware `1.3.0`+) exposes `number.*_valve_opening_degree` and `number.*_valve_closing_degree` through Zigbee2MQTT. Point a TRV device at those entities and RoomMind writes the valve position itself instead of inferring it from a boosted setpoint.

This removes the head's internal thermostat from the loop: the `climate` entity is parked at its boost setpoint so it always demands heat, and how far the valve actually opens is the MPC power fraction, mapped onto the head's usable range.

Why it is better than a proportional setpoint:

- the opening is exact, not a guess filtered through the head's own hysteresis, sensor calibration and `0.5°C` setpoint step
- output no longer depends on how well the head's internal sensor happens to agree with the room sensor
- the same demand always produces the same valve position

Settings, per device:

- `Minimum opening degree` (default `10`) — below this a TRVZB passes almost no water, so demand at or above the threshold is scaled into `[min, max]` rather than starting at zero
- `Maximum opening degree` (default `100`) — for heads that cap lower
- `Opening threshold` (default `5`) — demand below this shuts the valve entirely
- `Opening deadband` (default `5`) — smaller changes are not sent, sparing battery and valve motor; fully open and fully closed always go through

Requires an external room temperature sensor (`Full Control`). In `Managed` rooms the head must keep regulating on its own sensor, so the setpoint path applies and these settings are ignored.

The three setpoint modes are mutually exclusive: pick `Valve opening degree` to drive the position, or stay on `Proportional` / `Direct` to drive a setpoint. Valve protection (anti-seize) understands both: valve-regulated heads are cycled by driving the opening degree, not by setpoint games.

Do not enable the TRVZB's own PID / "Smart Temperature Control" (firmware `1.4.4`+) alongside this. That is a second regulator fighting RoomMind for the same valve.

### Direct

RoomMind sends the real target temperature and lets the device regulate itself.

Best for:

- space heaters
- pellet stoves
- devices with their own thermostat logic that should stay in control internally

## Heat Source Orchestration

For rooms holding both a radiator TRV and an AC that can heat. `Lead source` picks which of the two carries the full heating demand; the other supplements it at a reduced share whenever both run.

Put the source that is cheaper *per kWh of heat* in front. For a radiator that usually means the radiator — but not always: compare the heat tariff (price per Gcal ÷ 1163) against your electricity price divided by the heat pump's COP at the outdoor temperatures you actually see. Where heat is metered and electricity is cheap, the heat pump can win across its whole operating range, and the radiator becomes the expensive backup for the cold end.

Which source runs is then decided in this order:

1. Room more than `Combined heating threshold` × 2 below target → **both**, lead source at full power.
2. Otherwise `Outdoor preference switchover` decides by outdoor temperature: above it the AC, below it the radiator. This is a device-type preference and does not depend on which one leads.
3. Below `AC heating cutoff` the AC is removed entirely, in either role.

Changing the lead source does not change steps 2 and 3 — it changes who gets full power when both run, and who is preferred when no outdoor temperature is available at all.

## Heating Season

A global toggle in `Settings`, next to `Vacation Mode`, and also exposed as `switch.roommind_heating_season` for automations.

Turn it off when the building's water heating is shut down for the season. Radiator valves stop heating entirely: they are idled per their `When idle` action, valve-regulated heads are driven shut, and TRVs no longer count toward a room's heating capability. Rooms with no other heat source simply stop heating.

Air conditioners that heat are deliberately left alone — in the off-season they are usually what covers the gap. In a room with both, heat source orchestration is suspended while the season is off, so the AC receives the full heating demand instead of a share split with a cold radiator.

Cooling, dehumidification and mold prevention are unaffected. This is a hard gate, not a setback: unlike `Maximum outdoor temp for heating` it does not depend on the weather and is not bypassed by a manual override.

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

## Evaporator Drying

`Settings -> Evaporator drying` (with a per-device override in `Devices`) keeps an AC's indoor fan running for a while after cooling stops, before the unit really switches off.

Drying the evaporator coil this way cuts down on biofilm buildup and a musty smell on the next cooling start. It is off by default.

### Turning it on

- Enable it globally in `Settings -> Evaporator drying`.
- Or override a single device in `Devices`: `Use global setting`, `Always on`, or `Always off`. Only `Climate Device` / AC entries offer this; TRVs have no evaporator coil.

Drying time, the minimum cooling time before a run is worth it, an optional drain delay, drying mode (`fan_only` or `dry`), and fan speed are all configurable, both globally and per device. A device left on its default values falls back to the global setting.

### While it runs

- A returning cooling demand cancels the run immediately and the AC goes straight back to cooling.
- A returning heating demand cancels it too, and resets the tracked wetness for that room's ACs.
- A running or draining device shows a badge with the remaining time on the room's status.

### Relation to `When idle`

If a drain delay is configured, a run normally holds the device off first so condensate can drain, then switches to the fan. With `When idle` set to `Setback` the device is never actually off, so the drain step is skipped and the run starts directly in the fan phase.

### Relation to an explicit shutdown

An explicit shutdown, `Action when schedule is off` or `Action when away` set to `Turn off devices`, overrides `When idle` (described in the next section) so a device cannot stay fanning or set back indefinitely. Evaporator drying is different: an explicit shutdown does not block it. A run can start fresh or keep going, and always finishes normally, before the device goes off.

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
- **Auto** — speed proportional to demand. RoomMind maps the device's speed ladder (weakest → strongest, the device's own `Auto` is excluded) to how far the room is from target: far above → top of the band, near target → a gentle step, at/below target → the quietest step. The MPC power demand caps it too, so once the thermal model is confident the fan also follows the model (low power → low fan). `Min`/`Max fan speed` bound the ladder — e.g. minimum `Mute` for a quiet night and maximum `High` to cap noise.

Because the speed tracks the remaining error, the fan **eases off smoothly as the room approaches target** instead of staying pinned at maximum. With `setback` idle the AC keeps blowing, so once the room reaches target the fan settles to its minimum (e.g. `Mute`) rather than holding the last cooling speed. A short minimum interval between changes damps jitter on small sensor wobble.

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

## Turbo Override

`Turbo` is a room override next to `Comfort`, `Eco` and `Custom`, with the same duration chips. While it runs, the room cools flat out:

- the ACs are set to the coldest setpoint they accept and to the strongest fan speed on their ladder
- schedule targets, presence, vacation and the `fan_max_mode` caps above are all ignored
- active dehumidification hands the AC back, since Dry mode owns the unit while it runs

Two guards still apply: an open window (or a disabled climate control) idles the room as usual, and compressor group limits are respected.

Turbo observations are excluded from thermal-model training — the AC is deliberately held off its normal operating point, so those cycles say nothing about how the room behaves under regular control. Analytics history keeps recording, so the charts stay continuous.

Turbo is hidden in `heat_only` rooms.

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

## Watchdog: Devices That Accept Commands And Do Nothing

A device can report exactly the mode RoomMind asked for and still do nothing — an AC whose cloud API echoes `cool` back while the compressor never starts, a TRV whose valve is mechanically stuck, a radiator with no hot water. RoomMind normally trusts the reported state, and its redundancy guard will not re-send a mode the device already claims to be in, so without help the room would sit in that mode indefinitely.

The watchdog compares the room temperature against the one measured when the mode started. It only runs while RoomMind actually drives the room (climate control on, no open window, not forced off) and the device is at full power — a device that modulates down is either working or near target, and its episode is re-baselined.

When 20 minutes pass with less than 0.2 °C of movement in the commanded direction:

1. The room's ACs are power-cycled. Off then on re-sends the whole mode + setpoint + fan sequence, which is what usually revives a unit stuck in a phantom "on" state.
2. If another 20 minutes pass with still no movement, RoomMind raises a Home Assistant notification naming the room. At that point the problem is below RoomMind: either the hardware does not act on the commands, or its integration accepts commands the device never receives — a cloud-connected AC whose vendor API is down keeps reporting the mode it was asked for while the unit stays off.

The mode keeps running throughout — the watchdog reports, it never gives up on the room. The notification clears by itself once the room starts responding again. Turn the whole thing off under `Settings → Control` if the alerts are not useful for your setup.
