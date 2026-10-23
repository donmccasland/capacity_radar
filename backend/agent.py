"""
Capacity Radar - Agentic Chat Assistant Backend
Powered by Vertex AI Gemini (gemini-3-flash-preview / gemini-3.1-pro-preview)
and the Capacity Radar Agent Skill (skills/capacity-radar/SKILL.md).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from pathlib import Path
import re
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
### Critical Rules for Chip Resolution & Running Scans on the Advisory Panel
1. **Exact Chip Matching (Never Confuse B200 with GB200/GB300)**:
   - When the user asks for **B200**, use `a4-highgpu-8g` (`chip = "B200"`, A4 family). **NEVER substitute `GB200` (`a4x-highgpu-4g`) or `GB300` (`a4x-maxgpu-4g-metal`) when the user asks for `B200`!**
   - Only check `GB200` (`a4x-highgpu-4g`) if the user explicitly writes `"GB200"` or `"A4X"`.
2. **Always Execute the Advisory Scans (`advise_all`) When Asked to Find Capacity**:
   - When the user asks to find capacity for a GPU or TPU (especially open-ended requests like *"help me find any kind of B200 based GPUs on this project, any region, anytime before ..."*), **call `advise_all(machine_type=..., update_ui=true)`**!
   - `advise_all` runs **DWS Calendar Mode + Spot VM + DWS Flex Start** advisories in parallel and populates ALL THREE result tables on the **Capacity Advisory** tab below your chat panel at the same time (just as if the user clicked all the advisory scan buttons).
   - If the user also wants to know whether their project has quota for that chip, you can also call `check_project_quotas(chip=..., update_ui=false)` (keep `update_ui=false` when running an advisory so you don't switch the user away from the **Capacity Advisory** tab!).
3. **Date Horizons for DWS Calendar**:
   - In `advise_all` and `advise_calendar`, `duration_days` is the maximum slot length (default `7` days; the API automatically searches for any `1` to `duration_days` slot!), and `flexibility_days` is how many days forward from `start_date` to search (supports up to `365` days!). If the user gives a far-future deadline (e.g. *"anytime before 01/01/27"* or *"all the way out to the end of the year"*), calculate the days until that deadline (up to `365`) and pass it as `flexibility_days`, while keeping `duration_days=7` (unless they asked for a specific reservation duration).
4. **Answer Questions About Visible Results**:
   - Inspect `LIVE SCREEN CONTEXT` below first when the user asks about results already on screen.
5. **Drive Scan & Deploy (`drive_ui_configure_scan`)**:
   - When the user asks you to set up a scan/deployment or wants to proceed with deploying one of the recommended zones, call `drive_ui_configure_scan` (`auto_start=false` by default until the user confirms starting billable resources).

---
### LIVE SCREEN CONTEXT (What the user currently sees on the tabs below)
```json
{context_json}
```
"""


