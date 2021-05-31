"""Code shared between all platforms."""
import asyncio
import logging

from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_ENTITIES,
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_ID,
    CONF_PLATFORM,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.restore_state import RestoreEntity

from . import pytuya
from .const import (
    CONF_LOCAL_KEY,
    CONF_PRODUCT_KEY,
    CONF_PROTOCOL_VERSION,
    CONF_ZIGBEE,
    CONF_ZIGBEE_CID,
    CONF_ZIGBEE_REFRESH,
    CONF_ZIGBEE_REFRESH_DP,
    CONF_ZIGBEE_REFRESH_VALUE,
    CONF_ZIGBEE_REFRESH_INITIAL_VALUE,
    DOMAIN,
    TUYA_DEVICE,
)

_LOGGER = logging.getLogger(__name__)


def prepare_setup_entities(hass, config_entry, platform):
    """Prepare ro setup entities for a platform."""
    entities_to_setup = [
        entity
        for entity in config_entry.data[CONF_ENTITIES]
        if entity[CONF_PLATFORM] == platform
    ]
    if not entities_to_setup:
        return None, None

    tuyainterface = hass.data[DOMAIN][config_entry.entry_id][TUYA_DEVICE]

    return tuyainterface, entities_to_setup


async def async_setup_entry(
    domain, entity_class, flow_schema, hass, config_entry, async_add_entities
):
    """Set up a Tuya platform based on a config entry.

    This is a generic method and each platform should lock domain and
    entity_class with functools.partial.
    """
    tuyainterface, entities_to_setup = prepare_setup_entities(
        hass, config_entry, domain
    )
    if not entities_to_setup:
        return

    dps_config_fields = list(get_dps_for_platform(flow_schema))

    entities = []
    for device_config in entities_to_setup:
        # Add DPS used by this platform to the request list
        for dp_conf in dps_config_fields:
            if dp_conf in device_config:
                tuyainterface.dps_to_request[device_config[dp_conf]] = None

        id = f"{device_config[CONF_ZIGBEE][CONF_ZIGBEE_CID]}_{device_config[CONF_ID]}" if CONF_ZIGBEE in device_config else device_config[CONF_ID]
        entities.append(
            entity_class(
                tuyainterface,
                config_entry,
                id,
            )
        )

    async_add_entities(entities)


def get_dps_for_platform(flow_schema):
    """Return config keys for all platform keys that depends on a datapoint."""
    for key, value in flow_schema(None).items():
        if hasattr(value, "container") and value.container is None:
            yield key.schema


def get_entity_config(config_entry, dp_id, cid=None):
    """Return entity config for a given DPS id."""
    for entity in config_entry.data[CONF_ENTITIES]:
        if cid:
            if entity[CONF_ZIGBEE][CONF_ZIGBEE_CID] == cid:
                return entity
        else:
            if entity[CONF_ID] == dp_id:
                return entity
    raise Exception("missing entity config for " + f"cid {cid}" if cid else f"id {dp_id}")


@callback
def async_config_entry_by_device_id(hass, device_id):
    """Look up config entry by device id."""
    current_entries = hass.config_entries.async_entries(DOMAIN)
    for entry in current_entries:
        if entry.data[CONF_DEVICE_ID] == device_id:
            return entry
    return None


