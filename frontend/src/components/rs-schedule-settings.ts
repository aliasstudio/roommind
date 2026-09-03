import { html, css, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import type { ScheduleBlockData, ScheduleBlocks, ScheduleEntry, ClimateMode } from "../types";
import { localize } from "../utils/localize";
import {
  formatTemp,
  tempUnit,
  toDisplay,
  toCelsius,
  tempStep,
  tempRange,
} from "../utils/temperature";
import { RsScheduleBase } from "./shared/rs-schedule-base";
import { inputStyles } from "../styles/input-styles";
import { emptyBlocks, findActiveBlock, isInlineSchedule } from "../utils/schedule";
import "./rs-schedule-editor";

@customElement("rs-schedule-settings")
export class RsScheduleSettings extends RsScheduleBase {
  @property({ attribute: false }) public schedules: ScheduleEntry[] = [];
  /** Alias: parent passes .scheduleSelectorEntity, base uses selectorEntity. */
  @property({ type: String }) public set scheduleSelectorEntity(v: string) {
    this.selectorEntity = v;
  }
  public get scheduleSelectorEntity(): string {
    return this.selectorEntity;
  }
  /** Alias: parent passes .activeScheduleIndex, base uses activeIndex. */
  @property({ type: Number }) public set activeScheduleIndex(v: number) {
    this.activeIndex = v;
  }
  public get activeScheduleIndex(): number {
    return this.activeIndex;
  }
  @property({ type: Number }) public comfortHeat = 21.0;
  @property({ type: Number }) public comfortCool = 24.0;
  @property({ type: Number }) public ecoHeat = 17.0;
  @property({ type: Number }) public ecoCool = 27.0;
  @property({ type: String }) public climateMode: ClimateMode = "auto";

  /** Index of the inline schedule whose weekly editor is open, or -1. */
  @state() private _editingBlocksIndex = -1;
  /** Index of the schedule currently being imported from its helper, or -1. */
  @state() private _importingIndex = -1;
  @state() private _importError = "";

  static styles = [
    RsScheduleBase.sharedStyles,
    inputStyles,
    css`
      .blocks-editor {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin: 0 0 12px;
        padding: 12px 14px;
        border-radius: 8px;
        background: var(--secondary-background-color, rgba(0, 0, 0, 0.03));
      }

      .import-error {
        font-size: 12px;
        color: var(--error-color, #db4437);
        margin-bottom: 8px;
      }

      .add-inline-button {
        font: inherit;
        color: var(--primary-color);
        background: none;
        border: 1px dashed var(--primary-color);
        border-radius: 8px;
        padding: 8px 12px;
        cursor: pointer;
        margin-bottom: 12px;
        width: 100%;
      }

      .fallback-hint {
        font-size: 11px;
        color: var(--secondary-text-color);
        margin-top: 4px;
        font-style: italic;
      }

      .temp-inputs {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        margin-top: 16px;
      }

      .temp-input-group {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }

      ha-textfield {
        flex: 1;
      }

      .view-temps {
        display: flex;
        gap: 16px;
        font-size: 13px;
        color: var(--secondary-text-color);
        margin-top: 12px;
        padding-top: 12px;
        border-top: 1px solid var(--divider-color, #eee);
      }

      .view-temps span {
        font-weight: 500;
        color: var(--primary-text-color);
      }

      .view-selector-info {
        font-size: 12px;
        color: var(--secondary-text-color);
        margin-top: 8px;
      }

      .temp-grid-auto {
        display: grid;
        grid-template-columns: auto 1fr 1fr;
        gap: 8px 12px;
        align-items: center;
        margin-top: 16px;
      }
      .temp-grid-header {
        font-size: 12px;
        font-weight: 600;
        color: var(--secondary-text-color);
        text-transform: uppercase;
        letter-spacing: 0.3px;
        text-align: center;
      }
      .temp-grid-row-label {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 13px;
        font-weight: 500;
        color: var(--secondary-text-color);
        white-space: nowrap;
      }

      @media (max-width: 600px) {
        .temp-grid-auto {
          grid-template-columns: 1fr 1fr;
        }
        .temp-grid-row-label {
          grid-column: 1 / -1;
          margin-top: 8px;
        }
        .temp-grid-header {
          display: none;
        }
      }
    `,
  ];

  render() {
    if (!this.editing) {
      return this._renderViewMode();
    }
    return this._renderEditMode();
  }

  // ─── View mode ─────────────────────────────────────────────────

  private _renderViewMode() {
    const l = this.hass.language;
    const hasMultiple = this.schedules.length >= 2;

    return html`
      ${this.schedules.length > 0
        ? html`
            <div class="schedule-list">
              ${this.schedules.map((schedule, index) => {
                const state = this._getScheduleState(index, this.schedules.length);
                return html`
                  <div class="schedule-row ${state}">
                    ${hasMultiple
                      ? html`<span class="schedule-number">${index + 1}</span>`
                      : nothing}
                    <span class="schedule-status-dot"></span>
                    ${isInlineSchedule(schedule)
                      ? html`<span class="schedule-name">${this._getScheduleLabel(schedule)}</span>`
                      : html`<span
                          class="schedule-name schedule-link"
                          @click=${() => this._openEntityInfo(schedule.entity_id!)}
                          >${this._getScheduleLabel(schedule)}</span
                        >`}
                    <span class="schedule-status">${this._getStatusText(index, state)}</span>
                  </div>
                `;
              })}
            </div>
          `
        : html`<div class="no-schedules">${localize("schedule.no_schedules", l)}</div>`}
      ${this.climateMode === "auto"
        ? html`
            <div class="view-temps">
              ${localize("schedule.view_heat", l, {
                comfort: formatTemp(this.comfortHeat, this.hass),
                eco: formatTemp(this.ecoHeat, this.hass),
                unit: tempUnit(this.hass),
              })}
               · 
              ${localize("schedule.view_cool", l, {
                comfort: formatTemp(this.comfortCool, this.hass),
                eco: formatTemp(this.ecoCool, this.hass),
                unit: tempUnit(this.hass),
              })}
            </div>
          `
        : html`
            <div class="view-temps">
              ${localize("schedule.view_comfort", l, {
                temp: formatTemp(
                  this.climateMode === "cool_only" ? this.comfortCool : this.comfortHeat,
                  this.hass,
                ),
                unit: tempUnit(this.hass),
              })}
               · 
              ${localize("schedule.view_eco", l, {
                temp: formatTemp(
                  this.climateMode === "cool_only" ? this.ecoCool : this.ecoHeat,
                  this.hass,
                ),
                unit: tempUnit(this.hass),
              })}
            </div>
          `}
      ${this.scheduleSelectorEntity
        ? html`<div class="view-selector-info">
            ${localize("schedule.view_selector_prefix", l)}
            <span
              class="schedule-link"
              @click=${() => this._openEntityInfo(this.scheduleSelectorEntity!)}
              >${this._getFriendlyName(this.scheduleSelectorEntity)}</span
            >
          </div>`
        : nothing}
    `;
  }

  // ─── Edit mode ─────────────────────────────────────────────────

  private _renderEditMode() {
    const l = this.hass.language;
    const count = this.schedules.length;
    const usedIds = new Set(
      this.schedules.map((schedule) => schedule.entity_id).filter((id): id is string => !!id),
    );

    return html`
      ${this._renderScheduleList()}
      ${this._importError
        ? html`<div class="import-error">
            ${localize("schedule.editor_import_failed", l, { error: this._importError })}
          </div>`
        : nothing}
      <button class="add-inline-button" @click=${this._addInlineSchedule}>
        ${localize("schedule.editor_add_inline", l)}
      </button>
      ${this._renderAddRow(
        localize("schedule.select_schedule", l),
        this._getAvailableEntities(usedIds),
        (eid) => this._addSchedule(eid),
        localize("schedule.create_helper_hint", l),
      )}
      ${this._renderSelectorSection(
        count,
        localize("schedule.selector_label", l),
        this.scheduleSelectorEntity ? this._getSelectorValueText(l) : "",
        localize("schedule.selector_warning", l),
        (value) => this._onSelectorEntityChange(value),
      )}
      ${this._renderTemperatureInputs(l)}
    `;
  }

  // ─── Schedule list ─────────────────────────────────────────────

  private _renderScheduleList() {
    const l = this.hass.language;
    const count = this.schedules.length;

    if (count === 0) {
      return html`<div class="no-schedules">${localize("schedule.no_schedules", l)}</div>`;
    }

    return html`
      <div class="schedule-list">
        ${this.schedules.map((schedule, index) => {
          const state = this._getScheduleState(index, count);
          return html`
            <div class="schedule-row ${state}">
              ${count >= 2 ? html`<span class="schedule-number">${index + 1}</span>` : nothing}
              <span class="schedule-status-dot"></span>
              <span class="schedule-name">${this._getScheduleLabel(schedule)}</span>
              <span class="schedule-status">${this._getStatusText(index, state)}</span>
              ${isInlineSchedule(schedule)
                ? html`
                    <ha-icon-button
                      .path=${RsScheduleSettings.ICON_EDIT}
                      .label=${localize("schedule.editor_edit_blocks", l)}
                      @click=${() =>
                        (this._editingBlocksIndex =
                          this._editingBlocksIndex === index ? -1 : index)}
                    ></ha-icon-button>
                  `
                : html`
                    <ha-icon-button
                      .path=${RsScheduleSettings.ICON_IMPORT}
                      .label=${localize("schedule.editor_import", l)}
                      .disabled=${this._importingIndex === index}
                      @click=${() => this._importSchedule(index)}
                    ></ha-icon-button>
                  `}
              ${this._renderScheduleControls(
                index,
                count,
                (i, dir) => this._moveSchedule(i, dir),
                (i) => this._removeSchedule(i),
              )}
            </div>
            ${this._editingBlocksIndex === index && isInlineSchedule(schedule)
              ? html`
                  <div class="blocks-editor">
                    <ha-textfield
                      .label=${localize("schedule.editor_name", l)}
                      .value=${schedule.name ?? ""}
                      @change=${(event: Event) =>
                        this._patchSchedule(index, {
                          name: (event.target as HTMLElement & { value: string }).value,
                        })}
                    ></ha-textfield>
                    <rs-schedule-editor
                      .hass=${this.hass}
                      .blocks=${schedule.blocks ?? emptyBlocks()}
                      .climateMode=${this.climateMode}
                      @blocks-changed=${(event: CustomEvent<{ value: ScheduleBlocks }>) => {
                        event.stopPropagation();
                        this._patchSchedule(index, { blocks: event.detail.value });
                      }}
                    ></rs-schedule-editor>
                  </div>
                `
              : nothing}
          `;
        })}
      </div>
    `;
  }

  // ─── Temperature inputs ────────────────────────────────────────

  private _renderTemperatureInputs(l: string) {
    if (this.climateMode === "auto") {
      return html`
        <div class="temp-grid-auto">
          <div class="temp-grid-header"></div>
          <div class="temp-grid-header">${localize("schedule.column_comfort", l)}</div>
          <div class="temp-grid-header">${localize("schedule.column_eco", l)}</div>
          <div class="temp-grid-row-label">
            <ha-icon icon="mdi:fire" style="--mdc-icon-size:16px"></ha-icon>
            ${localize("schedule.row_heat", l)}
          </div>
          <ha-textfield
            type="number"
            .value=${String(toDisplay(this.comfortHeat, this.hass))}
            suffix=${tempUnit(this.hass)}
            step=${tempStep(this.hass)}
            min=${tempRange(5, 35, this.hass).min}
            max=${tempRange(5, 35, this.hass).max}
            @change=${this._onComfortHeatChange}
          ></ha-textfield>
          <ha-textfield
            type="number"
            .value=${String(toDisplay(this.ecoHeat, this.hass))}
            suffix=${tempUnit(this.hass)}
            step=${tempStep(this.hass)}
            min=${tempRange(5, 35, this.hass).min}
            max=${tempRange(5, 35, this.hass).max}
            @change=${this._onEcoHeatChange}
          ></ha-textfield>
          <div class="temp-grid-row-label">
            <ha-icon icon="mdi:snowflake" style="--mdc-icon-size:16px"></ha-icon>
            ${localize("schedule.row_cool", l)}
          </div>
          <ha-textfield
            type="number"
            .value=${String(toDisplay(this.comfortCool, this.hass))}
            suffix=${tempUnit(this.hass)}
            step=${tempStep(this.hass)}
            min=${tempRange(5, 35, this.hass).min}
            max=${tempRange(5, 35, this.hass).max}
            @change=${this._onComfortCoolChange}
          ></ha-textfield>
          <ha-textfield
            type="number"
            .value=${String(toDisplay(this.ecoCool, this.hass))}
            suffix=${tempUnit(this.hass)}
            step=${tempStep(this.hass)}
            min=${tempRange(5, 35, this.hass).min}
            max=${tempRange(5, 35, this.hass).max}
            @change=${this._onEcoCoolChange}
          ></ha-textfield>
        </div>
      `;
    }

    return html`
      <div class="temp-inputs">
        <div class="temp-input-group">
          <ha-textfield
            type="number"
            label=${localize("schedule.comfort_label", l)}
            suffix=${tempUnit(this.hass)}
            step=${tempStep(this.hass)}
            .value=${String(
              toDisplay(
                this.climateMode === "cool_only" ? this.comfortCool : this.comfortHeat,
                this.hass,
              ),
            )}
            min=${tempRange(5, 35, this.hass).min}
            max=${tempRange(5, 35, this.hass).max}
            @change=${this.climateMode === "cool_only"
              ? this._onComfortCoolChange
              : this._onComfortHeatChange}
          ></ha-textfield>
        </div>
        <div class="temp-input-group">
          <ha-textfield
            type="number"
            label=${localize("schedule.eco_label", l)}
            suffix=${tempUnit(this.hass)}
            step=${tempStep(this.hass)}
            .value=${String(
              toDisplay(this.climateMode === "cool_only" ? this.ecoCool : this.ecoHeat, this.hass),
            )}
            min=${tempRange(5, 35, this.hass).min}
            max=${tempRange(5, 35, this.hass).max}
            @change=${this.climateMode === "cool_only"
              ? this._onEcoCoolChange
              : this._onEcoHeatChange}
          ></ha-textfield>
        </div>
      </div>
      <div class="fallback-hint">${localize("schedule.comfort_hint", l)}</div>
    `;
  }

  // ─── Selector value text ───────────────────────────────────────

  private _getSelectorValueText(l: string): string {
    const selectorState = this.hass?.states?.[this.scheduleSelectorEntity];
    if (!selectorState) return "";
    if (this.scheduleSelectorEntity.startsWith("input_boolean.")) {
      return localize("schedule.selector_value_boolean", l, {
        value: selectorState.state === "on" ? "On" : "Off",
      });
    }
    return localize("schedule.selector_value_number", l, {
      value: selectorState.state,
    });
  }

  // ─── Status text (temperature-specific) ────────────────────────

  private _getStatusText(index: number, state: "active" | "inactive" | "unreachable"): string {
    const l = this.hass.language;

    if (state === "unreachable") return localize("schedule.state_unreachable", l);
    if (state === "inactive") return localize("schedule.state_inactive", l);

    const schedule = this.schedules[index];
    if (isInlineSchedule(schedule)) {
      return this._blockStatusText(findActiveBlock(schedule.blocks)?.data ?? null, l);
    }
    const entityState = this.hass?.states?.[schedule.entity_id!];
    if (!entityState) return localize("schedule.state_active", l);

    return this._blockStatusText(
      entityState.state === "on" ? (entityState.attributes ?? {}) : null,
      l,
    );
  }

  /** Render the active block's temperatures; `null` data means the schedule is off. */
  private _blockStatusText(data: ScheduleBlockData | null, l: string): string {
    if (data === null) {
      return localize("schedule.eco_detail", l, {
        temp: formatTemp(this.climateMode === "cool_only" ? this.ecoCool : this.ecoHeat, this.hass),
        unit: tempUnit(this.hass),
      });
    }

    if (data.temperature != null) {
      return localize("schedule.from_schedule", l, {
        temp: formatTemp(data.temperature, this.hass),
        unit: tempUnit(this.hass),
      });
    }

    if (data.heat_temperature != null || data.cool_temperature != null) {
      return localize("schedule.from_schedule_split", l, {
        heat: formatTemp(data.heat_temperature ?? this.comfortHeat, this.hass),
        cool: formatTemp(data.cool_temperature ?? this.comfortCool, this.hass),
        unit: tempUnit(this.hass),
      });
    }

    return localize("schedule.fallback", l, {
      temp: formatTemp(
        this.climateMode === "cool_only" ? this.comfortCool : this.comfortHeat,
        this.hass,
      ),
      unit: tempUnit(this.hass),
    });
  }

  private static readonly ICON_IMPORT =
    "M13,5V11H14.17L12,13.17L9.83,11H11V5H13M15,3H9V9H5L12,16L19,9H15V3M19,18H5V20H19V18Z";

  private static readonly ICON_EDIT =
    "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z";

  // ─── Schedule management ───────────────────────────────────────

  private _getScheduleLabel(schedule: ScheduleEntry): string {
    if (isInlineSchedule(schedule)) {
      return schedule.name || localize("schedule.editor_untitled", this.hass.language);
    }
    return this._getFriendlyName(schedule.entity_id!);
  }

  private _addInlineSchedule() {
    this._editingBlocksIndex = this.schedules.length;
    this._emitSchedules([
      ...this.schedules,
      {
        name: localize("schedule.editor_untitled", this.hass.language),
        blocks: emptyBlocks(),
      },
    ]);
  }

  /**
   * Copy a helper's weekly blocks into an inline schedule. The helper is left
   * in place, so an unwanted import is undone by re-adding it.
   */
  private async _importSchedule(index: number) {
    const entityId = this.schedules[index]?.entity_id;
    if (!entityId) return;

    this._importingIndex = index;
    this._importError = "";
    try {
      const imported = await this.hass.callWS<{ name: string; blocks: ScheduleBlocks }>({
        type: "roommind/schedule/import",
        entity_id: entityId,
      });
      this._emitSchedules(
        this.schedules.map((schedule, scheduleIndex) =>
          scheduleIndex === index ? { name: imported.name, blocks: imported.blocks } : schedule,
        ),
      );
      this._editingBlocksIndex = index;
    } catch (error) {
      this._importError = error instanceof Error ? error.message : String(error);
    } finally {
      this._importingIndex = -1;
    }
  }

  private _patchSchedule(index: number, patch: Partial<ScheduleEntry>) {
    this._emitSchedules(
      this.schedules.map((schedule, scheduleIndex) =>
        scheduleIndex === index ? { ...schedule, ...patch } : schedule,
      ),
    );
  }

  private _addSchedule(entityId: string) {
    this._emitSchedules([...this.schedules, { entity_id: entityId }]);
  }

  private _removeSchedule(index: number) {
    this._emitSchedules(this.schedules.filter((_, i) => i !== index));
  }

  private _moveSchedule(index: number, direction: -1 | 1) {
    const target = index + direction;
    if (target < 0 || target >= this.schedules.length) return;
    const next = [...this.schedules];
    [next[index], next[target]] = [next[target], next[index]];
    this._emitSchedules(next);
  }

  private _emitSchedules(value: ScheduleEntry[]) {
    this.dispatchEvent(
      new CustomEvent("schedules-changed", {
        detail: { value },
        bubbles: true,
        composed: true,
      }),
    );
  }

  private _onSelectorEntityChange(value: string) {
    this.dispatchEvent(
      new CustomEvent("schedule-selector-changed", {
        detail: { value },
        bubbles: true,
        composed: true,
      }),
    );
  }

  // ─── Temperature change handlers ──────────────────────────────

  private _onComfortHeatChange(e: Event) {
    const target = e.target as HTMLElement & { value: string };
    const val = toCelsius(parseFloat(target.value) || toDisplay(21.0, this.hass), this.hass);
    this.dispatchEvent(
      new CustomEvent("comfort-heat-changed", {
        detail: { value: val },
        bubbles: true,
        composed: true,
      }),
    );
    if (this.comfortCool < val) {
      this.dispatchEvent(
        new CustomEvent("comfort-cool-changed", {
          detail: { value: val },
          bubbles: true,
          composed: true,
        }),
      );
    }
  }

  private _onComfortCoolChange(e: Event) {
    const target = e.target as HTMLElement & { value: string };
    const val = toCelsius(parseFloat(target.value) || toDisplay(24.0, this.hass), this.hass);
    this.dispatchEvent(
      new CustomEvent("comfort-cool-changed", {
        detail: { value: val },
        bubbles: true,
        composed: true,
      }),
    );
    if (this.comfortHeat > val) {
      this.dispatchEvent(
        new CustomEvent("comfort-heat-changed", {
          detail: { value: val },
          bubbles: true,
          composed: true,
        }),
      );
    }
  }

  private _onEcoHeatChange(e: Event) {
    const target = e.target as HTMLElement & { value: string };
    const val = toCelsius(parseFloat(target.value) || toDisplay(17.0, this.hass), this.hass);
    this.dispatchEvent(
      new CustomEvent("eco-heat-changed", {
        detail: { value: val },
        bubbles: true,
        composed: true,
      }),
    );
    if (this.ecoCool < val) {
      this.dispatchEvent(
        new CustomEvent("eco-cool-changed", {
          detail: { value: val },
          bubbles: true,
          composed: true,
        }),
      );
    }
  }

  private _onEcoCoolChange(e: Event) {
    const target = e.target as HTMLElement & { value: string };
    const val = toCelsius(parseFloat(target.value) || toDisplay(27.0, this.hass), this.hass);
    this.dispatchEvent(
      new CustomEvent("eco-cool-changed", {
        detail: { value: val },
        bubbles: true,
        composed: true,
      }),
    );
    if (this.ecoHeat > val) {
      this.dispatchEvent(
        new CustomEvent("eco-heat-changed", {
          detail: { value: val },
          bubbles: true,
          composed: true,
        }),
      );
    }
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-schedule-settings": RsScheduleSettings;
  }
}
