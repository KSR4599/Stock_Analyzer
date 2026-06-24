from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "deploy" / "install_local_runtime.py"
)
SPEC = importlib.util.spec_from_file_location("install_local_runtime", MODULE_PATH)
assert SPEC and SPEC.loader
runtime_installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime_installer)


def test_runtime_install_migrates_once_and_preserves_state(tmp_path) -> None:
    source_db = tmp_path / "source.sqlite3"
    source_db.write_bytes(b"source-database")
    source_env = tmp_path / "source.env"
    source_env.write_text("STOCK_ANALYZER_DB_PATH=data/stock_analyzer.sqlite3\n")
    runtime_root = tmp_path / "runtime"

    first = runtime_installer.install_runtime(
        runtime_root,
        source_db,
        source_env,
        install_package=False,
        install_plists=False,
    )

    assert first["database_migrated"] is True
    assert first["database"].read_bytes() == b"source-database"
    assert first["environment"].read_text().startswith("STOCK_ANALYZER_DB_PATH")
    assert first["database"].stat().st_mode & 0o777 == 0o600
    first["database"].write_bytes(b"runtime-state")
    first["environment"].write_text("PRESERVE_ME=yes\n")

    second = runtime_installer.install_runtime(
        runtime_root,
        source_db,
        source_env,
        install_package=False,
        install_plists=False,
    )

    assert second["database_migrated"] is False
    assert second["database"].read_bytes() == b"runtime-state"
    assert second["environment"].read_text() == "PRESERVE_ME=yes\n"
    assert second["backup"].read_bytes() == b"runtime-state"
