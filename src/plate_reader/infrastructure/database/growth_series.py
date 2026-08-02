"""Lossless compact storage codec for rectangular plate-reader time series."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from collections.abc import Iterable, Mapping, Sequence

ENCODING = "zlib-f64-matrix-v1"
_TIME_MAGIC = b"PRT1"
_VALUE_MAGIC = b"PRV1"


class GrowthSeriesCodecError(ValueError):
    """Raised when raw observations are inconsistent or a stored chunk is corrupt."""


def encode_growth_series(
    plate_id: str,
    rows: Sequence[Mapping[str, object]],
    well_positions: Mapping[str, str],
) -> tuple[dict[str, object], ...]:
    """Encode observations as one compressed matrix per channel."""
    by_channel: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        channel = str(row["channel"])
        by_channel.setdefault(channel, []).append(row)

    chunks: list[dict[str, object]] = []
    for channel in sorted(by_channel):
        channel_rows = by_channel[channel]
        by_position: dict[str, dict[int, tuple[int, float | None]]] = {}
        for row in channel_rows:
            well_id = str(row["well_id"])
            try:
                position = well_positions[well_id]
            except KeyError as error:
                raise GrowthSeriesCodecError(f"Unknown well ID: {well_id}") from error
            time_index = _integer(row["time_index"], "time_index")
            elapsed = _integer(row["elapsed_microseconds"], "elapsed_microseconds")
            value_object = row.get("value_raw")
            value = _nullable_number(value_object, "value_raw")
            series = by_position.setdefault(position, {})
            if time_index in series:
                raise GrowthSeriesCodecError(
                    f"Duplicate observation for {channel}/{position}/{time_index}"
                )
            series[time_index] = (elapsed, value)

        positions = tuple(sorted(by_position, key=_position_sort_key))
        if not positions:
            continue
        first = by_position[positions[0]]
        axis = tuple((index, first[index][0]) for index in sorted(first))
        if not axis:
            raise GrowthSeriesCodecError(f"Channel {channel} has no timepoints")
        for position in positions[1:]:
            candidate = tuple(
                (index, by_position[position][index][0]) for index in sorted(by_position[position])
            )
            if candidate != axis:
                raise GrowthSeriesCodecError(
                    f"Channel {channel} does not have one shared time axis"
                )

        values = [by_position[position][index][1] for position in positions for index, _ in axis]
        positions_json = json.dumps(positions, separators=(",", ":"))
        timepoints_blob = _encode_timepoints(axis)
        values_blob = _encode_values(values)
        digest = _content_hash(channel, positions_json, timepoints_blob, values_blob)
        chunks.append(
            {
                "plate_id": plate_id,
                "channel": channel,
                "positions_json": positions_json,
                "timepoints_blob": timepoints_blob,
                "values_blob": values_blob,
                "timepoint_count": len(axis),
                "position_count": len(positions),
                "encoding": ENCODING,
                "content_sha256": digest,
            }
        )
    return tuple(chunks)


def decode_growth_series(
    chunk: Mapping[str, object], position_well_ids: Mapping[str, str]
) -> tuple[dict[str, object], ...]:
    """Decode one chunk to the repository's stable logical row representation."""
    if str(chunk["encoding"]) != ENCODING:
        raise GrowthSeriesCodecError(f"Unsupported growth encoding: {chunk['encoding']}")
    channel = str(chunk["channel"])
    positions_json = str(chunk["positions_json"])
    timepoints_blob = _blob(chunk["timepoints_blob"], "timepoints_blob")
    values_blob = _blob(chunk["values_blob"], "values_blob")
    expected = str(chunk["content_sha256"])
    if _content_hash(channel, positions_json, timepoints_blob, values_blob) != expected:
        raise GrowthSeriesCodecError("Compact growth-series checksum mismatch")
    loaded_positions = json.loads(positions_json)
    if not isinstance(loaded_positions, list) or not all(
        isinstance(position, str) for position in loaded_positions
    ):
        raise GrowthSeriesCodecError("Invalid compact growth-series position list")
    positions = tuple(loaded_positions)
    axis = _decode_timepoints(timepoints_blob)
    values = _decode_values(values_blob, len(positions) * len(axis))
    if len(axis) != _integer(chunk["timepoint_count"], "timepoint_count") or len(
        positions
    ) != _integer(chunk["position_count"], "position_count"):
        raise GrowthSeriesCodecError("Compact growth-series dimensions do not match metadata")

    plate_id = str(chunk["plate_id"])
    result: list[dict[str, object]] = []
    offset = 0
    for position in positions:
        try:
            well_id = position_well_ids[position]
        except KeyError as error:
            raise GrowthSeriesCodecError(
                f"Stored position is absent from plate: {position}"
            ) from error
        for time_index, elapsed in axis:
            result.append(
                {
                    "plate_id": plate_id,
                    "well_id": well_id,
                    "channel": channel,
                    "time_index": time_index,
                    "elapsed_microseconds": elapsed,
                    "value_raw": values[offset],
                }
            )
            offset += 1
    return tuple(result)


