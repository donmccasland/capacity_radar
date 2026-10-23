# Capacity Radar (`MG-Cafe/capacity_radar`) — Scanning, Advisory, Quota & Agentic Chat Improvements

> **Status Update (2026-10-23):** All improvements documented below — plus the **GPU & TPU Quota Explorer Tab** (`backend/quotas.py`, `QuotaPanel.jsx`), **1-Click Deploy Across All Advisory Cards**, **365-Day DWS Calendar Search Window**, and the **In-App AI Capacity Assistant** (`backend/agent.py`, `ChatAssistant.jsx` powered by Gemini 3 Flash & Gemini 3.1 Pro) — have been implemented on `main`. See [`CHANGELOG.md`](CHANGELOG.md) for full release details.

This document captures the engineering improvements implemented for **Capacity Radar**, focusing on:
1. **Pre-Flight Quota Verification, Quota Explorer Tab & Fail-Fast Scanning** (preventing scans in `0`-quota regions, surfacing live regional/zonal limits & usage across 51 GPU/TPU/VM metrics, and accurate quota diagnostics).
2. **Cloud TPU Advisory & Scanning Enhancements** (enabling live DWS Calendar Mode Advisories for TPUs, fixing TPU Calendar Mode reservation creation/polling including `PROCURING`, aligning DWS Flex-Start zone lists with dedicated pool realities, and adding TPU v7x / Ironwood support).
3. **In-App Agentic Chat Assistant & 1-Click Advisory Workflows** (context-aware Q&A and UI-driving agent tools powered by Vertex AI Gemini 3 Flash / 3.1 Pro and `skills/capacity-radar/SKILL.md`).

## Part 1: Quota Verification & Pre-Flight Filtering Improvements

### 1.1 Pre-Flight Quota Check Endpoint (`POST /api/quota/check`)

* **Current Gap**:
  * Neither `POST /api/auth/check` (`backend/main.py`, lines 254–263), nor the **Capacity Advisory** tab (`backend/advisory.py`), nor `ScanningSession.run()` (`backend/hunter.py`, lines 284–384) inspects the target project's GPU or TPU quota before scanning.
  * Customers frequently see `HIGH` Spot obtainability or `RECOMMENDED` Calendar windows in the Advisory tab for regions where their project actually has **`0` quota**, and then waste 10–30 minutes scanning zones that can never provision.
* **Proposed Improvement**:
  Add a `POST /api/quota/check` endpoint in `backend/main.py` (and a pre-flight check at the start of `ScanningSession.run()` in `backend/hunter.py`) that queries the project's effective quota for the selected `machineType`, `vmCount`, and `consumptionModel` (`on_demand`, `dws_calendar`, `dws_flex`, `spot`) across all candidate regions/zones.

#### Quota Metric Mapping by Accelerator & Consumption Model
 Crucially, **On-Demand / DWS Calendar Mode** and **Spot / DWS Flex-Start** draw from **different quota buckets**:

