"""Give `blobert-tdd`'s implement stage a backend specialist to dispatch.

Split out rather than appended to `migrations_templates`, which is already past
the size the organization gate allows a file to grow to; `migrations_ux_lanes`
and `migrations_ux_coverage` are the same shape for the same reason.
"""

from __future__ import annotations

import json

from loregarden.db.migration_utils import table_exists
from loregarden.db.migrations_templates import snapshot_template_version
from sqlalchemy import text
from sqlalchemy.engine import Connection

#: The stage whose roster had no backend lane, and the agent that fills it.
_STAGE_KEY = "implement"
_BACKEND_AGENT = "implementation_backend"
#: The route this one is inserted ahead of. Scoring is order-independent — the
#: max wins — so position only decides ties, and a tie between the frontend and
#: backend lanes on a backend ticket should not go to the frontend one.
_FRONTEND_AGENT = "implementation_frontend"

#: Deliberately does NOT declare `api`, `endpoint`, `route` and the rest:
#: `studio_routing._SPECIALTY_SYNONYMS["backend"]` already carries them as
#: synonyms, worth `_SYNONYM_HIT`. Declaring one as a specialty too promotes it
#: to a `_DIRECT_HIT`, which alone clears `_OVERRIDE_DEFAULT_SCORE` — so a
#: ticket that merely says "API" once would outrank both the declared default
#: and an operator's explicit `next_agent` pin. That is the same
#: one-incidental-word failure as the frontend lane's, pointed the other way.
#:
#: `pipeline` and `tooling` are declared because no synonym list owns them, and
#: they are how the Python work outside the web app reaches this lane.
_BACKEND_ROUTE = {
    "languages": ["python"],
    "specialties": ["backend", "pipeline", "tooling"],
    "agent_id": _BACKEND_AGENT,
    "skill_name": "",
}


def m_blobert_backend_lane(conn: Connection) -> None:
    """Add a Python/FastAPI route to `blobert-tdd`'s implement roster.

    The stage fielded core_simulation, gameplay_systems, presentation,
    engine_integration and implementation_frontend — every lane the Godot game
    needs and none for `asset_generation/web/backend/**`. `blob-procedural-sdf-25`,
    a FastAPI ticket, scored onto the frontend lane on the single word "layout"
    and was correctly declined; five runs exited `succeeded` having committed
    nothing before a human read the checkpoints. `ClassifyBasis.UNROUTEABLE`
    records that shape now, but recording it only names the defect — the stage
    still has nobody to send the work to until the roster grows a lane.

    `backend_implementer` was not reused: its role body scopes ownership to
    `/server/**` and describes this repository's Django/loremaker layout, so on
    blobert it would disown the same paths the frontend agent did.

    Guarded and idempotent: a migration runs against schemas older than itself,
    and re-running must not add the route twice.
    """
    if not table_exists(conn, "workflow_templates"):
        return
    row = (
        conn.execute(
            text("SELECT id, version, stages_json FROM workflow_templates WHERE slug='blobert-tdd'")
        )
        .mappings()
        .first()
    )
    if not row:
        return

    stages = json.loads(row["stages_json"] or "[]")
    stage = next((item for item in stages if item.get("key") == _STAGE_KEY), None)
    if stage is None:
        return
    routes = stage.get("classify_routes") or []
    if any(route.get("agent_id") == _BACKEND_AGENT for route in routes):
        return

    insert_at = next(
        (index for index, route in enumerate(routes) if route.get("agent_id") == _FRONTEND_AGENT),
        len(routes),
    )
    routes.insert(insert_at, dict(_BACKEND_ROUTE))
    stage["classify_routes"] = routes

    new_version = int(row["version"] or 1) + 1
    conn.execute(
        text("UPDATE workflow_templates SET stages_json=:st, version=:v WHERE id=:id"),
        {"st": json.dumps(stages), "v": new_version, "id": row["id"]},
    )
    snapshot_template_version(conn, row["id"], new_version, "Backend lane for the implement stage")


__all__ = ["m_blobert_backend_lane"]
