---
name: capacity-radar
description: >-
  Find, advise on, and deploy Google Cloud GPU/TPU capacity from chat. Use this
  skill whenever the user wants to check GPU/TPU availability, get capacity
  advice, compare consumption models (On-Demand, DWS Calendar, DWS Flex Start,
  Spot), or actually deploy/reserve/hunt for GPU or TPU capacity on Google Cloud
  (GCP). Triggers include: "find me H100s", "check spot availability", "reserve
  a3-highgpu-8g", "how long to wait for DWS Flex", "hunt for GPUs", "get me TPUs".
license: Not an official Google product. Use at your own risk.
---

# Capacity Radar Skill

Capacity Radar helps users **find and secure GPU/TPU capacity on Google Cloud**.
This skill lets you (the agent) drive every capability of the Capacity Radar app
through a single CLI, entirely via chat: authenticate, explore what's available,
run capacity advisories, and deploy using a priority-based strategy.

## What this skill can do (tell the user!)

Many users won't know the full capability. Proactively offer these:

1. **Authentication** — verify the user is logged into Google Cloud (ADC) and has
   access to their project; if not, help them log in (opens a browser).
2. **Catalog / discovery** — list supported GPU chips (H100, H200, B200, A100,
   L4, RTX PRO 6000, T4, GB200/GB300) and TPUs (v6e, v5p, v5e, v4, v3, v2),
   their machine types, supported zones, and which consumption models each
   supports.
3. **Capacity Advisories** (read-only, safe — no resources created):
   - **Spot VM Advisory** — availability + preemption risk per zone.
   - **DWS Calendar Advisory** — recommended zones/time-windows; plus a
     "best plan" mode that splits a large request across VM-count levels.
   - **DWS Flex Start Advisory** _(Preview — requires a whitelisted project)_ —
     estimated wait time in the queue before Flex Start capacity is granted.
4. **Scan & Deploy** — a priority-based hunt that tries consumption methods in
   order (or in parallel) across zones until capacity is secured. **This creates
   real, billable GCP resources** — always confirm with the user first.

## The one tool: `scripts/capacity_radar.py`

Everything is done by running this Python CLI. It reuses the app's own backend
modules, so results are identical to the web UI. Every command prints JSON to
stdout so you can parse it and decide the next step.

Run it with the repo's Python environment (see Setup). General shape:

```bash
python scripts/capacity_radar.py <group> <command> [flags...]
```

### Commands

| Command | Purpose |
|---|---|
| `auth check --project <ID>` | Check ADC auth + project access + Compute API status |
| `auth login` | Run `gcloud auth application-default login` (opens browser) |
| `catalog chips` | List GPU/TPU chips grouped, with machine types |
| `catalog machine-types` | Full machine-type list + support matrix |
| `catalog zones --machine-type <MT>` | Supported zones/regions for a machine type |
| `catalog networks --project <ID> [--region <R>]` | List VPC networks (+ regional subnetworks); flags whether a `default` VPC exists |

| `advise spot --project <ID> --machine-type <MT> [--regions r1,r2] [--zones z1,z2]` | Spot availability + preemption risk |
| `advise calendar --project <ID> --machine-type <MT> --vm-count N --start-date YYYY-MM-DD --flexibility-days 0..3 --duration-days D [--regions/--zones]` | DWS Calendar availability |
| `advise calendar-plan ... (same flags as calendar)` | DWS Calendar "best split" plan |
| `advise flex --project <ID> --machine-type <MT> --size N --max-run-hours H [--regions/--zones]` | DWS Flex Start wait-time advisory (Preview) |
| `deploy --project <ID> --machine-type <MT> --vm-count N --priority <spec> [--priority ...] [--parallel] [--yes]` | Priority-based scan & deploy (REAL resources) |

### `--priority` spec format (deploy)

Repeatable. `method[:zones][:max_retries][:retry_interval]`
- `method` = `on_demand` | `dws_calendar` | `dws_flex` | `spot`
- `zones` = comma-separated (omit → all supported zones for the machine type)
- Examples: `spot:us-central1-b,us-east4-a`  ·  `dws_flex:us-central1-b:3:60`

Extra deploy flags: `--name-prefix`, `--flex-max-wait-hours`, `--flex-usage-hours`,
`--calendar-start`/`--calendar-end` (ISO datetime), `--calendar-duration-hours`,
`--network`, `--subnetwork`.

### Network / subnetwork selection (GPU + TPU)

Both GPU VMs and TPU nodes need a VPC network. By default Cloud TPU uses a VPC
literally named `default`; on projects without one, deploys fail with
`The value "default" provided for field "Network"/"Subnetwork" does not exist`.

- Leave `--network`/`--subnetwork` **empty** to auto-select: prefer a `default`
  VPC, else the first VPC that has a subnetwork in the target region.
- Run `catalog networks --project <ID> --region <R>` to list options and see
  `hasDefault`. If there is no usable network, create one:
  `gcloud compute networks create <name> --subnet-mode=auto` (or use the app UI's
  "Create new VPC" option), then pass `--network <name>`.


## How to behave (agent workflow)

Follow this loop and **ask follow-up questions whenever an input is missing** —
mirror how the app collects inputs. Never guess the project ID.

1. **Auth first.** Run `auth check --project <ID>`. If `authenticated` is false,
   offer to run `auth login` (tell them a browser will open). If the project is
   invalid or the Compute API is disabled, surface the `instructions` from the
   JSON and help them fix it.
