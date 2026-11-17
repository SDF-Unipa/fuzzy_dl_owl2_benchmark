"""
One ``Runner`` per tool. A runner knows only what differs between the tools:
where its files are, how to launch one run, and when a run is a success. The
loop, the statistics and the persistence are in ``engine``.
"""

from __future__ import annotations

import abc
import logging
import os
import subprocess
import sys
import typing
from pathlib import Path

from .common import (
    DATA_DIR,
    DELOREAN_DIR,
    RESULTS_DIR,
    ROOT,
    OntologyKind,
    Tool,
)
from .providers import Provider, ProviderSet, find_cplex_executable

# === Global variables ===

logger = logging.getLogger(__name__)

# === Classes ===


class Runner(abc.ABC):
    """
    :cvar tool: The tool this runner launches.
    :cvar extension: Extension of its input files.
    :cvar results_prefix: Prefix of the results file (``<prefix>benchmark_results.json``).
    :cvar skip_name: Name of the skip list (``<skip_name>_skip.json``).
    :cvar discard_on_giveup: Drop the times collected so far when a unit is given up.
    :cvar missing_files_is_error: Raise (True) or only log (False) when there are no input files.
    """

    tool: typing.ClassVar[Tool]
    extension: typing.ClassVar[str]
    results_prefix: typing.ClassVar[str] = ""
    skip_name: typing.ClassVar[str]
    discard_on_giveup: typing.ClassVar[bool] = False
    missing_files_is_error: typing.ClassVar[bool] = True

    def __init__(
        self,
        kind: OntologyKind = OntologyKind.ORIGINAL,
        results_dir: Path = RESULTS_DIR,
        data_dir: typing.Optional[Path] = None,
    ) -> None:
        self.kind = kind
        self.results_dir = results_dir
        self.base_data_dir = data_dir if data_dir is not None else self.default_data_dir

    # --- files and names ---

    @property
    def default_data_dir(self) -> Path:
        return DATA_DIR

    @property
    def data_dir(self) -> Path:
        base = self.base_data_dir
        return base / "graded" if self.kind is OntologyKind.GRADED else base

    @property
    def kind_prefix(self) -> str:
        return "graded_" if self.kind is OntologyKind.GRADED else ""

    @property
    def file_prefix(self) -> str:
        """Prefix of the result and skip files, before the kind prefix."""
        return ""

    @property
    def results_path(self) -> Path:
        return (
            self.results_dir
            / f"{self.file_prefix}{self.kind_prefix}{self.results_prefix}benchmark_results.json"
        )

    @property
    def skip_path(self) -> Path:
        return (
            self.results_dir
            / f"{self.file_prefix}{self.kind_prefix}{self.skip_name}_skip.json"
        )

    @property
    def seed_path(self) -> typing.Optional[Path]:
        """Results file copied into a new results file, or None."""
        return None

    @property
    def file_level_resume(self) -> bool:
        """True: a file with any result is skipped; False: each label is checked."""
        return True

    def files(self) -> list[Path]:
        return sorted(self.data_dir.glob(f"*{self.extension}"))

    def resolve(self, names: typing.Iterable[str]) -> list[Path]:
        """Input files by name, relative to the data directory."""
        return [self.data_dir / n for n in names]

    # --- what a subclass decides ---

    def labels(self) -> list[typing.Optional[str]]:
        """Sub-units of a file (the MILP provider); ``[None]`` for none."""
        return [None]

    @abc.abstractmethod
    def command(self, file: Path, label: typing.Optional[str]) -> list[str]:
        """Command line of one run."""

    @abc.abstractmethod
    def is_success(self, proc: subprocess.CompletedProcess[str]) -> bool:
        """True if the finished process produced a valid answer."""

    def env(self) -> typing.Optional[dict[str, str]]:
        """Environment of the run; None inherits the current one."""
        return None


