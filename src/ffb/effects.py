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


# ---------------------------------------------------------------------------
# Dirt-specific effects
# ---------------------------------------------------------------------------


class RearTractionLoss(BaseEffect):
    """
    Rear traction loss / oversteer feel for dirt cars.

    This is THE primary effect for dirt sprint cars. On dirt, the front
    tires are mostly along for the ride — the driver steers with the
    throttle by controlling the rear slide angle. This effect translates
    the rear slip into a steering force so the driver can feel:

    1. When the rear breaks loose (onset of slide)
    2. How far the rear is sliding (slide angle magnitude)
    3. Which direction the rear is going (so you can counter-steer)
    4. The transition from grip to full slide

    The force pushes the wheel in the direction you need to counter-steer,
    so it naturally guides the driver's hands.
    """

    name = "rear_traction_loss"

    def __init__(self, config: EffectConfig | None = None,
                 slip_onset: float = 0.08, full_slide: float = 0.5):
        super().__init__(config or EffectConfig(gain=1.0))
        self.slip_onset = slip_onset    # rear slip ratio where you start to feel it
        self.full_slide = full_slide    # rear slip ratio considered "full slide"
        self._prev_slip = 0.0
        self._prev_yaw = 0.0

    def compute(self, telemetry: TelemetryData) -> float:
        # Average rear tire slip
        rear_slip = (telemetry.lr_tire_slip + telemetry.rr_tire_slip) / 2.0

        if rear_slip < self.slip_onset:
            self._prev_slip = rear_slip
            return 0.0

        # Normalize slip into 0-1 range (onset to full slide)
        slip_range = self.full_slide - self.slip_onset
        slip_magnitude = min(1.0, (rear_slip - self.slip_onset) / slip_range)

        # Direction: yaw rate tells us which way the rear is swinging
        # Positive yaw = car rotating left = rear swinging right
        # The force should push the wheel to counter-steer (same sign as yaw)
        yaw = telemetry.yaw_rate

        # Also use lateral velocity as a secondary direction indicator
        # VelocityY > 0 means car sliding to the right
        lat_vel = telemetry.velocity_y
        lat_factor = max(-1.0, min(1.0, lat_vel / 5.0))  # normalize ~5 m/s

        # Blend yaw and lateral velocity for direction
        # Yaw is more immediate, lat_vel confirms the slide direction
        yaw_normalized = max(-1.0, min(1.0, yaw / 1.5))  # ~1.5 rad/s = big yaw
        direction = yaw_normalized * 0.6 + lat_factor * 0.4

        # Rate of change of slip — sudden break-loose feels different than steady slide
        slip_delta = rear_slip - self._prev_slip
        self._prev_slip = rear_slip
        onset_kick = min(1.0, max(0.0, slip_delta / 0.05)) * 0.3  # sharp kick on breakaway

        # Combine: magnitude of slide * direction + onset kick
        force = (slip_magnitude + onset_kick) * direction

        return max(-1.0, min(1.0, force))


class DirtYawFeedback(BaseEffect):
    """
    Yaw rate feedback for dirt driving.

    Dirt cars are almost always rotating. This effect gives a constant
    sense of how fast the car is yawing. Combined with RearTractionLoss,
    it helps the driver modulate throttle to control the slide angle.

    On pavement this would be distracting, but on dirt it's the primary
    way you "feel" what the car is doing since the front tires aren't
    providing much useful SAT.
    """

    name = "dirt_yaw_feedback"

    def __init__(self, config: EffectConfig | None = None,
                 max_yaw_rate: float = 2.0):
        super().__init__(config or EffectConfig(gain=0.7))
        self.max_yaw_rate = max_yaw_rate  # rad/s for normalization
        self._yaw_history = deque(maxlen=8)

    def compute(self, telemetry: TelemetryData) -> float:
        yaw = telemetry.yaw_rate
        self._yaw_history.append(yaw)

        # Current yaw normalized
        yaw_norm = max(-1.0, min(1.0, yaw / self.max_yaw_rate))

        # Yaw acceleration (rate of change) — feels like the car snapping around
        if len(self._yaw_history) >= 3:
            recent = list(self._yaw_history)
            yaw_accel = recent[-1] - recent[-3]  # delta over ~3 ticks
            yaw_accel_norm = max(-0.5, min(0.5, yaw_accel / 0.5))
        else:
            yaw_accel_norm = 0.0

        # Steady yaw is smooth force, yaw accel adds sharpness
        return yaw_norm * 0.7 + yaw_accel_norm * 0.3


