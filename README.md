# Capacity Radar

**Priority-based GPU/TPU capacity scanning and deployment tool for Google Cloud Platform.**

Capacity Radar automates finding and securing GPU/TPU capacity across Google Cloud zones. It supports multiple consumption models — On-Demand Reservations, DWS Calendar, DWS Flex Start, and Spot VMs — and lets you define a priority-based strategy that tries each method in order until capacity is secured.

![Google Cloud](https://img.shields.io/badge/Google%20Cloud-GPU%20%2F%20TPU-4285F4?logo=google-cloud&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?logo=fastapi&logoColor=white)

---

## Features

### In-App AI Capacity Assistant (Gemini 3 Flash & Gemini 3.1 Pro)
- **Always-accessible collapsible chat panel** — Click **Ask Radar AI** in the top bar (or expand the assistant bar directly above the tabs) at any point without losing sight of the active tab below.
- **Screen-context aware Q&A** — Automatically receives a live snapshot of whichever tab you are viewing (`Capacity Advisory`, `Scan & Deploy`, or `Quota`) so you can ask questions directly about the results on screen (e.g., *"Which of the Spot zones below has the lowest preemption risk?"* or *"Which quotas on my project have a limit > 0?"*).
- **Agentic tool execution & UI driving** — Powered by Vertex AI **Gemini 3 Flash** (`gemini-3-flash-preview`) and **Gemini 3.1 Pro** (`gemini-3.1-pro-preview`) using the bundled [`skills/capacity-radar/SKILL.md`](skills/capacity-radar/SKILL.md) workflow:
  - Runs **all supported advisories in parallel** (`advise_all`: DWS Calendar + Spot VM + DWS Flex Start) and populates all three result cards on the **Capacity Advisory** tab automatically.
  - Queries live project quotas (`check_project_quotas`) and filters the **Quota** tab (`drive_ui_filter_quotas`).
  - Pre-configures single- or multi-priority hunting plans on the **Scan & Deploy** tab (`drive_ui_configure_scan`) while requiring explicit user confirmation before launching billable deployments.

### GPU & TPU Quota Explorer Tab
- **Complete 51-item GPU, TPU & VM Quota Catalog** — Covers all NVIDIA GPUs (`GB300`, `GB200`, `B200`, `H200`, `H100 Mega/High/Edge`, `RTX PRO 6000`, `A100 80GB/40GB`, `L4`, `T4`, `GPUS_ALL_REGIONS`), all Cloud TPUs (`v7x Ironwood`, `v6e Trillium`, `v5p`, `v5e Serving & Batch`, `v4`, `v3`, `v2`), and related VM/host quotas (`CPUS`, `PREEMPTIBLE_CPUS`, `A2_CPUS`, `CPUS_ALL_REGIONS`, `tpu.googleapis.com/queuedResources`, `tpus_per_tpu_family`).
- **Live regional & zonal limits and usage** — Queries Service Usage API (`consumerQuotaMetrics`) and Compute Engine API (`regions`/`projects`) in parallel to display real-time project limits and usage per region/zone.
- **Consumption model & family filtering** — Filter by `GPU` / `TPU`, specific accelerator chips, VM families, consumption models (`On-Demand / Reserved`, `Spot VM`, `DWS Flex-Start`, `DWS Calendar`, `Committed CUD`), non-zero limits, or toggle **Show related VM & host quotas**.
- **1-click GCP Console Quota deep links** — Click any quota row or location chip to open the Google Cloud Console Quotas page pre-filtered to that exact metric and project.

### Scan & Deploy
- **Priority-based scanning** — Define up to 4 priorities (e.g., try On-Demand first, then DWS Calendar, then Spot)
- **1-click deploy from any Advisory card** — Click **Deploy** on any recommended zone (or **Scan All Zones**) in **DWS Calendar Advisory**, **Spot VM Advisory**, or **DWS Flex Start Advisory** to jump straight into **Scan & Deploy** with all settings pre-populated
- **Multi-zone scanning & fail-fast quota pruning** — Automatically tries all supported zones and prunes regions/zones that fail with `QUOTA_EXCEEDED` so scans fail fast onto the next priority
- **Real-time progress** — WebSocket-based live updates with detailed step-by-step logs
- **Sequential or Parallel** execution modes
- **VPC network & subnetwork selector** — Pick an existing VPC/subnet or create a new VPC directly from the UI
- **Custom naming** — Set resource name prefixes for reservations and VMs
- **Cancel anytime** — Cancel a running scan and the tool stops gracefully

### Consumption Models

| Model | Description | GPU / TPU Support |
|-------|-------------|-------------------|
| **On-Demand Reservation** | Reserve capacity at on-demand rates. Immediate if available. | A3 Edge/Mega/High, A2, G2, G4, A4X; TPU v7x, v6e, v5p, v5e, v4, v3, v2 |
| **DWS Calendar Mode** | Book capacity for a specific time window as a future reservation (`CALENDAR` mode, `DENSE` deployment). Polls until `PROCURING` / `APPROVED` / `FULFILLED`. | GPU A4, A3 Ultra/Mega/High/Edge; TPU v6e, v5p, v5e |
| **DWS Flex Start** | Queue for capacity; retries until GPUs become available. For TPUs, uses Queued Resources (`runDuration` + dedicated DWS Flex pool zone ordering). | GPU A4, A3, A4X, A4X Max, G4, G2, N1; TPU v7x, v6e, v5p, v5e, v4 |
| **Spot VMs** | Spare capacity at discount. Can be preempted at any time. | All GPU & TPU families |

### Capacity Advisory

- **DWS Calendar Advisory** — Query the Calendar Mode Advisory API (`advice/calendarMode`, supporting both GPU `specificSkuResources` and TPU `aggregateResources` for `v6e`, `v5p`, `v5e`) to find optimal zones and time windows:
  - **Check Availability** — Search up to **+365 days (1 year)** ahead (or any custom AI horizon) for **1 to N day** reservation slots
  - **Find Best Plan** — Queries at multiple VM count levels (100%, 75%, 50%, 25%, 1 VM) to show how to split capacity across multiple smaller reservations
- **Spot VM Advisory** — Check spot capacity availability and preemption risk across zones, with 1-click **Deploy** buttons
- **DWS Flex Start Advisory** _(Preview — requires a whitelisted project)_ — Estimates how long a DWS Flex Start request would wait in the queue before capacity is granted, with 1-click **Deploy** buttons

### Supported Hardware

**GPU Families:**

| Family | GPU | Machine Types | DWS Calendar | Spot | On-Demand |
|--------|-----|---------------|:------------:|:----:|:---------:|
| A4X Max | NVIDIA GB300 | `a4x-maxgpu-4g-metal` | - | Yes | - |
| A4X | NVIDIA GB200 | `a4x-highgpu-4g` | - | Yes | Yes |
| A4 | NVIDIA B200 | `a4-highgpu-8g` | Yes (DENSE) | Yes | - |
| A3 Ultra | NVIDIA H200 | `a3-ultragpu-8g` | Yes (DENSE) | Yes | - |
| A3 Edge | NVIDIA H100 | `a3-edgegpu-8g` | Yes (DENSE) | Yes | Yes |
| A3 Mega | NVIDIA H100 | `a3-megagpu-8g` | Yes (DENSE) | Yes | Yes |
| A3 High | NVIDIA H100 | `a3-highgpu-{1,2,4,8}g` | Yes (DENSE) | Yes | Yes |
| A2 Ultra | NVIDIA A100 80GB | `a2-ultragpu-{1,2,4,8}g` | - | Yes | Yes |
| A2 Standard | NVIDIA A100 40GB | `a2-highgpu-{1,2,4,8}g` | - | Yes | Yes |
| G4 | NVIDIA RTX PRO 6000 | `g4-standard-{6..384}` | - | Yes | Yes |
| G2 | NVIDIA L4 | `g2-standard-{4..96}` | - | Yes | Yes |

**TPU Types:**

| Type | Zones | DWS Calendar | DWS Flex | Spot | On-Demand |
|------|-------|:------------:|:--------:|:----:|:---------:|
| v7x (Ironwood) | 5 zones | - | Yes | Yes | Yes |
| v6e (Trillium) | 9 zones | Yes (`aggregateResources`) | Yes | Yes | Yes |
| v5p | 3 zones | Yes (`aggregateResources`) | Yes | Yes | Yes |
| v5e | 5 zones | Yes (`aggregateResources`) | Yes | Yes | Yes |
| v4 | 1 zone | - | Yes | Yes | Yes |
| v3 | 3 zones | - | - | Yes | Yes |
| v2 | 5 zones | - | - | Yes | Yes |

---

## Two ways to use it

Capacity Radar can be used **either** as a web app **or** as an **AI Agent Skill**
(drive everything from chat in an agent like Claude, Google Antigravity, etc.).

- **Web app** — run the FastAPI backend + React UI (see Quick Start below).
- **Agent Skill** — install the bundled skill so an AI agent can authenticate,
  run advisories, and deploy capacity through conversation. See
  **[Use as an Agent Skill](#use-as-an-agent-skill)**.

---

## Quick Start

### Prerequisites

- **Python 3.10+**
- **Node.js 18+** and npm
- **Google Cloud SDK** (`gcloud`) installed and configured
- A GCP project with **Compute Engine API** enabled

### 1. Clone the Repository

```bash
git clone https://github.com/MG-Cafe/capacity_radar.git
cd capacity_radar
```

### 2. Authenticate with Google Cloud

```bash
# Login with your Google account (sets up Application Default Credentials)
gcloud auth application-default login

# Set your project (optional — you can also enter it in the UI)
gcloud config set project YOUR_PROJECT_ID
```

### 3. Install Backend Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Install Frontend Dependencies & Build

```bash
cd ../frontend
npm install
npm run build
```

### 5. Run the Application

```bash
cd ../backend
python main.py
```

The app will be available at **http://localhost:8000**

### Development Mode (Hot Reload)

For frontend development with hot reload:

```bash
# Terminal 1: Backend
cd backend && python main.py

# Terminal 2: Frontend dev server (proxied to backend)
cd frontend && npm run dev
```

Frontend dev server runs on http://localhost:3000 with API proxy to :8000.

---

## Architecture

```
capacity_radar/
├── backend/
│   ├── main.py              # FastAPI server — REST + WebSocket endpoints
│   ├── agent.py             # Gemini 3 Flash / 3.1 Pro AI Assistant & UI-driving tool loop
│   ├── quotas.py            # 51-item GPU/TPU/VM quota catalog + live Service Usage / GCE fetcher
│   ├── hunter.py            # Core scanning engine (GPU & TPU deployment + quota fail-fast)
│   ├── advisory.py          # DWS Calendar (up to 365d), Spot & Flex advisory API queries
│   ├── gpu_data.py          # Machine type catalog, zone mappings, consumption support
│   └── requirements.txt     # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Main app — auth drawer, AI assistant bar, tab navigation
│   │   └── components/
│   │       ├── ChatAssistant.jsx       # Collapsible top AI Capacity Assistant panel
│   │       ├── AdvisoryPanel.jsx       # Calendar, Spot & DWS Flex advisory queries + 1-click deploy
│   │       ├── ScanningPanel.jsx       # Scan & Deploy configuration + live WebSocket logs
│   │       ├── QuotaPanel.jsx          # GPU & TPU Quota Explorer with Cloud Console deep links
│   │       └── MachineTypeSelector.jsx # Category → Chip → Machine type picker
│   ├── vite.config.js       # Vite config with API proxy
│   └── package.json
├── skills/
│   └── capacity-radar/      # Bundled AI Agent Skill (SKILL.md + CLI script)
├── CHANGELOG.md             # Release & feature history
└── README.md
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, WebSocket, httpx |
| Frontend | React 18, Material UI v5, Vite |
| AI Agent | Vertex AI Gemini (`gemini-3-flash-preview`, `gemini-3.1-pro-preview`), Function Calling |
| Auth | Google Application Default Credentials (ADC) / OAuth 2.0 User Token (`X-User-Token`) |
| APIs | Compute Engine REST API (`v1`/`beta`/`alpha`), Cloud TPU API (`v2`), Service Usage API (`v1beta1`), Vertex AI API (`v1beta1`) |

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/check` | Verify GCP auth and project access |
| `POST` | `/api/auth/login` | Trigger `gcloud auth application-default login` |
| `GET` | `/api/machine-types` | List all GPU/TPU machine types with zone & support info |
| `GET` | `/api/chip-groups` | Machine types grouped by chip for the selector |
| `GET` | `/api/machine-types/{type}/zones` | Zones for a specific machine type |
| `POST` | `/api/advisory/calendar` | Query DWS Calendar Advisory API (up to 365 days ahead) |
| `POST` | `/api/advisory/calendar/splits` | Find best capacity split plan across VM count levels |
| `POST` | `/api/advisory/spot` | Query Spot VM Advisory API |
| `POST` | `/api/advisory/flex` | Query DWS Flex Start Capacity Advisory API (Preview — whitelisted projects) |
| `GET` | `/api/quotas` | Fetch 51-item GPU/TPU/VM quota catalog with live project limits, usage & Console URLs |
| `GET` | `/api/networks` | List VPC networks and regional subnetworks in the project |
| `POST` | `/api/networks` | Create a new auto-mode VPC network in the project |
| `GET` | `/api/chat/models` | List available Gemini 3+ models for the in-app AI Assistant |
| `POST` | `/api/chat` | Run the AI Capacity Assistant with live screen context and UI-driving tools |
| `WS` | `/ws/scan` | WebSocket for real-time scan & deploy |

---

## Configuration

### Required GCP IAM Permissions

The authenticated user or service account needs:

| Role | Purpose |
|------|---------|
| `roles/compute.admin` | Create reservations, instances, future reservations |
| `roles/tpu.admin` | Create TPU nodes and queued resources (if using TPUs) |

### CORS Configuration

By default, the backend allows requests from `localhost:3000` and `localhost:5173` (Vite dev servers). To customize:

```bash
export CORS_ORIGINS="http://localhost:3000,https://your-domain.com"
```

### Changing the Port

Edit `backend/main.py`:

```python
uvicorn.run(app, host="0.0.0.0", port=8000)  # Change 8000
```

---

## Usage Guide

### Setting Up a Scan

1. **Authenticate** — Click "Authenticate with Google" to open browser sign-in
2. **Connect** — Enter your GCP Project ID and click "Connect"
3. **Select machine type** — Pick Category (GPU/TPU) → Chip → Machine Type
4. **Set VM count** — Min and Max VMs (system tries max first, scales down to min)
5. **Add priorities** — Each priority specifies:
   - Consumption method (On-Demand, DWS Calendar, DWS Flex, Spot)
   - Target zones (leave empty = try all available)
   - Retry rounds and interval
   - Optional resource name prefix
6. **Method-specific settings**:
   - **DWS Calendar**: Start/end times for the reservation window
   - **DWS Flex**: Max wait time and usage duration
7. Choose **Sequential** or **Parallel** execution
8. Click **Start Scan & Deploy**

### Using the Advisory Tab

#### Check Availability
1. Select a machine type and enter VM count
2. Pick a start date and flexibility window (0-3 days)
3. Set duration in days
4. Click **Check Availability** to see zone recommendations

#### Find Best Plan
1. Same inputs as above
2. Click **Find Best Plan** — queries at 100%, 75%, 50%, 25%, and 1 VM counts
3. View results grouped by VM count level to plan multiple reservations
4. Example: "No 20-GPU slot, but 15 GPUs available days 1-3, then 5 more days 4-7"

### Understanding Scan Results

| Status | Meaning |
|--------|---------|
| **Success** | Capacity secured. Logs show method, zone, and resource details. |
| **Failed** | All priorities exhausted. Try different zones, types, or methods. |
| **Cancelled** | You stopped the scan. Check GCP Console for partial resources. |

### DWS Calendar Behavior
Calendar future reservations use `reservationMode: CALENDAR` with DENSE deployment for multi-node GPU families (A3, A4). After submission, the tool polls until Google approves or rejects. Approval is typically within a minute for eligible projects.

### DWS Flex Behavior
- **GPUs**: Uses Compute Engine reservations with retry logic (no real queue — each attempt succeeds or fails immediately)
- **TPUs**: Uses the TPU Queued Resource API with actual server-side queuing

---

## Use as an Agent Skill

Capacity Radar ships with a self-contained **AI Agent Skill** so any agent (e.g.
Claude, Google Antigravity, or other agentic IDEs) can drive **all** of the app's
functionality through chat — authenticate, run any advisory (Calendar / Spot /
DWS Flex, any chip/region/size), and configure + launch a priority-based
deployment — asking the user follow-up questions for any missing input, exactly
like the web UI.

The skill lives in **[`skills/capacity-radar/`](skills/capacity-radar/)** and is
built as a `SKILL.md` plus a portable CLI (`scripts/capacity_radar.py`) that
reuses the app's own backend, so results are identical to the UI.

### Install (one line)

Point your agent at this repo/skill folder, for example:

```
Install the skill at https://github.com/MG-Cafe/capacity_radar (skills/capacity-radar)
```

…or copy `skills/capacity-radar/` into your agent's skills directory.

### Prerequisites

```bash
pip install -r backend/requirements.txt      # backend deps the skill reuses
gcloud auth application-default login         # one-time auth (or use the skill's `auth login`)
```

### What the agent can do

- **Authenticate** — check ADC + project access; help the user log in if needed.
- **Discover** — list GPU/TPU chips, machine types, and supported zones.
- **Advise (safe / read-only)** — Spot, DWS Calendar (+ best-split plan), and
  DWS Flex Start (Preview / whitelisted) advisories for any chip/region/size.
- **Deploy** — priority-based scan & deploy (sequential or parallel). This
  creates **real, billable** resources, so the skill requires explicit
  confirmation (`--yes`) before it will proceed.

### Run the CLI directly (no agent required)

```bash
# Auth + discovery
python skills/capacity-radar/scripts/capacity_radar.py auth check --project my-project
python skills/capacity-radar/scripts/capacity_radar.py catalog chips

# Advisories
python skills/capacity-radar/scripts/capacity_radar.py advise spot --project my-project --machine-type a3-highgpu-8g --regions us-central1
python skills/capacity-radar/scripts/capacity_radar.py advise flex --project my-project --machine-type a3-highgpu-8g --size 4 --max-run-hours 24 --regions us-central1

# Deploy (creates real resources — requires --yes)
python skills/capacity-radar/scripts/capacity_radar.py deploy \
  --project my-project --machine-type a3-highgpu-8g --vm-count 2 \
  --priority spot:us-central1-b,us-east4-a --priority dws_flex:us-central1-b --yes
```

See **[`skills/capacity-radar/SKILL.md`](skills/capacity-radar/SKILL.md)** for the
full agent workflow, command reference, and safety rules.

---

## Deployment

### Docker

The repo ships a multi-stage `Dockerfile` (Node 20 builds the frontend, Python 3.12 serves it) listening on port 8080:

```bash
docker build -t capacity-radar .
docker run -p 8080:8080 \
  -e DEMO_MODE=false \
  -v ~/.config/gcloud:/root/.config/gcloud \
  capacity-radar
```

> Mount your `gcloud` config directory to pass ADC credentials to the container.

### Cloud Run — public hosted app (bring-your-own-credentials)

This reproduces the hosted web app exactly. The service runs as a **zero-permission** service account and never uses server-side credentials (`AUTH_MODE=user`): visitors paste their own access token (`gcloud auth print-access-token`, Cloud Shell works too) into the Authentication panel, so every scan and deploy runs under the visitor's own identity, project, and quota. That's what makes the URL safe to share publicly.

```bash
# One-time: create a service account and grant it NO roles
gcloud iam service-accounts create capacity-radar-web

gcloud run deploy capacity-radar \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --service-account capacity-radar-web@YOUR_PROJECT.iam.gserviceaccount.com \
  --set-env-vars DEMO_MODE=false,AUTH_MODE=user \
  --memory 512Mi --cpu 1 --max-instances 2
```

### Cloud Run — private deployment (server credentials)

If the app is only for you or your team, you can instead give the service account real permissions (`roles/compute.admin`, plus `roles/tpu.admin` for TPUs) and skip the token-pasting flow:

```bash
gcloud run deploy capacity-radar \
  --source . \
  --region us-central1 \
  --no-allow-unauthenticated \
  --service-account SA_NAME@PROJECT.iam.gserviceaccount.com \
  --set-env-vars DEMO_MODE=false
```

> Never combine server credentials with `--allow-unauthenticated`: anyone on the internet could then create billable resources in your project.

### Environment variables

| Variable | Dockerfile default | Hosted app | Purpose |
|----------|-------------------|------------|---------|
| `DEMO_MODE` | `true` | `false` | When `true`, real deployments are disabled (safe demo). Set `false` for a working app. |
| `AUTH_MODE` | `local` | `user` | `user` = backend never uses its own credentials; every request must carry a visitor-supplied token. |
| `DEFAULT_PROJECT` | empty | empty | Optional project ID to pre-fill in the UI. |
| `OAUTH_CLIENT_ID` | unset | unset | Optional: enables a "Sign in with Google" button instead of token pasting (requires your own verified OAuth client). |

---

## Important Notes

- **Cost**: This tool creates real GCP resources (reservations, VMs, TPU nodes). Understand cost implications before deploying.
- **Cleanup**: If a scan is cancelled, check GCP Console for partially created resources that may still incur charges.
- **Quotas**: Ensure your project has sufficient quota for the machine types and zones you're targeting.
- **DWS Calendar**: Future reservations may require Google account team approval for DENSE deployment eligibility. If you get a "not available for this project" error, contact your account representative.
- **A4X Max**: Bare metal instances — cannot use on-demand reservations.
- **Token Refresh**: ADC tokens are automatically refreshed every 50 minutes during long scans.

---

## Disclaimer

This is **not** an official Google product. There is no SLA, warranty, or guarantee of any kind. Use at your own risk.
