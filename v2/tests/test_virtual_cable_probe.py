from array import array
import unittest
from acceptance.virtual_cable_probe import resample, virtual_device


class VirtualCableProbeTests(unittest.TestCase):
    def test_32khz_to_48khz_keeps_the_tail_after_the_int32_overflow_boundary(self):
        samples = array('f', range(32000*6))
        result = resample(samples, 32000)
        self.assertEqual(len(result), 48000*6)
        for index in (72000, 144000, 264000):
            self.assertAlmostEqual(result[index], index*(32000/48000), places=2)
        self.assertEqual(result[-1], samples[-1])

    def test_48khz_downsampling_keeps_the_full_eight_second_source(self):
        result = resample(array('f', range(48000*8)), 48000, 16000)
        self.assertEqual(len(result), 16000*8)
        self.assertEqual(result[-1], 48000*8-3)

    def test_missing_or_ambiguous_virtual_endpoint_never_selects_physical_default(self):
        apis = [{'name': 'Windows WASAPI'}]
        name = 'CABLE Input (VB-Audio Virtual Cable)'
        physical = {'name': 'Physical speaker', 'hostapi': 0, 'max_output_channels': 2}
        cable = {'name': name, 'hostapi': 0, 'max_output_channels': 2}
        for devices in ([physical], [physical, cable, cable]):
            with self.assertRaises(ValueError):virtual_device(devices, apis, name, 'output')
        self.assertEqual(virtual_device([physical, cable], apis, name, 'output'), 1)
