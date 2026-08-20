from __future__ import annotations

import threading
import time
import unittest

import mutations_publish


class PublishConcurrencyTests(unittest.TestCase):
    def test_site_lock_serializes_same_site_but_not_contract(self) -> None:
        active = 0
        peak = 0
        guard = threading.Lock()

        @mutations_publish._locked_site
        def work(site_id: int) -> int:
            nonlocal active, peak
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(.01)
            with guard:
                active -= 1
            return site_id

        threads = [threading.Thread(target=work, args=(7,)) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak, 1)


if __name__ == "__main__":
    unittest.main()
