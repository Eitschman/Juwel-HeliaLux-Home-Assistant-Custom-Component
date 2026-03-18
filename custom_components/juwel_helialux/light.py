import logging
from homeassistant.components.light import (
    LightEntity,
    ColorMode,
)
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify
from .const import DOMAIN, CONF_UPDATE_INTERVAL, CONF_LED_CHANNELS, LED_CHANNELS_2

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the Juwel Helialux light platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, 1)
    coordinator.update_interval = timedelta(seconds=15)

    _LOGGER.debug("Coordinator contents: %s", dir(coordinator))

    if not hasattr(coordinator, "helialux"):
        _LOGGER.error("Coordinator is missing the 'helialux' attribute!")
        return

    await coordinator.async_config_entry_first_refresh()

    _LOGGER.debug("Coordinator initial data: %s", coordinator.data)

    tank_name = entry.title
    led_channels = entry.data.get(CONF_LED_CHANNELS, None)

    # Determine mode: if led_channels is not set (legacy config), default to 4-channel RGBW
    if led_channels == LED_CHANNELS_2:
        _LOGGER.debug("Setting up 2-channel (White + Blue) light entities")
        async_add_entities([
            JuwelHelialuxWhiteLight(coordinator, tank_name),
            JuwelHelialuxBlueLight(coordinator, tank_name),
        ], True)
    else:
        _LOGGER.debug("Setting up 4-channel (RGBW) light entity")
        async_add_entities([JuwelHelialuxLight(coordinator, tank_name)], True)


# =============================================================================
# 4-Channel RGBW Light (original behavior)
# =============================================================================

