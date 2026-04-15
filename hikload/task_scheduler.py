import json
import logging
import subprocess


logger = logging.getLogger("QueueRuntime")

WORKER_TASK_NAME = "HikLoad Worker"
ACTIVE_TASK_STATES = {"running", "queued"}


class TaskSchedulerError(RuntimeError):
    pass


def _powershell_literal(value):
    return value.replace("'", "''")


def get_task_info(task_name=WORKER_TASK_NAME):
    escaped_name = _powershell_literal(task_name)
    script = """
$task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue
if ($null -eq $task) {{
  [PSCustomObject]@{{
    Exists = $false
    TaskName = '{task_name}'
    TaskPath = $null
    State = 'NotFound'
    LastTaskResult = $null
    LastRunTime = $null
    NextRunTime = $null
  }} | ConvertTo-Json -Compress
  exit 0
}}
$info = $task | Get-ScheduledTaskInfo
[PSCustomObject]@{{
  Exists = $true
  TaskName = $task.TaskName
  TaskPath = $task.TaskPath
  State = [string]$task.State
  LastTaskResult = $info.LastTaskResult
  LastRunTime = if ($info.LastRunTime -and $info.LastRunTime -ne [datetime]::MinValue) {{ $info.LastRunTime.ToString('o') }} else {{ $null }}
  NextRunTime = if ($info.NextRunTime -and $info.NextRunTime -ne [datetime]::MinValue) {{ $info.NextRunTime.ToString('o') }} else {{ $null }}
}} | ConvertTo-Json -Compress
""".format(task_name=escaped_name)

    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=None,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise TaskSchedulerError(
            "Failed to query Task Scheduler task '{}': {}".format(
                task_name,
                completed.stderr.strip() or completed.stdout.strip() or completed.returncode,
            )
        )

    output = completed.stdout.strip()
    if not output:
        raise TaskSchedulerError("Task Scheduler returned no data for task '{}'".format(task_name))

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise TaskSchedulerError(
            "Failed to parse Task Scheduler JSON for task '{}': {}".format(task_name, output)
        ) from exc

    return data


def is_task_active(task_info):
    state = (task_info or {}).get("State") or ""
    return state.lower() in ACTIVE_TASK_STATES


def trigger_task(task_name=WORKER_TASK_NAME):
    completed = subprocess.run(
        ["schtasks", "/run", "/tn", task_name],
        cwd=None,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise TaskSchedulerError(
            "Failed to start Task Scheduler task '{}': {}".format(
                task_name,
                completed.stderr.strip() or completed.stdout.strip() or completed.returncode,
            )
        )

    logger.info("Requested Task Scheduler run for task '%s'", task_name)
    return completed.stdout.strip()
