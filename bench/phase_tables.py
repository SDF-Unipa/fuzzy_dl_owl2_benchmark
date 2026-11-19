"""
LaTeX tables (booktabs) of the phase benchmark, written to ``tables/`` from
``results/phases/raw.csv``. Each file holds a bold header line and one line per
row, without the ``tabular`` environment (it is written around them by hand); the KB name is a
``\\multirow`` over its solver rows, so the tables need ``multirow``.
Cells are ``mean $\\pm$ sample standard deviation`` in seconds; the minimum of
a group is in bold; N/A is a unit that timed out, failed or was not run.
"""

from __future__ import annotations

import csv
import logging
import typing
from pathlib import Path

from .common import ROOT, compute_stats
from .phases import RAW_CSV, TRIVIAL_NAME, Backend, Language

# === Global variables ===

logger = logging.getLogger(__name__)

TABLES_DIR: Path = ROOT / "tables"
SOLVERS: tuple[str, ...] = ("gurobi", "cbc", "highs")
SOLVER_LABEL: dict[str, str] = {"gurobi": "Gurobi", "cbc": "CBC", "highs": "HiGHS"}
NA: str = r"\textbf{N/A}"
MIDRULE: str = r"\midrule"
NOBREAK: str = "\\*"  # marks a row that must not be followed by a page break

# phase columns of the cold Python tables, split in two so that each fits the
# text width: (header, phases summed into it)
PY_COLUMNS_PREPARATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Startup", ("startup",)),
    ("Import", ("import",)),
    ("Parse", ("tokenize", "parse")),
    ("Tableau", ("tableau",)),
)
PY_COLUMNS_SOLVING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Build", ("build",)),
    ("Solver", ("solver",)),
    ("Other", ("other",)),
    ("Wall", ("wall",)),
)
# warm table, split in two like the cold one
WARM_COLUMNS_PREPARATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Parse", ("tokenize", "parse")),
    ("Tableau", ("tableau",)),
)
WARM_COLUMNS_SOLVING: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Build", ("build",)),
    ("Solver", ("solver",)),
    ("Warm total", ("tokenize", "parse", "tableau", "build", "solver")),
)

Runs = list[dict[str, dict[str, float]]]  # per run: mode -> phase -> seconds

# === Global functions ===


def load(path: Path = RAW_CSV) -> dict[tuple[str, str, str, str], Runs]:
    """Rows of the CSV grouped by (kb, language, solver, tokenizer) and run.

    :param path: The raw CSV.
    :return: For each group the list of its runs, each ``{mode: {phase: seconds}}``; an empty list for a unit that was given up; empty without the file.
    """
    runs: dict[tuple[str, str, str, str], dict[int, dict[str, dict[str, float]]]] = {}
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        for r in csv.DictReader(fh):
            # group by configuration, then by run number, then by mode and phase
            key = (r["kb"], r["language"], r["solver"], r["tokenizer"])
            # a given-up unit has no values: the group exists, empty, and stays N/A
            if r["phase"] in ("timeout", "failed"):
                runs.setdefault(key, {})
                continue
            per_run = runs.setdefault(key, {}).setdefault(int(r["run"]), {})
            per_run.setdefault(r["mode"], {})[r["phase"]] = float(r["seconds"])
    return {k: [v[i] for i in sorted(v)] for k, v in runs.items()}


def values(runs: Runs | None, mode: str, phases: typing.Iterable[str]) -> list[float]:
    """Per-run sum of ``phases`` (a run lacking one of them is left out).

    :param runs: The runs of a group, None when it was not measured.
    :param mode: ``cold`` or ``warm``.
    :param phases: Phases summed into one value per run.
    :return: One value per run.
    """
    out: list[float] = []
    for run in runs or []:
        got = run.get(mode, {})
        # a run needs every phase of the sum, else it would be a partial sum
        if all(p in got for p in phases):
            out.append(sum(got[p] for p in phases))
    return out


