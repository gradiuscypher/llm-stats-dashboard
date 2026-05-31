"""Password hashing with argon2id."""

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# In test mode use minimal parameters so hashing is fast and tests don't have timing issues.
# Set LSD_TEST_MODE=1 in pytest environment. Production keeps secure defaults.
_TEST_MODE = os.getenv("LSD_TEST_MODE", "").lower() in ("1", "true", "yes")
_ph = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1) if _TEST_MODE else PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