2. **Clarify intent.** Ask what they want: *advice* (safe) or *deploy* (creates
   resources). Suggest capabilities they may not know about.
3. **Resolve the resource accurately (NEVER confuse B200 with GB200/GB300).**
   Use this exact chip-to-machine-type mapping:
   - **B200 (NVIDIA B200, A4 family)**: `a4-highgpu-8g` (8x B200). **Do NOT confuse `B200` (`a4-highgpu-8g`) with `GB200` (`a4x-highgpu-4g`) or `GB300` (`a4x-maxgpu-4g-metal`)!**
   - **GB200 (NVIDIA GB200, A4X family)**: `a4x-highgpu-4g` (4x GB200).
   - **GB300 (NVIDIA GB300, A4X Max bare metal)**: `a4x-maxgpu-4g-metal` (4x GB300).
   - **H200 (NVIDIA H200, A3 Ultra)**: `a3-ultragpu-8g` (8x H200).
   - **H100 (NVIDIA H100 80GB, A3 family)**: `a3-highgpu-8g` (default 8x H100), `a3-highgpu-4g`, `a3-highgpu-2g`, `a3-highgpu-1g`, `a3-megagpu-8g` (A3 Mega), `a3-edgegpu-8g` (A3 Edge).
   - **RTX PRO 6000 (G4 family)**: `g4-standard-48` (1x), `g4-standard-96` (2x), `g4-standard-192` (4x), `g4-standard-384` (8x).
   - **L4 (NVIDIA L4, G2 family)**: `g2-standard-4` (1x, default), `g2-standard-8`, `g2-standard-12`, `g2-standard-16`, `g2-standard-24` (2x), `g2-standard-32`, `g2-standard-48` (4x), `g2-standard-96` (8x).
   - **A100 80GB (A2 Ultra)**: `a2-ultragpu-1g`, `a2-ultragpu-2g`, `a2-ultragpu-4g`, `a2-ultragpu-8g`.
   - **A100 40GB (A2 Standard/Mega)**: `a2-highgpu-1g`, `a2-highgpu-2g`, `a2-highgpu-4g`, `a2-highgpu-8g`, `a2-megagpu-16g`.
   - **T4 (NVIDIA T4, N1 family)**: `n1-standard-4 (1x T4)`, `n1-standard-8 (1x T4)`, `n1-standard-16 (2x T4)`, `n1-standard-32 (4x T4)`.
   - **TPU v6e (Trillium)**: `ct6e-standard-1t`, `ct6e-standard-4t`, `ct6e-standard-8t`.
   - **TPU v5p**: `ct5p-hightpu-1t`, `ct5p-hightpu-2t`, `ct5p-hightpu-4t`.
   - **TPU v5e**: `ct5e-hightpu-1t`, `ct5e-hightpu-4t`, `ct5e-hightpu-8t`.
   - **TPU v7x (Ironwood)**: `tpu7x-standard-4t`.
4. **Run the advisories proactively** when the user asks to find capacity (especially "find any kind of <chip>" or "I don't care what region or when"):
   - Run **all supported advisories** (DWS Calendar, Spot VM, and DWS Flex Start) right away rather than just setting dropdowns or asking unnecessary questions when defaults (e.g. `vm_count=1`, `duration_days=7`, all supported regions) work.
   - Summarize results clearly (which zones have capacity, windows, preemption scores, estimated waits) and also check or mention project quotas if relevant. For DWS Flex, note it is Preview/whitelisted; if you get `The service is not available for this project.`, explain the project needs allow-listing.
5. **For deploy:** build the priority strategy from the conversation, show the
   plan, and **require explicit user confirmation**. Only then run `deploy`
   with `--yes`. Without `--yes`, the CLI returns `requiresConfirmation: true`
   and a `plan` object — present that plan and ask the user to confirm. Stream
   progress (deploy writes live events to stderr and a full `events` array in
   the final JSON).

### Safety rules

- **Deploy creates real, billable resources.** Always confirm the exact plan
  (project, machine type, count, methods, zones) with the user before using
  `--yes`.
- Advisories are read-only and safe to run freely.
- Never fabricate project IDs, zones, or results — always run the CLI and report
  what it returns.

## Setup (one time)

The skill needs the Capacity Radar backend deps available to Python and the
gcloud SDK for auth.

```bash
# From the repo root:
pip install -r backend/requirements.txt
# Authenticate once (or use: python skills/capacity-radar/scripts/capacity_radar.py auth login)
gcloud auth application-default login
```

Then invoke the CLI from the repo root, e.g.:

```bash
python skills/capacity-radar/scripts/capacity_radar.py catalog chips
```

## Quick examples

```bash
# Check auth + project
python skills/capacity-radar/scripts/capacity_radar.py auth check --project my-project

# "Do you have H100s on spot in us-central1?"
python skills/capacity-radar/scripts/capacity_radar.py advise spot \
  --project my-project --machine-type a3-highgpu-8g --regions us-central1

# "How long would I wait for 4x H100 on DWS Flex?"
python skills/capacity-radar/scripts/capacity_radar.py advise flex \
  --project my-project --machine-type a3-highgpu-8g --size 4 --max-run-hours 24 --regions us-central1

# "Hunt for 2x H100: try spot first, then DWS Flex" (after user confirms)
python skills/capacity-radar/scripts/capacity_radar.py deploy \
  --project my-project --machine-type a3-highgpu-8g --vm-count 2 \
  --priority spot:us-central1-b,us-east4-a \
  --priority dws_flex:us-central1-b \
  --yes
```
