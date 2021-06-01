"""Platform to locally control Tuya-based climate devices."""
import logging
from functools import partial

import voluptuous as vol
from homeassistant.components.climate import (
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DOMAIN,
    ClimateEntity,
)
from homeassistant.components.climate.const import (  # HVAC_MODE_COOL,; HVAC_MODE_FAN_ONLY,; SUPPORT_TARGET_HUMIDITY,; SUPPORT_SWING_MODE,; SUPPORT_AUX_HEAT,
    HVAC_MODE_AUTO,
    HVAC_MODE_HEAT,
    HVAC_MODE_OFF,
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_AWAY,
    PRESET_HOME,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_TEMPERATURE_UNIT,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)

from .common import LocalTuyaEntity, async_setup_entry
from .const import (
    CONF_ZIGBEE,
    CONF_CURRENT_TEMPERATURE_DP,
    CONF_HVAC_MODE_DP,
    CONF_PRESET_MODE_DP,
    CONF_MAX_TEMP_DP,
    CONF_MIN_TEMP_DP,
    CONF_PRECISION,
    CONF_TARGET_TEMPERATURE_DP,
    CONF_TEMPERATURE_STEP,
)

_LOGGER = logging.getLogger(__name__)

TEMPERATURE_CELSIUS = "celsius"
TEMPERATURE_FAHRENHEIT = "fahrenheit"
DEFAULT_TEMPERATURE_UNIT = TEMPERATURE_CELSIUS
DEFAULT_PRECISION = PRECISION_TENTHS
DEFAULT_TEMPERATURE_STEP = PRECISION_HALVES

PRESET_REMAP = {
    PRESET_AWAY: "holiday",
    PRESET_BOOST: PRESET_BOOST.upper(),
    PRESET_HOME: "manual"
}

HVAC_PRESET_REMAP = {
    HVAC_MODE_HEAT: PRESET_REMAP[PRESET_HOME]
}

def flow_schema(dps):
    """Return schema used in config flow."""
    return {
        vol.Optional(CONF_TARGET_TEMPERATURE_DP): vol.In(dps),
        vol.Optional(CONF_CURRENT_TEMPERATURE_DP): vol.In(dps),
        vol.Optional(CONF_TEMPERATURE_STEP): vol.In(
            [PRECISION_WHOLE, PRECISION_HALVES, PRECISION_TENTHS]
        ),
        vol.Optional(CONF_HVAC_MODE_DP): vol.In(dps),
        vol.Optional(CONF_PRESET_MODE_DP): vol.In(dps),
        vol.Optional(CONF_MAX_TEMP_DP): vol.In(dps),
        vol.Optional(CONF_MIN_TEMP_DP): vol.In(dps),
        vol.Optional(CONF_PRECISION): vol.In(
            [PRECISION_WHOLE, PRECISION_HALVES, PRECISION_TENTHS]
        ),
        vol.Optional(CONF_TEMPERATURE_UNIT): vol.In(
            [TEMPERATURE_CELSIUS, TEMPERATURE_FAHRENHEIT]
        ),
    }


