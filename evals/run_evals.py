"""Run the eval suite against the real model with frozen tool data.

    python -m evals.run_evals                  # every case, 3 runs each
    python -m evals.run_evals --case off_topic --runs 1

This calls the Anthropic API and costs real money (one chat turn per case per run).
Output is a pass-rate table plus the first failure detail per check. Full results are
written to evals/results/latest.json (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

from src.api.chat import DEFAULT_MODEL, stream_chat

from .fixtures import FixtureTools
from .graders import CHECKS, DEFAULT_CHECKS, Run
from .usage import RecordingClient, Usage, format_cost

HERE = Path(__file__).resolve().parent


def load_cases(path: Path = HERE / "cases.yaml") -> list[dict]:
    return yaml.safe_load(path.read_text())


def parse_check(spec) -> tuple[str, object]:
    """'name' -> (name, None); {'name': arg} -> (name, arg)."""
    if isinstance(spec, str):
        return spec, None
    ((name, arg),) = spec.items()
    return name, arg


def run_case_once(case: dict, **chat_kwargs) -> Run:
    """chat_kwargs go to stream_chat (model=, client=, ...)."""
    tools = FixtureTools()
    text, error = [], None
    for event in stream_chat([{"role": "user", "content": case["question"]}], tool_runner=tools, **chat_kwargs):
        if event["type"] == "text":
            text.append(event["text"])
        elif event["type"] == "error":
            error = event["message"]
    return Run(question=case["question"], text="".join(text), tool_calls=tools.log, error=error)


def grade(case: dict, run: Run) -> dict[str, dict]:
    specs = [*DEFAULT_CHECKS, *case.get("checks", [])]
    out = {}
    for spec in specs:
        name, arg = parse_check(spec)
        result = CHECKS[name](run, arg)
        out[name] = {"passed": result.passed, "detail": result.detail}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3, help="runs per case (the model varies run to run)")
    parser.add_argument("--case", help="run only this case id")
    parser.add_argument("--model", help="override CHAT_MODEL")
    args = parser.parse_args()

    load_dotenv()
    cases = [c for c in load_cases() if not args.case or c["id"] == args.case]
    if not cases:
        raise SystemExit(f"no case with id {args.case!r}")

    model = args.model or os.environ.get("CHAT_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic()
    records, by_category = [], defaultdict(lambda: [0, 0])
    total_usage = Usage()
    print(f"{len(cases)} cases x {args.runs} runs on {model}\n")
    for case in cases:
        failures: dict[str, list[str]] = defaultdict(list)
        passed_runs = 0
        case_usage = Usage()
        for i in range(args.runs):
            recorder = RecordingClient(client)
            run = run_case_once(case, client=recorder, model=args.model)
            case_usage.add(recorder.usage)
            graded = grade(case, run)
            ok = all(g["passed"] for g in graded.values())
            passed_runs += ok
            by_category[case["category"]][0] += ok
            by_category[case["category"]][1] += 1
            for name, g in graded.items():
                if not g["passed"]:
                    failures[name].append(g["detail"])
            records.append({"case": case["id"], "run": i, "text": run.text, "graded": graded,
                            "tools": [{"name": c.name, "input": c.input} for c in run.tool_calls],
                            "usage": recorder.usage.to_dict()})  # fmt: skip
        total_usage.add(case_usage)
        case_cost = case_usage.cost(model)
        cost_note = f"  ${case_cost / args.runs:.4f}/run" if case_cost is not None else ""
        print(
            f"{'PASS' if passed_runs == args.runs else 'FAIL'}  {case['id']:<22} "
            f"{passed_runs}/{args.runs} runs{cost_note}"
        )
        for name, details in failures.items():
            print(f"        - {name} failed {len(details)}x, e.g. {details[0]}")

    print("\nBy category (runs fully passing):")
    for category, (ok, total) in by_category.items():
        print(f"  {category:<12} {ok}/{total}  ({100 * ok / total:.0f}%)")
    total_ok = sum(v[0] for v in by_category.values())
    total = sum(v[1] for v in by_category.values())
    print(f"  {'overall':<12} {total_ok}/{total}  ({100 * total_ok / total:.0f}%)")

    runs_total = len(cases) * args.runs
    print(
        f"\nUsage on {model}: {total_usage.calls} API calls, {total_usage.input_tokens:,} input + "
        f"{total_usage.output_tokens:,} output tokens"
    )
    print(f"Cost: {format_cost(total_usage, model, runs_total)}")
    if total_usage.cache_tokens:
        print(f"  Note: {total_usage.cache_tokens:,} cache tokens are not priced, so this undercounts.")

    out = HERE / "results" / "latest.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(records, indent=2))
    print(f"\nFull transcripts and grades: {out.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