class JuwelHelialuxLight(CoordinatorEntity, LightEntity):
    """Representation of a Juwel Helialux Light in RGBW mode."""

    def __init__(self, coordinator, tank_name):
        """Initialize the light entity."""
        super().__init__(coordinator)
        self._controller = coordinator.helialux
        tank_slug = slugify(tank_name)
        self._attr_unique_id = f"{tank_slug}_light"
        self.entity_id = f"light.{self._attr_unique_id}"
        self._attr_has_entity_name = True 
        self._attr_translation_key = "light_name"
        self._attr_supported_color_modes = {ColorMode.RGBW}
        self._attr_color_mode = ColorMode.RGBW
        self._attr_is_on = False
        self._attr_brightness = None
        self._attr_rgbw_color = (0, 0, 0, 0)
        self._attr_device_info = coordinator.device_info


    @property
    def is_on(self):
        """Return true if light is on (any RGBW value is greater than 0)."""
        if not self.coordinator.data:
            _LOGGER.warning("Coordinator data is None, returning False for is_on")
            return False
        raw_red = self.coordinator.data.get("red", 0)
        raw_green = self.coordinator.data.get("green", 0)
        raw_blue = self.coordinator.data.get("blue", 0)
        raw_white = self.coordinator.data.get("white", 0)

        return raw_red > 0 or raw_green > 0 or raw_blue > 0 or raw_white > 0

    @property
    def rgbw_color(self):
        """Return RGBW color values converted to Home Assistant's scale (0-255)."""
        if not self.coordinator.data:
            _LOGGER.warning("Coordinator data is None, returning default RGBW (0,0,0,0)")
            return (0, 0, 0, 0)
        raw_red = self.coordinator.data.get("red", 0)
        raw_green = self.coordinator.data.get("green", 0)
        raw_blue = self.coordinator.data.get("blue", 0)
        raw_white = self.coordinator.data.get("white", 0)

        # If the light is off (all values are 0), return black
        if raw_red == 0 and raw_green == 0 and raw_blue == 0 and raw_white == 0:
            return (0, 0, 0, 0)

        # Convert from 0-100 scale to 0-255
        converted_red = int(raw_red * 2.55)
        converted_green = int(raw_green * 2.55)
        converted_blue = int(raw_blue * 2.55)
        converted_white = int(raw_white * 2.55)

        return (converted_red, converted_green, converted_blue, converted_white)

    @property
    def brightness(self):
        """Return the brightness of the light, based on the highest RGBW value."""
        if not self.coordinator.data:
            _LOGGER.warning("Coordinator data is None, returning default brightness 0")
            return 0

        raw_red = self.coordinator.data.get("red", 0)
        raw_green = self.coordinator.data.get("green", 0)
        raw_blue = self.coordinator.data.get("blue", 0)
        raw_white = self.coordinator.data.get("white", 0)

        # If the light is off (all values are 0), return brightness 0
        if raw_red == 0 and raw_green == 0 and raw_blue == 0 and raw_white == 0:
            return 0

        # Convert to Home Assistant's scale (0-255) and calculate brightness
        max_rgbw_value = max(
            raw_red * 2.55,
            raw_green * 2.55,
            raw_blue * 2.55,
            raw_white * 2.55,
        )

        # Return the brightness based on the highest value
        return int(max_rgbw_value)

    async def async_turn_on(self, **kwargs):
        """Turn the light on with optional parameters."""
        _LOGGER.debug("Turn on called with: %s", kwargs)
        
        # Get target values with defaults
        brightness = kwargs.get("brightness", 255)
        rgbw_color = kwargs.get("rgbw_color", (255, 255, 255, 255))
        
        # Convert to device scale (0-100)
        white = min(100, max(0, rgbw_color[3] / 2.55))
        blue = min(100, max(0, rgbw_color[2] / 2.55))
        green = min(100, max(0, rgbw_color[1] / 2.55))
        red = min(100, max(0, rgbw_color[0] / 2.55))
        
        # Apply brightness scaling if needed
        if brightness < 255:
            scale = brightness / 255.0
            white = min(100, white * scale)
            blue = min(100, blue * scale)
            green = min(100, green * scale)
            red = min(100, red * scale)

        _LOGGER.debug("Setting light to W:%d B:%d G:%d R:%d", white, blue, green, red)
        
        # Enable manual override for 5 seconds
        await self.coordinator.set_manual_override(True, 5)
        
        try:
            # Get duration from number entity (same approach as in switch.py)
            duration_entity = f"number.{self.coordinator.tank_slug}_manual_color_simulation_duration"
            duration_state = self.coordinator.hass.states.get(duration_entity)
            duration_minutes = int(float(duration_state.state) * 60) if duration_state else 720  # Default to 12 hours if not found
            
            _LOGGER.debug("Using manual color simulation duration: %s minutes", duration_minutes)
            
            # Set the light state with the configured duration
            await self._controller.start_manual_color_simulation(duration_minutes)            
            await self._controller.set_manual_color(white, blue, green, red)
            
            # Update local state immediately
            self._attr_is_on = True
            self._attr_brightness = brightness
            self._attr_rgbw_color = rgbw_color
            self.async_write_ha_state()
            
        except Exception as e:
            _LOGGER.error("Error setting light state: %s", e)
            await self.coordinator.set_manual_override(False)
            raise

    async def async_turn_off(self, **kwargs):
        """Turn the light off."""
        _LOGGER.debug("Turning off Juwel Helialux light")
        try:
            # Get duration from number entity (same approach as in switch.py)
            duration_entity = f"number.{self.coordinator.tank_slug}_manual_color_simulation_duration"
            duration_state = self.coordinator.hass.states.get(duration_entity)
            duration_minutes = int(float(duration_state.state) * 60) if duration_state else 720  # Default to 12 hours if not found
            
            _LOGGER.debug("Using manual color simulation duration: %s minutes", duration_minutes)
            
            await self._controller.start_manual_color_simulation(duration_minutes)
            await self._controller.set_manual_color(0, 0, 0, 0)
            self._attr_is_on = False
            self._attr_brightness = 0
            self._attr_rgbw_color = (0, 0, 0, 0)
            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error turning off light: %s", e)
            raise


# =============================================================================
# 2-Channel Mode: Separate White and Blue brightness-only lights
# =============================================================================