| Accelerator Family | Machine Type(s) | On-Demand & DWS Calendar Quota Metric (`compute.googleapis.com` / `tpu.googleapis.com`) | Spot & DWS Flex-Start Quota Metric (`PREEMPTIBLE` Bucket) | Scope |
| :--- | :--- | :--- | :--- | :--- |
| **A4X (GB200)** | `a4x-highgpu-4g` | `NVIDIA_GB200_GPUS` / `gpus_per_gpu_family` (`NVIDIA_GB200`) | `PREEMPTIBLE_NVIDIA_GB200_GPUS` | Regional |
| **A4 (B200)** | `a4-highgpu-8g` | `NVIDIA_B200_GPUS` / `gpus_per_gpu_family` (`NVIDIA_B200`) | `PREEMPTIBLE_NVIDIA_B200_GPUS` | Regional |
| **A3 Ultra (H200)** | `a3-ultragpu-8g` | `NVIDIA_H200_GPUS` / `gpus_per_gpu_family` (`NVIDIA_H200`) | `PREEMPTIBLE_NVIDIA_H200_GPUS` | Regional |
| **A3 Mega (H100)** | `a3-megagpu-8g` | `NVIDIA_H100_MEGA_GPUS` | `PREEMPTIBLE_NVIDIA_H100_MEGA_GPUS` | Regional |
| **A3 High / Edge (H100)** | `a3-highgpu-*`, `a3-edgegpu-8g` | `NVIDIA_H100_GPUS` | `PREEMPTIBLE_NVIDIA_H100_GPUS` | Regional |
| **A2 Ultra (A100 80GB)** | `a2-ultragpu-*` | `NVIDIA_A100_80GB_GPUS` | `PREEMPTIBLE_NVIDIA_A100_80GB_GPUS` | Regional |
| **A2 Standard (A100 40GB)** | `a2-highgpu-*`, `a2-megagpu-16g` | `NVIDIA_A100_GPUS` | `PREEMPTIBLE_NVIDIA_A100_GPUS` | Regional |
| **G4 (RTX PRO 6000)** | `g4-standard-*` | `NVIDIA_RTX_PRO_6000_GPUS` | `PREEMPTIBLE_NVIDIA_RTX_PRO_6000_GPUS` | Regional |
| **G2 (L4)** | `g2-standard-*` | `NVIDIA_L4_GPUS` | `PREEMPTIBLE_NVIDIA_L4_GPUS` | Regional |
| **N1 (T4)** | `n1-standard-*` | `NVIDIA_T4_GPUS` | `PREEMPTIBLE_NVIDIA_T4_GPUS` | Regional |
| **Cloud TPU v6e** | `ct6e-standard-*` | `tpu.googleapis.com/tpu_v6e_training_chips` & `tpu_v6e_serving_chips` (or `tpus_per_tpu_family`) | `tpu.googleapis.com/tpu_v6e_preemptible_chips` & `tpu_v6e_serving_preemptible_chips` | Zonal / Regional |
| **Cloud TPU v5p** | `ct5p-hightpu-*` | `tpu.googleapis.com/tpu_v5p_chips` (or `tpus_per_tpu_family`) | `tpu.googleapis.com/tpu_v5p_preemptible_chips` | Zonal / Regional |
| **Cloud TPU v5e** | `ct5e-hightpu-*` | `tpu.googleapis.com/tpu_v5_lite_podslice_chips` | `tpu.googleapis.com/tpu_v5_lite_podslice_preemptible_chips` | Zonal / Regional |

* **Implementation Design**:
  1. For **GPUs**, call `GET https://compute.googleapis.com/compute/v1/projects/{project}/regions` (single fast call returning all regional `quotas[]` with `metric`, `limit`, and `usage`), plus `GET .../projects/{project}` for global `GPUS_ALL_REGIONS`.
  2. For **TPUs** (or dimension-scoped `gpus_per_gpu_family`), query `GET https://serviceusage.googleapis.com/v1beta1/projects/{project}/services/{compute|tpu}.googleapis.com/consumerQuotaMetrics` and evaluate `effectiveLimit` per `region` / `zone` and `gpu_family` / `tpu_family` dimension.
  3. Compute `available_quota = limit - usage` and compare against `required_chips = vm_count * chips_per_vm`.
  4. In the UI (`AdvisoryPanel.jsx` and `ScanningPanel.jsx`), display a **Quota Status Badge** (`🟢 Quota OK: 32/32 free`, `🟡 Partial Quota: 8/16 free`, or `🔴 0 Quota in Region`) next to each zone in the Zone Selector and Advisory results table, with a direct link to the GCP Console Quotas page pre-filtered to that metric and region.

### 1.2 Fix Synchronous `HTTP 403` Quota Error Masking in `_parse_api_error()`

* **Current Bug (`backend/hunter.py`, lines 1519–1544)**:
  ```python
  if code == 403:
      return f"Permission denied ({code}). Check IAM permissions."
  elif code == 404:
      return f"Not found ({code}). Resource or API doesn't exist."
  elif code == 409:
      return f"Conflict ({code}). Resource may already exist."
  elif "quota" in ml:
      return f"Quota exceeded. ({message[:150]})"
  ```
  When Compute Engine or Cloud TPU returns a synchronous `HTTP 403` error because a quota limit is exceeded (e.g., `Quota 'GPUS_PER_GPU_FAMILY' exceeded. Limit: 0.0 in region us-central1`), `if code == 403:` matches first and overwrites the error with `"Permission denied (403). Check IAM permissions."`, misleading the user into debugging IAM instead of requesting quota.
