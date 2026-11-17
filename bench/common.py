"""
Shared pieces of the benchmark: paths, enums, configuration, logging, the
statistics of a list of times and the JSON store of the results.
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import json
import logging
import os
import statistics
import typing
from pathlib import Path

# === Global variables ===

# the ``benchmark`` directory: the scripts (run_fdl.sh, ...) and the jars live here
ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT / "data"
OWL_DIR: Path = DATA_DIR / "owl"
DELOREAN_DIR: Path = DATA_DIR / "delorean"
RESULTS_DIR: Path = ROOT / "results"
LOGS_DIR: Path = ROOT / "logs"

logger = logging.getLogger(__name__)


# === Global functions ===


def setup_logging(tool: str, verbose: bool = False) -> Path:
    """
    Log to the console and to ``logs/<tool>/benchmark_<timestamp>.log``.

    :param tool: Name of the tool, used as the log sub-directory.
    :param verbose: Log at DEBUG instead of INFO.
    :return: Path of the log file.
    """
    log_dir = LOGS_DIR / tool
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"benchmark_{stamp}.log"
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path)],
        force=True,
    )
    return log_path


def compute_stats(times: list[float]) -> dict[str, float]:
    """
    Statistics of the successful run times.

    :param times: Wall-clock times in seconds.
    :return: avg, std, min, max, total and runs; an empty dict without times.
    """
    if not times:
        return {}
    return {
        "avg": statistics.mean(times),
        "std": statistics.stdev(times) if len(times) > 1 else 0.0,
        "min": min(times),
        "max": max(times),
        "total": sum(times),
        "runs": len(times),
    }


def load_skip_list(path: Path) -> set[str]:
    """
    Names of the files that a benchmark must not run (``{"files": [...]}``).

    :param path: JSON file of the skip list; may be missing.
    :return: The set of file names, empty when the file does not exist.
    """
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()).get("files", []))


def log_expected_runtime(units: int, cfg: "BenchmarkConfig") -> None:
    """
    Log the worst case (every run hits the timeout) and the minimum (every unit
    gives up after ``max_failures`` timeouts).

    :param units: Number of (file, label) pairs to run.
    :param cfg: Benchmark configuration.
    """
    worst = datetime.timedelta(minutes=units * cfg.runs * cfg.timeout_min)
    best = datetime.timedelta(
        minutes=units * max(cfg.max_failures, 1) * cfg.timeout_min
    )
    logger.info(f"Expected worst-case runtime: {worst}")
    logger.info(f"Expected minimum runtime (every unit fails early): {best}")


# === Classes ===


class Tool(enum.StrEnum):
    PYTHON = "python"
    ORIGINAL = "original"
    DELOREAN = "delorean"


class OntologyKind(enum.StrEnum):
    ORIGINAL = "original"
    GRADED = "graded"


@dataclasses.dataclass(frozen=True)
class BenchmarkConfig:
    """
    :param runs: Timed executions per (file, label).
    :param max_failures: Consecutive failures after which the unit is given up
        (0 never gives up).
    :param timeout_min: Minutes after which a run is killed and counts as a failure.
    """

    runs: int = 10
    max_failures: int = 1
    timeout_min: int = 20

    @classmethod
    def from_env(cls) -> "BenchmarkConfig":
        """Default configuration; ``BENCHMARK_RUNS`` overrides the number of runs."""
        return cls(runs=int(os.environ.get("BENCHMARK_RUNS", cls.runs)))


class ResultStore:
    """
    JSON file of the results, saved after every ``put`` so that an interrupted
    benchmark resumes where it stopped.

    The layout is ``{file: stats}`` for a tool without labels and
    ``{file: {label: stats}}`` with labels (the MILP provider). A failed unit is
    stored as ``{}`` and counts as done: delete its entry to run it again.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, typing.Any] = (
            json.loads(path.read_text()) if path.exists() else {}
        )
        if self.data:
            logger.info(f"Loaded {len(self.data)} file(s) from {path.name}.")

    def seed_from(self, source: Path, names: typing.Container[str]) -> bool:
        """
        Copy into the store the entries of ``source`` whose file name is in ``names``.

        :return: False (and a warning) when ``source`` does not exist.
        """
        if not source.exists():
            logger.warning(f"Previous results file {source.name} not found.")
            return False
        self.data.update(
            {k: v for k, v in json.loads(source.read_text()).items() if k in names}
        )
        logger.info(f"Copied previous results from {source.name}.")
        return True

    def has(self, name: str, label: typing.Optional[str] = None) -> bool:
        if name not in self.data:
            return False
        return label is None or label in self.data[name]

    def put(
        self,
        name: str,
        stats: dict[str, float],
        label: typing.Optional[str] = None,
    ) -> None:
        if label is None:
            self.data[name] = stats
        else:
            self.data.setdefault(name, {})[label] = stats
        self.save()

    def save(self) -> None:
        # write a temporary file and replace: a stalled or interrupted write
        # (Google Drive) never leaves a half-written JSON
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.new")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)
