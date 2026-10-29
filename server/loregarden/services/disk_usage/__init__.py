"""Where the disk went, and a careful way to get some of it back.

- `roots`: what is scanned — every workspace, worktrees of theirs that live
  outside the checkout, and folders a person added.
- `scanner`: one metadata-only walk into a pruned size tree. Names and sizes,
  never contents.
- `store`: the background scan job and the last finished tree, on disk.
- `safety`: the only place that decides whether a path may be cleaned.
- `cleanup`: preview, then execute a plan the person confirmed; moves to the Trash.
"""
