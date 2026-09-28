"""Row builders and data scenarios shared by the test suite and the dev tools.

In the package rather than under `tests/` so that `loregarden sandbox seed` can
build its production-shaped database from the very factories the integration
tests use — one way of making a valid row, not two that drift apart.
"""
