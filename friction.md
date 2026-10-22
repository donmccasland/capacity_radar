# Capacity Radar (`MG-Cafe/capacity_radar`) — Deployment & Operational Friction Log

This document captures the technical challenges, enterprise policy friction points, and architectural edge cases encountered when deploying and operating **Capacity Radar** on Google Cloud, along with verified workarounds for each.

## 1. Enterprise Project Creation & Billing Account Attachment

### Challenge
1. **Organization / Folder Creation Restrictions**: Running standard `gcloud projects create <project-id> --organization=<org-id>` (or `--folder=<folder-id>`) fails in enterprise organizations (such as `google.com` / `organizations/433637338589`) with:
   ```bash
   ERROR: (gcloud.projects.create) PERMISSION_DENIED: Permission 'resourcemanager.projects.create' denied on resource '//cloudresourcemanager.googleapis.com/organizations/433637338589'
   ```
   Enterprise GCP environments restrict direct `resourcemanager.projects.create` permissions for human users at the organization root and folder levels to enforce mandatory billing account attachment, data governance labels, and folder hierarchy placement.
2. **Missing Billing Account on Fresh CLI Projects**: When a customer creates a project via `gcloud projects create`, **no billing account is linked by default** (`billingEnabled: false`). Subsequent attempts to enable `run.googleapis.com`, `cloudbuild.googleapis.com`, or `compute.googleapis.com` fail with `FAILED_PRECONDITION: Billing must be enabled for activation of service(s)`.
3. **Missing Explicit Prerequisites in README**: The README's Cloud Run deployment section jumps straight to `gcloud iam service-accounts create` and `gcloud run deploy --source .` without listing the required API enablement command (`run.googleapis.com`, `cloudbuild.googleapis.com`, `artifactregistry.googleapis.com`, `iam.googleapis.com`, `compute.googleapis.com`).

### Workaround
- **External / Enterprise Customers**:
  1. Provision the project via your organization's Project Factory / Terraform pipeline, or link a billing account immediately after creation:
     ```bash
     gcloud billing projects link <project-id> --billing-account=<billing-account-id>
     ```
  2. Explicitly enable all required build and runtime APIs before deploying:
     ```bash
     gcloud services enable \
       run.googleapis.com \
       cloudbuild.googleapis.com \
       artifactregistry.googleapis.com \
       compute.googleapis.com \
       iam.googleapis.com \
       --project=<project-id>
     ```
- **Google Internal (`google.com` Org)**: Use the `cloudwerkcli` project factory, which automatically places the project in the appropriate folder and auto-assigns billing account `011F17-0F9CE4-E03264`:
  ```bash
  /google/bin/releases/chp-hia-team/cloudwerkcli/cloudwerkcli --defaults donmac-capacity-radar
  ```
  > **Note on Creator Roles in `cloudwerkcli` Projects**: `cloudwerkcli --defaults` grants the project creator `roles/writer` and `organizations/433637338589/roles/chp_project_creator_not_in_ual` (which includes `resourcemanager.projects.setIamPolicy` at the project level, but **not** `run.services.setIamPolicy`). Before configuring Cloud Run service IAM or IAP, grant yourself `roles/run.admin` and `roles/iap.admin`:
  > ```bash
  > gcloud projects add-iam-policy-binding donmac-capacity-radar \
  >   --member="user:$(gcloud config get-value account)" \
  >   --role="roles/run.admin"
  > gcloud projects add-iam-policy-binding donmac-capacity-radar \
  >   --member="user:$(gcloud config get-value account)" \
  >   --role="roles/iap.admin"
  > ```

## 2. Cloud Build Fails on New Projects Due to Default Compute Service Account Missing `roles/cloudbuild.builds.builder`

