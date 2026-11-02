# SupportAI local app (Gemini)

SupportAI is a multi-domain support desk demo. The global dashboard shows requests from every domain; selecting a domain filters the queue and shows that domain's categories and demo policies. The queue can also be searched by customer, ticket ID, or issue, and filtered by urgency and resolution status.

Included demo workspaces:

- Food delivery: delivery delays, missing items, food quality, and order issues
- Edtech: course access, live classes, learning content, and subscription billing
- Ecommerce: shipping, damaged products, returns, billing, and accounts
- Healthcare: appointments, prescription routing, records, and patient portal access
- Travel: booking changes, missed connections, and baggage issues
- Telecom: connectivity, plans, devices, and billing

Each ticket stores its domain. Knowledge retrieval and triage use that domain, and the policy reviewer applies domain-specific checks. Existing tickets without a domain are treated as ecommerce tickets. The policies and articles are illustrative demo content; replace them with approved, organization-specific guidance before using this for real customers. Healthcare tickets use local template drafting and are not sent to Gemini.

## Identity and access

- Local mode (`SUPPORTAI_AUTH_MODE=dev`) is restricted to localhost. For a public portfolio deployment, use `SUPPORTAI_AUTH_MODE=password` behind HTTPS: customers can register with email/password, while admins cannot self-register and must be provisioned with `python -m supportai.manage_auth`. Passwords are stored as salted PBKDF2 hashes in SQLite locally or Postgres when `DATABASE_URL` is set.
- For organization SSO, set `SUPPORTAI_AUTH_MODE=oidc` and configure the OIDC issuer, client ID/secret, exact callback URI, and either a verified admin email allowlist or admin group claim. The callback uses authorization code + PKCE, state/nonce, signature and issuer/audience/expiry validation.
- OIDC users not mapped to an admin group or verified admin email get the customer role. The browser cannot select its own role in OIDC mode.
- Customers can create and view only their own tickets. They cannot read the global queue, feedback, internal policies, triage notes, or draft resolutions. Admin-only actions are enforced by the API as well as hidden in the UI.
- Sessions are server-side, opaque, HttpOnly, SameSite cookies with expiry and CSRF tokens. Use HTTPS, set `SUPPORTAI_COOKIE_SECURE=true`, and configure the OIDC redirect URI to the HTTPS callback when deployed behind a TLS reverse proxy.
- Copy `.env.example` to `.env` for local configuration. `SUPPORTAI_DATA_DIR` controls where ticket, account/session, and vector data are stored. The local default data files are ignored by Git.

Customer signup requires a valid-looking email, name, password confirmation, and password of at least 12 characters. The portfolio deployment does not verify email addresses or provide password recovery. Public signup and expensive actions have per-IP/account rate limits. Passwords are never accepted with a caller-selected role; the account database determines the role. This is suitable for a public demo, not for sensitive or regulated customer data.

