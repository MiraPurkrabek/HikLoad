import logging
import math
import os
from datetime import datetime, timezone

from hikload.command_queue import CAMERAS_FOR_HARDDISK_SAVE
from hikload.runtime_queue import STATE_ROOT, atomic_write_yaml, get_runtime_root


logger = logging.getLogger("EtaEstimator")

ETA_STATS_FILENAME = "eta_stats.yml"
ETA_SAMPLE_WINDOW = 10
DEFAULT_RUNTIME_PER_10MIN = 5.0
ETA_RANGE_FRACTION = 0.20
DEFAULT_CAMERA_SET = [camera.strip() for camera in CAMERAS_FOR_HARDDISK_SAVE.split(",") if camera.strip()]


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def get_eta_stats_path(state_root=STATE_ROOT):
    return os.path.join(get_runtime_root(state_root), ETA_STATS_FILENAME)


def _default_stats():
    return {
        "version": 1,
        "sample_window": ETA_SAMPLE_WINDOW,
        "default_runtime_per_10min": DEFAULT_RUNTIME_PER_10MIN,
        "updated_at": None,
        "cameras": {},
    }


def _parse_datetime(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


def _normalize_camera_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        return []
    return [str(camera).strip().upper() for camera in candidates if str(camera).strip()]


def _normalize_camera_runtime_map(camera_processing_seconds):
    normalized = {}
    for camera_name, runtime_seconds in (camera_processing_seconds or {}).items():
        key = str(camera_name).strip().upper()
        if not key:
            continue
        try:
            runtime_value = float(runtime_seconds)
        except (TypeError, ValueError):
            continue
        if runtime_value < 0:
            continue
        normalized[key] = runtime_value
    return normalized


def _get_command_source(record):
    return record.get("command") or record.get("command_summary") or {}


def get_effective_cameras(record):
    if bool(record.get("harddisk_save")):
        return list(DEFAULT_CAMERA_SET)

    command = _get_command_source(record)
    cameras = _normalize_camera_list(command.get("cameras"))
    if cameras:
        return cameras

    logger.warning(
        "Job '%s' has no explicit cameras; ETA will conservatively assume %s",
        record.get("job_id"),
        ",".join(DEFAULT_CAMERA_SET),
    )
    return list(DEFAULT_CAMERA_SET)


def get_job_duration_minutes(record):
    command = _get_command_source(record)
    start_value = command.get("starttime")
    end_value = command.get("endtime")
    if not start_value or not end_value:
        return 0.0

    try:
        start_dt = _parse_datetime(start_value)
        end_dt = _parse_datetime(end_value)
    except Exception:
        logger.warning(
            "Failed to parse duration timestamps for job '%s': start=%r end=%r",
            record.get("job_id"),
            start_value,
            end_value,
        )
        return 0.0

    duration_minutes = (end_dt - start_dt).total_seconds() / 60.0
    if duration_minutes <= 0:
        logger.warning(
            "Job '%s' has non-positive requested duration (%s minutes); ETA contribution will be zero",
            record.get("job_id"),
            duration_minutes,
        )
        return 0.0
    return duration_minutes


def load_eta_stats(state_root=STATE_ROOT):
    path = get_eta_stats_path(state_root)
    if not os.path.exists(path):
        return _default_stats()

    try:
        import yaml

        with open(path, "r", encoding="utf-8") as fl:
            raw = yaml.safe_load(fl) or {}
    except Exception as exc:
        logger.warning("Failed to load ETA stats from '%s': %s. Falling back to defaults.", path, exc)
        return _default_stats()

    stats = _default_stats()
    if isinstance(raw, dict):
        default_runtime = raw.get("default_runtime_per_10min")
        if isinstance(default_runtime, (int, float)) and default_runtime > 0:
            stats["default_runtime_per_10min"] = float(default_runtime)

        sample_window = raw.get("sample_window")
        if isinstance(sample_window, int) and sample_window > 0:
            stats["sample_window"] = sample_window

        stats["updated_at"] = raw.get("updated_at")

        cameras = raw.get("cameras") or {}
        if isinstance(cameras, dict):
            normalized_cameras = {}
            for camera_name, payload in cameras.items():
                camera_key = str(camera_name).strip().upper()
                samples = []
                if isinstance(payload, dict):
                    for sample in payload.get("samples") or []:
                        if isinstance(sample, (int, float)) and sample > 0:
                            samples.append(float(sample))
                if samples:
                    normalized_cameras[camera_key] = {"samples": samples[-stats["sample_window"]:]}
            stats["cameras"] = normalized_cameras

    return stats


def save_eta_stats(stats, state_root=STATE_ROOT):
    path = get_eta_stats_path(state_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_yaml(path, stats)


def get_camera_coefficients(stats):
    coefficients = {}
    for camera_name, payload in (stats.get("cameras") or {}).items():
        samples = payload.get("samples") or []
        if samples:
            coefficients[camera_name] = sum(samples) / len(samples)
    return coefficients


def summarize_camera_coefficients(stats):
    summary = {}
    default_runtime = float(stats.get("default_runtime_per_10min", DEFAULT_RUNTIME_PER_10MIN))
    coefficients = get_camera_coefficients(stats)

    for camera_name in sorted(set(DEFAULT_CAMERA_SET) | set(coefficients.keys()) | set((stats.get("cameras") or {}).keys())):
        samples = ((stats.get("cameras") or {}).get(camera_name) or {}).get("samples") or []
        summary[camera_name] = {
            "runtime_per_10min": round(coefficients.get(camera_name, default_runtime), 3),
            "sample_count": len(samples),
            "using_default": camera_name not in coefficients,
        }
    return summary


def estimate_job_minutes(record, stats):
    default_runtime = float(stats.get("default_runtime_per_10min", DEFAULT_RUNTIME_PER_10MIN))
    coefficients = get_camera_coefficients(stats)
    duration_minutes = get_job_duration_minutes(record)
    cameras = get_effective_cameras(record)

    total_minutes = 0.0
    per_camera_minutes = {}
    used_defaults = []
    used_coefficients = {}

    if duration_minutes <= 0 or not cameras:
        return {
            "job_id": record.get("job_id"),
            "duration_minutes": duration_minutes,
            "cameras": cameras,
            "total_minutes": 0.0,
            "per_camera_minutes": per_camera_minutes,
            "coefficients": used_coefficients,
            "default_cameras": used_defaults,
        }

    factor = duration_minutes / 10.0
    for camera_name in cameras:
        coefficient = coefficients.get(camera_name, default_runtime)
        if camera_name not in coefficients:
            used_defaults.append(camera_name)
        contribution = factor * coefficient
        used_coefficients[camera_name] = coefficient
        per_camera_minutes[camera_name] = contribution
        total_minutes += contribution

    return {
        "job_id": record.get("job_id"),
        "duration_minutes": duration_minutes,
        "cameras": cameras,
        "total_minutes": total_minutes,
        "per_camera_minutes": per_camera_minutes,
        "coefficients": used_coefficients,
        "default_cameras": sorted(set(used_defaults)),
    }


def estimate_jobs_minutes(records, stats):
    total_minutes = 0.0
    default_cameras = set()
    job_estimates = []
    for record in records:
        estimate = estimate_job_minutes(record, stats)
        total_minutes += estimate["total_minutes"]
        default_cameras.update(estimate["default_cameras"])
        job_estimates.append(estimate)

    return {
        "total_minutes": total_minutes,
        "default_cameras": sorted(default_cameras),
        "jobs": job_estimates,
    }


def build_eta_range_minutes(estimated_total_minutes):
    safe_total = max(1.0, float(estimated_total_minutes))
    lower = max(1, int(math.floor(safe_total * (1.0 - ETA_RANGE_FRACTION))))
    upper = max(lower, int(math.ceil(safe_total * (1.0 + ETA_RANGE_FRACTION))))
    return lower, upper


def update_eta_stats_from_successful_job(job, finished_at, camera_processing_seconds=None, state_root=STATE_ROOT):
    stats = load_eta_stats(state_root=state_root)
    started_at = job.get("started_at")
    if not started_at or not finished_at:
        logger.warning(
            "Skipping ETA stats update for job '%s' because started_at/finished_at is missing",
            job.get("job_id"),
        )
        return None

    try:
        started_dt = _parse_datetime(started_at)
        finished_dt = _parse_datetime(finished_at)
    except Exception as exc:
        logger.warning(
            "Skipping ETA stats update for job '%s' because runtime timestamps are invalid: %s",
            job.get("job_id"),
            exc,
        )
        return None

    total_runtime_minutes = (finished_dt - started_dt).total_seconds() / 60.0
    if total_runtime_minutes <= 0:
        logger.warning(
            "Skipping ETA stats update for job '%s' because runtime is non-positive (%s minutes)",
            job.get("job_id"),
            total_runtime_minutes,
        )
        return None

    duration_minutes = get_job_duration_minutes(job)
    cameras = get_effective_cameras(job)
    if duration_minutes <= 0 or not cameras:
        logger.warning(
            "Skipping ETA stats update for job '%s' because duration=%s and cameras=%s are not usable",
            job.get("job_id"),
            duration_minutes,
            cameras,
        )
        return None

    sample_window = int(stats.get("sample_window", ETA_SAMPLE_WINDOW))
    normalized_runtime_seconds = _normalize_camera_runtime_map(camera_processing_seconds)
    exact_camera_minutes = {}
    missing_camera_metrics = [camera for camera in cameras if camera not in normalized_runtime_seconds]

    if normalized_runtime_seconds and not missing_camera_metrics:
        for camera_name in cameras:
            exact_camera_minutes[camera_name] = normalized_runtime_seconds[camera_name] / 60.0
        logger.info(
            "Updating ETA stats from successful job '%s' using exact per-camera runtimes: total_runtime=%.2f min, duration=%.2f min, cameras=%s",
            job.get("job_id"),
            total_runtime_minutes,
            duration_minutes,
            {camera: round(exact_camera_minutes[camera], 3) for camera in cameras},
        )
    else:
        per_camera_runtime_minutes = total_runtime_minutes / len(cameras)
        for camera_name in cameras:
            exact_camera_minutes[camera_name] = per_camera_runtime_minutes
        if normalized_runtime_seconds:
            logger.warning(
                "ETA stats for job '%s' are missing exact runtime metrics for cameras %s; falling back to equal split across %s",
                job.get("job_id"),
                missing_camera_metrics,
                cameras,
            )
        else:
            logger.info(
                "Updating ETA stats from successful job '%s' using equal split fallback: total_runtime=%.2f min, duration=%.2f min, cameras=%s",
                job.get("job_id"),
                total_runtime_minutes,
                duration_minutes,
                cameras,
            )

    cameras_store = stats.setdefault("cameras", {})
    for camera_name in cameras:
        normalized_sample = (exact_camera_minutes[camera_name] / duration_minutes) * 10.0
        payload = cameras_store.setdefault(camera_name, {"samples": []})
        payload["samples"].append(normalized_sample)
        payload["samples"] = payload["samples"][-sample_window:]
        logger.info(
            "ETA stats sample recorded for camera '%s': runtime=%.2f min, sample_per_10min=%.3f, recent_samples=%s",
            camera_name,
            exact_camera_minutes[camera_name],
            normalized_sample,
            [round(sample, 3) for sample in payload["samples"]],
        )

    stats["updated_at"] = _utcnow_iso()
    save_eta_stats(stats, state_root=state_root)
    return {
        "runtime_minutes": total_runtime_minutes,
        "duration_minutes": duration_minutes,
        "cameras": cameras,
        "camera_runtime_minutes": exact_camera_minutes,
    }