* **Proposed Fix**:
  Check `"quota" in ml` (and `reason == "quotaExceeded"` or `"RATE_LIMIT_EXCEEDED"`) **before** checking `if code == 403:`:
  ```python
  if "quota" in ml or any(e.get("reason") == "quotaExceeded" for e in errors):
      return f"Quota exceeded: {message[:180]}"
  elif code == 403:
      return f"Permission denied ({code}): {message[:150]}"
  ```

### 1.3 Fail-Fast Zone Pruning on `QUOTA_EXCEEDED` During Scan Loops

* **Current Gap (`backend/hunter.py`, `_scan_with_method()` lines 490–521 & `_run_parallel()` lines 423–470)**:
  During a multi-round scan (`max_retries = 10`, `retry_interval = 60s`), if `us-central1-a` fails with `Quota exceeded (Limit: 0 in region us-central1)`, `hunter.py` keeps `us-central1-a` (and sibling zones `us-central1-b`, `us-central1-c` in the same region) in the active `zones` list and retries them on every subsequent round.
* **Proposed Improvement**:
  * Maintain a per-method `quota_blocked_regions: set[str]` and `quota_blocked_zones: set[str]` inside `ScanningSession`.
  * When a provisioning call or polled operation fails with a regional `QUOTA_EXCEEDED` error, emit a clear warning (`🚫 Removing region us-central1 from [On-Demand] retry loop due to insufficient regional quota`) and prune all zones in that region from remaining retry attempts for that consumption model.
  * If **all** candidate zones for a priority are pruned due to quota exhaustion, **immediately advance to the next priority** (e.g., fall back from `On-Demand` to `DWS Flex Start` or `Spot`, which use `PREEMPTIBLE` quota buckets!) without waiting for `max_retries * retry_interval`.

## Part 2: Cloud TPU Advisory & Scanning Improvements

### 2.1 Enable Live DWS Calendar Mode Advisory for TPU `v6e`, `v5p`, and `v5e`

* **Current Bug (`backend/advisory.py`, lines 68–112, 148, 206, 313–318)**:
  * `_check_tpu()` unconditionally short-circuits `get_calendar_advisory()` and `find_best_splits()` for all TPU machine types, returning zero recommendations and a static message claiming: *"The Calendar Advisory API does not support TPU machine types."*
  * This happened because `_query_calendar_advisory_region()` constructs a GPU `specificSkuResources` payload (`{"instanceCount": vm_count, "machineType": "ct6e-standard-4t"}`), which the Compute Engine API rejects for TPUs.
* **Proposed Fix**:
  Google Cloud's Calendar Mode Advisory API (`POST https://compute.googleapis.com/compute/beta/projects/{project}/regions/{region}/advice/calendarMode`) **does** support TPU `v6e`, `v5p`, and `v5e` when called on the `beta` endpoint using `aggregateResources` instead of `specificSkuResources`:
  1. Only short-circuit `_check_tpu()` for older TPUs that do not support Calendar Mode (`v4`, `v3`, `v2`).
  2. In `_query_calendar_advisory_region()`, map TPU generations to their Compute Engine `vmFamily` enum and calculate `acceleratorCount = vm_count * chips_per_vm`:
     ```python
     TPU_CALENDAR_VM_FAMILY = {
         "v6e": "VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT6E",
         "v5p": "VM_FAMILY_CLOUD_TPU_POD_SLICE_CT5P",
         "v5e": "VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT5LP",
     }
     ```
  3. Build the TPU `spec_body` using the `compute/beta` URL:
     ```python
     if tpu_info and tpu_info["gen"] in TPU_CALENDAR_VM_FAMILY:
         url = (
             f"https://compute.googleapis.com/compute/beta/projects/{project}"
             f"/regions/{region}/advice/calendarMode"
         )
         total_chips = vm_count * tpu_info["chips"]
         spec_body = {
             "deploymentType": "DENSE",
             "targetResources": {
                 "aggregateResources": {
                     "acceleratorCount": str(total_chips),
                     "vmFamily": TPU_CALENDAR_VM_FAMILY[tpu_info["gen"]],
                     "workloadType": "BATCH",  # or user-selectable BATCH / SERVING
                 }
             },
             "timeRangeSpec": {
                 "minDuration": f"{min_duration_secs}s",
                 "maxDuration": f"{max_duration_secs}s",
                 "startTimeNotEarlierThan": start_time,
                 "startTimeNotLaterThan": end_time,
             },
         }
     ```

### 2.2 Fix TPU DWS Calendar Mode Provisioning & Polling in `backend/hunter.py`

