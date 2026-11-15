import os
import shutil
import tempfile

# Force pytest to use a temporary SQLite database before any app module imports settings/database,
# so tests can never read or write the user's real personal_erp.db.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="kitchen_pytest_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TEST_DB_DIR, 'pytest_erp.db')}"


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)

