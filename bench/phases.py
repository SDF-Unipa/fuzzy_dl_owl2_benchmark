"""
Phase benchmark: where the time of a (sat?) query goes.

Three parts, all written as one row per run and phase in
``results/phases/raw.csv`` (columns ``kb, language, solver, tokenizer, mode,
run, phase, seconds``):

* ``python``: fuzzy-dl-owl2 (DLParserFast, Lukasiewicz) in a fresh process per
  run (``phase_worker.py``): startup, import, parse, tableau, build, solver and,
  in the same process, one warm pass;
* ``tokenizers``: the three tokenizer backends on the same files;
* ``java``: fuzzyDL through ``run_fdl.sh``, wall clock and the ``Time (s)`` the
  jar prints; the same on a trivial KB gives the JVM startup.

A unit (one KB with one solver and tokenizer) that times out or fails is given
up and stored as one ``timeout`` / ``failed`` row; the tables print N/A.
"""

from __future__ import annotations

import csv
import dataclasses
import enum
import json
import logging
import re
import subprocess
import sys
import time
import typing
from pathlib import Path

from .common import (
    DATA_DIR,
    RESULTS_DIR,
    ROOT,
    BenchmarkConfig,
)
from .providers import Provider
from .tasks import load_selected_ontologies

# === Global variables ===

logger = logging.getLogger(__name__)

PHASES_DIR: Path = RESULTS_DIR / "phases"
RAW_CSV: Path = PHASES_DIR / "raw.csv"
TRIVIAL_NAME: str = "(trivial)"
TRIVIAL_KB: str = "(instance a Thing 1.0)\n(sat?)\n"
CSV_COLUMNS: tuple[str, ...] = (
    "kb", "language", "solver", "tokenizer", "mode", "run", "phase", "seconds",
)
WORKER: Path = Path(__file__).resolve().parent / "phase_worker.py"
MARKER: str = "PHASES "
JAVA_TIME = re.compile(r"Time \(s\):\s*([0-9.eE+-]+)")
NO_SOLVER: str = "-"

# === Classes ===


class Language(enum.StrEnum):
    """Reasoner under test; the ``language`` column of the CSV."""
    PYTHON = "python"
    JAVA = "java"


class Part(enum.StrEnum):
    """Parts of the phase benchmark (``--parts``)."""
    PYTHON = "python"
    TOKENIZERS = "tokenizers"
    JAVA = "java"


class Backend(enum.StrEnum):
    """Tokenizer backends; ``-`` in the CSV where no tokenizer is involved."""

    CYTHON = "cython"
    CFFI = "cffi"
    REGEX = "regex"


class Stage(enum.StrEnum):
    """How far the worker goes; ``scan`` rows come from the tokenizer alone."""

    SCAN = "tokenize"
    PARSE = "parse"
    SOLVE = "solve"


@dataclasses.dataclass(frozen=True)
class Unit:
    """One cell of the tables: the runs of a KB under one configuration.

    :param kb: File name of the KB (or the trivial KB).
    :param language: Reasoner under test.
    :param solver: ``gurobi``, ``cbc``, ``highs`` or ``-`` where no solver is involved.
    :param tokenizer: Tokenizer backend, ``-`` for Java.
    :param stage: How far the worker goes.
    """

    kb: str
    language: Language
    solver: str
    tokenizer: str
    stage: Stage = Stage.SOLVE

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        # the stage is part of the key through the phases it writes
        """Identity of the unit in the CSV.

        :return: kb, language, solver, tokenizer and ``scan`` for a tokenizer-only unit.
        """
        return (self.kb, self.language, self.solver, self.tokenizer,
                "scan" if self.stage is Stage.SCAN else "")


@dataclasses.dataclass
class Outcome:
    """Result of one process.

    :param rows: ``(mode, None, phase, seconds)`` tuples to store.
    :param status: ``ok``, ``timeout`` or ``failed``.
    """
    rows: list[tuple[str, int | None, str, float]]  # (mode, -, phase, seconds)
    status: str  # "ok", "timeout" or "failed"


# === Global functions ===


