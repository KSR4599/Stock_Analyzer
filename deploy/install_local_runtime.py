from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = (
    Path.home() / "Library" / "Application Support" / "StockAnalyzer"
)
LAUNCH_AGENT_NAMES = {
    "com.stock-analyzer.local": "com.stock-analyzer.template.plist",
    "com.stock-analyzer.shadow": "com.stock-analyzer.shadow.template.plist",
    "com.stock-analyzer.portfolio": "com.stock-analyzer.portfolio.template.plist",
    "com.stock-analyzer.portfolio-price-watch": (
        "com.stock-analyzer.portfolio-price-watch.template.plist"
    ),
    "com.stock-analyzer.portfolio-eod": "com.stock-analyzer.portfolio-eod.template.plist",
    "com.stock-analyzer.dashboard": "com.stock-analyzer.dashboard.template.plist",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install or update the private local Stock Analyzer runtime."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=PROJECT_ROOT / "data" / "stock_analyzer.sqlite3",
    )
    parser.add_argument(
        "--source-env",
        type=Path,
        default=PROJECT_ROOT / ".env",
    )
    parser.add_argument(
        "--skip-package-install",
        action="store_true",
        help="Prepare state and plists without installing the Python package.",
    )
    parser.add_argument(
        "--skip-plists",
        action="store_true",
        help="Do not update ~/Library/LaunchAgents.",
    )
    return parser.parse_args()


def install_runtime(
    runtime_root: Path,
    source_db: Path,
    source_env: Path,
    *,
    install_package: bool = True,
    install_plists: bool = True,
) -> dict[str, object]:
    runtime_root = runtime_root.expanduser().resolve()
    paths = {
        "root": runtime_root,
        "data": runtime_root / "data",
        "logs": runtime_root / "logs",
        "tmp_pdfs": runtime_root / "tmp" / "pdfs",
        "backups": runtime_root / "backups",
        "venv": runtime_root / ".venv",
        "env": runtime_root / ".env",
        "db": runtime_root / "data" / "stock_analyzer.sqlite3",
    }
    for key in ("root", "data", "logs", "tmp_pdfs", "backups"):
        paths[key].mkdir(parents=True, exist_ok=True)
        paths[key].chmod(0o700)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    source_db = source_db.expanduser().resolve()
    if not paths["db"].exists():
        if not source_db.exists():
            raise FileNotFoundError(f"Source database does not exist: {source_db}")
        backup = paths["backups"] / f"pre-runtime-{timestamp}.sqlite3"
        shutil.copy2(source_db, backup)
        backup.chmod(0o600)
        shutil.copy2(source_db, paths["db"])
        paths["db"].chmod(0o600)
        migrated_database = True
    else:
        backup = paths["backups"] / f"runtime-before-update-{timestamp}.sqlite3"
        shutil.copy2(paths["db"], backup)
        backup.chmod(0o600)
        migrated_database = False

    source_env = source_env.expanduser().resolve()
    if not paths["env"].exists():
        if not source_env.exists():
            raise FileNotFoundError(f"Source environment file does not exist: {source_env}")
        shutil.copy2(source_env, paths["env"])
    paths["env"].chmod(0o600)

    for log_name in (
        "stock-analyzer-launchd.out.log",
        "stock-analyzer-launchd.err.log",
        "stock-analyzer-shadow.out.log",
        "stock-analyzer-shadow.err.log",
        "stock-analyzer-portfolio.out.log",
        "stock-analyzer-portfolio.err.log",
        "stock-analyzer-price-watch.out.log",
        "stock-analyzer-price-watch.err.log",
        "stock-analyzer-eod.out.log",
        "stock-analyzer-eod.err.log",
        "stock-analyzer-dashboard.out.log",
        "stock-analyzer-dashboard.err.log",
    ):
        log_path = paths["logs"] / log_name
        log_path.touch(exist_ok=True)
        log_path.chmod(0o600)

    python_path = paths["venv"] / "bin" / "python"
    if install_package:
        if not python_path.exists():
            subprocess.run(
                [sys.executable, "-m", "venv", str(paths["venv"])],
                check=True,
            )
        subprocess.run(
            [
                str(python_path),
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--constraint",
                str(PROJECT_ROOT / "constraints.txt"),
                str(PROJECT_ROOT),
            ],
            check=True,
        )

    installed_plists: list[Path] = []
    if install_plists:
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True, exist_ok=True)
        for label, template_name in LAUNCH_AGENT_NAMES.items():
            template_path = PROJECT_ROOT / "deploy" / "launchd" / template_name
            payload = plistlib.loads(template_path.read_bytes())
            payload = _replace_project_dir(payload, str(runtime_root))
            target = launch_agents / f"{label}.plist"
            temporary = target.with_suffix(".plist.tmp")
            temporary.write_bytes(
                plistlib.dumps(payload, sort_keys=False)
            )
            temporary.chmod(0o600)
            os.replace(temporary, target)
            target.chmod(0o600)
            installed_plists.append(target)

    return {
        "runtime_root": runtime_root,
        "database": paths["db"],
        "database_migrated": migrated_database,
        "backup": backup,
        "environment": paths["env"],
        "python": python_path,
        "plists": installed_plists,
    }


def _replace_project_dir(value: object, runtime_root: str) -> object:
    if isinstance(value, dict):
        return {
            key: _replace_project_dir(item, runtime_root)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_project_dir(item, runtime_root) for item in value]
    if isinstance(value, str):
        return value.replace("__PROJECT_DIR__", runtime_root)
    return value


def main() -> None:
    args = parse_args()
    result = install_runtime(
        args.runtime_root,
        args.source_db,
        args.source_env,
        install_package=not args.skip_package_install,
        install_plists=not args.skip_plists,
    )
    print(f"Runtime root: {result['runtime_root']}")
    print(f"Database: {result['database']}")
    print(f"Backup: {result['backup']}")
    print(f"Environment: {result['environment']}")
    print(f"Python: {result['python']}")
    for plist in result["plists"]:
        print(f"LaunchAgent: {plist}")


if __name__ == "__main__":
    main()
