from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature, StreamType
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ReolinkBaichuanConfigEntry
from .bridge import BaichuanTcpBridge
from .const import DOMAIN, STREAM_TYPES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ReolinkBaichuanConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Reolink Baichuan camera entities."""
    runtime = entry.runtime_data
    entities: list[ReolinkBaichuanCamera] = []

    for stream_key, label, sub_ch, xml_name in STREAM_TYPES:
        bridge = BaichuanTcpBridge(
            host_ip=entry.data[CONF_HOST],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            port=entry.data[CONF_PORT],
            sub_channel=sub_ch,
            stream_xml_name=xml_name,
        )
        await bridge.start()
        runtime.bridges.append(bridge)
        entities.append(ReolinkBaichuanCamera(entry, bridge, stream_key, label))

    async_add_entities(entities)


class ReolinkBaichuanCamera(Camera):
    """Home Assistant Camera entity for a Baichuan-only Reolink camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        entry: ReolinkBaichuanConfigEntry,
        bridge: BaichuanTcpBridge,
        stream_key: str,
        label: str,
    ) -> None:
        super().__init__()
        self._host = entry.runtime_data.host
        self._bridge = bridge
        self._stream_key = stream_key

        self._attr_name = label
        self._attr_unique_id = f"{entry.unique_id}_{stream_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=f"Reolink {self._host.model or 'Camera'}",
            manufacturer="Reolink",
            model=self._host.model,
            sw_version=self._host.sw_version,
            hw_version=self._host.hardware_version,
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Fetch a still JPEG snapshot via Baichuan cmd_id=29."""
        try:
            return await self._host.get_snapshot(0, self._stream_key)
        except Exception as err:
            _LOGGER.debug("Snapshot failed (%s): %s", self._stream_key, err)
            return None

    async def stream_source(self) -> str | None:
        """Return the local loopback TCP URI for Home Assistant's stream worker."""
        port = await self._bridge.start()
        return f"tcp://127.0.0.1:{port}"
