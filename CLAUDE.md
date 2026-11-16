# garmin-mcp

Remote MCP server for a user's own Garmin Connect data, used from Claude and ChatGPT (web, desktop, mobile). Python, MCP SDK 2.x (`mcp.server.mcpserver.MCPServer`), Garmin's official `garmin-connect-personal-api-python` client, Garmin's `garmin-fit-sdk` for FIT decoding.

## Shape

- `app.py`: one tool per endpoint in Garmin's Personal API OpenAPI spec (26), with the spec's parameters and defaults. Stateless streamable HTTP (`stateless_http=True`, `json_response=True`) so it runs on serverless.
- `fit.py`: unzips `download_activity`'s original file and decodes FIT into JSON (named message types + downsampled records).
- Auth: clients send their Garmin Personal API key as `Authorization: Bearer <key>`. `require_garmin_key` ASGI middleware rejects requests without a key Garmin accepts (401) before MCP runs. The server stores no secrets; it exchanges the key for a 24h Garmin token, cached per key hash in `/tmp/garmin-tokens/`.
- Garmin's spec is embedded in their swagger docs page bundle (developer.garmin.com/gc-developer-program/personal/swagger-docs/); the client library wraps only 24 of the 26 endpoints and few optional params, so tools call paths directly via `client.get`.

## Constraints

- Garmin's personal API terms: individual use of your own data, "not intended for commercial applications serving multiple end users". A hosted multi-user product needs Garmin's business developer program (real Garmin OAuth, no user-pasted keys).
- Claude custom connectors take a static `Authorization` header via "Request headers"; ChatGPT uses its "Token" auth option. Both work today.

## Deploy

- Vercel personal account, scope `adam-ginzberg`, project `garmin-mcp`. Production URL: https://garmin-mcp-adam-ginzberg.vercel.app/mcp
- The CLI's default login is the work account, so always pass the personal config dir:
  `npx vercel@latest --global-config ~/.vercel-personal --scope adam-ginzberg deploy --prod --yes`
- Vercel Authentication (SSO protection) is disabled for this project on purpose: the Garmin key check is the gate.
- Git auto-deploy is not connected yet (Vercel GitHub app not installed on `adamginzberg`).

## GitHub

- Repo: https://github.com/adamginzberg/garmin-mcp (public). In `gh` the keyring account is still labeled `adam-codaio` (renamed to `adamginzberg`); the active `gh` account is the work account `adam-subframe`. Push with the personal token without switching accounts:
  `git -c credential.helper= push "https://x-access-token:$(gh auth token --user adam-codaio)@github.com/adamginzberg/garmin-mcp.git" main`

## Develop

```sh
uv sync
uv run uvicorn app:app --reload
```

For end-to-end tests without a real key, point the server at a fake Garmin with `GARMIN_PERSONAL_API_BASE_URL=http://127.0.0.1:<port>` (the client honors it for both token exchange and data calls).
