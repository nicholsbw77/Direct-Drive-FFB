"""
Individual FFB effect processors.

Each effect takes a TelemetryData snapshot and produces a torque value
in the range [-1.0, 1.0]. The FFB engine mixes these with per-effect
gain and priority settings.

Design inspired by SimCommander's telemetry-based approach:
- Each effect is an independent processor with its own state
- Effects can maintain history for filtering/derivative calculations
- All effects are normalized to [-1.0, 1.0] before mixing
"""

import math
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.telemetry.iracing_reader import TelemetryData


@dataclass
class EffectConfig:
    """Per-effect configuration parameters."""
    gain: float = 1.0           # 0.0 to 2.0 - effect strength multiplier
    enabled: bool = True
    smoothing: float = 0.0      # 0.0 to 1.0 - low-pass filter amount
    min_speed: float = 0.0      # m/s - effect is zero below this speed


class BaseEffect(ABC):
    """Base class for all FFB effects."""

    name: str = "base"

    def __init__(self, config: EffectConfig | None = None):
        self.config = config or EffectConfig()
        self._prev_output = 0.0

    @abstractmethod
    def compute(self, telemetry: TelemetryData) -> float:
        """Compute raw effect value from telemetry. Return [-1.0, 1.0]."""
        ...

    def process(self, telemetry: TelemetryData) -> float:
        """Full processing: compute, apply speed gate, smoothing, and gain."""
        if not self.config.enabled:
            return 0.0

        # Speed gate - fade effect in from 0 at min_speed to full at min_speed + 5 m/s
        if self.config.min_speed > 0 and telemetry.speed < self.config.min_speed + 5.0:
            if telemetry.speed < self.config.min_speed:
                speed_factor = 0.0
            else:
                speed_factor = (telemetry.speed - self.config.min_speed) / 5.0
        else:
            speed_factor = 1.0

        raw = self.compute(telemetry)

        # Apply smoothing (exponential moving average)
        if self.config.smoothing > 0:
            alpha = 1.0 - self.config.smoothing
            raw = alpha * raw + self.config.smoothing * self._prev_output

        # Apply gain and speed gate
        output = raw * self.config.gain * speed_factor

        # Clamp to [-1, 1]
        output = max(-1.0, min(1.0, output))
        self._prev_output = output
        return output


class SelfAligningTorque(BaseEffect):
    """
    Primary FFB effect - uses iRacing's physics-calculated steering torque.

    This is the most important effect. iRacing computes the self-aligning
    torque (SAT) from tire physics, suspension geometry, and caster/trail.
    We read it directly from telemetry for maximum fidelity.
    """

    name = "self_aligning_torque"

    def __init__(self, config: EffectConfig | None = None, max_torque_nm: float = 20.0):
        super().__init__(config)
        # Max expected torque from iRacing for normalization
        self.max_torque_nm = max_torque_nm

    def compute(self, telemetry: TelemetryData) -> float:
        # SteeringWheelTorque is in Nm, normalize to [-1, 1]
        torque = telemetry.steering_wheel_torque
        return max(-1.0, min(1.0, torque / self.max_torque_nm))


class CurbRumble(BaseEffect):
    """
    Simulates road surface texture and curb hits using suspension data.

    Uses high-frequency suspension deflection changes to detect when
    the car is riding over curbs, rumble strips, or rough surfaces.
    """

    name = "curb_rumble"

    def __init__(self, config: EffectConfig | None = None):
        super().__init__(config or EffectConfig(gain=0.5))
        self._history_lf = deque(maxlen=10)
        self._history_rf = deque(maxlen=10)

    def compute(self, telemetry: TelemetryData) -> float:
        # Use front suspension velocity as rumble indicator
        # High-frequency vertical movement = rough surface
        lf_vel = abs(telemetry.lf_shock_vel)
        rf_vel = abs(telemetry.rf_shock_vel)

        self._history_lf.append(lf_vel)
        self._history_rf.append(rf_vel)

        if len(self._history_lf) < 3:
            return 0.0

        # Compute variance of recent suspension velocity - high variance = rumble
        lf_var = float(np.var(list(self._history_lf)))
        rf_var = float(np.var(list(self._history_rf)))

        # Weight front-left and front-right differently based on steering
        steer = telemetry.steering_wheel_pct  # -1 left, +1 right
        # When turning left, left wheel loads more -> more rumble feel from left
        lf_weight = 0.5 + 0.3 * (-steer)
        rf_weight = 0.5 + 0.3 * steer

        rumble = lf_var * lf_weight + rf_var * rf_weight

        # Normalize - typical curb strike produces variance ~0.01-0.1
        normalized = min(1.0, rumble / 0.05)

        # Add directional component: rumble pushes steering toward curb side
        direction = 1.0 if (lf_var > rf_var) else -1.0

        return normalized * direction