TOOL_DECLARATIONS = [
    {
        "name": "advise_all",
        "description": (
            "Run ALL supported capacity advisories (DWS Calendar Mode, Spot VM, and DWS Flex Start) "
            "in parallel for a machine type and populate all result panels on the Capacity Advisory tab "
            "below. Prefer this tool whenever the user asks to find capacity for a GPU or TPU without "
            "restricting to a single consumption model."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "machine_type": {
                    "type": "STRING",
                    "description": "Exact GPU or TPU machine type (e.g. 'a4-highgpu-8g' for B200, 'a3-highgpu-8g' for H100, 'g2-standard-4' for L4, 'ct6e-standard-4t' for TPU v6e).",
                },
                "vm_count": {
                    "type": "INTEGER",
                    "description": "Number of VMs / TPU slices requested (default 1).",
                },
                "start_date": {
                    "type": "STRING",
                    "description": "Target start date in YYYY-MM-DD format for DWS Calendar (defaults to tomorrow).",
                },
                "duration_days": {
                    "type": "INTEGER",
                    "description": "Reservation duration in days for DWS Calendar (1 to 90, default 7).",
                },
                "flexibility_days": {
                    "type": "INTEGER",
                    "description": "Search window length in days for DWS Calendar (1 to 85, default 14).",
                },
                "max_run_hours": {
                    "type": "INTEGER",
                    "description": "Max run duration hours for DWS Flex Start (1 to 168, default 24).",
                },
                "regions": {
                    "type": "STRING",
                    "description": "Optional comma-separated region filter.",
                },
                "zones": {
                    "type": "STRING",
                    "description": "Optional comma-separated zone filter.",
                },
                "update_ui": {
                    "type": "BOOLEAN",
                    "description": "If true (default), switches to the Capacity Advisory tab and displays all results.",
                },
            },
            "required": ["machine_type"],
        },
    },
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
                    "description": "Optional filter on chip or machine type (e.g. 'B200', 'H100', 'v6e', 'a4-highgpu-8g').",
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
                    "description": "Exact GCE machine type (e.g. 'a4-highgpu-8g', 'a3-highgpu-8g', 'g2-standard-4').",
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
                    "description": "Exact GPU or TPU machine type (e.g. 'a4-highgpu-8g', 'a3-highgpu-8g', 'ct6e-standard-4t').",
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
                    "description": "Search window flexibility in days from start_date (1 to 85, default 7).",
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
                    "description": "Exact GPU machine type (e.g. 'a4-highgpu-8g', 'a3-highgpu-8g', 'g2-standard-4').",
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
            "filtered by category, chip, VM family, consumption model, or location. Keep update_ui=false "
            "if you are showing Capacity Advisory results so you don't switch away from the Advisory tab; "
            "set update_ui=true only when the user wants to view the Quota tab."
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
                    "description": "Optional chip filter (e.g. 'B200', 'H100', 'L4', 'v6e', 'v5p').",
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
                    "description": "If true, switches the UI to the Quota tab and applies these filters (default false).",
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
                    "description": "Exact GPU or TPU machine type (e.g. 'a4-highgpu-8g', 'a3-highgpu-8g', 'ct6e-standard-4t').",
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
                    "description": "VM families to filter by (e.g. ['A3', 'A4', 'G2']).",
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
    return {
        "category": "TPU" if machine_type in TPU_TYPES else "GPU",
        "chip": "",
        "machineType": machine_type,
        "supported": {},
        "advisorySupported": {},
    }


