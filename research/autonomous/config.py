from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LoopConfig:
    registry: Path = Path("/home/ubuntu/evolution-autonomous")
    lock: Path = Path("/var/lib/market-career-dashboard/evolution_autonomous.lock")
    data_dir: Path = ROOT / "data/toss_1m"
    data_manifest: Path | None = None
    provisional: bool = False
    naive_timezone: str | None = None
    max_iterations: int = 1
    min_candidates: int = 1
    max_candidates: int = 40
    min_free_bytes: int = 10 * 1024**3
    disk_reserve_fraction: float = .1
    max_archive_bytes: int = 2 * 1024**3
    max_extracted_bytes: int = 4 * 1024**3
    poll_seconds: float = 5
    export_timeout_seconds: int = 3600

    def __post_init__(self):
        if not 1 <= self.max_iterations <= 100:
            raise ValueError("max-iterations must be finite: 1..100")
        if not 1 <= self.min_candidates <= self.max_candidates <= 40:
            raise ValueError("Invalid candidate bounds; frozen maximum is 40")
        if self.data_manifest and self.provisional:
            raise ValueError("Choose reviewed manifest OR explicit provisional mode")
        if self.min_free_bytes < 0 or not 0 <= self.disk_reserve_fraction < 1:
            raise ValueError("Invalid resource reserve")
        for path in (self.registry, self.lock, self.data_dir):
            if Path(path).absolute() != Path(path).resolve():
                raise ValueError("Configured paths must not traverse symlinks")
        if self.registry == self.data_dir or self.data_dir in self.registry.parents:
            raise ValueError("Registry must be outside market data")
        from .safety_gate import data_path_gate
        data_path_gate(self.data_dir)
