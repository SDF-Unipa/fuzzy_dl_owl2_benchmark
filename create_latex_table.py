"""
Build the LaTeX benchmark tables from the JSON files in ``results/``.

Four tables are produced, one per ``Mode``:

* BASE:     original implementation vs Gurobi / CBC / HiGHS (+ speedup column).
* DELOREAN: original implementation vs Gurobi vs DeLorean (+ fastest family).
* ALL:      original implementation vs every Python MILP provider (+ speedup).
* CLASSICAL: classical DL reasoner (Pellet) vs original implementation vs Gurobi
             vs DeLorean (+ fastest family), on the KBs of the classical results.

Pipeline: ``build_row`` (data -> Row) -> ``render_row`` (Row -> LaTeX line)
-> ``print_summary`` (statistics on stdout). Nothing else touches the data.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# ---------------------------------------------------------------------------
# GLOBAL VARIABLES
# ---------------------------------------------------------------------------

# Directory containing the JSON result files.
RESULTS_DIR: Path = Path(__file__).parent / "results"

# Cell printed when a solver has no valid result.
NA: str = "\\textbf{N/A}"

# ---------------------------------------------------------------------------
# CLASSES
# ---------------------------------------------------------------------------


class Mode(StrEnum):
    """Which table to build. Determines the set of solvers and columns used."""

    BASE = "base"
    DELOREAN = "delorean"
    ALL = "all"
    CLASSICAL = "classical"


class Solver(StrEnum):
    """
    Table columns. These are the solvers that appear as columns in the LaTeX tables.
    Python values are the keys used in the JSON files.
    """

    ORIGINAL = "original"
    DELOREAN = "delorean"
    CLASSICAL = "classical"
    GUROBI = "Gurobi"
    CBC = "CBC"
    HIGHS = "HiGHS"
    MIP = "MIP"
    CPLEX = "CPLEX"
    GLPK = "GLPK"


@dataclass
class Row:
    """One KB of the table. Represents a single row with solver results and timings."""

    kb: str  # knowledge base name
    cells: dict[Solver, str]  # formatted "avg \pm std", or NA if no result
    times: dict[
        Solver, float
    ]  # average times; inf = no result (original/DeLorean), 0.0 = no result (Python)
    original_avg: float  # 0.0 when the original implementation has no result (Java)
    python_times: list[float]  # positive Python averages only (0.0 means no result)
    speedup: (
        float  # speedup of the original implementation over the fastest Python solver
    )
    speedup_str: str  # formatted speedup string for the table

    @property
    def best(self) -> Solver:
        """Fastest solver; on a tie the first inserted wins."""
        return min(self.times, key=self.times.get)

    @property
    def best_time(self) -> float:
        return self.times[self.best]


# ---------------------------------------------------------------------------
# INITIALISATION
# (tables keyed by the enums above, so they must come after the classes)
# ---------------------------------------------------------------------------

# Python solver groups for different modes.
_PYTHON_BASE = (Solver.GUROBI, Solver.CBC, Solver.HIGHS)
# All Python solvers considered in the "all" mode.
_PYTHON_ALL = _PYTHON_BASE + (Solver.MIP, Solver.CPLEX, Solver.GLPK)

# Python solvers that compete for "best" in each mode.
PYTHON_SOLVERS: dict[Mode, tuple[Solver, ...]] = {
    Mode.BASE: _PYTHON_BASE,
    Mode.DELOREAN: _PYTHON_BASE,
    Mode.ALL: _PYTHON_ALL,
    Mode.CLASSICAL: (Solver.GUROBI,),
}

# Columns printed in each mode (a subset of the competing solvers).
COLUMNS: dict[Mode, tuple[Solver, ...]] = {
    Mode.BASE: (Solver.ORIGINAL, *_PYTHON_BASE),
    Mode.DELOREAN: (Solver.ORIGINAL, Solver.GUROBI, Solver.DELOREAN),
    Mode.ALL: (Solver.ORIGINAL, *_PYTHON_ALL),
    Mode.CLASSICAL: (
        Solver.CLASSICAL,
        Solver.ORIGINAL,
        Solver.GUROBI,
        Solver.DELOREAN,
    ),
}

# Modes whose last column is the family of the fastest solver.
FAMILY_MODES: tuple[Mode, ...] = (Mode.DELOREAN, Mode.CLASSICAL)

# Output file names for each mode.
OUTPUT_NAMES: dict[Mode, str] = {
    Mode.BASE: "python_java_benchmark_table.tex",
    Mode.DELOREAN: "python_java_delorean_benchmark_table.tex",
    Mode.ALL: "all_python_java_benchmark_table.tex",
    Mode.CLASSICAL: "classical_python_java_delorean_benchmark_table.tex",
}

# Title of each solver column (the first column, the KB, is titled in `render_header`).
COLUMN_TITLES: dict[Solver, str] = {
    Solver.ORIGINAL: "Java",
    Solver.DELOREAN: "DeLorean",
    Solver.CLASSICAL: "Pellet",
    **{s: s.value for s in _PYTHON_ALL},
}

# Last column of the DELOREAN table: family of the fastest solver.
FAMILY_HEADER: dict[Solver, str] = {
    Solver.ORIGINAL: "\\textbf{Java}",
    Solver.DELOREAN: "\\textbf{DeLorean}",
    Solver.CLASSICAL: "\\textbf{Pellet}",
    **{s: "\\textbf{Python}" for s in _PYTHON_ALL},
}


# ---------------------------------------------------------------------------
# GLOBAL FUNCTIONS
# ---------------------------------------------------------------------------


def load_json_file(file_path: Path) -> dict:
    """
    Load a JSON file as a dict. A missing file is an empty result set.

    :param file_path: Path to the JSON file.
    :return: Dictionary with the JSON content, or empty if the file is missing.
    """
    if not file_path.exists():
        # If the file does not exist, return an empty dictionary.
        return {}
    # Read the file content and parse it as JSON.
    return json.loads(file_path.read_text())


def load_with_graded(name: str, graded_name: str) -> dict:
    """
    Merge the plain and the graded result files (graded wins on a clash).

    :param name: Base name of the plain result file (without extension).
    :param graded_name: Base name of the graded result file (without extension).
    :return: Merged dictionary with graded results taking precedence.
    """

    # Load the plain and graded JSON files and merge them, with graded taking precedence.
    return {
        **load_json_file(RESULTS_DIR / f"{name}.json"),
        **load_json_file(RESULTS_DIR / f"{graded_name}.json"),
    }


def dlr_to_txt_keys(results: dict) -> dict:
    """
    Rename the DeLorean entries ``X.dlr`` / ``X.100.dlr`` to ``X.txt`` / ``X.100.txt``,
    the names of the same KB in the other result files.

    :param results: DeLorean results keyed by ``.dlr`` file name.
    :return: The same entries keyed by ``.txt`` file name.
    """
    return {
        (f"{k[: -len('.dlr')]}.txt" if k.endswith(".dlr") else k): v
        for k, v in results.items()
    }


def format_cell(data: dict[str, float] | None) -> tuple[str, float]:
    """
    Format one solver result as ``"avg \\pm std"``.

    :param data: ``{"avg": ..., "std": ...}`` or empty / None.
    :return: (LaTeX text, average time). A missing or non-positive average
             gives (``NA``, 0.0), so the caller never sees a negative time.
    """
    data = data or {}
    avg = data.get("avg", 0)
    if avg <= 0:
        # A non-positive average is treated as missing.
        return NA, 0.0
    # Extract the standard deviation, defaulting to 0 if missing.
    std = data.get("std", 0)
    # Otherwise, format the average and standard deviation as a LaTeX cell.
    # Format the cell as "avg ± std" for LaTeX.
    return f"{avg:.2f} \\pm {std:.2f}", avg


def compute_speedup(
    original_avg: float, python_times: list[float]
) -> tuple[float, str]:
    """
    Speedup of the best Python solver over the original implementation.

    :return: (numeric value used for sorting and counting, LaTeX cell).
             The value is ``inf`` when the Python side wins by default
             (the original implementation has no result).
    """
    # If both the original and Python times are available, compute the speedup.
    if original_avg > 0 and python_times:
        ratio = original_avg / min(python_times)  # lower time is better
        return ratio, f"${ratio:.3f}$"
    # If only the original implementation answered, the speedup is zero.
    if original_avg > 0:
        # Only the original implementation answered.
        # The speedup is zero because no Python solver was faster.
        return math.inf, "$0$"
    # No original result: Python wins if it has any, otherwise nobody does.
    return (math.inf if python_times else 0.0), NA


def build_row(
    kb: str,
    python_data: dict[str, dict[str, float]],
    original_data: dict[str, float],
    delorean_data: dict[str, float],
    mode: Mode,
    classical_data: dict[str, float] | None = None,
) -> Row | None:
    """
    Collect everything needed to print one KB.

    :return: the Row, or None when no solver has any data for this KB.
    """
    if (
        not any(python_data.values())
        and not original_data
        and not delorean_data
        and not classical_data
    ):
        return None

    # Initialize dictionaries to hold LaTeX cells and numeric times for each solver.
    cells: dict[Solver, str] = {}
    # Insertion order matters: on a tie the first solver inserted is "best".
    times: dict[Solver, float] = {}

    # Format the original and DeLorean solver results into LaTeX cells and record their times.
    cells[Solver.ORIGINAL], original_avg = format_cell(original_data)
    times[Solver.ORIGINAL] = original_avg if original_avg > 0 else math.inf

    cells[Solver.DELOREAN], delorean_avg = format_cell(delorean_data)
    times[Solver.DELOREAN] = delorean_avg if delorean_avg > 0 else math.inf

    # The classical reasoner has the same result shape as the other solvers.
    cells[Solver.CLASSICAL], classical_avg = format_cell(classical_data)
    times[Solver.CLASSICAL] = classical_avg if classical_avg > 0 else math.inf

    # Format the Python solver results into LaTeX cells and record their times.
    python_times: list[float] = []
    for solver in PYTHON_SOLVERS[mode]:
        cells[solver], avg = format_cell(python_data.get(solver.value, {}))
        if avg > 0:
            times[solver] = avg
            python_times.append(avg)

    # Compute the speedup of the best Python solver over the original implementation.
    speedup, speedup_str = compute_speedup(original_avg, python_times)

    # Build and return the Row object containing all relevant information for this KB.
    # This includes the KB name, the LaTeX cells, the numeric times, the original average,
    # the list of Python times, and the computed speedup.
    return Row(
        kb=kb,
        cells=cells,
        times=times,
        original_avg=original_avg,
        python_times=python_times,
        speedup=speedup,
        speedup_str=speedup_str,
    )


def render_row(row: Row, mode: Mode) -> str:
    """
    Render one table line: bold the fastest cell, wrap in math mode.

    :param row: the Row object containing all relevant information for this KB.
    :param mode: the Mode indicating which set of solvers to consider.

    :return: the LaTeX string representing the table row for this KB.
    """
    cells = dict(row.cells)
    # Format the best cell in bold.
    cells[row.best] = f"\\mathbf{{{cells[row.best]}}}"

    for solver, text in cells.items():
        # Original / DeLorean "N/A" stay as plain text; Python cells are
        # always math, so "$\textbf{N/A}$" is kept for them.
        is_other = solver in (Solver.ORIGINAL, Solver.DELOREAN, Solver.CLASSICAL)
        if not (is_other and text == NA):
            cells[solver] = f"${text}$"

    if mode in FAMILY_MODES:
        # In these modes, the tail is determined by the family header of the best solver.
        tail = FAMILY_HEADER.get(row.best, FAMILY_HEADER[Solver.GUROBI])
    else:
        # In other modes, the tail is simply the speedup string.
        tail = row.speedup_str

    # Build the list of columns for the LaTeX table row.
    columns = [f"\\path{{{row.kb}}}", *(cells[s] for s in COLUMNS[mode]), tail]
    # Join the columns with " & " and append the LaTeX row ending.
    return " & ".join(columns) + " \\\\"


def render_header(mode: Mode) -> str:
    """
    Render the title line of one table: KB, one title per solver column, then the
    title of the last column (the fastest family or the speedup).

    :param mode: the Mode indicating which columns the table has.
    :return: the LaTeX string of the header row.
    """
    tail = "Fastest" if mode in FAMILY_MODES else "Speedup"
    titles = ["KB", *(COLUMN_TITLES[s] for s in COLUMNS[mode]), tail]
    return " & ".join(f"\\textbf{{{t}}}" for t in titles) + " \\\\"


def print_summary(rows: list[Row]) -> None:
    """Print how often, and by how much, Python beat the original."""
    # Determine which rows represent cases where Python solvers were faster than the original solver.
    wins = [r for r in rows if r.speedup > 1]

    print(
        f"Python solvers were faster than other solver in {len(wins)} out of {len(rows)} cases."
    )

    # Print the outperformed cases.
    outperformed = [
        f"\\path{{{r.kb}}}" for r in rows if r.speedup > 1 or r.original_avg == 0
    ]
    if outperformed:
        print(
            "Cases where Python outperformed other solver or other solver data was missing:"
        )
        print(", ".join(outperformed))

    # Gap between the original and the best Python solver (0 if no Python time).
    gaps = [
        abs(r.original_avg - min(r.python_times)) if r.python_times else 0
        for r in rows
        if r.original_avg > 0
    ]
    if gaps:
        print(
            "Maximum time difference between other solver and best Python solver:",
            max(gaps),
        )
        print(
            "Minimum time difference between other solver and best Python solver:",
            min(gaps),
        )


def generate_latex_table(
    python_results: dict[str, dict[str, dict[str, float]]],
    original_results: dict[str, dict[str, float]],
    delorean_results: dict[str, dict[str, float]],
    mode: Mode,
    classical_results: dict[str, dict[str, float]] | None = None,
    only: set[str] | None = None,
) -> str:
    """
    Build the table body for one mode (rows sorted by speedup, descending).

    :param python_results: ``{kb: {provider: {"avg", "std"}}}``.
    :param original_results: ``{kb: {"avg", "std"}}`` of the original implementation.
    :param delorean_results: same shape, keyed by ``.txt`` name; only read in
        DELOREAN and CLASSICAL mode.
    :param only: when given, the table keeps only these KBs (``.txt`` names).
    :param classical_results: ``{kb: {"avg", "std"}}`` of the classical reasoner; only
        read in CLASSICAL mode.
    """
    if mode not in FAMILY_MODES:
        # Without a DeLorean column, ignore any DeLorean results.
        delorean_results = {}
    classical_results = classical_results if mode == Mode.CLASSICAL else {}

    if mode == Mode.CLASSICAL:
        # The comparison covers exactly the KBs that the classical reasoner ran.
        kb_names = set(classical_results)
    else:
        # Collect all KB names from the Python, original, and DeLorean results.
        kb_names = set(python_results) | set(original_results) | set(delorean_results)

    if only is not None:
        kb_names &= only

    # Build rows for each KB, using the build_row function.
    rows: list[Row] = []
    for kb in sorted(kb_names, key=str.lower):
        # Build a row for the current KB.
        row = build_row(
            kb,
            python_results.get(kb, {}),
            original_results.get(kb, {}),
            delorean_results.get(kb, {}),
            mode,
            classical_results.get(kb, {}),
        )
        if row is None:
            # Skip this KB if no valid row could be built.
            continue
        rows.append(row)

    # Print a summary of the collected rows before sorting and rendering.
    print_summary(rows)

    # Stable sort: rows with equal keys keep the alphabetical order.
    rows.sort(key=lambda r: (r.speedup, r.best_time), reverse=True)
    # Render the sorted rows into LaTeX table format.
    return "\n".join([render_header(mode), *(render_row(r, mode) for r in rows)])


def load_selected_kbs() -> set[str]:
    """KBs of ``selected_ontologies.json``, as ``.txt`` names (DeLorean ones are renamed on load)."""
    return set(
        load_json_file(RESULTS_DIR / "selected_ontologies.json").get("files", [])
    )


def save_table(mode: Mode, out_dir: Path, reduced: bool = False) -> None:
    """
    Load the results of one mode, generate its table and write it.

    :param reduced: keep only the KBs of ``selected_ontologies.json``; the file
        name gets the suffix ``_selected``.
    """
    # Load the benchmark results for the specified mode.
    suffix = "all_" if mode == Mode.ALL else ""
    # Load the Python, original, and DeLorean benchmark results.
    python_results = load_with_graded(
        f"{suffix}benchmark_results", f"{suffix}graded_benchmark_results"
    )
    original_results = load_with_graded(
        "java_benchmark_results", "graded_java_benchmark_results"
    )
    delorean_results = (
        dlr_to_txt_keys(load_json_file(RESULTS_DIR / "delorean_benchmark_results.json"))
        if mode in FAMILY_MODES
        else {}
    )
    classical_results = (
        load_json_file(RESULTS_DIR / "classical_dl_sat_results.json")
        if mode == Mode.CLASSICAL
        else {}
    )

    # Generate the LaTeX table from the loaded results.
    table = generate_latex_table(
        python_results,
        original_results,
        delorean_results,
        mode,
        classical_results,
        load_selected_kbs() if reduced else None,
    )
    # Write the generated LaTeX table to the output file.
    name = OUTPUT_NAMES[mode]
    output_file = out_dir / (name.replace(".tex", "_selected.tex") if reduced else name)
    output_file.write_text(table)
    print(f"LaTeX table generated and saved to {output_file}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------


def main() -> None:
    """
    Generate LaTeX tables for benchmark results in different modes.
    The tables are saved to the specified output directory.

    The output directory can be specified using the --out command-line argument.

    Command-line usage:
        With the default output directory:
            python create_latex_table_v2.py
        With a custom output directory:
            python create_latex_table_v2.py --out <output_directory>
    """

    # Parse command-line arguments and generate LaTeX tables for each mode.
    parser = argparse.ArgumentParser(description=__doc__)
    # Add the output directory argument.
    # The output directory specifies where the generated .tex files will be saved.
    # Default is the directory next to this script.
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "tables",
        help="directory for the .tex files (default: next to this script)",
    )
    # Parse the command-line arguments.
    args = parser.parse_args()
    # Create the output directory if it doesn't exist.
    args.out.mkdir(parents=True, exist_ok=True)

    for mode in (Mode.BASE, Mode.DELOREAN, Mode.ALL, Mode.CLASSICAL):
        # Generate and save the LaTeX table for the current mode.
        if mode != Mode.ALL:
            save_table(mode, args.out)
        if mode != Mode.CLASSICAL:
            # The classical table already covers a fixed set of KBs.
            save_table(mode, args.out, reduced=True)


if __name__ == "__main__":
    # Call the main function to execute the script.
    main()
