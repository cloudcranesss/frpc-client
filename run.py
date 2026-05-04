from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys


REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic")
AUTO_INSTALL_ENV = "FRP_PANEL_AUTO_INSTALL_DEPS"


def _missing_modules() -> list[str]:
    missing: list[str] = []
    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(module_name)
    return missing


def _auto_install_enabled() -> bool:
    raw = os.getenv(AUTO_INSTALL_ENV, "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _install_requirements(requirements_file: Path) -> None:
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements_file),
        ]
    )


def ensure_runtime_dependencies() -> None:
    missing = _missing_modules()
    if not missing:
        return

    requirements_file = Path(__file__).resolve().parent / "requirements.txt"
    if not _auto_install_enabled():
        raise RuntimeError(
            f"Missing Python dependencies: {', '.join(missing)}. "
            f"Install manually with: {sys.executable} -m pip install -r {requirements_file}"
        )
    if not requirements_file.exists():
        raise FileNotFoundError(f"requirements file not found: {requirements_file}")

    print(f"[bootstrap] Missing deps detected: {', '.join(missing)}")
    print(f"[bootstrap] Installing from {requirements_file} ...")
    _install_requirements(requirements_file)

    missing_after = _missing_modules()
    if missing_after:
        raise RuntimeError(f"Dependency install incomplete, still missing: {', '.join(missing_after)}")


if __name__ == "__main__":
    ensure_runtime_dependencies()
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
