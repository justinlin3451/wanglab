# core/recipe_engine.py
"""
Recipe Engine – loads and executes CVD growth recipes.

A recipe is a sequence of Steps. Each Step runs for a duration and
contains Setpoints (one per device control). The engine interpolates
between setpoints for ramp steps.

Step types:
  - HOLD  : maintain setpoints for a duration
  - RAMP  : linearly interpolate from previous setpoints to new ones

Recipe JSON format:
{
  "name": "MoS2 growth",
  "description": "...",
  "version": 1,
  "steps": [
    {
      "name": "Purge",
      "type": "HOLD",
      "duration_s": 300,
      "setpoints": {
        "furnace.temp": 25,
        "ar.flow": 200,
        "h2.flow": 0,
        "rail.position": 0
      }
    },
    {
      "name": "Ramp to growth temp",
      "type": "RAMP",
      "duration_s": 1800,
      "setpoints": {
        "furnace.temp": 900,
        "ar.flow": 100,
        "h2.flow": 50
      }
    },
    ...
  ]
}
"""

import json
import time
import threading
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class StepType(Enum):
    HOLD = "HOLD"
    RAMP = "RAMP"


class RunStatus(Enum):
    IDLE     = auto()
    RUNNING  = auto()
    PAUSED   = auto()
    FINISHED = auto()
    ABORTED  = auto()
    ERROR    = auto()


@dataclass
class RecipeStep:
    name:       str
    step_type:  StepType
    duration_s: float
    setpoints:  dict[str, float]   # "device_id.control" → value
    index:      int = 0


