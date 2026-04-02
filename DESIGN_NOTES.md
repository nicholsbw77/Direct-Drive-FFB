# Direct Drive Force Feedback Steering Wheel - Design Notes

## Project Goal
Build a custom direct drive force feedback steering wheel using:
- **AASD series servo drive + motor** (hardware)
- **Python iRacing SDK** (telemetry source)
- **Telemetry-based FFB** (SimCommander-style, bypassing Windows game controller/DirectInput)
- **GUI interface** for configuration and tuning
- **Thanos 4U motion controller** (potential hardware interface between PC and servo)

## Why Telemetry-Based FFB (SimCommander Approach)
- Reads telemetry directly from iRacing shared memory (not limited to DirectInput FFB)
- DirectInput FFB is limited to ~60Hz update rate, coarse effect types, and lossy signal chain
- Telemetry approach gives access to raw physics data at full sim tick rate (~360Hz in iRacing)
- Can implement custom effect algorithms: self-aligning torque, curb rumble, road texture, engine vibration, understeer, collision impacts, tire slip
- Full control over signal processing pipeline: filtering, mixing, gain curves

## AASD Servo Drive - Key Technical Findings

### Control Modes (Pn002)
| Pn002 | Mode |
|-------|------|
| 0 | **Torque mode** (what we want for FFB) |
| 1 | Speed mode |
| 2 | Position mode (default) |
| 3 | Position/Speed mode |
| 4 | Position/Torque mode |
| 5 | Speed/Torque mode |

### Communication Interfaces
- **CN1**: RS-232 or RS-485 (Modbus ASCII or RTU)
- **CN2**: Control interface (DB25) with analog input, pulse input, digital I/O
- **CN3**: Encoder interface

### Two Approaches to Control Torque

#### Option A: Analog Voltage (-10V to +10V)
- Pin 25 (Vref) and Pin 13 (AGND) on CN2
- Direct analog voltage commands torque in torque mode
- Pn189: Analog torque gain (1-300, default 30 %/V)
- Pn190: Analog torque offset adjustment (-1500~1500 mV)
- Pn191: Analog torque direction (0-1)
- Pn188: Smooth filtering time (1~500, units 0.1ms)
- **Pros**: Very fast response, simple signal path
- **Cons**: Requires a DAC (digital-to-analog converter) from PC, noise susceptible

#### Option B: Modbus RTU over RS-232/RS-485
- Pn064: Communication mode (0=off, 1=RS232, 2=RS485)
- Pn065: Station address (1-254)
- Pn066: Baud rate (0-3) - need to check mapping, likely up to 115200
- Pn067: Communication mode setting (0-8)
- Modbus commands: 03H (read registers), 06H (write single), 10H (write multiple)
- Parameter address space: 0x0000-0x00EF maps to Pn000-Pn239
- Can write internal torque commands: Pn200-Pn203 (internal torque 1-4, range -300~300 %)
- Pn204: Torque command source (0=external/analog, 1=internal)
- **Pros**: Digital, no DAC needed, can also read motor status/position
- **Cons**: Modbus latency may limit update rate, ~5.5ms write delay per register

### Key Torque Parameters
| Parameter | Function | Range | Default |
|-----------|----------|-------|---------|
| Pn002 | Control mode | 0-5 | 2 (set to 0 for torque) |
| Pn003 | Servo enable mode | 0-1 | 0 |
| Pn008 | Internal CCW torque limit | 0-300% | 300 |
| Pn009 | Internal CW torque limit | -300~0% | -300 |
| Pn186 | Torque command deceleration mode | 0-1 | 0 |
| Pn187 | Torque linear decel time constant | 1-30000ms | 1 |
| Pn188 | Analog torque filter time | 1-500 (x0.1ms) | 1 |
| Pn189 | Analog torque gain | 1-300 %/V | 30 |
| Pn192-197 | Torque regulator PID gains | various | 100 |
| Pn198 | Torque control speed limit | 0-4500 rpm | 2500 |
| Pn200-203 | Internal torque commands 1-4 | -300~300% | 0 |
| Pn204 | Torque command source | 0=analog, 1=internal | 0 |

