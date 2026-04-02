"""
Application settings manager for the consumer wheel plugin.

Reads/writes %APPDATA%\\DirtFFB\\settings.json. All settings have sensible
defaults so the app works on first run even before the wizard completes.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
_SETTINGS_DIR  = _APPDATA / "DirtFFB"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

_DEFAULTS = {
    "wizard_complete":   False,
    "device_guid":       "",
    "device_name":       "",
    "driver_type":       "directinput",   # "directinput" | "logitech"
    "last_profile":      "default_profile",
    "start_with_windows": False,
    "minimize_to_tray":  True,
    "show_waveform":     True,
}

_STARTUP_REG_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_STARTUP_REG_NAME = "DirtFFBPlugin"


class AppSettings:
    """
    Persistent application settings backed by a JSON file.

    Attributes mirror the keys in _DEFAULTS and are kept in sync with the
    file whenever ``save()`` is called.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or _SETTINGS_FILE
        self._data: dict = dict(_DEFAULTS)
        self._load()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def wizard_complete(self) -> bool:
        return bool(self._data.get("wizard_complete", False))

    @wizard_complete.setter
    def wizard_complete(self, value: bool):
        self._data["wizard_complete"] = value

    @property
    def device_guid(self) -> str:
        return str(self._data.get("device_guid", ""))

    @device_guid.setter
    def device_guid(self, value: str):
        self._data["device_guid"] = value

    @property
    def device_name(self) -> str:
        return str(self._data.get("device_name", ""))

    @device_name.setter
    def device_name(self, value: str):
        self._data["device_name"] = value

    @property
    def driver_type(self) -> str:
        return str(self._data.get("driver_type", "directinput"))

    @driver_type.setter
    def driver_type(self, value: str):
        self._data["driver_type"] = value

    @property
    def last_profile(self) -> str:
        return str(self._data.get("last_profile", "default_profile"))

    @last_profile.setter
    def last_profile(self, value: str):
        self._data["last_profile"] = value

    @property
    def start_with_windows(self) -> bool:
        return bool(self._data.get("start_with_windows", False))

    @start_with_windows.setter
    def start_with_windows(self, value: bool):
        self._data["start_with_windows"] = value
        self._apply_startup_registry(value)

    @property
    def minimize_to_tray(self) -> bool:
        return bool(self._data.get("minimize_to_tray", True))

    @minimize_to_tray.setter
    def minimize_to_tray(self, value: bool):
        self._data["minimize_to_tray"] = value

    @property
    def show_waveform(self) -> bool:
        return bool(self._data.get("show_waveform", True))

    @show_waveform.setter
    def show_waveform(self, value: bool):
        self._data["show_waveform"] = value

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self):
        """Write current settings to disk."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4)
        except OSError as exc:
            logger.error("Could not save settings to %s: %s", self._path, exc)

    def reset_to_defaults(self):
        """Overwrite all settings with defaults and save."""
        self._data = dict(_DEFAULTS)
        self.save()

    # ------------------------------------------------------------------
    # Startup registry (Windows only)
    # ------------------------------------------------------------------

    def _apply_startup_registry(self, enable: bool):
        """
        Create or remove the Windows startup registry entry.

        On non-Windows systems this is a no-op.
        """
        try:
            import winreg  # type: ignore
        except ImportError:
            return

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _STARTUP_REG_KEY,
                0,
                winreg.KEY_SET_VALUE,
            )
            with key:
                if enable:
                    import sys
                    exe_path = sys.executable
                    winreg.SetValueEx(key, _STARTUP_REG_NAME, 0, winreg.REG_SZ, exe_path)
                    logger.info("Added startup registry entry: %s", exe_path)
                else:
                    try:
                        winreg.DeleteValue(key, _STARTUP_REG_NAME)
                        logger.info("Removed startup registry entry.")
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            logger.warning("Could not modify startup registry: %s", exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Merge — keep defaults for any missing keys
            self._data.update(loaded)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not load settings from %s: %s. Using defaults.", self._path, exc)
