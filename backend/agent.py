"""
Capacity Radar - Agentic Chat Assistant Backend
Powered by Vertex AI Gemini (gemini-3-flash-preview / gemini-3.1-pro-preview)
and the Capacity Radar Agent Skill (skills/capacity-radar/SKILL.md).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

import advisory
from gpu_data import (
    MACHINE_TYPES,
    TPU_TYPES,
    get_all_machine_types_info,
    get_chip_groups,
    get_zones_for_machine_type,
)
import hunter
from quotas import get_project_quotas

logger = logging.getLogger(__name__)

# Supported Gemini 3+ models on Vertex AI global endpoint
AVAILABLE_MODELS = [
    {
        "id": "gemini-3-flash-preview",
        "label": "Gemini 3 Flash (Fast)",
        "description": "Low-latency agentic tool execution & UI driving",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro (Deep Reasoning)",
        "description": "Complex multi-region capacity planning & quota analysis",
    },
]
DEFAULT_MODEL = os.environ.get("RADAR_AGENT_MODEL", "gemini-3-flash-preview")
FALLBACK_VERTEX_PROJECT = os.environ.get(
    "VERTEX_PROJECT",
    os.environ.get("DEFAULT_PROJECT", "donmac-capacity-radar") or "donmac-capacity-radar",
)


def _load_skill_markdown() -> str:
    """Load skills/capacity-radar/SKILL.md from repo root or container path."""
    candidates = [
        Path(__file__).resolve().parent.parent / "skills" / "capacity-radar" / "SKILL.md",
        Path("/app/skills/capacity-radar/SKILL.md"),
    ]
    for p in candidates:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read {p}: {e}")
    return ""


SKILL_MARKDOWN = _load_skill_markdown()


def _build_system_instruction(project: str, ui_context: Optional[dict]) -> str:
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    context_json = json.dumps(ui_context or {}, indent=2, default=str)
    if len(context_json) > 16000:
        context_json = context_json[:16000] + "\n... (truncated)"

    return f"""You are the **Capacity Radar AI Assistant**, an expert agent embedded directly inside the Capacity Radar web application.
You sit in a collapsible panel at the top of the screen, directly above three interactive tabs:
1. **Capacity Advisory** (`tab = "advisory"`): Checks DWS Calendar Mode availability/split plans, Spot VM availability/preemption scores, and DWS Flex Start wait-time estimates.
2. **Scan & Deploy** (`tab = "scan"`): Configures and runs priority-based capacity hunting across On-Demand Reservations (`on_demand`), DWS Calendar Mode (`dws_calendar`), DWS Flex Start (`dws_flex`), and Spot VMs (`spot`).
3. **Quota** (`tab = "quota"`): Displays the project's live GPU, TPU, and VM quotas across all regions and zones, organized by chip, VM family, and consumption model, with direct links to the GCP Quota Console.

Current Date (UTC): {today} (Tomorrow: {tomorrow})
Connected GCP Project: `{project or "(not connected)"}`

---
### Core Skill Instructions (from `skills/capacity-radar/SKILL.md`)
{SKILL_MARKDOWN}

---
### How You Operate Inside the Web App
Unlike a CLI-only agent, you have direct Python function tools that both **query live GCP APIs** AND **drive the web UI tabs below you in real time**:
- **Answer Questions About Visible Results**: The user can ask about what they are currently seeing on the tabs below. Inspect the `LIVE SCREEN CONTEXT` below first! If the data needed to answer their question is already in `LIVE SCREEN CONTEXT`, explain or compare it clearly without making redundant API calls.
- **Run Advisories & Update the UI**: When the user asks to find capacity or check Spot / Calendar / Flex availability for a machine type or chip, call `advise_spot`, `advise_calendar`, or `advise_flex` with `update_ui=true`. This automatically switches to the **Capacity Advisory** tab and populates the visual cards below your chat panel!
- **Check & Filter Quotas**: When the user asks which quota they need, or whether their project has enough quota for a GPU/TPU resource, call `check_project_quotas` (and optionally set `update_ui=true` or call `drive_ui_filter_quotas` to show those quotas on the **Quota** tab).
- **Drive Scan & Deploy**:
  - When the user asks you to set up a scan/deployment or help them acquire capacity, call `drive_ui_configure_scan` to switch to the **Scan & Deploy** tab and pre-populate the machine type, VM count, and multi-priority list (`on_demand`, `dws_calendar`, `dws_flex`, `spot`).
  - **SAFETY RULE**: Always set `auto_start=false` when first configuring a scan so the user can review the pre-filled priorities on the **Scan & Deploy** tab and either click **Start Hunting** or reply *"Yes, start the scan"*. Only set `auto_start=true` if the user has explicitly confirmed they want to launch the scan right now (as this creates real billable GCP resources).
