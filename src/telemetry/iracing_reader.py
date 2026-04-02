"""
iRacing telemetry reader using pyirsdk shared memory.

Reads raw physics data at full sim tick rate (~360Hz) for FFB processing.
Bypasses DirectInput FFB limitations by reading telemetry directly.
"""

import threading
import time
from dataclasses import dataclass, field

import irsdk


@dataclass
class TelemetryData:
    """Snapshot of all telemetry values relevant to FFB processing."""

    # Steering / force feedback
    steering_wheel_torque: float = 0.0      # Nm - self-aligning torque from physics
    steering_wheel_torque_st: float = 0.0   # Nm - filtered SAT
    steering_wheel_angle: float = 0.0       # degrees
    steering_wheel_angle_max: float = 0.0   # degrees - max rotation
    steering_wheel_pct: float = 0.0         # -1 to 1

    # Accelerations (G-forces)
    lat_accel: float = 0.0                  # m/s^2 - lateral
    long_accel: float = 0.0                 # m/s^2 - longitudinal
    vert_accel: float = 0.0                 # m/s^2 - vertical

    # Vehicle dynamics
    speed: float = 0.0                      # m/s
    rpm: float = 0.0                        # engine RPM
    gear: int = 0                           # current gear (-1=R, 0=N, 1-8)
    throttle: float = 0.0                   # 0-1
    brake: float = 0.0                      # 0-1
    clutch: float = 0.0                     # 0-1

    # Yaw / rotation
    yaw_rate: float = 0.0                   # rad/s
    pitch_rate: float = 0.0                 # rad/s
    roll_rate: float = 0.0                  # rad/s

    # Suspension deflection (meters)
    lf_shock_defl: float = 0.0
    rf_shock_defl: float = 0.0
    lr_shock_defl: float = 0.0
    rr_shock_defl: float = 0.0

    # Suspension velocity (m/s)
    lf_shock_vel: float = 0.0
    rf_shock_vel: float = 0.0
    lr_shock_vel: float = 0.0
    rr_shock_vel: float = 0.0

    # Tire slip (ratio, >1 means sliding)
    lf_tire_slip: float = 0.0
    rf_tire_slip: float = 0.0
    lr_tire_slip: float = 0.0
    rr_tire_slip: float = 0.0

    # Wheel speed (rad/s)
    lf_speed: float = 0.0
    rf_speed: float = 0.0
    lr_speed: float = 0.0
    rr_speed: float = 0.0

    # Velocity at wheels (m/s along heading)
    velocity_x: float = 0.0                 # longitudinal
    velocity_y: float = 0.0                 # lateral

    # Session state
    is_on_track: bool = False
    session_time: float = 0.0
    track_surface: int = 0                  # irsdk_TrkLoc - 0=NotInWorld, 1=OffTrack, 2=InPitStall, 3=AproachingPits, 4=OnTrack

    # Car contact / collision
    car_contact: bool = False               # True if car-to-car contact detected

    # Timestamps
    tick_rate: float = 0.0                  # sim tick rate in Hz
    timestamp: float = 0.0                  # time.perf_counter() of this snapshot


# Mapping from TelemetryData field names to iRacing var names
_VAR_MAP = {
    "steering_wheel_torque": "SteeringWheelTorque",
    "steering_wheel_torque_st": "SteeringWheelTorque_ST",
    "steering_wheel_angle": "SteeringWheelAngle",
    "steering_wheel_angle_max": "SteeringWheelAngleMax",
    "steering_wheel_pct": "SteeringWheelPct",
    "lat_accel": "LatAccel",
    "long_accel": "LongAccel",
    "vert_accel": "VertAccel",
    "speed": "Speed",
    "rpm": "RPM",
    "gear": "Gear",
    "throttle": "Throttle",
    "brake": "Brake",
    "clutch": "Clutch",
    "yaw_rate": "YawRate",
    "pitch_rate": "PitchRate",
    "roll_rate": "RollRate",
    "lf_shock_defl": "LFshockDefl",
    "rf_shock_defl": "RFshockDefl",
    "lr_shock_defl": "LRshockDefl",
    "rr_shock_defl": "RRshockDefl",
    "lf_shock_vel": "LFshockVel",
    "rf_shock_vel": "RFshockVel",
    "lr_shock_vel": "LRshockVel",
    "rr_shock_vel": "RRshockVel",
    "lf_tire_slip": "LFtireSlip",
    "rf_tire_slip": "RFtireSlip",
    "lr_tire_slip": "LRtireSlip",
    "rr_tire_slip": "RRtireSlip",
    "lf_speed": "LFspd",
    "rf_speed": "RFspd",
    "lr_speed": "LRspd",
    "rr_speed": "RRspd",
    "velocity_x": "VelocityX",
    "velocity_y": "VelocityY",
    "is_on_track": "IsOnTrack",
    "session_time": "SessionTime",
    "track_surface": "PlayerTrackSurface",
    "tick_rate": "TickRate",
}


class IRacingTelemetry:
    """
    Reads iRacing telemetry from shared memory using pyirsdk.

    Runs a background thread that polls shared memory and produces
    TelemetryData snapshots. The latest snapshot is always available
    via the `latest` property.
    """

    def __init__(self, poll_rate_hz: float = 360.0):
        self._ir = irsdk.IRSDK()
        self._poll_interval = 1.0 / poll_rate_hz
        self._latest = TelemetryData()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._connected = False
        self._callbacks: list = []

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def latest(self) -> TelemetryData:
        with self._lock:
            return self._latest

    def on_update(self, callback):
        """Register a callback that fires on each new telemetry snapshot."""
        self._callbacks.append(callback)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._connected:
            self._ir.shutdown()
            self._connected = False

    def _poll_loop(self):
        while self._running:
            if not self._connected:
                if self._ir.startup():
                    self._connected = True
                else:
                    time.sleep(1.0)
                    continue

            if self._ir.is_connected:
                self._ir.freeze_var_buffer_latest()
                data = self._read_snapshot()
                with self._lock:
                    self._latest = data
                for cb in self._callbacks:
                    try:
                        cb(data)
                    except Exception:
                        pass
            else:
                self._connected = False
                self._ir.shutdown()
                with self._lock:
                    self._latest = TelemetryData()
                time.sleep(1.0)
                continue

            time.sleep(self._poll_interval)

    def _read_snapshot(self) -> TelemetryData:
        data = TelemetryData(timestamp=time.perf_counter())

        for field_name, ir_var in _VAR_MAP.items():
            val = self._ir[ir_var]
            if val is not None:
                setattr(data, field_name, val)

        # Detect car contact via sudden lateral acceleration spike
        # iRacing doesn't have a direct "contact" var, so we infer it
        data.car_contact = abs(data.lat_accel) > 40.0  # ~4G lateral spike

        return data
