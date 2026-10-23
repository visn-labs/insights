#!/usr/bin/env python3
"""
setup_and_run.py — All-in-one bootstrap for the Visn Phase 0 project.

Checks / installs every dependency, creates the Python 3.12 virtual-environment
the Rust service expects (`.venv/bin/python`), and then builds + launches the
service with `cargo run`.

Usage:
    python3 setup_and_run.py            # full setup + run
    python3 setup_and_run.py --setup    # setup only, do not start the service
    python3 setup_and_run.py --run      # skip setup, just cargo run

Requirements to execute THIS script:
    • macOS (Apple Silicon or Intel)
    • Python ≥ 3.10 already on $PATH (needed only to run this script itself)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
REQUIREMENTS = PROJECT_DIR / "tools" / "requirements-yolo.txt"
ENV_EXAMPLE = PROJECT_DIR / ".env.example"
ENV_FILE = PROJECT_DIR / ".env"
RUST_TOOLCHAIN_CHANNEL = "1.87.0"
PYTHON_VERSION = "3.12"

# ANSI helpers
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def banner(msg: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {msg}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 60}{RESET}\n")


def info(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗ {msg}{RESET}", file=sys.stderr)
    sys.exit(1)


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess with nice logging."""
    merged_env = {**os.environ, **(env or {})}
    print(f"  {BOLD}${RESET} {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        env=merged_env,
        cwd=cwd or PROJECT_DIR,
    )


# ── Checks ───────────────────────────────────────────────────────────────────

def check_macos() -> None:
    if platform.system() != "Darwin":
        warn("This script is designed for macOS. Proceeding anyway …")


def find_python312() -> str:
    """Return the path to a usable Python 3.12 interpreter."""
    candidates = [
        f"python{PYTHON_VERSION}",
        "python3.12",
        "python3",
        "python",
    ]
    for name in candidates:
        exe = shutil.which(name)
        if exe is None:
            continue
        try:
            result = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, check=True
            )
            version_str = result.stdout.strip().split()[-1]  # e.g. "3.12.4"
            major, minor = (int(x) for x in version_str.split(".")[:2])
            if major == 3 and minor == 12:
                info(f"Found Python {version_str} → {exe}")
                return exe
        except (subprocess.CalledProcessError, ValueError):
            continue

    # Try pyenv
    pyenv = shutil.which("pyenv")
    if pyenv:
        result = subprocess.run(
            [pyenv, "versions", "--bare"], capture_output=True, text=True, check=False
        )
        for line in result.stdout.splitlines():
            if line.strip().startswith("3.12"):
                pyenv_python = Path.home() / ".pyenv" / "versions" / line.strip() / "bin" / "python3"
                if pyenv_python.is_file():
                    info(f"Found Python 3.12 via pyenv → {pyenv_python}")
                    return str(pyenv_python)

    # Not found — offer to install
    warn("Python 3.12 is not installed.")
    if shutil.which("brew"):
        print(f"\n  {BOLD}Installing Python 3.12 via Homebrew …{RESET}")
        run(["brew", "install", "python@3.12"])
        exe = shutil.which("python3.12")
        if exe:
            info(f"Homebrew installed Python 3.12 → {exe}")
            return exe
    elif pyenv:
        print(f"\n  {BOLD}Installing Python 3.12 via pyenv …{RESET}")
        run([pyenv, "install", "3.12"])
        result = subprocess.run(
            [pyenv, "prefix", "3.12"], capture_output=True, text=True, check=True
        )
        exe = str(Path(result.stdout.strip()) / "bin" / "python3")
        info(f"pyenv installed Python 3.12 → {exe}")
        return exe

    fail(
        "Cannot find or install Python 3.12.\n"
        "  Install it manually:  brew install python@3.12  OR  pyenv install 3.12"
    )
    return ""  # unreachable


# ── Rust toolchain ───────────────────────────────────────────────────────────

def ensure_rust() -> None:
    banner("Rust Toolchain")
    rustup = shutil.which("rustup")
    if rustup is None:
        cargo = shutil.which("cargo")
        if cargo is None:
            warn("Neither rustup nor cargo found. Installing Rust via rustup …")
            run(["sh", "-c", "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"])
            # Reload PATH
            cargo_bin = Path.home() / ".cargo" / "bin"
            os.environ["PATH"] = f"{cargo_bin}:{os.environ['PATH']}"
            rustup = str(cargo_bin / "rustup")
        else:
            info(f"cargo found at {cargo} (no rustup — skipping toolchain pin)")
            return

    # Ensure the exact toolchain the project needs
    info(f"Ensuring Rust {RUST_TOOLCHAIN_CHANNEL} …")
    run([rustup or "rustup", "toolchain", "install", RUST_TOOLCHAIN_CHANNEL, "--profile", "minimal",
         "--component", "rustfmt", "--component", "clippy"], check=False)
    info("Rust toolchain ready.")


