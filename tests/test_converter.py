import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

from rcp2motec.cli import main
from rcp2motec.converter import read_log, samples, write_ld


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'test.log'
        self.source.write_text('"Interval"|"ms"|0|0|1,"RPM"|"rpm"|0|9000|2,"Gear"|""|0|6|1\n'
                               '1000,3000,3\n0,1000,1\n500,2000,\n500,2500,\n')

    def test_sparse_unsorted_duplicate_and_interpolation(self):
        log = read_log(self.source)
        self.assertEqual(list(samples(log.channels[0], 0, 1)), [1000, 2500, 3000])
        self.assertEqual(list(samples(log.channels[1], 0, 1)), [1, 3])
        log.channels[0].rate = 4
        self.assertEqual(list(samples(log.channels[0], 0, 1, 'linear')), [1000, 1750, 2500, 2750, 3000])

    def test_binary_with_independent_reference_reader(self):
        log = read_log(self.source)
        output = self.source.with_suffix('.ld')
        write_ld(output, log, driver='Peter', vehicle='BMW', venue='VIR')
        raw = output.read_bytes()
        self.assertEqual(struct.unpack_from('<I', raw, 86)[0], 2)
        # Cross-check with the supplied project's independent decoder.
        reference = Path(__file__).resolve().parents[1] / 'reference_projects/sim-to-motec/stm/motec/ld.py'
        if not reference.exists():
            self.skipTest('reference decoder not available')
        spec = importlib.util.spec_from_file_location('reference_ld', reference)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        decoded = module.MotecLog.from_string(raw)
        self.assertEqual(decoded.driver, 'Peter')
        self.assertEqual(decoded.numchannels, 2)
        self.assertEqual(decoded.channels[0].samples.samples, [1000, 2500, 3000])
        self.assertEqual(decoded.channels[1].samples.samples, [1, 3])
        self.assertEqual(decoded.channels[-1].nextpos, 0)

    def test_cli_preserves_existing_output(self):
        output = self.source.with_suffix('.ld')
        self.assertEqual(main([str(self.source)]), 0)
        original = output.read_bytes()
        self.assertEqual(main([str(self.source)]), 1)
        self.assertEqual(output.read_bytes(), original)

    def test_analysis_aliases_and_beacon_origin(self):
        from rcp2motec.mapping import analysis_log, lap_markers, write_ldx
        from xml.etree import ElementTree as ET
        self.source.write_text('Interval,Speed,CurrentLap\n10000,100,1\n11000,110,2\n12000,120,2\n13000,130,1\n14000,140,2\n15000,150,2\n')
        log = read_log(self.source, 'Interval', 'ms', 1)
        log.channels[0].unit = 'mph'
        mapped = analysis_log(log)
        channels = {c.name: c for c in mapped.channels}
        self.assertAlmostEqual(channels['Ground Speed'].readings[0][1], 44.704)
        self.assertEqual(channels['Speed'].readings[0][1], 100)
        self.assertEqual(lap_markers(mapped), [1, 4])
        output = self.source.with_suffix('.ldx')
        write_ldx(output, mapped)
        root = ET.parse(output)
        self.assertEqual([m.get('Time') for m in root.findall('.//Marker')],
                         ['1000000.00', '4000000.00'])
        self.assertEqual(root.find('.//String').get('Value'), '3')
        config = self.source.with_suffix('.json')
        config.write_text('{"Speed": null}')
        self.assertNotIn('Ground Speed', {c.name for c in analysis_log(log, config).channels})

    def test_bad_values_and_plain_csv(self):
        self.source.write_text('Time,RPM\n0,1000\n1,2000\n')
        log = read_log(self.source, 'Time', 's', 2)
        self.assertEqual(list(samples(log.channels[0], 0, 1)), [1000, 1000, 2000])
        self.source.write_text('Time,RPM\n0,nan\n')
        with self.assertRaisesRegex(ValueError, 'invalid number'):
            read_log(self.source, 'Time', 's', 2)


if __name__ == '__main__':
    unittest.main()
