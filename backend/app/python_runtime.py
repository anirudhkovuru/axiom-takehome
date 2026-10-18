import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


ALLOWED_IMPORTS = {"numpy", "pandas", "scipy", "sklearn", "matplotlib", "seaborn"}
BLOCKED_NAMES = {"open", "exec", "eval", "compile", "input", "breakpoint", "__import__"}
BLOCKED_ATTRIBUTES = {
    "system",
    "popen",
    "spawn",
    "fork",
    "remove",
    "unlink",
    "rmdir",
    "rmtree",
    "chmod",
    "chown",
    "connect",
}
ALLOWED_ARTIFACT_SUFFIXES = {".csv", ".json", ".png", ".jpg", ".jpeg", ".html", ".md", ".txt"}


class UnsafeCodeError(ValueError):
    pass


class PythonRuntime:
    def __init__(self, database_path: Path):
        self.database_path = database_path.resolve()

    def validate(self, code: str) -> None:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            raise UnsafeCodeError(f"Invalid Python: {exc}") from exc

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                        raise UnsafeCodeError(f"Import '{alias.name}' is not allowed")
            if isinstance(node, ast.ImportFrom):
                module = (node.module or "").split(".")[0]
                if module not in ALLOWED_IMPORTS:
                    raise UnsafeCodeError(f"Import from '{node.module}' is not allowed")
            if isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
                raise UnsafeCodeError(f"Use of '{node.id}' is not allowed")
            if isinstance(node, ast.Attribute):
                if node.attr.startswith("_") or node.attr in BLOCKED_ATTRIBUTES:
                    raise UnsafeCodeError(f"Attribute '{node.attr}' is not allowed")

    def execute(self, code: str, run_directory: Path, timeout_seconds: int = 45) -> dict[str, Any]:
        self.validate(code)
        run_directory.mkdir(parents=True, exist_ok=True)
        script_path = run_directory / "analysis.py"
        if script_path.exists():
            index = 2
            while (run_directory / f"analysis-{index}.py").exists():
                index += 1
            script_path = run_directory / f"analysis-{index}.py"

        wrapper = self._wrapper(code, run_directory)
        script_path.write_text(wrapper, encoding="utf-8")
        before = {path.resolve() for path in run_directory.iterdir()}
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONUNBUFFERED": "1",
            "MPLCONFIGDIR": str(run_directory.parent.parent / "matplotlib-cache"),
        }
        Path(environment["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            [sys.executable, "-I", str(script_path)],
            cwd=run_directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        generated = [
            path
            for path in run_directory.iterdir()
            if path.resolve() not in before
            and path.is_file()
            and path.suffix.lower() in ALLOWED_ARTIFACT_SUFFIXES
        ]
        output = completed.stdout.strip()
        if completed.returncode != 0 and completed.stderr:
            output = (output + "\n" + completed.stderr).strip()
        return {
            "return_code": completed.returncode,
            "output": output[-12000:],
            "script_path": script_path,
            "generated_files": generated,
        }

    def _wrapper(self, code: str, run_directory: Path) -> str:
        prefix = textwrap.dedent(
            f"""
            import json
            from pathlib import Path

            import duckdb
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
            import pandas as pd

            DB_PATH = Path({str(self.database_path)!r})
            ARTIFACT_DIR = Path({str(run_directory.resolve())!r})
            con = duckdb.connect(str(DB_PATH), read_only=True)

            def save_table(frame, filename):
                path = ARTIFACT_DIR / Path(filename).name
                frame.to_csv(path, index=False)
                return str(path)

            def save_figure(figure, filename):
                path = ARTIFACT_DIR / Path(filename).name
                figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
                return str(path)
            """
        ).lstrip()
        return prefix + "\n" + code.strip() + "\n"
