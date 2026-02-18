import hashlib
import os
import time


def measure_hashrate(duration_seconds: float = 2.5) -> float:
    sample = os.urandom(32)
    start = time.perf_counter()
    end = start + max(0.5, duration_seconds)
    iterations = 0
    while time.perf_counter() < end:
        sample = hashlib.sha256(sample).digest()
        iterations += 1
    elapsed = max(time.perf_counter() - start, 1e-9)
    return max(iterations / elapsed, 1.0)
