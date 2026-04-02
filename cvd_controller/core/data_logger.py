# core/data_logger.py
"""
Data logger – writes device readings and run metadata to SQLite.
Also exports CSV per run for Excel compatibility.

Schema:
  runs      – one row per recipe execution
  readings  – time-series data for each device/control
"""

import csv
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from .devices.base import DeviceReading

logger = logging.getLogger(__name__)

DB_FILE = "data/cvd_runs.db"

CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    recipe_name TEXT,
    recipe_json TEXT,
    status      TEXT,
    notes       TEXT
);
"""

CREATE_READINGS = """
CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    INTEGER NOT NULL,
    timestamp REAL    NOT NULL,
    device_id TEXT    NOT NULL,
    control   TEXT    NOT NULL,
    value     REAL    NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
"""

CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_readings_run
    ON readings(run_id, timestamp);
"""


class DataLogger:

    def __init__(self, db_path: str | Path = DB_FILE):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._run_id: Optional[int] = None
        self._open()

    # ------------------------------------------------------------------ #
    # Setup                                                                #
    # ------------------------------------------------------------------ #

    def _open(self):
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(CREATE_RUNS)
        self._conn.execute(CREATE_READINGS)
        self._conn.execute(CREATE_IDX)
        self._conn.commit()
        logger.info(f"Database opened: {self._db_path}")

    def close(self):
        if self._conn:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Run management                                                       #
    # ------------------------------------------------------------------ #

    def start_run(self, recipe_name: str = "", recipe_dict: Optional[dict] = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO runs (started_at, recipe_name, recipe_json, status) VALUES (?,?,?,?)",
            (time.time(), recipe_name, json.dumps(recipe_dict) if recipe_dict else None, "RUNNING")
        )
        self._conn.commit()
        self._run_id = cur.lastrowid
        logger.info(f"Run started: id={self._run_id}")
        return self._run_id

    def end_run(self, status: str = "FINISHED", notes: str = ""):
        if self._run_id is None:
            return
        self._conn.execute(
            "UPDATE runs SET ended_at=?, status=?, notes=? WHERE id=?",
            (time.time(), status, notes, self._run_id)
        )
        self._conn.commit()
        logger.info(f"Run ended: id={self._run_id} status={status}")
        self._run_id = None

    # ------------------------------------------------------------------ #
    # Reading ingestion                                                    #
    # ------------------------------------------------------------------ #

    def log_reading(self, reading: DeviceReading):
        if self._run_id is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO readings (run_id, timestamp, device_id, control, value) VALUES (?,?,?,?,?)",
                (self._run_id, reading.timestamp, reading.device_id, reading.control, float(reading.value))
            )
            # Batch commit every N rows to avoid constant fsync
            self._conn.commit()
        except Exception as e:
            logger.warning(f"Log reading error: {e}")

    # ------------------------------------------------------------------ #
    # Query / export                                                       #
    # ------------------------------------------------------------------ #

    def get_runs(self, limit: int = 50) -> list[dict]:
        cur = self._conn.execute(
            "SELECT id, started_at, ended_at, recipe_name, status, notes "
            "FROM runs ORDER BY started_at DESC LIMIT ?",
            (limit,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_readings(self, run_id: int,
                     device_id: Optional[str] = None,
                     control: Optional[str] = None) -> list[dict]:
        query = "SELECT timestamp, device_id, control, value FROM readings WHERE run_id=?"
        params: list = [run_id]
        if device_id:
            query += " AND device_id=?"
            params.append(device_id)
        if control:
            query += " AND control=?"
            params.append(control)
        query += " ORDER BY timestamp"
        cur = self._conn.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def export_csv(self, run_id: int, output_dir: str | Path = "data") -> Path:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rows = self.get_readings(run_id)
        out = output_dir / f"run_{run_id}.csv"
        with open(out, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "device_id", "control", "value"])
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"CSV exported: {out}")
        return out
