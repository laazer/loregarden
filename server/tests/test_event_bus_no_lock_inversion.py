"""`event_bus.publish` must not hold a process lock across its commit (778).

The inversion, in two threads:

- B has an uncommitted write in its transaction (SQLite's write lock) and
  calls `publish`, which used to block on a process-wide lock;
- A holds that lock and, inside it, commits — which needs SQLite's write
  lock, held by B — and waits out the whole busy timeout.

Reproduced deterministically here with a 2s busy timeout: A signals B the
moment it is about to commit inside `publish`, B publishes on its own open
write. With the lock, A raised "database is locked" 3 of 6 solo runs of the
concurrent-orchestration test and twice in CI; without it, both commit.
"""

from __future__ import annotations

import threading

from loregarden.core.event_bus import event_bus
from loregarden.models.domain import EventType, Workspace
from sqlalchemy import event as sa_event
from sqlmodel import Session, select


def test_a_publish_does_not_wait_on_another_sessions_open_write(isolated_db):
    @sa_event.listens_for(isolated_db, "connect")
    def _fail_fast(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA busy_timeout=2000")

    with Session(isolated_db) as setup:
        setup.add(Workspace(id="ws-a", slug="a-side", name="A", repo_path="/nowhere"))
        setup.commit()

    b_holds_write = threading.Event()
    a_about_to_commit = threading.Event()
    errors: list[BaseException] = []

    def thread_b() -> None:
        try:
            with Session(isolated_db) as session:
                session.add(Workspace(id="ws-b", slug="b-side", name="B", repo_path="/nowhere"))
                session.flush()  # SQLite write lock is now B's, uncommitted
                b_holds_write.set()
                assert a_about_to_commit.wait(10), "A never reached its commit"
                event_bus.publish(session, EventType.TICKET_CREATED, workspace_id="ws-b")
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    def thread_a() -> None:
        try:
            with Session(isolated_db) as session:
                assert b_holds_write.wait(10), "B never took the write lock"
                original = session.commit

                def signalling_commit() -> None:
                    a_about_to_commit.set()
                    original()

                session.commit = signalling_commit  # type: ignore[method-assign]
                event_bus.publish(session, EventType.TICKET_CREATED, workspace_id="ws-a")
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=thread_b), threading.Thread(target=thread_a)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not errors, [repr(e) for e in errors]
    with Session(isolated_db) as session:
        published = session.exec(select(Workspace).where(Workspace.slug == "b-side")).first()
        assert published is not None, "B's write committed"
