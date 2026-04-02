"""
Direct Drive FFB - Main entry point.

Launches the telemetry reader, FFB engine, and GUI.
Hardware connection is managed through the GUI.

Usage:
    python main.py
    python main.py --profile config/my_profile.json
    python main.py --no-gui  (headless mode, connects hardware immediately)
"""

import sys
import argparse
import logging
import signal

from src.telemetry.iracing_reader import IRacingTelemetry
from src.ffb.engine import FFBEngine
from src.ffb.effects import EffectConfig
from src.hardware.modbus_driver import AASDModbusDriver
from src.config import load_profile, apply_engine_config, get_hardware_config, apply_effect_configs


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_and_apply_profile(profile_path, engine, hardware):
    """Load a profile and apply its settings to engine and hardware."""
    try:
        profile = load_profile(profile_path)
        logging.info(f"Loaded profile: {profile.get('profile_name', 'unnamed')}")

        # Apply engine config
        eng_cfg = apply_engine_config(profile)
        engine.config.master_gain = eng_cfg.master_gain
        engine.config.max_torque_pct = eng_cfg.max_torque_pct
        engine.config.output_rate_hz = eng_cfg.output_rate_hz
        engine.config.slew_rate_limit = eng_cfg.slew_rate_limit
        engine.config.deadzone = eng_cfg.deadzone
        engine.config.center_spring_gain = eng_cfg.center_spring_gain
        engine.config.center_spring_damping = eng_cfg.center_spring_damping
        engine.config.invert_output = eng_cfg.invert_output

        # Apply effect configs
        effects = apply_effect_configs(profile)
        for name, cfg in effects.items():
            if name in engine.effects:
                engine.effects[name].config.gain = cfg.get("gain", 1.0)
                engine.effects[name].config.enabled = cfg.get("enabled", True)
                engine.effects[name].config.smoothing = cfg.get("smoothing", 0.0)
                engine.effects[name].config.min_speed = cfg.get("min_speed", 0.0)

        # Apply hardware config
        hw_cfg = get_hardware_config(profile)
        hardware.port = hw_cfg.get("port", hardware.port)
        hardware.baudrate = hw_cfg.get("baudrate", hardware.baudrate)
        hardware.slave_address = hw_cfg.get("slave_address", hardware.slave_address)
        hardware.torque_limit_pct = hw_cfg.get("torque_limit_pct", hardware.torque_limit_pct)
        hardware.speed_limit_rpm = hw_cfg.get("speed_limit_rpm", hardware.speed_limit_rpm)

    except Exception as e:
        logging.warning(f"Could not load profile: {e}. Using defaults.")


def run_gui(telemetry, engine, hardware):
    """Launch the PyQt5 GUI."""
    from PyQt5.QtWidgets import QApplication
    from src.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark theme
    from PyQt5.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    window = MainWindow(telemetry, engine, hardware)
    window.show()

    return app.exec_()


def run_headless(telemetry, engine, hardware):
    """Run without GUI - connect hardware immediately and process FFB."""
    import time

    logging.info("Running in headless mode")
    logging.info(f"Connecting to {hardware.port}...")

    if not hardware.connect():
        logging.error("Hardware connection failed. Exiting.")
        return 1

    if not hardware.enable():
        logging.error("Could not enable servo. Exiting.")
        hardware.disconnect()
        return 1

    engine.set_hardware_callback(hardware.set_torque)
    logging.info("FFB pipeline active. Press Ctrl+C to stop.")

    # Block until interrupted
    stop = False

    def on_signal(sig, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    while not stop:
        time.sleep(0.5)
        telem = telemetry.latest
        if telem.is_on_track:
            logging.info(
                f"Speed: {telem.speed * 3.6:.0f} km/h | "
                f"Output: {engine.output:+.3f} | "
                f"Rate: {engine.loop_rate:.0f} Hz"
            )

    # Cleanup
    engine.set_hardware_callback(None)
    hardware.disable()
    hardware.disconnect()
    return 0


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Direct Drive FFB Steering Wheel Controller")
    parser.add_argument("--profile", type=str, default=None,
                        help="Path to profile JSON file")
    parser.add_argument("--no-gui", action="store_true",
                        help="Run in headless mode (no GUI)")
    parser.add_argument("--port", type=str, default=None,
                        help="Override serial port (e.g., COM3, /dev/ttyUSB0)")
    args = parser.parse_args()

    # Create core components
    telemetry = IRacingTelemetry(poll_rate_hz=360.0)
    hardware = AASDModbusDriver()
    engine = FFBEngine(telemetry)

    # Load profile
    load_and_apply_profile(args.profile, engine, hardware)

    # CLI overrides
    if args.port:
        hardware.port = args.port

    # Start telemetry and engine
    telemetry.start()
    engine.start()

    logging.info("Direct Drive FFB started")
    logging.info(f"Master gain: {engine.config.master_gain}")
    logging.info(f"Max torque: {engine.config.max_torque_pct}%")

    if args.no_gui:
        code = run_headless(telemetry, engine, hardware)
    else:
        code = run_gui(telemetry, engine, hardware)

    # Cleanup
    engine.stop()
    telemetry.stop()
    logging.info("Shutdown complete")
    return code


if __name__ == "__main__":
    sys.exit(main())
