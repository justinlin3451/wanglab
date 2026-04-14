# CVD Controller — Wang Lab

A desktop application for automated 2D material growth via Chemical Vapor Deposition (CVD).

Controls a furnace, two mass flow controllers (MFCs), and a linear rail motor. Supports time-based ramp/hold recipes, live data plotting, safety interlocks, and run logging.

---

## Hardware

| Device | Interface | Default |
|--------|-----------|---------|
| MTI Tube Furnace | RS-485 Serial (Modbus RTU) | COM5, 9600 baud |
| Alicat MFC — Ar | TCP Ethernet | 192.168.0.7:26, addr A |
| Alicat MFC — H₂ | TCP Ethernet | 192.168.0.7:26, addr B |
| Arduino Rail Motor | Serial | COM3, 19200 baud |

---

## Setup

**Requirements:** Python 3.10+

```bash
pip install PyQt6 pyserial pyqtgraph
```

**Run:**
```bash
python main.py
```

---

## Configuration

Edit `config/workspace.json` to set COM ports, IP addresses, and simulation mode.

Set `"simulate": true` for each device to run without hardware connected.

Rail open/close positions (in steps) are set via `open_pos` and `close_pos` in the rail config.

---

## Recipe Format

Recipes are JSON files stored in the `recipes/` folder. Each step has a duration and setpoints for all devices. Step type is either `HOLD` (maintain values) or `RAMP` (linearly interpolate to new values).

See `recipes/mos2_standard.json` for an example.

---

## Safety Interlocks

- Hard temperature ceiling: 1400°C
- H₂ flow blocked above 200°C unless explicitly armed
- Ramp rate warning above 50°C/min
- Emergency stop cuts all setpoints to zero instantly

---

## Project Structure

```
cvd_controller/
├── main.py                    # Entry point
├── config/workspace.json      # Device configuration
├── recipes/                   # Recipe JSON files
├── core/
│   ├── devices/               # Hardware drivers
│   │   ├── furnace.py         # MTI furnace (Modbus)
│   │   ├── mfc.py             # Alicat MFC (TCP)
│   │   ├── rail.py            # Arduino rail (Serial)
│   │   └── manager.py         # Device lifecycle manager
│   ├── recipe_engine.py       # Recipe execution
│   ├── safety.py              # Safety interlocks
│   └── data_logger.py         # SQLite + CSV logging
└── gui/
    └── main_window.py         # PyQt6 UI
```

---

## Roadmap

- [ ] AI parameter optimization via Claude API
- [ ] Anomaly detection during runs
- [ ] Closed-loop defect feedback from optical microscope
- [ ] Run history browser
- [ ] Recipe editor improvements
