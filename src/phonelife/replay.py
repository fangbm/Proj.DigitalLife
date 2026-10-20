"""Offline replay has no device adapter and never sends ADB commands."""
from __future__ import annotations

from types import SimpleNamespace
from .llm import ModelOutputError, parse_object
from .models import Action, ValidationError
from .policy import ActionPolicy, PolicyDenied


def replay_task(store, run_id: str) -> dict:
    task = store.task(run_id)
    replayed = []
    for attempt in task["attempts"]:
        payload = attempt["payload"]
        row = {"attempt_id": attempt["attempt_id"], "step_index": attempt["step_index"],
               "generation_attempt": attempt["generation_attempt"],
               "recorded_validation": payload.get("validation_status"),
               "recorded_execution": payload.get("execution_status"), "device_execution": "disabled"}
        candidate = payload.get("candidate_action")
        if candidate and candidate.get("type") == "text":
            row["replay_validation"] = "unavailable_redacted_text"
        else:
            try:
                # Invalid responses deliberately have no stored raw prose; replay their recorded rejection.
                if candidate is None:
                    excerpt = payload.get("model_response_excerpt", "")
                    if excerpt.startswith("[REDACTED") or not excerpt:
                        row["replay_validation"] = "recorded_rejection_metadata_only"
                        replayed.append(row)
                        continue
                    candidate = parse_object(excerpt, allow_fences=False)
                action = Action.from_dict(candidate)
                observation = SimpleNamespace(width=payload["width"], height=payload["height"],
                                               observation_id=payload["observation_id"])
                policy = ActionPolicy(task["metadata"].get("device_kind", "emulator"),
                                      frozenset(task["metadata"].get("allowed_apps", [])))
                policy.check(action.bind(observation), observation)
                row["replay_validation"] = "valid"
            except (ValidationError, ModelOutputError, PolicyDenied, KeyError) as error:
                row["replay_validation"] = "rejected"
                row["errors"] = getattr(error, "errors", [{"code": getattr(error, "code", "REPLAY_INVALID")}])
        replayed.append(row)
    result = task.get("result") or {}
    verified = (result.get("verification") or {}).get("verdict") == "verified"
    interpreted = store.interpret_legacy_status(task["status"])
    if interpreted == "succeeded" and not verified:
        interpreted = "uncertain"
    return {"mode": "dry-run", "task_run_id": run_id, "adb_actions_sent": 0,
            "recorded_status": task["status"], "interpreted_status": interpreted, "attempts": replayed}