### Encoder Feedback
- 2500 pulses/rev, 15-line incremental, differential output
- Encoder divider output available on CN2 (PA+/PA-, PB+/PB-, PZ+/PZ-)
- Pn016/Pn017: Encoder divider ratio (DA/DB)
- Can read wheel position for centering and rotation limits

### Safety Considerations
- Pn008/009: Torque limits (set conservatively for FFB use!)
- Pn198: Speed limit in torque mode (critical - limit to ~200rpm for steering)
- Pn012-015: Overload alarm settings
- Emergency stop input available on SigIn
- Electromagnetic brake support (Pn029-032)

## Thanos 4U Controller
- GitHub: https://github.com/tronicgr/Thanos4U-firmware
- Originally designed for motion simulator platforms
- Communicates with PC via USB serial
- Could potentially serve as intermediate controller
- Has its own config tool for parameter setup
- **Research needed**: Exact serial protocol, whether it can relay torque commands to AASD

## Hardware Interface Options (Ranked)

### 1. Direct RS-232/RS-485 from PC to AASD (Simplest)
- USB-to-RS485 adapter ($5-10)
- Python `pymodbus` or `minimalmodbus` library
- Write torque commands via Modbus RTU
- Read encoder position for wheel angle feedback
- **Concern**: Modbus write latency (~5.5ms per register write)

### 2. DAC Board for Analog Torque (Fastest Response)
- USB DAC or Arduino/ESP32 with DAC output
- Output -10V to +10V analog signal to CN2 pin 25
- Need level shifting (most DACs are 0-3.3V or 0-5V)
- Op-amp circuit to scale and offset to +/-10V range
- **Concern**: Analog noise, need good shielding

### 3. Thanos Controller as Interface
- Would need custom firmware or protocol understanding
- May add unnecessary complexity
- Better suited for motion platform use case
- **Verdict**: Probably not ideal for single-axis FFB steering

### 4. Arduino/STM32 Intermediary
- Microcontroller receives commands via USB serial from Python
- Outputs either analog voltage (DAC) or step/dir pulses or Modbus
- Can also read encoder for position feedback
- Adds flexibility but also a layer of complexity

## Software Architecture (Planned)

```
[iRacing] --> [Shared Memory/pyirsdk] --> [Python FFB Engine] --> [Hardware Interface] --> [AASD Servo]
                                               |
                                          [GUI (PyQt/tkinter)]
                                               |
                                     [Effect Config/Profiles]
```

### FFB Engine Components (to research/implement)
1. **Telemetry Reader** - pyirsdk shared memory access
2. **Effect Processors** - Individual FFB effect algorithms
   - Self-aligning torque (from steering torque telemetry)
   - Road surface / curb rumble
   - Engine vibration
   - Tire slip / understeer
   - Collision / impact
   - Wind effect
3. **Signal Mixer** - Combine effects with gain/priority
4. **Output Filter** - Smoothing, slew rate limiting, safety clamps
5. **Hardware Driver** - Modbus RTU or analog output to AASD

### Key iRacing Telemetry Variables (to verify)
- `SteeringWheelTorque` - self-aligning torque from physics
- `LateralAccel` / `LongAccel` - G-forces
- `Speed` - vehicle speed
- `RPM` - engine RPM
- `Gear` - current gear
- `LFshockDefl`, `RFshockDefl`, etc. - suspension
- `LFtempCL/CM/CR` - tire temps (grip indicator)
- Various wheel slip variables

## Next Steps / TODO
- [ ] Research pyirsdk telemetry variables in detail
- [ ] Research SimCommander FFB algorithms
- [ ] Decide on hardware interface (Modbus vs analog vs hybrid)
- [ ] Test Modbus communication latency with AASD
- [ ] Prototype basic torque control via Modbus from Python
- [ ] Design GUI layout and feature set
- [ ] Implement telemetry reader
- [ ] Implement basic self-aligning torque effect
- [ ] Add safety systems (torque limits, e-stop, rotation limits)
- [ ] Build and test full FFB pipeline