@dataclass
class Recipe:
    name:        str
    description: str
    version:     int
    steps:       list[RecipeStep]
    filepath:    Optional[Path] = None

    @classmethod
    def from_dict(cls, d: dict, filepath: Optional[Path] = None) -> "Recipe":
        steps = []
        for i, s in enumerate(d.get("steps", [])):
            steps.append(RecipeStep(
                name=s.get("name", f"Step {i+1}"),
                step_type=StepType(s.get("type", "HOLD")),
                duration_s=float(s["duration_s"]),
                setpoints={k: float(v) for k, v in s.get("setpoints", {}).items()},
                index=i,
            ))
        return cls(
            name=d.get("name", "Unnamed"),
            description=d.get("description", ""),
            version=int(d.get("version", 1)),
            steps=steps,
            filepath=filepath,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "Recipe":
        path = Path(path)
        with open(path) as f:
            return cls.from_dict(json.load(f), filepath=path)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "steps": [
                {
                    "name": s.name,
                    "type": s.step_type.value,
                    "duration_s": s.duration_s,
                    "setpoints": s.setpoints,
                }
                for s in self.steps
            ],
        }

    def save(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        self.filepath = path

    @property
    def total_duration_s(self) -> float:
        return sum(s.duration_s for s in self.steps)


# ---------------------------------------------------------------------------
# Progress snapshot (emitted to GUI / logger regularly)
# ---------------------------------------------------------------------------

@dataclass
class RunProgress:
    status:          RunStatus
    current_step:    int
    total_steps:     int
    step_name:       str
    step_elapsed_s:  float
    step_duration_s: float
    total_elapsed_s: float
    total_duration_s: float
    setpoints:       dict[str, float]


# ---------------------------------------------------------------------------
# Recipe Engine
# ---------------------------------------------------------------------------

class RecipeEngine:
    """
    Executes recipes in a background thread.
    Communicates with hardware via a DeviceManager.

    Callbacks:
        on_progress(RunProgress) – called every tick (~1 s)
        on_step_change(step_index, step_name)
        on_finished(status)
        on_alarm(message)
    """

    TICK_S = 1.0  # resolution of the execution loop

    def __init__(self, device_manager):
        self._dm          = device_manager
        self._recipe: Optional[Recipe] = None
        self._status      = RunStatus.IDLE
        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._stop_event  = threading.Event()
        self._pause_event.set()  # not paused initially

        # Callbacks
        self.on_progress:    Optional[Callable[[RunProgress], None]] = None
        self.on_step_change: Optional[Callable[[int, str], None]]    = None
        self.on_finished:    Optional[Callable[[RunStatus], None]]   = None
        self.on_alarm:       Optional[Callable[[str], None]]         = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load(self, recipe: Recipe):
        if self._status == RunStatus.RUNNING:
            raise RuntimeError("Cannot load recipe while running")
        self._recipe = recipe
        logger.info(f"Recipe loaded: {recipe.name} ({len(recipe.steps)} steps)")

    def start(self):
        if not self._recipe:
            raise RuntimeError("No recipe loaded")
        if self._status == RunStatus.RUNNING:
            return
        self._stop_event.clear()
        self._pause_event.set()
        self._status = RunStatus.RUNNING
        self._thread = threading.Thread(
            target=self._run_loop,
            name="recipe-engine",
            daemon=True,
        )
        self._thread.start()

    def pause(self):
        if self._status == RunStatus.RUNNING:
            self._pause_event.clear()
            self._status = RunStatus.PAUSED
            logger.info("Recipe paused")

    def resume(self):
        if self._status == RunStatus.PAUSED:
            self._pause_event.set()
            self._status = RunStatus.RUNNING
            logger.info("Recipe resumed")

    def abort(self):
        self._stop_event.set()
        self._pause_event.set()  # unblock if paused
        if self._thread:
            self._thread.join(timeout=5)
        self._status = RunStatus.ABORTED
        logger.info("Recipe aborted")
        if self.on_finished:
            self.on_finished(RunStatus.ABORTED)

    @property
    def status(self) -> RunStatus:
        return self._status

    # ------------------------------------------------------------------ #
    # Execution loop                                                       #
    # ------------------------------------------------------------------ #

    def _run_loop(self):
        recipe = self._recipe
        total_duration = recipe.total_duration_s
        run_start = time.time()

        # Capture "start setpoints" as the current cached values
        prev_setpoints: dict[str, float] = {}

        try:
            for step in recipe.steps:
                if self._stop_event.is_set():
                    break

                logger.info(f"Step {step.index+1}/{len(recipe.steps)}: {step.name}")
                if self.on_step_change:
                    self.on_step_change(step.index, step.name)

                step_start = time.time()
                step_duration = step.duration_s

                # For RAMP: capture starting values
                if step.step_type == StepType.RAMP:
                    start_sp = deepcopy(prev_setpoints)
                    # Fill in any missing start values from device cache
                    for key in step.setpoints:
                        if key not in start_sp:
                            dev_id, control = key.split(".", 1)
                            cached = self._dm.get_value(dev_id, control)
                            start_sp[key] = float(cached) if cached is not None else 0.0

                while True:
                    # Check stop / pause
                    if self._stop_event.is_set():
                        break
                    self._pause_event.wait()

                    elapsed = time.time() - step_start
                    if elapsed >= step_duration:
                        break

                    frac = min(elapsed / step_duration, 1.0)

                    # Compute and apply setpoints
                    current_sp: dict[str, float] = {}
                    if step.step_type == StepType.HOLD:
                        current_sp = dict(step.setpoints)
                    else:  # RAMP
                        for key, end_val in step.setpoints.items():
                            start_val = start_sp.get(key, end_val)
                            current_sp[key] = start_val + (end_val - start_val) * frac

                    self._apply_setpoints(current_sp)

                    # Emit progress
                    if self.on_progress:
                        progress = RunProgress(
                            status=self._status,
                            current_step=step.index,
                            total_steps=len(recipe.steps),
                            step_name=step.name,
                            step_elapsed_s=elapsed,
                            step_duration_s=step_duration,
                            total_elapsed_s=time.time() - run_start,
                            total_duration_s=total_duration,
                            setpoints=current_sp,
                        )
                        self.on_progress(progress)

                    time.sleep(self.TICK_S)

                prev_setpoints = dict(step.setpoints)
                if self._stop_event.is_set():
                    break

            # Done
            if not self._stop_event.is_set():
                self._status = RunStatus.FINISHED
                logger.info("Recipe finished successfully")
                if self.on_finished:
                    self.on_finished(RunStatus.FINISHED)
        except Exception as e:
            self._status = RunStatus.ERROR
            logger.error(f"Recipe engine error: {e}", exc_info=True)
            if self.on_finished:
                self.on_finished(RunStatus.ERROR)

    def _apply_setpoints(self, setpoints: dict[str, float]):
        for key, value in setpoints.items():
            try:
                dev_id, control = key.split(".", 1)
                self._dm.set_value(dev_id, control, value)
            except Exception as e:
                logger.warning(f"Failed to apply setpoint {key}={value}: {e}")
