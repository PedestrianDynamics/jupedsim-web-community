"""Local HTTP bridge for validating and opening JuPedSim Web scenario files.

Run:
    python3 bridge_server.py --port 8090

Validate, publish, and open a pair of files in a connected viewer:
    curl -X POST http://127.0.0.1:8090/api/validate \
      -F "config=@config.json" \
      -F "geometry=@geometry.wkt"

LLM clients may also send JSON:
    {
      "config": { ... contents of config.json ... },
      "geometry_wkt": "POLYGON ((...))"
    }

Queue a simulation in the connected viewer:
    curl -X POST http://127.0.0.1:8090/api/simulations/run

Queue a scene clear in the connected viewer:
    curl -X POST http://127.0.0.1:8090/api/scenarios/clear
"""

from __future__ import annotations

import argparse
import os
import copy
import hashlib
import io
import json
import math
import re
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit


MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_UI_STATE_ZIP_ENTRIES = 16
MAX_UI_STATE_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
MAX_RESULT_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_RESULT_ARCHIVE_ENTRIES = 512
MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_SQLITE_QUERY_LIMIT = 1000
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
POLYGON_SECTIONS = ("distributions", "exits", "checkpoints", "zones")
JOURNEY_COLORS = (
    "#10b981",
    "#f97316",
    "#3b82f6",
    "#a855f7",
    "#facc15",
    "#ef4444",
    "#22d3ee",
    "#84cc16",
    "#ec4899",
    "#0ea5e9",
    "#f59e0b",
    "#14b8a6",
)
WKT_TOKEN = re.compile(
    r"\s*(?:(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<word>[A-Za-z_]+)|(?P<symbol>[(),]))"
)


class ScenarioStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def publish(self, config_text: str, geometry_text: str) -> dict[str, str]:
        scenario_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("config.json", config_text)
            archive.writestr("geometry.wkt", geometry_text)

        metadata = {
            "id": scenario_id,
            "bundle_url": f"/api/scenarios/{scenario_id}/bundle",
            "filename": f"jupedsim-bridge-{scenario_id}.zip",
        }
        with self._lock:
            self._latest = {"metadata": metadata, "bundle": archive_buffer.getvalue()}
        return metadata.copy()

    def latest_after(self, scenario_id: str | None) -> dict[str, str] | None:
        with self._lock:
            if self._latest is None or self._latest["metadata"]["id"] == scenario_id:
                return None
            return self._latest["metadata"].copy()

    def bundle(self, scenario_id: str) -> tuple[dict[str, str], bytes] | None:
        with self._lock:
            if self._latest is None or self._latest["metadata"]["id"] != scenario_id:
                return None
            return self._latest["metadata"].copy(), self._latest["bundle"]

    def clear(self) -> None:
        with self._lock:
            self._latest = None


SCENARIOS = ScenarioStore()


class UiStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def publish(self, config: dict[str, Any], geometry_text: str) -> dict[str, Any]:
        config_text = json.dumps(config, indent=2, sort_keys=True)
        digest = hashlib.sha256(
            config_text.encode("utf-8") + b"\0" + geometry_text.encode("utf-8")
        ).hexdigest()
        captured_at = int(time.time() * 1000)
        with self._lock:
            changed = self._latest is None or self._latest["digest"] != digest
            if changed:
                self._latest = {
                    "id": f"{captured_at}-{digest[:8]}",
                    "captured_at": captured_at,
                    "digest": digest,
                    "config": config,
                    "geometry_wkt": geometry_text,
                }
            metadata = self._metadata(self._latest)
        metadata["changed"] = changed
        return metadata

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._latest)

    def clear(self) -> None:
        with self._lock:
            self._latest = None

    @staticmethod
    def _metadata(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": state["id"],
            "captured_at": state["captured_at"],
            "digest": state["digest"],
        }


UI_STATES = UiStateStore()


