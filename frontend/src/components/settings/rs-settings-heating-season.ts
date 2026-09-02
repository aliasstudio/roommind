/**
 * rs-settings-heating-season – Seasonal on/off for the water heating circuit.
 */
import { html } from "lit";
import { RsSettingsBase } from "./rs-settings-base";
import { customElement, property } from "lit/decorators.js";
import type { HomeAssistant } from "../../types";
import { localize } from "../../utils/localize";

@customElement("rs-settings-heating-season")
export class RsSettingsHeatingSeason extends RsSettingsBase {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ type: Boolean }) public heatingSeason = true;

  render() {
    const l = this.hass.language;

    return html`
      <div class="toggle-row">
        <div class="toggle-text">
          <span class="toggle-label">${localize("heating_season.title", l)}</span>
          <span class="toggle-hint">${localize("heating_season.hint", l)}</span>
        </div>
        <ha-switch
          .checked=${this.heatingSeason}
          @change=${(e: Event) =>
            this._fire("heatingSeason", (e.target as HTMLInputElement).checked)}
        ></ha-switch>
      </div>
    `;
  }

  static styles = [RsSettingsBase.settingsBaseStyles];
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-settings-heating-season": RsSettingsHeatingSeason;
  }
}
