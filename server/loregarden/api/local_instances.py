"""Local instances: launch, list and stop branch servers and clients.

The endpoints are lore-eden's (`lore_eden.instances.make_instances_router`);
what loregarden contributes is the templates, in `services/local_instances.py`.
"""

from lore_eden.instances import make_instances_router
from loregarden.services.local_instances import get_instance_manager

router = make_instances_router(get_instance_manager)
