from fastapi import FastAPI, HTTPException, Query
import uvicorn
import subprocess
import json
import re
from typing import Optional, Any

app = FastAPI()


@app.get("/")
def read_root():
    return {"status": "ok"}


def run_dhapi(args: list[str]) -> dict:
    try:
        result = subprocess.run(
            ["dhapi", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="dhapi command not found") from exc

    payload = {
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=payload)

    return payload


def _split_table_row(line: str) -> list[str]:
    normalized = line.replace("┃", "│")
    parts = [p.strip() for p in normalized.strip().strip("│").split("│")]
    return parts


def _parse_amount(value: str) -> int | str | None:
    cleaned = value.replace("원", "").replace(",", "").strip()
    if cleaned == "-" or cleaned == "":
        return None
    if cleaned.isdigit():
        return int(cleaned)
    return value


def _normalize_date(value: str) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else None


def _normalize_text(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value.replace("\n", " ")).strip()
    return collapsed


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d]", "", str(value))
    return int(cleaned) if cleaned.isdigit() else None


def _normalize_buy_item(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    if "buy_date" in normalized:
        normalized["buy_date"] = _normalize_date(str(normalized["buy_date"]))
    if "drawing_date" in normalized:
        normalized["drawing_date"] = _normalize_date(str(normalized["drawing_date"]))
    if "round" in normalized:
        normalized["round"] = _parse_int(normalized["round"])
    if "buy_counts" in normalized:
        normalized["buy_counts"] = _parse_int(normalized["buy_counts"])
    if "winnings" in normalized:
        normalized["winnings"] = _parse_amount(str(normalized["winnings"]))
    if "text" in normalized and normalized["text"] is not None:
        normalized["text"] = _normalize_text(str(normalized["text"]))
    return normalized


BALANCE_HEADER_MAP = {
    "총예치금": "total",
    "구매가능금액": "available",
    "예약구매금액": "reserved",
    "출금신청중금액": "withdraw_pending",
    "구매불가능금액": "unavailable",
    "최근1달누적구매금액": "total_month_usage",
}

BUY_LIST_HEADER_MAP = {
    "구입일자": "buy_date",
    "복권명": "name",
    "회차": "round",
    "선택번호/복권번호": "text",
    "구입매수": "buy_counts",
    "당첨결과": "result",
    "당첨금": "winnings",
    "추첨일": "drawing_date",
}


def _map_headers(headers: list[str], mapping: dict[str, str]) -> list[str]:
    mapped = []
    for header in headers:
        normalized = header.replace("…", "").replace(".", "").replace(" ", "").strip()
        if normalized in mapping:
            mapped.append(mapping[normalized])
            continue
        # Fallback: prefix match for truncated headers
        key = next((k for k in mapping.keys() if normalized and k.startswith(normalized)), None)
        if key:
            mapped.append(mapping[key])
        else:
            mapped.append(mapping.get(header, header))
    return mapped


def parse_balance_output(stdout: str) -> dict[str, Any]:
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    message = lines[0] if lines else ""
    note = ""
    if lines and lines[-1].startswith("(") and lines[-1].endswith(")"):
        note = lines[-1]

    header_line = next((ln for ln in lines if ln.startswith("┃")), "")
    data_line = ""
    for ln in lines[lines.index(header_line) + 1 :] if header_line in lines else []:
        if ln.startswith("│"):
            data_line = ln
            break

    if not header_line or not data_line:
        return {"message": message, "raw": stdout, "note": note}

    headers = _map_headers(_split_table_row(header_line), BALANCE_HEADER_MAP)
    values = _split_table_row(data_line)
    data: dict[str, Any] = {}
    for i in range(len(headers)):
        key = headers[i]
        raw_val = values[i] if i < len(values) else ""
        data[key] = _parse_amount(raw_val)
    result: dict[str, Any] = {"message": message, "data": data}
    if note:
        result["note"] = note
    return result


def parse_buy_list_output(stdout: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, list):
            items = [_normalize_buy_item(_map_buy_item_fields(p)) for p in parsed]
            return {"items": items}
        if isinstance(parsed, dict):
            raw_items = parsed.get("items")
            if isinstance(raw_items, list):
                items = [_normalize_buy_item(_map_buy_item_fields(p)) for p in raw_items]
                parsed["items"] = items
            return parsed
    except json.JSONDecodeError:
        pass

    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    message = lines[0] if lines else ""

    range_start = None
    range_end = None
    if "(" in message and "~" in message and ")" in message:
        try:
            range_part = message.split("(", 1)[1].split(")", 1)[0]
            range_start, range_end = [p.strip() for p in range_part.split("~", 1)]
        except ValueError:
            range_start, range_end = None, None

    header_line = next((ln for ln in lines if ln.startswith("┃")), "")
    if not header_line:
        return {"message": message, "raw": stdout}

    headers = _map_headers(_split_table_row(header_line), BUY_LIST_HEADER_MAP)
    rows: list[dict[str, Any]] = []

    in_rows = False
    for ln in lines:
        if ln.startswith("┡"):
            in_rows = True
            continue
        if not in_rows:
            continue
        if not ln.startswith("│"):
            continue

        values = _split_table_row(ln)
        if not values:
            continue

        first_col = values[0] if values else ""
        if first_col:
            row = {headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))}
            row = _normalize_buy_item(row)
            rows.append(row)
        else:
            if not rows:
                continue
            sel_key = "text" if "text" in headers else headers[0]
            prev_val = rows[-1].get(sel_key, "")
            extra = values[3] if len(values) > 3 else ""
            if prev_val and extra:
                rows[-1][sel_key] = _normalize_text(f"{prev_val}\n{extra}")
            elif extra:
                rows[-1][sel_key] = _normalize_text(extra)

    result: dict[str, Any] = {"message": message, "items": rows}
    if range_start and range_end:
        result["range"] = {"start": range_start, "end": range_end}
    return result


def _map_buy_item_fields(item: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key, value in item.items():
        mapped_key = BUY_LIST_HEADER_MAP.get(key, key)
        mapped[mapped_key] = value
    return mapped


@app.get("/show-buy-list")
def show_buy_list(
    profile: str = Query("default", alias="profile"),
    start_date: Optional[str] = Query(None, alias="start-date"),
    end_date: Optional[str] = Query(None, alias="end-date"),
):
    args = ["show-buy-list", "--profile", profile, "--format", "json"]
    if start_date:
        args.extend(["--start-date", start_date])
    if end_date:
        args.extend(["--end-date", end_date])

    payload = run_dhapi(args)
    return parse_buy_list_output(payload["stdout"])


@app.get("/show-balance")
def show_balance():
    payload = run_dhapi(["show-balance"])
    return parse_balance_output(payload["stdout"])


@app.post("/buy-lotto645")
def buy_lotto645(
    mode: str = Query("auto", alias="mode"),
    count: int = Query(5, alias="count"),
    numbers: Optional[str] = Query(None, alias="numbers"),
):
    if mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="mode must be 'auto' or 'manual'")

    args = ["buy-lotto645", "-y"]
    if mode == "auto":
        if count <= 0:
            raise HTTPException(status_code=400, detail="count must be >= 1")
        if count != 5:
            args.extend([""] * count)
    else:
        if not numbers:
            raise HTTPException(status_code=400, detail="numbers is required for manual mode")
        args.append(numbers)

    payload = run_dhapi(args)
    return {"message": payload["stdout"]}


if __name__ == "__main__":
    # Run via Python for debugger-friendly startup.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True, log_level="debug")
