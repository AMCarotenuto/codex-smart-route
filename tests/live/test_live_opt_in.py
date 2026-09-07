import os

import pytest


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SMART_ROUTE_LIVE") != "1",
    reason="set SMART_ROUTE_LIVE=1 and configure an authorized catalog",
)
def test_live_routing_requires_explicit_opt_in():
    pytest.skip(
        "Run `smart-route exec` twice with authorized profiles; live billing is never automatic."
    )
