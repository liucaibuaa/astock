"""Run Vibe-Trading analysis in background for Feishu Bot."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import json
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(_PROJECT_ROOT / ".env", override=True)


def run_analysis_async(
    prompt: str,
    max_iter: int = 50,
    stop_event: Optional[threading.Event] = None,
) -> dict:
    """Run a Vibe-Trading analysis via CLI and return structured result.

    This calls `vibe-trading run <prompt> --no-rich --json` in a subprocess
    and parses the JSON output.

    If stop_event is provided and set, the subprocess will be killed.
    """
    start_time = time.time()

    # Build command: vibe-trading run -p "prompt" --no-rich --json
    cmd = [
        sys.executable, "-m", "cli",
        "run", "-p", prompt,
        "--no-rich",
        "--json",
    ]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PROJECT_ROOT / "agent")

    # Start process in a new session so we can kill the whole process tree
    try:
        p = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT / "agent"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except Exception as e:
        return {
            "status": "error",
            "prompt": prompt,
            "elapsed_seconds": time.time() - start_time,
            "content": f"启动分析进程失败: {str(e)}",
        }

    # Monitor thread: check stop_event and kill process tree if set
    cancelled = False
    def _monitor():
        nonlocal cancelled
        if stop_event is None:
            return
        while p.poll() is None:
            if stop_event.is_set():
                cancelled = True
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGTERM)
                    time.sleep(1)
                    if p.poll() is None:
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass  # Already exited
                except Exception as e:
                    print(f"[ERROR] Failed to kill process: {e}")
                break
            time.sleep(0.5)

    if stop_event:
        threading.Thread(target=_monitor, daemon=True).start()

    try:
        stdout, stderr = p.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except Exception:
            pass
        p.wait()
        return {
            "status": "timeout",
            "prompt": prompt,
            "elapsed_seconds": time.time() - start_time,
            "content": "分析超时（超过10分钟），请稍后重试或简化问题。",
        }

    elapsed = time.time() - start_time

    # If cancelled, return early
    if cancelled:
        return {
            "status": "cancelled",
            "prompt": prompt,
            "elapsed_seconds": elapsed,
            "content": "分析已被用户终止。",
        }

    # Parse JSON output from stdout
    output = stdout.strip()
    parsed = {}
    if output:
        try:
            for line in reversed(output.splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    parsed = json.loads(line)
                    break
        except json.JSONDecodeError:
            pass

    status = parsed.get("status", "unknown") if parsed else ("completed" if p.returncode == 0 else "failed")
    run_id = parsed.get("run_id") if parsed else None
    run_dir = parsed.get("run_dir") if parsed else None
    reason = parsed.get("reason", "") if parsed else ""

    # Try to read the actual answer content from trace.jsonl
    content = ""
    if run_dir:
        trace_path = Path(run_dir) / "trace.jsonl"
        if trace_path.exists():
            try:
                with open(trace_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "answer" and entry.get("content"):
                                content = entry["content"]
                                break
                        except json.JSONDecodeError:
                            continue
            except Exception:
                pass

    # Fallback to stdout if trace didn't have content
    if not content:
        content = output or stderr or "无输出"

    return {
        "status": status,
        "prompt": prompt,
        "run_id": run_id,
        "run_dir": run_dir,
        "content": content,
        "reason": reason,
        "elapsed_seconds": elapsed,
    }
