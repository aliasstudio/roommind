import { LitElement, html, css, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { WEEKDAYS } from "../types";
import type {
  ClimateMode,
  DeviceConfig,
  HomeAssistant,
  IdleAction,
  ScheduleBlock,
  ScheduleBlockData,
  ScheduleBlocks,
  Weekday,
} from "../types";
import { localize } from "../utils/localize";
import { getSelectValue } from "../utils/events";
import {
  emptyBlocks,
  mapWeekdays,
  overlappingBlocks,
  padTime,
  sortBlocks,
  trimTime,
} from "../utils/schedule";
import { toCelsius, toDisplay, tempStep, tempUnit, tempRange } from "../utils/temperature";
import { inputStyles } from "../styles/input-styles";

/** What each device type can actually do while idle. */
const IDLE_ACTIONS_BY_TYPE = {
  ac: ["off", "fan_only", "setback"],
  trv: ["off", "low", "setback"],
} as const satisfies Record<string, readonly IdleAction[]>;

const FAN_CONTROL_MODES = ["off", "fixed", "auto"] as const;

const DEFAULT_BLOCK: ScheduleBlock = { from: "08:00:00", to: "22:00:00", data: {} };

/** Weekly block editor for one inline schedule; emits the whole week on change. */
@customElement("rs-schedule-editor")
export class RsScheduleEditor extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ attribute: false }) public blocks: ScheduleBlocks = emptyBlocks();
  @property({ type: String }) public climateMode: ClimateMode = "auto";
  /** Room devices, so fan fields offer the same modes as the device settings. */
  @property({ attribute: false }) public devices: DeviceConfig[] = [];

  /** "monday:2" — only one block's advanced panel is open at a time. */
  @state() private _expanded = "";

  static styles = [
    inputStyles,
    css`
      :host {
        display: block;
      }

      .day {
        border-top: 1px solid var(--divider-color, #eee);
        padding: 10px 0;
      }
      .day:first-child {
        border-top: none;
      }

      .day-header {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
      }
      .day-name {
        font-size: 13px;
        font-weight: 600;
        text-transform: capitalize;
      }
      .day-empty {
        font-size: 12px;
        color: var(--secondary-text-color);
        font-style: italic;
      }
      .day-actions {
        margin-left: auto;
        display: flex;
        gap: 2px;
      }

      .block {
        display: grid;
        grid-template-columns: auto auto 1fr auto auto;
        align-items: center;
        gap: 6px 8px;
        padding: 6px 8px;
        border-radius: 8px;
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.03));
        margin-bottom: 6px;
      }
      .block.overlap {
        outline: 1px solid var(--error-color, #db4437);
      }

      input[type="time"] {
        font: inherit;
        color: var(--primary-text-color);
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color, #ddd);
        border-radius: 6px;
        padding: 5px 6px;
      }

      .temps {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        align-items: center;
        min-width: 0;
      }
      .temps.single {
        grid-template-columns: minmax(0, 1fr);
      }
      .temps.triple {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      /* Six controls on a 3-column grid fill exactly two rows, no ragged tail. */
      .advanced {
        grid-column: 1 / -1;
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
        padding: 10px 0 4px;
        border-top: 1px dashed var(--divider-color, #ddd);
      }
      .field {
        min-width: 0;
      }
      /* Inner labels everywhere, so every control shares one baseline. */
      ha-textfield,
      ha-select {
        width: 100%;
      }
      .switch-field {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 13px;
        padding-left: 2px;
      }

      .overlap-hint {
        font-size: 12px;
        color: var(--error-color, #db4437);
        margin-bottom: 6px;
      }

      @media (max-width: 900px) {
        .advanced {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }

      @media (max-width: 600px) {
        .block {
          grid-template-columns: auto auto 1fr;
        }
        .temps {
          grid-column: 1 / -1;
        }
        .advanced {
          grid-template-columns: minmax(0, 1fr);
        }
      }
    `,
  ];

  private static readonly ICON_ADD = "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z";
  private static readonly ICON_DELETE =
    "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z";
  private static readonly ICON_COPY =
    "M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z";
  private static readonly ICON_TUNE =
    "M3,17V19H9V17H3M3,5V7H13V5H3M13,21V19H21V17H13V15H11V21H13M7,9V11H3V13H7V15H9V9H7M21,13V11H11V13H21M15,9H17V7H21V5H17V3H15V9Z";

  private _tempsLayoutClass(data: ScheduleBlockData): string {
    if (this.climateMode !== "auto") return "single";
    return data.temperature != null ? "triple" : "";
  }

  private get _hasAc(): boolean {
    return this.devices.some((device) => device.type === "ac");
  }

  /** Device types present in the room, so only relevant idle fields are shown. */
  private get _roomDeviceTypes(): ("ac" | "trv")[] {
    return (["ac", "trv"] as const).filter((deviceType) =>
      this.devices.some((device) => device.type === deviceType),
    );
  }

  /** Union of the fan modes the room's climate entities report. */
  private get _fanModes(): string[] {
    const modes = this.devices.flatMap(
      (device) => (this.hass?.states?.[device.entity_id]?.attributes?.fan_modes ?? []) as string[],
    );
    return [...new Set(modes)];
  }

  protected render(): TemplateResult {
    return html`${WEEKDAYS.map((day) => this._renderDay(day))}`;
  }

  private _renderDay(day: Weekday): TemplateResult {
    const language = this.hass.language;
    const dayBlocks = this.blocks[day] ?? [];
    const overlaps = overlappingBlocks(dayBlocks);

    return html`
      <div class="day">
        <div class="day-header">
          <span class="day-name">${localize(`weekday.${day}`, language)}</span>
          ${dayBlocks.length === 0
            ? html`<span class="day-empty"
                >${localize("schedule.editor_day_empty", language)}</span
              >`
            : nothing}
          <span class="day-actions">
            ${dayBlocks.length > 0
              ? html`
                  <ha-icon-button
                    .path=${RsScheduleEditor.ICON_COPY}
                    .label=${localize("schedule.editor_copy_day", language)}
                    @click=${() => this._copyDayToWeek(day)}
                  ></ha-icon-button>
                `
              : nothing}
            <ha-icon-button
              .path=${RsScheduleEditor.ICON_ADD}
              .label=${localize("schedule.editor_add_block", language)}
              @click=${() => this._addBlock(day)}
            ></ha-icon-button>
          </span>
        </div>
        ${overlaps.size > 0
          ? html`<div class="overlap-hint">${localize("schedule.editor_overlap", language)}</div>`
          : nothing}
        ${dayBlocks.map((block, index) =>
          this._renderBlock(day, block, index, overlaps.has(index)),
        )}
      </div>
    `;
  }

  private _renderBlock(
    day: Weekday,
    block: ScheduleBlock,
    index: number,
    isOverlapping: boolean,
  ): TemplateResult {
    const language = this.hass.language;
    const key = `${day}:${index}`;
    const data = block.data ?? {};

    return html`
      <div class="block ${isOverlapping ? "overlap" : ""}">
        <input
          type="time"
          .value=${trimTime(block.from)}
          @change=${(event: Event) =>
            this._patchBlock(day, index, { from: padTime(this._inputValue(event)) })}
        />
        <input
          type="time"
          .value=${trimTime(block.to)}
          @change=${(event: Event) =>
            this._patchBlock(day, index, { to: padTime(this._inputValue(event)) })}
        />
        <div class="temps ${this._tempsLayoutClass(data)}">
          ${this._renderTemps(day, index, data)}
        </div>
        <ha-icon-button
          .path=${RsScheduleEditor.ICON_TUNE}
          .label=${localize("schedule.editor_advanced", language)}
          @click=${() => (this._expanded = this._expanded === key ? "" : key)}
        ></ha-icon-button>
        <ha-icon-button
          .path=${RsScheduleEditor.ICON_DELETE}
          .label=${localize("schedule.editor_remove_block", language)}
          @click=${() => this._removeBlock(day, index)}
        ></ha-icon-button>
        ${this._expanded === key ? this._renderAdvanced(day, index, data) : nothing}
      </div>
    `;
  }

  /**
   * A single `temperature` still shows in auto mode when the block carries one
   * (YAML schedules commonly do), otherwise imported targets would be invisible
   * here while still driving the room.
   */
  private _renderTemps(day: Weekday, index: number, data: ScheduleBlockData): TemplateResult {
    const language = this.hass.language;
    const single = this._renderTempField(
      localize("schedule.editor_temperature", language),
      data.temperature,
      (value) => this._patchData(day, index, { temperature: value }),
    );

    if (this.climateMode !== "auto") return single;

    return html`
      ${data.temperature != null ? single : nothing}
      ${this._renderTempField(
        localize("schedule.row_heat", language),
        data.heat_temperature,
        (value) => this._patchData(day, index, { heat_temperature: value }),
      )}
      ${this._renderTempField(
        localize("schedule.row_cool", language),
        data.cool_temperature,
        (value) => this._patchData(day, index, { cool_temperature: value }),
      )}
    `;
  }

  private _renderTempField(
    label: string,
    value: number | undefined,
    onChange: (value: number | undefined) => void,
  ): TemplateResult {
    const range = tempRange(5, 35, this.hass);
    return html`
      <ha-textfield
        type="number"
        .label=${label}
        .value=${value == null ? "" : String(toDisplay(value, this.hass))}
        suffix=${tempUnit(this.hass)}
        step=${tempStep(this.hass)}
        min=${range.min}
        max=${range.max}
        @change=${(event: Event) => {
          const raw = this._inputValue(event);
          onChange(raw === "" ? undefined : toCelsius(parseFloat(raw), this.hass));
        }}
      ></ha-textfield>
    `;
  }

  private _renderAdvanced(day: Weekday, index: number, data: ScheduleBlockData): TemplateResult {
    const language = this.hass.language;

    return html`
      <div class="advanced">
        ${this._roomDeviceTypes.map((deviceType) =>
          this._renderChoiceField(
            localize(`schedule.editor_idle_action_${deviceType}`, language),
            data[`${deviceType}_idle_action`] ?? data.idle_action,
            IDLE_ACTIONS_BY_TYPE[deviceType].map((action) => ({
              value: action,
              label: localize(`devices.idle_action_${action}`, language),
            })),
            (value) => this._patchIdleAction(day, index, deviceType, value),
          ),
        )}
        ${this._hasAc ? this._renderAcFields(day, index, data) : nothing}
      </div>
    `;
  }

  /** Fan behavior and dehumidifying are AC-only, so a TRV-only room never shows them. */
  private _renderAcFields(day: Weekday, index: number, data: ScheduleBlockData): TemplateResult {
    const language = this.hass.language;
    const fanFields = ["fan_min_mode", "fan_max_mode"] as const;

    return html`
      ${this._renderFanModeField(
        localize("schedule.editor_idle_fan_mode", language),
        data.idle_fan_mode,
        (value) => this._patchData(day, index, { idle_fan_mode: value }),
      )}
      ${this._renderChoiceField(
        localize("schedule.editor_fan_control", language),
        data.fan_control,
        FAN_CONTROL_MODES.map((mode) => ({
          value: mode,
          label: localize(`devices.fan_control_${mode}`, language),
        })),
        (value) => this._patchData(day, index, { fan_control: value }),
      )}
      ${fanFields.map((field) =>
        this._renderFanModeField(
          localize(`schedule.editor_${field}`, language),
          data[field],
          (value) => this._patchData(day, index, { [field]: value }),
        ),
      )}
      <div class="field switch-field">
        <ha-switch
          .checked=${data.dehumidify !== false}
          @change=${(event: Event) => {
            const isEnabled = (event.target as HTMLInputElement).checked;
            this._patchData(day, index, { dehumidify: isEnabled ? undefined : false });
          }}
        ></ha-switch>
        <label>${localize("schedule.editor_dehumidify", language)}</label>
      </div>
    `;
  }

  /**
   * Fan modes come from the room's climate entities. With none reported (no AC,
   * or the entity is unavailable) fall back to text, so an imported value stays
   * visible and editable instead of being silently uneditable.
   */
  private _renderFanModeField(
    label: string,
    value: string | undefined,
    onChange: (value: string | undefined) => void,
  ): TemplateResult {
    const fanModes = this._fanModes;
    if (fanModes.length === 0) {
      return this._renderTextField(label, value, onChange);
    }
    return this._renderChoiceField(
      label,
      value,
      fanModes.map((mode) => ({ value: mode, label: mode })),
      onChange,
    );
  }

  /** A select whose empty option means "inherit the device setting". */
  private _renderChoiceField<T extends string>(
    label: string,
    value: T | undefined,
    options: { value: T; label: string }[],
    onChange: (value: T | undefined) => void,
  ): TemplateResult {
    const inherit = localize("schedule.editor_inherit", this.hass.language);
    return html`
      <div class="field">
        <ha-select
          .label=${label}
          .value=${value ?? ""}
          .options=${[{ value: "", label: inherit }, ...options]}
          @selected=${(event: Event) => onChange((getSelectValue(event) || undefined) as T)}
          @closed=${(event: Event) => event.stopPropagation()}
          fixedMenuPosition
          naturalMenuWidth
        >
          <ha-list-item value="">${inherit}</ha-list-item>
          ${options.map(
            (option) => html`<ha-list-item value=${option.value}>${option.label}</ha-list-item>`,
          )}
        </ha-select>
      </div>
    `;
  }

  private _renderTextField(
    label: string,
    value: string | undefined,
    onChange: (value: string | undefined) => void,
  ): TemplateResult {
    return html`
      <div class="field">
        <ha-textfield
          .label=${label}
          .value=${value ?? ""}
          @change=${(event: Event) => onChange(this._inputValue(event) || undefined)}
        ></ha-textfield>
      </div>
    `;
  }

  // ─── Mutations ─────────────────────────────────────────────────

  private _inputValue(event: Event): string {
    return (event.target as HTMLElement & { value: string }).value.trim();
  }

  private _addBlock(day: Weekday): void {
    this._emit({ ...this.blocks, [day]: [...(this.blocks[day] ?? []), DEFAULT_BLOCK] });
  }

  private _removeBlock(day: Weekday, index: number): void {
    this._expanded = "";
    this._emit({
      ...this.blocks,
      [day]: (this.blocks[day] ?? []).filter((_, blockIndex) => blockIndex !== index),
    });
  }

  private _copyDayToWeek(day: Weekday): void {
    const source = this.blocks[day] ?? [];
    this._emit(mapWeekdays(() => structuredClone(source)));
  }

  private _patchBlock(day: Weekday, index: number, patch: Partial<ScheduleBlock>): void {
    const dayBlocks = (this.blocks[day] ?? []).map((block, blockIndex) =>
      blockIndex === index ? { ...block, ...patch } : block,
    );
    this._emit({ ...this.blocks, [day]: dayBlocks });
  }

  /**
   * Writing one type's action pins the other type's too, resolving the shared
   * `idle_action` fallback and dropping it, so a block never states idling in
   * two places at once.
   */
  private _patchIdleAction(
    day: Weekday,
    index: number,
    deviceType: "ac" | "trv",
    value: IdleAction | undefined,
  ): void {
    const data = this.blocks[day]?.[index]?.data ?? {};
    const other = deviceType === "ac" ? "trv" : "ac";
    this._patchData(day, index, {
      [`${deviceType}_idle_action`]: value,
      [`${other}_idle_action`]: data[`${other}_idle_action`] ?? data.idle_action,
      idle_action: undefined,
    });
  }

  private _patchData(day: Weekday, index: number, patch: ScheduleBlockData): void {
    const current = this.blocks[day]?.[index]?.data ?? {};
    const data = Object.fromEntries(
      Object.entries({ ...current, ...patch }).filter(([, value]) => value !== undefined),
    ) as ScheduleBlockData;
    this._patchBlock(day, index, { data });
  }

  /** Blocks are stored sorted so the backend's first-match lookup stays predictable. */
  private _emit(blocks: ScheduleBlocks): void {
    const sorted = mapWeekdays((day) => sortBlocks(blocks[day] ?? []));
    this.dispatchEvent(
      new CustomEvent("blocks-changed", {
        detail: { value: sorted },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-schedule-editor": RsScheduleEditor;
  }
}
