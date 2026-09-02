# M2 HTTP API

All M2 identity and conversation routes are under `/api/v1`. Errors use
`{"error":{"code":"...","message":"..."}}`; authentication failures are `401`, explicit
role failures are `403`, and inaccessible conversations are the same safe `404` whether absent or
owned by another customer or tenant.

| Method | Path | Access | Result |
|---|---|---|---|
| GET | `/auth/demo-personas` | Public, demo mode only | Synthetic allowlisted personas; disabled mode returns 401. |
| POST | `/auth/demo-login` | Public, demo mode only | Customer session cookie for organization slug + persona key. |
| POST | `/auth/staff-login` | Public | Staff session cookie after password and active-membership checks. |
| GET | `/auth/me` | Authenticated | Session-derived kind, organization, subject and staff role. |
| POST | `/auth/logout` | Authenticated | Revokes the hashed server session and clears the cookie. |
| GET | `/auth/admin-check` | Admin | Minimal RBAC verification endpoint. |
| POST | `/conversations` | Customer | Creates a customer-owned conversation; supplied identity fields are ignored. |
| GET | `/conversations` | Customer | Lists only the authenticated customer's conversations (`limit`, `offset`). |
| GET | `/conversations/{id}` | Owner or organization staff | Returns one authorized conversation. |
| POST | `/conversations/{id}/messages` | Owner or organization staff | Persists a customer/staff message without AI generation. |
| GET | `/conversations/{id}/messages` | Owner or organization staff | Stable ascending sequence pagination (`after_sequence`, `limit`). |

Cookies are HttpOnly, path `/`, expire with the server session, and have configurable `Secure` and
`SameSite=lax|strict` attributes. Browser payload IDs never establish identity or tenant scope.