class ThrottleSteer(BaseEffect):
    """
    Throttle-induced rear slide feedback.

    On dirt sprint cars, getting on the throttle mid-corner kicks the
    rear out. This effect ties throttle input to the FFB when the rear
    is already loose, so you feel the connection between your right foot
    and the steering wheel.

    When throttle increases while rear tires are slipping, the wheel
    pushes in the counter-steer direction proportionally.
    """

    name = "throttle_steer"

    def __init__(self, config: EffectConfig | None = None):
        super().__init__(config or EffectConfig(gain=0.5))
        self._prev_throttle = 0.0

    def compute(self, telemetry: TelemetryData) -> float:
        rear_slip = (telemetry.lr_tire_slip + telemetry.rr_tire_slip) / 2.0
        throttle = telemetry.throttle

        # Only active when rear is sliding
        if rear_slip < 0.05:
            self._prev_throttle = throttle
            return 0.0

        # Throttle change — positive = getting on it, negative = lifting
        throttle_delta = throttle - self._prev_throttle
        self._prev_throttle = throttle

        # Scale by how much the rear is already loose
        slip_factor = min(1.0, rear_slip / 0.3)

        # Throttle application while sliding pushes the force
        # Direction comes from yaw (which way the car is rotating)
        yaw_dir = 1.0 if telemetry.yaw_rate > 0 else -1.0

        # Getting on throttle while sliding = more counter-steer force
        # Lifting while sliding = less force (car straightening)
        force = throttle_delta * slip_factor * yaw_dir * 3.0

        # Also add steady-state component: holding throttle while sliding
        steady = throttle * slip_factor * yaw_dir * 0.15

        return max(-1.0, min(1.0, force + steady))


class DirtSurfaceRumble(BaseEffect):
    """
    Dirt surface texture and loose surface rumble.

    Dirt tracks have more surface variation than pavement. This uses
    all four suspension channels to create a richer rumble that
    conveys the loose, bumpy surface. Heavier than pavement rumble
    since the surface is constantly moving under the tires.
    """

    name = "dirt_surface_rumble"

    def __init__(self, config: EffectConfig | None = None):
        super().__init__(config or EffectConfig(gain=0.4))
        self._hist_lf = deque(maxlen=12)
        self._hist_rf = deque(maxlen=12)
        self._hist_lr = deque(maxlen=12)
        self._hist_rr = deque(maxlen=12)

    def compute(self, telemetry: TelemetryData) -> float:
        self._hist_lf.append(telemetry.lf_shock_vel)
        self._hist_rf.append(telemetry.rf_shock_vel)
        self._hist_lr.append(telemetry.lr_shock_vel)
        self._hist_rr.append(telemetry.rr_shock_vel)

        if len(self._hist_lf) < 4:
            return 0.0

        # Variance across all four corners — dirt is rough everywhere
        lf_var = float(np.var(list(self._hist_lf)))
        rf_var = float(np.var(list(self._hist_rf)))
        lr_var = float(np.var(list(self._hist_lr)))
        rr_var = float(np.var(list(self._hist_rr)))

        # Front corners weighted more for steering feel, but rears contribute
        total_rumble = (lf_var + rf_var) * 0.35 + (lr_var + rr_var) * 0.15

        # Normalize — dirt surfaces produce higher variance than pavement
        magnitude = min(1.0, total_rumble / 0.08)

        # Directional bias from front left/right difference
        front_diff = lf_var - rf_var
        direction = max(-1.0, min(1.0, front_diff / 0.03))

        # If direction is very small, add some oscillation for texture feel
        if abs(direction) < 0.2:
            direction = 0.3 * math.sin(telemetry.session_time * 40.0)

        return magnitude * direction
