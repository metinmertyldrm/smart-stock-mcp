"""Deterministic golden evaluation for Smart Stock AI contracts.

This evaluator intentionally does not call Ollama or business services. It checks
curated user intents against the production fast-route classifier, execution-plan
parser/state guards, and the same acceptance invariants used by the live runner.

Usage from the repository root:
    python llm-host/golden_eval.py
    python llm-host/golden_eval.py --only fast_out_of_stock_listing
    python llm-host/golden_eval.py --json golden-eval.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from acceptance_runner import SCENARIOS, evaluate as acceptance_evaluate  # noqa: E402
from conversation_state import ConversationState  # noqa: E402
from llm import _fast_execution_plan, prepare_inference_messages  # noqa: E402
from plan_validation import parse_execution_plan, validate_plan_against_state  # noqa: E402

GOLDEN_CASES_PATH = os.path.join(BASE_DIR, "golden_cases.json")
FULL_PLANNER_MARKER = "Smart Stock & Procurement execution planner.\n"


def load_cases(path: str | None = None) -> list[dict]:
    source = path or GOLDEN_CASES_PATH
    with open(source, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Golden case dosyasının kökü liste olmalıdır.")
    return payload


def _acceptance_scenario(case: dict) -> dict | None:
    scenario_id = case.get("acceptance_scenario")
    if not scenario_id:
        return None
    for scenario in SCENARIOS:
        if scenario["id"] == scenario_id:
            return scenario
    raise ValueError(f"Bilinmeyen acceptance_scenario: {scenario_id}")


def _expected_contract(case: dict) -> dict:
    scenario = _acceptance_scenario(case)
    if scenario is not None:
        return deepcopy(scenario.get("expect", {}))
    return deepcopy(case.get("expect", {}))


def _planner_messages(user: str) -> list[dict]:
    return [
        {"role": "system", "content": FULL_PLANNER_MARKER + ("x" * 4000)},
        {"role": "user", "content": user},
    ]


def evaluate_route(case: dict) -> tuple[dict, list[str]]:
    expected = case.get("route") or {"mode": "full"}
    original = _planner_messages(case.get("user", ""))
    prepared, fast_tool = prepare_inference_messages(original)
    actual_mode = "fast" if fast_tool else "full"

    problems = []
    expected_mode = expected.get("mode", "full")
    if actual_mode != expected_mode:
        problems.append(f"route={actual_mode}, beklenen {expected_mode}")

    expected_tool = expected.get("tool")
    if expected_tool != fast_tool:
        if expected_tool is not None or fast_tool is not None:
            problems.append(f"fast_tool={fast_tool!r}, beklenen {expected_tool!r}")

    if actual_mode == "fast":
        if len(prepared) != 2:
            problems.append(f"fast route mesaj sayısı {len(prepared)}, beklenen 2")
        if prepared is original:
            problems.append("fast route full planner mesajlarını küçültmedi")
    else:
        if prepared is not original:
            problems.append("full route planner mesajlarını beklenmedik biçimde değiştirdi")

    return {"mode": actual_mode, "tool": fast_tool}, problems


def _build_state(case: dict) -> tuple[ConversationState, list[str]]:
    state = ConversationState()
    problems = []
    for key, value in (case.get("state") or {}).items():
        if not hasattr(state, key):
            problems.append(f"bilinmeyen state alanı: {key}")
            continue
        setattr(state, key, value)
    return state, problems


def _response_from_plan(plan: dict) -> dict:
    trace = []
    for index, step in enumerate(plan.get("steps") or [], start=1):
        trace.append({
            "stepId": step.get("id") or f"step_{index}",
            "tool": step.get("tool"),
            "status": "success",
            "arguments": deepcopy(step.get("arguments") or {}),
            "resultSummary": "golden contract",
        })
    return {
        "plan": plan,
        "trace": trace,
        "finalAnswer": "golden contract",
        "succeeded": True,
    }


def evaluate_case(case: dict) -> dict:
    problems = []
    route, route_problems = evaluate_route(case)
    problems.extend(route_problems)

    raw_plan = json.dumps(case.get("plan") or {}, ensure_ascii=False)
    try:
        parsed = parse_execution_plan(raw_plan)
    except Exception as exc:  # noqa: BLE001 - evaluator should report, not crash
        parsed = None
        problems.append(f"plan parse edilemedi: {type(exc).__name__}: {exc}")

    if parsed is not None and route["mode"] == "fast" and route.get("tool"):
        try:
            generated_fast_plan = parse_execution_plan(_fast_execution_plan(route["tool"]))
            if generated_fast_plan != parsed:
                problems.append("deterministic fast plan golden plan ile eşleşmiyor")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"fast plan üretilemedi: {type(exc).__name__}: {exc}")

    state, state_problems = _build_state(case)
    problems.extend(state_problems)

    expected_validation_error = case.get("validation_error_contains")
    validation_status = "not-run"
    if parsed is not None:
        try:
            validate_plan_against_state(parsed, state)
            validation_status = "accepted"
            if expected_validation_error:
                problems.append(
                    "state validation planı kabul etti; beklenen hata: "
                    f"{expected_validation_error!r}"
                )
        except ValueError as exc:
            validation_status = "rejected"
            if expected_validation_error:
                if expected_validation_error not in str(exc):
                    problems.append(
                        f"state validation hatası {str(exc)!r}; "
                        f"beklenen parça {expected_validation_error!r}"
                    )
            else:
                problems.append(f"state validation beklenmedik biçimde reddetti: {exc}")
        except Exception as exc:  # noqa: BLE001
            validation_status = "error"
            problems.append(f"state validation çöktü: {type(exc).__name__}: {exc}")

    acceptance = None
    if parsed is not None and not expected_validation_error and validation_status == "accepted":
        contract = _expected_contract(case)
        acceptance = acceptance_evaluate(
            {"id": case.get("id", "golden"), "expect": contract},
            _response_from_plan(parsed),
        )
        problems.extend(acceptance["problems"])

    return {
        "id": case.get("id"),
        "tags": case.get("tags") or [],
        "ok": not problems,
        "route": route,
        "goal": (parsed or {}).get("goal"),
        "tools": [step.get("tool") for step in (parsed or {}).get("steps", [])],
        "validation": validation_status,
        "expected_rejection": bool(expected_validation_error),
        "problems": problems,
        "acceptance_signature": acceptance.get("signature") if acceptance else None,
    }


def run_cases(cases: list[dict], only: set[str] | None = None) -> list[dict]:
    selected = cases
    if only:
        selected = [case for case in cases if case.get("id") in only]
        found = {case.get("id") for case in selected}
        missing = only - found
        if missing:
            raise ValueError("Bilinmeyen golden case: " + ", ".join(sorted(missing)))
    return [evaluate_case(case) for case in selected]


def print_report(results: list[dict]) -> None:
    print("Smart Stock golden AI evaluation")
    print("=" * 78)
    for result in results:
        mark = "OK " if result["ok"] else "BAD"
        route = result["route"]["mode"]
        if result["route"].get("tool"):
            route += f":{result['route']['tool']}"
        tools = " -> ".join(result["tools"]) if result["tools"] else "(tool yok)"
        print(
            f"[{mark}] {result['id']:<38} "
            f"route={route:<26} goal={str(result['goal']):<8} {tools}"
        )
        for problem in result["problems"]:
            print(f"      - {problem}")

    passed = sum(1 for result in results if result["ok"])
    print("-" * 78)
    print(f"TOPLAM: {passed}/{len(results)} golden case geçti")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smart Stock offline golden AI evaluator")
    parser.add_argument("--cases", help="alternatif golden case JSON dosyası")
    parser.add_argument("--only", action="append", help="yalnızca bu case id'sini çalıştır")
    parser.add_argument("--json", help="sonuçları JSON olarak yaz")
    parser.add_argument("--quiet", action="store_true", help="başarılı raporu konsola yazma")
    args = parser.parse_args()

    try:
        cases = load_cases(args.cases)
        results = run_cases(cases, set(args.only or []))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Golden eval başlatılamadı: {exc}")
        return 2

    if not args.quiet or any(not result["ok"] for result in results):
        print_report(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump({"results": results}, handle, ensure_ascii=False, indent=2)

    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
