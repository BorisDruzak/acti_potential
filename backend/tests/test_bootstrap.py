from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.identity.models import LocalCredential, User, UserRole


def test_concurrent_bootstrap_is_single_and_does_not_reset(factory):
    from app.identity.bootstrap import BootstrapExists, bootstrap_admin

    def attempt(_):
        try:
            with factory.begin() as db:
                return bootstrap_admin(db, "admin", "Admin", "synthetic-password-for-tests")
        except BootstrapExists:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(result is not None for result in results) == 1
    with factory.begin() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(UserRole)) == 1
        assert db.scalar(select(LocalCredential)).login == "admin"
