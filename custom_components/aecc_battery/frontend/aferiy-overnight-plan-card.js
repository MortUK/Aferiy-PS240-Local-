class AferiyOvernightPlanCard extends HTMLElement {
  setConfig(config) {
    this.config = config || {};
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  getCardSize() {
    return 8;
  }

  render() {
    if (!this._hass) {
      return;
    }

    const hass = this._hass;
    const config = this.config || {};
    const recommended = this._findState(
      config.recommended_entity,
      "sensor",
      "_recommended_overnight_soc",
    );
    const overnightStatus = this._findState(
      config.overnight_status_entity,
      "sensor",
      "_automatic_overnight_charging_status",
    );
    const solarAvailability = this._findState(
      config.solar_availability_entity,
      "select",
      "_solar_availability",
    );
    const smartHistory = this._findState(
      config.smart_history_entity,
      "sensor",
      "_smart_history",
    );

    const attrs = recommended?.attributes || {};
    const breakdown = attrs.target_breakdown || {};
    const title = config.title || "Overnight Charge Plan";
    const target = this._stateText(recommended, "%");
    const plannedNeed = this._plannedNeedKwh(recommended, attrs, breakdown);
    const needed = this._numberText(plannedNeed, 3, "kWh");
    const status = this._cleanText(overnightStatus?.state, "Waiting");
    const tariff = this._tariffLabel(hass);
    const solarMode = this._solarMode(solarAvailability, attrs);
    const demand = this._numberText(
      breakdown.projected_house_demand_kwh,
      1,
      "kWh",
    );
    const solar = this._numberText(breakdown.projected_solar_kwh, 1, "kWh");
    const pre = this._numberText(breakdown.pre_sunrise_need_kwh, 1, "kWh");
    const uncovered = this._numberText(
      breakdown.uncovered_shortfall_kwh ?? attrs.uncovered_shortfall_kwh ?? 0,
      1,
      "kWh",
    );
    const wholeShortfall = this._numberText(
      attrs.whole_day_net_shortfall_kwh,
      1,
      "kWh",
      true,
    );
    const batteryCapacity = this._numberText(
      breakdown.battery_capacity_kwh ?? attrs.battery_capacity_kwh,
      2,
      "kWh",
    );
    const postSunset = this._numberText(breakdown.post_sunset_need_kwh, 2, "kWh");
    const postAt = this._timeText(breakdown.post_sunset_start_at);
    const usefulSolar = this._timeText(attrs.solar_break_even_at, true)
      || "No break-even in forecast window";
    const confidence = this._titleText(attrs.forecast_confidence, "Waiting");
    const history = this._cleanText(attrs.recorder_history_status, "warming").replaceAll("_", " ");
    const smartHistoryText = smartHistory && this._isKnown(smartHistory.state)
      ? `${Math.round(Number(smartHistory.state))}% complete`
      : "Waiting";
    const method = this._cleanText(attrs.method, "Waiting for Solcast/history").replaceAll("_", " ");

    this.innerHTML = `
      <ha-card>
        <style>
          :host {
            --aferiy-accent: var(--primary-color, #03a9f4);
            --aferiy-ok: #4caf50;
            --aferiy-warn: #ffc107;
            --aferiy-alert: #ff5252;
            display: block;
          }
          ha-card {
            padding: 12px;
            background: var(--ha-card-background, var(--card-background-color));
            color: var(--primary-text-color);
          }
          .header {
            display: grid;
            grid-template-columns: 34px 1fr;
            gap: 10px;
            align-items: center;
            border: 1px solid var(--divider-color);
            border-radius: 10px;
            padding: 12px;
            margin-bottom: 10px;
          }
          .title {
            font-size: 16px;
            font-weight: 700;
            line-height: 1.2;
          }
          .subtitle {
            color: var(--secondary-text-color);
            font-size: 13px;
            margin-top: 2px;
          }
          .icon {
            color: var(--aferiy-accent);
            --mdc-icon-size: 30px;
          }
          .tiles {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
          }
          .tiles.two {
            grid-template-columns: repeat(2, minmax(0, 1fr));
            margin-top: 8px;
          }
          .tile {
            min-height: 82px;
            border: 1px solid var(--divider-color);
            border-radius: 10px;
            padding: 10px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            text-align: center;
            gap: 4px;
          }
          .tile ha-icon {
            color: var(--tile-color, var(--aferiy-accent));
            --mdc-icon-size: 24px;
          }
          .tile .value {
            font-size: 15px;
            font-weight: 700;
            line-height: 1.2;
            overflow-wrap: anywhere;
          }
          .tile .label {
            color: var(--secondary-text-color);
            font-size: 12px;
            line-height: 1.2;
            overflow-wrap: anywhere;
          }
          .explain {
            border: 1px solid var(--divider-color);
            border-radius: 10px;
            margin-top: 10px;
            padding: 12px;
            font-size: 14px;
            line-height: 1.55;
          }
          .explain b {
            font-weight: 700;
          }
          @media (max-width: 480px) {
            .tiles,
            .tiles.two {
              grid-template-columns: 1fr;
            }
          }
        </style>

        <div class="header">
          <ha-icon class="icon" icon="mdi:battery-clock"></ha-icon>
          <div>
            <div class="title">${this._escape(title)}</div>
            <div class="subtitle">Target ${this._escape(target)} · Need ${this._escape(needed)} · ${this._escape(this._cleanText(attrs.status, "Estimated"))}</div>
          </div>
        </div>

        <div class="tiles">
          ${this._tile("mdi:battery-clock", status, "Overnight Status", "#9e9e9e")}
          ${this._tile("mdi:solar-power-variant", solarMode, "Solar Mode", "var(--aferiy-warn)")}
          ${this._tile("mdi:cash-clock", tariff, "Tariff", "#9e9e9e")}
        </div>

        <div class="tiles two">
          ${this._tile("mdi:home-lightning-bolt", demand, "Projected house demand", "#ff9800")}
          ${this._tile("mdi:solar-power-variant", solar, "Projected solar", "var(--aferiy-warn)")}
          ${this._tile("mdi:weather-sunset-up", pre, "Pre-Sunrise Need", "#ff6d00")}
          ${this._tile("mdi:battery-alert", uncovered, "Cannot cover from battery", "var(--aferiy-alert)")}
        </div>

        <div class="explain">
          <div><b>Target:</b> ${this._escape(target)}</div>
          <div><b>Battery capacity:</b> ${this._escape(batteryCapacity)}</div>
          <div><b>Day balance:</b> ${this._escape(demand)} demand · ${this._escape(solar)} solar${wholeShortfall ? ` · ${this._escape(wholeShortfall)} shortfall` : ""}</div>
          <div><b>Battery reserve:</b> Pre-sunrise ${this._escape(this._numberText(breakdown.pre_sunrise_need_kwh, 2, "kWh"))} · Post-sunset ${this._escape(postSunset)}${postAt ? ` from ${this._escape(postAt)}` : ""}</div>
          <div><b>Useful solar:</b> ${this._escape(usefulSolar)}</div>
          <div><b>Confidence:</b> ${this._escape(confidence)} · History ${this._escape(history)}</div>
          <div><b>Smart History:</b> ${this._escape(smartHistoryText)}</div>
          <div><b>Mode:</b> ${this._escape(solarMode === "Batteries Only" ? "Batteries Only" : `Forecast solar · ${method}`)}</div>
        </div>
      </ha-card>
    `;
  }

  _findState(configuredEntity, domain, suffix) {
    const states = this._hass?.states || {};
    if (configuredEntity && states[configuredEntity]) {
      return states[configuredEntity];
    }

    const exact = `${domain}.aferiy_ps240_local${suffix}`;
    if (states[exact]) {
      return states[exact];
    }

    const garage = `${domain}.garage_aferiy_ps240_local${suffix}`;
    if (states[garage]) {
      return states[garage];
    }

    return Object.values(states).find((state) => (
      state.entity_id.startsWith(`${domain}.`)
      && state.entity_id.endsWith(suffix)
    ));
  }

  _tile(icon, value, label, color) {
    return `
      <div class="tile" style="--tile-color: ${color}">
        <ha-icon icon="${icon}"></ha-icon>
        <div class="value">${this._escape(value)}</div>
        <div class="label">${this._escape(label)}</div>
      </div>
    `;
  }

  _tariffLabel(hass) {
    const offPeak = Object.values(hass.states).find((state) => (
      state.entity_id.endsWith("_off_peak") && state.state === "on"
    ));
    if (offPeak) {
      return "Off-Peak";
    }

    const free = Object.values(hass.states).find((state) => (
      state.entity_id.includes("octopus")
      && state.entity_id.includes("free")
      && state.state === "on"
    ));
    return free ? "Free" : "Peak";
  }

  _solarMode(solarAvailability, attrs) {
    if (solarAvailability?.state === "Solar Unavailable"
      || attrs.solar_override_status === "Batteries Only"
      || attrs.solar_unavailable_override === true) {
      return "Batteries Only";
    }
    return "Forecast";
  }

  _stateText(state, suffix = "") {
    if (!state || !this._isKnown(state.state)) {
      return "Waiting";
    }
    return `${state.state}${suffix}`;
  }

  _numberText(value, decimals, suffix, hideZero = false) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "Waiting";
    }
    if (hideZero && Math.abs(number) < 0.05) {
      return "";
    }
    return `${number.toFixed(decimals)} ${suffix}`;
  }

  _plannedNeedKwh(recommended, attrs, breakdown) {
    const direct = Number(
      breakdown.battery_energy_needed_kwh
      ?? attrs.battery_energy_needed_kwh
      ?? breakdown.required_battery_energy_before_buffer_kwh
      ?? attrs.required_battery_energy_before_buffer_kwh
    );
    if (Number.isFinite(direct) && direct > 0) {
      return direct;
    }

    const target = Number(recommended?.state);
    const reserve = Number(
      breakdown.reserve_soc
      ?? attrs.reserve_soc
      ?? breakdown.min_soc
      ?? attrs.min_soc
      ?? 10,
    );
    const capacity = Number(
      breakdown.battery_capacity_kwh
      ?? attrs.battery_capacity_kwh,
    );
    if (
      Number.isFinite(target)
      && Number.isFinite(reserve)
      && Number.isFinite(capacity)
      && target > reserve
    ) {
      return capacity * ((target - reserve) / 100);
    }

    return Number.NaN;
  }

  _timeText(value, includeDay = false) {
    if (!value) {
      return "";
    }
    const timestamp = Date.parse(value);
    if (Number.isNaN(timestamp)) {
      return "";
    }
    const options = includeDay
      ? { weekday: "short", hour: "2-digit", minute: "2-digit" }
      : { hour: "2-digit", minute: "2-digit" };
    return new Intl.DateTimeFormat(undefined, options).format(new Date(timestamp));
  }

  _titleText(value, fallback) {
    return this._cleanText(value, fallback)
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  _cleanText(value, fallback) {
    if (value === undefined || value === null || value === "") {
      return fallback;
    }
    const text = String(value);
    return this._isKnown(text) ? text : fallback;
  }

  _isKnown(value) {
    return !["unknown", "unavailable", "none", "None", ""].includes(String(value));
  }

  _escape(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }
}

customElements.define("aferiy-overnight-plan-card", AferiyOvernightPlanCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "aferiy-overnight-plan-card",
  name: "AFERIY Overnight Plan",
  description: "Shows the PS240 smart overnight charge target, solar forecast balance, reserves, and history confidence.",
  preview: true,
  documentationURL: "https://github.com/MortUK/Aferiy-PS240-Local-",
});
