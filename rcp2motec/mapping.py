"""Analysis aliases and lap markers, independent of the LD binary layout."""
from dataclasses import replace
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

from .converter import Channel, Log

# source: (destination, destination unit, scale, offset). Raw channels are retained.
DEFAULT_MAPPING = {
    'Speed': ('Ground Speed', 'm/s', .44704, 0),
    'RPM': ('Engine RPM', 'rpm', 1, 0),
    'TPS': ('Throttle Position', '%', 1, 0),
    'Battery': ('Battery Voltage', 'V', 1, 0),
    'Latitude': ('GPS Latitude', 'deg', 1, 0),
    'Longitude': ('GPS Longitude', 'deg', 1, 0),
    'Altitude': ('GPS Altitude', 'm', .3048, 0),
    'CurrentLap': ('Lap Number', '', 1, 0),
    'LapTime': ('Lap Time', 's', 60, 0),
    'Coolant': ('Engine Temp', 'C', 5 / 9, -32 * 5 / 9),
    'OilTemp': ('Eng Oil Temp', 'C', 5 / 9, -32 * 5 / 9),
    'FuelLevel': ('Fuel Level', 'l', 1, 0),
    'Brake_F': ('Brake Pressure Front', 'bar', .0689475729, 0),
    'Brake_R': ('Brake Pressure Rear', 'bar', .0689475729, 0),
    'Brake': ('Brake Switch', '', 1, 0),
    'Steering': ('Steering Pos', '%', 1, 0),
}
for source, corner in [('LF', 'FL'), ('RF', 'FR'), ('LR', 'RL'), ('RR', 'RR')]:
    DEFAULT_MAPPING['Wheel_' + source] = ('Wheel Speed ' + corner, 'm/s', .44704, 0)
    DEFAULT_MAPPING['TPress_' + source] = ('Tyre Pres ' + corner, 'kPa', 6.89475729, 0)


def analysis_log(log: Log, mapping_path: Path | None = None) -> Log:
    """Add aliases; an optional JSON object overrides or disables default entries."""
    mapping = dict(DEFAULT_MAPPING)
    overrides = {}
    if mapping_path:
        overrides = json.loads(mapping_path.read_text())
        if not isinstance(overrides, dict):
            raise ValueError('channel map must be a JSON object')
        mapping.update(overrides)
    channels = list(log.channels)
    names = {c.name for c in channels}
    for source in log.channels:
        entry = mapping.get(source.name)
        if entry is None:
            continue
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            raise ValueError(f'{source.name}: mapping requires [name, unit, scale, offset]')
        expected_units = {'Speed': 'mph', 'Altitude': 'ft', 'LapTime': 'min',
                          'Coolant': 'f', 'OilTemp': 'f', 'Latitude': 'degrees',
                          'Longitude': 'degrees', 'Brake_F': 'psi', 'Brake_R': 'psi',
                          'FuelLevel': 'ltrs'}
        if source.name.startswith('Wheel_'):
            expected_units[source.name] = 'mph'
        if source.name.startswith('TPress_'):
            expected_units[source.name] = 'psi'
        expected = expected_units.get(source.name)
        if source.name not in overrides and expected and source.unit.lower() != expected:
            continue
        name, unit, scale, offset = entry
        if not isinstance(name, str) or not isinstance(unit, str) or not name:
            raise ValueError(f'{source.name}: invalid mapping name or unit')
        if name in names:
            raise ValueError(f'{source.name}: mapped channel {name!r} already exists')
        try:
            readings = [(t, value * float(scale) + float(offset)) for t, value in source.readings]
        except (TypeError, ValueError):
            raise ValueError(f'{source.name}: invalid mapping scale or offset') from None
        if any(not math.isfinite(v) or abs(v) > 3.4028234e38 for _, v in readings):
            raise ValueError(f'{source.name}: mapped value outside float32 range')
        channels.append(Channel(name, unit, source.rate, readings))
        names.add(name)
    return replace(log, channels=channels)


def lap_markers(log: Log) -> list[float]:
    """Use observed increases only; don't invent crossings for skipped lap numbers."""
    by_name = {c.name: c for c in log.channels}
    channel = by_name.get('CurrentLap') or by_name.get('LapCount')
    if channel is None:
        return []
    return [t - log.start for (_, previous), (t, current)
            in zip(channel.readings, channel.readings[1:])
            if current > previous and current.is_integer() and previous.is_integer()
            and log.start < t < log.end]


def write_ldx(path: Path, log: Log) -> None:
    root = ET.Element('LDXFile', Locale='English_United States.1252', DefaultLocale='C', Version='1.6')
    layers = ET.SubElement(root, 'Layers')
    layer = ET.SubElement(layers, 'Layer')
    block = ET.SubElement(layer, 'MarkerBlock')
    markers = lap_markers(log)
    group = ET.SubElement(block, 'MarkerGroup', Name='Beacons', Index=str(len(markers) - 1))
    for i, seconds in enumerate(markers, 1):
        ET.SubElement(group, 'Marker', Version='100', ClassName='BCN', Name=f'Manual.{i}',
                      Flags='77', Time=f'{seconds * 1_000_000:.2f}')
    details = ET.SubElement(layers, 'Details')
    ET.SubElement(details, 'String', Id='Total Laps', Value=str(len(markers) + 1))
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)
