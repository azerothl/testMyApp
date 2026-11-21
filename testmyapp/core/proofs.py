"""Résolution et vérification des critères structurés d’une campagne."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TOKEN = re.compile(r"\{\{([a-z_][a-z_0-9]*)\}\}")


def run_parameters(name: str) -> dict[str, str]:
    try:
        day = datetime.strptime(name.split("-", 1)[0], "%Y%m%d").date()
    except ValueError:
        day = datetime.now(ZoneInfo("Europe/Paris")).date()
    return {"run_suffix": name.rsplit("-", 1)[-1], "tomorrow": (day + timedelta(days=1)).isoformat()}


def expand(value: str, parameters: dict[str, str]) -> str:
    return TOKEN.sub(lambda match: parameters.get(match[1], match[0]), value)


def check_records(data: dict, rule: dict, parameters: dict[str, str]) -> tuple[bool, dict]:
    filters = {key: expand(value, parameters) if isinstance(value, str) else value
               for key, value in rule["match"].items()}
    rows = data.get(rule["collection"])
    result = {"endpoint": rule["endpoint"].split("?", 1)[0], "expected_count": rule.get("count", 1),
              "fields": filters, "count": None}
    if not isinstance(rows, list) or any(TOKEN.search(value) for value in filters.values() if isinstance(value, str)):
        return False, result
    # A first page alone cannot prove uniqueness when the source advertises more rows.
    if any(data.get(field) is not None for field in ("next_cursor", "next_offset")):
        return False, {**result, "error": "La réponse est paginée"}
    matches = [row for row in rows if isinstance(row, dict)
               and all(key in row and row[key] == value for key, value in filters.items())]
    result["count"] = len(matches)
    ok = len(matches) == result["expected_count"]
    if ok and rule.get("save_as"):
        source = matches[0].get(rule.get("record_key", "id")) if len(matches) == 1 else None
        if not isinstance(source, str) or not source:
            return False, result
        result["proof"] = {rule["save_as"]: source}
    return ok, result