- **Formatting**: Keep responses concise, well-structured in GitHub-flavored Markdown, use bullet points or compact tables when comparing zones/quotas, and mention when you've updated the tab below.

---
### LIVE SCREEN CONTEXT (What the user currently sees on the tabs below)
```json
{context_json}
```
"""


TOOL_DECLARATIONS = [
    {
        "name": "catalog_chips_and_machines",
        "description": (
            "List supported GPU and TPU chips, machine types, accelerator counts, "
            "supported consumption models (on_demand, dws_calendar, dws_flex, spot), "
            "and available GCP zones."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Optional filter: 'GPU' or 'TPU'. Leave empty for both.",
                },
                "chip_filter": {
                    "type": "STRING",
                    "description": "Optional substring filter on chip or machine type (e.g. 'H100', 'B200', 'v6e', 'a3-highgpu-8g').",
                },
            },
        },
    },
    {
        "name": "advise_spot",
        "description": (
            "Run the Spot VM Capacity Advisory for a GPU machine type to get live Spot "
            "availability and preemption risk scores across zones. Also updates the "
            "Capacity Advisory tab in the UI when update_ui is true."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine_type": {
                    "type": "STRING",
                    "description": "Exact GCE machine type (e.g. 'a3-highgpu-8g', 'g2-standard-4', 'a2-highgpu-1g').",
                },
                "regions": {
                    "type": "STRING",
                    "description": "Optional comma-separated region filter (e.g. 'us-central1,us-east4').",
                },
                "zones": {
                    "type": "STRING",
                    "description": "Optional comma-separated zone filter (e.g. 'us-central1-a,us-central1-b').",
                },
                "update_ui": {
                    "type": "BOOLEAN",
                    "description": "If true (default), switches to the Capacity Advisory tab and displays the Spot results.",
                },
            },
            "required": ["machine_type"],
        },
    },
    {
        "name": "advise_calendar",
        "description": (
            "Run the DWS Calendar Mode Advisory (futureReservation.advice) for a GPU or TPU "
            "machine type to find available reservation windows across zones. Set find_splits=true "
            "when requesting multiple VMs to also evaluate multi-zone split plans. Updates the "
            "Capacity Advisory tab in the UI when update_ui is true."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine_type": {
                    "type": "STRING",
                    "description": "Exact GPU or TPU machine type (e.g. 'a3-highgpu-8g', 'a3-megagpu-8g', 'ct6e-standard-4t').",
                },
                "vm_count": {
                    "type": "INTEGER",
                    "description": "Number of VMs / TPU slices requested (default 1).",
                },
                "start_date": {
                    "type": "STRING",
                    "description": "Target start date in YYYY-MM-DD format (defaults to tomorrow).",
                },
                "duration_days": {
                    "type": "INTEGER",
                    "description": "Reservation duration in days (1 to 90, default 7).",
                },
                "flexibility_days": {
                    "type": "INTEGER",
                    "description": "Additional search window flexibility in days beyond start_date + duration_days (default 3).",
                },
                "find_splits": {
                    "type": "BOOLEAN",
                    "description": "If true, runs the Best Split Plan solver across zones.",
                },
                "regions": {
                    "type": "STRING",
                    "description": "Optional comma-separated regions.",
                },
                "zones": {
                    "type": "STRING",
                    "description": "Optional comma-separated zones.",
                },
                "update_ui": {
                    "type": "BOOLEAN",
                    "description": "If true (default), updates the Capacity Advisory tab below with these results.",
                },
            },
            "required": ["machine_type"],
        },
    },
    {
        "name": "advise_flex",
        "description": (
            "Run the DWS Flex Start Advisory for a GPU machine type to estimate queue wait "
            "times across zones. Updates the Capacity Advisory tab when update_ui is true."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine_type": {
                    "type": "STRING",
                    "description": "Exact GPU machine type (e.g. 'a3-highgpu-8g', 'g2-standard-4').",
                },
                "size": {
                    "type": "INTEGER",
                    "description": "Number of instances requested (default 1).",
                },
                "max_run_hours": {
                    "type": "INTEGER",
                    "description": "Maximum run duration in hours (1 to 168, default 24).",
                },
                "regions": {
                    "type": "STRING",
                    "description": "Optional comma-separated regions.",
                },
                "zones": {
                    "type": "STRING",
                    "description": "Optional comma-separated zones.",
                },
                "update_ui": {
                    "type": "BOOLEAN",
                    "description": "If true (default), updates the Capacity Advisory tab below with these results.",
                },
            },
            "required": ["machine_type"],
        },
    },
    {
        "name": "check_project_quotas",
        "description": (
            "Query the connected project's live GPU, TPU, and VM quotas across all regions/zones, "
            "filtered by category, chip, VM family, consumption model, or location. Optionally "
            "switches to and filters the Quota tab in the UI."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Filter by 'ALL', 'GPU', or 'TPU' (default 'ALL').",
                },
                "chip": {
                    "type": "STRING",
                    "description": "Optional chip substring filter (e.g. 'H100', 'B200', 'L4', 'v6e', 'v5p').",
                },
                "vm_family": {
                    "type": "STRING",
                    "description": "Optional VM family filter (e.g. 'A3', 'A4', 'G2', 'N1', 'TPU-v6e').",
                },
                "consumption_model": {
                    "type": "STRING",
                    "description": "Optional consumption model filter: 'on_demand', 'spot', 'dws_flex', 'dws_calendar', or 'committed'.",
                },
                "location": {
                    "type": "STRING",
                    "description": "Optional region or zone substring filter (e.g. 'us-central1', 'europe-west4').",
                },
                "include_vm_quotas": {
                    "type": "BOOLEAN",
                    "description": "Whether to include related host/CPU/QueuedResource quotas (default true).",
                },
                "only_non_zero": {
                    "type": "BOOLEAN",
                    "description": "If true, only return quotas where the project has a limit > 0.",
                },
                "update_ui": {
                    "type": "BOOLEAN",
                    "description": "If true, switches the UI to the Quota tab and applies these filters.",
                },
            },
        },
    },
    {
        "name": "list_project_networks",
        "description": (
            "List VPC networks and regional subnetworks available in the project for Scan & Deploy."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "region": {
                    "type": "STRING",
                    "description": "Optional GCP region (e.g. 'us-central1') to also list subnetworks.",
                },
            },
        },
    },
    {
        "name": "drive_ui_configure_scan",
        "description": (
            "Switch to the 'Scan & Deploy' tab and configure a single- or multi-priority "
            "capacity hunting plan. By default (auto_start=false), pre-fills the form so the "
            "user can review and click Start Hunting. Set auto_start=true ONLY when the user "
            "has explicitly confirmed they want to launch the scan and create billable resources."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine_type": {
                    "type": "STRING",
                    "description": "Exact GPU or TPU machine type (e.g. 'a3-highgpu-8g', 'ct6e-standard-4t').",
                },
                "vm_count": {
                    "type": "INTEGER",
                    "description": "Number of VMs / TPU slices to deploy (default 1).",
                },
                "execution_mode": {
                    "type": "STRING",
                    "description": "'sequential' (try Priority 1 then Priority 2) or 'parallel' (race all priorities at once). Default 'sequential'.",
                },
                "priorities": {
                    "type": "ARRAY",
                    "description": "Ordered list of hunting priorities to configure on the Scan & Deploy tab.",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "method": {
                                "type": "STRING",
                                "description": "One of: 'on_demand', 'dws_calendar', 'dws_flex', 'spot'.",
                            },
                            "zones": {
                                "type": "ARRAY",
                                "items": {"type": "STRING"},
                                "description": "Target zones for this priority (empty = all supported zones).",
                            },
                            "max_retries": {
                                "type": "INTEGER",
                                "description": "Max retry attempts per zone (default 5).",
                            },
                            "retry_interval": {
                                "type": "INTEGER",
                                "description": "Seconds between retries (default 60).",
                            },
                            "calendar_start_time": {
                                "type": "STRING",
                                "description": "ISO UTC start time for dws_calendar (e.g. '2026-10-25T00:00:00Z').",
                            },
                            "calendar_end_time": {
                                "type": "STRING",
                                "description": "ISO UTC end time for dws_calendar (e.g. '2026-11-01T00:00:00Z').",
                            },
                            "flex_usage_hours": {
                                "type": "INTEGER",
                                "description": "Run duration hours for dws_flex (default 24).",
                            },
                        },
                        "required": ["method"],
                    },
                },
                "auto_start": {
                    "type": "BOOLEAN",
                    "description": "ONLY set true if the user has explicitly confirmed starting the real deployment scan now.",
                },
            },
            "required": ["machine_type", "priorities"],
        },
    },
    {
        "name": "drive_ui_filter_quotas",
        "description": (
            "Switch to the 'Quota' tab and apply filters so the user sees matching quotas on screen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "'ALL', 'GPU', or 'TPU'.",
                },
                "chips": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Exact chip names to select (e.g. ['NVIDIA H100 80GB', 'NVIDIA B200']).",
                },
                "families": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "VM families to filter by (e.g. ['A3', 'G2']).",
                },
                "models": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Consumption models: 'on_demand', 'spot', 'dws_flex', 'dws_calendar', 'committed'.",
                },
                "location": {
                    "type": "STRING",
                    "description": "Region or zone substring (e.g. 'us-central1').",
                },
                "search": {
                    "type": "STRING",
                    "description": "Free-text search query for metric/quota name.",
                },
                "show_vm_quotas": {
                    "type": "BOOLEAN",
                    "description": "Toggle related VM / CPU / QueuedResource quotas.",
                },
                "only_non_zero": {
                    "type": "BOOLEAN",
                    "description": "Toggle showing only quotas with limit > 0.",
                },
            },
        },
    },
    {
        "name": "drive_ui_tab",
        "description": "Switch the active tab below the chat ('advisory', 'scan', or 'quota').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "tab": {
                    "type": "STRING",
                    "description": "Target tab: 'advisory', 'scan', or 'quota'.",
                },
            },
            "required": ["tab"],
        },
    },
]


def _lookup_machine_meta(machine_type: str) -> dict:
    for mt in get_all_machine_types_info():
        if mt["machineType"] == machine_type:
            return mt
    return {"category": "TPU" if machine_type in TPU_TYPES else "GPU", "chip": "", "machineType": machine_type}


async def _execute_tool(
    name: str,
    args: dict[str, Any],
    project: str,
    user_token: Optional[str],
    ui_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute a tool call and optionally append UI-driving actions."""
    try:
        if name == "catalog_chips_and_machines":
            cat_filter = (args.get("category") or "").strip().upper()
            chip_filter = (args.get("chip_filter") or "").strip().lower()
            all_mts = get_all_machine_types_info()
            filtered = []
            for mt in all_mts:
                if cat_filter in ("GPU", "TPU") and mt.get("category") != cat_filter:
                    continue
                if chip_filter:
                    haystack = f"{mt.get('machineType','')} {mt.get('chip','')} {mt.get('gpuModel','')}".lower()
                    if chip_filter not in haystack:
                        continue
                filtered.append({
                    "machineType": mt["machineType"],
                    "category": mt.get("category", "GPU"),
                    "chip": mt.get("chip"),
                    "acceleratorCount": mt.get("gpuCount"),
                    "supportedConsumption": mt.get("supportedConsumption"),
                    "zoneCount": mt.get("zoneCount", len(mt.get("zones", []))),
                    "zones": mt.get("zones", []),
                })
            return {
                "ok": True,
                "count": len(filtered),
                "machineTypes": filtered,
                "chipGroups": get_chip_groups() if not chip_filter else None,
            }

        elif name == "advise_spot":
            if not project:
                return {"ok": False, "error": "No GCP project connected. Ask the user to connect a project first."}
            mt = args["machine_type"]
            regions = [r.strip() for r in (args.get("regions") or "").split(",") if r.strip()] or None
            zones = [z.strip() for z in (args.get("zones") or "").split(",") if z.strip()] or None
            if not zones and not regions:
                all_z = get_zones_for_machine_type(mt)
                zones = all_z or None
                regions = sorted({z.rsplit("-", 1)[0] for z in all_z}) if all_z else None

            res = await advisory.get_spot_advisory(
                project=project,
                machine_type=mt,
                regions=regions,
                zones=zones,
                user_token=user_token,
            )
            if args.get("update_ui", True):
                meta = _lookup_machine_meta(mt)
                ui_actions.append({
                    "type": "update_advisory",
                    "tab": 0,
                    "category": meta.get("category", "GPU"),
                    "chip": meta.get("chip", ""),
                    "machineType": mt,
                    "spotResults": res,
                })
            return {"ok": True, **res}

        elif name == "advise_calendar":
            if not project:
                return {"ok": False, "error": "No GCP project connected. Ask the user to connect a project first."}
            mt = args["machine_type"]
            vm_count = int(args.get("vm_count") or 1)
            tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            start_date = (args.get("start_date") or "").strip() or tomorrow
            duration_days = int(args.get("duration_days") or 7)
            flexibility_days = int(args.get("flexibility_days") if args.get("flexibility_days") is not None else 3)
            find_splits = bool(args.get("find_splits", False))

            regions = [r.strip() for r in (args.get("regions") or "").split(",") if r.strip()] or None
            zones = [z.strip() for z in (args.get("zones") or "").split(",") if z.strip()] or None
            if not zones and not regions:
                all_z = get_zones_for_machine_type(mt)
                zones = all_z or None
                regions = sorted({z.rsplit("-", 1)[0] for z in all_z}) if all_z else None

            if find_splits:
                res = await advisory.find_best_splits(
                    project=project,
                    machine_type=mt,
                    vm_count=vm_count,
                    start_date=start_date,
                    flexibility_days=flexibility_days,
                    duration_days=duration_days,
                    regions=regions,
                    zones=zones,
                    user_token=user_token,
                )
            else:
                res = await advisory.get_calendar_advisory(
                    project=project,
                    machine_type=mt,
                    vm_count=vm_count,
                    start_date=start_date,
                    flexibility_days=flexibility_days,
                    duration_days=duration_days,
                    regions=regions,
                    zones=zones,
                    user_token=user_token,
                )

            if args.get("update_ui", True):
                meta = _lookup_machine_meta(mt)
                action = {
                    "type": "update_advisory",
                    "tab": 0,
                    "category": meta.get("category", "GPU"),
                    "chip": meta.get("chip", ""),
                    "machineType": mt,
                    "vmCount": vm_count,
                    "startDate": start_date,
                    "durationDays": duration_days,
                    "flexibilityDays": flexibility_days,
                    "calendarQueryInfo": {
                        "mode": "splits" if find_splits else "check",
                        "startDate": start_date,
                        "flexibilityDays": flexibility_days,
                        "durationDays": duration_days,
                        "vmCount": vm_count,
                    },
                }
                if find_splits:
                    action["splitsResults"] = res
                else:
                    action["calendarResults"] = res
                ui_actions.append(action)

            return {"ok": True, "mode": "splits" if find_splits else "calendar", **res}

        elif name == "advise_flex":
            if not project:
                return {"ok": False, "error": "No GCP project connected. Ask the user to connect a project first."}
            mt = args["machine_type"]
            size = int(args.get("size") or 1)
            max_run_hours = int(args.get("max_run_hours") or 24)
            regions = [r.strip() for r in (args.get("regions") or "").split(",") if r.strip()] or None
            zones = [z.strip() for z in (args.get("zones") or "").split(",") if z.strip()] or None
            if not zones and not regions:
                all_z = get_zones_for_machine_type(mt)
                zones = all_z or None
                regions = sorted({z.rsplit("-", 1)[0] for z in all_z}) if all_z else None

            res = await advisory.get_flex_advisory(
                project=project,
                machine_type=mt,
                size=size,
                max_run_duration_hours=max_run_hours,
                regions=regions,
                zones=zones,
                user_token=user_token,
            )
            if args.get("update_ui", True):
                meta = _lookup_machine_meta(mt)
                ui_actions.append({
                    "type": "update_advisory",
                    "tab": 0,
                    "category": meta.get("category", "GPU"),
                    "chip": meta.get("chip", ""),
                    "machineType": mt,
                    "flexSize": size,
                    "flexMaxRunHours": max_run_hours,
                    "flexResults": res,
                })
            return {"ok": True, **res}

        elif name == "check_project_quotas":
            if not project:
                return {"ok": False, "error": "No GCP project connected."}
            data = await get_project_quotas(project, user_token=user_token, refresh=False)
            cat = (args.get("category") or "ALL").strip().upper()
            chip_q = (args.get("chip") or "").strip().lower()
            fam_q = (args.get("vm_family") or "").strip().lower()
            mod_q = (args.get("consumption_model") or "").strip().lower()
            loc_q = (args.get("location") or "").strip().lower()
            inc_vm = bool(args.get("include_vm_quotas", True))
            only_nz = bool(args.get("only_non_zero", False))

            matched = []
            for q in data.get("quotas", []):
                if cat in ("GPU", "TPU") and q.get("category") != cat:
                    continue
                if not inc_vm and q.get("isVmQuota"):
                    continue
                if chip_q and chip_q not in (q.get("chip") or "").lower() and chip_q not in (q.get("displayName") or "").lower():
                    continue
                if fam_q and not any(fam_q in f.lower() for f in (q.get("vmFamilies") or [])):
                    continue
                if mod_q and mod_q not in [m.lower() for m in (q.get("consumptionModels") or [])]:
                    continue

                locs = q.get("locations") or []
                if loc_q:
                    locs = [l for l in locs if loc_q in (l.get("location") or "").lower()]
                non_zero_locs = [l for l in locs if (l.get("limit") or 0) > 0 or (l.get("usage") or 0) > 0]
                if only_nz and not non_zero_locs and (q.get("maxLimit") or 0) <= 0:
                    continue

                matched.append({
                    "id": q.get("id"),
                    "displayName": q.get("displayName"),
                    "category": q.get("category"),
                    "chip": q.get("chip"),
                    "vmFamilies": q.get("vmFamilies"),
                    "consumptionModels": q.get("consumptionModels"),
                    "unit": q.get("unit"),
                    "scope": q.get("scope"),
                    "isVmQuota": q.get("isVmQuota"),
                    "maxLimit": q.get("maxLimit"),
                    "totalNonZeroLocations": q.get("totalNonZeroLocations"),
                    "nonZeroLocations": non_zero_locs[:15],
                    "matchingLocations": locs[:10] if loc_q else [],
                    "consoleUrl": q.get("consoleUrl"),
                    "notes": q.get("notes"),
                })

            if args.get("update_ui", False):
                ui_actions.append({
                    "type": "filter_quotas",
                    "tab": 2,
                    "category": cat if cat in ("ALL", "GPU", "TPU") else "ALL",
                    "search": args.get("chip") or "",
                    "families": [args["vm_family"]] if args.get("vm_family") else [],
                    "models": [args["consumption_model"]] if args.get("consumption_model") else [],
                    "location": args.get("location") or "",
                    "showVmQuotas": inc_vm,
                    "onlyNonZero": only_nz,
                })

            return {
                "ok": True,
                "matchedCount": len(matched),
                "quotas": matched[:25],
                "apiWarnings": data.get("errors") or [],
            }

        elif name == "list_project_networks":
            if not project:
                return {"ok": False, "error": "No GCP project connected."}
            tok = user_token or await hunter.get_access_token()
            res = await hunter.list_networks(project=project, token=tok, region=args.get("region") or "")
            return {"ok": True, **res}

        elif name == "drive_ui_configure_scan":
            mt = args["machine_type"]
            meta = _lookup_machine_meta(mt)
            vm_count = max(1, int(args.get("vm_count") or 1))
            exec_mode = args.get("execution_mode") if args.get("execution_mode") in ("sequential", "parallel") else "sequential"
            raw_priorities = args.get("priorities") or []
            norm_priorities = []
            for p in raw_priorities:
                method = p.get("method", "spot")
                zones = p.get("zones") or []
                norm_priorities.append({
                    "method": method,
                    "zones": zones,
                    "max_retries": int(p.get("max_retries") or 5),
                    "retry_interval": int(p.get("retry_interval") or 60),
                    "calendarStartTime": p.get("calendar_start_time") or "",
                    "calendarEndTime": p.get("calendar_end_time") or "",
                    "flexUsageDurationHours": int(p.get("flex_usage_hours") or 24),
                })
            if not norm_priorities:
                norm_priorities.append({
                    "method": "spot",
                    "zones": [],
                    "max_retries": 5,
                    "retry_interval": 60,
                })

            first = norm_priorities[0]
            auto_start = bool(args.get("auto_start", False))
            preset = {
                "category": meta.get("category", "GPU"),
                "chip": meta.get("chip", ""),
                "machineType": mt,
                "vmCount": vm_count,
                "executionMode": exec_mode,
                "method": first["method"],
                "zones": first["zones"],
                "startTime": first.get("calendarStartTime") or "",
                "endTime": first.get("calendarEndTime") or "",
                "flexUsageDurationHours": first.get("flexUsageDurationHours") or 24,
                "priorities": norm_priorities,
                "sourceLabel": "AI Capacity Assistant",
                "autoStart": auto_start,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            ui_actions.append({
                "type": "configure_scan",
                "tab": 1,
                "deployPreset": preset,
            })
            return {
                "ok": True,
                "message": (
                    "Switched to Scan & Deploy tab, configured the priority plan, and started the scan."
                    if auto_start
                    else "Switched to Scan & Deploy tab and pre-populated the priority plan. Ask the user to review and click 'Start Hunting' (or confirm in chat to start)."
                ),
                "configuredPlan": preset,
            }

        elif name == "drive_ui_filter_quotas":
            action = {
                "type": "filter_quotas",
                "tab": 2,
                "category": (args.get("category") or "ALL").upper(),
                "chips": args.get("chips") or [],
                "families": args.get("families") or [],
                "models": args.get("models") or [],
                "location": args.get("location") or "",
                "search": args.get("search") or "",
                "showVmQuotas": args.get("show_vm_quotas"),
                "onlyNonZero": args.get("only_non_zero"),
            }
            ui_actions.append(action)
            return {"ok": True, "message": "Switched to Quota tab and applied filters.", "filters": action}

        elif name == "drive_ui_tab":
            tab_map = {"advisory": 0, "scan": 1, "quota": 2}
            tab_idx = tab_map.get((args.get("tab") or "").lower(), 0)
            ui_actions.append({"type": "switch_tab", "tab": tab_idx})
            return {"ok": True, "switchedToTab": args.get("tab")}

        else:
            return {"ok": False, "error": f"Unknown tool: {name}"}
    except Exception as e:
        logger.exception(f"Error executing agent tool {name}")
        return {"ok": False, "error": str(e)}


async def _call_vertex_gemini(
    contents: list[dict[str, Any]],
    system_instruction: str,
    model: str,
    project: str,
    user_token: Optional[str],
) -> dict[str, Any]:
    """Call Vertex AI Gemini (global endpoint) with automatic credential/project fallback."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    payload = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "tools": [{"functionDeclarations": TOOL_DECLARATIONS}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        if api_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"GEMINI_API_KEY call returned {resp.status_code}: {resp.text[:200]}")

        # Build candidate (token, vertex_project) pairs in priority order
        attempts: list[tuple[str, str]] = []
        server_token: Optional[str] = None
        try:
            server_token = await hunter.get_access_token()
        except Exception:
            server_token = None

        if user_token and project:
            attempts.append((user_token, project))
        if user_token and FALLBACK_VERTEX_PROJECT and FALLBACK_VERTEX_PROJECT != project:
            attempts.append((user_token, FALLBACK_VERTEX_PROJECT))
        if server_token and FALLBACK_VERTEX_PROJECT:
            attempts.append((server_token, FALLBACK_VERTEX_PROJECT))
        if server_token and project and project != FALLBACK_VERTEX_PROJECT:
            attempts.append((server_token, project))

        if not attempts:
            raise RuntimeError(
                "No Google Cloud credentials available to call Vertex AI Gemini. "
                "Please sign in or provide an access token."
            )

        last_err = ""
        models_to_try = [model]
        if model != "gemini-3-flash-preview":
            models_to_try.append("gemini-3-flash-preview")

        for m in models_to_try:
            for tok, v_proj in attempts:
                url = (
                    f"https://aiplatform.googleapis.com/v1beta1/projects/{v_proj}"
                    f"/locations/global/publishers/google/models/{m}:generateContent"
                )
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {tok}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if resp.status_code == 200:
                    return resp.json()
                last_err = f"HTTP {resp.status_code} ({v_proj}/{m}): {resp.text[:300]}"
                logger.warning(f"Vertex AI attempt failed: {last_err}")

        raise RuntimeError(f"Vertex AI Gemini call failed: {last_err}")


async def run_agent_chat(
    project: str,
    messages: list[dict[str, Any]],
    ui_context: Optional[dict[str, Any]] = None,
    user_token: Optional[str] = None,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Run a multi-turn agentic conversation with tool execution and UI action generation."""
    chosen_model = model or DEFAULT_MODEL
    valid_ids = {m["id"] for m in AVAILABLE_MODELS}
    if chosen_model not in valid_ids:
        chosen_model = DEFAULT_MODEL

    system_instruction = _build_system_instruction(project, ui_context)

    # Convert frontend chat messages [{role: 'user'|'assistant', content: '...'}] into Gemini contents
    contents: list[dict[str, Any]] = []
    for msg in messages[-20:]:
        role = "model" if msg.get("role") in ("assistant", "model") else "user"
        text = (msg.get("content") or "").strip()
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})

    if not contents:
        return {
            "reply": "Ask me anything about the results on screen, or tell me what GPU/TPU capacity you need!",
            "toolCalls": [],
            "uiActions": [],
            "model": chosen_model,
        }

    ui_actions: list[dict[str, Any]] = []
    tool_traces: list[dict[str, Any]] = []

    max_rounds = 5
    for _round in range(max_rounds):
        resp_data = await _call_vertex_gemini(
            contents=contents,
            system_instruction=system_instruction,
            model=chosen_model,
            project=project,
            user_token=user_token,
        )
        candidates = resp_data.get("candidates") or []
        if not candidates:
            break

        model_content = candidates[0].get("content") or {"role": "model", "parts": []}
        parts = model_content.get("parts") or []

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not function_calls:
            # Final text answer
            reply_texts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought")]
            reply = "\n".join(t for t in reply_texts if t).strip()
            if not reply:
                reply = "I've updated the interface below with the requested information."
            return {
                "reply": reply,
                "toolCalls": tool_traces,
                "uiActions": ui_actions,
                "model": chosen_model,
            }

        # Preserve exact model_content (including thoughtSignature required by Gemini 3)
        contents.append(model_content)

        response_parts = []
        for fc in function_calls:
            fn_name = fc.get("name", "")
            fn_args = fc.get("args") or {}
            tool_res = await _execute_tool(
                name=fn_name,
                args=fn_args,
                project=project,
                user_token=user_token,
                ui_actions=ui_actions,
            )
            tool_traces.append({
                "name": fn_name,
                "args": fn_args,
                "ok": bool(tool_res.get("ok", True)),
            })
            response_parts.append({
                "functionResponse": {
                    "name": fn_name,
                    "response": tool_res,
                }
            })

        contents.append({"role": "user", "parts": response_parts})

    # If max tool rounds reached, extract any text or return summary
    return {
        "reply": "I've completed running the capacity checks and updated the view below.",
        "toolCalls": tool_traces,
        "uiActions": ui_actions,
        "model": chosen_model,
    }
