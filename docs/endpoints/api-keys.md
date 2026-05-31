# API Keys

**Related**: [Authentication](auth.md) · [Log Ingestion](logs.md) · [Index](../index.md)

API keys allow programmatic access to the LLM Stats Dashboard API — primarily
for LLM clients that need to POST log entries. Keys are:

- **Scoped**: each key has explicit permissions (`logs:write`, `logs:read`)
- **Named**: create multiple keys per account (e.g. `laptop`, `ci-server`)
- **Revocable**: revoke any key instantly, with no effect on others
- **Audited**: `last_used_at` is updated on each successful use
- **Hashed**: the raw secret is shown **once** at creation and never stored

Key format: `lsd_<8-char-prefix>_<secret>`  
Example: `lsd_aB3xYz12_Kq7mN...`

---

## Available scopes

| Scope | Description |
|-------|-------------|
| `logs:write` | POST to `/logs` (ingest LLM calls) |
| `logs:read` | GET `/logs`, `/logs/{id}`, `/conversations/{id}`, `/stats/summary` |

---

## Usage

Send the raw key in the `X-API-Key` header:

```
X-API-Key: lsd_aB3xYz12_Kq7mN...
```

---

## Endpoints

All key management endpoints require a **session cookie + CSRF token**.

### `GET /api/v1/api-keys`

List all API keys for the current user. Never returns raw secrets.

**Response** `200 OK` — array of [API key objects](#api-key-object)

---

### `POST /api/v1/api-keys`

Create a new API key.

**Auth**: session cookie + CSRF token  
**Request body**
```json
{
  "name": "laptop",
  "scopes": ["logs:write"]
}
```

**Response** `201 Created` — [API key object](#api-key-object) with additional `raw_key` field.

> ⚠️ **`raw_key` is returned only once.** Store it securely. It cannot be
> retrieved again.

**Example (curl)**
```bash
# Assumes you're logged in and have the CSRF token in $CSRF
curl -b cookies.txt \
  -H "X-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -X POST http://localhost:8000/api/v1/api-keys \
  -d '{"name": "laptop", "scopes": ["logs:write"]}'
```

**Example (Python)**
```python
# client already has session cookies from login
resp = client.post(
    "/api/v1/api-keys",
    json={"name": "laptop", "scopes": ["logs:write"]},
    headers={"X-CSRF-Token": csrf_token},
)
api_key = resp.json()["raw_key"]  # save this!
```

---

### `DELETE /api/v1/api-keys/{id}`

Permanently revoke an API key. Cannot be undone.

**Auth**: session cookie + CSRF token  
**Response** `204 No Content`

**Example (curl)**
```bash
curl -b cookies.txt \
  -H "X-CSRF-Token: $CSRF" \
  -X DELETE http://localhost:8000/api/v1/api-keys/550e8400-e29b-41d4-a716-446655440000
```

---

## API key object

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "laptop",
  "prefix": "lsd_aB3xYz12",
  "scopes": ["logs:write"],
  "last_used_at": "2025-06-01T12:00:00Z",
  "revoked_at": null,
  "created_at": "2025-01-01T00:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Key identifier (use for revocation) |
| `name` | string | Human-readable label you chose |
| `prefix` | string | Short visible prefix (e.g. `lsd_aB3xYz12`) shown in UI |
| `scopes` | string[] | Permissions granted to this key |
| `last_used_at` | ISO-8601\|null | Last successful use timestamp |
| `revoked_at` | ISO-8601\|null | Revocation timestamp (null = active) |
| `created_at` | ISO-8601 | Creation timestamp |
