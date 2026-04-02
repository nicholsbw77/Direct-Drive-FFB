"""
Configuration and profile management.

Loads/saves FFB profiles as JSON files. Each profile contains
engine settings, hardware settings, and per-effect configurations.
"""

import json
import os
from pathlib import Path
from dataclasses import asdict

from src.ffb.engine import EngineConfig
from src.ffb.effects import EffectConfig
from src.resource_path import resource_path


CONFIG_DIR = resource_path("config")
DEFAULT_PROFILE = CONFIG_DIR / "default_profile.json"


def load_profile(path: str | Path | None = None) -> dict:
    """Load a profile from JSON file. Returns the full profile dict."""
    if path is None:
        path = DEFAULT_PROFILE
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")

    with open(path, "r") as f:
        return json.load(f)


def save_profile(profile: dict, path: str | Path):
    """Save a profile dict to JSON file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(profile, f, indent=4)


def apply_engine_config(profile: dict) -> EngineConfig:
    """Extract EngineConfig from a profile dict."""
    eng = profile.get("engine", {})
    return EngineConfig(
        master_gain=eng.get("master_gain", 1.0),
        max_torque_pct=eng.get("max_torque_pct", 50.0),
        output_rate_hz=eng.get("output_rate_hz", 360.0),
        slew_rate_limit=eng.get("slew_rate_limit", 0.5),
        deadzone=eng.get("deadzone", 0.01),
        center_spring_gain=eng.get("center_spring_gain", 0.0),
        center_spring_damping=eng.get("center_spring_damping", 0.0),
        invert_output=eng.get("invert_output", False),
    )


def apply_effect_configs(profile: dict) -> dict[str, dict]:
    """Extract per-effect configs from a profile dict."""
    return profile.get("effects", {})


def get_hardware_config(profile: dict) -> dict:
    """Extract hardware config from a profile dict."""
    return profile.get("hardware", {})


def list_profiles() -> list[Path]:
    """List all available profile files."""
    if not CONFIG_DIR.exists():
        return []
    return sorted(CONFIG_DIR.glob("*.json"))


def build_profile_from_state(
    profile_name: str,
    engine_config: EngineConfig,
    hardware_config: dict,
    effects: dict,
) -> dict:
    """Build a saveable profile dict from current runtime state."""
    effect_configs = {}
    for name, effect in effects.items():
        effect_configs[name] = {
            "enabled": effect.config.enabled,
            "gain": effect.config.gain,
            "smoothing": effect.config.smoothing,
            "min_speed": effect.config.min_speed,
        }

    return {
        "profile_name": profile_name,
        "engine": {
            "master_gain": engine_config.master_gain,
            "max_torque_pct": engine_config.max_torque_pct,
            "output_rate_hz": engine_config.output_rate_hz,
            "slew_rate_limit": engine_config.slew_rate_limit,
            "deadzone": engine_config.deadzone,
            "center_spring_gain": engine_config.center_spring_gain,
            "center_spring_damping": engine_config.center_spring_damping,
            "invert_output": engine_config.invert_output,
        },
        "hardware": hardware_config,
        "effects": effect_configs,
    }
