"""Audio device enumeration and hot-plug detection (Phase 5.1)."""

from __future__ import annotations

from jarvis.core.event_bus import EventBus
from jarvis.core.events import (
    AudioDeviceConnected,
    AudioDeviceDisconnected,
    AudioErrorOccurred,
)
from jarvis.voice.audio_io import AudioIO
from jarvis.voice.models import AudioDeviceInfo, AudioError


class AudioDeviceManager:
    """Tracks available audio devices, preferences, and hot-plug events."""

    def __init__(self, audio_io: AudioIO, bus: EventBus) -> None:
        self._io = audio_io
        self._bus = bus
        self._known: dict[str, AudioDeviceInfo] = {}
        self.preferred_input_id: str | None = None
        self.preferred_output_id: str | None = None

    @property
    def devices(self) -> tuple[AudioDeviceInfo, ...]:
        return tuple(self._known.values())

    def select_input(self, device_id: str | None) -> None:
        self.preferred_input_id = device_id

    def select_output(self, device_id: str | None) -> None:
        self.preferred_output_id = device_id

    async def refresh_devices(self) -> tuple[AudioDeviceInfo, ...]:
        """Re-query devices and publish hot-plug connect/disconnect events."""
        try:
            current_list = self._io.list_devices()
        except AudioError as exc:
            await self._bus.publish(AudioErrorOccurred(detail=str(exc)))
            return tuple(self._known.values())

        current = {d.device_id: d for d in current_list}
        for did, dev in current.items():
            if did not in self._known:
                direction = "input" if dev.is_input else "output"
                await self._bus.publish(
                    AudioDeviceConnected(
                        device_id=did, device_name=dev.name, direction=direction
                    )
                )
        for did, dev in list(self._known.items()):
            if did not in current:
                direction = "input" if dev.is_input else "output"
                await self._bus.publish(
                    AudioDeviceDisconnected(
                        device_id=did, device_name=dev.name, direction=direction
                    )
                )
        self._known = current
        return tuple(self._known.values())