def decode_plate_growth_series(
    chunks: Iterable[Mapping[str, object]], position_well_ids: Mapping[str, str]
) -> tuple[dict[str, object], ...]:
    rows = [row for chunk in chunks for row in decode_growth_series(chunk, position_well_ids)]
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                str(row["channel"]),
                _integer(row["time_index"], "time_index"),
                str(row["well_id"]),
            ),
        )
    )


def _encode_timepoints(axis: Sequence[tuple[int, int]]) -> bytes:
    raw = (
        _TIME_MAGIC
        + struct.pack(">I", len(axis))
        + b"".join(struct.pack(">qq", index, elapsed) for index, elapsed in axis)
    )
    return zlib.compress(raw, level=9)


def _decode_timepoints(blob: bytes) -> tuple[tuple[int, int], ...]:
    try:
        raw = zlib.decompress(blob)
    except zlib.error as error:
        raise GrowthSeriesCodecError("Invalid compressed time axis") from error
    if len(raw) < 8 or raw[:4] != _TIME_MAGIC:
        raise GrowthSeriesCodecError("Invalid compact time-axis header")
    count = struct.unpack(">I", raw[4:8])[0]
    if len(raw) != 8 + count * 16:
        raise GrowthSeriesCodecError("Invalid compact time-axis length")
    return tuple(
        struct.unpack(">qq", raw[8 + offset * 16 : 24 + offset * 16]) for offset in range(count)
    )


def _encode_values(values: Sequence[float | None]) -> bytes:
    bitmap = bytearray((len(values) + 7) // 8)
    numeric: list[float] = []
    for index, value in enumerate(values):
        if value is not None:
            bitmap[index // 8] |= 1 << (index % 8)
            numeric.append(value)
        else:
            numeric.append(0.0)
    raw = (
        _VALUE_MAGIC
        + struct.pack(">II", len(values), len(bitmap))
        + bytes(bitmap)
        + struct.pack(f">{len(numeric)}d", *numeric)
    )
    return zlib.compress(raw, level=9)


def _decode_values(blob: bytes, expected_count: int) -> tuple[float | None, ...]:
    try:
        raw = zlib.decompress(blob)
    except zlib.error as error:
        raise GrowthSeriesCodecError("Invalid compressed growth values") from error
    if len(raw) < 12 or raw[:4] != _VALUE_MAGIC:
        raise GrowthSeriesCodecError("Invalid compact growth-value header")
    count, bitmap_size = struct.unpack(">II", raw[4:12])
    if count != expected_count or bitmap_size != (count + 7) // 8:
        raise GrowthSeriesCodecError("Invalid compact growth-value dimensions")
    if len(raw) != 12 + bitmap_size + count * 8:
        raise GrowthSeriesCodecError("Invalid compact growth-value length")
    bitmap = raw[12 : 12 + bitmap_size]
    numbers = struct.unpack(f">{count}d", raw[12 + bitmap_size :])
    return tuple(
        numbers[index] if bitmap[index // 8] & (1 << (index % 8)) else None
        for index in range(count)
    )


def _content_hash(channel: str, positions_json: str, timepoints: bytes, values: bytes) -> str:
    digest = hashlib.sha256()
    for part in (channel.encode(), positions_json.encode(), timepoints, values):
        digest.update(struct.pack(">Q", len(part)))
        digest.update(part)
    return digest.hexdigest()


def _position_sort_key(position: str) -> tuple[str, int]:
    prefix = position.rstrip("0123456789")
    suffix = position[len(prefix) :]
    return prefix, int(suffix) if suffix else 0


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GrowthSeriesCodecError(f"{field} must be an integer")
    return value


def _nullable_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GrowthSeriesCodecError(f"{field} must be numeric or null")
    return float(value)


def _blob(value: object, field: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise GrowthSeriesCodecError(f"{field} must contain bytes")
    return bytes(value)