# ── Python venv ──────────────────────────────────────────────────────────────

def ensure_venv(python_exe: str) -> Path:
    banner("Python 3.12 Virtual Environment")
    venv_python = VENV_DIR / "bin" / "python"

    if venv_python.is_file():
        # Verify it's actually 3.12
        result = subprocess.run(
            [str(venv_python), "--version"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and "3.12" in result.stdout:
            info(f"Existing .venv is Python 3.12 — reusing")
        else:
            warn("Existing .venv is NOT Python 3.12 — recreating …")
            shutil.rmtree(VENV_DIR)
            run([python_exe, "-m", "venv", str(VENV_DIR)])
    else:
        info(f"Creating .venv with {python_exe} …")
        run([python_exe, "-m", "venv", str(VENV_DIR)])

    info(f".venv ready at {VENV_DIR}")
    return venv_python


def install_python_deps(venv_python: Path) -> None:
    banner("Python Dependencies")
    pip = str(VENV_DIR / "bin" / "pip")

    # Upgrade pip + wheel first
    info("Upgrading pip, setuptools, wheel …")
    run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])

    if REQUIREMENTS.is_file():
        info(f"Installing from {REQUIREMENTS.name} …")
        run([pip, "install", "-r", str(REQUIREMENTS)])
    else:
        warn(f"{REQUIREMENTS} not found — installing defaults …")
        run([pip, "install", "ultralytics>=8.4,<9", "opencv-python>=4.10,<5", "imageio-ffmpeg>=0.5,<0.7"])

    info("Python dependencies installed.")


# ── .env file ────────────────────────────────────────────────────────────────

def ensure_env_file() -> None:
    banner("Environment Configuration")
    if ENV_FILE.is_file():
        info(f".env already exists — not overwriting")
    elif ENV_EXAMPLE.is_file():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)
        info(f"Copied .env.example → .env")
    else:
        warn("No .env.example found — skipping .env creation")

    # Ensure data directories exist
    data_dir = PROJECT_DIR / "data"
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (data_dir / "memory").mkdir(parents=True, exist_ok=True)
    info("data/uploads and data/memory directories ready.")


# ── Build & Run ──────────────────────────────────────────────────────────────

def cargo_build() -> None:
    banner("Building Rust Service")
    run(["cargo", "build"], cwd=PROJECT_DIR)
    info("cargo build succeeded.")


def cargo_run() -> None:
    banner("Starting Visn Phase 0")
    print(textwrap.dedent(f"""\
        {GREEN}Service will start on http://127.0.0.1:8080{RESET}
        {YELLOW}Press Ctrl+C to stop.{RESET}
    """))

    env_overrides: dict[str, str] = {}

    # Auto-set detector executable to the venv python
    venv_python = VENV_DIR / "bin" / "python"
    if venv_python.is_file():
        env_overrides["VISN_DETECTOR_EXECUTABLE"] = str(venv_python)

    # If .env exists, load it into the environment (simple key=value parsing)
    if ENV_FILE.is_file():
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()
                    # Don't override what's already set in the real environment
                    if key not in os.environ:
                        env_overrides[key] = val

    try:
        run(["cargo", "run"], env=env_overrides, cwd=PROJECT_DIR, check=True)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Service stopped.{RESET}")


# ── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="All-in-one setup and runner for Visn Phase 0"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--setup", action="store_true", help="Setup only — do not start the service"
    )
    group.add_argument(
        "--run", action="store_true", help="Skip setup — just build and run"
    )
    args = parser.parse_args()

    os.chdir(PROJECT_DIR)

    if args.run:
        cargo_run()
        return

    # ── Full Setup ──────────────────────────────────────────────────────
    banner("Visn Phase 0 — Setup & Run")
    check_macos()

    # 1. Rust
    ensure_rust()

    # 2. Python 3.12
    python_exe = find_python312()

    # 3. Virtual environment
    venv_python = ensure_venv(python_exe)

    # 4. Python packages
    install_python_deps(venv_python)

    # 5. .env
    ensure_env_file()

    # 6. Build (catches compile errors before interactive run)
    cargo_build()

    if args.setup:
        banner("Setup Complete ✓")
        print(textwrap.dedent(f"""\
            Everything is ready. Start the service with:

              {BOLD}cargo run{RESET}
              {BOLD}python3 setup_and_run.py --run{RESET}

            Then open {CYAN}http://127.0.0.1:8080{RESET}
        """))
        return

    # 7. Run
    cargo_run()


if __name__ == "__main__":
    main()