Authlib's JWT/JWK validation is used by the OIDC implementation. Provider configuration still must be supplied by the organization. See [Authlib's web OAuth client guide](https://docs.authlib.org/en/latest/oauth2/client/web/) and [JWT validation guide](https://docs.authlib.org/en/stable/jose/jwt.html).

## AI security

- Ticket messages and retrieved article text are treated as untrusted data, not model instructions.
- High-confidence prompt-injection patterns in either input skip Gemini and use the local template; the warning is shown for human review.
- Drafts that appear to expose internal prompts or credential-like values are replaced by the local template.
- Healthcare drafts stay local and are not sent to Gemini. Human approval remains required. These checks are starter guardrails, not a complete prompt-injection defense.

## Install dependencies

From the project folder, create a virtual environment and install dependencies into it. This keeps SupportAI's packages separate from tools installed in your global Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --timeout 120 --retries 10 -r requirements.txt
```

The resolution flow uses LangGraph to run knowledge retrieval, Gemini drafting, and policy review as separate steps. If LangGraph is not installed yet, the same nodes run in sequence so the local app can still start.

The workflow is split into ticket triage, knowledge retrieval, resolution drafting, policy review, and quality evaluation specialist agents. Gemini is called by the resolution agent when enabled; the other stages use local rules and support data. Chroma is the default persistent vector store in `supportai/chroma_db`; the first vector search may download Chroma's default local embedding model. If Chroma cannot start, the app falls back to keyword search and reports the active backend in `GET /health`.

Drafts also show a four-part heuristic quality checklist for knowledge support, actionable next steps, reply completeness, and sensitive/promise policy flags. It is an agent aid, not a correctness guarantee.

When an agent reviews a draft, SupportAI stores its checklist score with the outcome. The dashboard compares average scores for unchanged approvals versus edited or rejected drafts. This helps evaluate whether the heuristic tracks human corrections; older reviews without saved scores are excluded.

## MCP knowledge tools

SupportAI includes a read-only MCP server for connecting external agent hosts to domain lists, policy summaries, and support article search. It never exposes customer tickets or write actions.

```powershell
.\.venv\Scripts\python.exe -m supportai.mcp_server
```

Configure your MCP host to launch that command with the project folder as its working directory. Keep the MCP server local to trusted agent hosts; its policy tool exposes internal support rules.

## Forward-deployed engineer integration

The Zendesk connector is an explicit admin action in the draft review panel. After approval, it creates a Zendesk ticket containing the issue and approved reply as a **private internal note**. It does not send a customer-facing message. Configure `SUPPORTAI_ZENDESK_SUBDOMAIN`, `SUPPORTAI_ZENDESK_EMAIL`, `SUPPORTAI_ZENDESK_API_TOKEN`, and `SUPPORTAI_ZENDESK_REQUESTER_ID` in `.env`; the Zendesk account must allow private initial comments and the configured requester ID must be valid. The app records the Zendesk ticket ID to avoid normal repeat submissions. If the network fails after Zendesk creates a ticket but before SupportAI stores the ID, check Zendesk before retrying.

For a customer implementation, an FDE would map each customer's domains, article sources, policy rules, IdP group claims, ticket fields, and escalation owners; validate the ticket lifecycle in a sandbox; and hand over an operational runbook. This demo includes configurable domain policy/knowledge, OIDC role mapping, a local vector index, read-only MCP knowledge tools, and the Zendesk outbound handoff. The free Render setup uses Postgres for tickets/accounts and keyword retrieval to stay within the free web instance's resource limits. Free hosting is for a portfolio demo: the web service may sleep when idle and free provider quotas can temporarily suspend service.
## Start the app

From the project folder in VS Code, run:

```powershell
.\.venv\Scripts\python.exe -m supportai.api
```

Then open `http://127.0.0.1:8000` in your browser. The ticket desk lets you create tickets, review triage, draft a resolution, inspect source articles and policy findings, and approve, edit, or reject a draft. Staff can change ticket status with a customer-visible update or an internal note. Customers can reply to their own tickets, and staff can reply publicly or add internal notes. Approval saves a resolution draft for an agent; it does not send a message.

Before using local password mode as an admin, create your account once from another terminal:

```powershell
.\.venv\Scripts\python.exe -m supportai.manage_auth
```

Ticket data is saved in `supportai/tickets.json` locally. When `DATABASE_URL` is set, both ticket records and authentication data use Postgres instead.

## Enable Gemini reply drafting

SupportAI uses its local template unless Gemini is explicitly enabled. Install dependencies, then add your replacement key to the project-root `.env` file:

```powershell
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple --timeout 120 --retries 10 -r requirements.txt
.\.venv\Scripts\python.exe -m supportai.api
```

The `.env` file is loaded automatically and excluded from Git. Set `GEMINI_API_KEY` to your new key and `SUPPORTAI_USE_GEMINI=true` in that file. Restart the server after changing `.env`.

The default Gemini model is `gemini-3.5-flash-lite`. To choose another model available to your API key, set `$env:SUPPORTAI_GEMINI_MODEL` before starting the server.

When Gemini is enabled, the customer's name and message, reviewed category and urgency, and matching support article text are sent to Google to draft a reply. If the key, SDK, or model request is unavailable, SupportAI falls back to its local template and displays a warning. Drafts still go through policy checks and human review before approval.

Never put an API key in source code, commit it to Git, or paste it into chat. If a key is shared, revoke it and create a replacement.

## HTTP endpoints

- `GET /health` - check that the API is running
- `POST /auth/signup` - create a customer account (local password mode only)
- `POST /auth/login` - authenticate a customer or provisioned admin account
- `GET /domains` - list domain names, categories, and demo policies
- `GET /tickets` or `GET /tickets?domain=all` - list all tickets across domains
- `GET /tickets?domain=food_delivery` - list tickets for one domain
- `GET /feedback` - summarize global triage agreement and resolution draft outcomes
- `GET /feedback?domain=food_delivery` - summarize quality metrics for one domain
- `POST /tickets` - create and triage a ticket

Ticket creation accepts `customer_name`, `message`, and an optional `domain` (`food_delivery`, `edtech`, `ecommerce`, `healthcare`, `travel`, or `telecom`). If omitted, the domain defaults to `ecommerce` for compatibility with earlier CLI usage.
- `GET /tickets/{id-prefix}` - fetch one ticket
- `GET /tickets/{id-prefix}/resolution` - generate a draft plan and policy review
- `POST /tickets/{id-prefix}/triage-review` - save a human category and urgency review
- `POST /tickets/{id-prefix}/status` - transition between `open`, `in_progress`, `waiting_on_customer`, `escalated`, `resolved`, and `closed`
- `POST /tickets/{id-prefix}/messages` - add a customer reply, staff reply, or staff-only note
- `POST /tickets/{id-prefix}/resolution-review` - save an approval, edit, or rejection
- `POST /tickets/{id-prefix}/zendesk` - admin-only, approved-draft Zendesk handoff as a private note

The local demo server binds to localhost. Do not expose `dev` authentication publicly. For the password-auth portfolio deployment, use HTTPS, secure cookies, one app instance, and persistent storage. For a full production service, add email verification/password recovery and migrate to managed databases before scaling.

## Free public demo deployment

The Blueprint in `render.yaml` selects Render's **Free** web-service plan and requires a Postgres URL so the app refuses to save public customer data to Render's temporary filesystem. Create a Neon Free Postgres project; its current plan advertises $0, no credit card, 1 GB per project, and a monthly compute allowance. Copy its pooled Postgres connection string into the Render `DATABASE_URL` secret. Render Free services can sleep after inactivity and use an ephemeral filesystem; customer accounts/tickets remain in Neon. See [Render's free service limitations](https://render.com/docs/free) and [Neon's Free plan](https://neon.com/pricing).

1. Keep `.env`, local databases, ticket files, and API keys out of GitHub. The project `.gitignore` excludes these files.
2. Create the Neon project on its Free plan. Copy the connection string from the Neon dashboard; keep it private.
3. In Render, create a Blueprint from this GitHub repository. Select the Free plan and add `DATABASE_URL` and `GEMINI_API_KEY` as secret values when prompted (or later under the service's Environment page). Do not put either secret in `render.yaml` or GitHub.
4. The blueprint enables `SUPPORTAI_USE_GEMINI=true`; Gemini still requires a valid key and usable quota. Model API usage can have separate charges if billing is enabled on the Google account. Healthcare tickets and messages detected with common credential/payment patterns use the local template. Other customer messages and relevant article text are sent to Google for drafting.
5. Create the first admin account from a local terminal connected to the same Neon database: set `DATABASE_URL` in that terminal, run `python -m supportai.manage_auth`, then clear the environment variable. Do not paste the database URL into chat or commit it.
6. Open the Render HTTPS URL. Verify `/health`, customer signup/login, ticket creation, status updates, customer replies, and persistence after the Render service sleeps and wakes.

Both providers enforce free-plan quotas. Render may sleep the app while idle; Neon scales its compute to zero and resumes it for database requests. When a free quota is exhausted, service may pause until its usage resets. This is suitable for a public portfolio demo, not a production SLA or sensitive regulated data. MCP is a separate stdio process and is not started by the web service.

The app uses JSON tickets and SQLite accounts/sessions on the mounted disk. Keep one instance and arrange backups. Before scaling beyond this portfolio demo, migrate ticket/account data to managed databases and move rate limits to a shared store. MCP is a separate stdio process and is not started by the web service.
