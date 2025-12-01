## Leads Admin Dashboard API Specification

**Base URL**: `https://eus.lat`

All endpoints are JSON APIs (except where noted) and are intended to be called from a separate React dashboard.

---

## Authentication & CORS

- **Admin authentication**
  - All admin endpoints under `/admin/api/*` require the HTTP header:
    - `X-Admin-Password: <ADMIN_PASSWORD>`
  - The password is configured on the server as `ADMIN_PASSWORD`.
  - There are no sessions or cookies; each request must send the header.

- **Login flow (dashboard)**
  - The dashboard collects a password from the user.
  - It calls `POST /admin/api/login` with that password.
  - On success, it stores the password locally (e.g. `localStorage`) and sends it in `X-Admin-Password` on all later admin API calls.

- **CORS**
  - The server accepts any browser origin:
    - `Access-Control-Allow-Origin: *`
  - Allowed methods: `GET, POST, OPTIONS`
  - Allowed request headers: `Content-Type, X-Admin-Password`
  - Preflight max age: `600` seconds.

---

## Common Error Shapes

Unless otherwise specified:

- **401 Unauthorized**
  - Returned when `X-Admin-Password` is missing or invalid.
  - Shape:
    ```json
    { "error": "unauthorized" }
    ```

- **5xx**
  - On unhandled server-side errors, a generic 5xx JSON response may be returned.

---

## Health

### GET `/_health`

- **Auth**: none
- **Description**: Simple liveness endpoint.
- **Response**
  - `200` – body: plain text `"ok"`

The dashboard can optionally ping this endpoint to display a basic "backend online" indicator.

---

## Admin Auth

### POST `/admin/api/login`

- **Auth**: none (this is how the dashboard validates the password).
- **Description**: Validate the admin password against `ADMIN_PASSWORD`.

- **Request body**
  ```json
  {
    "password": "string"
  }
  ```

- **Responses**
  - `200 OK`
    ```json
    { "ok": true }
    ```
  - `401 Unauthorized`
    ```json
    { "ok": false }
    ```
  - `500 Internal Server Error`
    ```json
    { "error": "ADMIN_PASSWORD not set on server" }
    ```

- **Dashboard usage**
  - On login form submit, call this endpoint.
  - If `ok: true`, store the password and mark the app as authenticated.
  - If `ok: false`, show an "invalid password" error.

---

## Admin: Users

### GET `/admin/api/users`

- **Auth**: required
  - Header: `X-Admin-Password: <ADMIN_PASSWORD>`
- **Description**: List users with optional filters.

- **Query parameters**
  - `user_id` (optional, integer)
    - If provided, return data only for this internal user id.
  - `telegram_id` (optional, integer)
    - If provided, return data only for this Telegram user id.
  - `limit` (optional, integer)
    - Default: `100`
    - Min: `1`, Max: `500`

- **Responses**
  - `401 Unauthorized`
    ```json
    { "error": "unauthorized" }
    ```
  - `200 OK`
    ```json
    {
      "items": [
        {
          "id": 123,
          "uid": "internal-uid",
          "telegram_id": 123456789,
          "telegram_username": "username-or-null",
          "display_name": "display-or-null",
          "locale": "en",
          "created_at": "YYYY-MM-DD HH:MM:SS"
        }
      ],
      "total": 1
    }
    ```

- **Notes**
  - If no filters are provided, returns the most recent users ordered by `id desc`, limited by `limit`.
  - `created_at` is a formatted string (`YYYY-MM-DD HH24:MI:SS` in server local timezone).

- **Dashboard usage**
  - Use to power:
    - A "Users" table view with filters for `user_id` and `telegram_id`.
    - A detail sidebar for a specific user (single-element list).

---

## Admin: Messages

### GET `/admin/api/messages`

- **Auth**: required
  - Header: `X-Admin-Password: <ADMIN_PASSWORD>`
- **Description**: Get messages for a specific user.

- **Query parameters**
  - `user_id` (required, integer)
    - The internal user id whose messages to fetch.
  - `limit` (optional, integer)
    - Default: `100`
    - Min: `1`, Max: `500`

- **Responses**
  - `401 Unauthorized`
    ```json
    { "error": "unauthorized" }
    ```
  - `200 OK` with missing or invalid `user_id`
    ```json
    { "items": [], "total": 0 }
    ```
  - `200 OK` with data
    ```json
    {
      "items": [
        {
          "id": 1,
          "role": "user",
          "content": "message text",
          "created_at": "YYYY-MM-DD HH:MM:SS"
        }
      ],
      "total": 42
    }
    ```

- **Notes**
  - Messages are ordered `id desc` (newest first).
  - `role` is typically `"user"` or `"assistant"`.
  - `created_at` uses the same string format as in the users API.

- **Dashboard usage**
  - When an admin selects a user, call this endpoint to render that user’s message history.
  - You can implement "load more" by decreasing `limit` and/or adding client-side pagination; the backend currently only supports `limit`.

---

## Non-dashboard Endpoints (for completeness)

These endpoints are part of the service but are not expected to be called by the admin dashboard.

### `*` `/tg/pay/freekassa/notify`

- Freekassa payment callback, used to mark subscriptions as paid.

### GET `/tg/pay/success`

- Small HTML page shown after successful payment.

### GET `/tg/pay/fail`

- Small HTML page shown after failed or cancelled payment.


