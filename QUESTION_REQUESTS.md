# User question requests

## Minimum input from the user

Required:

- `request_text`: the requested question idea, draft, or subject (12–1,200 characters).
- `category`: an exact main-category value from the current Sheets catalog.

Optional:

- `subcategory`: an exact subcategory from the chosen category; the generation engine may choose it when omitted.
- `scope_preference`: `global` (default) or `local`.
- `country_code`: required for `local`; must match the country/region catalog. Global requests must omit it.
- `desired_answer`: a short expected correct answer or fact (up to 500 characters).
- `source_urls`: up to three HTTPS references.
- `language_code`: `auto` by default, or one of the 35 supported language codes. For local requests the selected language must be in that region's Sheets language list. The generation target languages still come from the global/local language rules.

The client must send a Firebase Authentication ID token in `Authorization: Bearer <token>`. The server derives `user_uid` from the verified token. Clients must not send or set `user_uid`, status, duplicate-check results, timestamps, or the saved question ID. No real name, email address, phone number, or other profile field is needed in the request body.

## Firestore records

Requests are created in `question_generation_requests/{request_id}`. The first successful request creates the collection. The server stores the normalized user inputs, verified UID, state, duplicate-check placeholder, result ID, failure code, schema version, and server timestamps. A per-user daily counter is stored in `question_generation_request_limits/{uid_yyyymmdd}` and reserved in the same Firestore transaction.

Allowed future states are `queued`, `checking_duplicate`, `duplicate`, `generating`, `needs_review`, `published`, and `failed`. The initial API currently creates only `queued`; it does not yet run the duplicate checker or generation worker.

## API in this repository

- `POST /api/question-requests` — validates and queues a request. Requires Firebase Auth. Returns HTTP 202 with `request_id` and `status`.
- `GET /api/question-requests/{request_id}` — owner or admin can read status and a duplicate match/result ID; requires Firebase Auth.
- `GET /api/admin/question-requests?status=queued&limit=50` — admin custom claim (`admin: true`) required.
- `GET /api/question-request-options?category=...&country_code=...` — returns the current Sheets categories, regions, languages, and context-sensitive subcategories for the client form.
- `OPTIONS` — browser preflight. Set `QUESTION_REQUESTS_ALLOWED_ORIGINS` to an explicit comma-separated list of trusted client origins.

Default limit is five requests per Firebase UID per UTC day. Set `QUESTION_REQUESTS_DAILY_LIMIT` to change it (1–100).
The admin query filtered by status and sorted by `created_at` needs the Firestore composite index `status ASC, created_at DESC` before that view is used.

## Important integration boundary

These endpoints are currently part of the desktop automation API. `ui_server.py` binds to `127.0.0.1` by default, so it is not reachable by app users outside that computer. Do not expose that whole server publicly: its existing generation endpoints are not Firebase-authenticated. Before users can submit from the Knowley app or website, move these three request routes into an isolated authenticated backend service (or a dedicated Cloud Run deployment) and connect the chosen client form. The current work does not create Firestore documents, publish/deploy a backend, or start processing queued requests.

The semantic duplicate index/checker and the request processor are still required before a request can be safely auto-published. The existing question writer already fills the ordinary 1.1.0 question schema, but this intake API does not call it yet.
