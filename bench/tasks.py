"""
Tasks around the benchmark: the OWL to DeLorean conversion, the list of the
selected ontologies, and the classical reasoner on the OWL files.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path

from .common import (
    DELOREAN_DIR,
    OWL_DIR,
    RESULTS_DIR,
    ROOT,
    compute_stats,
)

# === Global variables ===

logger = logging.getLogger(__name__)

# === Global functions ===


def convert_owl_to_delorean(input_file: Path, output_file: Path) -> bool:
    """
    Convert an OWL file with the FuzzyOwl2toDeLorean tool; a failed conversion
    leaves no output file.

    :return: True on success.
    """
    logger.info(f"Converting {input_file.name}")
    ok = True
    try:
        result = subprocess.run(
            [
                "java",
                "-cp",
                "./FuzzyOwl2toDeLorean.jar:./lib/*",
                "FuzzyOwl2toDeLorean",
                str(input_file.absolute()),
                str(output_file.absolute()),
            ],
            check=True,
            text=True,
            capture_output=True,
            cwd=ROOT,
        )
        if result.stdout:
            logger.info(f"Output for {input_file.name}: {result.stdout}")
        if result.stderr:
            logger.error(f"Error output for {input_file.name}: {result.stderr}")
            ok = False
    except Exception as e:
        logger.error(f"Cannot convert {input_file.name}: {e}")
        ok = False
    if not ok:
        output_file.unlink(missing_ok=True)
    return ok


def convert_all(
    owl_dir: Path = OWL_DIR, out_dir: Path = DELOREAN_DIR
) -> dict[str, bool]:
    """Convert every ``.owl`` file under ``owl_dir`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return {
        f.name: convert_owl_to_delorean(f, out_dir / f.with_suffix(".dlr").name)
        for f in sorted(owl_dir.glob("**/*.owl"))
    }


def load_selected_ontologies(results_dir: Path = RESULTS_DIR) -> list[str]:
    """File names of ``selected_ontologies.json`` (a list written by hand)."""
    return json.loads((results_dir / "selected_ontologies.json").read_text())["files"]


def run_classical_dl_sat(
    names: list[str], runs: int = 10, results_dir: Path = RESULTS_DIR
) -> dict[str, dict[str, float]]:
    """
    Time the classical reasoner (Pellet) on the OWL file of each benchmark file.

    The OWL file of ``X.100.txt`` is ``data/owl/X.owl`` and its result is saved
    under ``X.txt``, the name of the same KB without the degree of grading.

    :param names: Names of the benchmark files.
    :param runs: Reasoning runs per file.
    :return: Statistics (avg, std, min, max, total, runs) per file name.
    """
    import owlready2  # only this task needs it

    results: dict[str, dict[str, float]] = {}
    for name in names:
        stem = re.sub(r"\.\d+$", "", Path(name).stem)
        owl_file = OWL_DIR / f"{stem}.owl"
        if not owl_file.exists():
            logger.warning(f"OWL file not found for {name}: {owl_file}")
            continue
        key = re.sub(r"\.\d+(\.txt)$", r"\1", name)
        ontology = owlready2.World().get_ontology(owl_file.as_uri())
        times: list[float] = []
        for _ in range(runs):
            with ontology:
                t0 = time.perf_counter()
                result = owlready2.sync_reasoner_pellet(
                    infer_property_values=False, infer_data_property_values=False
                )
                times.append(time.perf_counter() - t0)
            logger.info(f"Reasoning result for {key}: {result}")
            logger.info(f"Reasoning time for {key}: {times[-1]} seconds")
        results[key] = compute_stats(times)

    out = results_dir / "classical_dl_sat_results.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True))
    logger.info(f"Classical DL SAT results written to {out}")
    return results
