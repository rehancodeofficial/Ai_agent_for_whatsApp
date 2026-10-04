# 🤖 WhatsApp AI Customer Support Agent (Odoo CRM & Sales Integration)

Production-grade, asynchronous WhatsApp AI Customer Support Agent for Odoo (CRM, Inventory, Sales). Built with Python 3.11+, FastAPI, Claude (`claude-haiku-4-5-20251001`), PostgreSQL via `psycopg v3`, XML-RPC for Odoo, and Meta WhatsApp Cloud API.

---

## 🌟 Architecture & Features

- **Multi-lingual AI Support**: Responds in the customer's preferred language (Urdu, Roman Urdu, or English).
- **Odoo XML-RPC Integration**: Read-only product & stock lookups behind an interface, create leads (`crm.lead`), and draft quotations (`sale.order` in `draft` state only).
- **Strict Data Isolation**: Server-side injection of verified WhatsApp sender phone number. Customers can **only** query their own orders.
- **Human Handoff Workflow**: Pauses bot for angry customers or complex queries and immediately alerts staff via WhatsApp (`STAFF_ALERT_PHONE`) with a 3-line chat summary.
- **Security & Anti-Prompt Injection**: Verified Meta HMAC SHA256 signatures (`X-Hub-Signature-256`), strict prompt guardrails, parameterized SQL queries, admin token endpoints.
- **Cost & Rate Control**: Max 20 messages/minute per phone number, max 5 tool iterations per turn, max 500 tokens per completion, token usage logging.

---

## 📁 Project Structure

```text
whatsapp-agent/
├── app/
│   ├── main.py          # FastAPI app, webhook routes, rate limiting, admin routes
│   ├── config.py        # Settings loaded from .env (pydantic-settings)
│   ├── db.py            # Postgres pool + parameterized queries (psycopg v3)
│   ├── odoo_client.py   # Odoo XML-RPC interface with TTL cache & async execution
│   ├── tools.py         # Odoo tool functions + Anthropic tool schemas
│   ├── agent.py         # Claude loop with tool use & retries
│   ├── whatsapp.py      # Meta Graph API sender, parser, HMAC signature verification
│   ├── handoff.py       # Human handoff alerts & admin pause/resume
│   └── prompts.py       # Multi-lingual system prompt & knowledge base loader
├── knowledge.md         # Business FAQs, timings, policies
├── schema.sql           # PostgreSQL table schemas and indexes
├── tests/               # Pytest suite
├── scripts/
│   ├── chat_cli.py                 # Interactive terminal chat
│   ├── test_odoo.py                # Odoo connection test script
│   └── seed_test_conversations.py  # 30 multilingual test scenarios
├── .env.example
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## 🚀 Quickstart & Local Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL 15+ (or Docker)
- Odoo v14+ instance with XML-RPC enabled and API Key
- Meta Developer Account (WhatsApp Cloud API)

### 2. Installation
```bash
git clone <repository_url>
cd whatsapp-agent

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

### 4. Database Setup
Start local Postgres via Docker Compose:
```bash
docker-compose up -d postgres
```
Or execute `schema.sql` directly on your PostgreSQL server:
```bash
psql -U postgres -d whatsapp_agent -f schema.sql
```

---

## 🧪 Testing Tools

### Run Pytest Suite
```bash
pytest
```

### Test Odoo Connection
```bash
python scripts/test_odoo.py
```

### Run Terminal Interactive Chat (CLI Simulator)
Test agent behavior directly in your terminal without WhatsApp:
```bash
python scripts/chat_cli.py
```

### Run 30 Sample Multilingual Test Conversations
```bash
python scripts/seed_test_conversations.py
```

---

## 🌐 Local Webhook Setup with ngrok & Meta Dashboard

1. **Start FastAPI Application**:
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

2. **Expose Server via ngrok**:
   ```bash
   ngrok http 8000
   ```
   Copy the generated HTTPS URL (e.g. `https://a1b2c3d4.ngrok-free.app`).

3. **Configure Meta Webhook Dashboard**:
   - Go to **Meta App Dashboard > WhatsApp > Configuration > Webhook**.
   - Click **Edit**.
   - **Callback URL**: `https://a1b2c3d4.ngrok-free.app/webhook`
   - **Verify Token**: Must match `WA_VERIFY_TOKEN` in `.env`.
   - Click **Verify and Save**.
   - Subscribe to the **`messages`** webhook field.

---

## 🚢 Production Deployment Guide (Render / Railway)

### Deployment Steps:
1. **Managed Postgres**: Provision a Postgres instance on Render/Railway and run `schema.sql`.
2. **Set Environment Variables**: Set all mandatory variables in Render/Railway dashboard:
   - `ANTHROPIC_API_KEY`
   - `DATABASE_URL`
   - `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_API_KEY`
   - `WA_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`
   - `STAFF_ALERT_PHONE`, `ADMIN_TOKEN`, `ENV=production`
3. **Build & Deploy Dockerfile**:
   Render/Railway automatically detects `Dockerfile`.

---

## 🛡️ Security & Code Quality Audit

1. **Secret Leak Prevention**: All credentials and tokens are read exclusively from environment variables (`app/config.py`). Zero hardcoded secrets.
2. **HMAC Signature Verification**: `POST /webhook` verifies `X-Hub-Signature-256` using `hmac.compare_digest` with `WA_APP_SECRET`.
3. **Prompt Injection Defense**: High-privilege actions (like fetching customer orders or creating quotations) require server-side verified phone parameters. The system prompt strictly prohibits rules overriding.
4. **SQL Injection Protection**: All PostgreSQL operations in `app/db.py` use parameterized queries (`%s`).
5. **Timeouts & Exception Safety**: `httpx` HTTP requests and `xmlrpc.client` network calls specify explicit timeouts.
6. **Cost Control Safeguards**: Max 20 msgs/min per phone, 5 max tool iterations per request, token usage logged per turn.
