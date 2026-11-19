# Benchmarks

Performance harness for the `fuzzy_dl_owl2` reasoner. It times a corpus of fuzzy-DL knowledge bases (KBs) on four reasoners and builds the comparison tables of the paper: **Fuzzy OWL~2 Reasoning: A Re-Engineered Python Framework**.

| Tool | What it runs | Input |
|---|---|---|
| **python** | the Python port, once per MILP provider (Gurobi, CBC, HiGHS, MIP, CPLEX, GLPK) | `data/*.txt` |
| **original** | the original implementation (`fuzzydl.jar`), through `run_fdl.sh` | `data/*.txt` |
| **delorean** | DeLorean (`DeLorean.jar`), through `run_delorean.sh` | `data/delorean/*.dlr` |
| **classical** | the classical DL reasoner Pellet (via `owlready2`) | `data/owl/*.owl` |

The original implementation is the oracle: where it disagrees with the Python
port, its answer is the correct one.

Every timing is the **wall-clock time of a fresh process** (interpreter or JVM
start-up included), averaged over N runs.

## Layout

| Path | What it is |
|---|---|
| `bench/` | The benchmark package (`python -m bench`). See [The `bench` package](#the-bench-package). |
| `create_latex_table.py` | Builds the LaTeX tables from `results/`. |
| `bench/phase_worker.py` | One timed process of the phase benchmark (see [Phase benchmark](#phase-benchmark)). |
| `run_queries.py <provider> <file>` | One run: solve one KB with one provider. The building block of the Python benchmark. |
| `run_fdl.sh <file>` | One run of the original implementation. |
| `run_delorean.sh <file.dlr> [N]` | One run of DeLorean. |
| `old/` | The former scripts (`run_all.py`, `run_all_java.py`, `run_all_delorean.py`, earlier table scripts), replaced by `bench/` and kept for reference. |
| `data.zip` | The `data/` directory, zipped. Unzip it before running anything (see [Data](#data)). |
| `data/` | The KBs (below). |
| `results/` | JSON results and solver model dumps (below). |
| `tables/` | LaTeX tables written by `create_latex_table.py` and by `python -m bench phases`. |
| `logs/<tool>/` | One timestamped log per invocation (`python`, `original`, `delorean`, `classical`, `convert`). |
| `lib/`, `fuzzydl.jar`, `DeLorean.jar` | Java classpath of the original implementation and of DeLorean (`lib/HermiT/` for DeLorean). |
| `FuzzyOwl2toDeLorean.jar` | OWL to DeLorean converter. |
| `CONFIG`, `CONFIG.ini` | Reasoner configuration, read from this directory: `CONFIG` by the original implementation, `CONFIG.ini` by the Python port. |

## Data

The corpus is shipped as `data.zip`. Extract it here before running any benchmark
(it recreates `data/`):

```bash
unzip -q data.zip          # from this directory
```

To rebuild the archive after changing `data/`:

```bash
zip -r -q data.zip data -x "*.DS_Store"
```

| Directory | Content |
|---|---|
| `data/*.txt` | 61 KBs in fuzzyDL syntax: the benchmark corpus (`original` kind). |
| `data/graded/` | 153 graded variants, `X.33.txt`, `X.66.txt`, `X.100.txt` for each KB `X` (`graded` kind). |
| `data/owl/` | The same ontologies in OWL 2 (`X.owl`), input of Pellet and of the DeLorean conversion. |
| `data/delorean/` | 89 DeLorean files (`X.dlr`, `X.33.dlr`, ...), produced by `python -m bench convert`. |

A DeLorean file has the extension `.dlr`, not `.txt`: results of DeLorean are keyed `X.100.dlr`, and `create_latex_table.py` renames them to `X.100.txt` when it merges them with the other tools.

## Setup

### System requirements

- **Python 3.11+**.
- **Java** (a JDK): original implementation, DeLorean, the OWL to DeLorean conversion and Pellet.
- **MILP solvers.** CBC and HiGHS come with the Python packages below. The others need a local install: Gurobi (licence, `~/gurobi.lic`), CPLEX Studio (the `cplex` binary), GLPK (`glpsol`, e.g. `brew install glpk`). `bench` runs only the providers that it finds and warns about the others.
  - CPLEX is looked for on `PATH`, in `CPLEX_STUDIO_BINARIES` / `CPLEX_HOME` / `CPLEX_STUDIO_DIR`, and in the usual install directories (`/Applications` and `~/Applications` on macOS).
- **Gurobi for the original implementation**: `run_fdl.sh` needs it. Set `GUROBI_HOME` (otherwise it probes `/Library/gurobi*/macos_universal2` and `/opt/gurobi*/macos_universal2`) and `GRB_LICENSE_FILE` (default `~/gurobi.lic`).

### Virtual environment

Create the environment and install the packages. `run_queries.py` puts the
repository root first on `sys.path`, so the reasoner that is timed is the checkout
of this repository; `fuzzy-dl-owl2==1.0.25` provides its dependencies:

```bash
python3.11 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip

pip install tqdm \
    "fuzzy-dl-owl2==1.0.25" \
    "gurobipy==12.0.0" \
    "mip>=1.17,<2" \
    "cbcbox==2.929" \
    "pulp==3.2.1" \
    highspy
```

| Package | Used for |
|---|---|
| `fuzzy-dl-owl2` | the dependencies of the reasoner, among them `pyowl2` and `owlready2` (Pellet, `python -m bench classical`) |
| `gurobipy` | Gurobi provider |
| `mip` | MIP provider |
| `cbcbox` | CBC solver binaries |
| `pulp` | CBC, HiGHS, GLPK and CPLEX providers |
| `highspy` | HiGHS provider |
| `tqdm` | progress bars of `bench` |

A package that is not installed only removes its provider: `bench` warns and skips it.

If the repository is in a synced folder (Google Drive, iCloud), create the environment outside it (for example `python3.11 -m venv ~/.venvs/fuzzy` and `source ~/.venvs/fuzzy/bin/activate`): thousands of small files slow the sync down.

Activate the environment in every new shell before running `python -m bench` or `create_latex_table.py`.

## The `bench` package

Run it from this directory. One engine times the four tools; a small `Runner` class per tool says only what differs (the command, and when a run is valid).

```bash
python -m bench run python   [options]    # Python port
python -m bench run original [options]    # original implementation
python -m bench run delorean [options]    # DeLorean
python -m bench convert                   # data/owl/*.owl -> data/delorean/*.dlr
python -m bench classical [--runs N]      # Pellet on the selected ontologies
python -m bench -v ...                    # DEBUG log (before the command)
```

Options of `run`:

| Option | Meaning |
|---|---|
| `--kind original\|graded` | `data/` or `data/graded/` (default `original`; DeLorean has one set). |
| `--runs N` | Runs per (file, provider). Default: `BENCHMARK_RUNS` or 10. |
| `--timeout MIN` | Minutes after which a run is killed and counts as a failure (default 20). |
| `--max-failures K` | Consecutive failures after which a unit is given up (default 1; 0 never gives up). |
| `--provider-set selected\|others` | Python only. `selected`: Gurobi, CBC, HiGHS. `others`: MIP, CPLEX, GLPK. |
| `--providers P ...` | Python only. Explicit providers instead of the set (`gurobi mip cbc cplex glpk highs`). |
| `--files NAME ...` | Only these files, by name in the data directory. |
| `--selected` | The files of `results/selected_ontologies.json` (graded). |
| `--data-dir`, `--results-dir` | Other input / output directories. |

### Valid runs, failures, resume

- **A run is valid** when the process exits with 0 and: *python* prints no `None` (a missing solution); *original* prints `Is KnowledgeBase satisfiable?` or `KnowledgeBase inconsistent`; *delorean* writes nothing on stderr.
- **A unit** is one file (one file and one provider, for Python). It is given up after `--max-failures` consecutive failures. A given-up unit is stored as `{}` and counts as done; the *original* tool discards the times it had collected.
- **Resumable.** Results are saved after every unit. Units already in the results file are not run again; delete the entry to rerun it. With the `selected` provider set and with *original* / *delorean* a file with any result is skipped; with the `others` set each (file, provider) pair is checked, so a file gets only its missing providers.
- **Skip lists** (`{"files": [...]}`) name files that must not run; see the table below.

### Two stages for the Python port

1. `python -m bench run python --kind graded` writes `results/graded_benchmark_results.json` for Gurobi, CBC and HiGHS.
2. `python -m bench run python --kind graded --provider-set others` writes the `all_*` files. The first time, `all_graded_benchmark_results.json` is **seeded** with the entries of `graded_benchmark_results.json` for the files that are run, then MIP, CPLEX and GLPK are added. The `all_*` file therefore holds all six providers.

## Results

All files are in `results/`. `graded_` before the name is the `graded` kind; `all_` is the `others` provider set.

| File | Content |
|---|---|
| `[all_][graded_]benchmark_results.json` | Python port: `{file: {provider: stats}}`. |
| `[graded_]java_benchmark_results.json` | Original implementation: `{file: stats}`. |
| `delorean_benchmark_results.json` | DeLorean: `{file.dlr: stats}`. |
| `classical_dl_sat_results.json` | Pellet: `{file.txt: stats}`; the grade is dropped from the name (`X.100.txt` → `X.txt`). |
| `selected_ontologies.json` | `{"files": [...]}`: the fastest and slowest graded KBs (a list kept by hand). |
| `[all_][graded_]python_skip.json`, `[graded_]java_skip.json`, `delorean_skip.json` | Skip lists. |
| `gurobi_model.lp`, `highs_model.lp`, `gurobi_solution.json` | Model and solution dumps of the solvers. |

`stats` is `{"avg", "std", "min", "max", "total", "runs"}` in seconds (`std` is the sample standard deviation); a failed unit is `{}`. Provider keys: `Gurobi`, `MIP`, `CBC`, `CPLEX`, `GLPK`, `HiGHS`.

## Scripts around a single run

`run_queries.py <provider> <file>`: `gurobi`, `mip`, `cbc` (PuLP + CBC), `cplex`, `glpk` or `highs`. Prints one `<query><solution>` line per query.

```bash
python run_queries.py gurobi ./data/FuzzyWine.txt
```

`run_fdl.sh <file>`: runs `fuzzydl.parser.Parser` of `fuzzydl.jar` with `-Xms20000m -Xmx20000m` and the Gurobi library (`GUROBI_HOME`) on the classpath `fuzzydl.jar:lib/*`. It resolves the input to an absolute path, runs from this directory and creates a `CONFIG` file (`showVersion = false`) if there is none.

`run_delorean.sh <file.dlr> [N]`: runs `RunDeLorean` of `DeLorean.jar` with `-Xmx20000m` and the classpath `DeLorean.jar:lib/*:lib/HermiT/HermiT.jar`. `N` is the number of degrees; the benchmark passes 101.

## LaTeX tables

```bash
python create_latex_table.py [--out DIR]     # default: tables/
```

It merges the plain and the graded JSON files (graded wins on a clash), renames the DeLorean keys from `.dlr` to `.txt`, and writes one `.tex` per table. Each file has a header row (`\textbf{KB} & ...`) followed by one row per KB, sorted by speedup. The fastest cell is in bold; a missing result is `N/A`. The summary (how often Python wins, largest and smallest gap) is printed to stdout.

| Table | Columns after the KB | Last column | File |
|---|---|---|---|
| base | Java, Gurobi, CBC, HiGHS | Speedup (Java / best Python) | `python_java_benchmark_table.tex` |
| delorean | Java, Gurobi, DeLorean | Fastest family | `python_java_delorean_benchmark_table.tex` |
| all | Java, Gurobi, CBC, HiGHS, MIP, CPLEX, GLPK | Speedup | `all_python_java_benchmark_table_selected.tex` (reduced only) |
| classical | Pellet, Java, Gurobi, DeLorean | Fastest family | `classical_python_java_delorean_benchmark_table.tex` |

"Java" is the original implementation. The `base`, `delorean` and `all` tables also have a **reduced** version (suffix `_selected`) that keeps only the KBs of `results/selected_ontologies.json`. The classical table covers the KBs of `classical_dl_sat_results.json` (the plain `X.txt` KBs).

## Phase benchmark

Where the time of a `(sat?)` query goes (Łukasiewicz, `DLParserFast`, 20-minute timeout, 10 runs, mean ± sample standard deviation). It runs on the KBs of `results/selected_ontologies.json` (the graded `.100` files) with Gurobi, CBC and HiGHS, plus a trivial KB (`results/phases/trivial.txt`) that gives the fixed costs.

```bash
python -m bench phases run --dry-run     # only the estimate of the running time
python -m bench phases run               # measure; writes results/phases/raw.csv and tables/phases_*.tex
python -m bench phases tables            # rebuild the tables from the CSV
```

Options: `--parts python tokenizers java`, `--solvers gurobi cbc highs`, `--files NAME ...`, `--runs`, `--timeout` (minutes), `--warm-max-s` (no warm pass after a slower cold pass; default 60). The run resumes from the CSV; a unit that times out or fails is given up (N/A) and is not run again. The estimate comes from `graded_benchmark_results.json` / `graded_java_benchmark_results.json`. Every unit is run, the long ones included: `python -m bench phases run` measures all the KBs of `selected_ontologies.json`.

**What is measured** (`time.perf_counter`, timers around calls only; the reasoner is not changed, the wrappers are installed on the loaded classes of the worker process):

| Phase | Meaning |
|---|---|
| startup | process spawn until the first line of the worker (wall clock stamp of the parent) |
| import | import of the parser, the reasoner and the solver library |
| parse | `DLParserFast.get_kb`: tokenize + parser + KB construction (tokenize is also stored apart) |
| tableau | `solve_kb` + `query.solve` minus the MILP optimisation call: ABox expansion and every MILP row written |
| build | `MILPHelper.optimize` minus solver: rows copied into the model of the back end, solution read back and checked |
| solver | `gurobipy.Model.optimize` / `pulp.LpProblem.solve` (for CBC and HiGHS this includes PuLP writing the model and, for CBC, starting the binary) |
| other | wall minus the phases above (mostly interpreter exit; also stored as `teardown`) |
| wall | wall clock of the whole process; the warm pass is subtracted (`process_wall` keeps the raw value) |

Each process does a **cold** pass and, in the same process, one **warm** pass (same KB, same tokenizer; warm rows have `mode = warm`). The **Java** part runs `run_fdl.sh` (wall clock and the `Time (s)` of the jar); the trivial KB gives the JVM startup. The jar has no phase timers, so no finer split is possible.

**Tokenizers.** `scan` runs the tokenizer alone on the file (read + scan + tuples): Cython (re2c scanner + Cython tuple builder, the default; the scanner is generated by re2c, not flex: `lexer_gen.c` is "Generated by re2c", although `generate.py` also writes a flex source that is not built), CFFI (the same re2c scanner, read through int32 arrays and converted in Python) and the pure-Python regular expressions. The file→KB time is measured with all three. The CFFI conversion of the reasoner (`FdlStringTokenizer._tokens_to_parser`) is used as it is, with two corrections applied in the worker process only (`cffi_tokens` in `bench/phase_worker.py`): its table of scanner codes is out of date (NUMBER is code 8, the table has it at 19, so numbers would become identifiers) and `*top*` / `*bottom*` must get the kinds `T_TOP` / `T_BOTTOM`, as in the Cython tuple builder; with them the CFFI stream equals the Cython one on every selected KB except `chebi.100` and `FMA.100`, which were not compared. The file-based CFFI path of the reasoner is broken (it calls `_tokens_to_parser_slice`, which does not exist), so CFFI uses the in-memory one. The two corrections add a small cost to the CFFI times (a lookup and one pass over the `*top*` tokens).

| Output | Content |
|---|---|
| `results/phases/raw.csv` | one row per run and phase: `kb, language, solver, tokenizer, mode, run, phase, seconds` (`n_tokens` is a count, `timeout` / `failed` mark a given-up unit) |
| `tables/phases_python_preparation.tex` | cold phases per KB and solver: Startup, Import, Parse, Tableau |
| `tables/phases_python_solving.tex` | cold phases per KB and solver: Build, Solver, Other, Wall (the table is split in two so that each fits the text width) |
| `tables/phases_python_warm_preparation.tex` | warm phases per KB and solver: Parse, Tableau |
| `tables/phases_python_warm_solving.tex` | warm phases per KB and solver: Build, Solver, Warm total |
| `tables/phases_java.tex` | Java wall and `Time (s)` next to the Python wall, startup + import and tableau + build + solver (Gurobi) |
| `tables/phases_tokenizers_scan.tex` | tokens and scan time per tokenizer |
| `tables/phases_tokenizers_file_to_kb.tex` | file→KB time per tokenizer |

Each table file holds a bold header line and one line per row, without the `tabular` environment: write `\begin{tabular}{...}`, the rules and `\input{...}` around it by hand. The smallest mean of a group is in bold.

**Rebuild the tables without measuring again.** The tables come only from `results/phases/raw.csv`, so after editing the CSV or the generator (`bench/phase_tables.py`) run `python -m bench phases tables` from `benchmark/`. It does not start any process and rewrites the seven `tables/phases_*.tex` files, **overwriting hand edits** of those files; copy them first if you changed them. To write elsewhere, call `phase_tables.save_tables(out_dir=Path(...))` from Python.

**Limits.** Build and solver are separable only through the wrappers above, and the PuLP solver time includes file I/O. Tokenize is inside the cold parse, so it is not added twice. The import phase loads the solver library that the reasoner would load lazily. A warm pass is skipped after a slow cold pass, and the Python and Java timers differ (in-process timers versus the wall clock of a script that starts the JVM with a 20 GB heap and loads Gurobi). The timers add a small overhead (a few microseconds per call, not measured). Teardown is large for big KBs and is not split.

## Commands of the benchmark

The results in `results/` come from these commands, run from this directory in this order, after `unzip -q data.zip` and with the virtual environment active. `--runs N` (or `BENCHMARK_RUNS=N`) sets the number of runs; the default is 10.

**Python port**

```bash
# 1. original KBs, selected providers (Gurobi, CBC, HiGHS) -> benchmark_results.json
python -m bench run python --kind original

# 2. graded KBs, selected providers -> graded_benchmark_results.json
python -m bench run python --kind graded

# 3. the KBs of selected_ontologies.json, the other providers (MIP, CPLEX, GLPK)
#    -> all_graded_benchmark_results.json, seeded with the results of step 2
python -m bench run python --kind graded --provider-set others --selected

# 4. classical reasoner (Pellet) on the same KBs -> classical_dl_sat_results.json
python -m bench classical
```

**Original implementation**

```bash
python -m bench run original --kind original   # -> java_benchmark_results.json
python -m bench run original --kind graded     # -> graded_java_benchmark_results.json
```

**DeLorean**

```bash
python -m bench convert                        # data/owl/*.owl -> data/delorean/*.dlr
python -m bench run delorean                   # -> delorean_benchmark_results.json
```

**Tables**

```bash
python create_latex_table.py                   # -> tables/
```

`results/selected_ontologies.json` is the list of KBs of steps 3 and 4 and of the reduced tables; it is kept by hand.

**Phase benchmark**

```bash
python -m bench phases run --dry-run           # estimate first
python -m bench phases run                     # -> results/phases/raw.csv, tables/phases_*.tex
```
