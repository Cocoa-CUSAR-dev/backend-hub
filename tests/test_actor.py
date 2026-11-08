import uuid

import pytest

from src.actor import Actor, require
from src.exceptions import Forbidden


def _farmer_and_processor() -> Actor:
    return Actor(
        user_id=uuid.uuid4(),
        roles=frozenset({"farmer", "processor"}),
        perms=frozenset({"read:farm:own", "read:batch:own"}),
        channel="api",
    )


def test_permissions_from_all_roles_count() -> None:
    actor = _farmer_and_processor()
    assert actor.can("read:farm:own")
    assert actor.can("read:batch:own")
    assert actor.has_role("farmer") and actor.has_role("processor")


def test_require_raises_forbidden_naming_the_permission() -> None:
    with pytest.raises(Forbidden, match="read:farm:all"):
        require(_farmer_and_processor(), "read:farm:all")


def test_require_passes_silently_when_allowed() -> None:
    require(_farmer_and_processor(), "read:farm:own")


def test_system_actor_can_do_anything() -> None:
    system = Actor.system()
    assert system.user_id is None
    assert system.channel == "system"
    assert system.can("delete:researcher:any")
