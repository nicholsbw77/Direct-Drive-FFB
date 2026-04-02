"""
AASD servo drive Modbus RTU driver.

Communicates with the AASD servo drive via Modbus RTU over RS-232/RS-485
to control torque in torque mode (Pn002=0).

Key AASD register addresses (hex):
  Pn002 (0x0002): Control mode - set to 0 for torque mode
  Pn003 (0x0003): Servo enable mode
  Pn008 (0x0008): Internal CCW torque limit (0-300%)
  Pn009 (0x0009): Internal CW torque limit (-300~0%)
  Pn064 (0x0040): Communication mode (1=RS232, 2=RS485)
  Pn065 (0x0041): Station address (1-254)
  Pn066 (0x0042): Baud rate
  Pn198 (0x00C6): Speed limit in torque mode (rpm)
  Pn200 (0x00C8): Internal torque command 1 (-300~300%)
  Pn204 (0x00CC): Torque command source (0=analog, 1=internal)
"""

import threading
import time
import logging

from pymodbus.client import ModbusSerialClient
from pymodbus.constants import Endian
from pymodbus.payload import BinaryPayloadDecoder

from src.hardware.base import BaseHardwareDriver, HardwareStatus

logger = logging.getLogger(__name__)


# AASD parameter addresses (parameter number = register address)
class AASDRegisters:
    CONTROL_MODE = 0x0002           # Pn002: 0=torque, 1=speed, 2=position
    SERVO_ENABLE = 0x0003           # Pn003: 0=pin SigIn, 1=comm enable
    CCW_TORQUE_LIMIT = 0x0008      # Pn008: 0-300%
    CW_TORQUE_LIMIT = 0x0009       # Pn009: -300~0%
    COMM_MODE = 0x0040              # Pn064: 0=off, 1=RS232, 2=RS485
    STATION_ADDR = 0x0041           # Pn065: 1-254
    BAUD_RATE = 0x0042              # Pn066: 0=4800, 1=9600, 2=19200, 3=38400
    COMM_SETTING = 0x0043           # Pn067: protocol setting
    TORQUE_FILTER = 0x00BC          # Pn188: analog torque filter (0.1ms units)
    TORQUE_GAIN = 0x00BD            # Pn189: analog torque gain (%/V)
    SPEED_LIMIT_TORQUE = 0x00C6    # Pn198: speed limit in torque mode (rpm)
    INTERNAL_TORQUE_1 = 0x00C8     # Pn200: internal torque cmd 1 (-300~300%)
    INTERNAL_TORQUE_2 = 0x00C9     # Pn201
    INTERNAL_TORQUE_3 = 0x00CA     # Pn202
    INTERNAL_TORQUE_4 = 0x00CB     # Pn203
    TORQUE_CMD_SOURCE = 0x00CC     # Pn204: 0=analog, 1=internal

    # Status/monitoring registers (read-only area, base 0x0100)
    STATUS_DISPLAY = 0x0100         # Dn000: status display
    MOTOR_SPEED = 0x0101            # Dn001: motor speed (rpm)
    MOTOR_CURRENT = 0x0105          # Dn005: motor current
    ENCODER_POS_LO = 0x0106         # Dn006: encoder position low word
    ENCODER_POS_HI = 0x0107         # Dn007: encoder position high word
    ERROR_CODE = 0x010A             # Dn010: error code


