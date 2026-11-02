# SupportAI

Multi-domain customer-support demo with customer accounts, domain-specific queues and policies, ticket status updates, knowledge retrieval, Gemini-assisted response drafts, and human approval.

## Domains

Food delivery, edtech, ecommerce, healthcare, travel, and telecom. Each domain has its own categories, routing teams, support articles, and policy checks. The admin dashboard shows requests across every domain; customers see only tickets they own.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m supportai.api
```

Open `http://127.0.0.1:8000`. See [the app guide](supportai/README.md) for local admin setup, Gemini configuration, and API details.

## Free public demo

The included Render Blueprint uses Render's free web service and Neon Free Postgres. Postgres is required for hosted accounts and ticket persistence because Render Free's local filesystem is temporary. Create a Neon Free project, then set `DATABASE_URL` in the Render service as a secret. Add a valid `GEMINI_API_KEY` as a separate Render secret; `SUPPORTAI_USE_GEMINI` is enabled by the Blueprint. Never commit `.env`, database URLs, or model keys. The web app may sleep while idle, and provider free quotas apply.

After deployment, create the first admin using the Neon connection string in a local PowerShell session and `python -m supportai.manage_auth`; customer accounts can sign up from the live app. Deployment and limits are described in [the app guide](supportai/README.md#free-public-demo-deployment).

SupportAI is a portfolio demo, not a production SLA or a place for regulated medical or payment data. Gemini receives non-healthcare customer message content and selected support context when drafting; use test data and a key with appropriate quota settings.