class EngineVibration(BaseEffect):
    """
    Engine RPM-based vibration effect.

    Creates a subtle vibration tied to engine RPM. Stronger at idle
    and near redline, reduced at mid-range RPM. Adds immersion.
    """

    name = "engine_vibration"

    def __init__(self, config: EffectConfig | None = None,
                 idle_rpm: float = 800.0, redline_rpm: float = 8000.0):
        super().__init__(config or EffectConfig(gain=0.15))
        self.idle_rpm = idle_rpm
        self.redline_rpm = redline_rpm
        self._phase = 0.0

    def compute(self, telemetry: TelemetryData) -> float:
        rpm = telemetry.rpm
        if rpm < 100:
            return 0.0

        # Vibration intensity curve: higher at idle and redline
        rpm_pct = (rpm - self.idle_rpm) / (self.redline_rpm - self.idle_rpm)
        rpm_pct = max(0.0, min(1.0, rpm_pct))

        # U-shaped curve: strong at extremes, weak in middle
        intensity = 0.3 + 0.7 * (2.0 * (rpm_pct - 0.5) ** 2)

        # Near-redline boost
        if rpm_pct > 0.9:
            intensity = min(1.0, intensity * 1.5)

        # Oscillation frequency proportional to RPM
        # Engine fires at RPM/60 * (cylinders/2) Hz, approximate with sine
        freq = rpm / 60.0  # base frequency
        self._phase += freq * 0.003  # advance phase per tick (~360Hz tick)
        oscillation = math.sin(self._phase * 2 * math.pi)

        return intensity * oscillation * 0.5


class TireSlip(BaseEffect):
    """
    Understeer/oversteer feedback through front tire slip.

    When front tires begin to slip (understeer), the steering goes light.
    This effect reduces force to simulate loss of grip, then adds
    oscillation as tires slide to simulate the feel of sliding rubber.
    """

    name = "tire_slip"

    def __init__(self, config: EffectConfig | None = None,
                 slip_threshold: float = 0.15):
        super().__init__(config or EffectConfig(gain=0.6))
        self.slip_threshold = slip_threshold
        self._phase = 0.0

    def compute(self, telemetry: TelemetryData) -> float:
        # Average front tire slip ratio
        front_slip = (telemetry.lf_tire_slip + telemetry.rf_tire_slip) / 2.0

        if front_slip < self.slip_threshold:
            return 0.0

        # How much beyond threshold (normalized)
        excess = (front_slip - self.slip_threshold) / (1.0 - self.slip_threshold)
        excess = min(1.0, excess)

        # Slip oscillation - tires chattering
        self._phase += 0.15  # ~54Hz oscillation at 360Hz tick rate
        chatter = math.sin(self._phase * 2 * math.pi) * excess * 0.4

        # Lightening effect - reduce SAT feel (returned as small value opposing SAT)
        # Steering direction determines sign
        steer_dir = 1.0 if telemetry.steering_wheel_pct > 0 else -1.0

        # Return a force that opposes the current steering (lightens feel)
        return chatter - (excess * 0.3 * steer_dir)


class CollisionImpact(BaseEffect):
    """
    Sudden impact effect for car contact and wall hits.

    Detects spikes in lateral/longitudinal acceleration that indicate
    collisions and produces a sharp, decaying torque impulse.
    """

    name = "collision_impact"

    def __init__(self, config: EffectConfig | None = None,
                 accel_threshold: float = 25.0):
        super().__init__(config or EffectConfig(gain=0.8))
        self.accel_threshold = accel_threshold  # m/s^2 threshold for impact
        self._impact_magnitude = 0.0
        self._impact_direction = 1.0
        self._decay_rate = 0.85  # per tick

    def compute(self, telemetry: TelemetryData) -> float:
        lat = abs(telemetry.lat_accel)
        lon = abs(telemetry.long_accel)
        total_accel = math.sqrt(lat ** 2 + lon ** 2)

        # Detect new impact
        if total_accel > self.accel_threshold:
            impact_strength = min(1.0, total_accel / 60.0)
            if impact_strength > self._impact_magnitude:
                self._impact_magnitude = impact_strength
                # Impact pushes steering in direction of lateral force
                self._impact_direction = 1.0 if telemetry.lat_accel > 0 else -1.0

        # Decay existing impact
        if self._impact_magnitude > 0.01:
            result = self._impact_magnitude * self._impact_direction
            self._impact_magnitude *= self._decay_rate
            return result

        self._impact_magnitude = 0.0
        return 0.0


class SuspensionEffect(BaseEffect):
    """
    Suspension load transfer and body roll feedback.

    Translates lateral weight transfer into a steering torque that
    communicates chassis dynamics. As the car rolls in a corner,
    the driver feels the load building through the steering.
    """

    name = "suspension"

    def __init__(self, config: EffectConfig | None = None):
        super().__init__(config or EffectConfig(gain=0.3))

    def compute(self, telemetry: TelemetryData) -> float:
        # Front suspension differential = lateral load transfer
        lf_defl = telemetry.lf_shock_defl
        rf_defl = telemetry.rf_shock_defl

        # Positive = more compression on left = turning right = weight on left
        diff = lf_defl - rf_defl

        # Normalize - typical range is about +/- 0.03m
        normalized = max(-1.0, min(1.0, diff / 0.03))

        # Also incorporate longitudinal transfer for braking feel
        front_avg = (lf_defl + rf_defl) / 2.0
        rear_avg = (telemetry.lr_shock_defl + telemetry.rr_shock_defl) / 2.0
        lon_transfer = front_avg - rear_avg  # positive = nose dive (braking)
        lon_normalized = max(-0.3, min(0.3, lon_transfer / 0.02))

        # Lateral is primary, longitudinal adds weight to the feel
        return normalized * 0.8 + lon_normalized * 0.2
