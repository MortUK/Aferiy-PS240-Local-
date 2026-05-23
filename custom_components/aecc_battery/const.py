"""Constants for the AFERIY PS240 local integration."""

DOMAIN = "aecc_battery"

# Config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_NAME = "name"
CONF_EXTENDED_POWER = "extended_power"
CONF_ADVANCED_ENERGY_SENSORS = "advanced_energy_sensors"
CONF_POLL_INTERVAL = "poll_interval"
CONF_MANUFACTURER = "manufacturer"
CONF_MODEL = "model"

# Default connection values
DEFAULT_HOST = "192.168.0.1"
DEFAULT_PORT = 8080
DEFAULT_NAME = "AFERIY PS240 (Local)"
DEFAULT_MANUFACTURER = "AFERIY"
DEFAULT_MODEL = "PS240"
DEFAULT_TIMEOUT = 5  # seconds
POLL_INTERVAL = 5  # seconds – change this to update faster/slower
MIN_POLL_INTERVAL = 2  # seconds – hard floor to avoid flooding the device
MAX_REGISTER_POWER_DEFAULT = 800  # watts – observed reliable local TCP output limit
PS240_EXPERIMENTAL_MAX_OUTPUT_W = 1200  # watts – exposed for cautious local testing
MAX_BATTERY_POWER_W = PS240_EXPERIMENTAL_MAX_OUTPUT_W
BATTERY_MODULE_CAPACITY_KWH = 1.958
DEFAULT_BATTERY_MODULE_COUNT = 3
DEFAULT_BATTERY_CAPACITY_KWH = round(
    BATTERY_MODULE_CAPACITY_KWH * DEFAULT_BATTERY_MODULE_COUNT,
    3,
)
BATTERY_CAPACITY_PRESET_MODULE_COUNTS = tuple(range(1, 16))


def battery_capacity_for_modules(module_count: int) -> float:
    """Return total capacity for an AFERIY stack module count."""
    return round(float(module_count) * BATTERY_MODULE_CAPACITY_KWH, 3)


def battery_capacity_preset_label(module_count: int) -> str:
    """Human-readable capacity preset label."""
    capacity = battery_capacity_for_modules(module_count)
    suffix = "module" if module_count == 1 else "modules"
    return f"{module_count} {suffix} ({capacity:.3f} kWh)"

# ─── Sensor cleaning profile ─────────────────────────────────────────────────
# Per-brand thresholds for the physics-aware SOC cleaner.
# - soc_zero_reject_during_active_w: reject SOC=0 readings when the absolute
#   wall-side power exceeds this threshold (battery is clearly in motion, so
#   SOC cannot have collapsed to 0 instantaneously).
# - soc_max_rate_pct_per_min: discard SOC readings whose change rate from the
#   last accepted sample exceeds this. Catches BMS step-jump glitches.
# - hold_last_value_seconds: how long an entity may keep returning its last
#   accepted value after readings start being rejected before going
#   "unavailable". Hybrid pattern: smooth charts for transient blips, honest
#   signal for prolonged sensor failure.
#
# Lunergy is the known-bad device (sustained SOC=0 lockups during active
# discharge). Sunpura / others are stable and get a permissive profile that
# only catches obvious physical impossibilities.
# ──────────────────────────────────────────────────────────────────────────────

CONF_BRAND_PROFILE_KEY = "brand_profile"

BRAND_PROFILES: dict[str, dict[str, float | int]] = {
    "AFERIY": {
        "soc_zero_reject_during_active_w": 100,
        "soc_max_rate_pct_per_min": 8.0,
        "hold_last_value_seconds": 120,
    },
    "Lunergy": {
        "soc_zero_reject_during_active_w": 50,
        "soc_max_rate_pct_per_min": 5.0,
        "hold_last_value_seconds": 120,
    },
    "Sunpura": {
        "soc_zero_reject_during_active_w": 200,
        "soc_max_rate_pct_per_min": 10.0,
        "hold_last_value_seconds": 120,
    },
    "Voltdeer": {
        "soc_zero_reject_during_active_w": 200,
        "soc_max_rate_pct_per_min": 10.0,
        "hold_last_value_seconds": 120,
    },
    "AEG": {
        "soc_zero_reject_during_active_w": 200,
        "soc_max_rate_pct_per_min": 10.0,
        "hold_last_value_seconds": 120,
    },
    "Other": {
        "soc_zero_reject_during_active_w": 100,
        "soc_max_rate_pct_per_min": 8.0,
        "hold_last_value_seconds": 120,
    },
}

BRAND_PROFILES["Richard Owen"] = BRAND_PROFILES["AFERIY"]

DEFAULT_BRAND_PROFILE: dict[str, float | int] = BRAND_PROFILES["Richard Owen"]

# ─── Control register addresses (confirmed by register scan) ─────────────────
REG_EMS_ENABLE = "3000"  # 0 = off, 1 = on
REG_SCHEDULE_MODE = "3020"  # Schedule mode (6 = custom schedule)
REG_AI_SMART_CHARGE = "3021"  # 0 = off, 1 = on
REG_AI_SMART_DISC = "3022"  # 0 = off, 1 = on
REG_CUSTOM_MODE = "3030"  # 0 = off, 1 = on

# Power setpoint, time-slot format (confirmed from scan):
#   "timeSwitch,startHH:MM,endHH:MM,powerW,0,mode,0,0,0,chargingSOC,dischargingSOC"
#   e.g. "1,00:00,23:59,800,0,6,0,0,0,100,10"     (discharge at 800 W)
#        "1,00:00,23:59,-800,0,6,0,0,0,100,10"    (charge at 800 W)
#        "0,00:00,00:00,0,0,0,0,0,0,100,10"       (idle / disabled)
REG_CONTROL_TIME1 = "3003"  # First active time slot

REG_MIN_SOC = "3023"  # Minimum discharge SOC  (confirmed: currently 10)
REG_MAX_SOC = "3024"  # Maximum charge SOC     (confirmed: currently 98)
REG_MAX_FEED_POWER = "3039"  # Max feed power in W (read for diagnostics; not written by default)

# Empty schedule slot - clears the active time slot so the firmware won't
# auto-re-enable EMS after a disable.
SLOT_DISABLED = "0,00:00,00:00,0,0,0,0,0,0,100,10"

# Work modes (human-readable names for the Select entity)
MODE_SELF_CONSUMPTION = "Self-Consumption (AI)"
MODE_CUSTOM = "Custom / Manual"
MODE_DISABLED = "Disabled"

WORK_MODES = [MODE_SELF_CONSUMPTION, MODE_CUSTOM, MODE_DISABLED]

# Register sets for each mode
MODE_REGISTERS = {
    MODE_SELF_CONSUMPTION: {
        REG_EMS_ENABLE: "1",
        REG_AI_SMART_CHARGE: "1",
        REG_AI_SMART_DISC: "1",
        REG_CUSTOM_MODE: "0",
        REG_CONTROL_TIME1: SLOT_DISABLED,
    },
    MODE_CUSTOM: {
        REG_EMS_ENABLE: "1",
        REG_AI_SMART_CHARGE: "0",
        REG_AI_SMART_DISC: "0",
        REG_CUSTOM_MODE: "1",
    },
    MODE_DISABLED: {
        REG_EMS_ENABLE: "0",
    },
}