### Challenge
On any newly created GCP project (following Google Cloud's May 2024 security change and the `constraints/iam.automaticIamGrantsForDefaultServiceAccounts` organization policy), the **Default Compute Engine Service Account** (`<project-number>-compute@developer.gserviceaccount.com`) is **no longer automatically granted `roles/editor`**.

When running the README's deployment command (`gcloud run deploy capacity-radar --source .`), Cloud Run uploads the source zip to a staging GCS bucket (`run-sources-<project>-<region>`) and triggers Cloud Build using `<project-number>-compute@developer.gserviceaccount.com`. The build immediately fails with:
```bash
ERROR: (gcloud.run.deploy) PERMISSION_DENIED: Build failed because the default service account is missing required IAM permissions.
could not resolve source: Get "https://storage.googleapis.com/storage/v1/b/run-sources-donmac-capacity-radar-us-central1/o/services%2Fcapacity-radar%2F...zip":
generic::permission_denied: IAM permission denied for service account <project-number>-compute@developer.gserviceaccount.com.
```

### Workaround
Grant `roles/cloudbuild.builds.builder` to the project's default compute service account before running `gcloud run deploy --source .`:
```bash
PROJECT_ID="donmac-capacity-radar"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"
```
*(Alternatively, create a dedicated build service account and pass `--build-service-account` to `gcloud run deploy`.)*

## 3. Domain Restricted Sharing Blocks `--allow-unauthenticated` on Cloud Run

### Challenge
The README's hosted Cloud Run deployment instructions recommend:
```bash
gcloud run deploy capacity-radar --source . --allow-unauthenticated ...
```
In almost all enterprise GCP organizations, the **Domain Restricted Sharing** organization policy (`constraints/iam.allowedPolicyMemberDomains`) is enforced. Passing `--allow-unauthenticated` attempts to bind `allUsers` to `roles/run.invoker`, which fails with an IAM policy violation (`FAILED_PRECONDITION: One or more users named in the policy do not belong to a permitted customer`).

### Workaround
1. **Option A — Cloud Run with Native Identity-Aware Proxy (`--iap`) (Recommended for Enterprise)**:
   Deploy with `--no-allow-unauthenticated --iap` so only authenticated users within your organization/group can reach the app:
   ```bash
   gcloud beta run deploy capacity-radar \
     --source . \
     --project <host-project> \
     --region us-central1 \
     --no-allow-unauthenticated \
     --iap \
     --service-account capacity-radar-web@<host-project>.iam.gserviceaccount.com \
     --set-env-vars DEMO_MODE=false,AUTH_MODE=user \
     --memory 512Mi --cpu 1 --max-instances 1 \
     --session-affinity --timeout 3600
   ```
   Then grant `roles/run.invoker` (and IAP Web App User) to your user or team group.
2. **Option B — Local Authenticated Proxy via `gcloud run services proxy`**:
   Deploy with `--no-allow-unauthenticated` (and `--no-iap`) and tunnel locally on port `8080` using your `gcloud` identity:
   ```bash
   # On Debian/Ubuntu workstations, install the proxy component first if not present:
   sudo apt-get install -y google-cloud-cli-cloud-run-proxy

   gcloud run services proxy capacity-radar --project=<host-project> --region=us-central1 --port=8080
   ```

## 4. Certificate-Based Access (CBA) Rejects `gcloud auth print-access-token`

### Challenge
In `AUTH_MODE=user`, the Capacity Radar UI and README instruct the user to run:
```bash
gcloud auth print-access-token
```
and paste the token into the **Access Token** field.
In enterprise environments enforcing **Context-Aware Access / Certificate-Based Access (CBA)** (including Google Cloudtop workstations), `gcloud auth print-access-token` returns a token bound to the `gcloud` CLI's specific OAuth client ID, which **requires mTLS client certificates** on every API request:
```bash
WARNING: This access token is for the gcloud CLI tool itself and is bound to its specific client ID, which requires Certificate Based Access (CBA).
```
Because the Capacity Radar FastAPI backend (`httpx.AsyncClient`) does not possess the user's local device mTLS client certificate, API calls made with this token fail with `401/403` authentication errors.

### Workaround
Generate tokens using **Application Default Credentials (ADC)** instead, which uses an OAuth client ID intended for application development without CBA mTLS restrictions:
```bash
# One-time setup if not already logged in:
gcloud auth application-default login

# Generate a token that works inside Capacity Radar:
gcloud auth application-default print-access-token
```

## 5. Compute Alpha API & Preview Allowlisting Required for Spot & Flex-Start Advisories

### Challenge
Capacity Radar offers three Advisory features in the **Capacity Advisory** tab, but two of them rely on **Alpha / Preview** Compute Engine endpoints (`POST https://compute.googleapis.com/compute/alpha/projects/{project}/regions/{region}/advice/capacity`):
1. **Spot VM Advisory (`provisioningModel: "SPOT"`)**: Fails on standard GCP projects (including newly created projects like `donmac-capacity-radar`) with:
   ```bash
   HTTP 403: Required 'Alpha Access' permission for 'Compute API'
   ```
2. **DWS Flex-Start Advisory (`provisioningModel: "FLEX_START"`)**: Even on projects allowlisted for Compute Alpha API (where Spot Advisory succeeds), Flex-Start Advisory fails unless the project is separately enrolled in the Flex-Start Advisory Preview:
   ```bash
   HTTP 400: The service is not available for this project.
   ```
3. **DWS Calendar Mode Advisory (`compute/v1/.../advice/calendarMode`)**: Uses the `v1` endpoint and works on standard projects, provided the caller has `compute.advice.calendarMode` permission (included in `roles/owner` and `roles/compute.admin`, but **not** always in `roles/editor` on restricted projects).

### Workaround
- Use a project enrolled in the Capacity Advisor Preview (e.g., `gpu-launchpad-playground`, where Spot, Flex-Start, and Calendar Advisories all return `200 OK`, or `dx-supercomputer-testing` for Spot + Calendar Advisories) when running Advisory queries in the UI, or request project allowlisting via the account team (`go/caapis-preview-fr`).
- You can run the Advisory step against an allowlisted project to identify the best region/zone, and then switch the **Project ID** in the left drawer to your target deployment project before running **Scan & Deploy**.

## 6. Multi-Instance Scaling (`--max-instances 2`) Breaks In-Memory WebSocket Sessions

### Challenge
The README's Cloud Run deployment command specifies `--max-instances 2`:
```bash
gcloud run deploy capacity-radar ... --max-instances 2
```
However, `backend/hunter.py` stores all active scanning sessions in a process-local Python dictionary (`active_sessions: dict[str, ScanningSession] = {}`).
If Cloud Run scales to 2 instances:
- `POST /api/scan/start` may land on **Instance 1**, while the WebSocket connection (`WS /ws/scan/{session_id}`) or `POST /api/scan/{session_id}/cancel` lands on **Instance 2**, returning `404 Session not found` or losing live progress logs.
- Additionally, Cloud Run's default request timeout is 300 seconds (5 minutes), which drops long-running WebSocket connections during multi-zone capacity scans.

### Workaround
Deploy Cloud Run with `--max-instances=1`, enable `--session-affinity`, and increase `--timeout=3600`:
```bash
gcloud run deploy capacity-radar \
  ... \
  --max-instances=1 \
  --session-affinity \
  --timeout=3600
```

## 7. User-Supplied OAuth Tokens Expire After 60 Minutes During Long Hunts (`AUTH_MODE=user`)

### Challenge
The README notes that *"ADC tokens are automatically refreshed every 50 minutes during long scans."* However, inspecting `backend/hunter.py` (`ScanningSession._get_token`) shows:
```python
# User-supplied tokens cannot be refreshed server-side; use as-is.
if self._user_token:
    return self._user_token
```
When deployed in `AUTH_MODE=user` (token pasting or browser Google Sign-In), the backend only has a short-lived OAuth2 access token (~60 minutes TTL) and no refresh token. Any multi-hour scan (`totalHuntingHours > 1`) will fail with `401 Unauthorized` after 1 hour.

### Workaround
- **For scans < 1 hour**: `AUTH_MODE=user` works as expected; if a token expires, generate a fresh token via `gcloud auth application-default print-access-token` and start a new scan.
- **For multi-hour scans (> 1 hour)**:
  - Run Capacity Radar in **private server-credential mode (`AUTH_MODE=local`)** where the Cloud Run service account (which automatically refreshes tokens every 50 minutes) is granted `roles/compute.admin` (and `roles/tpu.admin`) on the target project, **or**
  - Run the CLI (`python3 skills/capacity-radar/scripts/capacity_radar.py deploy ...`) or local Docker container with mounted ADC (`~/.config/gcloud/application_default_credentials.json`).

## 8. Production `Dockerfile` Lacks `gcloud` CLI Used by Fallback Code Paths

### Challenge
`backend/advisory.py` defines CLI fallbacks (`_calendar_advisory_gcloud_fallback` calling `gcloud beta compute advice calendar-mode` and `_spot_advisory_gcloud_fallback` calling `gcloud alpha compute advice spot-resources`), and `backend/main.py` calls `gcloud config get-value account`.
However, Stage 2 of the `Dockerfile` uses `FROM python:3.12-slim` and never installs `google-cloud-cli`. If a REST API returns `404` and triggers the `gcloud` fallback inside Cloud Run or Docker, it fails with `FileNotFoundError: [Errno 2] No such file or directory: 'gcloud'`.

### Workaround
Ensure the target project has the Compute Engine REST APIs enabled so the primary `httpx` REST paths succeed without falling back to `gcloud`, or install `google-cloud-cli` in the `Dockerfile` if CLI fallbacks are needed.

## 9. `DEFAULT_PROJECT` Environment Variable Is Ignored When `DEMO_MODE=false`

### Challenge
In `backend/main.py` (`/api/config`), `DEFAULT_PROJECT` is only returned when `DEMO_MODE` is `true`:
```python
"project": DEFAULT_PROJECT if DEMO_MODE else "",
```
And `frontend/src/App.jsx` only reads `config.project` inside `if (config.demoMode)`. Setting `DEFAULT_PROJECT=<project>` with `DEMO_MODE=false` does not pre-populate the Project ID field in the UI.

### Workaround
Users must manually enter the target Project ID (e.g., `dx-supercomputer-testing` or `gpu-launchpad-playground`) in the left **Authentication** drawer before clicking **Connect**, or update `/api/config` and `App.jsx` to pre-fill `projectInput` from `DEFAULT_PROJECT` even when `DEMO_MODE=false` *(patched in this deployment)*.

## 10. `Authorization: Bearer` Header Collision with Cloud Run IAP and `gcloud run services proxy` (`AUTH_MODE=user`)

### Challenge
In `AUTH_MODE=user`, the upstream frontend (`AdvisoryPanel.jsx` and `ScanningPanel.jsx`) passes the user's pasted OAuth access token (`ya29...`) in the standard HTTP `Authorization: Bearer <token>` header on API requests (`/api/advisory/*`, `/api/networks*`), and `backend/main.py` (`_bearer_token`) reads `request.headers.get("authorization")`.

However, when `--allow-unauthenticated` is blocked by Domain Restricted Sharing (Challenge #3), both enterprise access methods intercept the `Authorization: Bearer` header at the proxy layer:
1. **Behind Cloud Run IAP (`--iap`)**: When the browser sends `Authorization: Bearer ya29...` on an API call, Google's Identity-Aware Proxy intercepts the `Authorization` header, assumes it is an IAP OIDC JWT instead of using the browser's `GCP_IAAP_AUTH_TOKEN` session cookie, and rejects the request before it ever reaches the container:
   ```bash
   HTTP 401: Invalid IAP credentials: Invalid bearer token. Invalid JWT audience.
   ```
2. **Behind `gcloud run services proxy`**: The local `gcloud` proxy overwrites the incoming `Authorization: Bearer ya29...` header with its own Cloud Run OIDC Identity Token (`eyJhbGciOiJSUzI1NiIs...`) to authenticate to Cloud Run ingress, stripping the user's OAuth access token before FastAPI receives the request.

### Workaround
Send the user's Google Cloud OAuth access token in a dedicated custom header (`X-User-Token`) from the frontend (`AdvisoryPanel.jsx` and `ScanningPanel.jsx`), and update `_bearer_token(request)` in `backend/main.py` to read `request.headers.get("x-user-token")` first (while ignoring OIDC JWTs starting with `eyJ` in `Authorization: Bearer`). *(Patched in this deployment.)*

## Customer FAQ & Architectural Deep-Dive

### Q1: When we deploy Capacity Radar into a new host project (e.g., `donmac-capacity-radar`), what credentials are used to scan a different target project?

**Short Answer:** It depends on the `AUTH_MODE` environment variable set on the Cloud Run service. Capacity Radar supports two distinct cross-project credential models:

| Feature / Dimension | Mode A: User-Credential Pass-Through (`AUTH_MODE=user`) *(Default Hosted Mode)* | Mode B: Server Service Account / ADC (`AUTH_MODE=local`) *(Dedicated Team Appliance)* |
| :--- | :--- | :--- |
| **Whose credentials call Compute/TPU APIs on the target project?** | **The logged-in user's own OAuth2 access token** (`ya29...` with `cloud-platform` scope), passed per request (`X-User-Token` / WebSocket `userToken`). | **The Cloud Run runtime Service Account** (`capacity-radar-web@<host-project>.iam.gserviceaccount.com`) via the Cloud Run Metadata Server (ADC). |
| **Host Service Account IAM Roles** | **Zero permissions** (`capacity-radar-web` has no IAM bindings on either the host project or any target project). | Must be explicitly granted `roles/compute.admin` (and `roles/tpu.admin` for TPUs) on **each target project** it needs to scan. |
| **Cross-Project Setup Required?** | **None.** Any user can immediately scan any project where their own Google account already has `roles/compute.admin` or `roles/editor`. | **Yes.** Target project admins must run `gcloud projects add-iam-policy-binding <target-project> --member="serviceAccount:capacity-radar-web@<host-project>.iam.gserviceaccount.com" --role="roles/compute.admin"`. |
| **Security & Blast Radius** | **Zero privilege escalation.** Server-side ADC is hard-disabled in code (`_require_user_token()`). Users can never use the host project's identity, and Cloud Audit Logs in the target project record the **individual user's email** as the caller. | **Shared privilege.** Anyone who can reach the web UI inherits the service account's permissions on all target projects where that SA has been granted access. Cloud Audit Logs show the service account as the caller. |
| **Max Scan / Hunting Duration** | **~60 minutes** (user OAuth2 access tokens cannot be refreshed server-side without a refresh token). | **Unlimited / Multi-day** (`ScanningSession._get_token()` in `backend/hunter.py` auto-refreshes the SA token every 50 minutes via ADC). |

#### How Mode A (`AUTH_MODE=user`) Works Under the Hood
1. **Runtime SA Isolation**: We deployed the Cloud Run service in `donmac-capacity-radar` with `--service-account capacity-radar-web@donmac-capacity-radar.iam.gserviceaccount.com`, which has **no IAM roles** on `donmac-capacity-radar` or any target project.
2. **Server-Side ADC Disabled**: In `backend/main.py` (`USER_AUTH_MODE = True`), `_require_user_token()` rejects any request that does not carry a user-supplied OAuth token (`HTTP 401`). The backend never falls back to `google.auth.default()`.
3. **Pass-Through Execution**:
   - The user provides their own OAuth2 token in the browser (either via **1-click Google Sign-In** if `OAUTH_CLIENT_ID` is configured, or by pasting the output of `gcloud auth application-default print-access-token`).
   - The token is kept only in browser `sessionStorage` (`cr_user_token`) and sent in-memory with each Advisory REST call (`X-User-Token` header) or Scan WebSocket message (`userToken` field in `WS /ws/scan`).
   - FastAPI forwards `Authorization: Bearer <user_token>` directly to `https://compute.googleapis.com/compute/.../projects/<target-project>/...`.
   - Google Cloud IAM evaluates the request against **the user's own IAM permissions** on `<target-project>` (e.g., `dx-supercomputer-testing` or `gpu-launchpad-playground`), and **the target project's quota and billing** are used for any reservations or VMs created.

#### When to Recommend Mode B (`AUTH_MODE=local`) to a Customer
If a customer wants **unattended, multi-hour or multi-day capacity hunting** (`totalHuntingHours > 1`) from the Cloud Run UI without their token expiring after 60 minutes, they should:
1. Set `AUTH_MODE=local` on the Cloud Run service.
2. Lock down Cloud Run ingress with **Cloud Run IAP (`--iap`)** so only authorized team members can access the UI.
3. Grant the host project's Cloud Run service account (`capacity-radar-web@<host-project>.iam.gserviceaccount.com`) `roles/compute.admin` (and `roles/tpu.admin`) on the specific target project(s) they want to scan.

### Q2: Why don't the Calendar Mode scans or advisories show up (or succeed) for TPUs?

There are **three distinct layers** to this behavior across the UI, the Advisory backend, and the Scan & Deploy backend:

#### 1. Why DWS Calendar Mode Is Hidden for TPU `v4`, `v3`, and `v2` (Expected Platform Behavior)
In `backend/gpu_data.py` (`TPU_TYPES`, lines 530–551), `"dws_calendar"` is set to `True` **only** for **`v6e`**, **`v5p`**, and **`v5e`**, and `False` for **`v4`**, **`v3`**, and **`v2`**.
Because both `AdvisoryPanel.jsx` (line 313) and `ScanningPanel.jsx` (line 556) default to *"Showing only supported methods & zones"* (`showUnsupported = false`), selecting TPU `v4`, `v3`, or `v2` hides the **DWS Calendar Mode** option entirely. This matches Google Cloud's actual platform support: DWS Calendar Mode only supports TPU **v5e, v5p, and v6e**.

#### 2. Why DWS Calendar Advisory Never Shows Zone Recommendations for TPU `v6e`, `v5p`, or `v5e` (Bug in `backend/advisory.py`)
When a user selects a supported TPU (`v6e`, `v5p`, or `v5e`) in the **Capacity Advisory** tab and clicks **Check Availability** or **Find Best Plan**, the UI never displays a table of recommended zones/dates. Instead, it displays an informational banner:
> *"The Calendar Advisory API does not support TPU machine types. However, Cloud TPU v6e (Trillium) (ct6e-standard-4t) fully supports DWS Calendar reservations — you can create them directly using the Scan & Deploy tab."*

* **Root Cause**:
  In `backend/advisory.py`, both `get_calendar_advisory()` (line 148) and `find_best_splits()` (line 206) call `_check_tpu()` (lines 68–112), which **unconditionally short-circuits and skips the API call for all TPUs**.
  The original author added this skip because `_query_calendar_advisory_region()` (lines 313–318) constructs a GPU-style `specificSkuResources` payload (`{"instanceCount": vm_count, "machineType": "ct6e-standard-4t"}`), which the Compute Engine API rejects for TPUs.
* **The Actual GCP API Behavior & Workaround**:
  Google Cloud's `advice/calendarMode` API (`POST https://compute.googleapis.com/compute/beta/projects/{project}/regions/{region}/advice/calendarMode`) **does** support TPU `v6e`, `v5p`, and `v5e`, but requires **`aggregateResources`** instead of `specificSkuResources` (along with `deploymentType: "DENSE"`):
  ```json
  {
    "futureResourcesSpecs": {
      "spec": {
        "deploymentType": "DENSE",
        "targetResources": {
          "aggregateResources": {
            "acceleratorCount": "16",
            "vmFamily": "VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT6E",
            "workloadType": "BATCH"
          }
        },
        "timeRangeSpec": {
          "minDuration": "86400s",
          "maxDuration": "604800s",
          "startTimeNotEarlierThan": "2026-10-18T00:00:00Z",
          "startTimeNotLaterThan": "2026-10-20T00:00:00Z"
        }
      }
    }
  }
  ```
  Where `vmFamily` maps as follows:
  * **TPU v6e**: `VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT6E`
  * **TPU v5p**: `VM_FAMILY_CLOUD_TPU_POD_SLICE_CT5P`
  * **TPU v5e**: `VM_FAMILY_CLOUD_TPU_LITE_POD_SLICE_CT5LP`

  Customers can also run the CLI equivalent directly:
  ```bash
  gcloud beta compute advice calendar-mode \
    --project=<project-id> \
    --region=us-east5 \
    --tpu-version=V6E \
    --chip-count=16 \
    --workload-type=BATCH \
    --duration-range=min=1d,max=7d \
    --start-time-range=from=2026-10-18T00:00:00Z,to=2026-10-20T00:00:00Z
  ```

#### 3. Why DWS Calendar Mode in "Scan & Deploy" Fails or Times Out for TPUs (Bugs in `backend/hunter.py`)
If a user goes to the **Scan & Deploy** tab and selects **DWS Calendar Mode** for TPU `v6e`, `v5p`, or `v5e`, `_try_tpu_dws_calendar()` (`backend/hunter.py`, lines 1380–1460) hits three issues:
1. **Uses TPU Queued Resources Instead of GCE Future Reservations**: `_try_tpu_dws_calendar()` posts to `tpu.googleapis.com/v2/.../queuedResources` with `"guaranteed": {"minDuration": ...}` and `"queueingPolicy": {"validUntilTime": end_str}` (omitting `calendar_start_time` from the queueing policy), whereas DWS Calendar Mode reservations for TPUs are provisioned via Compute Engine Future Reservations (`gcloud compute future-reservations create --tpu-version=v6e --chip-count=... --reservation-mode=CALENDAR`).
2. **Default Start Time Violates Mandatory Lead Time Rules**: When the user leaves `calendarStartTime` blank in the UI, `hunter.py` (lines 628 and 1392) defaults to `now + 1 hour`. However, DWS Calendar Mode enforces a **mandatory minimum lead time of 6 hours for TPUs** (and **87 hours / 3 days 15 hours for GPUs**). Any Calendar reservation requested inside that lead-time window is automatically rejected by the API.
3. **Polls for `ACTIVE` State on a Future Reservation**: `_try_tpu_dws_calendar()` calls `_poll_tpu_queued_resource()`, which waits for `state == "ACTIVE"`. Because Calendar Mode bookings start in the future (at least 6+ hours out), the resource cannot become `ACTIVE` during the 20-minute scan polling window, causing the scan to report *"not yet active — continuing to try other options"* and fail.

### Q3: Will Capacity Radar tell me that I need quota before scanning for resources?

**Short Answer: No.** Capacity Radar performs **zero pre-flight quota checks** before starting a scan, and it has three important behaviors/pitfalls around quota that customers should know:

#### 1. No Pre-Flight Quota Check in "Connect to Project" or "Capacity Advisory"
* **Step 2 (Connect to Project — `POST /api/auth/check` in `backend/main.py`, lines 254–263)**: Only calls `GET /compute/v1/projects/{project}` to check that the project exists and the Compute Engine API is enabled. It never inspects regional GPU/TPU quotas (`regions.get` or `serviceusage.googleapis.com`).
* **Capacity Advisory Tab (`backend/advisory.py`)**: The Spot, Flex-Start, and Calendar Mode Advisory APIs query **Google Cloud's regional physical fleet obtainability**, **not** your project's quota. A zone will show as `HIGH` availability or `RECOMMENDED` even if your project has `0` quota for that GPU/TPU in that region.

#### 2. Quota Errors Only Surface Mid-Scan (During Provisioning Attempts) — And Synchronous `403` Quota Errors Are Masked as "Permission Denied"
* You only discover missing quota **after** clicking **Start Scan & Deploy**, when `backend/hunter.py` actually attempts to create the reservation, VM, or TPU in each zone.
* **Bug in `_parse_api_error()` (`backend/hunter.py`, lines 1528–1535)**:
  ```python
  if code == 403:
      return f"Permission denied ({code}). Check IAM permissions."
  ...
  elif "quota" in ml:
      return f"Quota exceeded. ({message[:150]})"
  ```
  Because `if code == 403:` is evaluated **before** `elif "quota" in ml:`, any synchronous `HTTP 403` quota rejection from the Compute Engine or Cloud TPU API is **mislabeled in the scan log as `Permission denied (403). Check IAM permissions.`**, hiding the actual quota metric name!
  *(The user only sees the real `Quota '...' exceeded` text when Compute Engine accepts the initial `POST` with `HTTP 200` and fails asynchronously during operation polling in `_wait_for_operation()`, lines 262–266.)*

#### 3. No Fail-Fast on `0`-Quota Regions (Wasted Retry Cycles)
* When a zone fails due to insufficient quota (`QUOTA_EXCEEDED`), `_scan_with_method()` (`backend/hunter.py`, lines 490–521) treats it the same as a temporary physical stockout (`ZONE_RESOURCE_POOL_EXHAUSTED`).
* It **does not remove the quota-blocked zone** from subsequent retry rounds. If a customer sets `Retry Rounds = 20` and `Interval = 60s` across zones where they have `0` quota, Capacity Radar will spend 20 minutes repeatedly polling zones that can never succeed.

#### Workaround / Pre-Flight Quota Check for Customers
Before starting a scan in Capacity Radar, verify which regions have non-zero quota for the target accelerator family and consumption model (`PREEMPTIBLE` for Spot/Flex-Start vs. standard/committed for On-Demand/Calendar):
```bash
# Check regional GPU quotas (On-Demand & Preemptible/Spot/Flex):
gcloud compute regions list --project=<project-id> --format="json(name,quotas)" | python3 -c '
import json, sys
for r in json.load(sys.stdin):
    for q in r.get("quotas", []):
        if any(k in q["metric"] for k in ("A3", "A4", "H100", "H200", "B200", "G4", "L4", "A2", "GPU")):
            if q["limit"] > 0:
                print(f"{r[\"name\"]:20s} {q[\"metric\"]:45s} limit={int(q[\"limit\"])} usage={int(q[\"usage\"])}")
'
```
Then use the **Zones** dropdown in Capacity Radar to restrict the scan strictly to regions where `limit - usage >= requested_gpus`.
