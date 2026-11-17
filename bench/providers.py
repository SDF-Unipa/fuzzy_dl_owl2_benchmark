"""
MILP providers of the Python port and the detection of the ones that are
installed. Nothing here imports ``fuzzy_dl_owl2``: every run is a subprocess.
"""

from __future__ import annotations

import enum
import functools
import glob
import importlib
import os
import platform
import shutil
import typing

# === Global functions ===


@functools.cache
def _has_modules(*names: str) -> bool:
    """True if every module can be imported (as the original benchmark checked)."""
    try:
        for name in names:
            importlib.import_module(name)
    except Exception:
        return False
    return True


def _cplex_usable() -> bool:
    """True if PuLP is installed and its CPLEX command can run the binary that was found."""
    path = find_cplex_executable()
    if path is None:
        return False
    try:
        import pulp

        return bool(pulp.CPLEX_CMD(path=path).available())
    except Exception:
        return False


def find_cplex_executable() -> typing.Optional[str]:
    """
    Locate the CPLEX command-line binary on macOS, Linux and Windows.

    Order: ``PATH``, the CPLEX environment variables, then the install
    directories of the platform (the home directory included).

    :return: Path of the binary, or None.
    """
    system = platform.system()
    exe_name = "cplex.exe" if system == "Windows" else "cplex"
    found = shutil.which(exe_name)
    if found:
        return found

    for env_var in ("CPLEX_STUDIO_BINARIES", "CPLEX_HOME", "CPLEX_STUDIO_DIR"):
        base = os.environ.get(env_var)
        if base:
            candidates = glob.glob(os.path.join(base, "**", exe_name), recursive=True)
            if candidates:
                return sorted(candidates)[-1]

    home = os.path.expanduser("~")
    if system == "Darwin":
        patterns = [
            "/Applications/CPLEX_Studio*/cplex/bin/*/cplex",
            f"{home}/Applications/CPLEX_Studio*/cplex/bin/*/cplex",
        ]
    elif system == "Windows":
        patterns = [
            r"C:\Program Files\IBM\ILOG\CPLEX_Studio*\cplex\bin\*\cplex.exe",
            r"C:\Program Files (x86)\IBM\ILOG\CPLEX_Studio*\cplex\bin\*\cplex.exe",
        ]
    else:
        patterns = [
            "/opt/ibm/ILOG/CPLEX_Studio*/cplex/bin/*/cplex",
            "/opt/CPLEX_Studio*/cplex/bin/*/cplex",
            f"{home}/CPLEX_Studio*/cplex/bin/*/cplex",
        ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[-1]
    return None


# === Classes ===


class Provider(enum.StrEnum):
    """
    MILP provider; the value is the name that ``run_queries.py`` accepts.
    """

    GUROBI = "gurobi"
    MIP = "mip"
    CBC = "cbc"
    CPLEX = "cplex"
    GLPK = "glpk"
    HIGHS = "highs"

    @property
    def display(self) -> str:
        """Name used as the key of the results (the one of the existing JSON files)."""
        return _DISPLAY[self]

    @classmethod
    def from_display(cls, label: str) -> "Provider":
        return next(p for p in cls if p.display == label)

    def is_available(self) -> bool:
        """True if the provider can be used on this machine."""
        if self is Provider.GUROBI:
            return _has_modules("gurobipy")
        if self is Provider.MIP:
            return _has_modules("mip", "pulp")
        if self is Provider.HIGHS:
            return _has_modules("highspy", "pulp")
        if self is Provider.CPLEX:
            return _cplex_usable()
        return _has_modules("pulp")  # CBC, GLPK


class ProviderSet(enum.StrEnum):
    """
    Which providers a benchmark covers, and in which file the results go: the
    ``others`` set saves to ``all_*`` files, seeded with the results of the
    selected set.
    """

    SELECTED = "selected"
    OTHERS = "others"

    @property
    def providers(self) -> tuple[Provider, ...]:
        return DEFAULT_PROVIDERS if self is ProviderSet.SELECTED else OTHER_PROVIDERS


_DISPLAY: dict[Provider, str] = {
    Provider.GUROBI: "Gurobi",
    Provider.MIP: "MIP",
    Provider.CBC: "CBC",
    Provider.CPLEX: "CPLEX",
    Provider.GLPK: "GLPK",
    Provider.HIGHS: "HiGHS",
}

# providers of the default benchmark
DEFAULT_PROVIDERS: tuple[Provider, ...] = (
    Provider.GUROBI,
    Provider.CBC,
    Provider.HIGHS,
)

# the other providers, benchmarked in the second stage
OTHER_PROVIDERS: tuple[Provider, ...] = tuple(
    p for p in Provider if p not in DEFAULT_PROVIDERS
)