class PythonRunner(Runner):
    """The Python port: one subprocess of ``run_queries.py`` per run."""

    tool = Tool.PYTHON
    extension = ".txt"
    skip_name = "python"

    def __init__(
        self,
        provider_set: ProviderSet = ProviderSet.SELECTED,
        providers: typing.Optional[typing.Sequence[Provider]] = None,
        **kwargs: typing.Any,
    ) -> None:
        """
        :param provider_set: Which set of providers this benchmark belongs to; it
            decides the default providers and the names of the files.
        :param providers: Explicit providers, instead of those of the set.
        """
        super().__init__(**kwargs)
        self.provider_set = provider_set
        wanted = list(providers) if providers is not None else provider_set.providers
        self.providers = [p for p in wanted if p.is_available()]
        missing = [p.display for p in wanted if p not in self.providers]
        if missing:
            logger.warning(
                f"Providers not available, skipped: {', '.join(missing)}"
            )
        if not self.providers:
            raise RuntimeError("No MILP provider is available.")

    @property
    def file_prefix(self) -> str:
        # the other providers are saved in ``all_`` files
        return "" if self.provider_set is ProviderSet.SELECTED else "all_"

    @property
    def seed_path(self) -> typing.Optional[Path]:
        # an ``all_`` file starts from the results of the selected providers
        if self.provider_set is ProviderSet.SELECTED:
            return None
        return self.results_dir / "graded_benchmark_results.json"

    @property
    def file_level_resume(self) -> bool:
        return self.provider_set is ProviderSet.SELECTED

    def labels(self) -> list[typing.Optional[str]]:
        return [p.display for p in self.providers]

    def command(self, file: Path, label: typing.Optional[str]) -> list[str]:
        provider = Provider.from_display(typing.cast(str, label))
        return [
            sys.executable,
            str(ROOT / "run_queries.py"),
            provider.value,
            str(file.resolve()),
        ]

    def is_success(self, proc: subprocess.CompletedProcess[str]) -> bool:
        # run_queries.py prints "<query><solution>"; a missing solution prints None
        return "None" not in proc.stdout

    def env(self) -> typing.Optional[dict[str, str]]:
        # the reasoner finds CPLEX through these variables; give it the binary
        # that this process found, wherever it is installed
        cplex = find_cplex_executable()
        if cplex is None or Provider.CPLEX not in self.providers:
            return None
        return {**os.environ, "CPLEX_STUDIO_BINARIES": str(Path(cplex).parent)}


class OriginalRunner(Runner):
    """The original implementation, through ``run_fdl.sh``."""

    tool = Tool.ORIGINAL
    extension = ".txt"
    results_prefix = "java_"
    skip_name = "java"
    discard_on_giveup = True  # times of a unit that was given up are not reliable

    SUCCESS_MARKERS: typing.ClassVar[tuple[str, ...]] = (
        "Is KnowledgeBase satisfiable?",
        "KnowledgeBase inconsistent",
    )

    def command(self, file: Path, label: typing.Optional[str]) -> list[str]:
        return [str(ROOT / "run_fdl.sh"), str(file.resolve())]

    def is_success(self, proc: subprocess.CompletedProcess[str]) -> bool:
        return any(m in proc.stdout for m in self.SUCCESS_MARKERS)


class DeloreanRunner(Runner):
    """DeLorean, through ``run_delorean.sh``; its inputs are the converted ``.dlr`` files."""

    tool = Tool.DELOREAN
    extension = ".dlr"
    results_prefix = "delorean_"
    skip_name = "delorean"
    missing_files_is_error = False

    DEGREES_NUMBER: typing.ClassVar[int] = 101

    @property
    def default_data_dir(self) -> Path:
        return DELOREAN_DIR

    @property
    def data_dir(self) -> Path:
        return self.base_data_dir

    @property
    def kind_prefix(self) -> str:
        return ""  # DeLorean has one set of files, not a graded one

    def command(self, file: Path, label: typing.Optional[str]) -> list[str]:
        return [
            str(ROOT / "run_delorean.sh"),
            str(file.resolve()),
            str(self.DEGREES_NUMBER),
        ]

    def is_success(self, proc: subprocess.CompletedProcess[str]) -> bool:
        return not proc.stderr


RUNNERS: dict[Tool, type[Runner]] = {
    Tool.PYTHON: PythonRunner,
    Tool.ORIGINAL: OriginalRunner,
    Tool.DELOREAN: DeloreanRunner,
}
