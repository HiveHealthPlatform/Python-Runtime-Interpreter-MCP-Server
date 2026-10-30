"""Orchestrate sandbox execution of untrusted Python code."""
from __future__ import annotations

import asyncio
import base64
import shutil
import textwrap
from pathlib import Path
from typing import TypedDict, List
import mimetypes

from server.config import TIMEOUT_SECONDS, TMP_DIR
import os

# The sandbox runs model-written code. It must not inherit the server's secrets
# (Supabase service key and the like). Only a fixed set of harmless variables is
# passed through, plus anything deliberately exposed with a SANDBOX_ prefix.
_SANDBOX_ENV_PASSTHROUGH = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP",
    "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "MPLBACKEND",
)


def sandbox_environment() -> dict:
    env = {k: v for k, v in os.environ.items() if k in _SANDBOX_ENV_PASSTHROUGH or k.startswith("SANDBOX_")}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("MPLBACKEND", "Agg")
    return env
from server.sandbox.downloader import download_files
from server.sandbox.env import create_virtualenv

__all__ = ["run_code"]


# Precise schema for each artifact entry.
class ArtifactMeta(TypedDict):
    name: str
    relative_path: str
    size: int
    mime: str
    data: str  # base64 encoded file content


# Typed return for run_code results.
class RunCodeResult(TypedDict, total=False):
    """Result of running code in the sandbox.
    Optionally includes a feedback field with suggestions or warnings (list of strings).
    """
    stdout: str
    stderr: str
    artifacts: List[ArtifactMeta]
    feedback: str
    script_code: str
    requirements: List[str]


async def run_code(
    *,
    code: str,
    requirements: list[str],
    files: list[dict[str, str]],
    run_id: str,
    session_id: str | None = None,
) -> RunCodeResult:
    """Execute *code* inside an isolated virtual-env and return captured output. Artifacts are returned as paths relative to the output directory. Only files inside output/ are included."""

    if session_id:
        # Persist workspace for the lifetime of the client session.
        work = TMP_DIR / f"session_{session_id}"
        work.mkdir(parents=True, exist_ok=True)
    else:
        # Legacy per-run workspace (stateless behaviour).
        work = TMP_DIR / f"run_{run_id}"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)

    # Ensure mounts directory exists for all modes.
    (work / "mounts").mkdir(parents=True, exist_ok=True)
    # Directory where user code should place output/artifacts.
    (work / "output").mkdir(parents=True, exist_ok=True)

    await download_files(files, work / "mounts")

    py = await create_virtualenv(requirements, work)

    script_name = f"script_{run_id}.py" if session_id else "script.py"
    script = work / script_name
    script.write_text(textwrap.dedent(code))

    proc = await asyncio.create_subprocess_exec(
        str(py),
        str(script),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work,
        env=sandbox_environment(),
    )

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"Execution timed out after {TIMEOUT_SECONDS}s")

    # Collect artifacts inside the output directory.
    artifacts: list[ArtifactMeta] = []
    output_dir = work / "output"
    for p in output_dir.rglob("*"):
        if p.is_file():
            try:
                rel_path = p.relative_to(output_dir)
            except ValueError:
                continue  # skip files not in output_dir
            size = p.stat().st_size
            mime, _ = mimetypes.guess_type(str(p))
            
            # Read file and encode as base64
            with open(p, 'rb') as f:
                file_data = base64.b64encode(f.read()).decode('utf-8')
            
            print(f"[PRIMCS] Collected artifact: {rel_path.name}, size: {size}, mime: {mime}")
            artifacts.append({
                "name": rel_path.name,
                "relative_path": rel_path.as_posix(),
                "size": size,
                "mime": mime or "application/octet-stream",
                "data": file_data,  # NEW: Include base64 encoded file data
            })
    
    print(f"[PRIMCS] Total artifacts collected: {len(artifacts)}")

    return {
        "stdout": out.decode(),
        "stderr": err.decode(),
        "artifacts": artifacts,
        "script_code": code,
        "requirements": requirements,
    } 