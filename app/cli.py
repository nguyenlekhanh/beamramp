"""CLI: generate all scenario template JSONs and the smoke-test scenario.

Usage:
  python -m app.cli generate [--seed 12345]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.scenario import ScenarioError
from app.services import scenario_service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["generate", "smoke"])
    parser.add_argument("--seed", type=int, default=12345)
    args = parser.parse_args()

    if args.command == "generate":
        for tpl in scenario_service.TEMPLATES:
            sc = scenario_service.build_scenario(tpl["id"], args.seed)
            out = scenario_service.save_scenario_file(sc)
            print(f"saved {out}")
        return 0

    if args.command == "smoke":
        path = scenario_service.ensure_smoke_scenario(
            scenario_service.DEFAULT_SCENARIOS_DIR / "smoke_default.json", seed=args.seed)
        print(f"saved {path}")
        return 0
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ScenarioError as exc:
        print(f"ScenarioError: {exc}")
        sys.exit(2)