class AASDModbusDriver(BaseHardwareDriver):
    """
    Modbus RTU driver for AASD servo drive.

    Sends torque commands by writing to the internal torque register (Pn200).
    Reads motor status, speed, and encoder position for feedback.
    """

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 38400,
        slave_address: int = 1,
        torque_limit_pct: float = 50.0,
        speed_limit_rpm: int = 200,
    ):
        self.port = port
        self.baudrate = baudrate
        self.slave_address = slave_address
        self.torque_limit_pct = torque_limit_pct
        self.speed_limit_rpm = speed_limit_rpm

        self._client: ModbusSerialClient | None = None
        self._connected = False
        self._enabled = False
        self._lock = threading.Lock()
        self._last_torque = 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def connect(self) -> bool:
        try:
            self._client = ModbusSerialClient(
                port=self.port,
                baudrate=self.baudrate,
                parity="E",         # AASD default: Even parity
                stopbits=1,
                bytesize=8,
                timeout=1.0,
            )
            if self._client.connect():
                self._connected = True
                logger.info(f"Connected to AASD on {self.port} at {self.baudrate} baud")
                return True
            else:
                logger.error(f"Failed to connect to {self.port}")
                return False
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False

    def disconnect(self):
        if self._enabled:
            self.disable()
        if self._client:
            self._client.close()
        self._connected = False
        logger.info("Disconnected from AASD")

    def configure_for_ffb(self) -> bool:
        """
        Configure AASD parameters for FFB steering use.

        Sets torque mode, internal torque source, safety limits.
        Call this once after connecting and before enabling.
        """
        if not self._connected:
            return False

        try:
            writes = [
                # Set torque control mode
                (AASDRegisters.CONTROL_MODE, 0),
                # Set torque command source to internal (Modbus-controlled)
                (AASDRegisters.TORQUE_CMD_SOURCE, 1),
                # Set speed limit for safety
                (AASDRegisters.SPEED_LIMIT_TORQUE, self.speed_limit_rpm),
                # Set torque limits (symmetrical)
                (AASDRegisters.CCW_TORQUE_LIMIT, int(self.torque_limit_pct)),
                (AASDRegisters.CW_TORQUE_LIMIT, int(-self.torque_limit_pct)),
                # Zero initial torque
                (AASDRegisters.INTERNAL_TORQUE_1, 0),
                # Enable servo via communication
                (AASDRegisters.SERVO_ENABLE, 1),
            ]

            for reg, value in writes:
                result = self._write_register(reg, value)
                if not result:
                    logger.error(f"Failed to write register 0x{reg:04X} = {value}")
                    return False
                time.sleep(0.01)  # Small delay between writes

            logger.info(
                f"AASD configured: torque mode, limit={self.torque_limit_pct}%, "
                f"speed_limit={self.speed_limit_rpm}rpm"
            )
            return True

        except Exception as e:
            logger.error(f"Configuration error: {e}")
            return False

    def enable(self) -> bool:
        if not self._connected:
            return False

        if not self.configure_for_ffb():
            return False

        self._enabled = True
        logger.info("AASD servo enabled for FFB")
        return True

    def disable(self):
        if self._connected and self._client:
            # Zero torque first
            self._write_register(AASDRegisters.INTERNAL_TORQUE_1, 0)
            time.sleep(0.01)
        self._enabled = False
        self._last_torque = 0.0
        logger.info("AASD servo disabled")

    def set_torque(self, value: float):
        """
        Set torque output.

        Args:
            value: [-1.0, 1.0] where 1.0 = torque_limit_pct of rated torque
        """
        if not self._enabled:
            return

        # Clamp input
        value = max(-1.0, min(1.0, value))

        # Convert to AASD internal torque units (-300 to 300 %)
        # Scale by our configured torque limit
        torque_pct = int(value * self.torque_limit_pct)

        # Only write if value changed (reduce bus traffic)
        if torque_pct == int(self._last_torque * self.torque_limit_pct):
            return

        self._write_register(AASDRegisters.INTERNAL_TORQUE_1, torque_pct)
        self._last_torque = value

    def get_status(self) -> HardwareStatus:
        status = HardwareStatus(
            connected=self._connected,
            enabled=self._enabled,
        )

        if not self._connected:
            return status

        try:
            # Read motor speed
            val = self._read_register(AASDRegisters.MOTOR_SPEED)
            if val is not None:
                # Interpret as signed 16-bit
                status.motor_rpm = val if val < 32768 else val - 65536

            # Read error code
            val = self._read_register(AASDRegisters.ERROR_CODE)
            if val is not None:
                status.error_code = val
                status.error_message = self._decode_error(val)

            # Read encoder position (32-bit, 2 registers)
            lo = self._read_register(AASDRegisters.ENCODER_POS_LO)
            hi = self._read_register(AASDRegisters.ENCODER_POS_HI)
            if lo is not None and hi is not None:
                status.encoder_position = (hi << 16) | lo

            status.actual_torque_pct = self._last_torque * self.torque_limit_pct

        except Exception as e:
            logger.warning(f"Status read error: {e}")

        return status

    def read_encoder_angle(self) -> float | None:
        """Read encoder position and convert to degrees (0-360)."""
        lo = self._read_register(AASDRegisters.ENCODER_POS_LO)
        hi = self._read_register(AASDRegisters.ENCODER_POS_HI)
        if lo is None or hi is None:
            return None

        position = (hi << 16) | lo
        # 2500 pulses/rev * 4 (quadrature) = 10000 counts/rev
        degrees = (position % 10000) / 10000.0 * 360.0
        return degrees

    def _write_register(self, address: int, value: int) -> bool:
        """Write a single holding register via Modbus function 06H."""
        with self._lock:
            try:
                # Handle signed values: convert negative to unsigned 16-bit
                if value < 0:
                    value = value & 0xFFFF

                result = self._client.write_register(
                    address=address,
                    value=value,
                    slave=self.slave_address,
                )
                return not result.isError()
            except Exception as e:
                logger.error(f"Write register 0x{address:04X} error: {e}")
                return False

    def _read_register(self, address: int) -> int | None:
        """Read a single holding register via Modbus function 03H."""
        with self._lock:
            try:
                result = self._client.read_holding_registers(
                    address=address,
                    count=1,
                    slave=self.slave_address,
                )
                if result.isError():
                    return None
                return result.registers[0]
            except Exception as e:
                logger.error(f"Read register 0x{address:04X} error: {e}")
                return None

    @staticmethod
    def _decode_error(code: int) -> str:
        """Decode AASD error codes to human-readable strings."""
        errors = {
            0: "No error",
            1: "Overcurrent (Er.01)",
            2: "Overvoltage (Er.02)",
            3: "Overload (Er.03)",
            4: "Overspeed (Er.04)",
            5: "Encoder error (Er.05)",
            6: "Position deviation overflow (Er.06)",
            7: "Undervoltage (Er.07)",
            8: "Parameter error (Er.08)",
            9: "EEPROM error (Er.09)",
        }
        return errors.get(code, f"Unknown error (Er.{code:02d})")
