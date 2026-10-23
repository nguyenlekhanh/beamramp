"""Expand user settings into validated per-seed render jobs (variations).

Reproducibility contract:
  * every job pins exactly one (template_id, seed) pair
  * the full effective config dict is stored on the job
  * the same seed + config builds the same scenario wherever the layout
    generator is deterministic (it is: build_layout is seeded per template)
Geometry/physics are never randomized here; the seed only feeds the same
layout+color pipeline the single-render path already used.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..services.render_service import template_by_id, validate_config
from ..services import preset_service

MAX_BATCH_JOBS = 50
DEFAULT_SEED_STEP = 111


class VariationError(ValueError):
    """Raised when a variation plan cannot be built or validated."""


def clamp_count(template_id: str, vehicle_count: Optional[int]) -> int:
    tpl = template_by_id(template_id)
    if tpl is None:
        raise VariationError(f"unknown template: {template_id!r}")
    requested = int(vehicle_count or tpl["vehicles"])
    return max(1, min(requested, int(tpl.get("max_vehicles", requested))))


def build_variation_config(base_cfg: Dict[str, Any], template_id: str, seed: int,
                           batch_id: str) -> Dict[str, Any]:
    """One effective render config: base + preset + template/seed pinned."""
    cfg = dict(base_cfg)
    tpl = template_by_id(template_id)
    if tpl is None:
        raise VariationError(f"unknown template: {template_id!r}")
    cfg["template_id"] = template_id
    cfg["seed"] = int(seed)
    count = clamp_count(template_id, cfg.get("vehicle_count"))
    cfg["vehicle_count"] = count
    cfg["file_name"] = f"{template_id}_s{seed}_{batch_id}.mp4"
    if cfg.get("duration") is None:
        cfg.pop("duration", None)  # template default
    return cfg


def expand_variations(base_cfg: Dict[str, Any], *, template_ids: List[str],
                      variations: int, seed: int, seed_step: int,
                      batch_id: str, batch_name: str,
                      job_factory=None):
    """Validate once, then produce JobRecord objects for the queue.

    ``job_factory(batch_id, name, template_id, template_name, seed, config,
    preset)`` builds jobs (kept injectable for tests). Returns the batch name
    plus the validated job list.
    """
    from .jobs import JobRecord, JobError, new_job_id

    if job_factory is None:
        def job_factory(batch_id, name, template_id, template_name, seed_val,
                        config, preset_name):
            return JobRecord(
                job_id=new_job_id(), batch_id=batch_id, name=name,
                template_id=template_id, template_name=template_name,
                seed=seed_val, config=config, preset=preset_name)

    variations = int(variations)
    if not 1 <= variations <= MAX_BATCH_JOBS:
        raise VariationError(f"variations must be within 1..{MAX_BATCH_JOBS} (got {variations})")
    seed = int(seed)
    seed_step = int(seed_step) if seed_step else DEFAULT_SEED_STEP
    if seed_step <= 0 or abs(seed_step) > 1_000_000:
        raise VariationError(f"seed step must be within 1..1000000 (got {seed_step})")
    if not template_ids:
        raise VariationError("no template selected")

    # preset reference (name recorded per job; settings are baked into cfg)
    preset_name = str(base_cfg.get("preset_name") or "")
    clean = dict(base_cfg)
    clean.pop("preset_name", None)
    clean = {k: v for k, v in clean.items() if not k.startswith("_")}

    jobs = []
    pairs_seen: set = set()
    for template_id in template_ids:
        for i in range(variations):
            seed_i = seed + i * seed_step
            pair = (template_id, seed_i)
            if pair in pairs_seen:
                raise VariationError(
                    f"duplicate job ({template_id}, seed {seed_i}) produced; increase the seed step")
            pairs_seen.add(pair)
            cfg = build_variation_config(clean, template_id, seed_i, batch_id)
            try:
                validate_config(cfg)
            except Exception as exc:
                raise VariationError(
                    f"variation {template_id} seed {seed_i} failed validation: {exc}") from exc
            tpl = template_by_id(template_id)
            jobs.append(job_factory(
                batch_id, f"{tpl['name']} #{i + 1}", template_id, tpl["name"],
                seed_i, cfg, preset_name))
    total_jobs = len(template_ids) * variations
    if total_jobs > MAX_BATCH_JOBS:
        raise VariationError(
            f"batch too large: {total_jobs} jobs (max {MAX_BATCH_JOBS})")
    return batch_name, jobs
