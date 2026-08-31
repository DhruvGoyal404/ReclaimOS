"""The live Razorpay test-mode slice: recorded, small, and deliberately not core.

The sealed simulated batch is the primary result (SIMULATION.md). This package
exists to prove the integration is real -- that we authenticate against Razorpay,
create real objects, read real error envelopes, and verify real webhook
signatures -- on a recorded run of 10-20 records.
"""

from reclaimos.live.client import BASE_URL, RazorpayLiveClient
from reclaimos.live.recorder import (
    LIVE_DIR,
    TRANSCRIPT,
    CallRecord,
    Recorder,
    read_transcript,
    write_json,
)

__all__ = [
    "BASE_URL",
    "LIVE_DIR",
    "TRANSCRIPT",
    "CallRecord",
    "RazorpayLiveClient",
    "Recorder",
    "read_transcript",
    "write_json",
]
