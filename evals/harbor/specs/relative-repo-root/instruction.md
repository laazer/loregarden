# A relative LOREGARDEN_REPO_ROOT stays relative

The repository is at `/app`; the Python server is in `/app/server` (run its tests with
`cd /app/server && uv run pytest <path>`).

When `LOREGARDEN_REPO_ROOT` is set to a relative path (for example `LOREGARDEN_REPO_ROOT=..`
while working from `server/`), `Settings().repo_root` comes back relative. Every data
directory is joined onto it, so `run_log_dir` is relative too: a detached agent's shell
writes its `.out`/`.rc` files relative to its own working directory, the server reads them
relative to a different one, finds nothing, and records the run as failed with no reason.

With the default (no environment variable) the root is already absolute.

Make `Settings.repo_root` absolute whatever its source, including a relative value read
from the environment, so that a relative value is resolved against the current working
directory when the settings are loaded. Keep the existing behaviour for the default.
