"""
The benchmark loop, shared by every tool.
"""

from __future__ import annotations

import logging
import subprocess
import time
import typing
from pathlib import Path

import tqdm

from .common import (
    ROOT,
    BenchmarkConfig,
    ResultStore,
    compute_stats,
    load_skip_list,
    log_expected_runtime,
)

if typing.TYPE_CHECKING:
    from .runners import Runner

# === Global variables ===

logger = logging.getLogger(__name__)

# === Global functions ===


def time_runs(
    command: list[str],
    is_success: typing.Callable[[subprocess.CompletedProcess[str]], bool],
    desc: str,
    cfg: BenchmarkConfig,
    env: typing.Optional[dict[str, str]] = None,
) -> tuple[list[float], bool]:
    """
    Run ``command`` up to ``cfg.runs`` times, each in a fresh process with a timeout.

    The unit is given up after ``cfg.max_failures`` consecutive failures
    (timeout, non-zero exit, or ``is_success`` false).

    :param command: Command line of one run.
    :param is_success: Decides from the finished process whether the run is valid.
    :param desc: Text of the progress bar.
    :param cfg: Benchmark configuration.
    :param env: Environment of the process; None inherits it.
    :return: Wall-clock seconds of the successful runs, and whether the unit was given up.
    """
    times: list[float] = []
    fails = 0
    gave_up = False
    for _ in tqdm.tqdm(range(cfg.runs), desc=desc, unit="run"):
        if cfg.max_failures > 0 and fails >= cfg.max_failures:
            logger.warning(f"{fails} consecutive failure(s) for {desc}. Giving up.")
            gave_up = True
            break

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(
                command,
                check=True,
                text=True,
                capture_output=True,
                timeout=cfg.timeout_min * 60,
                cwd=ROOT,
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout for {desc}")
            fails += 1
            continue
        except subprocess.CalledProcessError as e:
            logger.warning(
                f"Run failed for {desc} (exit {e.returncode}):\n"
                f"{(e.stderr or '')[-2000:]}\n{(e.stdout or '')[-2000:]}"
            )
            fails += 1
            continue
        except Exception as e:  # script or interpreter not found, anything else
            logger.error(f"Unexpected error running {desc}: {e}")
            fails += 1
            continue
        elapsed = time.perf_counter() - t0

        logger.info(f"Output for {desc}:\n{proc.stdout.strip()}")
        if not is_success(proc):
            logger.warning(f"Invalid answer for {desc}: counted as a failure.")
            fails += 1
            continue

        logger.info(f"Run completed for {desc} in {elapsed:.4f} seconds")
        fails = 0
        times.append(elapsed)
    return times, gave_up


def run_benchmark(
    runner: "Runner",
    cfg: BenchmarkConfig,
    files: typing.Optional[list[Path]] = None,
) -> dict[str, typing.Any]:
    """
    Benchmark every file with every label of the runner.

    Results are read from and saved to the runner's JSON file after each unit,
    so the units already there (and the files of the skip list) are not run again.

    :param runner: The tool to benchmark.
    :param cfg: Benchmark configuration.
    :param files: Files to run; None runs every file of the runner's data directory.
    :return: The whole content of the results file.
    """
    files = files if files is not None else runner.files()
    if not files:
        message = f"No {runner.extension} files found in {runner.data_dir}"
        if runner.missing_files_is_error:
            raise FileNotFoundError(message)
        logger.error(message)
        return {}
    labels = runner.labels()
    logger.info(
        f"Benchmarking {len(files)} file(s) x {len(labels)} label(s) x {cfg.runs} runs"
    )
    log_expected_runtime(len(files) * len(labels), cfg)

    store = ResultStore(runner.results_path)
    if runner.seed_path is not None and not runner.results_path.exists():
        if not store.seed_from(runner.seed_path, {f.name for f in files}):
            return {}
    skip = load_skip_list(runner.skip_path)
    if skip:
        logger.info(f"Skip list ({runner.skip_path.name}): {sorted(skip)}")

    for file in files:
        if file.name in skip:
            logger.info(f"Skipping {file.name}: in the skip list.")
            continue
        if runner.file_level_resume and store.has(file.name):
            logger.info(
                f"Results for {file.name} already exist, skipping. "
                f"Delete the entry from {store.path.name} to rerun."
            )
            continue
        for label in labels:
            if store.has(file.name, label):
                logger.info(
                    f"Results for {file.name} {label or ''} exist, skipping. "
                    f"Delete the entry from {store.path.name} to rerun."
                )
                continue
            desc = f"{file.name}" + (f" - {label}" if label else "")
            times, gave_up = time_runs(
                runner.command(file, label), runner.is_success, desc, cfg, runner.env()
            )
            if gave_up and runner.discard_on_giveup:
                times = []  # too many failures: the times are not reliable
            stats = compute_stats(times)
            if stats:
                logger.info(f"{desc}: avg={stats['avg']:.4f}s over {stats['runs']} runs")
            else:
                logger.warning(f"No successful runs for {desc}.")
            store.put(file.name, stats, label)
    return store.data
