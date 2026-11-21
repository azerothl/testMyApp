"""Différences entre deux rapports d’un même projet."""

from pathlib import PurePosixPath


def execution_context(report: dict, manifest: dict) -> dict:
    evidence = list(report.get("evidence", {}).values())
    browsers = sorted({(event["data"]["browser"], event["data"]["version"]) for event in evidence
                       if event["type"] == "target.environment"})
    dependent = {event["session_id"] for event in evidence if event["type"] == "session.started"
                 and event["data"].get("after_tasks")}
    starts = {(event["session_id"], event["step_id"]): event["data"].get("intent") for event in evidence
              if event["type"] == "step.started" and event["data"].get("precondition")
              and event["session_id"] not in dependent}
    data = []
    labels = {}
    for event in evidence:
        key = (event["session_id"], event["step_id"])
        if key not in starts or event["type"] not in {"step.passed", "step.failed"}:
            continue
        for check in event["data"].get("evidence", []):
            detail = check.get("detail", {})
            data.append({"session": event["session_id"], "step": event["step_id"] + 1,
                         "criterion": check.get("expect"), "ok": check.get("ok"),
                         "count": detail.get("count"), "expected_count": detail.get("expected_count"),
                         "status": detail.get("fields", {}).get("status")})
            labels[f"{event['session_id']}:{event['step_id'] + 1}"] = starts[key]
    data.sort(key=lambda item: (item["session"], item["step"], item["criterion"] or ""))
    return {"app_version": manifest.get("app_version"), "configuration": manifest.get("config_hash"),
            "browsers": [" ".join(pair) for pair in browsers],
            "target_type": report.get("campaign", {}).get("target_type"), "starting_data": data,
            "starting_labels": labels}


def _steps(events: list[dict]) -> dict:
    steps = {}
    for event in events:
        index = event.get("step_id")
        if index is None:
            continue
        row = steps.setdefault(index, {"label": "", "status": None, "checks": [], "capture": None, "reason": None})
        data = event["data"]
        if event["type"] == "step.started":
            row["label"] = data.get("intent", "")
        elif event["type"] == "step.screenshot" and data.get("path"):
            path = PurePosixPath(data["path"])
            if path.parent == PurePosixPath("screenshots") and (not row["capture"] or data.get("phase") == "after"):
                row["capture"] = path.name
        elif event["type"] in {"step.passed", "step.failed", "step.warned", "step.skipped"}:
            row["status"] = event["type"].removeprefix("step.")
            row["label"] = row["label"] or data.get("intent", "")
            row["reason"] = data.get("reason")
            row["checks"] = [[check.get("expect"), check.get("ok"), check.get("detail", {}).get("observed_failure")]
                             for check in data.get("evidence", [])]
    return steps


def _step_differences(left: dict, right: dict) -> list[dict]:
    result = []
    for index in sorted(left.keys() | right.keys()):
        before, after = left.get(index), right.get(index)
        signatures = [None if row is None else (row["status"], row["checks"]) for row in (before, after)]
        result.append({"number": index + 1, "before": before, "after": after,
                       "changed": signatures[0] != signatures[1]})
    return result


def compare_reports(before: dict, after: dict) -> dict:
    def summary(report: dict) -> dict:
        return {key: report.get(key) for key in ("status", "score")}

    def sessions(report: dict) -> dict:
        rows = {}
        evidence = report.get("evidence", {})
        for row in report.get("sessions", []):
            scenario = row["scenario"]
            events = [evidence[ref] for ref in row.get("evidence_refs", []) if ref in evidence]
            start = next((event["data"] for event in events if event.get("type") == "session.started"), {})
            label = start.get("scenario_name") or scenario
            goal = label if scenario.startswith("decouverte-") else ""
            rows[(row["persona"], scenario, goal)] = (row["outcome"], label, _steps(events))
        return rows

    def findings(report: dict) -> set[str]:
        return {row["title"] for row in report.get("negatives", [])}

    left, right = sessions(before), sessions(after)
    old_findings, new_findings = findings(before), findings(after)
    empty = (None, None, {})
    return {
        "before": summary(before), "after": summary(after),
        "same_campaign": before.get("campaign", {}).get("id") == after.get("campaign", {}).get("id"),
        "sessions": [
            {"persona": key[0], "scenario": key[1], "label": (right.get(key) or left[key])[1],
             "before": left.get(key, empty)[0], "after": right.get(key, empty)[0],
             "steps": _step_differences(left.get(key, empty)[2], right.get(key, empty)[2])}
            for key in sorted(left.keys() | right.keys())
        ],
        "new_findings": sorted(new_findings - old_findings),
        "missing_findings": sorted(old_findings - new_findings),
    }
