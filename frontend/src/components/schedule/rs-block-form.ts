import { LitElement, html, css, nothing, type PropertyValues } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import type { DeviceConfig, HomeAssistant } from "../../types";
import { SWING_MODE_KEYS, fanModeLabel } from "../../utils/device-utils";
import { localize } from "../../utils/localize";
import { tokens } from "../../styles/tokens";
import { tempRowStyles } from "../../styles/temp-row-styles";
import {
  MAX_TARGET_TEMP_C,
  MIN_TARGET_TEMP_C,
  tempUnit,
  toDisplay,
  usesFahrenheit,
} from "../../utils/temperature";
import {
  COVER_CLOSED,
  COVER_OPEN,
  DAY_MINUTES,
  SNAP_MINUTES,
  blockPosition,
  blockTemps,
  hm,
  usesComfort,
  type BlockDataPatch,
  type ScheduleBlock,
} from "../../utils/schedule-week";
import "../shared/rs-segmented";
import "../shared/rs-stepper";
import "../shared/rs-number-field";
import "../shared/rs-list-row";

const DEVICE_TYPES = ["ac", "trv"] as const;
type DeviceType = (typeof DEVICE_TYPES)[number];

const IDLE_ACTIONS_BY_TYPE = {
  ac: ["off", "fan_only", "setback"],
  trv: ["off", "low", "setback"],
} as const satisfies Record<DeviceType, readonly string[]>;

const FAN_CONTROLS = ["off", "fixed", "auto"] as const;
const FAN_BOUND_KEYS = ["fan_min_mode", "fan_max_mode"] as const;

export interface BlockTempChange {
  heat?: number;
  cool?: number;
}

/**
 * Form for one schedule block (desktop popover, phone sheet): from/to, comfort or own
 * temperature, delete. Applies every valid change at once (no save button):
 * `block-times` `{ start, end }`, `block-split` `{ start, endNextDay }` for a range past
 * midnight, `block-temps` `{ temps }` (null = comfort, else only the sides that changed),
 * `block-data` `{ patch }` (device settings, undefined drops a key), `block-delete`, `block-done`. A cover schedule's block (`cover`) has a position instead of a
 * temperature: `block-position` `{ position }`.
 * Events bubble but are not composed: the editor listens on the form itself.
 */