class TuyaDevice(pytuya.TuyaListener, pytuya.ContextualLogger):
    """Cache wrapper for pytuya.TuyaInterface."""

    def __init__(self, hass, config_entry):
        """Initialize the cache."""
        super().__init__()
        self._hass = hass
        self._config_entry = config_entry
        self._interface = None
        self._status = {}
        self.dps_to_request = {}
        self._is_closing = False
        self._connect_task = None
        self.set_logger(_LOGGER, config_entry[CONF_DEVICE_ID])

        # This has to be done in case the device type is type_0d
        for entity in config_entry[CONF_ENTITIES]:
            self.dps_to_request[entity[CONF_ID]] = None

    @property
    def connected(self):
        """Return if connected to device."""
        return self._interface is not None

    def async_connect(self):
        """Connect to device if not already connected."""
        if not self._is_closing and self._connect_task is None and not self._interface:
            self._connect_task = asyncio.create_task(self._make_connection())

    async def _make_connection(self):
        self.debug("Connecting to %s", self._config_entry[CONF_HOST])

        try:
            self._interface = await pytuya.connect(
                self._config_entry[CONF_HOST],
                self._config_entry[CONF_DEVICE_ID],
                self._config_entry[CONF_LOCAL_KEY],
                float(self._config_entry[CONF_PROTOCOL_VERSION]),
                self,
            )
            self._interface.add_dps_to_request(self.dps_to_request)

            request_status = True
            for entity in self._config_entry[CONF_ENTITIES]:
                if CONF_ZIGBEE in entity:
                    request_status = False
                    if CONF_ZIGBEE_REFRESH in entity[CONF_ZIGBEE]:
                        await self.refresh_subdevice(
                            entity[CONF_ZIGBEE][CONF_ZIGBEE_CID],
                            entity[CONF_ZIGBEE][CONF_ZIGBEE_REFRESH][CONF_ZIGBEE_REFRESH_DP], 
                            entity[CONF_ZIGBEE][CONF_ZIGBEE_REFRESH][CONF_ZIGBEE_REFRESH_VALUE], 
                            entity[CONF_ZIGBEE][CONF_ZIGBEE_REFRESH].get(CONF_ZIGBEE_REFRESH_INITIAL_VALUE)
                        )
                    else:
                        await self.status(entity[CONF_ZIGBEE][CONF_ZIGBEE_CID])

            if request_status:
                await self.status()

        except Exception:  # pylint: disable=broad-except
            self.exception(f"Connect to {self._config_entry[CONF_HOST]} failed")
            if self._interface is not None:
                await self._interface.close()
                self._interface = None
        self._connect_task = None

    async def close(self):
        """Close connection and stop re-connect loop."""
        self._is_closing = True
        if self._connect_task is not None:
            self._connect_task.cancel()
            await self._connect_task
        if self._interface is not None:
            await self._interface.close()

    async def set_dp(self, state, dp_index, cid=None):
        """Change value of a DP of the Tuya device."""
        if self._interface is not None:
            try:
                await self._interface.set_dp(state, dp_index, cid)
            except Exception:  # pylint: disable=broad-except
                self.exception("Failed to set DP %d to %d", dp_index, state)
        else:
            self.error("Not connected to device %s", self._config_entry[CONF_FRIENDLY_NAME])

    async def set_dps(self, states, cid=None):
        """Change value of a DPs of the Tuya device."""
        if self._interface is not None:
            try:
                await self._interface.set_dps(states, cid)
            except Exception:  # pylint: disable=broad-except
                self.exception("Failed to set DPs %r", states)
        else:
            self.error("Not connected to device %s", self._config_entry[CONF_FRIENDLY_NAME])

    async def refresh_subdevice(self, cid, dp, value, initial_value=None):
        """Refresh zigbee subdevice."""

        async def refresh_callback():
            del self._refresh_callbacks[cid]

            self.debug(f"Sub device {cid} refreshed!")

            if initial_value:
                self.debug(f"Setting initial value for force refresh dp of sub device {cid}")
                await self._interface.set_dps({dp: initial_value}, cid)

            await self.status(cid)

        self.debug(f"Forcing refresh for sub device {cid}")
        if not hasattr(self, '_refresh_callbacks'):
            self._refresh_callbacks = {}
        self._refresh_callbacks[cid] = refresh_callback 
        await self._interface.set_dps({dp: value}, cid)

    async def status(self, cid=None):
        """Get Tuya device status."""
        if self._interface is not None:
            self.debug(f"Retrieving state {f'for subdevice {cid}' if cid else ''}")

            try:
                status = await self._interface.status(cid)
                if status is not None:
                    self.status_updated(status, cid)
                else:
                    raise Exception("Failed to retrieve status")
            except Exception:  # pylint: disable=broad-except
                self.exception("Failed to get status " + f"for sub device {cid}" if cid else "")
        else:
            self.error(
                "Not connected to device %s", self._config_entry[CONF_FRIENDLY_NAME]
            )

    @callback
    def status_updated(self, status, cid=None):
        """Device updated status."""
        if cid:
            if cid in self._status:
                self._status[cid].update(status[cid])
            else:
                self._status[cid] = status[cid]

        else:
            self._status.update(status)

        signal = f"localtuya_{self._config_entry[CONF_DEVICE_ID]}"
        async_dispatcher_send(self._hass, signal, self._status)

    @callback
    def disconnected(self):
        """Device disconnected."""
        signal = f"localtuya_{self._config_entry[CONF_DEVICE_ID]}"
        async_dispatcher_send(self._hass, signal, None)

        self._interface = None
        self.debug("Disconnected - waiting for discovery broadcast")