* **Current Bugs (`backend/hunter.py`, `_try_tpu_dws_calendar()` lines 1380–1460)**:
  1. **Wrong API & Missing Start Time**: `_try_tpu_dws_calendar()` calls `POST https://tpu.googleapis.com/v2/.../queuedResources` with `"guaranteed": {"minDuration": ...}` and `"queueingPolicy": {"validUntilTime": end_str}`—never even passing `start_str` (`validInterval` / `validAfterTime`)! Furthermore, official DWS Calendar Mode reservations for TPUs are created via **Compute Engine Future Reservations** (`POST https://compute.googleapis.com/compute/beta/projects/{project}/zones/{zone}/futureReservations` with `reservationMode: "CALENDAR"` and `aggregateReservation`).
  2. **Violates Mandatory Minimum Lead Time Rules**: When the user leaves `calendarStartTime` blank in the UI, `hunter.py` defaults to `datetime.now(timezone.utc) + timedelta(hours=1)` (lines 628 and 1392). However, Google Cloud DWS Calendar Mode enforces a **mandatory minimum lead time**:
     * **TPUs (`v5e`, `v5p`, `v6e`)**: At least **6 hours** before `startTime`.
     * **GPUs (`A4`, `A3 Ultra`, `A3 Mega`, `A3 High`)**: At least **87 hours (3 days, 15 hours)** before `startTime`.
     Requests inside this lead-time window are automatically rejected by the API.
  3. **Polls for `ACTIVE` Instead of `APPROVED` / `WAITING_FOR_RESOURCES`**: `_try_tpu_dws_calendar()` calls `_poll_tpu_queued_resource()`, which only returns `True` when `state == "ACTIVE"`. Because a Calendar reservation starts in the future (6+ hours out), it will remain in `WAITING_FOR_RESOURCES` / `ACCEPTED` (or `APPROVED` on a Future Reservation) until the future start date arrives. Polling for `ACTIVE` guarantees that every TPU Calendar Mode scan times out and reports failure!
* **Proposed Fixes**:
  1. **Enforce Minimum Lead Times in Default & UI Validation**:
     * Default `calendar_start_time` to `now + 7 hours` for TPUs and `now + 88 hours` for GPUs (and validate in `ScanningPanel.jsx` with a helpful warning if the user picks a start time closer than 6h for TPUs or 87h for GPUs).
  2. **Use Compute Engine `futureReservations` with `aggregateReservation` for TPU Calendar Mode**:
     ```python
     body = {
         "name": res_name,
         "reservationMode": "CALENDAR",
         "planningStatus": "SUBMITTED",
         "specificReservationRequired": True,
         "autoDeleteAutoCreatedReservations": True,
         "deploymentType": "DENSE",
         "timeWindow": {"startTime": start_str, "endTime": end_str},
         "aggregateReservation": {
             "vmFamily": TPU_CALENDAR_VM_FAMILY[tpu_version],
             "workloadType": "BATCH",
             "reservedResources": [{
                 "accelerator": {
                     "acceleratorCount": total_chips,
                     "acceleratorType": f"zones/{zone}/acceleratorTypes/{accel_type}",
                 }
             }],
         },
     }
     ```
     And poll `_poll_future_reservation_status()` until `procurementStatus in ("APPROVED", "PROVISIONING", "FULFILLED")` (or if using TPU Queued Resources with `validInterval`, treat `state in ("WAITING_FOR_RESOURCES", "PROVISIONING", "ACTIVE")` as a successful reservation booking).

### 2.3 Fix TPU DWS Flex-Start (`_try_tpu_queued_resource`) Missing Run Duration & Dedicated Pool Zone Filtering