@customElement("rs-block-form")
export class RsBlockForm extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ attribute: false }) public block!: ScheduleBlock;
  /** The other blocks of the day, to name the one a new range would run into. */
  @property({ attribute: false }) public others: ScheduleBlock[] = [];
  /** Which own-temperature keys the rooms using this schedule need. */
  @property({ attribute: false }) public sides = { heat: true, cool: true };
  /** Own temperature preset when switching from comfort (HA unit system). */
  @property({ attribute: false }) public defaults = { heat: 21, cool: 24 };
  /** "Wohnzimmer 21–24 °C, Bad 22 °C" for the comfort hint. */
  @property({ type: String }) public comfortText = "";
  @property({ type: String }) public nextDayName = "";
  /** Changes when another block is opened: the draft starts over. */
  @property({ type: String }) public blockKey = "";
  /** Block of a cover schedule: position instead of temperature. */
  @property({ type: Boolean }) public cover = false;
  /** Every room uses the cover schedule only to allow shading: the position does not count. */
  @property({ type: Boolean }) public gateOnly = false;
  /** Devices of the rooms using the schedule: which device settings the block offers. */
  @property({ attribute: false }) public devices: DeviceConfig[] = [];

  @state() private _start = 0;
  @state() private _end = 60;
  @state() private _own = false;
  @state() private _heat = 21;
  @state() private _cool = 24;
  @state() private _position: number = COVER_CLOSED;

  static styles = [
    tokens,
    tempRowStyles,
    css`
      :host {
        display: block;
      }

      .times {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--rs-space-3);
      }

      label {
        display: flex;
        flex-direction: column;
        gap: var(--rs-space-1);
        font-size: var(--rs-font-sm);
        color: var(--secondary-text-color);
      }

      select {
        height: var(--rs-touch);
        padding: 0 var(--rs-space-2);
        border: 1px solid var(--rs-border);
        border-radius: var(--rs-radius-sm);
        background-color: var(--rs-surface);
        color: var(--primary-text-color);
        font: inherit;
        font-size: 16px;
        font-variant-numeric: tabular-nums;
      }

      /* the open list is drawn by the browser; without its own colours it stays light in dark mode */
      select option {
        background-color: var(--rs-surface);
        color: var(--primary-text-color);
      }

      select:focus-visible {
        outline: 2px solid var(--primary-color);
        outline-offset: 1px;
      }

      select[aria-invalid="true"] {
        border-color: var(--rs-err);
      }

      .error {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        gap: var(--rs-space-2);
        margin-top: var(--rs-space-2);
        font-size: var(--rs-font-sm);
        line-height: 1.4;
        color: var(--rs-err-text);
      }

      .section {
        margin-top: var(--rs-space-4);
      }

      .section-title {
        margin: 0 0 var(--rs-space-2);
        font-size: var(--rs-font-sm);
        color: var(--secondary-text-color);
      }

      .advanced > summary {
        cursor: pointer;
        user-select: none;
      }

      .advanced-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0 var(--rs-space-3);
      }

      .advanced-grid > rs-list-row {
        grid-column: 1 / -1;
      }

      .hint {
        margin: var(--rs-space-2) 0 0;
        font-size: var(--rs-font-sm);
        line-height: 1.4;
        color: var(--secondary-text-color);
      }

      .footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: var(--rs-space-2);
        margin-top: var(--rs-space-4);
        padding-top: var(--rs-space-3);
        border-top: 1px solid var(--rs-border);
      }
    `,
  ];

  protected willUpdate(changed: PropertyValues<this>): void {
    if (changed.has("blockKey") || (changed.has("block") && !changed.get("block"))) {
      this._reset();
    } else if (changed.has("block") && this.block) {
      // Applied from outside (drag, keyboard) while the form is open.
      this._start = this.block.start;
      this._end = this.block.end;
    }
  }

  private _reset() {
    if (!this.block) return;
    this._start = this.block.start;
    this._end = this.block.end;
    const t = blockTemps(this.block);
    this._own = !usesComfort(this.block);
    this._heat = t.heat ?? t.single ?? this.defaults.heat;
    this._cool = t.cool ?? t.single ?? this.defaults.cool;
    this._position = blockPosition(this.block) ?? COVER_CLOSED;
  }

  render() {
    if (!this.block) return nothing;
    const l = this.hass.language;
    const error = this._timeError();
    return html`
      <div class="times">
        <label>
          ${localize("schedule_editor.from", l)}
          <select
            style=${`color-scheme: ${this.hass.themes?.darkMode ? "dark" : "light"}`}
            aria-invalid=${error ? "true" : "false"}
            @change=${(e: Event) => this._onTime("start", e)}
          >
            ${this._options(0, DAY_MINUTES - SNAP_MINUTES, this._start)}
          </select>
        </label>
        <label>
          ${localize("schedule_editor.to", l)}
          <select
            style=${`color-scheme: ${this.hass.themes?.darkMode ? "dark" : "light"}`}
            aria-invalid=${error ? "true" : "false"}
            aria-describedby="time-error"
            @change=${(e: Event) => this._onTime("end", e)}
          >
            ${this._options(SNAP_MINUTES, DAY_MINUTES, this._end)}
          </select>
        </label>
      </div>
      ${
        error
          ? html`<div class="error" id="time-error" role="alert">
              <span>${error}</span>
              ${
                this._end < this._start
                  ? html`<ha-button appearance="outlined" @click=${this._onSplit}
                      >${localize("schedule_editor.split", l)}</ha-button
                    >`
                  : nothing
              }
            </div>`
          : nothing
      }
      ${this.cover ? this._renderPosition() : html`${this._renderTemperature()}${this._renderDeviceFields()}`}
      <div class="footer">
        <ha-button variant="danger" appearance="plain" @click=${this._onDelete}>
          <ha-icon slot="start" icon="mdi:delete-outline"></ha-icon>
          ${localize("schedule_editor.delete_block", l)}
        </ha-button>
        <ha-button @click=${this._onDone}>${localize("schedule.done", l)}</ha-button>
      </div>
    `;
  }

  private _renderTemperature() {
    const l = this.hass.language;
    return html`<div class="section">
      <p class="section-title" id="temp-title">${localize("schedule_editor.temperature", l)}</p>
      <rs-segmented
        .label=${localize("schedule_editor.temperature", l)}
        .options=${[
          { value: "comfort", label: localize("override.comfort", l), icon: "mdi:sofa" },
          { value: "own", label: localize("schedule_editor.own", l), icon: "mdi:thermometer" },
        ]}
        .value=${this._own ? "own" : "comfort"}
        @value-changed=${this._onKind}
      ></rs-segmented>
      ${this._own ? this._renderOwn() : html`<p class="hint">${this._comfortHint()}</p>`}
    </div>`;
  }

  private _renderPosition() {
    const l = this.hass.language;
    return html`<div class="section">
      <rs-number-field
        .label=${localize("schedule_editor.cover_position", l)}
        suffix="%"
        .min=${COVER_CLOSED}
        .max=${COVER_OPEN}
        .step=${1}
        .value=${this._position}
        .hint=${localize(
          this.gateOnly
            ? "schedule_editor.cover_position_gate"
            : "schedule_editor.cover_position_hint",
          l,
        )}
        @value-changed=${this._onPosition}
      ></rs-number-field>
    </div>`;
  }

  private _renderOwn() {
    const l = this.hass.language;
    const step = usesFahrenheit(this.hass) ? 1 : 0.5;
    const min = Math.ceil(toDisplay(MIN_TARGET_TEMP_C, this.hass));
    const max = Math.floor(toDisplay(MAX_TARGET_TEMP_C, this.hass));
    const both = this.sides.heat && this.sides.cool;
    return html`
      ${
        this.sides.heat
          ? html`<div class="temp-row heat">
              <span
                ><ha-icon icon="mdi:fire" aria-hidden="true"></ha-icon>${localize(
                  both ? "schedule_editor.heat_below" : "schedule_editor.target",
                  l,
                )}</span
              >
              <rs-stepper
                .language=${l}
                .label=${localize(both ? "schedule_editor.heat_below" : "schedule_editor.target", l)}
                .value=${this._heat}
                .min=${min}
                .max=${max}
                .step=${step}
                .editable=${true}
                .unit=${tempUnit(this.hass)}
                @value-changed=${(e: CustomEvent<{ value: number }>) =>
                  this._onTemp("heat", e.detail.value)}
              ></rs-stepper>
            </div>`
          : nothing
      }
      ${
        this.sides.cool
          ? html`<div class="temp-row cool">
              <span
                ><ha-icon icon="mdi:snowflake" aria-hidden="true"></ha-icon>${localize(
                  both ? "schedule_editor.cool_above" : "schedule_editor.target",
                  l,
                )}</span
              >
              <rs-stepper
                .language=${l}
                .label=${localize(both ? "schedule_editor.cool_above" : "schedule_editor.target", l)}
                .value=${this._cool}
                .min=${min}
                .max=${max}
                .step=${step}
                .editable=${true}
                .unit=${tempUnit(this.hass)}
                @value-changed=${(e: CustomEvent<{ value: number }>) =>
                  this._onTemp("cool", e.detail.value)}
              ></rs-stepper>
            </div>`
          : nothing
      }
    `;
  }

  /** Settings the block gives the devices of the rooms using the schedule; "" inherits the device's own. */
  private _renderDeviceFields() {
    const l = this.hass.language;
    const types = DEVICE_TYPES.filter((type) =>
      this.devices.some((device) => device.type === type),
    );
    if (types.length === 0) return nothing;
    const data = this.block.data ?? {};
    const text = (key: string): string => {
      const value = data[key];
      return typeof value === "string" ? value : "";
    };
    const modesOf = (attribute: "fan_modes" | "swing_modes"): string[] => [
      ...new Set(
        this.devices.flatMap(
          (device) =>
            (this.hass.states[device.entity_id]?.attributes?.[attribute] ?? []) as string[],
        ),
      ),
    ];
    const fanOptions = modesOf("fan_modes").map((mode) => ({
      value: mode,
      label: fanModeLabel(this.hass, mode),
    }));
    const swingOptions = modesOf("swing_modes").map((mode) => ({ value: mode, label: mode }));
    return html`<details class="section advanced">
      <summary class="section-title">${localize("schedule.editor_advanced", l)}</summary>
      <div class="advanced-grid">
      ${types.map((type) =>
        this._renderSelect(
          localize(`schedule.editor_idle_action_${type}`, l),
          text(`${type}_idle_action`) || text("idle_action"),
          IDLE_ACTIONS_BY_TYPE[type].map((action) => ({
            value: action,
            label: localize(`devices.idle_action_${action}`, l),
          })),
          (value) => this._patchIdleAction(type, value),
        ),
      )}
      ${
        types.includes("ac")
          ? html`${this._renderSelect(
                localize("schedule.editor_idle_fan_mode", l),
                text("idle_fan_mode"),
                fanOptions,
                (value) => this._patchData({ idle_fan_mode: value }),
              )}
              ${this._renderSelect(
                localize("schedule.editor_fan_control", l),
                text("fan_control"),
                FAN_CONTROLS.map((control) => ({
                  value: control,
                  label: localize(`devices.fan_control_${control}`, l),
                })),
                (value) => this._patchData({ fan_control: value }),
              )}
              ${FAN_BOUND_KEYS.map((key) =>
                this._renderSelect(
                  localize(`schedule.editor_${key}`, l),
                  text(key),
                  fanOptions,
                  (value) => this._patchData({ [key]: value }),
                ),
              )}
              ${
                swingOptions.length > 0
                  ? SWING_MODE_KEYS.map((key) =>
                      this._renderSelect(
                        localize(`schedule.editor_${key}`, l),
                        text(key),
                        swingOptions,
                        (value) => this._patchData({ [key]: value }),
                      ),
                    )
                  : nothing
              }
              <rs-list-row
                switch
                wrap
                .heading=${localize("schedule.editor_dehumidify", l)}
                .checked=${data.dehumidify !== false}
                @toggle-changed=${(e: CustomEvent<boolean>) =>
                  this._patchData({ dehumidify: e.detail ? undefined : false })}
              ></rs-list-row>`
          : nothing
      }
      </div>
    </details>`;
  }

  /** A stored value the devices no longer offer stays visible instead of reading as inherit. */
  private _renderSelect(
    label: string,
    value: string,
    options: { value: string; label: string }[],
    onPick: (value: string | undefined) => void,
  ) {
    const l = this.hass.language;
    const isForeign = value !== "" && !options.some((option) => option.value === value);
    return html`<label>
      ${label}
      <select
        style=${`color-scheme: ${this.hass.themes?.darkMode ? "dark" : "light"}`}
        @change=${(e: Event) => onPick((e.target as HTMLSelectElement).value || undefined)}
      >
        <option value="" ?selected=${value === ""}>
          ${localize("schedule.editor_inherit", l)}
        </option>
        ${options.map(
          (option) =>
            html`<option value=${option.value} ?selected=${option.value === value}>
              ${option.label}
            </option>`,
        )}
        ${isForeign ? html`<option value=${value} selected>${value}</option>` : nothing}
      </select>
    </label>`;
  }

  /** One type's idle action pins the other's too and drops the shared `idle_action` fallback. */
  private _patchIdleAction(type: DeviceType, value: string | undefined) {
    const data = this.block.data ?? {};
    const other = type === "ac" ? "trv" : "ac";
    this._patchData({
      [`${type}_idle_action`]: value,
      [`${other}_idle_action`]: data[`${other}_idle_action`] ?? data.idle_action,
      idle_action: undefined,
    });
  }

  private _patchData(patch: BlockDataPatch) {
    this._fire("block-data", { patch });
  }

  private _comfortHint() {
    const l = this.hass.language;
    return this.comfortText
      ? localize("schedule_editor.comfort_hint", l, { rooms: this.comfortText })
      : localize("schedule_editor.comfort_hint_plain", l);
  }

  /** Quarter hours plus the current value when it is off the grid (made elsewhere). */
  private _options(from: number, to: number, current: number) {
    const values: number[] = [];
    for (let m = from; m <= to; m += SNAP_MINUTES) values.push(m);
    if (!values.includes(current)) values.push(current);
    values.sort((a, b) => a - b);
    return values.map(
      (m) => html`<option value=${String(m)} ?selected=${m === current}>${hm(m)}</option>`,
    );
  }

  private _timeError(): string {
    const l = this.hass.language;
    if (this._end < this._start) {
      return localize("schedule_editor.error_before_start_midnight", l, {
        day: this.nextDayName,
      });
    }
    if (this._end === this._start) return localize("schedule_editor.error_empty", l);
    const clash = this.others.find((b) => b.start < this._end && this._start < b.end);
    if (clash) {
      return localize("schedule_editor.error_overlap", l, {
        range: `${hm(clash.start)}–${hm(clash.end)}`,
      });
    }
    return "";
  }

  private _onTime(edge: "start" | "end", e: Event) {
    const value = Number((e.target as HTMLSelectElement).value);
    if (edge === "start") this._start = value;
    else this._end = value;
    if (!this._timeError()) this._fire("block-times", { start: this._start, end: this._end });
  }

  private _onSplit() {
    this._fire("block-split", { start: this._start, endNextDay: this._end });
  }

  private _onKind(e: CustomEvent<{ value: string }>) {
    this._own = e.detail.value === "own";
    // Switching to own writes every side the rooms need, with the values the steppers show.
    this._fire("block-temps", {
      temps: this._own
        ? {
            ...(this.sides.heat ? { heat: this._heat } : {}),
            ...(this.sides.cool ? { cool: this._cool } : {}),
          }
        : null,
    });
  }

  private _onTemp(side: "heat" | "cool", v: number) {
    const temps: BlockTempChange = {};
    if (side === "heat") {
      this._heat = temps.heat = v;
      // A band never inverts: cooling above stays at or over heating below.
      if (this.sides.cool && this._cool < v) this._cool = temps.cool = v;
    } else {
      this._cool = temps.cool = v;
      if (this.sides.heat && this._heat > v) this._heat = temps.heat = v;
    }
    // Only the sides that moved: the other keeps its stored value or keeps following comfort.
    this._fire("block-temps", { temps });
  }

  private _onPosition(e: CustomEvent<{ value: number | null }>) {
    if (e.detail.value == null) return;
    this._position = e.detail.value;
    this._fire("block-position", { position: this._position });
  }

  private _onDelete() {
    this._fire("block-delete", {});
  }

  private _onDone() {
    this._fire("block-done", {});
  }

  private _fire(name: string, detail: unknown) {
    this.dispatchEvent(new CustomEvent(name, { detail, bubbles: true }));
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-block-form": RsBlockForm;
  }
}
