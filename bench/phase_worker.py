"""
One timed process of the phase benchmark (run as a script, never imported).

    python bench/phase_worker.py <file> --provider gurobi --tokenizer cython [--stop-after parse] [--no-warm]

It does what ``run_queries.py`` does (``DLParserFast.main``: parse, ``solve_kb``,
answer every query) but with a timer around each phase, and prints one line
``PHASES {json}`` per pass: the cold pass first and then, in the same process,
one warm pass. Timers only wrap calls; the reasoner is not modified (the
wrappers are installed on the loaded classes, in this process only).

Phases of a pass (seconds, ``time.perf_counter``):

* ``tokenize``: reading the file and building the token tuples;
* ``parse``: ``DLParserFast.get_kb`` minus ``tokenize`` (parser + KB construction);
* ``tableau``: ``solve_kb`` plus ``query.solve`` minus ``milp_optimize`` (ABox
  expansion, clone and every MILP row written before the solver is called);
* ``build``: ``milp_optimize`` minus ``solver`` (rows copied into the model of
  the back end, the solution read back and checked);
* ``solver``: the optimisation call of the back end (``gurobipy.Model.optimize``,
  ``pulp.LpProblem.solve``).

The startup of the interpreter is measured against the wall-clock stamp that the
parent passes in ``BENCH_SPAWN_TS``; the import phase is the import of the
parser, the reasoner and the library of the solver.
"""

import time

# === Entry stamps (before anything else is imported) ===

ENTRY_WALL: float = time.time()
ENTRY_PERF: float = time.perf_counter()

import argparse
import gc
import json
import os
import sys
import typing
from pathlib import Path

# === Global variables ===

ROOT: Path = Path(__file__).resolve().parent.parent
MARKER: str = "PHASES "

# === Global functions ===


def emit(**fields: typing.Any) -> None:
    """Print one result line for the parent process.

    :param fields: Values of the line; they are written as ``PHASES {json}``.
    """
    print(MARKER + json.dumps(fields), flush=True)


def import_reasoner(provider: str, with_solver: bool) -> dict[str, typing.Any]:
    """Import the reasoner (and the solver library) and return the modules used.

    :param provider: ``gurobi``, ``cbc`` or ``highs``; it selects the solver library.
    :param with_solver: Import the solver library too (False for a parse-only run).
    :return: The modules and classes the worker patches or calls.
    """
    # reasoner modules: they are imported here so that the import phase times them
    from fuzzy_dl_owl2.fuzzydl.milp.milp_helper import MILPHelper
    from fuzzy_dl_owl2.fuzzydl.parser import DLParserFast
    from fuzzy_dl_owl2.fuzzydl.parser.tokenizer import tokenizer_handler
    from fuzzy_dl_owl2.fuzzydl.util import constants

    # the objects that the timers wrap later
    modules: dict[str, typing.Any] = {
        "DLParser": DLParserFast,
        "MILPHelper": MILPHelper,
        "handler": tokenizer_handler,
        "constants": constants,
    }
    # solver library: the reasoner would load it lazily, here it is part of the import phase
    if with_solver:
        if provider == "gurobi":
            import gurobipy

            modules["gurobipy"] = gurobipy
        else:
            import pulp

            modules["pulp"] = pulp
            if provider == "highs":
                import highspy  # noqa: F401
    return modules


def cffi_tokens(handler: typing.Any, data: bytes) -> list:
    """Parser tokens of ``data`` through the CFFI scanner and the reasoner's own conversion.

    The conversion of the reasoner is used as it is, with two corrections that the
    parser needs and that are applied in this process only. Its table from scanner
    codes to parser kinds is out of date: it puts NUMBER at code 19, but the scanner
    emits it as code 8, so every number would come out as an identifier. And a bare
    ``*top*`` / ``*bottom*`` comes out as an identifier, where the Cython tuple
    builder gives the kinds ``T_TOP`` / ``T_BOTTOM`` (the same numbers as the scanner
    codes).

    :param handler: The ``tokenizer_handler`` module.
    :param data: The UTF-8 bytes of the file.
    :return: The token tuples, the same stream as the Cython tuple builder gives.
    """
    import numpy as np
    from fuzzy_dl_owl2.fuzzydl.parser.tokenizer import tokens as scanner

    handler._FDL_CODE_TO_TK[scanner.NUMBER] = handler.NUMBER  # the corrected lookup
    codes = {name: code for code, name in scanner.TOK_NAMES.items()}
    toks = handler.FdlStringTokenizer()._get_tokens_from_bytes(data)
    try:
        out = handler.FdlStringTokenizer._tokens_to_parser(toks, len(data))
        types = toks.types
        # the conversion drops every comma: token i is out[i - commas before i]
        before = np.cumsum(types == codes["COMMA"])
        for name, kind in (("*top*", handler.T_TOP), ("*bottom*", handler.T_BOTTOM)):
            for i in np.flatnonzero(types == codes[name]):
                kept = out[i - int(before[i])]
                out[i - int(before[i])] = (kind, kept[1], kept[2], kept[3])
        return out
    finally:
        toks.close()


