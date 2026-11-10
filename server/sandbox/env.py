"""Utility helpers for creating isolated virtual environments."""
from __future__ import annotations

import asyncio
import os
import sys
import venv
from pathlib import Path
from typing import List

# The sandbox runs model-written code, and pip install runs package build hooks
# chosen by that code. Neither may inherit the server's secrets (the backend
# imports this runner in-process, so its environment holds the Supabase service
# role key). Only this fixed allowlist passes through:
#   PATH, HOME                    find the interpreter; pip cache lives under HOME
#   LANG, LC_*, TZ                text decoding and timestamps
#   TMPDIR, TEMP, TMP             scratch space
#   PYTHON*IO/UNBUFFERED/BYTECODE output behaviour only
#   SSL_CERT_FILE, REQUESTS_CA_BUNDLE, *_PROXY  outbound HTTPS (pip, requests)
#   MPLBACKEND                    headless matplotlib
# plus anything an operator deliberately exposes with a SANDBOX_ prefix (the hook
# for a future org-scoped credential). PIP_* is not passed: an index URL can carry
# credentials, and pip's defaults are what the server's own install uses.
_SANDBOX_ENV_PASSTHROUGH = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
    "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "MPLBACKEND",
})
# Never passed, even under a SANDBOX_ prefix by mistake.
_SANDBOX_ENV_DENY_SUBSTRINGS = ("SERVICE_ROLE", "PRIMCS_AUTH_TOKEN")


def sandbox_environment() -> dict:
    """The complete environment for every sandbox subprocess (pip and the script)."""
    env = {
        k: v for k, v in os.environ.items()
        if (k in _SANDBOX_ENV_PASSTHROUGH or k.startswith("SANDBOX_"))
        and not any(s in k for s in _SANDBOX_ENV_DENY_SUBSTRINGS)
    }
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("MPLBACKEND", "Agg")
    return env


# Default libraries always installed in every sandbox environment.
_DEFAULT_PACKAGES: List[str] = ["pandas", "openpyxl", "requests"]

# Packages the server's own environment ships with (see requirements.txt). A run
# whose requirements are all covered here needs no pip install at all: the venv is
# created with system-site-packages so it sees them, and only extras are installed.
_WARM_PACKAGES = frozenset(
    {"pandas", "openpyxl", "requests", "numpy", "matplotlib", "supabase", "xlsxwriter"}
)


def _base_name(spec: str) -> str:
    """'pandas>=2.0' -> 'pandas', 'Supabase[async]' -> 'supabase'."""
    name = spec.strip().split(";")[0]
    for sep in ("[", "==", ">=", "<=", "~=", "!=", ">", "<", " "):
        name = name.split(sep)[0]
    return name.strip().lower().replace("_", "-")


async def create_virtualenv(requirements: list[str], run_dir: Path) -> Path:
    """Create a venv in run_dir/venv and install whatever is not already warm.

    The venv sees the server's site-packages, so the common data stack (pandas,
    openpyxl, matplotlib, ...) is available instantly. Anything else is installed
    with the pip wheel cache enabled, so repeat installs take seconds.
    """
    venv_dir = run_dir / "venv"
    venv.EnvBuilder(with_pip=True, clear=True, system_site_packages=True).create(venv_dir)

    python = venv_dir / ("Scripts" if sys.platform.startswith("win") else "bin") / "python"

    # Combine caller-specified requirements with default packages, then drop the
    # ones the server environment already provides.
    all_requirements = list(dict.fromkeys(requirements + _DEFAULT_PACKAGES))
    extras = [r for r in all_requirements if _base_name(r) not in _WARM_PACKAGES]

    if extras:
        proc = await asyncio.create_subprocess_exec(
            str(python),
            "-m",
            "pip",
            "install",
            "--prefer-binary",
            *extras,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sandbox_environment(),
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"pip install failed: {err.decode()}")

    return python 