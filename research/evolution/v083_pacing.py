"""V083 opt-in cooperative pacing. The 75% host CPU hard abort is unchanged."""
import time
from .adaptive_capacity import ResourceMonitor


class PacedMonitor(ResourceMonitor):
    def __init__(self, *, pacing_seconds=.025, **kwargs):
        if not .005 <= pacing_seconds <= .100:
            raise ValueError("V083_PACING_OUT_OF_RANGE")
        super().__init__(**kwargs)
        self.pacing_seconds = pacing_seconds

    def check(self):
        # Still raises immediately if *any* sampled host CPU >75%, RAM>65%,
        # swap>3%, deadline exceeded or monitor measurement fails.
        super().check()
        # Evaluation invokes check() once per market minute. Voluntary sleep
        # reduces average CPU pressure but cannot promise lower peak under
        # concurrent host activity. A failing run remains a failure.
        time.sleep(self.pacing_seconds)
        super().check()
