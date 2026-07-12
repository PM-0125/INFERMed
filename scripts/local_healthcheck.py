from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _status_line(ok: bool, label: str, detail: str = "") -> str:
    marker = "OK" if ok else "WARN"
    return f"[{marker}] {label}" + (f": {detail}" if detail else "")


def _file_size(path: Path) -> str:
    if not path.exists():
        return "missing"
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return str(path.stat().st_size)


def _command_output(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local INFERMed development data/runtime readiness.")
    parser.add_argument("--data-mode", default="public_safe", choices=["public_safe", "local_dev", "full_research_future"])
    parser.add_argument("--duckdb-dir", default="data/duckdb")
    parser.add_argument("--manifest", default="data_manifest.yaml")
    parser.add_argument("--enable-drugbank", action="store_true")
    parser.add_argument("--skip-dotenv", action="store_true", help="Do not load .env while checking local files.")
    args = parser.parse_args()

    if args.skip_dotenv:
        os.environ["INFERMED_SKIP_DOTENV"] = "1"

    root = _repo_root()
    sys.path.insert(0, str(root))

    from src.config.data_policy import get_source_status
    from src.config.settings import get_settings
    from src.retrieval.duckdb_query import DuckDBClient

    settings = get_settings()
    settings = replace(
        settings,
        data_mode=args.data_mode,
        duckdb_dir=args.duckdb_dir,
        data_manifest_path=args.manifest,
        enable_drugbank=bool(args.enable_drugbank),
    )

    print("INFERMed local healthcheck")
    print(f"repo={root}")
    print(f"python={sys.executable}")
    print(f"data_mode={settings.data_mode}")
    print(f"duckdb_dir={settings.duckdb_dir}")
    print(f"manifest={settings.data_manifest_path}")
    print()

    required_files = ["twosides.parquet", "dilirank.parquet", "dictrank.parquet", "diqt.parquet"]
    if settings.enable_drugbank:
        required_files.append("drugbank.parquet")
    for filename in required_files:
        path = root / settings.duckdb_dir / filename
        print(_status_line(path.exists(), f"data file {filename}", _file_size(path)))

    print()
    for status in get_source_status(settings):
        print(_status_line(status.available if status.enabled else True, status.name, status.reason))

    print()
    try:
        client = DuckDBClient(
            base_dir=settings.duckdb_dir,
            enable_drugbank=settings.enable_drugbank,
            enable_duckdb=settings.enable_duckdb,
        )
        print(_status_line(True, "DuckDB registered views", str(client.get_available_sources())))
    except Exception as exc:
        print(_status_line(False, "DuckDB registered views", str(exc)))

    print()
    ollama_path = shutil.which("ollama")
    print(_status_line(bool(ollama_path), "ollama command", ollama_path or "not installed"))
    if ollama_path:
        ok, output = _command_output(["ollama", "list"])
        print(_status_line(ok, "ollama list", output.splitlines()[0] if output else "no output"))

    nvidia_smi = shutil.which("nvidia-smi")
    print(_status_line(bool(nvidia_smi), "nvidia-smi", nvidia_smi or "not installed"))
    if nvidia_smi:
        ok, output = _command_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"]
        )
        print(_status_line(ok, "GPU", output))

    print()
    print("Notes:")
    print("- This script does not print API keys or secret environment values.")
    print("- Use --skip-dotenv for a pure file/data check.")
    print("- On small local GPUs, prefer NVIDIA API or mock provider over large local Ollama models.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
