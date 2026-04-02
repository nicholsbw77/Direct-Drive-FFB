"""
FFB Engine - Central pipeline that mixes effects and outputs torque commands.

Pipeline: Telemetry -> Effects -> Mixer -> Output Filter -> Hardware Driver

The engine runs its own processing loop, reading the latest telemetry
snapshot and producing a final torque command at the configured rate.
"""

import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from src.telemetry.iracing_reader import TelemetryData, IRacingTelemetry
from src.ffb.effects import (
    BaseEffect,
    SelfAligningTorque,
    CurbRumble,
    EngineVibration,
    TireSlip,
    CollisionImpact,
    SuspensionEffect,
    RearTractionLoss,
    DirtYawFeedback,
    ThrottleSteer,
    DirtSurfaceRumble,
    EffectConfig,
)

# Registry of all available effects by name
EFFECT_REGISTRY: dict[str, type[BaseEffect]] = {
    "self_aligning_torque": SelfAligningTorque,
    "curb_rumble": CurbRumble,
    "engine_vibration": EngineVibration,
    "tire_slip": TireSlip,
    "collision_impact": CollisionImpact,
    "suspension": SuspensionEffect,
    "rear_traction_loss": RearTractionLoss,
    "dirt_yaw_feedback": DirtYawFeedback,
    "throttle_steer": ThrottleSteer,
    "dirt_surface_rumble": DirtSurfaceRumble,
}


@dataclass
class EngineConfig:
    """Global FFB engine configuration."""
    master_gain: float = 1.0            # 0.0 to 2.0 - overall strength
    max_torque_pct: float = 80.0        # max torque as % of AASD rated torque
    output_rate_hz: float = 360.0       # how fast we send commands to hardware
    slew_rate_limit: float = 0.5        # max change per tick (0-1 range per tick)
    deadzone: float = 0.01              # below this, output zero
    center_spring_gain: float = 0.0     # optional software centering spring
    center_spring_damping: float = 0.0  # damping for centering spring
    invert_output: bool = False         # flip torque direction


class OutputFilter:
    """
    Final output stage: slew rate limiting, smoothing, and safety clamping.

    Prevents sudden torque spikes that could damage hardware or hurt the user.
    """

    def __init__(self, config: EngineConfig):
        self.config = config
        self._prev_output = 0.0
        self._history = np.zeros(5)
        self._hist_idx = 0

    def apply(self, raw_value: float) -> float:
        # Deadzone
        if abs(raw_value) < self.config.deadzone:
            raw_value = 0.0

        # Slew rate limiting - cap how fast output can change per tick
        delta = raw_value - self._prev_output
        max_delta = self.config.slew_rate_limit
        if abs(delta) > max_delta:
            raw_value = self._prev_output + max_delta * (1.0 if delta > 0 else -1.0)

        # Short moving average for final smoothing
        self._history[self._hist_idx % len(self._history)] = raw_value
        self._hist_idx += 1
        smoothed = float(np.mean(self._history))

        # Hard clamp to [-1, 1]
        output = max(-1.0, min(1.0, smoothed))

        # Apply max torque percentage
        output *= (self.config.max_torque_pct / 100.0)

        self._prev_output = output
        return output


class FFBEngine:
    """
    Main FFB processing engine.

    Manages all effects, mixes their outputs, applies filtering,
    and sends the final torque command to the hardware driver.
    """

    def __init__(self, telemetry: IRacingTelemetry, config: EngineConfig | None = None):
        self.config = config or EngineConfig()
        self.telemetry = telemetry
        self._output_filter = OutputFilter(self.config)

        # Initialize all registered effects with defaults
        self.effects: dict[str, BaseEffect] = {
            name: cls() for name, cls in EFFECT_REGISTRY.items()
        }

        self._running = False
        self._thread: threading.Thread | None = None
        self._output_value = 0.0
        self._lock = threading.Lock()
        self._hardware_callback = None
        self._monitor_callbacks: list = []

        # Diagnostics
        self._effect_outputs: dict[str, float] = {}
        self._raw_mix = 0.0
        self._filtered_output = 0.0
        self._loop_rate_actual = 0.0

    @property
    def output(self) -> float:
        with self._lock:
            return self._output_value

    @property
    def effect_outputs(self) -> dict[str, float]:
        """Per-effect output values for GUI monitoring."""
        with self._lock:
            return dict(self._effect_outputs)

    @property
    def loop_rate(self) -> float:
        return self._loop_rate_actual

    def set_hardware_callback(self, callback):
        """Set callback that receives final torque value each tick."""
        self._hardware_callback = callback

    def on_output(self, callback):
        """Register callback for monitoring (GUI updates)."""
        self._monitor_callbacks.append(callback)

    def set_effect_gain(self, effect_name: str, gain: float):
        if effect_name in self.effects:
            self.effects[effect_name].config.gain = max(0.0, min(2.0, gain))

    def set_effect_enabled(self, effect_name: str, enabled: bool):
        if effect_name in self.effects:
            self.effects[effect_name].config.enabled = enabled

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _process_loop(self):
        interval = 1.0 / self.config.output_rate_hz
        tick_count = 0
        rate_timer = time.perf_counter()

        while self._running:
            tick_start = time.perf_counter()

            telem = self.telemetry.latest

            if telem.is_on_track:
                mixed = self._mix_effects(telem)
                mixed += self._center_spring(telem)
                mixed *= self.config.master_gain

                if self.config.invert_output:
                    mixed = -mixed

                filtered = self._output_filter.apply(mixed)
            else:
                # Not on track - output centering spring only or zero
                filtered = self._output_filter.apply(
                    self._center_spring(telem) * self.config.master_gain
                )

            with self._lock:
                self._output_value = filtered
                self._raw_mix = mixed if telem.is_on_track else 0.0
                self._filtered_output = filtered

            # Send to hardware
            if self._hardware_callback:
                try:
                    self._hardware_callback(filtered)
                except Exception:
                    pass

            # Notify monitors (for GUI) - don't call every tick, throttle to ~30Hz
            tick_count += 1
            if tick_count % 12 == 0:
                for cb in self._monitor_callbacks:
                    try:
                        cb(filtered, dict(self._effect_outputs))
                    except Exception:
                        pass

            # Measure actual loop rate
            if tick_count % 360 == 0:
                now = time.perf_counter()
                elapsed = now - rate_timer
                self._loop_rate_actual = 360.0 / elapsed if elapsed > 0 else 0.0
                rate_timer = now

            # Sleep for remainder of interval
            elapsed = time.perf_counter() - tick_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _mix_effects(self, telemetry: TelemetryData) -> float:
        """Mix all enabled effects into a single torque value."""
        total = 0.0
        for name, effect in self.effects.items():
            value = effect.process(telemetry)
            self._effect_outputs[name] = value
            total += value

        # Soft clamp the mix (tanh-style limiting to avoid hard clipping)
        if abs(total) > 1.0:
            total = math.copysign(1.0, total) * (1.0 - 1.0 / (abs(total) + 1.0))

        return total

    def _center_spring(self, telemetry: TelemetryData) -> float:
        """Software-based centering spring (optional, for when car is stationary)."""
        if self.config.center_spring_gain <= 0:
            return 0.0

        # Spring force proportional to wheel angle
        angle_pct = telemetry.steering_wheel_pct  # -1 to 1
        spring = -angle_pct * self.config.center_spring_gain

        # Damping proportional to yaw rate (approximation of angular velocity)
        damping = -telemetry.yaw_rate * self.config.center_spring_damping

        return spring + damping