def install_tokenizer(modules: dict[str, typing.Any], backend: str, acc: dict) -> None:
    """Force a tokenizer backend and time it.

    ``cython``: the re2c scanner with the Cython tuple builder (the default path
    of ``DLParserFast.parse_string_opt``); ``cffi``: the same scanner read through
    CFFI int32 arrays and converted in Python; ``regex``: the pure-Python regular
    expression tokenizer, chunked by form as ``DLParserFast.parse_string`` does.

    :param modules: The modules returned by :func:`import_reasoner`.
    :param backend: ``cython``, ``cffi`` or ``regex``.
    :param acc: Accumulators of the pass; ``acc["tokenize"]`` receives the seconds.
    """
    handler = modules["handler"]
    file_tokenizer = handler.FdlFileTokenizer
    original = file_tokenizer.tokenize_file  # the default (Cython) path, kept for the "cython" backend


    def regex_chunks(self: typing.Any, path: str) -> typing.Iterator[list]:
        """Token chunks of the file through the pure-Python regular expressions.

        :param self: The ``FdlFileTokenizer`` the method is installed on (unused).
        :param path: Path of the fuzzy-DL file.
        :return: An iterator of token lists, one per chunk of forms.
        """
        # read the whole file as text
        with open(path, "r") as fh:
            source = fh.read()
        regex = handler.PythonRegexTokenizer()
        # a small file is tokenized in one piece
        if len(source) <= handler._STREAM_THRESHOLD:
            yield regex.tokenize(source)
            return
        # a large one form by form, as the reasoner does, so that the parser can
        # consume each chunk before the next is produced
        for chunk in handler._iter_form_chunks(source):
            yield regex.tokenize(chunk)

    def cffi_chunks(self: typing.Any, path: str) -> typing.Iterator[list]:
        """Token list of the whole file through the in-memory CFFI path.

        The file-based CFFI branch of the reasoner is stale (it calls a missing
        helper), so the in-memory path is used: ``get_tokens_from_bytes`` plus the
        conversion of ``FdlStringTokenizer``.

        :param self: The ``FdlFileTokenizer`` the method is installed on (unused).
        :param path: Path of the fuzzy-DL file.
        :return: An iterator with one token list.
        """
        # read the file as bytes for the C scanner
        with open(path, "rb") as fh:
            data = fh.read()
        # the C scanner fills int32 arrays; they are converted to token tuples in Python
        yield cffi_tokens(handler, data)

    # backend chosen by the caller
    inner = {"cython": original, "cffi": cffi_chunks, "regex": regex_chunks}[backend]

    def timed(self: typing.Any, path: str) -> typing.Iterator[list]:
        """Run the chosen backend and add the time spent inside it to ``acc``.

        Only the time of producing each chunk is counted, not the parsing that runs
        between two chunks.

        :param self: The ``FdlFileTokenizer`` the method is installed on.
        :param path: Path of the fuzzy-DL file.
        :return: An iterator of token lists.
        """
        it = iter(inner(self, path))
        while True:
            # the clock runs only while the backend produces a chunk: the parser
            # works between two next() calls, and that time is not tokenizing
            t0 = time.perf_counter()
            try:
                chunk = next(it)
            except StopIteration:
                acc["tokenize"] += time.perf_counter() - t0
                return
            acc["tokenize"] += time.perf_counter() - t0
            yield chunk  # hand the chunk to the parser, clock stopped

    # the parser calls tokenize_file by attribute: replacing it here, in this process only,
    # puts the timer on the path without changing the reasoner
    file_tokenizer.tokenize_file = timed