def cell(vals: list[float], bold: bool = False, digits: int = 3) -> str:
    """One table cell: ``mean \\pm sample standard deviation``.

    :param vals: Values of the runs.
    :param bold: Print the cell in bold.
    :param digits: Decimals.
    :return: The LaTeX cell, N/A without values.
    """
    stats = compute_stats(vals)
    if not stats:
        return NA  # no value: timeout, failure or not run
    body = f"{stats['avg']:.{digits}f} \\pm {stats['std']:.{digits}f}"
    return f"$\\mathbf{{{body}}}$" if bold else f"${body}$"


def cells(groups: list[list[float]]) -> list[str]:
    """Cells of one comparable group, the smallest mean in bold.

    :param groups: The values of each member of the group.
    :return: One cell per member.
    """
    means = [compute_stats(g).get("avg") for g in groups]
    # the smallest mean among the members that have one
    known = [m for m in means if m is not None]
    best = min(known) if known else None
    return [cell(g, bold=(m is not None and m == best)) for g, m in zip(groups, means)]


def tabular(header: list[str], rows: list[list[str]]) -> str:
    """Header line and rows of a table, without the ``tabular`` environment.

    :param header: Column titles.
    :param rows: Cells of each row.
    :return: The LaTeX text: one bold header line, then one line per row.
    """
    # bold header line, then one line per row
    head = " & ".join(f"\\textbf{{{h}}}" for h in header) + r" \\"
    # a row that is only \\midrule separates two KBs
    lines = [head]
    for r in rows:
        if r == [MIDRULE]:
            lines.append(MIDRULE)
        elif r[-1] == NOBREAK:
            # no page break after this row: the rows of a KB stay together
            lines.append(" & ".join(r[:-1]) + r" \\*")
        else:
            lines.append(" & ".join(r) + r" \\")
    return "\n".join(lines) + "\n"


def kb_name(kb: str) -> str:
    """The KB name as written in a table.

    :param kb: File name, or :data:`TRIVIAL_NAME`.
    :return: ``\\path{...}`` for a file, the name itself for the trivial KB.
    """
    return kb if kb == TRIVIAL_NAME else f"\\path{{{kb}}}"


def kbs_of(data: dict, language: str) -> list[str]:
    """KBs measured for a language, the trivial one first.

    :param data: The grouped rows of :func:`load`.
    :param language: ``python`` or ``java``.
    :return: Sorted file names.
    """
    names = {k[0] for k in data if k[1] == language}
    return sorted(names, key=lambda n: (n != TRIVIAL_NAME, n))


def python_table(data: dict, columns: typing.Sequence, mode: str) -> str:
    """Table of the phases of the Python port, one row per KB and solver.

    :param data: The grouped rows of :func:`load`.
    :param columns: ``(title, phases summed)`` of each column.
    :param mode: ``cold`` or ``warm``.
    :return: The LaTeX text.
    """
    rows: list[list[str]] = []
    for kb in kbs_of(data, Language.PYTHON):
        # the three solvers of the KB, with the Cython tokenizer
        per_solver = {s: data.get((kb, Language.PYTHON, s, Backend.CYTHON)) for s in SOLVERS}
        if all(v is None for v in per_solver.values()):
            continue
        # one list of cells per column: the minimum is bold among the solvers
        by_col = [cells([values(per_solver[s], mode, ph) for s in SOLVERS]) for _, ph in columns]
        # one row per solver; the KB name only on the first
        for i, s in enumerate(SOLVERS):
            # the KB name spans the three solver rows and is vertically centred; it is
            # written in the last row with a negative count, so that the background
            # of \\rowcolors, painted row by row, does not cover it; the width ``=`` is the
            # one of the column, so that a long name wraps in a ``p`` column
            last = i == len(SOLVERS) - 1
            first = f"\\multirow{{-{len(SOLVERS)}}}{{=}}{{{kb_name(kb)}}}" if last else ""
            row = [first, SOLVER_LABEL[s], *(col[i] for col in by_col)]
            # a negative \\multirow is drawn upwards from the last row: a page break
            # inside the group would put the name over the header of the next page
            rows.append(row if last else [*row, NOBREAK])
        rows.append([MIDRULE])  # between this KB and the next
    if rows:
        rows.pop()  # no rule after the last KB: the table ends with its own
    header = ["KB", "Solver", *(h for h, _ in columns)]
    return tabular(header, rows)


