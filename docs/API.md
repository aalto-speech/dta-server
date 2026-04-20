# API reference

These endpoints can be accessed at `http://<host>:<port>/api/v1/docs` when the app is running. See [Development guide](DEVELOPMENT.md) for local setup and running instructions.

## Endpoints

- `GET /ping`: health check.
- `GET /status`: app status and uptime.
- `POST /analytics/comparison`: cohort comparison stats for a user.
- `POST /request/user`: delete or export request submission.
- `POST /feedback`: feedback submission.
- `POST /speech/assess`: WAV upload and speech scoring.
- `POST /onboarding`: create a user from onboarding data.
- `DELETE /users`: admin user deletion.

## Request notes

- The app is served behind a `/api/v1` root path.
- Most write endpoints accept form data.
- `POST /speech/assess` requires multipart form data with a `.wav` file.
- `DELETE /users` requires header `X-API-Key` and form field `guid`.
