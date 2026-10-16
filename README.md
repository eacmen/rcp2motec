# rcp2motec

CLI converter for RaceCapture Pro logs to MoTeC i2 `.ld` files. Python 3.10+
with no runtime dependencies. Reference projects and example logs are development
inputs; the installed converter does not depend on them.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
rcp2motec rcp_examples/day1_sess1.log -o day1_sess1.ld --driver Peter --vehicle BMW --venue VIR
rcp2motec rcp_examples/*.log --output-dir converted
```

You can also run directly from this directory without installation:

```sh
python3 -m rcp2motec rcp_examples/day1_sess1.log --output-dir converted
python3 -m rcp2motec --help
```

Existing files are protected unless `--force` is passed. Each output is written
to a temporary file and published only when conversion succeeds. Batch conversion
continues after errors and returns exit status 1 if any input failed.

## Data handling

RaceCapture headers contain `name|unit|min|max|rate` metadata. The converter
preserves channel names, units and numeric values, including lap and GPS channels.
Empty channels are omitted. `Interval` (milliseconds) supplies the common time
axis; `Interval` and `Utc` are not exported as data channels. The first timestamp
becomes time zero, and UTC determines the file date/time (stored in UTC). Without
UTC, the file date defaults to 1970-01-01 rather than an invented session date.

Rows are sorted by timestamp independently for each channel. At duplicate
timestamps, the last populated reading in file order wins. Each channel is
resampled at its header rate using the last known reading; the first reading is
extended back to the session start and the last forward to the end. Consequently,
missing readings and long gaps are held, and the converter does not split sessions
at gaps or infer missing data. `--interpolation linear` interpolates between
readings and should be used with care for discrete channels such as gear.

Use `--rate 25` to export all channels at 25 Hz. `--time-channel`, `--time-unit`
and `--rate` also allow ordinary numeric CSV files:

```sh
rcp2motec telemetry.csv --time-channel Time --time-unit s --rate 25
```

CSV must have a header, consistent column counts and finite numeric values.
LD stores float32 samples, so values may lose precision. Names and units must be
ASCII and fit the LD limits (32 and 12 bytes respectively); oversized fields
produce errors rather than silently colliding. Channels retain their RCP names;
the default export also adds analysis aliases while keeping every original channel.
Aliases include Speed → Ground Speed (mph → m/s), RPM → Engine RPM,
TPS → Throttle Position, CurrentLap → Lap Number, GPS coordinates, altitude,
wheel speeds, tyre pressures, coolant/oil temperature, fuel and battery.
Physical conversions apply only when the source unit matches the expected RCP unit.
Brake is a switch, not pedal travel; steering remains percent. Acceleration axes
are left unmapped until the sensor orientation/signs are confirmed. Distance is
left unchanged because its lap/session reset semantics require verification.

Each conversion also writes a same-name `.ldx` with beacon markers at observed
increases in CurrentLap (falling back to LapCount if CurrentLap is absent).
Times are relative to the same session origin as the LD and encoded in microseconds.
Counter decreases are ignored; skipped counts produce one observed crossing.
The first and last segments can be partial laps. Crossing precision is limited
by the source counter update rate. Keep the LD and LDX together when copying to i2.
Existing companions are protected by the same `--force` policy as LD files.

Use `--raw-channels` to omit aliases. Use `--channel-map mapping.json` to override
or extend aliases with an object of source names to `[name, unit, scale, offset]`:

```json
{"TPS": ["Throttle Position", "%", 1, 0], "Steering": null}
```

A null entry disables that alias. Custom mappings explicitly define conversion
as `output = input * scale + offset`; they take precedence over default unit checks.
Mapped names must be unique. i2 workspaces can still require their own channel
selection; channel names alone do not define every analysis configuration.

## Validation and format provenance

```sh
python3 -m unittest discover -s tests -v
```

Tests cover sparse and unordered input, duplicate timestamps, interpolation,
plain CSV, malformed values, overwrite protection and decoding using the supplied
sim-to-motec reader. The LD writer uses the MIT-licensed motec-i2 header template
and channel layout; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The binary format is reverse engineered. Structural tests and independent
reader checks do not establish compatibility with every i2 release. Actual
opening, plotting and GPS/lap behavior should be checked in your Windows i2 Pro
installation; i2 is not available in this development environment.
