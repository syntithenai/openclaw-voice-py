def _resolve_device_index(device: int | str | None, want_input: bool) -> int | None:
    """Resolve configured device value to a PortAudio device index.

    Returns None for "default" or if not resolvable.
    """
    if device is None:
        return None

    dev_str = str(device).strip()
    if not dev_str or dev_str.lower() == "default":
        return None

    try:
        import sounddevice as sd

        devices = sd.query_devices()
        channel_key = "max_input_channels" if want_input else "max_output_channels"

        if dev_str.isdigit():
            idx = int(dev_str)
            if 0 <= idx < len(devices) and int(devices[idx].get(channel_key, 0) or 0) > 0:
                return idx
            return None

        if dev_str.startswith(("hw:", "plughw:")):
            hw = dev_str.split(":", 1)[1]
            card = hw.split(",", 1)[0]
            match = next(
                (
                    i
                    for i, d in enumerate(devices)
                    if (
                        f"(hw:{hw})" in d.get("name", "")
                        or f"(hw:{card}," in d.get("name", "")
                    )
                    and int(d.get(channel_key, 0) or 0) > 0
                ),
                None,
            )
            return match

        needle = dev_str.lower()
        match = next(
            (
                i
                for i, d in enumerate(devices)
                if needle in str(d.get("name", "")).lower()
                and int(d.get(channel_key, 0) or 0) > 0
            ),
            None,
        )
        return match
    except Exception:
        return None


def _rank_device_priority(name: str, hostapi_name: str) -> int:
    txt = f"{name} {hostapi_name}".lower()
    if "pipewire" in txt:
        return 0
    if "pulseaudio" in txt or "pulse" in txt:
        return 1
    if "usb" in txt:
        return 2
    return 3


def _auto_select_audio_device(want_input: bool) -> int | None:
    """Select best available device using configure_audio_devices-style priorities.

    Priority: PipeWire -> PulseAudio -> USB -> first available.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        channel_key = "max_input_channels" if want_input else "max_output_channels"

        candidates: list[tuple[int, int]] = []
        for i, dev in enumerate(devices):
            if int(dev.get(channel_key, 0) or 0) <= 0:
                continue
            hostapi_idx = int(dev.get("hostapi", -1) or -1)
            hostapi_name = ""
            if 0 <= hostapi_idx < len(hostapis):
                hostapi_name = str(hostapis[hostapi_idx].get("name", ""))
            rank = _rank_device_priority(str(dev.get("name", "")), hostapi_name)
            candidates.append((rank, i))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][1]
    except Exception:
        return None


def _auto_select_physical_input_device(preferred_rate: int | None = None) -> int | None:
    """Prefer a physical input device (USB/ALSA hw) over virtual PipeWire/Pulse inputs.

    If preferred_rate is provided, prefer devices that can open at that sample rate.
    """
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        hostapis = sd.query_hostapis()

        physical_usb: list[int] = []
        physical_other: list[int] = []
        for i, dev in enumerate(devices):
            if int(dev.get("max_input_channels", 0) or 0) <= 0:
                continue

            hostapi_idx = int(dev.get("hostapi", -1) or -1)
            hostapi_name = ""
            if 0 <= hostapi_idx < len(hostapis):
                hostapi_name = str(hostapis[hostapi_idx].get("name", ""))

            name = str(dev.get("name", ""))
            txt = f"{name} {hostapi_name}".lower()
            if "pipewire" in txt or "pulse" in txt or "pulseaudio" in txt:
                continue

            if "usb" in txt:
                physical_usb.append(i)
            else:
                physical_other.append(i)

        def _supports_rate(idx: int) -> bool:
            if preferred_rate is None:
                return True
            try:
                sd.check_input_settings(device=idx, samplerate=int(preferred_rate), channels=1)
                return True
            except Exception:
                return False

        for idx in physical_usb:
            if _supports_rate(idx):
                return idx
        for idx in physical_other:
            if _supports_rate(idx):
                return idx

        if physical_usb:
            return physical_usb[0]
        if physical_other:
            return physical_other[0]
        return None
    except Exception:
        return None


def _pick_working_playback_rate(device_idx: int | None, desired_rate: int) -> int:
    """Pick a working playback sample rate for device.

    Tries desired first, then common rates (highest preferred), then device default.
    """
    try:
        import sounddevice as sd

        rates_desc = [192000, 176400, 96000, 88200, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000]
        ordered = [desired_rate] + [r for r in rates_desc if r != desired_rate]
        for rate in ordered:
            try:
                sd.check_output_settings(device=device_idx, samplerate=rate, channels=1)
                return int(rate)
            except Exception:
                continue

        info = sd.query_devices(device_idx, "output")
        fallback = int(round(float(info.get("default_samplerate", desired_rate) or desired_rate)))
        return fallback
    except Exception:
        return int(desired_rate)


def _describe_device(device_idx: int | None) -> str:
    if device_idx is None:
        return "default"
    try:
        import sounddevice as sd

        dev = sd.query_devices(device_idx)
        return f"#{device_idx} {dev.get('name', 'unknown')}"
    except Exception:
        return str(device_idx)
