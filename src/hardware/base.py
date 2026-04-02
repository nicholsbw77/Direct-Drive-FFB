"""
Abstract base class for hardware drivers.

All hardware interfaces (Modbus, analog DAC, etc.) implement this
interface so the FFB engine doesn't need to know the specifics.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class HardwareStatus:
    """Current hardware state."""
    connected: bool = False
    enabled: bool = False
    actual_torque_pct: float = 0.0
    motor_rpm: float = 0.0
    encoder_position: int = 0
    error_code: int = 0
    error_message: str = ""
    temperature: float = 0.0


class BaseHardwareDriver(ABC):

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the hardware. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self):
        """Disconnect from hardware and set torque to zero."""
        ...

    @abstractmethod
    def enable(self) -> bool:
        """Enable servo drive. Returns True on success."""
        ...

    @abstractmethod
    def disable(self):
        """Disable servo drive (zero torque, servo off)."""
        ...

    @abstractmethod
    def set_torque(self, value: float):
        """
        Set torque output.

        Args:
            value: Torque in range [-1.0, 1.0] where 1.0 = max configured torque
        """
        ...

    @abstractmethod
    def get_status(self) -> HardwareStatus:
        """Read current hardware status."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        ...