class _JuwelHelialuxChannelLight(CoordinatorEntity, LightEntity):
    """Base class for a single-channel brightness-only Helialux light."""

    def __init__(self, coordinator, tank_name, channel_key, translation_key, unique_suffix):
        """Initialize a single-channel light entity."""
        super().__init__(coordinator)
        self._controller = coordinator.helialux
        self._channel_key = channel_key  # "white" or "blue"
        tank_slug = slugify(tank_name)
        self._tank_slug = tank_slug
        self._attr_unique_id = f"{tank_slug}_{unique_suffix}"
        self.entity_id = f"light.{self._attr_unique_id}"
        self._attr_has_entity_name = True
        self._attr_translation_key = translation_key
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_is_on = False
        self._attr_brightness = 0
        self._attr_device_info = coordinator.device_info

    def _get_other_channel_value(self):
        """Return the current 0-100 value of the OTHER channel (to preserve it when setting this one)."""
        raise NotImplementedError

    @property
    def is_on(self):
        """Return true if this channel is on."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get(self._channel_key, 0) > 0

    @property
    def brightness(self):
        """Return brightness for this channel (0-255)."""
        if not self.coordinator.data:
            return 0
        raw = self.coordinator.data.get(self._channel_key, 0)
        return int(raw * 2.55)

    async def _send_color(self, white_pct, blue_pct):
        """Send color command to the controller. Green and Red are always 0."""
        await self.coordinator.set_manual_override(True, 5)
        try:
            duration_entity = f"number.{self._tank_slug}_manual_color_simulation_duration"
            duration_state = self.coordinator.hass.states.get(duration_entity)
            duration_minutes = int(float(duration_state.state) * 60) if duration_state else 720

            await self._controller.start_manual_color_simulation(duration_minutes)
            # API signature: set_manual_color(white, blue, green, red)
            await self._controller.set_manual_color(white_pct, blue_pct, 0, 0)

            self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error setting %s light: %s", self._channel_key, e)
            await self.coordinator.set_manual_override(False)
            raise

    async def async_turn_off(self, **kwargs):
        """Turn this channel off (set to 0) while preserving the other channel."""
        _LOGGER.debug("Turning off %s channel", self._channel_key)
        other_value = self._get_other_channel_value()
        if self._channel_key == "white":
            await self._send_color(0, other_value)
        else:
            await self._send_color(other_value, 0)
        self._attr_is_on = False
        self._attr_brightness = 0


class JuwelHelialuxWhiteLight(_JuwelHelialuxChannelLight):
    """White channel light entity."""

    def __init__(self, coordinator, tank_name):
        super().__init__(coordinator, tank_name, "white", "light_white", "light_white")

    def _get_other_channel_value(self):
        """Return current blue value (0-100)."""
        if not self.coordinator.data:
            return 0
        return self.coordinator.data.get("blue", 0)

    async def async_turn_on(self, **kwargs):
        """Turn white channel on."""
        brightness = kwargs.get("brightness", 255)
        white_pct = min(100, max(0, brightness / 2.55))
        blue_pct = self._get_other_channel_value()
        _LOGGER.debug("Setting white to %d%%, preserving blue at %d%%", white_pct, blue_pct)
        await self._send_color(white_pct, blue_pct)
        self._attr_is_on = True
        self._attr_brightness = brightness


class JuwelHelialuxBlueLight(_JuwelHelialuxChannelLight):
    """Blue channel light entity."""

    def __init__(self, coordinator, tank_name):
        super().__init__(coordinator, tank_name, "blue", "light_blue", "light_blue")

    def _get_other_channel_value(self):
        """Return current white value (0-100)."""
        if not self.coordinator.data:
            return 0
        return self.coordinator.data.get("white", 0)

    async def async_turn_on(self, **kwargs):
        """Turn blue channel on."""
        brightness = kwargs.get("brightness", 255)
        blue_pct = min(100, max(0, brightness / 2.55))
        white_pct = self._get_other_channel_value()
        _LOGGER.debug("Setting blue to %d%%, preserving white at %d%%", blue_pct, white_pct)
        await self._send_color(white_pct, blue_pct)
        self._attr_is_on = True
        self._attr_brightness = brightness
