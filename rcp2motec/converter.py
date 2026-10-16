"""Sparse RaceCapture CSV parsing and fixed-rate LD serialization."""
from array import array
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.resources import files
import math
from pathlib import Path
import struct
import sys


@dataclass
class Channel:
    name: str
    unit: str
    rate: int
    readings: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Log:
    channels: list[Channel]
    start: float
    end: float
    date: datetime


def read_log(path: Path, time_channel: str = 'Interval', time_unit: str | None = None,
             rate: int | None = None) -> Log:
    """Read RCP pipe-delimited metadata embedded in a CSV header.

    Plain CSV headers are supported with an explicit rate and time unit.
    Duplicate channel timestamps use the last nonempty value in file order.
    """
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError('empty input file') from None
        channels = []
        for header in headers:
            parts = [part.strip().strip('"') for part in header.split('|')]
            name = parts[0]
            if not name:
                raise ValueError('empty channel name')
            unit = parts[1] if len(parts) > 1 else ''
            try:
                frequency = float(parts[4]) if len(parts) >= 5 else rate
                if frequency is None or not math.isfinite(frequency) or frequency != int(frequency):
                    raise ValueError()
                frequency = int(frequency)
            except (ValueError, OverflowError):
                raise ValueError(f'{name}: missing or invalid integer rate; use --rate for plain CSV') from None
            if not 1 <= frequency <= 65535:
                raise ValueError(f'{name}: rate must be between 1 and 65535 Hz')
            channels.append(Channel(name, unit, rate or frequency))
        names = [c.name for c in channels]
        if len(set(names)) != len(names):
            raise ValueError('duplicate channel names')
        if time_channel not in names:
            raise ValueError(f'time channel {time_channel!r} not found')
        ti = names.index(time_channel)
        unit = (time_unit or channels[ti].unit).lower()
        factors = {'ms': .001, 's': 1., 'sec': 1., 'min': 60., 'us': .000001}
        if unit not in factors:
            raise ValueError('unknown time unit; specify --time-unit ms, s, min, or us')
        start, end = math.inf, -math.inf
        utc = []
        for line, row in enumerate(reader, 2):
            if not row or all(not v.strip() for v in row):
                continue
            if len(row) != len(channels):
                raise ValueError(f'line {line}: expected {len(channels)} columns, got {len(row)}')
            try:
                t = float(row[ti]) * factors[unit]
                if not math.isfinite(t):
                    raise ValueError()
            except ValueError:
                raise ValueError(f'line {line}: invalid timestamp {row[ti]!r}') from None
            start, end = min(start, t), max(end, t)
            for channel, raw in zip(channels, row):
                if not raw.strip():
                    continue
                try:
                    value = float(raw)
                    if not math.isfinite(value) or abs(value) > 3.4028234e38:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f'line {line}, {channel.name}: invalid number {raw!r}') from None
                if channel.name == 'Utc' and value > 0:
                    utc.append((t, value))
                if channel.name not in {time_channel, 'Utc'}:
                    channel.readings.append((t, value))
        if not math.isfinite(start):
            raise ValueError('no data rows')
        channels = [c for c in channels if c.readings]
        if not channels:
            raise ValueError('no populated data channels')
        for channel in channels:
            channel.readings.sort(key=lambda item: item[0])
            channel.readings = list(dict(channel.readings).items())
        date = datetime.fromtimestamp(0, timezone.utc)
        if utc:
            t, stamp = min(utc, key=lambda item: item[0])
            date = datetime.fromtimestamp(stamp / 1000 - (t - start), timezone.utc)
        return Log(channels, start, end, date)


def samples(channel: Channel, start: float, end: float, method: str = 'hold') -> array:
    """Use a common zero origin; extend the first/last reading to session edges."""
    count = math.floor((end - start) * channel.rate + 1e-7) + 1
    if count > 100_000_000:
        raise ValueError(f'{channel.name}: excessive sample count; check time units')
    result = array('f')
    readings = channel.readings
    index = 0
    for i in range(count):
        t = start + i / channel.rate
        while index + 1 < len(readings) and readings[index + 1][0] <= t + 1e-9:
            index += 1
        t0, value = readings[index]
        if method == 'linear' and t > t0 and index + 1 < len(readings):
            t1, next_value = readings[index + 1]
            value += (next_value - value) * (t - t0) / (t1 - t0)
        result.append(value)
    return result


def text(value: str, length: int) -> bytes:
    encoded = value.encode('ascii')
    if len(encoded) > length:
        raise ValueError(f'{value!r} exceeds the LD field limit of {length} bytes')
    return encoded.ljust(length, b'\0')


def write_ld(path: Path, log: Log, *, driver: str = '', vehicle: str = '',
             venue: str = '', session: str = '', method: str = 'hold') -> None:
    """Write little-endian float32 channels, retaining the known-good LD header."""
    header = bytearray(files('rcp2motec').joinpath('header.bin').read_bytes())
    base = len(header)
    data_pos = base + len(log.channels) * 124
    struct.pack_into('<II', header, 8, base, data_pos)
    struct.pack_into('<I', header, 86, len(log.channels))
    for offset, size, value in [(94, 16, log.date.strftime('%d/%m/%Y')),
                                (126, 16, log.date.strftime('%H:%M:%S')),
                                (158, 64, driver), (222, 64, vehicle),
                                (350, 64, venue), (1508, 64, session),
                                (1572, 64, 'Converted by rcp2motec')]:
        header[offset:offset + size] = text(value, size)
    # Replace the template's event tree, including its original vehicle details.
    header[1762:] = bytes(len(header) - 1762)
    struct.pack_into('<I', header, 36, 1762)
    struct.pack_into('<64s64s1024sH', header, 1762,
                     text('RaceCapture', 64), text(session, 64), bytes(1024), 4918)
    struct.pack_into('<64sH', header, 4918, text(venue, 64), 6218)
    struct.pack_into('<64sI32s32s', header, 6218, text(vehicle, 64), 0, bytes(32), bytes(32))
    names = [text(c.name, 32) for c in log.channels]
    units = [text(c.unit, 12) for c in log.channels]
    with path.open('wb') as output:
        output.write(header)
        output.write(bytes(124 * len(log.channels)))
        for i, channel in enumerate(log.channels):
            values = samples(channel, log.start, log.end, method)
            if sys.byteorder != 'little':
                values.byteswap()
            position = base + i * 124
            metadata = struct.pack('<IIII8H32s8s12s40s',
                                   position - 124 if i else 0,
                                   position + 124 if i + 1 < len(log.channels) else 0,
                                   data_pos, len(values), 4, 7, 4, channel.rate,
                                   0, 1, 1, 0, names[i], b'', units[i], b'\xc9')
            output.seek(position)
            output.write(metadata)
            output.seek(data_pos)
            values.tofile(output)
            data_pos += len(values) * 4