def java_table(data: dict) -> str:
    """Java wall clock and ``Time (s)`` next to the Python figures with Gurobi.

    :param data: The grouped rows of :func:`load`.
    :return: The LaTeX text.
    """
    rows: list[list[str]] = []
    for kb in kbs_of(data, Language.JAVA):
        java = data.get((kb, Language.JAVA, "gurobi", "-"))
        py = data.get((kb, Language.PYTHON, "gurobi", Backend.CYTHON))
        # the original implementation's figures, then the port's with Gurobi
        rows.append([
            kb_name(kb),
            cell(values(java, "cold", ("wall",))),
            cell(values(java, "cold", ("reported_time",))),
            cell(values(py, "cold", ("wall",))),
            cell(values(py, "cold", ("startup", "import"))),
            cell(values(py, "cold", ("tableau", "build", "solver"))),
        ])
    header = ["KB", "Java wall", "Java Time (s)", "Python wall",
              "Python startup+import", "Python tableau+build+solver"]
    return tabular(header, rows)


def tokenizer_scan_table(data: dict) -> str:
    """Tokens and scan time per tokenizer backend (the CFFI stream check stays in the CSV only).

    :param data: The grouped rows of :func:`load`.
    :return: The LaTeX text.
    """
    rows: list[list[str]] = []
    order = (Backend.REGEX, Backend.CFFI, Backend.CYTHON)
    for kb in kbs_of(data, Language.PYTHON):
        units = {b: data.get((kb, Language.PYTHON, "-", b)) for b in order}
        if all(v is None for v in units.values()):
            continue
        # scan time of the three backends (minimum in bold)
        scan = cells([values(units[b], "cold", ("scan",)) for b in order])
        # number of tokens of the Cython scanner
        tokens = values(units[Backend.CYTHON], "cold", ("n_tokens",))
        rows.append([kb_name(kb), f"{int(tokens[0])}" if tokens else NA, *scan])
    header = ["KB", "Tokens", "Scan regex", "Scan CFFI", "Scan Cython"]
    return tabular(header, rows)


def tokenizer_file_table(data: dict) -> str:
    """Time from the file to the KB per tokenizer backend.

    :param data: The grouped rows of :func:`load`.
    :return: The LaTeX text.
    """
    rows: list[list[str]] = []
    order = (Backend.REGEX, Backend.CFFI, Backend.CYTHON)
    for kb in kbs_of(data, Language.PYTHON):
        units = {b: data.get((kb, Language.PYTHON, "-", b)) for b in order}
        if all(v is None for v in units.values()):
            continue
        # tokenizing plus parsing, minimum in bold among the three back ends
        parse = cells([values(units[b], "cold", ("tokenize", "parse")) for b in order])
        rows.append([kb_name(kb), *parse])
    header = ["KB", "File$\\to$KB regex", "File$\\to$KB CFFI", "File$\\to$KB Cython"]
    return tabular(header, rows)


def save_tables(out_dir: Path = TABLES_DIR, path: Path = RAW_CSV) -> None:
    """Write the seven tables of the phase benchmark.

    :param out_dir: Directory of the tables.
    :param path: The raw CSV.
    """
    # read the raw CSV once and write the four tables
    data = load(path)
    if not data:
        logger.warning(f"No rows in {path}: no table written.")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "phases_python_preparation.tex": python_table(data, PY_COLUMNS_PREPARATION, "cold"),
        "phases_python_solving.tex": python_table(data, PY_COLUMNS_SOLVING, "cold"),
        "phases_python_warm_preparation.tex": python_table(data, WARM_COLUMNS_PREPARATION, "warm"),
        "phases_python_warm_solving.tex": python_table(data, WARM_COLUMNS_SOLVING, "warm"),
        "phases_java.tex": java_table(data),
        "phases_tokenizers_scan.tex": tokenizer_scan_table(data),
        "phases_tokenizers_file_to_kb.tex": tokenizer_file_table(data),
    }
    for name, text in tables.items():
        (out_dir / name).write_text(text)
        logger.info(f"Saved {out_dir / name}")