def install_solver_timers(modules: dict[str, typing.Any], acc: dict) -> None:
    """Time ``MILPHelper.optimize`` and the optimisation call of the back end.

    :param modules: The modules returned by :func:`import_reasoner`.
    :param acc: Accumulators of the pass; ``milp_optimize`` and ``solver`` receive the seconds.
    """
    helper = modules["MILPHelper"]
    original_optimize = helper.optimize  # whole build + solve + read-back of the model

    def optimize(self: typing.Any, objective: typing.Any) -> typing.Any:
        """Timed ``MILPHelper.optimize``.

        :param self: The MILP helper.
        :param objective: The objective expression.
        :return: The solution of the original method.
        """
        t0 = time.perf_counter()
        try:
            return original_optimize(self, objective)
        finally:
            # accumulated: a query may call optimize several times
            acc["milp_optimize"] += time.perf_counter() - t0

    helper.optimize = optimize

    # the optimisation call of the back end, apart from the model building

    if "gurobipy" in modules:
        gp = modules["gurobipy"]

        class TimedModel(gp.Model):
            """``gurobipy.Model`` whose ``optimize`` is timed."""
            def optimize(self, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
                """Timed ``Model.optimize``.

                :param args: Positional arguments of ``optimize``.
                :param kwargs: Keyword arguments of ``optimize``.
                :return: The result of ``Model.optimize``.
                """
                t0 = time.perf_counter()
                try:
                    return super().optimize(*args, **kwargs)
                finally:
                    acc["solver"] += time.perf_counter() - t0

        # the reasoner creates the model through gp.Model, after this point
        gp.Model = TimedModel
    elif "pulp" in modules:
        problem = modules["pulp"].LpProblem
        original_solve = problem.solve

        def solve(self: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            """Timed ``LpProblem.solve``.

            :param self: The PuLP problem.
            :param args: Positional arguments of ``solve``.
            :param kwargs: Keyword arguments of ``solve``.
            :return: The result of ``LpProblem.solve``.
            """
            t0 = time.perf_counter()
            try:
                return original_solve(self, *args, **kwargs)
            finally:
                acc["solver"] += time.perf_counter() - t0

        # PuLP's solve also writes the model file and starts the CBC / HiGHS process
        problem.solve = solve


def scan(modules: dict[str, typing.Any], path: str, backend: str) -> list:
    """The whole file as parser tokens through one backend (file read included).

    :param modules: The modules returned by :func:`import_reasoner`.
    :param path: Path of the fuzzy-DL file.
    :param backend: ``cython``, ``cffi`` or ``regex``.
    :return: The token tuples of the file.
    """
    handler = modules["handler"]
    if backend == "cython":
        from fuzzy_dl_owl2.fuzzydl.parser.tokenizer._fdl_tuples import FdlScan

        # re2c scan of the bytes, then every token as a tuple in one batch
        with open(path, "rb") as fh:
            sc = FdlScan(fh.read())
        return sc.batch(0, sc.ntok)
    if backend == "cffi":
        # the file-based CFFI branch of the reasoner is stale, use the in-memory one
        with open(path, "rb") as fh:
            data = fh.read()
        return cffi_tokens(handler, data)
    # regex backend: the whole text in one call
    with open(path, "r") as fh:
        return handler.PythonRegexTokenizer().tokenize(fh.read())


def tokenize_only(modules: dict[str, typing.Any], path: str, backend: str) -> dict:
    """Time the tokenizer alone.

    :param modules: The modules returned by :func:`import_reasoner`.
    :param path: Path of the fuzzy-DL file.
    :param backend: ``cython``, ``cffi`` or ``regex``.
    :return: The ``tokenize`` time and the number of tokens.
    """
    gc.disable()  # as the parser does: no collection inside the timed region
    # timed region: file read + scan + tuples
    t0 = time.perf_counter()
    tokens = scan(modules, path, backend)
    elapsed = time.perf_counter() - t0
    return {"phases": {"tokenize": elapsed}, "n_tokens": len(tokens), "answers": []}


def one_pass(
    modules: dict[str, typing.Any], path: str, provider: str, stop_after: str, acc: dict
) -> dict[str, typing.Any]:
    """Parse, expand and solve ``path`` once; return the phases and the answers.

    :param modules: The modules returned by :func:`import_reasoner`.
    :param path: Path of the fuzzy-DL file.
    :param provider: ``gurobi``, ``cbc`` or ``highs``.
    :param stop_after: ``parse`` stops after ``get_kb``; ``solve`` runs the queries.
    :param acc: Accumulators of the timers; they are reset at the start of the pass.
    :return: ``phases`` (seconds per phase) and ``answers`` (the printed query answers).
    """
    from fuzzy_dl_owl2.fuzzydl.util import constants

    providers = {
        "gurobi": constants.MILPProvider.GUROBI,
        "cbc": constants.MILPProvider.PULP,
        "highs": constants.MILPProvider.PULP_HIGHS,
    }
    # a pass starts with zeroed accumulators
    for key in acc:
        acc[key] = 0.0
    parser = modules["DLParser"]
    # DLParserFast.main disables the cyclic collector for the whole run; so do we
    gc_was_enabled = gc.isenabled()
    gc.disable()
    answers: list[str] = []
    try:
        # parse: tokenizer + parser + KB construction; the tokenizer share comes from the wrapper
        t0 = time.perf_counter()
        kb, queries = parser.get_kb(
            path, debugPrint=False, milpProvider=providers[provider]
        )
        t1 = time.perf_counter()
        phases = {"tokenize": acc["tokenize"], "parse": (t1 - t0) - acc["tokenize"]}
        if stop_after == "parse":
            return {"phases": phases, "answers": answers}
        # reasoning: tableau rules, MILP model, optimisation and every query
        kb.solve_kb()
        for query in queries:
            solution = query.solve(kb)
            answers.append(f"{query}{solution}")
        t2 = time.perf_counter()
        # split of t2 - t1 with the two wrappers: what is not inside optimize is
        # tableau; inside optimize, what is not the solver call is model building
        phases["tableau"] = (t2 - t1) - acc["milp_optimize"]
        phases["build"] = acc["milp_optimize"] - acc["solver"]
        phases["solver"] = acc["solver"]
        return {"phases": phases, "answers": answers}
    finally:
        # restore the collector, also after an error
        if gc_was_enabled:
            gc.enable()


def main() -> None:
    """Run the cold pass and, in the same process, one warm pass; print one line per pass."""
    # command line: KB file, solver, tokenizer, how far to go, warm pass options
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--provider", default="gurobi", choices=["gurobi", "cbc", "highs"])
    parser.add_argument("--tokenizer", default="cython", choices=["cython", "cffi", "regex"])
    parser.add_argument("--stop-after", default="solve", choices=["tokenize", "parse", "solve"])
    parser.add_argument("--no-warm", action="store_true")
    parser.add_argument("--warm-max-s", type=float, default=60.0,
                        help="no warm pass when the cold pass took longer")
    args = parser.parse_args()

    # same set-up as run_queries.py: CONFIG.ini is read from the cwd and the
    # local checkout shadows any installed fuzzy_dl_owl2
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT.parent))

    # startup: wall-clock stamp of the parent at spawn to the stamp taken at the first
    # line of this script (wall clocks, because the two processes share no perf_counter)
    spawn = float(os.environ.get("BENCH_SPAWN_TS", "nan"))
    startup = ENTRY_WALL - spawn

    # import phase: reasoner and solver library
    t0 = time.perf_counter()
    modules = import_reasoner(args.provider, args.stop_after == "solve")
    import_time = time.perf_counter() - t0

    # accumulators filled by the wrappers, then the wrappers themselves
    acc = {"tokenize": 0.0, "milp_optimize": 0.0, "solver": 0.0}
    install_tokenizer(modules, args.tokenizer, acc)
    install_solver_timers(modules, acc)

    path = str(Path(args.file).resolve())
    # tokenizer-only run: one line for the pass, one for the end of the process
    if args.stop_after == "tokenize":
        emit(mode="cold", startup=startup, **{"import": import_time},
             **tokenize_only(modules, path, args.tokenizer))
        emit(mode="end", worker_total=time.perf_counter() - ENTRY_PERF)
        return
    # cold pass: first use of the code and of the solver in this process
    cold = one_pass(modules, path, args.provider, args.stop_after, acc)
    emit(mode="cold", startup=startup, **{"import": import_time}, **cold)
    # warm pass: same process and same KB, only if the cold pass was short enough
    cold_total = sum(cold["phases"].values())
    if not args.no_warm and cold_total <= args.warm_max_s:
        warm = one_pass(modules, path, args.provider, args.stop_after, acc)
        emit(mode="warm", **warm)
    # time since the first line of the script: the parent uses it to get the teardown
    emit(mode="end", worker_total=time.perf_counter() - ENTRY_PERF)


if __name__ == "__main__":
    main()