def _chip_matches(query: str, text: str) -> bool:
    """Match chip token with word boundaries so 'B200' never matches 'GB200'."""
    q = query.strip().lower()
    t = (text or "").strip().lower()
    if not q:
        return True
    if q == t:
        return True
    # Word-boundary regex match (so \bb200\b matches "nvidia b200" but NOT "nvidia gb200")
    pattern = rf"(?<![a-z0-9]){re.escape(q)}(?![a-z0-9])"
    return bool(re.search(pattern, t))


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
            chip_filter = (args.get("chip_filter") or "").strip()
            all_mts = get_all_machine_types_info()

            def _format_mt(mt: dict) -> dict:
                return {
                    "machineType": mt["machineType"],
                    "category": mt.get("category", "GPU"),
                    "chip": mt.get("chip"),
                    "gpu": mt.get("gpu"),
                    "family": mt.get("family"),
                    "acceleratorCount": mt.get("gpuCount"),
                    "supported": mt.get("supported"),
                    "advisorySupported": mt.get("advisorySupported"),
                    "zoneCount": len(mt.get("zones", [])),
                    "zones": mt.get("zones", []),
                }

            cat_mts = [
                mt for mt in all_mts
                if cat_filter not in ("GPU", "TPU") or mt.get("category") == cat_filter
            ]

            if chip_filter:
                # 1. Word-boundary / exact token match first (prevents 'B200' matching 'GB200')
                exact_matches = [
                    _format_mt(mt) for mt in cat_mts
                    if _chip_matches(chip_filter, f"{mt.get('chip','')} {mt.get('gpu','')} {mt.get('machineType','')} {mt.get('family','')}")
                ]
                if exact_matches:
                    filtered = exact_matches
                else:
                    # 2. Fallback to substring match only if no word-boundary match exists
                    cf_lower = chip_filter.lower()
                    filtered = [
                        _format_mt(mt) for mt in cat_mts
                        if cf_lower in f"{mt.get('chip','')} {mt.get('gpu','')} {mt.get('machineType','')} {mt.get('family','')}".lower()
                    ]
            else:
                filtered = [_format_mt(mt) for mt in cat_mts]

            return {
                "ok": True,
                "count": len(filtered),
                "machineTypes": filtered,
                "chipGroups": get_chip_groups() if not chip_filter else None,
            }

        elif name == "advise_all":
            if not project:
                return {"ok": False, "error": "No GCP project connected. Ask the user to connect a project first."}
            mt = args["machine_type"]
            meta = _lookup_machine_meta(mt)
            vm_count = max(1, int(args.get("vm_count") or 1))
            tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            start_date = (args.get("start_date") or "").strip() or tomorrow
            duration_days = max(1, min(90, int(args.get("duration_days") or 7)))
            raw_flex = int(args.get("flexibility_days") if args.get("flexibility_days") is not None else 14)
            flexibility_days = max(duration_days, min(365, raw_flex))
            max_run_hours = max(1, min(168, int(args.get("max_run_hours") or 24)))

            regions = [r.strip() for r in (args.get("regions") or "").split(",") if r.strip()] or None
            zones = [z.strip() for z in (args.get("zones") or "").split(",") if z.strip()] or None
            if not zones and not regions:
                all_z = get_zones_for_machine_type(mt)
                zones = all_z or None
                regions = sorted({z.rsplit("-", 1)[0] for z in all_z}) if all_z else None

            adv_sup = meta.get("advisorySupported") or meta.get("supported") or {}
            run_cal = adv_sup.get("dws_calendar", True)
            run_spot = adv_sup.get("spot", True)
            run_flex = adv_sup.get("dws_flex", True)

            async def _maybe_cal():
                if not run_cal:
                    return None
                return await advisory.get_calendar_advisory(
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

            async def _maybe_spot():
                if not run_spot:
                    return None
                return await advisory.get_spot_advisory(
                    project=project,
                    machine_type=mt,
                    regions=regions,
                    zones=zones,
                    user_token=user_token,
                )

            async def _maybe_flex():
                if not run_flex:
                    return None
                return await advisory.get_flex_advisory(
                    project=project,
                    machine_type=mt,
                    size=vm_count,
                    max_run_duration_hours=max_run_hours,
                    regions=regions,
                    zones=zones,
                    user_token=user_token,
                )

            cal_res, spot_res, flex_res = await asyncio.gather(
                _maybe_cal(), _maybe_spot(), _maybe_flex()
            )

            if args.get("update_ui", True):
                action: dict[str, Any] = {
                    "type": "update_advisory",
                    "tab": 0,
                    "category": meta.get("category", "GPU"),
                    "chip": meta.get("chip", ""),
                    "machineType": mt,
                    "vmCount": vm_count,
                    "startDate": start_date,
                    "durationDays": duration_days,
                    "flexibilityDays": flexibility_days,
                    "flexSize": vm_count,
                    "flexMaxRunHours": max_run_hours,
                    "calendarQueryInfo": {
                        "mode": "check",
                        "startDate": start_date,
                        "flexibilityDays": flexibility_days,
                        "durationDays": duration_days,
                        "vmCount": vm_count,
                    },
                }
                if cal_res is not None:
                    action["calendarResults"] = cal_res
                if spot_res is not None:
                    action["spotResults"] = spot_res
                if flex_res is not None:
                    action["flexResults"] = flex_res
                ui_actions.append(action)

            return {
                "ok": True,
                "machineType": mt,
                "chip": meta.get("chip"),
                "calendarAdvisory": cal_res,
                "spotAdvisory": spot_res,
                "flexAdvisory": flex_res,
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
            vm_count = max(1, int(args.get("vm_count") or 1))
            tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
            start_date = (args.get("start_date") or "").strip() or tomorrow
            duration_days = max(1, min(90, int(args.get("duration_days") or 7)))
            raw_flex = int(args.get("flexibility_days") if args.get("flexibility_days") is not None else 7)
            flexibility_days = max(duration_days, min(365, raw_flex))
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
            size = max(1, int(args.get("size") or 1))
            max_run_hours = max(1, min(168, int(args.get("max_run_hours") or 24)))
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
            chip_q = (args.get("chip") or "").strip()
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
                if chip_q and not _chip_matches(chip_q, f"{q.get('chip','')} {q.get('displayName','')}"):
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


def _coalesce_ui_actions(ui_actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge multiple update_advisory actions so separate tool calls never clobber each other."""
    if len(ui_actions) <= 1:
        return ui_actions

    advisory_by_mt: dict[str, dict[str, Any]] = {}
    other_actions: list[dict[str, Any]] = []

    for act in ui_actions:
        if act.get("type") == "update_advisory":
            mt = act.get("machineType") or "_default"
            if mt not in advisory_by_mt:
                advisory_by_mt[mt] = dict(act)
            else:
                advisory_by_mt[mt].update(act)
        else:
            other_actions.append(act)

    if not advisory_by_mt:
        return other_actions

    # Pick the most complete advisory update (or the first requested machine type that has results)
    best_mt = next(iter(advisory_by_mt.keys()))
    best_score = -1
    for mt, merged in advisory_by_mt.items():
        score = 0
        if merged.get("calendarResults") or merged.get("splitsResults"):
            score += 2
        if merged.get("spotResults") and (merged["spotResults"].get("recommendations") or merged["spotResults"].get("scores")):
            score += 2
        if merged.get("flexResults") and merged["flexResults"].get("recommendations"):
            score += 2
        if score > best_score:
            best_score = score
            best_mt = mt

    # If we have an advisory update, keep the user on the Advisory tab (tab 0) unless configure_scan was called
    has_scan = any(a.get("type") == "configure_scan" for a in other_actions)
    filtered_others = [
        a for a in other_actions
        if not (a.get("type") == "filter_quotas" and not has_scan)
    ]
    result = filtered_others + [advisory_by_mt[best_mt]]
    return result


async def _call_vertex_gemini(
    contents: list[dict[str, Any]],
    system_instruction: str,
    model: str,
    project: str,
    user_token: Optional[str],
    allow_tools: bool = True,
) -> dict[str, Any]:
    """Call Vertex AI Gemini (global endpoint) with automatic credential/project fallback."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "tools": [{"functionDeclarations": TOOL_DECLARATIONS}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }
    if not allow_tools:
        payload["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}

    async with httpx.AsyncClient(timeout=60.0) as client:
        if api_key:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"GEMINI_API_KEY call returned {resp.status_code}: {resp.text[:200]}")

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

    max_rounds = 8
    for round_idx in range(max_rounds):
        resp_data = await _call_vertex_gemini(
            contents=contents,
            system_instruction=system_instruction,
            model=chosen_model,
            project=project,
            user_token=user_token,
            allow_tools=(round_idx < max_rounds - 1),
        )
        candidates = resp_data.get("candidates") or []
        if not candidates:
            break

        model_content = candidates[0].get("content") or {"role": "model", "parts": []}
        parts = model_content.get("parts") or []

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not function_calls:
            reply_texts = [p.get("text", "") for p in parts if "text" in p and not p.get("thought")]
            reply = "\n".join(t for t in reply_texts if t).strip()
            if not reply:
                reply = "I've updated the interface below with the requested information."
            return {
                "reply": reply,
                "toolCalls": tool_traces,
                "uiActions": _coalesce_ui_actions(ui_actions),
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

    return {
        "reply": "I've completed running the capacity checks and updated the view below.",
        "toolCalls": tool_traces,
        "uiActions": _coalesce_ui_actions(ui_actions),
        "model": chosen_model,
    }
