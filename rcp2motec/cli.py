"""Command line interface."""
import argparse
from pathlib import Path
import os
import sys
import tempfile

from .converter import read_log, write_ld
from .mapping import analysis_log, lap_markers, write_ldx


def main(argv=None):
    parser = argparse.ArgumentParser(description='Convert RaceCapture CSV logs to MoTeC i2 .ld files.')
    parser.add_argument('inputs', nargs='+', type=Path, help='RCP .log or CSV files')
    parser.add_argument('-o', '--output', type=Path, help='output file (one input only)')
    parser.add_argument('--output-dir', type=Path, help='directory for converted logs')
    parser.add_argument('--raw-channels', action='store_true', help='omit analysis aliases')
    parser.add_argument('--channel-map', type=Path, help='JSON overrides for analysis aliases')
    parser.add_argument('--force', action='store_true', help='replace existing output files')
    parser.add_argument('--time-channel', default='Interval')
    parser.add_argument('--time-unit', choices=['ms', 's', 'min', 'us'])
    parser.add_argument('--rate', type=int, help='override channel rates, required for plain CSV')
    parser.add_argument('--interpolation', choices=['hold', 'linear'], default='hold')
    for name in ['driver', 'vehicle', 'venue', 'session']:
        parser.add_argument('--' + name, default='')
    args = parser.parse_args(argv)
    if args.output and (len(args.inputs) != 1 or args.output_dir):
        parser.error('--output requires one input and cannot be combined with --output-dir')
    if args.rate is not None and not 1 <= args.rate <= 65535:
        parser.error('--rate must be between 1 and 65535')
    destinations = [args.output or (args.output_dir or p.parent) / (p.stem + '.ld') for p in args.inputs]
    if len({p.resolve() for p in destinations}) != len(destinations):
        parser.error('multiple inputs would write to the same output file')
    if any(p.resolve() in {s.resolve() for s in args.inputs} for p in destinations):
        parser.error('output cannot overwrite an input file')
    failed = False
    for source, destination in zip(args.inputs, destinations):
        temporary = None
        extra_temporary = None
        try:
            if destination.exists() and not args.force:
                raise ValueError(f'{destination} already exists; use --force to replace it')
            sidecar = destination.with_suffix('.ldx')
            if sidecar.exists() and not args.force:
                raise ValueError(f'{sidecar} already exists; use --force to replace it')
            log = read_log(source, args.time_channel, args.time_unit, args.rate)
            if not args.raw_channels:
                log = analysis_log(log, args.channel_map)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.rcp2motec-', dir=destination.parent)
            os.close(fd)
            write_ld(Path(temporary), log, driver=args.driver, vehicle=args.vehicle,
                     venue=args.venue, session=args.session, method=args.interpolation)
            fd, extra_temporary = tempfile.mkstemp(prefix='.rcp2motec-', dir=destination.parent)
            os.close(fd)
            write_ldx(Path(extra_temporary), log)
            if args.force:
                os.replace(extra_temporary, sidecar)
                extra_temporary = None
                os.replace(temporary, destination)
            else:
                os.link(extra_temporary, sidecar)
                os.unlink(extra_temporary)
                extra_temporary = None
                os.link(temporary, destination)
                os.unlink(temporary)
            temporary = None
            print(f'{source} -> {destination} ({len(log.channels)} channels, {log.end - log.start:.2f}s, {len(lap_markers(log))} lap markers)')
        except (OSError, ValueError, OverflowError) as exc:
            print(f'{source}: {exc}', file=sys.stderr)
            failed = True
        finally:
            if extra_temporary is not None:
                Path(extra_temporary).unlink(missing_ok=True)
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
    return int(failed)
