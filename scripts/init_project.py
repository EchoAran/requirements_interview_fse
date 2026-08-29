import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from elicitation_core.config import AppConfig
from elicitation_core.pipeline import ElicitationPipeline


async def main():
    parser = argparse.ArgumentParser(description="Initialize a new semi-structured interview project.")
    parser.add_argument("--input", "-i", required=True, help="Path to input JSON file containing project_name and initial_requirements.")
    parser.add_argument("--config", "-c", default="configs/default.yaml", help="Path to configuration YAML file.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        input_data = json.load(f)

    project_name = input_data.get("project_name", "Untitled Project")
    initial_requirements = input_data.get("initial_requirements", "")

    config_path = Path(args.config)
    if not config_path.is_absolute() and not config_path.exists():
        root_config = Path(__file__).parent.parent / args.config
        if root_config.exists():
            config_path = root_config

    config = AppConfig.load_from_yaml(config_path)

    root_dir = Path(__file__).parent.parent
    if not Path(config.runtime.runs_dir).is_absolute():
        config.runtime.runs_dir = str(root_dir / config.runtime.runs_dir)
    if not Path(config.runtime.prompts_dir).is_absolute():
        config.runtime.prompts_dir = str(root_dir / config.runtime.prompts_dir)

    print(f"=== Initializing Project: {project_name} ===")
    pipeline = ElicitationPipeline.create(
        project_name=project_name,
        initial_requirements=initial_requirements,
        config=config,
    )

    result = await pipeline.initialize()

    print("\n--- Project Initialized Successfully ---")
    print(f"Project ID    : {result.project_id}")
    print(f"Initial Topic : [{result.current_topic_number}] {result.current_topic_content}")
    print(f"Strategy      : {result.selected_strategy}")
    print(f"Runs Dir      : {Path(config.runtime.runs_dir) / result.project_id}")
    print("\n[Interviewer First Question]:")
    print(result.next_question)
    print("----------------------------------------\n")


if __name__ == "__main__":
    asyncio.run(main())
