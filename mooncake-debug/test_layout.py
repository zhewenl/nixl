import unittest
from bench_two_peers import region_counts, memory_budget, descriptor_rows


class LayoutTests(unittest.TestCase):
    def test_historical_payload_and_fixed_registration_count(self):
        counts = region_counts(207360, 256)
        self.assertEqual(len(counts), 256)
        self.assertEqual(sum(counts), 207360)
        self.assertEqual(memory_budget(207360, 32256, 16, 256, 4),
                         207360 * 32272 * 4 + 256 * 128)

    def test_slots_are_disjoint_and_descriptors_stay_in_region(self):
        counts = region_counts(200000, 256)
        addresses = [10**12 + r * 10**9 for r in range(256)]
        seen = set()
        for slot in range(4):
            rows = descriptor_rows(addresses, counts, 32256, 16, slot, 2)
            self.assertEqual(len(rows), 200000)
            pos = 0
            for addr, count in zip(addresses, counts):
                for start, length, dev in rows[pos:pos + count]:
                    self.assertGreaterEqual(start, addr + 64)
                    self.assertLessEqual(start + length, addr + 64 + count * 32272 * 4)
                    self.assertEqual(dev, 2)
                    self.assertNotIn(start, seen)
                    seen.add(start)
                pos += count

    def test_invalid_shape_rejected(self):
        for n, r in [(0, 1), (10, 0), (10, 11)]:
            with self.assertRaises(ValueError):
                region_counts(n, r)


if __name__ == '__main__':
    unittest.main()