* **Current Gaps**:
  1. **`flex_usage_duration_hours` Is Ignored in `_try_tpu_queued_resource()` (`backend/hunter.py`, lines 1315–1348)**:
     Although `_try_tpu_queued_resource` accepts `flex_usage_duration_hours` and logs `"usage: {flex_usage_duration_hours}h"`, the request body sent to `tpu.googleapis.com/v2/.../queuedResources` **never includes `maxRunDuration` / `runDuration`**! It only sets `queueingPolicy.validUntilDuration`.
  2. **Dedicated DWS Flex Pool vs. General TPU Zones (`backend/gpu_data.py`, lines 484–496)**:
     `TPU_TYPES["v6e"]["zones"]` lists 7 zones (`asia-northeast1-b`, `europe-west4-a`, `southamerica-west1-a`, `us-central1-b`, `us-east1-d`, `us-east5-a`, `us-east5-b`).
     However, Cloud TPU DWS Flex-Start does **not** draw from general unreserved fleet headroom; it draws from an explicitly partitioned **Dedicated DWS Flex Pool** (for `v6e`, provisioned in `us-central1-a`, `us-east5-b`, `southamerica-west1-a`, and `asia-northeast1-b`). Notice that `us-central1-a` isn't even in `TPU_TYPES["v6e"]["zones"]`, while zones without a dedicated DWS Flex pool (`europe-west4-a`, `us-east1-d`, `us-east5-a`) are scanned and will queue indefinitely without dispatching!
* **Proposed Improvements**:
  1. Add per-consumption-model zone lists (or `dws_flex_zones` metadata) to `TPU_TYPES` in `backend/gpu_data.py` (including `us-central1-a` and `us-west1-c` for `v6e`), and prioritize Dedicated DWS Flex Pool zones first when scanning `dws_flex`.
  2. Pass the requested run duration (`schedulingConfig` / `runDuration`) when creating DWS Flex TPU Queued Resources so DWS can schedule the slice appropriately.

### 2.4 Multi-Host TPU Slice Support (`topologies` vs. Single-Host `machine_types`) & TPU v7x (Ironwood)

* **Current Gaps (`backend/gpu_data.py`, lines 483–553 & `backend/hunter.py`, lines 1176–1195)**:
  1. **Multi-Host Pod Slices Cannot Be Scanned**:
     In `backend/gpu_data.py`, `TPU_TYPES` defines `topologies` (e.g., `"2x4"`, `"4x4"`, `"8x8"`, `"16x16"` for `v6e`), **but the UI selector only exposes single-host `machine_types`** (`ct6e-standard-1t`, `ct6e-standard-4t`, `ct6e-standard-8t`).
     Furthermore, in `backend/hunter.py`, `_get_tpu_accelerator_type()` (lines 1192–1194) hardcodes `f"{prefix}-{chips}"` using the single-host `chips` count (`1`, `4`, or `8`) and ignores `self.vm_count`! If a user requests `vm_count = 4` of `ct6e-standard-4t` (expecting a 16-chip `v6e-16` pod slice or 4x 4-chip nodes), `_try_tpu_on_demand` / `_try_tpu_queued_resource` only requests a single 4-chip `v6e-4` (`vm_count` is completely ignored for TPUs!).
  2. **Missing TPU v7x (Ironwood)**:
     Projects like `tpu-launchpad-playground` already have `tpu7x` Spot quota (`tpu.googleapis.com/preemptible_tpu7x_chips`), but `TPU_TYPES` in `backend/gpu_data.py` only lists `v6e`, `v5p`, `v5e`, `v4`, `v3`, and `v2`.
* **Proposed Improvements**:
  1. **Account for `vm_count` (or Topology Selector) in TPU Provisioning**:
     Multiply `total_chips = chips_per_host * self.vm_count` in `_get_tpu_accelerator_type()` (e.g., `4 hosts * ct6e-standard-4t` → `v6e-16`), or allow the user to select a TPU slice topology (`v6e-16`, `v6e-32`, `v6e-64`, `v5p-16`, `v5p-32`, etc.) directly in the UI.
  2. **Add `v7x` (Cloud TPU v7x / Ironwood) to `TPU_TYPES` in `backend/gpu_data.py`**:
     Include `v7x` machine types/topologies and supported consumption models so customers with Ironwood preview/Spot access can scan and deploy from Capacity Radar.
  3. **Make `runtimeVersion` Configurable (and Up-to-Date)**:
     `backend/hunter.py` (lines 1240, 1338, 1413, 1480) hardcodes `"runtimeVersion": "tpu-vm-tf-2.17.0-pjrt"` for all TPU generations. `v6e` and `v7x` often require `v2-alpha-tpuv6e` or `tpu-ubuntu2204-base` rather than an older TensorFlow runtime image, which can cause `400 Invalid runtimeVersion` errors during TPU creation. Map default `runtimeVersion` per TPU generation in `TPU_TYPES` (defaulting to `"v2-alpha-tpuv6e"` / `"tpu-ubuntu2204-base"` for `v6e`).
