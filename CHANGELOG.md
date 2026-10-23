# Changelog

All notable changes, features, and fixes added to **Capacity Radar** are documented in this file.

---

## [2026-10-23] — Agentic Chat Assistant, Quota Explorer & 1-Click Advisory Deployments

### Added
- **In-App AI Capacity Assistant (`backend/agent.py`, `frontend/src/components/ChatAssistant.jsx`)**:
  - Collapsible assistant panel at the top of the workspace (above the `Capacity Advisory`, `Scan & Deploy`, and `Quota` tabs) powered by Vertex AI **Gemini 3 Flash** (`gemini-3-flash-preview`) and **Gemini 3.1 Pro** (`gemini-3.1-pro-preview`) and [`skills/capacity-radar/SKILL.md`](skills/capacity-radar/SKILL.md).
  - **Live Screen Context Injection (`uiContext`)**: Automatically passes the active tab's state and visible results (`AdvisoryPanel`, `ScanningPanel`, and `QuotaPanel`) to the assistant so users can ask questions about what is currently on screen without redundant API calls.
  - **UI-Driving Agent Tools (`uiActions`)**:
    - `advise_all`: Runs **DWS Calendar Mode + Spot VM + DWS Flex Start** advisories concurrently (`asyncio.gather`) and populates all three result tables on the **Capacity Advisory** tab at once.
    - `advise_spot`, `advise_calendar`, `advise_flex`: Run individual advisories and update the **Capacity Advisory** tab.
    - `check_project_quotas` & `drive_ui_filter_quotas`: Query live project quotas across all 51 GPU/TPU/VM metrics and optionally switch to and filter the **Quota** tab.
    - `drive_ui_configure_scan`: Switches to **Scan & Deploy** and pre-configures single- or multi-priority hunting plans (`on_demand`, `dws_calendar`, `dws_flex`, `spot`) with `auto_start=false` by default so billable resource creation always requires user confirmation.
  - **Strict Chip Disambiguation (`B200` vs `GB200`/`GB300`)**: Word-boundary matching (`_chip_matches`) and explicit chip-to-machine-type mapping in `SKILL.md` ensure `"B200"` always resolves to `a4-highgpu-8g` and never matches `GB200` (`a4x-highgpu-4g`).
- **365-Day DWS Calendar Search Window (`frontend/src/components/AdvisoryPanel.jsx`, `backend/agent.py`)**:
  - Extended the **Search Window** selector on the **DWS Calendar Advisory** card from a 14-day maximum up to **+30 days**, **+60 days**, **+90 days**, **+180 days**, and **+365 days (1 year)**, plus dynamic support for any custom day horizon set by the AI Assistant (searching for any `1` to `Max Duration` day slot).
- **1-Click Deploy Across All Advisory Cards (`frontend/src/components/AdvisoryPanel.jsx`, `frontend/src/components/ScanningPanel.jsx`)**:
  - Added **Deploy** buttons on every available zone row and **Scan All Spot/Flex/Recommended Zones** buttons across **Spot VM Advisory**, **DWS Flex Start Advisory**, and **DWS Calendar Mode Advisory** (including Best Split Plans) to jump straight into **Scan & Deploy** with all parameters pre-filled.
- **GPU & TPU Quota Explorer Tab (`backend/quotas.py`, `frontend/src/components/QuotaPanel.jsx`, `GET /api/quotas`)**:
  - Added a 51-item catalog of all GPU (`GB300`, `GB200`, `B200`, `H200`, `H100 Mega/High/Edge`, `RTX PRO 6000`, `A100 80GB/40GB`, `L4`, `T4`, `GPUS_ALL_REGIONS`), TPU (`v7x`, `v6e`, `v5p`, `v5e`, `v4`, `v3`, `v2`), and related VM/host quotas (`CPUS`, `PREEMPTIBLE_CPUS`, `A2_CPUS`, `CPUS_ALL_REGIONS`, `tpu.googleapis.com/queuedResources`, `tpus_per_tpu_family`).
  - Fetches live regional/zonal limits and current usage in parallel from `serviceusage.googleapis.com` and `compute.googleapis.com`.
  - Organizes quotas by category, chip, VM family, and consumption model (`On-Demand / Reserved`, `Spot VM`, `DWS Flex-Start`, `DWS Calendar`, `Committed CUD`), with a toggle for related VM/host quotas and 1-click deep links to the Google Cloud Console Quotas page.

---

## [2026-10-22] — Cloud TPU DWS Calendar Mode, Quota Fail-Fast & TPU v7x Enhancements

### Added
- **Live DWS Calendar Mode Advisory for Cloud TPUs (`v6e`, `v5p`, `v5e`) (`backend/advisory.py`)**:
  - Enabled `POST .../compute/beta/projects/{project}/regions/{region}/advice/calendarMode` for TPUs using `aggregateResources` (`VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT6E`, `VM_FAMILY_CLOUD_TPU_POD_SLICE_CT5P`, `VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT5LP`).
- **TPU DWS Calendar Future Reservation Provisioning & Polling (`backend/hunter.py`)**:
  - Updated `_try_tpu_dws_calendar()` to create Compute Engine `futureReservations` (`reservationMode: "CALENDAR"`, `deploymentType: "DENSE"`, `aggregateReservation`) and updated `_poll_future_reservation_status()` to treat `"PROCURING"`, `"APPROVED"`, `"PROVISIONING"`, and `"FULFILLED"` as approved states.
  - Enforced mandatory minimum lead times (`6h 15m` for TPUs and DWS Calendar advisories, `87h+` for GPUs where applicable).
- **Fail-Fast Quota Pruning & Accurate HTTP 403 Quota Diagnostics (`backend/hunter.py`)**:
  - Fixed `_parse_api_error()` so synchronous `HTTP 403` `quotaExceeded` errors are reported as quota errors rather than generic IAM permission errors.
  - Added `quota_blocked_regions` and `quota_blocked_zones` tracking during scan loops so regions with `0` quota are pruned immediately instead of retrying in a loop.
- **TPU v7x (Ironwood), Multi-Host Slice Sizing & Dedicated DWS Flex Pool Zones (`backend/gpu_data.py`, `backend/hunter.py`)**:
  - Added Cloud TPU `v7x` (`tpu7x-standard-4t`) to the catalog.
  - Updated TPU provisioning to scale `total_chips = chips_per_host * vm_count` for multi-host pod slices, pass `runDuration` on DWS Flex Queued Resources, and prioritize Dedicated DWS Flex Pool zones (`us-central1-a`, `us-east5-b`, `southamerica-west1-a`, `asia-northeast1-b` for `v6e`).
