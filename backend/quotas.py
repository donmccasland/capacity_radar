"""
GPU & TPU Quota Explorer for Capacity Radar.

Maps every supported GPU and TPU chip, VM family, and consumption model to its
authoritative Google Cloud quota metrics (`compute.googleapis.com` and
`tpu.googleapis.com`), fetches live project limits & usage per region/zone,
and generates deep-link URLs to the Google Cloud Console Quotas page.
"""

import asyncio
import json
import logging
import time
import urllib.parse
from typing import Optional

import httpx

from gpu_data import GPU_ZONES, FAMILY_TO_GPU_ZONE_KEY, MACHINE_TYPES, TPU_TYPES

logger = logging.getLogger(__name__)

# In-memory cache: project -> (timestamp, response_dict)
_QUOTA_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 60.0


def _regions_from_zones(zones: list[str]) -> list[str]:
    return sorted({z.rsplit("-", 1)[0] for z in zones if "-" in z})


def _gpu_family_zones(families: list[str]) -> list[str]:
    zones: set[str] = set()
    for fam in families:
        key = FAMILY_TO_GPU_ZONE_KEY.get(fam)
        if key and key in GPU_ZONES:
            zones.update(GPU_ZONES[key])
    return sorted(zones)


def _gpu_family_machine_types(families: list[str]) -> list[str]:
    return [mt for mt, info in MACHINE_TYPES.items() if info.get("family") in families]


def build_console_quota_url(
    project: str,
    service: str,
    metric: str,
    location: Optional[str] = None,
    dimensions: Optional[dict[str, str]] = None,
) -> str:
    """Build a direct link to the Google Cloud Console Quotas page filtered for a metric."""
    params: list[tuple[str, str]] = []
    if project:
        params.append(("project", project))
    if service:
        params.append(("service", service))
    if metric:
        params.append(("metric", metric))
    if location and location.lower() != "global":
        params.append(("location", location))
    if dimensions:
        params.append(("dimensions", json.dumps(dimensions, separators=(",", ":"))))
    query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"https://console.cloud.google.com/iam-admin/quotas?{query}"


# ---------------------------------------------------------------------------
# Canonical GPU & TPU Quota Catalog
# ---------------------------------------------------------------------------

