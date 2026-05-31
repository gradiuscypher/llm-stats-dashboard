"""Root conftest — sets test env vars before any app modules are imported."""
import os

# Use fast argon2 parameters in tests to avoid timing-related flakiness.
os.environ["LSD_TEST_MODE"] = "1"
