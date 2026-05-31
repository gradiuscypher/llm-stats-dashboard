# Authentication

**Related**: [API Keys](api-keys.md) · [Users](users.md) · [Index](../index.md)

The dashboard uses **server-side session cookies** for browser authentication
and **API keys** for programmatic access. Both are completely separate auth
paths.

---

## Endpoints

### `POST /api/v1/auth/login`

Authenticate with username and password. On success, sets two cookies:

| Cookie | httpOnly | Description |
|--------|----------|-------------|
| `lsd_session` | ✓ | Opaque session ID. Never readable by JS. |
| `lsd_csrf` | ✗ | CSRF token. Must be read by JS and sent as `X-CSRF-Token` on mutating requests. |

**Request body**
```json
{
  "username": "alice",
  "password": "my-secure-password"
}
```

**Response** `200 OK`
```json
{ "message": "logged in" }
```

**Error responses**
- `401` — invalid username or password

**Example (curl)**
```bash
curl -c cookies.txt -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "hunter2"}'
```

**Example (Python)**
```python
import httpx

client = httpx.Client(base_url="http://localhost:8000/api/v1")
resp = client.post("/auth/login", json={"username": "alice", "password": "hunter2"})
resp.raise_for_status()
# cookies are now stored in client.cookies
```

**Example (JavaScript)**
```js
const resp = await fetch("/api/v1/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "alice", password: "hunter2" }),
});
```

---

### `POST /api/v1/auth/logout`

Revokes the current session and clears cookies.

**Auth**: session cookie required  
**Response** `200 OK` `{ "message": "logged out" }`

---

### `GET /api/v1/auth/me`

Returns the currently authenticated user's public profile.

**Auth**: session cookie required  
**Response** `200 OK` — see [Users schema](users.md#user-object)

---

### `GET /api/v1/auth/csrf`

Issues or refreshes a CSRF token tied to the current session.
Call this after login and store the token for use in `X-CSRF-Token`.

**Auth**: session cookie required  
**Response**
```json
{ "csrf_token": "1748728422.abc123..." }
```

---

## CSRF protection

All state-changing requests made with a **session cookie** require the
`X-CSRF-Token` header to be set. The token is read from the `lsd_csrf` cookie:

```js
// Read from cookie
const csrf = document.cookie.match(/lsd_csrf=([^;]+)/)?.[1];

fetch("/api/v1/api-keys", {
  method: "POST",
  credentials: "include",
  headers: {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrf,
  },
  body: JSON.stringify({ name: "my-key", scopes: ["logs:write"] }),
});
```

API key requests (no session cookie) are **exempt** from CSRF.

---

## OAuth scaffolding

The following endpoints exist but return `501 Not Implemented` in the current
version. They document the interface for future OAuth support:

- `GET /api/v1/auth/oauth/{provider}/authorize`
- `GET /api/v1/auth/oauth/{provider}/callback`

Planned providers: `google`, `github`.
