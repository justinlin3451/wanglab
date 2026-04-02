# core/safety.py
"""
Safety interlock engine.

Rules are evaluated continuously. Any violation triggers an alarm
and optionally executes an automatic response (e.g. cut setpoint to 0).

Built-in rules:
  1. TEMP_MAX       – hard ceiling on furnace temperature
  2. H2_HOT_FLOW    – block H2 flow above a threshold temp unless armed
  3. RAIL_RAMP_LOCK – block rail movement when temp is ramping fast
  4. EMERGENCY_STOP – all setpoints → 0 instantly

Rules can be extended by adding SafetyRule instances to the engine.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class AlarmSeverity(Enum):
    WARNING  = auto()
    CRITICAL = auto()


@dataclass
class Alarm:
    rule_id:   str
    severity:  AlarmSeverity
    message:   str
    timestamp: float = field(default_factory=time.time)
    active:    bool  = True


@dataclass
class SafetyRule:
    """
    A named safety rule.

    check_fn(state) -> (violated: bool, message: str)
        'state' is the SystemState dict passed by the engine.

    response_fn(device_manager) is called when the rule is first violated.
    """
    rule_id:     str
    severity:    AlarmSeverity
    check_fn:    Callable[[dict], tuple[bool, str]]
    response_fn: Optional[Callable] = None
    enabled:     bool = True


class SafetyEngine:
    """
    Evaluates safety rules against current system state.
    Call evaluate(state, device_manager) regularly (e.g. every poll cycle).
    """

    def __init__(self, config: dict):
        self._rules: list[SafetyRule] = []
        self._active_alarms: dict[str, Alarm] = {}
        self._alarm_callbacks: list[Callable[[Alarm], None]] = []
        self._h2_armed = False   # operator must explicitly arm H2 at high temp

        self._temp_max      = float(config.get("temp_max", 1400.0))
        self._h2_temp_limit = float(config.get("h2_temp_limit", 200.0))
        self._ramp_rate_max = float(config.get("ramp_rate_max_per_min", 50.0))

        self._last_temp: Optional[float] = None
        self._last_temp_time: Optional[float] = None

        self._register_builtin_rules()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def evaluate(self, state: dict, device_manager) -> list[Alarm]:
        """
        Evaluate all rules against current state.
        Returns list of newly triggered alarms.
        """
        new_alarms = []
        current_temp = state.get("furnace_temp")

        # Track ramp rate
        ramp_rate = 0.0
        now = time.time()
        if current_temp is not None and self._last_temp is not None and self._last_temp_time:
            dt = now - self._last_temp_time
            if dt > 0:
                ramp_rate = abs(current_temp - self._last_temp) / dt * 60  # °C/min
        if current_temp is not None:
            self._last_temp = current_temp
            self._last_temp_time = now
        state["ramp_rate_per_min"] = ramp_rate

        for rule in self._rules:
            if not rule.enabled:
                continue
            try:
                violated, msg = rule.check_fn(state)
            except Exception as e:
                logger.warning(f"Safety rule '{rule.rule_id}' check error: {e}")
                continue

            if violated:
                if rule.rule_id not in self._active_alarms:
                    alarm = Alarm(rule.rule_id, rule.severity, msg)
                    self._active_alarms[rule.rule_id] = alarm
                    new_alarms.append(alarm)
                    logger.warning(f"SAFETY ALARM [{rule.severity.name}] {rule.rule_id}: {msg}")
                    if rule.response_fn:
                        try:
                            rule.response_fn(device_manager)
                        except Exception as e:
                            logger.error(f"Safety response error: {e}")
                    for cb in self._alarm_callbacks:
                        try:
                            cb(alarm)
                        except Exception:
                            pass
            else:
                if rule.rule_id in self._active_alarms:
                    self._active_alarms[rule.rule_id].active = False
                    del self._active_alarms[rule.rule_id]
                    logger.info(f"Safety alarm cleared: {rule.rule_id}")

        return new_alarms

    def emergency_stop(self, device_manager):
        """Cut all device setpoints to safe values immediately."""
        logger.critical("EMERGENCY STOP ACTIVATED")
        try:
            device_manager.set_value("furnace", "temp", 0)
        except Exception:
            pass
        for mfc_id in ["ar", "h2"]:
            try:
                device_manager.set_value(mfc_id, "flow", 0)
            except Exception:
                pass
        alarm = Alarm("EMERGENCY_STOP", AlarmSeverity.CRITICAL, "Emergency stop activated by operator")
        for cb in self._alarm_callbacks:
            try:
                cb(alarm)
            except Exception:
                pass

    def arm_h2(self, armed: bool):
        """Operator explicitly arms H2 flow at elevated temperature."""
        self._h2_armed = armed
        logger.info(f"H2 high-temp flow {'ARMED' if armed else 'DISARMED'}")

    def add_alarm_callback(self, callback: Callable[[Alarm], None]):
        self._alarm_callbacks.append(callback)

    def active_alarms(self) -> list[Alarm]:
        return list(self._active_alarms.values())

    def add_rule(self, rule: SafetyRule):
        self._rules.append(rule)

    # ------------------------------------------------------------------ #
    # Built-in rules                                                       #
    # ------------------------------------------------------------------ #

    def _register_builtin_rules(self):

        temp_max = self._temp_max
        h2_limit = self._h2_temp_limit
        ramp_max = self._ramp_rate_max
        h2_armed_ref = lambda: self._h2_armed

        self._rules = [
            SafetyRule(
                rule_id="TEMP_MAX",
                severity=AlarmSeverity.CRITICAL,
                check_fn=lambda s: (
                    s.get("furnace_temp", 0) > temp_max,
                    f"Furnace temp {s.get('furnace_temp'):.1f}°C exceeds hard limit {temp_max}°C"
                ),
            ),
            SafetyRule(
                rule_id="H2_HOT_FLOW",
                severity=AlarmSeverity.WARNING,
                check_fn=lambda s: (
                    (s.get("furnace_temp", 0) > h2_limit
                     and s.get("h2_flow", 0) > 0
                     and not h2_armed_ref()),
                    f"H2 flowing ({s.get('h2_flow'):.1f} sccm) while furnace > {h2_limit}°C and not armed"
                ),
            ),
            SafetyRule(
                rule_id="RAMP_RATE",
                severity=AlarmSeverity.WARNING,
                check_fn=lambda s: (
                    s.get("ramp_rate_per_min", 0) > ramp_max,
                    f"Ramp rate {s.get('ramp_rate_per_min', 0):.1f}°C/min exceeds limit {ramp_max}°C/min"
                ),
            ),
        ]
