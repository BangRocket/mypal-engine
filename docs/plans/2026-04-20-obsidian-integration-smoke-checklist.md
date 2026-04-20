# Obsidian Integration — End-to-End Smoke Test Checklist (Task F2)

**Date:** 2026-04-20
**Status:** Ready to run (all code shipped)

Use this checklist after starting the identity service + gateway with the new env vars set. Record results next to each step. File any failures under `## Issues found` at the bottom.

---

## Prerequisites

1. **Generate a Fernet key** (do this once, keep it in your `.env`):
   ```bash
   poetry run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Add to identity service environment:
   ```bash
   export SECRETS_ENCRYPTION_KEY="<the key>"
   ```

2. **Identity service URL** (gateway reads this to fetch per-user tokens):
   ```bash
   export IDENTITY_SERVICE_URL="http://localhost:18791"
   export IDENTITY_SERVICE_SECRET="<same value as the identity service's IDENTITY_SERVICE_SECRET>"
   ```

3. **Have the dev token ready** — the one used when you first asked for this feature. Do NOT paste it into any file that ends up in git.

---

## Part 1 — Identity service

| # | Step | Expected | Result |
|---|------|----------|--------|
| 1 | `cd services/identity && SECRETS_ENCRYPTION_KEY=... poetry run python -m identity.main` | Service starts, logs `Uvicorn running on ... 18791`. No errors about encryption key or schema migration. | ☐ |
| 2 | Open `http://localhost:18791/` in a browser, log in with an existing account. | Dashboard loads; sees "Welcome, <name>", linked platforms, API Keys, and **Obsidian Integration** card. | ☐ |
| 3 | In the Obsidian card, verify initial state. | Status shows "Not configured". Host and verify-TLS inputs are empty/default. Clear button hidden. | ☐ |
| 4 | Paste your dev token. Leave host blank (default `obsidian.shmp.app`). TLS verify ON. Click Save. | Token field clears. Status shows `Configured — host: obsidian.shmp.app, TLS verify: on`. Clear button now visible. | ☐ |
| 5 | Refresh the page. | Obsidian card still shows "Configured — host: obsidian.shmp.app, TLS verify: on". Token input starts empty (server never returns it). | ☐ |
| 6 | In DevTools Network tab, call `GET /users/me` and inspect the response body. | Body includes `obsidian_configured: true`, `obsidian_api_host`, `obsidian_verify_tls`. **Does NOT include any key containing "token" or the token string.** | ☐ |
| 7 | From a terminal: `curl -H "X-Service-Secret: $IDENTITY_SERVICE_SECRET" http://localhost:18791/users/<your-canonical-user-id>/obsidian-token` | 200 with `{"api_token": "<your token>", "api_host": "obsidian.shmp.app", "verify_tls": true}`. | ☐ |
| 8 | Same curl but with wrong service secret. | 401 Invalid service secret. | ☐ |
| 9 | Click Clear in the UI, confirm dialog. | Status returns to "Not configured". | ☐ |
| 10 | Re-save the token so Part 2/3 can use it. | Status shows Configured. | ☐ |

---

## Part 2 — Gateway startup

| # | Step | Expected | Result |
|---|------|----------|--------|
| 11 | Start the gateway with the env vars from Prerequisites. `poetry run python -m mypalclara.gateway start` | Gateway starts, logs MCP + core tools registered. In the logs for `[core_tools]`, look for `Registered 16 obsidian tools`. | ☐ |
| 12 | Check logs for any warning about `Failed to register obsidian_tool`. | **No such warning.** | ☐ |
| 13 | `curl http://127.0.0.1:18790/health` (or whatever your gateway HTTP health route is). | 200 OK. | ☐ |

---

## Part 3 — Clara on Discord

(All steps below assume the Discord bot is running and the configured user is the one who saved the token in Part 1.)

