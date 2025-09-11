#!/usr/bin/env python3
"""
Startup script for the Railway Control System backend.
Creates/uses a local virtual environment, installs dependencies,
and starts the FastAPI server with Uvicorn.
"""

import subprocess
import sys
import os
from pathlib import Path
from shutil import which

def run(cmd, check=True):
    print(">", " ".join(map(str, cmd)))
    return subprocess.run(cmd, check=check)

def main():
    repo_root = Path(__file__).resolve().parent
    backend_dir = repo_root / "backend"
    if not backend_dir.exists():
        print(f"❌ backend directory not found at: {backend_dir}")
        sys.exit(1)

    os.chdir(backend_dir)
    print("🚂 Starting Railway Control System Backend…")
    print(f"📁 Working directory: {os.getcwd()}")

    # Create venv if missing
    venv_path = Path("venv")
    if not venv_path.exists():
        print("📦 Creating virtual environment…")
        run([sys.executable, "-m", "venv", str(venv_path)])

    # Resolve venv executables (cross‑platform)
    if os.name == "nt":
        python_path = venv_path / "Scripts" / "python.exe"
        pip_path = venv_path / "Scripts" / "pip.exe"
    else:
        python_path = venv_path / "bin" / "python"
        pip_path = venv_path / "bin" / "pip"

    if not python_path.exists():
        print(f"❌ venv python not found at: {python_path}")
        sys.exit(1)

    # Ensure pip available and up to date (best effort)
    print("📥 Upgrading pip (best effort)…")
    run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"], check=False)

    # Install dependencies
    req_file = Path("requirements.txt")
    if not req_file.exists():
        print(f"❌ requirements.txt not found at: {req_file}")
        sys.exit(1)

    print("📥 Installing dependencies…")
    # Use python -m pip to avoid Windows PATH issues with pip.exe
    run([str(python_path), "-m", "pip", "install", "-r", str(req_file)])

    # Read server settings from environment (with sensible defaults)
    host = os.getenv("HOST", "0.0.0.0")
    port = os.getenv("PORT", "8000")
    reload_flag = os.getenv("RELOAD", "true").lower() in ("1", "true", "yes")

    print(f"🚀 Starting FastAPI server on http://{host}:{port}")
    print("📡 WebSocket endpoint:", f"ws://{host}:{port}/ws")
    print("🛑 Press Ctrl+C to stop the server")
    print("-" * 50)

    uvicorn_cmd = [
        str(python_path), "-m", "uvicorn",
        "main:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload_flag:
        uvicorn_cmd.append("--reload")

    try:
        run(uvicorn_cmd)
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error starting server: {e}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
