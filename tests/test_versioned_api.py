from server.app import create_app
from server.security import api_routes


def test_every_api_route_is_versioned(tmp_path):
    # U01-4: clients use /api/v1, owner tools /api/v1/admin. The reachability
    # check and the container's own readiness probe (#238, answered only to a
    # direct local caller) are the two unversioned paths.
    app = create_app(tmp_path)
    unversioned = sorted(
        {
            route.path
            for route in api_routes(app)
            if not route.path.startswith("/api/v1/")
        }
    )
    assert unversioned == ["/api/health", "/api/health/ready"]


def test_owner_administration_lives_under_admin(tmp_path):
    app = create_app(tmp_path)
    paths = {route.path for route in api_routes(app)}
    for path in (
        "/api/v1/admin/queue",
        "/api/v1/admin/library",
        "/api/v1/admin/settings/downloads",
        "/api/v1/admin/migration",
        "/api/v1/admin/library/scan",
        "/api/v1/admin/warmer",
        "/api/v1/admin/access",
    ):
        assert path in paths, path
