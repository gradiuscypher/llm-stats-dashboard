# Users

**Related**: [Authentication](auth.md) · [Index](../index.md)

---

## Endpoints

### `POST /api/v1/users`

Register a new user account. Open self-serve; no prior auth required.

**Request body**
```json
{
  "username": "alice",
  "email": "alice@example.com",
  "password": "my-secure-password"
}
```

| Field | Required | Rules |
|-------|----------|-------|
| `username` | ✓ | Letters, numbers, `-`, `_`. Max 64 chars. |
| `email` | ✗ | Valid email format. Must be unique if provided. |
| `password` | ✓ | Minimum 8 characters. |

**Response** `201 Created` — [User object](#user-object)

**Error responses**
- `409` — username or email already taken
- `422` — validation error (password too short, invalid username chars, etc.)

**Example (curl)**
```bash
curl -X POST http://localhost:8000/api/v1/users \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "hunter22222"}'
```

**Example (Python)**
```python
import httpx

resp = httpx.post(
    "http://localhost:8000/api/v1/users",
    json={"username": "alice", "email": "alice@example.com", "password": "hunter22222"},
)
resp.raise_for_status()
user = resp.json()
print(user["id"])
```

---

### `PATCH /api/v1/users/me`

Update the current user's email or password.

**Auth**: session cookie + CSRF token required

**Request body** (all fields optional)
```json
{
  "email": "new@example.com",
  "password": "new-password"
}
```

**Response** `200 OK` — [User object](#user-object)

---

## User object

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "username": "alice",
  "email": "alice@example.com",
  "is_active": true,
  "created_at": "2025-01-01T00:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique user identifier |
| `username` | string | Login username |
| `email` | string\|null | Email address (optional) |
| `is_active` | bool | Whether the account is active |
| `created_at` | ISO-8601 datetime | Account creation time |