class LocalTuyaEntity(RestoreEntity, pytuya.ContextualLogger):
    """Representation of a Tuya entity."""

    def __init__(self, device, config_entry, dp_id, logger, **kwargs):
        """Initialize the Tuya entity."""
        super().__init__()
        if "_" in str(dp_id):
            self._cid, self._dp_id = str(dp_id).split("_")
        else:
            self._dp_id = dp_id
            self._cid = None

        self._device = device
        self._config_entry = config_entry
        self._config = get_entity_config(config_entry, self._dp_id, self._cid)
        self._status = {}
        self.set_logger(logger, self._config_entry.data[CONF_DEVICE_ID])

    async def async_added_to_hass(self):
        """Subscribe localtuya events."""
        await super().async_added_to_hass()

        self.debug("Adding %s with configuration: %s", self.entity_id, self._config)

        state = await self.async_get_last_state()
        if state:
            self.status_restored(state)

        async def _update_handler(status):
            """Update entity state when status was updated."""
            if self._cid:
                status = status.get(self._cid)

                if status and self._cid in self._device._refresh_callbacks:
                    await self._device._refresh_callbacks[self._cid]()
                    return

            if status:
                self._status = status
                self.status_updated()
            else:
                self._status = {}

            self.schedule_update_ha_state()

        signal = f"localtuya_{self._config_entry.data[CONF_DEVICE_ID]}"
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, _update_handler)
        )

    @property
    def device_info(self):
        """Return device information for the device registry."""
        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, f"local_{self._config_entry.data[CONF_DEVICE_ID]}")
            },
            "name": self._config_entry.data[CONF_FRIENDLY_NAME],
            "manufacturer": "Unknown",
            "model": self._config_entry.data.get(CONF_PRODUCT_KEY, "Tuya generic"),
            "sw_version": self._config_entry.data[CONF_PROTOCOL_VERSION],
        }

    @property
    def name(self):
        """Get name of Tuya entity."""
        return self._config[CONF_FRIENDLY_NAME]

    @property
    def should_poll(self):
        """Return if platform should poll for updates."""
        return False

    @property
    def unique_id(self):
        """Return unique device identifier."""
        return f"local_{self._config_entry.data[CONF_DEVICE_ID]}_{self._cid or self._dp_id}"

    def has_config(self, attr):
        """Return if a config parameter has a valid value."""
        value = self._config.get(attr, "-1")
        return value is not None and value != "-1"

    @property
    def available(self):
        """Return if device is available or not."""
        return str(self._dp_id) in self._status

    def dps(self, dp_index):
        """Return cached value for DPS index."""
        value = self._status.get(str(dp_index))
        if value is None:
            self.warning(
                "Entity %s is requesting unknown DPS index %s",
                self.entity_id,
                dp_index,
            )

        return value

    async def set_dps(self, states):
        """Change value of a DPs of the Tuya device."""
        await self._device.set_dps(states, self._cid)

    async def set_dp(self, state, dp_index):
        """Change value of a DP of the Tuya device."""
        await self._device.set_dp(state, dp_index, self._cid)

    def dps_conf(self, conf_item):
        """Return value of datapoint for user specified config item.

        This method looks up which DP a certain config item uses based on
        user configuration and returns its value.
        """
        dp_index = self._config.get(conf_item)
        if dp_index is None:
            self.warning(
                "Entity %s is requesting unset index for option %s",
                self.entity_id,
                conf_item,
            )
        return self.dps(dp_index)

    def status_updated(self):
        """Device status was updated.

        Override in subclasses and update entity specific state.
        """

    def status_restored(self, stored_state):
        """Device status was restored.

        Override in subclasses and update entity specific state.
        """