def _build_quota_catalog() -> list[dict]:
    all_gpu_zones = sorted({z for zlist in GPU_ZONES.values() for z in zlist})
    all_tpu_zones = sorted({z for info in TPU_TYPES.values() for z in info.get("zones", [])})

    catalog = [
        # ===================================================================
        # GLOBAL GPU QUOTA
        # ===================================================================
        {
            "id": "gpu-all-regions-global",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "All GPUs",
            "hardwareLabel": "All NVIDIA GPUs (Global Cap)",
            "vmFamilies": ["A4X", "A4", "A3 Ultra", "A3 Mega", "A3 High", "A3 Edge", "A2 Ultra", "A2 Standard", "A2 Mega", "G4", "G2", "N1"],
            "machineTypes": list(MACHINE_TYPES.keys()),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_all_regions",
            "legacyMetric": "GPUS_ALL_REGIONS",
            "displayName": "GPUs (all regions)",
            "dimensions": None,
            "scope": "global",
            "unitLabel": "GPUs",
            "supportedZones": ["global"],
            "supportedRegions": ["global"],
            "notes": "Global ceiling across all regions for standard/on-demand GPUs. Must be >= your sum of regional GPU quotas.",
        },

        # ===================================================================
        # NVIDIA GB300 (A4X Max)
        # ===================================================================
        {
            "id": "gpu-gb300-committed",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "GB300",
            "hardwareLabel": "NVIDIA GB300",
            "vmFamilies": ["A4X Max"],
            "machineTypes": _gpu_family_machine_types(["A4X Max"]),
            "consumptionModels": ["committed"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/committed_nvidia_gb300_gpus",
            "legacyMetric": "COMMITTED_NVIDIA_GB300_GPUS",
            "displayName": "Committed NVIDIA GB300 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A4X Max"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A4X Max"])),
            "notes": "Regional committed use discount (CUD) quota for A4X Max (a4x-maxgpu-4g-metal).",
        },

        # ===================================================================
        # NVIDIA GB200 (A4X)
        # ===================================================================
        {
            "id": "gpu-gb200-ondemand",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "GB200",
            "hardwareLabel": "NVIDIA GB200",
            "vmFamilies": ["A4X"],
            "machineTypes": _gpu_family_machine_types(["A4X"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_per_gpu_family",
            "legacyMetric": None,
            "displayName": "GPUs per GPU family (NVIDIA_GB200)",
            "dimensions": {"gpu_family": "NVIDIA_GB200"},
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A4X"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A4X"])),
            "notes": "Dimensional quota (gpu_family=NVIDIA_GB200) for On-Demand / Reserved A4X (a4x-highgpu-4g).",
        },
        {
            "id": "gpu-gb200-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "GB200",
            "hardwareLabel": "NVIDIA GB200",
            "vmFamilies": ["A4X"],
            "machineTypes": _gpu_family_machine_types(["A4X"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_gb200_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_GB200_GPUS",
            "displayName": "Preemptible NVIDIA GB200 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A4X"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A4X"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on A4X (a4x-highgpu-4g).",
        },

        # ===================================================================
        # NVIDIA B200 (A4)
        # ===================================================================
        {
            "id": "gpu-b200-ondemand-cal",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "B200",
            "hardwareLabel": "NVIDIA B200",
            "vmFamilies": ["A4"],
            "machineTypes": _gpu_family_machine_types(["A4"]),
            "consumptionModels": ["dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_per_gpu_family",
            "legacyMetric": None,
            "displayName": "GPUs per GPU family (NVIDIA_B200)",
            "dimensions": {"gpu_family": "NVIDIA_B200"},
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A4"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A4"])),
            "notes": "Dimensional quota (gpu_family=NVIDIA_B200) for Reserved and DWS Calendar Mode A4 (a4-highgpu-8g).",
        },
        {
            "id": "gpu-b200-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "B200",
            "hardwareLabel": "NVIDIA B200",
            "vmFamilies": ["A4"],
            "machineTypes": _gpu_family_machine_types(["A4"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_b200_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_B200_GPUS",
            "displayName": "Preemptible NVIDIA B200 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A4"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A4"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on A4 (a4-highgpu-8g).",
        },
        {
            "id": "gpu-b200-committed",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "B200",
            "hardwareLabel": "NVIDIA B200",
            "vmFamilies": ["A4"],
            "machineTypes": _gpu_family_machine_types(["A4"]),
            "consumptionModels": ["committed"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/committed_nvidia_b200_gpus",
            "legacyMetric": "COMMITTED_NVIDIA_B200_GPUS",
            "displayName": "Committed NVIDIA B200 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A4"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A4"])),
            "notes": "Committed use discount (CUD) quota for NVIDIA B200 GPUs.",
        },

        # ===================================================================
        # NVIDIA H200 (A3 Ultra)
        # ===================================================================
        {
            "id": "gpu-h200-ondemand-cal",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H200",
            "hardwareLabel": "NVIDIA H200",
            "vmFamilies": ["A3 Ultra"],
            "machineTypes": _gpu_family_machine_types(["A3 Ultra"]),
            "consumptionModels": ["dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_per_gpu_family",
            "legacyMetric": None,
            "displayName": "GPUs per GPU family (NVIDIA_H200)",
            "dimensions": {"gpu_family": "NVIDIA_H200"},
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 Ultra"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 Ultra"])),
            "notes": "Dimensional quota (gpu_family=NVIDIA_H200) for Reserved and DWS Calendar Mode A3 Ultra (a3-ultragpu-8g).",
        },
        {
            "id": "gpu-h200-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H200",
            "hardwareLabel": "NVIDIA H200",
            "vmFamilies": ["A3 Ultra"],
            "machineTypes": _gpu_family_machine_types(["A3 Ultra"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_h200_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_H200_GPUS",
            "displayName": "Preemptible NVIDIA H200 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 Ultra"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 Ultra"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on A3 Ultra (a3-ultragpu-8g).",
        },
        {
            "id": "gpu-h200-committed",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H200",
            "hardwareLabel": "NVIDIA H200",
            "vmFamilies": ["A3 Ultra"],
            "machineTypes": _gpu_family_machine_types(["A3 Ultra"]),
            "consumptionModels": ["committed"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/committed_nvidia_h200_gpus",
            "legacyMetric": "COMMITTED_NVIDIA_H200_GPUS",
            "displayName": "Committed NVIDIA H200 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 Ultra"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 Ultra"])),
            "notes": "Committed use discount (CUD) quota for NVIDIA H200 GPUs.",
        },

        # ===================================================================
        # NVIDIA H100 80GB — A3 Mega vs A3 High / A3 Edge
        # ===================================================================
        {
            "id": "gpu-h100-mega-ondemand-cal",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H100",
            "hardwareLabel": "NVIDIA H100 80GB (Mega)",
            "vmFamilies": ["A3 Mega"],
            "machineTypes": _gpu_family_machine_types(["A3 Mega"]),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_per_gpu_family",
            "legacyMetric": None,
            "displayName": "GPUs per GPU family (NVIDIA_H100_MEGA)",
            "dimensions": {"gpu_family": "NVIDIA_H100_MEGA"},
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 Mega"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 Mega"])),
            "notes": "Dimensional quota (gpu_family=NVIDIA_H100_MEGA) for On-Demand, Reserved, and DWS Calendar Mode A3 Mega (a3-megagpu-8g).",
        },
        {
            "id": "gpu-h100-mega-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H100",
            "hardwareLabel": "NVIDIA H100 80GB (Mega)",
            "vmFamilies": ["A3 Mega"],
            "machineTypes": _gpu_family_machine_types(["A3 Mega"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_h100_mega_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_H100_MEGA_GPUS",
            "displayName": "Preemptible NVIDIA H100 MEGA GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 Mega"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 Mega"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on A3 Mega (a3-megagpu-8g).",
        },
        {
            "id": "gpu-h100-high-ondemand-cal",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H100",
            "hardwareLabel": "NVIDIA H100 80GB (High / Edge)",
            "vmFamilies": ["A3 High", "A3 Edge"],
            "machineTypes": _gpu_family_machine_types(["A3 High", "A3 Edge"]),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_per_gpu_family",
            "legacyMetric": "NVIDIA_H100_GPUS",
            "displayName": "GPUs per GPU family (NVIDIA_H100)",
            "dimensions": {"gpu_family": "NVIDIA_H100"},
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 High", "A3 Edge"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 High", "A3 Edge"])),
            "notes": "Dimensional quota (gpu_family=NVIDIA_H100) for On-Demand, Reserved, and DWS Calendar Mode A3 High & A3 Edge (a3-highgpu-*, a3-edgegpu-8g).",
        },
        {
            "id": "gpu-h100-high-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "H100",
            "hardwareLabel": "NVIDIA H100 80GB (High / Edge)",
            "vmFamilies": ["A3 High", "A3 Edge"],
            "machineTypes": _gpu_family_machine_types(["A3 High", "A3 Edge"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_h100_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_H100_GPUS",
            "displayName": "Preemptible NVIDIA H100 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A3 High", "A3 Edge"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A3 High", "A3 Edge"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on A3 High & A3 Edge (a3-highgpu-*, a3-edgegpu-8g).",
        },

        # ===================================================================
        # NVIDIA RTX PRO 6000 (G4)
        # ===================================================================
        {
            "id": "gpu-rtx6000-ondemand",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "RTX PRO 6000",
            "hardwareLabel": "NVIDIA RTX PRO 6000",
            "vmFamilies": ["G4"],
            "machineTypes": _gpu_family_machine_types(["G4"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/gpus_per_gpu_family",
            "legacyMetric": None,
            "displayName": "GPUs per GPU family (NVIDIA_RTX_PRO_6000)",
            "dimensions": {"gpu_family": "NVIDIA_RTX_PRO_6000"},
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["G4"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["G4"])),
            "notes": "Dimensional quota (gpu_family=NVIDIA_RTX_PRO_6000) for On-Demand / Reserved G4 VMs (g4-standard-*).",
        },
        {
            "id": "gpu-rtx6000-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "RTX PRO 6000",
            "hardwareLabel": "NVIDIA RTX PRO 6000",
            "vmFamilies": ["G4"],
            "machineTypes": _gpu_family_machine_types(["G4"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_rtx_pro_6000_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_RTX_PRO_6000_GPUS",
            "displayName": "Preemptible NVIDIA RTX PRO 6000 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["G4"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["G4"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on G4 VMs (g4-standard-*).",
        },

        # ===================================================================
        # NVIDIA A100 80GB (A2 Ultra)
        # ===================================================================
        {
            "id": "gpu-a100-80gb-ondemand",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "A100 80GB",
            "hardwareLabel": "NVIDIA A100 80GB",
            "vmFamilies": ["A2 Ultra"],
            "machineTypes": _gpu_family_machine_types(["A2 Ultra"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/nvidia_a100_80gb_gpus",
            "legacyMetric": "NVIDIA_A100_80GB_GPUS",
            "displayName": "NVIDIA A100 80GB GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A2 Ultra"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A2 Ultra"])),
            "notes": "Regional On-Demand / Reserved quota for A2 Ultra VMs (a2-ultragpu-*).",
        },
        {
            "id": "gpu-a100-80gb-spot",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "A100 80GB",
            "hardwareLabel": "NVIDIA A100 80GB",
            "vmFamilies": ["A2 Ultra"],
            "machineTypes": _gpu_family_machine_types(["A2 Ultra"]),
            "consumptionModels": ["spot"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_a100_80gb_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_A100_80GB_GPUS",
            "displayName": "Preemptible NVIDIA A100 80GB GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A2 Ultra"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A2 Ultra"])),
            "notes": "Regional Spot / Preemptible quota for A2 Ultra VMs (a2-ultragpu-*).",
        },

        # ===================================================================
        # NVIDIA A100 40GB (A2 Standard / A2 Mega)
        # ===================================================================
        {
            "id": "gpu-a100-40gb-ondemand",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "A100 40GB",
            "hardwareLabel": "NVIDIA A100 40GB",
            "vmFamilies": ["A2 Standard", "A2 Mega"],
            "machineTypes": _gpu_family_machine_types(["A2 Standard", "A2 Mega"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/nvidia_a100_gpus",
            "legacyMetric": "NVIDIA_A100_GPUS",
            "displayName": "NVIDIA A100 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A2 Standard", "A2 Mega"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A2 Standard", "A2 Mega"])),
            "notes": "Regional On-Demand / Reserved quota for A2 Standard & A2 Mega VMs (a2-highgpu-*, a2-megagpu-16g).",
        },
        {
            "id": "gpu-a100-40gb-spot",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "A100 40GB",
            "hardwareLabel": "NVIDIA A100 40GB",
            "vmFamilies": ["A2 Standard", "A2 Mega"],
            "machineTypes": _gpu_family_machine_types(["A2 Standard", "A2 Mega"]),
            "consumptionModels": ["spot"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_a100_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_A100_GPUS",
            "displayName": "Preemptible NVIDIA A100 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["A2 Standard", "A2 Mega"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A2 Standard", "A2 Mega"])),
            "notes": "Regional Spot / Preemptible quota for A2 Standard & A2 Mega VMs (a2-highgpu-*, a2-megagpu-16g).",
        },

        # ===================================================================
        # NVIDIA L4 (G2)
        # ===================================================================
        {
            "id": "gpu-l4-ondemand",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "L4",
            "hardwareLabel": "NVIDIA L4",
            "vmFamilies": ["G2"],
            "machineTypes": _gpu_family_machine_types(["G2"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/nvidia_l4_gpus",
            "legacyMetric": "NVIDIA_L4_GPUS",
            "displayName": "NVIDIA L4 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["G2"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["G2"])),
            "notes": "Regional On-Demand / Reserved quota for G2 VMs (g2-standard-*).",
        },
        {
            "id": "gpu-l4-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "L4",
            "hardwareLabel": "NVIDIA L4",
            "vmFamilies": ["G2"],
            "machineTypes": _gpu_family_machine_types(["G2"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_l4_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_L4_GPUS",
            "displayName": "Preemptible NVIDIA L4 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["G2"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["G2"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on G2 VMs (g2-standard-*).",
        },

        # ===================================================================
        # NVIDIA T4 (N1)
        # ===================================================================
        {
            "id": "gpu-t4-ondemand",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "T4",
            "hardwareLabel": "NVIDIA T4",
            "vmFamilies": ["N1"],
            "machineTypes": _gpu_family_machine_types(["N1"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/nvidia_t4_gpus",
            "legacyMetric": "NVIDIA_T4_GPUS",
            "displayName": "NVIDIA T4 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["N1"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["N1"])),
            "notes": "Regional On-Demand / Reserved quota for N1 VMs with T4 accelerators.",
        },
        {
            "id": "gpu-t4-spot-flex",
            "category": "GPU",
            "isVmQuota": False,
            "chip": "T4",
            "hardwareLabel": "NVIDIA T4",
            "vmFamilies": ["N1"],
            "machineTypes": _gpu_family_machine_types(["N1"]),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_nvidia_t4_gpus",
            "legacyMetric": "PREEMPTIBLE_NVIDIA_T4_GPUS",
            "displayName": "Preemptible NVIDIA T4 GPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "GPUs",
            "supportedZones": _gpu_family_zones(["N1"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["N1"])),
            "notes": "Used for Spot VMs and DWS Flex-Start on N1 VMs with T4 accelerators.",
        },

        # ===================================================================
        # RELATED GPU HOST VM / CPU QUOTAS (Toggled via isVmQuota=True)
        # ===================================================================
        {
            "id": "vm-cpus-all-regions",
            "category": "GPU",
            "isVmQuota": True,
            "chip": "All GPUs",
            "hardwareLabel": "Host VM vCPUs (Global Cap)",
            "vmFamilies": ["A4X", "A4", "A3 Ultra", "A3 Mega", "A3 High", "A3 Edge", "A2 Ultra", "A2 Standard", "A2 Mega", "G4", "G2", "N1"],
            "machineTypes": list(MACHINE_TYPES.keys()),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/cpus_all_regions",
            "legacyMetric": "CPUS_ALL_REGIONS",
            "displayName": "CPUs (all regions)",
            "dimensions": None,
            "scope": "global",
            "unitLabel": "vCPUs",
            "supportedZones": ["global"],
            "supportedRegions": ["global"],
            "notes": "Global vCPU ceiling across all regions for On-Demand / Reserved GPU host VMs.",
        },
        {
            "id": "vm-cpus-regional",
            "category": "GPU",
            "isVmQuota": True,
            "chip": "All GPUs",
            "hardwareLabel": "Host VM vCPUs (On-Demand / Reserved)",
            "vmFamilies": ["A4X", "A4", "A3 Ultra", "A3 Mega", "A3 High", "A3 Edge", "G4", "G2", "N1"],
            "machineTypes": _gpu_family_machine_types(["A4X", "A4", "A3 Ultra", "A3 Mega", "A3 High", "A3 Edge", "G4", "G2", "N1"]),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/cpus",
            "legacyMetric": "CPUS",
            "displayName": "CPUs (regional)",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "vCPUs",
            "supportedZones": all_gpu_zones,
            "supportedRegions": _regions_from_zones(all_gpu_zones),
            "notes": "Regional vCPU quota required alongside GPU quota for On-Demand/Reserved GPU VMs (e.g. a3-highgpu-8g requires 208 CPUs; a4-highgpu-8g requires 224 CPUs).",
        },
        {
            "id": "vm-preemptible-cpus-regional",
            "category": "GPU",
            "isVmQuota": True,
            "chip": "All GPUs",
            "hardwareLabel": "Host VM Preemptible vCPUs (Spot / DWS Flex)",
            "vmFamilies": ["A4X Max", "A4X", "A4", "A3 Ultra", "A3 Mega", "A3 High", "A3 Edge", "A2 Ultra", "A2 Standard", "A2 Mega", "G4", "G2", "N1"],
            "machineTypes": list(MACHINE_TYPES.keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_cpus",
            "legacyMetric": "PREEMPTIBLE_CPUS",
            "displayName": "Preemptible CPUs (regional)",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "vCPUs",
            "supportedZones": all_gpu_zones,
            "supportedRegions": _regions_from_zones(all_gpu_zones),
            "notes": "Regional Preemptible vCPU quota required alongside Preemptible GPU quota when launching Spot VMs or DWS Flex-Start GPU instances.",
        },
        {
            "id": "vm-a2-cpus-regional",
            "category": "GPU",
            "isVmQuota": True,
            "chip": "A100 40GB",
            "hardwareLabel": "A2 Host VM vCPUs (A100 40GB / 80GB)",
            "vmFamilies": ["A2 Ultra", "A2 Standard", "A2 Mega"],
            "machineTypes": _gpu_family_machine_types(["A2 Ultra", "A2 Standard", "A2 Mega"]),
            "consumptionModels": ["on_demand"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/a2_cpus",
            "legacyMetric": "A2_CPUS",
            "displayName": "NVIDIA A2 CPUs",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "vCPUs",
            "supportedZones": _gpu_family_zones(["A2 Ultra", "A2 Standard", "A2 Mega"]),
            "supportedRegions": _regions_from_zones(_gpu_family_zones(["A2 Ultra", "A2 Standard", "A2 Mega"])),
            "notes": "Dedicated regional vCPU quota for On-Demand/Reserved A2 VMs (12 A2_CPUS per A100 GPU).",
        },

        # ===================================================================
        # CLOUD TPU v7x (Ironwood)
        # ===================================================================
        {
            "id": "tpu-v7x-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v7x",
            "hardwareLabel": "Cloud TPU v7x (Ironwood)",
            "vmFamilies": ["TPU v7x"],
            "machineTypes": list(TPU_TYPES["v7x"]["machine_types"].keys()),
            "consumptionModels": ["on_demand"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu7x",
            "legacyMetric": None,
            "displayName": "TPU7x cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v7x"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v7x"]["zones"]),
            "notes": "On-Demand TPU v7x quota in Cloud TPU API.",
        },
        {
            "id": "tpu-v7x-spot-flex",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v7x",
            "hardwareLabel": "Cloud TPU v7x (Ironwood)",
            "vmFamilies": ["TPU v7x"],
            "machineTypes": list(TPU_TYPES["v7x"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu7x-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU7x cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v7x"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v7x"]["zones"]),
            "notes": "Used for Spot and DWS Flex-Start (QueuedResource) TPU v7x deployments.",
        },
        {
            "id": "tpu-v7x-reserved",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v7x",
            "hardwareLabel": "Cloud TPU v7x (Ironwood)",
            "vmFamilies": ["TPU v7x"],
            "machineTypes": list(TPU_TYPES["v7x"]["machine_types"].keys()),
            "consumptionModels": ["dws_calendar", "committed"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu7x-reserved",
            "legacyMetric": None,
            "displayName": "Reserved TPU7x cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v7x"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v7x"]["zones"]),
            "notes": "Used when deploying TPU v7x slices against a reservation.",
        },

        # ===================================================================
        # CLOUD TPU v6e (Trillium)
        # ===================================================================
        {
            "id": "tpu-v6e-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v6e",
            "hardwareLabel": "Cloud TPU v6e (Trillium)",
            "vmFamilies": ["TPU v6e"],
            "machineTypes": list(TPU_TYPES["v6e"]["machine_types"].keys()),
            "consumptionModels": ["on_demand"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v6e",
            "legacyMetric": None,
            "displayName": "TPU-V6E cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v6e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v6e"]["zones"]),
            "notes": "On-Demand TPU v6e chip quota (ct6e-standard-1t/4t/8t).",
        },
        {
            "id": "tpu-v6e-spot-flex",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v6e",
            "hardwareLabel": "Cloud TPU v6e (Trillium)",
            "vmFamilies": ["TPU v6e"],
            "machineTypes": list(TPU_TYPES["v6e"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v6e-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU-V6E cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v6e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v6e"]["zones"]),
            "notes": "Used for Spot TPU v6e nodes and DWS Flex-Start QueuedResources.",
        },
        {
            "id": "tpu-v6e-reserved",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v6e",
            "hardwareLabel": "Cloud TPU v6e (Trillium)",
            "vmFamilies": ["TPU v6e"],
            "machineTypes": list(TPU_TYPES["v6e"]["machine_types"].keys()),
            "consumptionModels": ["dws_calendar", "committed"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v6e-reserved",
            "legacyMetric": None,
            "displayName": "Reserved TPU-V6E cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v6e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v6e"]["zones"]),
            "notes": "Required when consuming a DWS Calendar Mode or standard reservation for TPU v6e.",
        },

        # ===================================================================
        # CLOUD TPU v5p (Viperfish)
        # ===================================================================
        {
            "id": "tpu-v5p-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5p",
            "hardwareLabel": "Cloud TPU v5p (Viperfish)",
            "vmFamilies": ["TPU v5p"],
            "machineTypes": list(TPU_TYPES["v5p"]["machine_types"].keys()),
            "consumptionModels": ["on_demand"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5p",
            "legacyMetric": None,
            "displayName": "TPU-V5P cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v5p"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5p"]["zones"]),
            "notes": "On-Demand TPU v5p quota (ct5p-hightpu-1t/2t/4t).",
        },
        {
            "id": "tpu-v5p-spot-flex",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5p",
            "hardwareLabel": "Cloud TPU v5p (Viperfish)",
            "vmFamilies": ["TPU v5p"],
            "machineTypes": list(TPU_TYPES["v5p"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5p-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU-V5P cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v5p"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5p"]["zones"]),
            "notes": "Used for Spot TPU v5p nodes and DWS Flex-Start QueuedResources.",
        },
        {
            "id": "tpu-v5p-reserved",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5p",
            "hardwareLabel": "Cloud TPU v5p (Viperfish)",
            "vmFamilies": ["TPU v5p"],
            "machineTypes": list(TPU_TYPES["v5p"]["machine_types"].keys()),
            "consumptionModels": ["dws_calendar", "committed"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5p-reserved",
            "legacyMetric": None,
            "displayName": "Reserved TPU-V5P cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v5p"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5p"]["zones"]),
            "notes": "Required when consuming a DWS Calendar Mode or standard reservation for TPU v5p.",
        },

        # ===================================================================
        # CLOUD TPU v5e (Viperlite) — Serving (1–8 chips) vs Batch (>=16 chips)
        # ===================================================================
        {
            "id": "tpu-v5e-serving-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5e",
            "hardwareLabel": "Cloud TPU v5e (Serving 1–8 chips)",
            "vmFamilies": ["TPU v5e"],
            "machineTypes": list(TPU_TYPES["v5e"]["machine_types"].keys()),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5s-litepod-serving",
            "legacyMetric": None,
            "displayName": "TPU-V5 lite pod cores for serving workload",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v5e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5e"]["zones"]),
            "notes": "Used for 1–8 chip On-Demand/Reserved TPU v5e slices (ct5e-hightpu-1t/4t/8t).",
        },
        {
            "id": "tpu-v5e-serving-spot-flex",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5e",
            "hardwareLabel": "Cloud TPU v5e (Serving 1–8 chips)",
            "vmFamilies": ["TPU v5e"],
            "machineTypes": list(TPU_TYPES["v5e"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5s-litepod-serving-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU-V5 lite pod cores for serving workload",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v5e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5e"]["zones"]),
            "notes": "Used for 1–8 chip Spot or DWS Flex-Start TPU v5e slices (ct5e-hightpu-1t/4t/8t).",
        },
        {
            "id": "tpu-v5e-batch-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5e",
            "hardwareLabel": "Cloud TPU v5e (Batch / Pod >=16 chips)",
            "vmFamilies": ["TPU v5e"],
            "machineTypes": list(TPU_TYPES["v5e"]["machine_types"].keys()),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5s-litepod",
            "legacyMetric": None,
            "displayName": "TPU-V5 lite pod cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v5e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5e"]["zones"]),
            "notes": "Used for multi-host (>=16 chip) On-Demand/Reserved TPU v5e pod slices.",
        },
        {
            "id": "tpu-v5e-batch-spot-flex",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v5e",
            "hardwareLabel": "Cloud TPU v5e (Batch / Pod >=16 chips)",
            "vmFamilies": ["TPU v5e"],
            "machineTypes": list(TPU_TYPES["v5e"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v5s-litepod-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU-V5 lite pod cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v5e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5e"]["zones"]),
            "notes": "Used for multi-host (>=16 chip) Spot or DWS Flex-Start TPU v5e pod slices.",
        },

        # ===================================================================
        # CLOUD TPU v4 (Pufferfish)
        # ===================================================================
        {
            "id": "tpu-v4-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v4",
            "hardwareLabel": "Cloud TPU v4 (Pufferfish)",
            "vmFamilies": ["TPU v4"],
            "machineTypes": list(TPU_TYPES["v4"]["machine_types"].keys()),
            "consumptionModels": ["on_demand"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v4s-pod",
            "legacyMetric": None,
            "displayName": "TPU v4 pod cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v4"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v4"]["zones"]),
            "notes": "On-Demand TPU v4 pod cores (ct4p-hightpu-4t). Visible on allowlisted projects.",
        },
        {
            "id": "tpu-v4-spot-flex",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v4",
            "hardwareLabel": "Cloud TPU v4 (Pufferfish)",
            "vmFamilies": ["TPU v4"],
            "machineTypes": list(TPU_TYPES["v4"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v4s-pod-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU v4 pod cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v4"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v4"]["zones"]),
            "notes": "Preemptible/Spot and DWS Flex-Start TPU v4 pod cores (ct4p-hightpu-4t). Visible on allowlisted projects.",
        },

        # ===================================================================
        # CLOUD TPU v3 & v2
        # ===================================================================
        {
            "id": "tpu-v3-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v3",
            "hardwareLabel": "Cloud TPU v3 (Dragonfish)",
            "vmFamilies": ["TPU v3"],
            "machineTypes": list(TPU_TYPES["v3"]["machine_types"].keys()),
            "consumptionModels": ["on_demand"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v3s",
            "legacyMetric": None,
            "displayName": "TPU-V3 cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v3"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v3"]["zones"]),
            "notes": "On-Demand single-device TPU v3 cores (v3-8). Pod slices use tpu.googleapis.com/tpu-v3s-pod.",
        },
        {
            "id": "tpu-v3-spot",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v3",
            "hardwareLabel": "Cloud TPU v3 (Dragonfish)",
            "vmFamilies": ["TPU v3"],
            "machineTypes": list(TPU_TYPES["v3"]["machine_types"].keys()),
            "consumptionModels": ["spot"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v3s-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU-V3 cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v3"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v3"]["zones"]),
            "notes": "Spot/Preemptible single-device TPU v3 cores (v3-8). Pod slices use tpu.googleapis.com/tpu-v3s-pod-preemptible.",
        },
        {
            "id": "tpu-v2-ondemand",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v2",
            "hardwareLabel": "Cloud TPU v2 (Jellyfish)",
            "vmFamilies": ["TPU v2"],
            "machineTypes": list(TPU_TYPES["v2"]["machine_types"].keys()),
            "consumptionModels": ["on_demand"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v2s",
            "legacyMetric": None,
            "displayName": "TPU-V2 cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v2"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v2"]["zones"]),
            "notes": "On-Demand single-device TPU v2 cores (v2-8).",
        },
        {
            "id": "tpu-v2-spot",
            "category": "TPU",
            "isVmQuota": False,
            "chip": "v2",
            "hardwareLabel": "Cloud TPU v2 (Jellyfish)",
            "vmFamilies": ["TPU v2"],
            "machineTypes": list(TPU_TYPES["v2"]["machine_types"].keys()),
            "consumptionModels": ["spot"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/tpu-v2s-preemptible",
            "legacyMetric": None,
            "displayName": "Preemptible TPU-V2 cores in use by TPU API",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "TPU cores",
            "supportedZones": TPU_TYPES["v2"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v2"]["zones"]),
            "notes": "Spot/Preemptible single-device TPU v2 cores (v2-8).",
        },

        # ===================================================================
        # RELATED TPU VM / QUEUED RESOURCE / GCE QUOTAS (Toggled via isVmQuota=True)
        # ===================================================================
        {
            "id": "tpu-queued-resources",
            "category": "TPU",
            "isVmQuota": True,
            "chip": "All TPUs",
            "hardwareLabel": "Cloud TPU Queued Resources",
            "vmFamilies": ["TPU v7x", "TPU v6e", "TPU v5p", "TPU v5e", "TPU v4"],
            "machineTypes": [mt for info in TPU_TYPES.values() for mt in info.get("machine_types", {})],
            "consumptionModels": ["dws_flex", "on_demand", "spot"],
            "service": "tpu.googleapis.com",
            "metric": "tpu.googleapis.com/queuedResources",
            "legacyMetric": None,
            "displayName": "Accepted QueuedResource requests",
            "dimensions": None,
            "scope": "zone_or_region",
            "unitLabel": "Queued requests",
            "supportedZones": all_tpu_zones,
            "supportedRegions": _regions_from_zones(all_tpu_zones),
            "notes": "Maximum concurrent active/waiting QueuedResource requests per zone/region (required for TPU DWS Flex-Start).",
        },
        {
            "id": "tpu-gce-ct6e-family",
            "category": "TPU",
            "isVmQuota": True,
            "chip": "v6e",
            "hardwareLabel": "GCE TPU Family Quota (CT6E)",
            "vmFamilies": ["TPU v6e"],
            "machineTypes": list(TPU_TYPES["v6e"]["machine_types"].keys()),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/tpus_per_tpu_family",
            "legacyMetric": None,
            "displayName": "TPUs per TPU family (CT6E)",
            "dimensions": {"tpu_family": "CT6E"},
            "scope": "region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v6e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v6e"]["zones"]),
            "notes": "Compute Engine dimensional TPU quota (tpu_family=CT6E) checked for GCE-managed TPU v6e reservations/VMs.",
        },
        {
            "id": "tpu-gce-v5e-podslice-ondemand",
            "category": "TPU",
            "isVmQuota": True,
            "chip": "v5e",
            "hardwareLabel": "GCE TPU v5e Lite PodSlice Chips",
            "vmFamilies": ["TPU v5e"],
            "machineTypes": list(TPU_TYPES["v5e"]["machine_types"].keys()),
            "consumptionModels": ["on_demand", "dws_calendar"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/tpu_lite_podslice_v5",
            "legacyMetric": "TPU_LITE_PODSLICE_V5",
            "displayName": "TPU v5 Lite PodSlice chips (GCE)",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v5e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5e"]["zones"]),
            "notes": "Compute Engine regional quota for On-Demand/Reserved TPU v5e Lite PodSlice chips.",
        },
        {
            "id": "tpu-gce-v5e-podslice-spot",
            "category": "TPU",
            "isVmQuota": True,
            "chip": "v5e",
            "hardwareLabel": "GCE Preemptible TPU v5e Lite PodSlice Chips",
            "vmFamilies": ["TPU v5e"],
            "machineTypes": list(TPU_TYPES["v5e"]["machine_types"].keys()),
            "consumptionModels": ["spot", "dws_flex"],
            "service": "compute.googleapis.com",
            "metric": "compute.googleapis.com/preemptible_tpu_lite_podslice_v5",
            "legacyMetric": "PREEMPTIBLE_TPU_LITE_PODSLICE_V5",
            "displayName": "Preemptible TPU v5 Lite PodSlice chips (GCE)",
            "dimensions": None,
            "scope": "region",
            "unitLabel": "TPU chips",
            "supportedZones": TPU_TYPES["v5e"]["zones"],
            "supportedRegions": _regions_from_zones(TPU_TYPES["v5e"]["zones"]),
            "notes": "Compute Engine regional quota for Preemptible/Spot TPU v5e Lite PodSlice chips.",
        },
    ]
    return catalog


QUOTA_CATALOG = _build_quota_catalog()


# ---------------------------------------------------------------------------
# Live Quota Fetching & Merging
# ---------------------------------------------------------------------------

def _resolve_token(user_token: Optional[str]) -> str:
    if user_token:
        return user_token
    import google.auth
    import google.auth.transport.requests
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _extract_service_usage_locations(
    metric_obj: dict,
    required_dims: Optional[dict[str, str]],
    supported_regions: list[str],
    supported_zones: list[str],
    legacy_usage_by_loc: dict[str, dict[str, float]],
) -> list[dict]:
    """
    Extract per-location (global, region, or zone) effective limits from a
    Service Usage `consumerQuotaMetrics` object, filtering by `required_dims`
    if present (e.g. `{"gpu_family": "NVIDIA_B200"}`).
    """
    if not metric_obj:
        return []

    results_by_loc: dict[str, dict] = {}
    supported_loc_set = set(supported_regions) | set(supported_zones)

    for limit_entry in metric_obj.get("consumerQuotaLimits", []):
        unit = limit_entry.get("unit", "")
        buckets = limit_entry.get("quotaBuckets", [])
        api_supported_locs = limit_entry.get("supportedLocations", [])

        # Determine location key type from unit
        if "{zone}" in unit:
            loc_dim_key = "zone"
            candidate_locs = [z for z in supported_zones if z != "global"]
        elif "{region}" in unit:
            loc_dim_key = "region"
            candidate_locs = [r for r in supported_regions if r != "global"]
        else:
            loc_dim_key = None
            candidate_locs = ["global"]

        if api_supported_locs and loc_dim_key:
            api_set = set(api_supported_locs)
            filtered = [loc for loc in candidate_locs if loc in api_set]
            if filtered:
                candidate_locs = filtered

        default_limit: Optional[int] = None
        loc_overrides: dict[str, int] = {}

        for b in buckets:
            b_dims = b.get("dimensions", {})
            # Check if required_dims match
            if required_dims:
                matched = True
                for k, v in required_dims.items():
                    if b_dims.get(k) != v:
                        matched = False
                        break
                if not matched:
                    continue
            else:
                # Ignore buckets that have unexpected non-location dimensions
                extra_keys = set(b_dims.keys()) - {"region", "zone"}
                if extra_keys:
                    continue

            raw_eff = b.get("effectiveLimit", "0")
            try:
                eff_val = int(float(raw_eff))
            except (ValueError, TypeError):
                eff_val = 0

            # Ignore -1 unlimited placeholder on zonal buckets when regional quota governs
            if eff_val < 0:
                continue

            if loc_dim_key and loc_dim_key in b_dims:
                loc_name = b_dims[loc_dim_key]
                loc_overrides[loc_name] = eff_val
            elif not loc_dim_key and not b_dims:
                default_limit = eff_val
            elif loc_dim_key and loc_dim_key not in b_dims:
                # Bucket applies across all locations for this dimension
                default_limit = eff_val

        # If neither default_limit nor overrides were found (e.g. -1 zonal placeholder), skip
        if default_limit is None and not loc_overrides:
            continue

        base_limit = default_limit if default_limit is not None else 0

        if not loc_dim_key:
            loc_usage = legacy_usage_by_loc.get("global", {}).get("usage", 0.0)
            results_by_loc["global"] = {
                "location": "global",
                "scope": "global",
                "limit": base_limit,
                "usage": int(loc_usage),
            }
            continue

        # Populate all supported locations for this hardware + any location with explicit override > 0
        all_target_locs = sorted(set(candidate_locs) | {k for k, v in loc_overrides.items() if v > 0})
        for loc in all_target_locs:
            # Keep output focused on locations relevant to this hardware (or where override > 0)
            limit_val = loc_overrides.get(loc, base_limit)
            if loc not in supported_loc_set and limit_val <= 0:
                continue
            loc_usage = legacy_usage_by_loc.get(loc, {}).get("usage", 0.0)
            # If both regional and zonal limits exist (like TPU metrics), keep both or let specific win
            existing = results_by_loc.get(loc)
            if existing is None or limit_val > existing["limit"]:
                results_by_loc[loc] = {
                    "location": loc,
                    "scope": loc_dim_key,
                    "limit": limit_val,
                    "usage": int(loc_usage),
                }

    # If no finite buckets were found, check if the metric is explicitly Unlimited (-1)
    if not results_by_loc:
        for limit_entry in metric_obj.get("consumerQuotaLimits", []):
            unit = limit_entry.get("unit", "")
            buckets = limit_entry.get("quotaBuckets", [])
            if any(str(b.get("effectiveLimit", "")) == "-1" for b in buckets):
                loc_dim_key = "zone" if "{zone}" in unit else ("region" if "{region}" in unit else "global")
                target_locs = supported_zones if loc_dim_key == "zone" else (supported_regions if loc_dim_key == "region" else ["global"])
                for loc in target_locs:
                    results_by_loc[loc] = {
                        "location": loc,
                        "scope": loc_dim_key,
                        "limit": -1,
                        "unlimited": True,
                        "usage": 0,
                    }
                break

    return list(results_by_loc.values())


async def get_project_quotas(
    project: str,
    user_token: Optional[str] = None,
    refresh: bool = False,
) -> dict:
    """
    Return the full GPU/TPU quota catalog enriched with live per-region/zone
    limits, usage, and Cloud Console deep-link URLs for `project`.
    """
    now = time.time()
    if project and not refresh and project in _QUOTA_CACHE:
        ts, cached = _QUOTA_CACHE[project]
        if now - ts < _CACHE_TTL_SECONDS:
            return cached

    # If no project is provided yet, return the static catalog with console links
    if not project:
        items = []
        for entry in QUOTA_CATALOG:
            item = dict(entry)
            item["consoleUrl"] = build_console_quota_url(
                project="",
                service=entry["service"],
                metric=entry["metric"],
                dimensions=entry.get("dimensions"),
            )
            item["locations"] = []
            item["maxLimit"] = None
            item["totalUsage"] = 0
            item["nonZeroLocationsCount"] = 0
            items.append(item)
        return {"project": "", "liveLoaded": False, "quotas": items, "errors": []}

    token = await asyncio.to_thread(_resolve_token, user_token)
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "X-Goog-User-Project": project,
    }
    compute_headers = {
        "Authorization": f"Bearer {token}",
    }

    errors: list[str] = []
    tpu_metrics_map: dict[str, dict] = {}
    compute_su_metrics_map: dict[str, dict] = {}
    # legacy_metric -> {location -> {"limit": float, "usage": float}}
    legacy_compute_map: dict[str, dict[str, dict[str, float]]] = {}

    unique_compute_su_metrics = sorted({
        entry["metric"]
        for entry in QUOTA_CATALOG
        if entry["service"] == "compute.googleapis.com"
    })

    async with httpx.AsyncClient(timeout=20.0) as client:
        async def fetch_tpu_service_usage():
            page_token = ""
            while True:
                url = (
                    f"https://serviceusage.googleapis.com/v1beta1/projects/{project}"
                    f"/services/tpu.googleapis.com/consumerQuotaMetrics?pageSize=100"
                )
                if page_token:
                    url += f"&pageToken={urllib.parse.quote(page_token)}"
                try:
                    resp = await client.get(url, headers=auth_headers)
                    if resp.status_code != 200:
                        errors.append(f"TPU Service Usage API returned HTTP {resp.status_code}")
                        return
                    data = resp.json()
                    for m in data.get("metrics", []):
                        m_name = m.get("metric")
                        if m_name:
                            tpu_metrics_map[m_name] = m
                    page_token = data.get("nextPageToken", "")
                    if not page_token:
                        break
                except Exception as exc:
                    errors.append(f"Failed to query TPU quotas: {exc}")
                    return

        async def fetch_compute_global():
            url = f"https://compute.googleapis.com/compute/v1/projects/{project}"
            try:
                resp = await client.get(url, headers=compute_headers)
                if resp.status_code == 200:
                    data = resp.json()
                    for q in data.get("quotas", []):
                        m = q.get("metric")
                        if m:
                            legacy_compute_map.setdefault(m, {})["global"] = {
                                "limit": float(q.get("limit", 0)),
                                "usage": float(q.get("usage", 0)),
                            }
            except Exception as exc:
                logger.warning(f"Compute global quota fetch warning: {exc}")

        async def fetch_compute_regions():
            url = f"https://compute.googleapis.com/compute/v1/projects/{project}/regions"
            try:
                resp = await client.get(url, headers=compute_headers)
                if resp.status_code == 200:
                    data = resp.json()
                    for reg in data.get("items", []):
                        r_name = reg.get("name")
                        if not r_name:
                            continue
                        for q in reg.get("quotas", []):
                            m = q.get("metric")
                            if m:
                                legacy_compute_map.setdefault(m, {})[r_name] = {
                                    "limit": float(q.get("limit", 0)),
                                    "usage": float(q.get("usage", 0)),
                                }
            except Exception as exc:
                logger.warning(f"Compute regional quota fetch warning: {exc}")

        sem = asyncio.Semaphore(10)

        async def fetch_single_compute_su_metric(metric_name: str):
            enc = urllib.parse.quote(metric_name, safe="")
            url = (
                f"https://serviceusage.googleapis.com/v1beta1/projects/{project}"
                f"/services/compute.googleapis.com/consumerQuotaMetrics/{enc}"
            )
            async with sem:
                try:
                    resp = await client.get(url, headers=auth_headers)
                    if resp.status_code == 200:
                        compute_su_metrics_map[metric_name] = resp.json()
                except Exception as exc:
                    logger.debug(f"Metric {metric_name} fetch skipped: {exc}")

        await asyncio.gather(
            fetch_tpu_service_usage(),
            fetch_compute_global(),
            fetch_compute_regions(),
            *(fetch_single_compute_su_metric(m) for m in unique_compute_su_metrics),
        )

    # Enrich each catalog item
    enriched_items: list[dict] = []
    for entry in QUOTA_CATALOG:
        item = dict(entry)
        service = entry["service"]
        metric = entry["metric"]
        legacy_metric = entry.get("legacyMetric")
        dims = entry.get("dimensions")
        sup_regions = entry.get("supportedRegions", [])
        sup_zones = entry.get("supportedZones", [])

        legacy_usage_by_loc = legacy_compute_map.get(legacy_metric, {}) if legacy_metric else {}

        metric_obj = (
            tpu_metrics_map.get(metric)
            if service == "tpu.googleapis.com"
            else compute_su_metrics_map.get(metric)
        )

        locations = _extract_service_usage_locations(
            metric_obj=metric_obj,
            required_dims=dims,
            supported_regions=sup_regions,
            supported_zones=sup_zones,
            legacy_usage_by_loc=legacy_usage_by_loc,
        )

        # Fallback to Compute Engine regions/global quota data if Service Usage didn't return buckets
        if not locations and legacy_usage_by_loc:
            target_locs = ["global"] if entry["scope"] == "global" else sup_regions
            for loc in sorted(set(target_locs) | set(legacy_usage_by_loc.keys())):
                if loc not in legacy_usage_by_loc:
                    continue
                lim = int(legacy_usage_by_loc[loc]["limit"])
                usg = int(legacy_usage_by_loc[loc]["usage"])
                if loc not in target_locs and lim <= 0 and usg <= 0:
                    continue
                locations.append({
                    "location": loc,
                    "scope": "global" if loc == "global" else "region",
                    "limit": lim,
                    "usage": usg,
                })

        # Attach console URL to each location and sort: non-zero limit first, then location name
        for loc_entry in locations:
            loc_entry["consoleUrl"] = build_console_quota_url(
                project=project,
                service=service,
                metric=metric,
                location=loc_entry["location"],
                dimensions=dims,
            )

        locations.sort(
            key=lambda x: (
                -2 if x.get("unlimited") else (-1 if x["limit"] > 0 else 0),
                -x["limit"],
                x["location"],
            )
        )

        is_unlimited = any(loc.get("unlimited") for loc in locations)
        non_zero = [loc for loc in locations if loc["limit"] > 0 or loc.get("unlimited")]
        max_limit = -1 if is_unlimited else (max((loc["limit"] for loc in locations), default=0) if locations else 0)
        total_usage = sum(loc["usage"] for loc in locations)

        item["consoleUrl"] = build_console_quota_url(
            project=project,
            service=service,
            metric=metric,
            dimensions=dims,
        )
        item["locations"] = locations
        item["isUnlimited"] = is_unlimited
        item["maxLimit"] = max_limit
        item["totalUsage"] = total_usage
        item["nonZeroLocationsCount"] = len(non_zero)
        enriched_items.append(item)

    result = {
        "project": project,
        "liveLoaded": True,
        "quotas": enriched_items,
        "errors": errors,
    }
    _QUOTA_CACHE[project] = (now, result)
    return result