class LocaltuyaClimate(LocalTuyaEntity, ClimateEntity):
    """Tuya climate device."""

    def __init__(
        self,
        device,
        config_entry,
        switchid,
        **kwargs,
    ):
        """Initialize a new LocaltuyaClimate."""
        super().__init__(device, config_entry, switchid, _LOGGER, **kwargs)
        self._state = None
        self._target_temperature = None
        self._current_temperature = None
        self._min_temp = None
        self._max_temp = None
        self._hvac_mode = None
        self._hvac_action = None
        self._preset_mode = None
        self._precision = self._config.get(CONF_PRECISION, DEFAULT_PRECISION)
        print("Initialized climate [{}]".format(self.name))

    @property
    def supported_features(self):
        """Flag supported features."""
        supported_features = 0
        if self.has_config(CONF_TARGET_TEMPERATURE_DP):
            supported_features = supported_features | SUPPORT_TARGET_TEMPERATURE
        if self.has_config(CONF_PRESET_MODE_DP):
            supported_features = supported_features | SUPPORT_PRESET_MODE
        return supported_features

    @property
    def precision(self):
        """Return the precision of the system."""
        return self._precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement used by the platform."""
        if (
            self._config.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT)
            == TEMPERATURE_FAHRENHEIT
        ):
            return TEMP_FAHRENHEIT
        return TEMP_CELSIUS

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        for mode in HVAC_PRESET_REMAP:
            if HVAC_PRESET_REMAP[mode] == self._preset_mode:
                return mode

        if self._hvac_mode in self.hvac_modes:
            return self._hvac_mode

        return HVAC_MODE_HEAT

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        modes = []
        if (self.has_config(CONF_HVAC_MODE_DP)):
            modes = [HVAC_MODE_AUTO, HVAC_MODE_HEAT]
            if not self.has_config(CONF_ZIGBEE):
                modes.append(HVAC_MODE_OFF)
        return modes

    @property
    def hvac_action(self):
        """Return current action ie. heating, idle."""
        return self._hvac_action

    @property
    def preset_mode(self):
        """Return current preset mode"""
        if (self.has_config(CONF_PRESET_MODE_DP)):
            for preset in PRESET_REMAP:
                if PRESET_REMAP[preset] == self._preset_mode:
                    return preset

            if self._preset_mode in self.preset_modes:
                return self._preset_mode
            
            return PRESET_HOME
        else:
            return NotImplementedError()

    @property
    def preset_modes(self):
        """Return the list of available preset modes."""
        if (self.has_config(CONF_PRESET_MODE_DP)):
            return [PRESET_COMFORT, PRESET_ECO] + list(PRESET_REMAP.keys())
        else:
            return NotImplementedError()

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._config.get(CONF_TEMPERATURE_STEP, DEFAULT_TEMPERATURE_STEP)

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        if ATTR_TEMPERATURE in kwargs and self.has_config(CONF_TARGET_TEMPERATURE_DP):
            temperature = round(kwargs[ATTR_TEMPERATURE] / self._precision)
            await self.set_dp(temperature, self._config[CONF_TARGET_TEMPERATURE_DP])

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target operation mode."""
        if hvac_mode in HVAC_PRESET_REMAP:
            await self.async_set_preset_mode(HVAC_PRESET_REMAP[hvac_mode])
        elif self.has_config(CONF_HVAC_MODE_DP):
            await self.set_dp(hvac_mode, self._config[CONF_HVAC_MODE_DP])
        else:
            return NotImplementedError()

    async def async_set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        if self.has_config(CONF_PRESET_MODE_DP):
            await self.set_dp(PRESET_REMAP[preset_mode] if preset_mode in PRESET_REMAP else preset_mode, self._config[CONF_PRESET_MODE_DP])
        else:
            return NotImplementedError()

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        if self._min_temp:
            return self._min_temp
        return DEFAULT_MIN_TEMP

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        if self._max_temp:
            return self._max_temp
        return DEFAULT_MAX_TEMP

    def status_updated(self):
        """Device status was updated."""
        self._state = self.dps(self._dp_id)
        self._hvac_action = CURRENT_HVAC_HEAT if self._state else CURRENT_HVAC_IDLE

        if self.has_config(CONF_TARGET_TEMPERATURE_DP):
            self._target_temperature = (
                self.dps_conf(CONF_TARGET_TEMPERATURE_DP) * self._precision
            )

        if self.has_config(CONF_CURRENT_TEMPERATURE_DP):
            self._current_temperature = (
                self.dps_conf(CONF_CURRENT_TEMPERATURE_DP) * self._precision
            )

        if self.has_config(CONF_MIN_TEMP_DP):
            self._min_temp = self.dps_conf(CONF_MIN_TEMP_DP)

        if self.has_config(CONF_MAX_TEMP_DP):
            self._max_temp = self.dps_conf(CONF_MAX_TEMP_DP)

        if self.has_config(CONF_HVAC_MODE_DP):
            self._hvac_mode = self.dps_conf(CONF_HVAC_MODE_DP)
        else:
            self._hvac_mode = HVAC_MODE_HEAT
        
        if self.has_config(CONF_PRESET_MODE_DP):
            self._preset_mode = self.dps_conf(CONF_PRESET_MODE_DP)


async_setup_entry = partial(async_setup_entry, DOMAIN, LocaltuyaClimate, flow_schema)