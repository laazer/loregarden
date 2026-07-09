"""Single entrypoint for running the control plane: `python -m loregarden`.

Used both for local dev (spawned by the Tauri shell with --reload) and as the
PyInstaller build target for the packaged desktop sidecar — one code path
instead of the dev/prod command strings drifting apart.
"""

import argparse
import os
import sys
import threading
import time

import uvicorn


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def _watch_parent(parent_pid: int, poll_interval: float = 2.0) -> None:
    """Self-terminate if the desktop shell that launched us disappears.

    Tauri's own shutdown hooks (RunEvent::ExitRequested, OS signals) are the
    primary way the desktop app stops this process cleanly — but they depend
    on the parent getting a chance to run cleanup code at all, which isn't
    guaranteed for every way a process can end (a crash, a forceful kill, a
    platform-level quit path that bypasses them). This is the backstop.

    Checks the specific `parent_pid` we were told about (via
    LOREGARDEN_PARENT_PID), not os.getppid(): under PyInstaller's --onefile
    bootloader, our real OS parent is the bootloader's own child-launcher
    process, not the Tauri app that actually spawned us, so getppid() never
    changes even after the real parent is long gone.

    Uses os._exit() rather than a self-delivered SIGTERM: this is a last-
    resort backstop after the parent is already gone, not a negotiated
    shutdown, and a hard exit is what's actually verified to reliably tear
    down this process (and, since it's the frozen bootloader's own child,
    the bootloader wrapper along with it) in every environment tested.
    """
    while True:
        time.sleep(poll_interval)
        if not _process_alive(parent_pid):
            print("parent process is gone — shutting down", file=sys.stderr)
            sys.stderr.flush()
            os._exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="loregarden")
    parser.add_argument("--host", default=os.environ.get("LOREGARDEN_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("LOREGARDEN_PORT", "8000"))
    )
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--log-level", default=os.environ.get("LOREGARDEN_LOG_LEVEL", "info")
    )
    args = parser.parse_args()

    parent_pid = int(os.environ.get("LOREGARDEN_PARENT_PID", os.getppid()))
    threading.Thread(
        target=_watch_parent, args=(parent_pid,), daemon=True, name="parent-watchdog"
    ).start()

    uvicorn.run(
        "loregarden.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