class SimulationCommandStore:
    ACTIVE_STATUSES = {"queued", "accepted", "running"}
    NEXT_STATUSES = {
        "queued": {"accepted", "failed", "rejected"},
        "accepted": {"running", "failed", "rejected"},
        "running": {"completed", "failed"},
        "completed": set(),
        "failed": set(),
        "rejected": set(),
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def queue(self) -> tuple[dict[str, Any], bool]:
        queued_at = int(time.time() * 1000)
        with self._lock:
            if (
                self._latest is not None
                and self._latest["status"] in self.ACTIVE_STATUSES
            ):
                return copy.deepcopy(self._latest), False
            self._latest = {
                "id": f"{queued_at}-{uuid.uuid4().hex[:8]}",
                "status": "queued",
                "queued_at": queued_at,
                "updated_at": queued_at,
            }
            return copy.deepcopy(self._latest), True

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._latest)

    def latest_after(self, command_id: str | None) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None or self._latest["id"] == command_id:
                return None
            return copy.deepcopy(self._latest)

    def update(
        self,
        command_id: str,
        status: str,
        detail: str | None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None or self._latest["id"] != command_id:
                return None
            current_status = self._latest["status"]
            if status != current_status and status not in self.NEXT_STATUSES[current_status]:
                raise ValueError(
                    f"Simulation status cannot change from {current_status!r} to {status!r}."
                )
            self._latest["status"] = status
            self._latest["updated_at"] = int(time.time() * 1000)
            if detail:
                self._latest["detail"] = detail
            if result is not None:
                self._latest["result"] = copy.deepcopy(result)
            return copy.deepcopy(self._latest)


SIMULATION_COMMANDS = SimulationCommandStore()


class ViewResultsCommandStore:
    ACTIVE_STATUSES = {"queued", "accepted"}
    NEXT_STATUSES = {
        "queued": {"accepted", "completed", "failed", "rejected"},
        "accepted": {"completed", "failed", "rejected"},
        "completed": set(),
        "failed": set(),
        "rejected": set(),
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def queue(self) -> tuple[dict[str, Any], bool]:
        queued_at = int(time.time() * 1000)
        with self._lock:
            if (
                self._latest is not None
                and self._latest["status"] in self.ACTIVE_STATUSES
            ):
                return copy.deepcopy(self._latest), False
            self._latest = {
                "id": f"{queued_at}-{uuid.uuid4().hex[:8]}",
                "status": "queued",
                "queued_at": queued_at,
                "updated_at": queued_at,
            }
            return copy.deepcopy(self._latest), True

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._latest)

    def latest_after(self, command_id: str | None) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None or self._latest["id"] == command_id:
                return None
            return copy.deepcopy(self._latest)

    def update(
        self,
        command_id: str,
        status: str,
        detail: str | None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None or self._latest["id"] != command_id:
                return None
            current_status = self._latest["status"]
            if status != current_status and status not in self.NEXT_STATUSES[current_status]:
                raise ValueError(
                    f"View-results status cannot change from {current_status!r} to {status!r}."
                )
            self._latest["status"] = status
            self._latest["updated_at"] = int(time.time() * 1000)
            if detail:
                self._latest["detail"] = detail
            return copy.deepcopy(self._latest)


VIEW_RESULTS_COMMANDS = ViewResultsCommandStore()


class ClearSceneCommandStore:
    ACTIVE_STATUSES = {"queued", "accepted"}
    NEXT_STATUSES = {
        "queued": {"accepted", "completed", "failed", "rejected"},
        "accepted": {"completed", "failed", "rejected"},
        "completed": set(),
        "failed": set(),
        "rejected": set(),
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def queue(self) -> tuple[dict[str, Any], bool]:
        queued_at = int(time.time() * 1000)
        with self._lock:
            if (
                self._latest is not None
                and self._latest["status"] in self.ACTIVE_STATUSES
            ):
                return copy.deepcopy(self._latest), False
            self._latest = {
                "id": f"{queued_at}-{uuid.uuid4().hex[:8]}",
                "status": "queued",
                "queued_at": queued_at,
                "updated_at": queued_at,
            }
            return copy.deepcopy(self._latest), True

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._latest)

    def latest_after(self, command_id: str | None) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None or self._latest["id"] == command_id:
                return None
            return copy.deepcopy(self._latest)

    def update(
        self,
        command_id: str,
        status: str,
        detail: str | None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None or self._latest["id"] != command_id:
                return None
            current_status = self._latest["status"]
            if status != current_status and status not in self.NEXT_STATUSES[current_status]:
                raise ValueError(
                    f"Clear-scene status cannot change from {current_status!r} to {status!r}."
                )
            self._latest["status"] = status
            self._latest["updated_at"] = int(time.time() * 1000)
            if detail:
                self._latest["detail"] = detail
            if result is not None:
                self._latest["result"] = copy.deepcopy(result)
            return copy.deepcopy(self._latest)


CLEAR_SCENE_COMMANDS = ClearSceneCommandStore()


class SimulationResultStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def publish(
        self,
        result: dict[str, Any],
        simulation_id: str | None = None,
    ) -> dict[str, Any]:
        captured_at = int(time.time() * 1000)
        normalized_result = copy.deepcopy(result)
        result_text = json.dumps(
            normalized_result,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(
            result_text.encode("utf-8")
            + b"\0"
            + (simulation_id or "").encode("utf-8")
        ).hexdigest()
        with self._lock:
            changed = self._latest is None or self._latest["digest"] != digest
            if changed:
                self._latest = {
                    "id": f"{captured_at}-{digest[:8]}",
                    "captured_at": captured_at,
                    "digest": digest,
                    "simulation_id": simulation_id,
                    "result": normalized_result,
                }
            result_record = copy.deepcopy(self._latest)
        result_record["changed"] = changed
        return result_record

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._latest)


SIMULATION_RESULTS = SimulationResultStore()


def json_sqlite_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    return value


def quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


@contextmanager
def sqlite_connection_from_bytes(sqlite_bytes: bytes):
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
    temp_path = temp_file.name
    connection: sqlite3.Connection | None = None
    try:
        temp_file.write(sqlite_bytes)
        temp_file.close()
        connection = sqlite3.connect(f"file:{temp_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        yield connection
    finally:
        if connection is not None:
            connection.close()
        try:
            temp_file.close()
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def read_sqlite_table_rows(
    sqlite_bytes: bytes,
    table_name: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    with sqlite_connection_from_bytes(sqlite_bytes) as connection:
        table = connection.execute(
            "SELECT name, type FROM sqlite_schema "
            "WHERE name = ? AND type IN ('table', 'view')",
            (table_name,),
        ).fetchone()
        if table is None:
            raise KeyError(table_name)
        table_identifier = quote_sqlite_identifier(table_name)
        columns = [
            {
                "name": row["name"],
                "type": row["type"],
                "not_null": bool(row["notnull"]),
                "primary_key": row["pk"],
            }
            for row in connection.execute(f"PRAGMA table_info({table_identifier})")
        ]
        row_count = connection.execute(
            f"SELECT COUNT(*) AS count FROM {table_identifier}"
        ).fetchone()["count"]
        rows = [
            {key: json_sqlite_value(row[key]) for key in row.keys()}
            for row in connection.execute(
                f"SELECT * FROM {table_identifier} LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]
    return {
        "table": table_name,
        "type": table["type"],
        "columns": columns,
        "row_count": row_count,
        "limit": limit,
        "offset": offset,
        "rows": rows,
    }


def inspect_sqlite_bytes(sqlite_bytes: bytes) -> dict[str, Any]:
    with sqlite_connection_from_bytes(sqlite_bytes) as connection:
        schema_rows = connection.execute(
            "SELECT name, type FROM sqlite_schema "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        tables: list[dict[str, Any]] = []
        for schema_row in schema_rows:
            table_name = schema_row["name"]
            table_identifier = quote_sqlite_identifier(table_name)
            columns = [
                {
                    "name": row["name"],
                    "type": row["type"],
                    "not_null": bool(row["notnull"]),
                    "primary_key": row["pk"],
                }
                for row in connection.execute(f"PRAGMA table_info({table_identifier})")
            ]
            try:
                row_count = connection.execute(
                    f"SELECT COUNT(*) AS count FROM {table_identifier}"
                ).fetchone()["count"]
            except sqlite3.Error:
                row_count = None
            try:
                sample_rows = [
                    {key: json_sqlite_value(row[key]) for key in row.keys()}
                    for row in connection.execute(
                        f"SELECT * FROM {table_identifier} LIMIT 5"
                    )
                ]
            except sqlite3.Error:
                sample_rows = []
            tables.append(
                {
                    "name": table_name,
                    "type": schema_row["type"],
                    "columns": columns,
                    "row_count": row_count,
                    "sample_rows": sample_rows,
                }
            )
    return {"tables": tables}


def read_result_archive_zip(
    archive_bytes: bytes,
) -> tuple[list[dict[str, Any]], dict[str, bytes], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    sqlite_payloads: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_RESULT_ARCHIVE_ENTRIES:
                errors.append(
                    issue(
                        "results.zip",
                        "$",
                        "too_many_files",
                        f"Result archive contains more than {MAX_RESULT_ARCHIVE_ENTRIES} files.",
                    )
                )
                return files, sqlite_payloads, errors
            total_size = 0
            for info in infos:
                normalized_name = info.filename.replace("\\", "/")
                if normalized_name.startswith("/") or ".." in normalized_name.split("/"):
                    errors.append(
                        issue(
                            "results.zip",
                            normalized_name,
                            "unsafe_path",
                            "Result archive contains an unsafe file path.",
                        )
                    )
                    continue
                total_size += info.file_size
                if total_size > MAX_RESULT_ARCHIVE_UNCOMPRESSED_BYTES:
                    errors.append(
                        issue(
                            "results.zip",
                            "$",
                            "archive_too_large",
                            "Result archive expands beyond the allowed size.",
                        )
                    )
                    return files, sqlite_payloads, errors
                file_bytes = archive.read(info)
                file_digest = hashlib.sha256(file_bytes).hexdigest()
                file_type = (
                    "sqlite"
                    if normalized_name.lower().endswith(SQLITE_SUFFIXES)
                    else "csv"
                    if normalized_name.lower().endswith(".csv")
                    else "other"
                )
                file_metadata: dict[str, Any] = {
                    "index": len(files),
                    "path": normalized_name,
                    "size": info.file_size,
                    "compressed_size": info.compress_size,
                    "sha256": file_digest,
                    "type": file_type,
                }
                if file_type == "sqlite":
                    sqlite_payloads[normalized_name] = file_bytes
                    try:
                        file_metadata["sqlite"] = inspect_sqlite_bytes(file_bytes)
                    except sqlite3.Error as error:
                        file_metadata["sqlite_error"] = str(error)
                files.append(file_metadata)
    except zipfile.BadZipFile:
        errors.append(
            issue(
                "results.zip",
                "$",
                "invalid_zip",
                "Result archive is not a readable ZIP archive.",
            )
        )
    return files, sqlite_payloads, errors


class SimulationResultArchiveStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None

    def publish(
        self,
        archive_bytes: bytes,
        simulation_id: str | None = None,
    ) -> dict[str, Any]:
        files, sqlite_payloads, errors = read_result_archive_zip(archive_bytes)
        if errors:
            raise ValueError(json.dumps(errors))
        captured_at = int(time.time() * 1000)
        digest = hashlib.sha256(
            archive_bytes + b"\0" + (simulation_id or "").encode("utf-8")
        ).hexdigest()
        sqlite_files = [
            {
                **copy.deepcopy(file_metadata),
                "sqlite_index": sqlite_index,
            }
            for sqlite_index, file_metadata in enumerate(
                file_metadata for file_metadata in files if file_metadata["type"] == "sqlite"
            )
        ]
        with self._lock:
            changed = self._latest is None or self._latest["digest"] != digest
            if changed:
                self._latest = {
                    "id": f"{captured_at}-{digest[:8]}",
                    "captured_at": captured_at,
                    "digest": digest,
                    "simulation_id": simulation_id,
                    "files": files,
                    "sqlite_files": sqlite_files,
                    "archive": archive_bytes,
                    "sqlite_payloads": sqlite_payloads,
                }
            metadata = self._metadata(self._latest)
        metadata["changed"] = changed
        return metadata

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._metadata(self._latest) if self._latest is not None else None

    def table_rows(
        self,
        sqlite_index: int,
        table_name: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._latest is None:
                return None
            sqlite_files = self._latest["sqlite_files"]
            if sqlite_index < 0 or sqlite_index >= len(sqlite_files):
                return None
            sqlite_file = sqlite_files[sqlite_index]
            sqlite_bytes = self._latest["sqlite_payloads"][sqlite_file["path"]]
        return {
            "sqlite_file": {
                "sqlite_index": sqlite_index,
                "path": sqlite_file["path"],
                "size": sqlite_file["size"],
                "sha256": sqlite_file["sha256"],
            },
            **read_sqlite_table_rows(sqlite_bytes, table_name, limit, offset),
        }

    def file_bytes(self, file_index: int) -> tuple[dict[str, Any], bytes] | None:
        with self._lock:
            if self._latest is None:
                return None
            if file_index < 0 or file_index >= len(self._latest["files"]):
                return None
            file_metadata = self._latest["files"][file_index]
            with zipfile.ZipFile(io.BytesIO(self._latest["archive"])) as archive:
                return copy.deepcopy(file_metadata), archive.read(file_metadata["path"])

    @staticmethod
    def _metadata(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": state["id"],
            "captured_at": state["captured_at"],
            "digest": state["digest"],
            "simulation_id": state["simulation_id"],
            "files": copy.deepcopy(state["files"]),
            "sqlite_files": copy.deepcopy(state["sqlite_files"]),
        }


SIMULATION_RESULT_ARCHIVES = SimulationResultArchiveStore()


try:
    from shapely import wkt as shapely_wkt
    from shapely.validation import explain_validity as explain_shapely_validity
except ImportError:
    shapely_wkt = None
    explain_shapely_validity = None


def issue(file: str, path: str, code: str, message: str) -> dict[str, str]:
    return {"file": file, "path": path, "code": code, "message": message}


def is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def same_point(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return math.isclose(left[0], right[0]) and math.isclose(left[1], right[1])


def signed_area(points: list[tuple[float, float]]) -> float:
    return sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:])
    ) / 2


def orientation(
    start: tuple[float, float],
    middle: tuple[float, float],
    end: tuple[float, float],
) -> float:
    return (middle[1] - start[1]) * (end[0] - middle[0]) - (
        middle[0] - start[0]
    ) * (end[1] - middle[1])


def point_on_segment(
    start: tuple[float, float],
    point: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    return (
        min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
        and min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
    )


def segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    first_orientation = orientation(first_start, first_end, second_start)
    second_orientation = orientation(first_start, first_end, second_end)
    third_orientation = orientation(second_start, second_end, first_start)
    fourth_orientation = orientation(second_start, second_end, first_end)

    if (
        (first_orientation > 0 > second_orientation or first_orientation < 0 < second_orientation)
        and (third_orientation > 0 > fourth_orientation or third_orientation < 0 < fourth_orientation)
    ):
        return True

    return (
        math.isclose(first_orientation, 0) and point_on_segment(first_start, second_start, first_end)
    ) or (
        math.isclose(second_orientation, 0) and point_on_segment(first_start, second_end, first_end)
    ) or (
        math.isclose(third_orientation, 0) and point_on_segment(second_start, first_start, second_end)
    ) or (
        math.isclose(fourth_orientation, 0) and point_on_segment(second_start, first_end, second_end)
    )


def ring_error(points: list[tuple[float, float]]) -> str | None:
    if len(points) < 4:
        return "A polygon ring needs at least four points including its closing point."
    if not same_point(points[0], points[-1]):
        return "The first and last points of a polygon ring must be identical."
    if len(set(points[:-1])) < 3:
        return "A polygon ring needs at least three distinct points."
    if math.isclose(signed_area(points), 0):
        return "A polygon ring must enclose a non-zero area."

    segment_count = len(points) - 1
    for first_index in range(segment_count):
        for second_index in range(first_index + 1, segment_count):
            adjacent = second_index == first_index + 1
            closing_pair = first_index == 0 and second_index == segment_count - 1
            if adjacent or closing_pair:
                continue
            if segments_intersect(
                points[first_index],
                points[first_index + 1],
                points[second_index],
                points[second_index + 1],
            ):
                return (
                    "A polygon ring must not intersect itself "
                    f"(segments {first_index} and {second_index})."
                )
    return None


class WktSyntaxError(ValueError):
    pass


class WktParser:
    def __init__(self, text: str):
        self.tokens = self._tokenize(text)
        self.position = 0

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens: list[str] = []
        position = 0
        while position < len(text):
            match = WKT_TOKEN.match(text, position)
            if not match:
                if text[position:].strip() == "":
                    break
                raise WktSyntaxError(f"Unexpected token at character {position + 1}.")
            tokens.append(match.group("number") or match.group("word") or match.group("symbol"))
            position = match.end()
        return tokens

    def peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        token = self.peek()
        if token is None:
            raise WktSyntaxError("Unexpected end of geometry.")
        if expected is not None and token.upper() != expected.upper():
            raise WktSyntaxError(f"Expected '{expected}' but received '{token}'.")
        self.position += 1
        return token

    def take_number(self) -> float:
        token = self.take()
        try:
            value = float(token)
        except ValueError as error:
            raise WktSyntaxError(f"Expected a coordinate but received '{token}'.") from error
        if not math.isfinite(value):
            raise WktSyntaxError("Coordinates must be finite numbers.")
        return value

    def parse_ring(self) -> list[tuple[float, float]]:
        self.take("(")
        points: list[tuple[float, float]] = []
        while True:
            points.append((self.take_number(), self.take_number()))
            if self.peek() == ",":
                self.take(",")
                continue
            self.take(")")
            return points

    def parse_polygon(self) -> list[list[tuple[float, float]]]:
        self.take("(")
        rings = [self.parse_ring()]
        while self.peek() == ",":
            self.take(",")
            rings.append(self.parse_ring())
        self.take(")")
        return rings

    def parse(self) -> list[list[list[tuple[float, float]]]]:
        geometry_type = self.take().upper()
        if geometry_type == "POLYGON":
            polygons = [self.parse_polygon()]
        elif geometry_type == "MULTIPOLYGON":
            self.take("(")
            polygons = [self.parse_polygon()]
            while self.peek() == ",":
                self.take(",")
                polygons.append(self.parse_polygon())
            self.take(")")
        else:
            raise WktSyntaxError("geometry.wkt must contain a POLYGON or MULTIPOLYGON.")
        if self.peek() is not None:
            raise WktSyntaxError(f"Unexpected trailing token '{self.peek()}'.")
        return polygons


def validate_wkt(text: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(text, str) or not text.strip():
        errors.append(
            issue("geometry.wkt", "$", "missing_geometry", "geometry.wkt must not be empty.")
        )
        return

    try:
        polygons = WktParser(text.strip()).parse()
    except WktSyntaxError as error:
        errors.append(issue("geometry.wkt", "$", "invalid_wkt", str(error)))
        return

    for polygon_index, polygon in enumerate(polygons):
        for ring_index, ring in enumerate(polygon):
            message = ring_error(ring)
            if message:
                errors.append(
                    issue(
                        "geometry.wkt",
                        f"$.polygons[{polygon_index}].rings[{ring_index}]",
                        "invalid_polygon_ring",
                        message,
                    )
                )

    if shapely_wkt is None:
        return

    try:
        geometry = shapely_wkt.loads(text)
    except Exception as error:
        errors.append(issue("geometry.wkt", "$", "invalid_wkt", str(error)))
        return
    if geometry.is_empty:
        errors.append(issue("geometry.wkt", "$", "empty_geometry", "geometry.wkt must not be empty."))
    elif not geometry.is_valid:
        explanation = explain_shapely_validity(geometry) if explain_shapely_validity else "Invalid geometry."
        errors.append(issue("geometry.wkt", "$", "invalid_geometry", explanation))


def validate_polygon_coordinates(
    coordinates: Any,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(coordinates, list):
        errors.append(
            issue("config.json", path, "invalid_coordinates", "coordinates must be an array.")
        )
        return

    points: list[tuple[float, float]] = []
    for index, point in enumerate(coordinates):
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not all(is_number(coordinate) for coordinate in point)
        ):
            errors.append(
                issue(
                    "config.json",
                    f"{path}[{index}]",
                    "invalid_coordinate",
                    "Each polygon coordinate must be an array of two finite numbers.",
                )
            )
            return
        points.append((float(point[0]), float(point[1])))

    message = ring_error(points)
    if message:
        errors.append(issue("config.json", path, "invalid_polygon_ring", message))


def validate_entity_sections(config: dict[str, Any], errors: list[dict[str, str]]) -> set[str]:
    entity_ids: set[str] = set()
    for section in POLYGON_SECTIONS:
        entities = config.get(section, {})
        if not isinstance(entities, dict):
            errors.append(
                issue("config.json", f"$.{section}", "invalid_section", f"{section} must be an object.")
            )
            continue
        for entity_id, entity in entities.items():
            path = f"$.{section}.{entity_id}"
            entity_ids.add(entity_id)
            if not isinstance(entity_id, str) or not entity_id:
                errors.append(issue("config.json", path, "invalid_id", "Entity IDs must be non-empty strings."))
                continue
            if not isinstance(entity, dict):
                errors.append(issue("config.json", path, "invalid_entity", "Each entity must be an object."))
                continue
            if entity.get("type", "polygon") != "polygon":
                errors.append(
                    issue("config.json", f"{path}.type", "unsupported_type", "Only polygon entities are supported.")
                )
            validate_polygon_coordinates(entity.get("coordinates"), f"{path}.coordinates", errors)
    return entity_ids


def validate_settings(config: dict[str, Any], errors: list[dict[str, str]]) -> None:
    app_config = config.get("config")
    if not isinstance(app_config, dict):
        errors.append(issue("config.json", "$.config", "missing_config", "config must be an object."))
        return
    settings = app_config.get("simulation_settings")
    if not isinstance(settings, dict):
        errors.append(
            issue(
                "config.json",
                "$.config.simulation_settings",
                "missing_settings",
                "simulation_settings must be an object.",
            )
        )
        return
    seed = settings.get("baseSeed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        errors.append(
            issue("config.json", "$.config.simulation_settings.baseSeed", "invalid_seed", "baseSeed must be an integer.")
        )
    parameters = settings.get("simulationParams")
    if not isinstance(parameters, dict):
        errors.append(
            issue(
                "config.json",
                "$.config.simulation_settings.simulationParams",
                "missing_parameters",
                "simulationParams must be an object.",
            )
        )
        return
    model_type = parameters.get("model_type")
    if not isinstance(model_type, str) or not model_type:
        errors.append(
            issue(
                "config.json",
                "$.config.simulation_settings.simulationParams.model_type",
                "invalid_model_type",
                "model_type must be a non-empty string.",
            )
        )
    maximum_time = parameters.get("max_simulation_time")
    if not is_number(maximum_time) or maximum_time <= 0:
        errors.append(
            issue(
                "config.json",
                "$.config.simulation_settings.simulationParams.max_simulation_time",
                "invalid_maximum_time",
                "max_simulation_time must be a positive finite number.",
            )
        )


def validate_journeys(
    config: dict[str, Any],
    entity_ids: set[str],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    journey_ids: set[str] = set()
    journey_count = 0
    for section, stages_key in (("journeys", "stages"), ("journeys_v2", "sequence")):
        journeys = config.get(section, [])
        if not isinstance(journeys, list):
            errors.append(issue("config.json", f"$.{section}", "invalid_section", f"{section} must be an array."))
            continue
        journey_count += len(journeys)
        for index, journey in enumerate(journeys):
            path = f"$.{section}[{index}]"
            if not isinstance(journey, dict):
                errors.append(issue("config.json", path, "invalid_journey", "Each journey must be an object."))
                continue
            journey_id = journey.get("id")
            if not isinstance(journey_id, str) or not journey_id:
                errors.append(issue("config.json", f"{path}.id", "invalid_id", "Journey IDs must be non-empty strings."))
            else:
                journey_ids.add(journey_id)
            stages = journey.get(stages_key)
            if not isinstance(stages, list) or not stages:
                errors.append(
                    issue("config.json", f"{path}.{stages_key}", "invalid_stages", f"{stages_key} must be a non-empty array.")
                )
                continue
            for stage_index, stage_id in enumerate(stages):
                stage_path = f"{path}.{stages_key}[{stage_index}]"
                if not isinstance(stage_id, str) or not stage_id:
                    errors.append(
                        issue(
                            "config.json",
                            stage_path,
                            "invalid_stage",
                            "Stage IDs must be non-empty strings.",
                        )
                    )
                elif stage_id not in entity_ids:
                    errors.append(
                        issue(
                            "config.json",
                            stage_path,
                            "unknown_stage",
                            f"Unknown stage ID '{stage_id}'.",
                        )
                    )

    if not journey_count:
        warnings.append(
            issue(
                "config.json",
                "$",
                "missing_journeys",
                "No journeys or journeys_v2 entries are defined.",
            )
        )

    distributions = config.get("distributions", {})
    if not isinstance(distributions, dict):
        return
    for distribution_id, distribution in distributions.items():
        if not isinstance(distribution, dict):
            continue
        weights = distribution.get("journey_weights", [])
        if not isinstance(weights, list):
            errors.append(
                issue(
                    "config.json",
                    f"$.distributions.{distribution_id}.journey_weights",
                    "invalid_journey_weights",
                    "journey_weights must be an array.",
                )
            )
            continue
        for index, weight in enumerate(weights):
            path = f"$.distributions.{distribution_id}.journey_weights[{index}]"
            if not isinstance(weight, dict):
                errors.append(issue("config.json", path, "invalid_journey_weight", "Each journey weight must be an object."))
                continue
            journey_id = weight.get("journey_id")
            if not isinstance(journey_id, str) or not journey_id:
                errors.append(
                    issue(
                        "config.json",
                        f"{path}.journey_id",
                        "invalid_journey",
                        "journey_id must be a non-empty string.",
                    )
                )
            elif journey_id not in journey_ids:
                errors.append(
                    issue(
                        "config.json",
                        f"{path}.journey_id",
                        "unknown_journey",
                        f"Unknown journey ID '{journey_id}'.",
                    )
                )
            weight_value = weight.get("weight")
            if not is_number(weight_value) or weight_value < 0:
                errors.append(
                    issue(
                        "config.json",
                        f"{path}.weight",
                        "invalid_weight",
                        "weight must be a non-negative finite number.",
                    )
                )


def validate_config(config: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not isinstance(config, dict):
        return [issue("config.json", "$", "invalid_root", "config.json must contain a JSON object.")], warnings

    validate_settings(config, errors)
    entity_ids = validate_entity_sections(config, errors)
    validate_journeys(config, entity_ids, errors, warnings)

    for section in ("distributions", "exits"):
        entities = config.get(section)
        if isinstance(entities, dict) and not entities:
            warnings.append(
                issue(
                    "config.json",
                    f"$.{section}",
                    f"missing_{section}",
                    f"No {section} are defined.",
                )
            )
    return errors, warnings


def parse_json_text(text: str) -> tuple[Any | None, list[dict[str, str]]]:
    try:
        return json.loads(text), []
    except json.JSONDecodeError as error:
        return None, [
            issue(
                "config.json",
                "$",
                "invalid_json",
                f"{error.msg} at line {error.lineno}, column {error.colno}.",
            )
        ]


def upgrade_legacy_routing(
    config_text: str,
) -> tuple[str, dict[str, Any], list[dict[str, str]]]:
    config = json.loads(config_text)
    journeys_v2 = config.get("journeys_v2")
    if isinstance(journeys_v2, list) and journeys_v2:
        return config_text, {"schema": "journeys_v2", "converted_from_legacy": False}, []

    journeys = config.get("journeys")
    if not isinstance(journeys, list) or not journeys:
        return config_text, {"schema": "none", "converted_from_legacy": False}, []

    upgraded = copy.deepcopy(config)
    distributions = upgraded.get("distributions", {})
    new_journeys: list[dict[str, Any]] = []
    journeys_by_origin: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []

    for index, journey in enumerate(journeys):
        path = f"$.journeys[{index}]"
        journey_id = journey["id"]
        stages = journey["stages"]
        origin = stages[0]
        sequence = stages[1:]
        if origin not in distributions:
            errors.append(
                issue(
                    "config.json",
                    f"{path}.stages[0]",
                    "legacy_route_missing_start_area",
                    "A legacy journey must begin with a distribution before it can be converted.",
                )
            )
            continue
        if not sequence:
            errors.append(
                issue(
                    "config.json",
                    f"{path}.stages",
                    "legacy_route_missing_sequence",
                    "A legacy journey needs at least one stage after its distribution.",
                )
            )
            continue

        new_journeys.append(
            {
                "id": journey_id,
                "name": journey_id,
                "color": JOURNEY_COLORS[index % len(JOURNEY_COLORS)],
                "sequence": sequence,
            }
        )
        journeys_by_origin.setdefault(origin, []).append(journey_id)

    for origin, journey_ids in journeys_by_origin.items():
        distribution = distributions[origin]
        weights = distribution.get("journey_weights")
        if weights:
            continue
        if len(journey_ids) > 1:
            errors.append(
                issue(
                    "config.json",
                    f"$.distributions.{origin}.journey_weights",
                    "ambiguous_legacy_route_weights",
                    "This start area has multiple legacy routes. Supply journey_weights explicitly.",
                )
            )
            continue
        distribution["journey_weights"] = [{"journey_id": journey_ids[0], "weight": 100}]

    if errors:
        return config_text, {"schema": "legacy", "converted_from_legacy": False}, errors

    upgraded["journeys_v2"] = new_journeys
    return (
        json.dumps(upgraded, indent=2),
        {"schema": "journeys_v2", "converted_from_legacy": True},
        [],
    )


def parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\n"
        b"MIME-Version: 1.0\r\n\r\n" + body
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message.iter_parts()
        if part.get_param("name", header="content-disposition")
    }


def decode_utf8(value: bytes, filename: str) -> tuple[str | None, list[dict[str, str]]]:
    try:
        return value.decode("utf-8"), []
    except UnicodeDecodeError as error:
        return None, [
            issue(filename, "$", "invalid_encoding", f"{filename} must use UTF-8 encoding: {error}.")
        ]


def read_ui_state_zip(body: bytes) -> tuple[dict[str, Any] | None, str | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = [entry for entry in archive.infolist() if not entry.is_dir()]
            if len(entries) > MAX_UI_STATE_ZIP_ENTRIES:
                return None, None, [
                    issue(
                        "request",
                        "$",
                        "too_many_zip_entries",
                        f"UI state ZIP contains more than {MAX_UI_STATE_ZIP_ENTRIES} files.",
                    )
                ]
            if sum(entry.file_size for entry in entries) > MAX_UI_STATE_UNCOMPRESSED_BYTES:
                return None, None, [
                    issue(
                        "request",
                        "$",
                        "zip_too_large",
                        "UI state ZIP expands beyond the supported size.",
                    )
                ]
            by_name = {entry.filename.rsplit("/", 1)[-1].lower(): entry for entry in entries}
            config_entry = by_name.get("config.json")
            geometry_entry = by_name.get("geometry.wkt")
            if config_entry is None or geometry_entry is None:
                return None, None, [
                    issue(
                        "request",
                        "$",
                        "missing_project_files",
                        "UI state ZIP must contain config.json and geometry.wkt.",
                    )
                ]
            config_text, config_decode_errors = decode_utf8(archive.read(config_entry), "config.json")
            geometry_text, geometry_decode_errors = decode_utf8(archive.read(geometry_entry), "geometry.wkt")
            errors.extend(config_decode_errors)
            errors.extend(geometry_decode_errors)
    except zipfile.BadZipFile:
        return None, None, [issue("request", "$", "invalid_zip", "UI state body must be a ZIP file.")]

    if errors or config_text is None or geometry_text is None:
        return None, None, errors

    config, json_errors = parse_json_text(config_text)
    errors.extend(json_errors)
    if not json_errors:
        config_errors, _warnings = validate_config(config)
        errors.extend(config_errors)
    validate_wkt(geometry_text, errors)
    return (config if isinstance(config, dict) else None), geometry_text, errors


def read_scenario_files(content_type: str, body: bytes) -> tuple[str | None, str | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    if content_type.startswith("application/json"):
        request_json, json_errors = parse_json_text(body.decode("utf-8", errors="replace"))
        if json_errors:
            return None, None, [
                issue("request", item["path"], item["code"], item["message"])
                for item in json_errors
            ]
        if not isinstance(request_json, dict):
            return None, None, [
                issue("request", "$", "invalid_request", "The request body must be a JSON object.")
            ]
        missing = object()
        config_value = request_json.get("config", request_json.get("config_json", missing))
        geometry_value = request_json.get("geometry_wkt", request_json.get("geometry"))
        if isinstance(config_value, str):
            config_text = config_value
        elif config_value is not missing:
            config_text = json.dumps(config_value)
        else:
            config_text = None
        geometry_text = geometry_value if isinstance(geometry_value, str) else None
    elif content_type.startswith("multipart/form-data"):
        try:
            fields = parse_multipart(content_type, body)
        except (UnicodeEncodeError, ValueError) as error:
            return None, None, [
                issue("request", "$", "invalid_multipart", f"Invalid multipart request: {error}.")
            ]
        config_bytes = fields.get("config") or fields.get("config.json")
        geometry_bytes = fields.get("geometry") or fields.get("geometry_wkt") or fields.get("geometry.wkt")
        config_text = None
        geometry_text = None
        if config_bytes is not None:
            config_text, decode_errors = decode_utf8(config_bytes, "config.json")
            errors.extend(decode_errors)
        if geometry_bytes is not None:
            geometry_text, decode_errors = decode_utf8(geometry_bytes, "geometry.wkt")
            errors.extend(decode_errors)
    else:
        return None, None, [
            issue(
                "request",
                "$",
                "unsupported_content_type",
                "Use application/json or multipart/form-data.",
            )
        ]

    if config_text is None:
        errors.append(issue("config.json", "$", "missing_file", "Send config.json as the 'config' field."))
    if geometry_text is None:
        errors.append(issue("geometry.wkt", "$", "missing_file", "Send geometry.wkt as the 'geometry' file field or the 'geometry_wkt' JSON field."))
    return config_text, geometry_text, errors


def validate_request(
    content_type: str,
    body: bytes,
) -> tuple[HTTPStatus, dict[str, Any], str | None, str | None]:
    config_text, geometry_text, errors = read_scenario_files(content_type, body)
    warnings: list[dict[str, str]] = []
    if config_text is not None:
        config, json_errors = parse_json_text(config_text)
        errors.extend(json_errors)
        if not json_errors:
            config_errors, config_warnings = validate_config(config)
            errors.extend(config_errors)
            warnings.extend(config_warnings)
    if geometry_text is not None:
        validate_wkt(geometry_text, errors)

    response = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "validator": "stdlib+shapely" if shapely_wkt else "stdlib",
    }
    return (
        HTTPStatus.OK if not errors else HTTPStatus.UNPROCESSABLE_ENTITY,
        response,
        config_text,
        geometry_text,
    )


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "JuPedSimHttpBridge/0.4"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self._send_cors_header()
        self.end_headers()

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        path = url.path
        if path in ("/", "/api/health"):
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "jupedsim-http-bridge",
                    "validator": "stdlib+shapely" if shapely_wkt else "stdlib",
                    "endpoints": {
                        "validate_and_publish": "POST /api/validate",
                        "latest_scenario": "GET /api/scenarios/latest",
                        "publish_ui_state": "POST /api/ui-state",
                        "latest_ui_state": "GET /api/ui-state/latest",
                        "clear_scene": "POST /api/scenarios/clear",
                        "latest_clear_scene": "GET /api/scenarios/clear/latest",
                        "run_simulation": "POST /api/simulations/run",
                        "latest_simulation": "GET /api/simulations/latest",
                        "publish_results": "POST /api/results",
                        "latest_results": "GET /api/results/latest",
                        "view_results": "POST /api/results/view",
                        "latest_view_results": "GET /api/results/view/latest",
                        "publish_results_archive": "POST /api/results/archive",
                        "latest_results_archive": "GET /api/results/latest/archive",
                        "latest_sqlite_files": "GET /api/results/latest/sqlite",
                        "sqlite_table_rows": "GET /api/results/latest/sqlite/{index}/tables/{table}?limit=100&offset=0",
                    },
                },
            )
            return
        if path == "/api/ui-state/latest":
            self.send_json(HTTPStatus.OK, {"ok": True, "ui_state": UI_STATES.latest()})
            return
        if path == "/api/simulations/latest":
            after = (parse_qs(url.query).get("after") or [None])[0]
            simulation = (
                SIMULATION_COMMANDS.latest_after(after)
                if after is not None
                else SIMULATION_COMMANDS.latest()
            )
            self.send_json(HTTPStatus.OK, {"ok": True, "simulation": simulation})
            return
        if path == "/api/scenarios/clear/latest":
            after = (parse_qs(url.query).get("after") or [None])[0]
            clear_scene = (
                CLEAR_SCENE_COMMANDS.latest_after(after)
                if after is not None
                else CLEAR_SCENE_COMMANDS.latest()
            )
            self.send_json(HTTPStatus.OK, {"ok": True, "clear_scene": clear_scene})
            return
        if path == "/api/results/latest":
            self.send_json(HTTPStatus.OK, {"ok": True, "results": SIMULATION_RESULTS.latest()})
            return
        if path == "/api/results/view/latest":
            after = (parse_qs(url.query).get("after") or [None])[0]
            view_results = (
                VIEW_RESULTS_COMMANDS.latest_after(after)
                if after is not None
                else VIEW_RESULTS_COMMANDS.latest()
            )
            self.send_json(HTTPStatus.OK, {"ok": True, "view_results": view_results})
            return
        if path == "/api/results/latest/archive":
            self.send_json(
                HTTPStatus.OK,
                {"ok": True, "archive": SIMULATION_RESULT_ARCHIVES.latest()},
            )
            return
        if path == "/api/results/latest/sqlite":
            archive = SIMULATION_RESULT_ARCHIVES.latest()
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "archive_id": archive["id"] if archive else None,
                    "simulation_id": archive["simulation_id"] if archive else None,
                    "sqlite_files": archive["sqlite_files"] if archive else [],
                },
            )
            return
        table_match = re.fullmatch(
            r"/api/results/latest/sqlite/(\d+)/tables/(.+)",
            path,
        )
        if table_match:
            query = parse_qs(url.query)
            try:
                limit = min(
                    MAX_SQLITE_QUERY_LIMIT,
                    max(1, int((query.get("limit") or ["100"])[0])),
                )
                offset = max(0, int((query.get("offset") or ["0"])[0]))
            except ValueError:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": "limit and offset must be integers."},
                )
                return
            try:
                rows = SIMULATION_RESULT_ARCHIVES.table_rows(
                    int(table_match.group(1)),
                    unquote(table_match.group(2)),
                    limit,
                    offset,
                )
            except KeyError:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "SQLite table not found."},
                )
                return
            except sqlite3.Error as error:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": str(error)},
                )
                return
            if rows is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "SQLite result file not found."},
                )
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "sqlite": rows})
            return
        file_match = re.fullmatch(r"/api/results/latest/files/(\d+)", path)
        if file_match:
            file_record = SIMULATION_RESULT_ARCHIVES.file_bytes(int(file_match.group(1)))
            if file_record is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "Result file not found."},
                )
                return
            metadata, file_bytes = file_record
            content_type = (
                "application/vnd.sqlite3"
                if metadata["type"] == "sqlite"
                else "text/csv; charset=utf-8"
                if metadata["type"] == "csv"
                else "application/octet-stream"
            )
            filename = os.path.basename(metadata["path"]).replace('"', "")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(file_bytes)))
            self.send_header("Cache-Control", "no-store")
            self._send_cors_header()
            self.end_headers()
            self.wfile.write(file_bytes)
            return
        if path == "/api/scenarios/latest":
            after = (parse_qs(url.query).get("after") or [None])[0]
            self.send_json(
                HTTPStatus.OK,
                {"ok": True, "scenario": SCENARIOS.latest_after(after)},
            )
            return
        bundle_match = re.fullmatch(r"/api/scenarios/([^/]+)/bundle", path)
        if bundle_match:
            scenario = SCENARIOS.bundle(bundle_match.group(1))
            if scenario is None:
                self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Scenario bundle not found."})
                return
            metadata, bundle = scenario
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{metadata["filename"]}"')
            self.send_header("Content-Length", str(len(bundle)))
            self.send_header("Cache-Control", "no-store")
            self._send_cors_header()
            self.end_headers()
            self.wfile.write(bundle)
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        path = url.path
        if path == "/api/simulations/run":
            simulation, created = SIMULATION_COMMANDS.queue()
            self.send_json(
                HTTPStatus.ACCEPTED if created else HTTPStatus.CONFLICT,
                {
                    "ok": created,
                    "simulation": simulation,
                    "error": None if created else "A simulation request is already active.",
                },
            )
            return

        if path == "/api/results/view":
            view_results, created = VIEW_RESULTS_COMMANDS.queue()
            self.send_json(
                HTTPStatus.ACCEPTED if created else HTTPStatus.CONFLICT,
                {
                    "ok": created,
                    "view_results": view_results,
                    "error": None if created else "A view-results request is already active.",
                },
            )
            return

        if path == "/api/scenarios/clear":
            clear_scene, created = CLEAR_SCENE_COMMANDS.queue()
            if created:
                SCENARIOS.clear()
                UI_STATES.clear()
            self.send_json(
                HTTPStatus.ACCEPTED if created else HTTPStatus.CONFLICT,
                {
                    "ok": created,
                    "clear_scene": clear_scene,
                    "error": None if created else "A clear-scene request is already active.",
                },
            )
            return

        simulation_status_match = re.fullmatch(
            r"/api/simulations/([^/]+)/status",
            path,
        )
        view_results_status_match = re.fullmatch(
            r"/api/results/view/([^/]+)/status",
            path,
        )
        clear_scene_status_match = re.fullmatch(
            r"/api/scenarios/clear/([^/]+)/status",
            path,
        )
        if path not in (
            "/validate",
            "/api/validate",
            "/api/ui-state",
            "/api/results",
            "/api/results/archive",
        ) and not simulation_status_match and not view_results_status_match and not clear_scene_status_match:
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found."})
            return

        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self.send_json(HTTPStatus.LENGTH_REQUIRED, {"ok": False, "error": "Content-Length is required."})
            return
        try:
            body_size = int(content_length)
        except ValueError:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Content-Length."})
            return
        if body_size < 0:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid Content-Length."})
            return
        max_body_bytes = (
            MAX_RESULT_ARCHIVE_BYTES
            if path == "/api/results/archive"
            else MAX_BODY_BYTES
        )
        if body_size > max_body_bytes:
            self.send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": f"Request body exceeds {max_body_bytes} bytes."},
            )
            return

        body = self.rfile.read(body_size)
        if clear_scene_status_match:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"ok": False, "error": "Use application/json for clear-scene status updates."},
                )
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
                return
            status = payload.get("status") if isinstance(payload, dict) else None
            detail = payload.get("detail") if isinstance(payload, dict) else None
            result = payload.get("result") if isinstance(payload, dict) else None
            if status not in ClearSceneCommandStore.NEXT_STATUSES:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Unknown clear-scene status."},
                )
                return
            if detail is not None and not isinstance(detail, str):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Clear-scene status detail must be a string."},
                )
                return
            if result is not None and not isinstance(result, dict):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Clear-scene result must be an object."},
                )
                return
            try:
                clear_scene = CLEAR_SCENE_COMMANDS.update(
                    clear_scene_status_match.group(1),
                    status,
                    detail,
                    result,
                )
            except ValueError as error:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": str(error)},
                )
                return
            if clear_scene is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "Clear-scene request not found."},
                )
                return
            if status == "completed":
                UI_STATES.clear()
                SCENARIOS.clear()
            self.send_json(HTTPStatus.OK, {"ok": True, "clear_scene": clear_scene})
            return

        if view_results_status_match:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"ok": False, "error": "Use application/json for view-results status updates."},
                )
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
                return
            status = payload.get("status") if isinstance(payload, dict) else None
            detail = payload.get("detail") if isinstance(payload, dict) else None
            if status not in ViewResultsCommandStore.NEXT_STATUSES:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Unknown view-results status."},
                )
                return
            if detail is not None and not isinstance(detail, str):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "View-results status detail must be a string."},
                )
                return
            try:
                view_results = VIEW_RESULTS_COMMANDS.update(
                    view_results_status_match.group(1),
                    status,
                    detail,
                )
            except ValueError as error:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": str(error)},
                )
                return
            if view_results is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "View-results request not found."},
                )
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "view_results": view_results})
            return

        if path == "/api/results/archive":
            if not self.headers.get("Content-Type", "").startswith("application/zip"):
                self.send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"ok": False, "error": "Use application/zip for result archives."},
                )
                return
            simulation_id = (parse_qs(url.query).get("simulation_id") or [None])[0]
            try:
                archive = SIMULATION_RESULT_ARCHIVES.publish(body, simulation_id)
            except ValueError as error:
                try:
                    errors = json.loads(str(error))
                except json.JSONDecodeError:
                    errors = [{"message": str(error)}]
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "errors": errors},
                )
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "archive": archive})
            return

        if path == "/api/results":
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"ok": False, "error": "Use application/json for simulation results."},
                )
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
                return
            if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Result payload must include a result object."},
                )
                return
            simulation_id = payload.get("simulation_id")
            if simulation_id is not None and not isinstance(simulation_id, str):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "simulation_id must be a string when provided."},
                )
                return
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "results": SIMULATION_RESULTS.publish(
                        payload["result"],
                        simulation_id,
                    ),
                },
            )
            return

        if simulation_status_match:
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                self.send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"ok": False, "error": "Use application/json for simulation status updates."},
                )
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body."})
                return
            status = payload.get("status") if isinstance(payload, dict) else None
            detail = payload.get("detail") if isinstance(payload, dict) else None
            result = payload.get("result") if isinstance(payload, dict) else None
            if status not in SimulationCommandStore.NEXT_STATUSES:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Unknown simulation status."},
                )
                return
            if detail is not None and not isinstance(detail, str):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Simulation status detail must be a string."},
                )
                return
            if result is not None and not isinstance(result, dict):
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": "Simulation result must be an object."},
                )
                return
            result_record = (
                SIMULATION_RESULTS.publish(result, simulation_status_match.group(1))
                if result is not None
                else None
            )
            try:
                simulation = SIMULATION_COMMANDS.update(
                    simulation_status_match.group(1),
                    status,
                    detail,
                    result_record,
                )
            except ValueError as error:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": str(error)},
                )
                return
            if simulation is None:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": "Simulation request not found."},
                )
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "simulation": simulation})
            return

        if path == "/api/ui-state":
            if not self.headers.get("Content-Type", "").startswith("application/zip"):
                self.send_json(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    {"ok": False, "error": "Use application/zip for UI state snapshots."},
                )
                return
            config, geometry_text, errors = read_ui_state_zip(body)
            if errors or config is None or geometry_text is None:
                self.send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "errors": errors},
                )
                return
            self.send_json(
                HTTPStatus.OK,
                {"ok": True, "ui_state": UI_STATES.publish(config, geometry_text)},
            )
            return

        status, response, config_text, geometry_text = validate_request(
            self.headers.get("Content-Type", ""),
            body,
        )
        if status == HTTPStatus.OK and config_text is not None and geometry_text is not None:
            viewer_config, routing, routing_errors = upgrade_legacy_routing(config_text)
            response["routing"] = routing
            if routing_errors:
                status = HTTPStatus.UNPROCESSABLE_ENTITY
                response["ok"] = False
                response["errors"].extend(routing_errors)
            else:
                response["scenario"] = SCENARIOS.publish(viewer_config, geometry_text)
        self.send_json(status, response)

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_header()
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_header(self) -> None:
        origin = self.headers.get("Origin")
        if origin is None or re.fullmatch(r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?", origin):
            self.send_header("Access-Control-Allow-Origin", origin or "*")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=8090, help="Bind port. Default: 8090")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"JuPedSim HTTP bridge listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