| # | Prompt to Clara | Expected | Result |
|---|-----------------|----------|--------|
| 14 | `"what folders are in my Obsidian vault?"` | Clara calls `obsidian_list_vault` and describes the top-level folders. No "not configured" error. | ☐ |
| 15 | `"search my vault for 'clara'"` | Clara calls `obsidian_search` with `query="clara"`, reports hits. | ☐ |
| 16 | `"list my obsidian tags"` | Clara calls `obsidian_list_tags` and names the top few tags. | ☐ |
| 17 | `"what's in today's daily note?"` | Clara calls `obsidian_get_periodic_note` with `period="daily"`. Returns content OR "Note not found" if you haven't started today's note. Either is fine. | ☐ |
| 18 | `"append this to today's daily note: 'Clara integration smoke test marker'"` | Clara calls `obsidian_append_to_periodic_note`. Replies confirming. Verify in Obsidian that the marker appears. | ☐ |
| 19 | `"what's in my daily note now?"` | Clara reads the note back; the marker appears. The snapshot was invalidated after the append, so the read is a fresh fetch. | ☐ |
| 20 | Ask Clara to describe her awareness of your vault (e.g., `"what do you know about my obsidian setup?"`). | Clara's response should reference the vault snapshot block — folder names, tag counts, recent-edit paths. This confirms the snapshot injection is working. | ☐ |

---

## Part 4 — Verify in debug logs / transcript

| # | Step | Expected | Result |
|---|------|----------|--------|
| 21 | In gateway logs for one of the requests above, find where the system prompt is logged (or dump via DEBUG). | System prompt contains a `## User Context` section with "User's Obsidian vault (obsidian.shmp.app): ..." | ☐ |
| 22 | Same prompt: look for the `## Tool-specific guidance — obsidian` block from the registered SYSTEM_PROMPT. | Present, with the "Prefer search before get_file" guidance etc. | ☐ |
| 23 | Check that the prompt does NOT contain the API token or encrypted bytes anywhere. | Token string and `encrypted_obsidian_token` do not appear. | ☐ |

---

## Part 5 — Negative path

| # | Step | Expected | Result |
|---|------|----------|--------|
| 24 | In the identity SPA, click Clear on the Obsidian card. Confirm. | Status shows "Not configured". | ☐ |
| 25 | Wait 60s+ (to clear the gateway's per-user ObsidianClient cache). Alternatively, restart the gateway. | — | ☐ |
| 26 | Ask Clara: `"list my obsidian vault"` | Clara should NOT have `obsidian_*` tools in her inventory (per-user filter hides them when unconfigured). She responds without calling those tools — likely telling you she doesn't have Obsidian access, or just responding without tool calls. | ☐ |
| 27 | Re-save the token via the UI. Wait 60s (or restart gateway). | — | ☐ |
| 28 | Ask the same question again. | Clara calls `obsidian_list_vault` again. Confirms the dynamic per-user filtering works in both directions. | ☐ |

---

## Rollback (if something is broken and you need to reset fast)

1. In the identity SPA, click Clear for your own Obsidian config.
2. `docker-compose stop gateway` / kill the gateway process.
3. On the identity DB, if the migration columns are the problem: they're nullable (safe). Worst-case manual fix:
   ```sql
   UPDATE canonical_users
     SET encrypted_obsidian_token = NULL,
         obsidian_api_host = NULL,
         obsidian_verify_tls = TRUE;
   ```
4. Revert the main-branch commits starting at the first `feat(identity): add Fernet` commit if the whole feature needs to come out. No destructive rebase required — just `git revert <sha>..<sha>`.

---

## Issues found

*(record any step that didn't match expectations here; each entry should reference the step number and describe observed vs. expected)*

- ⬜ None yet.

---

## Follow-ups (non-blocking, flagged during implementation)

- The CalVer git hook sometimes auto-bumps on `refactor:` / `test:` commits. The repo convention in `CLAUDE.md` says it shouldn't. Separate cleanup task.
- The `require_service_secret` dependency is permissive when `IDENTITY_SERVICE_SECRET` is unset. Make sure the env var is set in prod before anything calls the service-auth Obsidian endpoint.
- `build_prompt_layered` has full system_prompts + vault-block support (D6). The non-layered fallback `build_prompt` also has it. Both code paths tested.
- The snapshot cache is invalidated on write-tool success but NOT on the identity-service DELETE (Josh clears his token). Until a webhook is added, a gateway restart is the clean way to force-refresh after a Clear. Documented in the design doc under "Open questions (deferred)".