def kb_path(name: str) -> Path:
    """The graded file of a KB; the trivial KB is written on first use.

    :param name: File name of the KB, or :data:`TRIVIAL_NAME`.
    :return: Path of the file.
    """
    if name == TRIVIAL_NAME:
        PHASES_DIR.mkdir(parents=True, exist_ok=True)
        path = PHASES_DIR / "trivial.txt"
        path.write_text(TRIVIAL_KB)
        return path
    return DATA_DIR / "graded" / name


def read_done(path: Path = RAW_CSV) -> tuple[dict[tuple, set[int]], set[tuple]]:
    """Runs already stored per unit key, and the keys that were given up.

    :param path: The raw CSV; it may not exist yet.
    :return: Run numbers done per unit key, and the set of given-up unit keys.
    """
    runs: dict[tuple, set[int]] = {}
    given_up: set[tuple] = set()
    if not path.exists():
        return runs, given_up
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            scan = "scan" if r["phase"] in ("scan", "n_tokens") else ""
            key = (r["kb"], r["language"], r["solver"], r["tokenizer"], scan)
            if r["phase"] in ("timeout", "failed"):
                given_up.add(key)
            else:
                runs.setdefault(key, set()).add(int(r["run"]))
    return runs, given_up


def append_rows(rows: list[dict[str, typing.Any]], path: Path = RAW_CSV) -> None:
    """Append rows to the raw CSV, writing the header when the file is new.

    :param rows: One dict per row, with the :data:`CSV_COLUMNS` keys.
    :param path: The raw CSV.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def previous_average(unit: Unit) -> float | None:
    """Mean wall time of the unit in the earlier benchmark.

    :param unit: The unit; its KB, language and solver select the entry.
    :return: The mean in seconds, None when the earlier run had no answer.
    """
    if unit.language is Language.JAVA:
        data = json.loads((RESULTS_DIR / "graded_java_benchmark_results.json").read_text())
        stats = data.get(unit.kb, {})
    else:
        data = json.loads((RESULTS_DIR / "graded_benchmark_results.json").read_text())
        stats = data.get(unit.kb, {}).get(Provider(unit.solver).display, {})
    return stats.get("avg") if stats else None


def estimate_seconds(unit: Unit, cfg: BenchmarkConfig, warm_max_s: float) -> tuple[float, float]:
    """Expected and worst-case running time of a unit, from the earlier results.

    A unit that failed before counts one timeout (it is then given up); a unit
    without solver (tokenizers) is a fixed guess of 30 s per run.

    :param unit: The unit.
    :param cfg: Runs and timeout of the benchmark.
    :param warm_max_s: Cold time up to which the warm pass doubles the time.
    :return: ``(expected, worst)`` seconds for ``cfg.runs`` runs.
    """
    timeout = cfg.timeout_min * 60.0
    worst = cfg.runs * timeout
    if unit.kb == TRIVIAL_NAME:
        return cfg.runs * 5.0, cfg.runs * 5.0
    if unit.solver == NO_SOLVER:  # tokenizer units stop before the solver: a guess
        return cfg.runs * 30.0, cfg.runs * 30.0
    avg = previous_average(unit)
    if avg is None:  # it failed before: one timeout and the unit is given up
        return timeout, worst
    warm = 2.0 if avg <= warm_max_s and unit.language is Language.PYTHON else 1.0
    return cfg.runs * avg * warm, worst


def run_process(
    cmd: list[str], timeout_s: float, env_extra: dict[str, str] | None = None
) -> tuple[str, str, float, str]:
    """Run a command and measure its wall clock.

    :param cmd: The command line.
    :param timeout_s: Seconds after which the process is killed.
    :param env_extra: Extra environment variables; ``BENCH_SPAWN_TS`` is always set.
    :return: stdout, stderr, wall seconds and the status (ok / timeout / failed).
    """
    import os

    # the wall-clock stamp at spawn lets the worker compute its own startup time
    env = {**os.environ, **(env_extra or {}), "BENCH_SPAWN_TS": repr(time.time())}
    # wall clock of the whole process, from the spawn to the exit
    t0 = time.perf_counter()
    try:
        done = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, cwd=ROOT, env=env
        )
    except subprocess.TimeoutExpired as e:
        # killed at the timeout: keep what the process printed (the cold rows, if any)
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return out, "", time.perf_counter() - t0, "timeout"
    # a non-zero exit code is a failure
    status = "ok" if done.returncode == 0 else "failed"
    return done.stdout, done.stderr, time.perf_counter() - t0, status


def python_run(unit: Unit, timeout_s: float, warm_max_s: float) -> Outcome:
    """One fresh process of ``phase_worker.py``; rows of the cold and warm pass.

    :param unit: The unit to run.
    :param timeout_s: Seconds after which the process is killed.
    :param warm_max_s: Passed to the worker: no warm pass after a slower cold pass.
    :return: The rows to store and the status; a timeout after the cold pass keeps the cold rows.
    """
    # worker command line: a fresh interpreter per run
    cmd = [sys.executable, str(WORKER), str(kb_path(unit.kb)),
           "--tokenizer", unit.tokenizer, "--stop-after", unit.stage.value,
           "--warm-max-s", str(warm_max_s)]
    if unit.solver != NO_SOLVER:
        cmd += ["--provider", unit.solver]
    if unit.stage is not Stage.SOLVE:
        cmd.append("--no-warm")
    out, err, wall, status = run_process(cmd, timeout_s)
    # the worker prints one JSON line per pass ("cold", "warm", "end")
    lines = [json.loads(l[len(MARKER):]) for l in out.splitlines() if l.startswith(MARKER)]
    by_mode = {l["mode"]: l for l in lines}
    cold = by_mode.get("cold")
    # no cold line, or a solve without an answer: the run failed
    if cold is None or (unit.stage is Stage.SOLVE and (
            not cold["answers"] or any("None" in a for a in cold["answers"]))):
        if status == "ok":
            status = "failed"
        logger.warning(f"{unit.kb} {unit.solver} {unit.tokenizer}: {status}\n{err[-400:]}")
        return Outcome([], status)
    # rows of the cold pass: phases, startup and import (tokens for a tokenizer-only run)
    rows: list[tuple[str, int | None, str, float]] = []
    for phase, seconds in cold["phases"].items():
        rows.append(("cold", None, "scan" if unit.stage is Stage.SCAN else phase, seconds))
    rows += [("cold", None, "startup", cold["startup"]), ("cold", None, "import", cold["import"])]
    if unit.stage is Stage.SCAN:
        rows.append(("cold", None, "n_tokens", float(cold["n_tokens"])))
    # rows of the warm pass, when there was one
    warm = by_mode.get("warm")
    if warm:
        rows += [("warm", None, p, s) for p, s in warm["phases"].items()]
    # the process ended: wall, residual and teardown can be computed
    end = by_mode.get("end")
    if end and status == "ok":
        # the warm pass runs in the same process: it is taken out of the wall
        # clock, so that "wall" is the cold run alone (comparable with the
        # earlier benchmark); the teardown after a warm pass can still differ
        warm_total = sum(warm["phases"].values()) if warm else 0.0
        cold_wall = wall - warm_total
        # everything the cold pass explains: startup, import and the phases (counts excluded)
        cold_sum = sum(s for m, _, p, s in rows
                       if m == "cold" and p != "n_tokens")
        # other = wall not explained by the timers (starting the solver process,
        # interpreter exit, ...); teardown = what the process did after the last line
        rows += [("cold", None, "other", cold_wall - cold_sum),
                 ("cold", None, "teardown", wall - cold["startup"] - end["worker_total"]),
                 ("cold", None, "wall", cold_wall),
                 ("cold", None, "process_wall", wall)]
    elif status == "timeout":
        # a warm pass that did not finish: keep the cold rows, no wall
        status = "ok"
    return Outcome(rows, status)


def java_run(unit: Unit, timeout_s: float) -> Outcome:
    """One run of ``run_fdl.sh``.

    :param unit: The unit to run.
    :param timeout_s: Seconds after which the process is killed.
    :return: ``wall`` and the ``Time (s)`` printed by the jar, and the status.
    """
    # the whole script: JVM start, load, parse, reasoning, solver, exit
    out, err, wall, status = run_process(["./run_fdl.sh", str(kb_path(unit.kb))], timeout_s)
    # a run counts only if the jar printed an answer
    ok = "Is KnowledgeBase satisfiable?" in out or "KnowledgeBase inconsistent" in out
    if status != "ok" or not ok:
        return Outcome([], "failed" if status == "ok" else status)
    rows = [("cold", None, "wall", wall)]
    # the time that the jar prints itself (last occurrence)
    reported = JAVA_TIME.findall(out)
    if reported:
        rows.append(("cold", None, "reported_time", float(reported[-1])))
    return Outcome(rows, "ok")


def build_units(
    parts: list[Part], solvers: list[Provider], names: list[str]
) -> list[Unit]:
    """The units of the benchmark: the trivial KB first, then each KB.

    Every KB of ``names`` is run, whatever the skip lists of the earlier
    benchmark say; every backend has a file to KB unit.

    :param parts: Parts to run.
    :param solvers: Solvers of the Python part.
    :param names: File names of the KBs (``selected_ontologies.json``).
    :return: The units, in the order they run.
    """
    units: list[Unit] = []
    # the trivial KB first: it gives the fixed costs of both languages
    kbs = [TRIVIAL_NAME, *names]
    # one Python unit per solver (Cython tokenizer) and one Java unit (Gurobi)
    for kb in kbs:
        if Part.PYTHON in parts:
            units += [Unit(kb, Language.PYTHON, s.value, Backend.CYTHON) for s in solvers]
        if Part.JAVA in parts:
            units.append(Unit(kb, Language.JAVA, Provider.GUROBI.value, "-"))
    # tokenizer units: scan alone with each backend, then file to KB
    if Part.TOKENIZERS in parts:
        for kb in names:
            units += [Unit(kb, Language.PYTHON, NO_SOLVER, b, Stage.SCAN) for b in Backend]
            units += [Unit(kb, Language.PYTHON, NO_SOLVER, b, Stage.PARSE) for b in Backend]
    return units


def run_phases(
    parts: list[Part],
    solvers: list[Provider],
    names: list[str],
    cfg: BenchmarkConfig,
    warm_max_s: float = 60.0,
    dry_run: bool = False,
) -> None:
    """Run the units that the CSV does not hold yet; log the estimate first.

    :param parts: Parts to run.
    :param solvers: Solvers of the Python part.
    :param names: File names of the KBs.
    :param cfg: Runs and timeout of the benchmark.
    :param warm_max_s: No warm pass after a cold pass slower than this (seconds).
    :param dry_run: Only log the estimate.
    """
    # resume: what the CSV already holds, and the units given up in earlier runs
    runs_done, given_up = read_done()
    units = [u for u in build_units(parts, solvers, names) if u.key not in given_up]
    # estimate of the runs still to do
    todo: list[tuple[Unit, int]] = []
    expected = worst = 0.0
    for unit in units:
        missing = cfg.runs - len(runs_done.get(unit.key, set()))
        if missing <= 0:
            continue
        exp, wst = estimate_seconds(unit, cfg, warm_max_s)
        exp, wst = exp * missing / cfg.runs, wst * missing / cfg.runs
        expected += exp
        worst += wst
        todo.append((unit, missing))
    logger.info(f"{len(todo)} unit(s) to run; estimated {expected / 3600:.2f} h "
                f"(worst case, every run times out: {worst / 3600:.1f} h)")
    if dry_run:
        return

    # run the missing runs of each unit, one fresh process per run
    for unit, missing in todo:
        done = runs_done.get(unit.key, set())
        for run in [r for r in range(cfg.runs) if r not in done][:missing]:
            logger.info(f"{unit.kb} {unit.language} {unit.solver} {unit.tokenizer} "
                        f"{unit.stage} run {run + 1}/{cfg.runs}")
            outcome = (java_run(unit, cfg.timeout_min * 60.0)
                       if unit.language is Language.JAVA
                       else python_run(unit, cfg.timeout_min * 60.0, warm_max_s))
            # a timeout or a failure is stored and the unit is given up
            base = {"kb": unit.kb, "language": unit.language, "solver": unit.solver,
                    "tokenizer": unit.tokenizer, "run": run}
            if outcome.status != "ok":
                append_rows([{**base, "mode": "cold", "phase": outcome.status,
                              "seconds": cfg.timeout_min * 60.0}])
                break  # the remaining runs are skipped: N/A in the tables
            # the rows are saved after every run, so an interrupted benchmark resumes
            append_rows([{**base, "mode": m, "phase": p, "seconds": s}
                         for m, _, p, s in outcome.rows])
