"""Contrato de salida comun a todos los checkers."""
import json
import sys
from pathlib import Path

SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
              ".go", ".java", ".kt", ".rb", ".php", ".cs"}


def source_files(root: Path):
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_EXT:
            continue
        parts = set(path.parts)
        if parts & {"node_modules", ".venv", "dist", "build", "__pycache__", ".git"}:
            continue
        yield path


def finding(file, line, rule, evidence):
    return {"file": str(file), "line": line, "rule": rule, "evidence": evidence}


def emit(findings):
    json.dump({"findings": findings}, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(1 if findings else 0)
