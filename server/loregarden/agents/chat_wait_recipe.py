"""The command an agent runs to start a child the chat-wait tool can register.

Prompt text, not code this server runs: the tool module stays free of anything
that spawns a process (`test_wait_modules_never_call_the_leave_alone_list`).
"""

from __future__ import annotations

#: How an agent starts a child in its own POSIX session. Python's
#: `start_new_session`, not `setsid(1)`: macOS ships no `setsid` binary, and the
#: wrap this replaced failed there before the command ran. The subshell keeps an
#: `exit` in the command from skipping the `.rc` write. Prints the pid to register.
DETACH_RECIPE = (
    'python3 -c \'import subprocess,sys;print(subprocess.Popen(["sh","-c",'
    '"(eval \\"$1\\") >\\"$2\\" 2>&1; echo $? >\\"$2.rc\\"","sh",sys.argv[1],sys.argv[2]],'
    "start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
    "stderr=subprocess.DEVNULL).pid)' '<command>' \"$LOG\""
)
