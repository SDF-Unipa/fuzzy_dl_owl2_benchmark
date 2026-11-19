"""
Command line of the benchmark: ``python -m bench <command>`` from the
``benchmark`` directory.
"""

from __future__ import annotations

import argparse
import dataclasses
import typing
from pathlib import Path

from . import phase_tables, phases, tasks
from .common import RESULTS_DIR, BenchmarkConfig, OntologyKind, Tool, setup_logging
from .engine import run_benchmark
from .providers import Provider, ProviderSet
from .runners import RUNNERS, Runner

# === Global functions ===


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="log at DEBUG")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="benchmark a tool")
    run.add_argument("tool", type=Tool, choices=list(Tool))
    run.add_argument("--kind", type=OntologyKind, choices=list(OntologyKind),
                     default=OntologyKind.ORIGINAL, help="original or graded files")
    run.add_argument("--runs", type=int, help="runs per file (default: BENCHMARK_RUNS or 10)")
    run.add_argument("--timeout", type=int, help="minutes per run (default 20)")
    run.add_argument("--max-failures", type=int,
                     help="consecutive failures before giving up a unit (default 1, 0 never)")
    run.add_argument("--provider-set", type=ProviderSet, choices=list(ProviderSet),
                     default=ProviderSet.SELECTED,
                     help="python only; selected = gurobi, cbc, highs; others = the rest "
                          "(saved in the all_* files, seeded with the graded results)")
    run.add_argument("--providers", nargs="+", type=Provider, metavar="P",
                     help=f"python only; explicit providers instead of the set; any of "
                          f"{[p.value for p in Provider]}")
    run.add_argument("--data-dir", type=Path, default=None,
                     help="directory of the input files (default: data/ or data/delorean)")
    group = run.add_mutually_exclusive_group()
    group.add_argument("--files", nargs="+", metavar="NAME",
                       help="file names in the data directory")
    group.add_argument("--selected", action="store_true",
                       help="the files of results/selected_ontologies.json (in data/graded)")
    run.add_argument("--results-dir", type=Path, default=RESULTS_DIR)

    sub.add_parser("convert", help="convert the OWL files to DeLorean format")

    classical = sub.add_parser("classical", help="time Pellet on the selected ontologies")
    classical.add_argument("--runs", type=int, help="runs per file (default: BENCHMARK_RUNS or 10)")

    ph = sub.add_parser("phases", help="time the phases of a (sat?) query (startup, parse, "
                                       "tableau, solver) and compare the tokenizers")
    ph_sub = ph.add_subparsers(dest="phases_command", required=True)
    ph_run = ph_sub.add_parser("run", help="measure and write results/phases/raw.csv and the tables")
    ph_run.add_argument("--parts", nargs="+", type=phases.Part, choices=list(phases.Part),
                        default=list(phases.Part))
    ph_run.add_argument("--solvers", nargs="+", type=Provider,
                        default=[Provider.GUROBI, Provider.CBC, Provider.HIGHS])
    ph_run.add_argument("--files", nargs="+", metavar="NAME",
                        help="KBs of data/graded (default: results/selected_ontologies.json)")
    ph_run.add_argument("--runs", type=int, default=10)
    ph_run.add_argument("--timeout", type=int, default=20, help="minutes per process")
    ph_run.add_argument("--warm-max-s", type=float, default=60.0,
                        help="no warm pass when the cold pass takes longer (seconds)")
    ph_run.add_argument("--dry-run", action="store_true", help="only log the estimate")
    ph_sub.add_parser("tables", help="rebuild the LaTeX tables from results/phases/raw.csv")
    return parser


def _make_runner(args: argparse.Namespace) -> Runner:
    kwargs: dict[str, typing.Any] = {
        "kind": args.kind,
        "results_dir": args.results_dir,
        "data_dir": args.data_dir,
    }
    if args.tool is Tool.PYTHON:
        kwargs["provider_set"] = args.provider_set
        kwargs["providers"] = args.providers
    return RUNNERS[args.tool](**kwargs)


def _make_config(args: argparse.Namespace) -> BenchmarkConfig:
    overrides = {
        "runs": args.runs,
        "timeout_min": args.timeout,
        "max_failures": args.max_failures,
    }
    return dataclasses.replace(
        BenchmarkConfig.from_env(),
        **{k: v for k, v in overrides.items() if v is not None},
    )


def main(argv: typing.Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    setup_logging(args.tool if args.command == "run" else args.command, args.verbose)

    if args.command == "run":
        runner = _make_runner(args)
        if args.selected:
            # the selected KBs are graded files, whatever the kind of the results
            # file (the kind only names it: `all_benchmark_results.json` for original)
            graded_dir = runner.base_data_dir / "graded"
            files = [graded_dir / n for n in tasks.load_selected_ontologies(args.results_dir)]
        else:
            files = runner.resolve(args.files) if args.files else None
        run_benchmark(runner, _make_config(args), files)
    elif args.command == "phases":
        if args.phases_command == "run":
            cfg = BenchmarkConfig(runs=args.runs, max_failures=1, timeout_min=args.timeout)
            names = args.files or tasks.load_selected_ontologies()
            phases.run_phases(args.parts, args.solvers, names, cfg, args.warm_max_s,
                              args.dry_run)
            if args.dry_run:
                return
        phase_tables.save_tables()
    elif args.command == "convert":
        tasks.convert_all()
    elif args.command == "classical":
        runs = args.runs if args.runs is not None else BenchmarkConfig.from_env().runs
        tasks.run_classical_dl_sat(tasks.load_selected_ontologies(), runs)
