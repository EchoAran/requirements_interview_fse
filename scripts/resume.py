import argparse
import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from elicitation_core.config import AppConfig
from elicitation_core.pipeline import ElicitationPipeline
from elicitation_core.storage.project_store import ProjectStore


async def main():
    parser = argparse.ArgumentParser(description="Resume an interrupted interview project session.")
    parser.add_argument("--project-id", "-p", required=True, help="Project ID to resume.")
    parser.add_argument("--config", "-c", default="configs/default.yaml", help="Path to configuration YAML file.")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute() and not config_path.exists():
        root_config = Path(__file__).parent.parent / args.config
        if root_config.exists():
            config_path = root_config

    config = AppConfig.load_from_yaml(config_path)
    root_dir = Path(__file__).parent.parent
    if not Path(config.runtime.runs_dir).is_absolute():
        config.runtime.runs_dir = str(root_dir / config.runtime.runs_dir)

    print(f"=== Resuming Project: {args.project_id} ===")
    try:
        pipeline = ElicitationPipeline.resume(
            project_id=args.project_id,
            config=config,
            base_runs_dir=config.runtime.runs_dir,
        )
        state = pipeline.store.load_state(args.project_id)
        current_topic = state.get_current_topic()
        turns = pipeline.store.load_turns(args.project_id)

        print("\n--- Project Resumed Successfully ---")
        print(f"Project Name   : {state.project_name}")
        print(f"Project Status : {state.project_status}")
        print(f"Current Turn   : {state.turn_index} (Recorded Messages: {len(turns)})")
        if current_topic:
            print(f"Active Topic   : [{current_topic.topic_number}] {current_topic.topic_content}")
        else:
            print(f"Active Topic   : None (Interview Finished)")

        if turns and turns[-1].role == "Interviewer":
            print(f"\n[Last Question for User]: {turns[-1].message_content}")

    except Exception as e:
        print(f"Error resuming project: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
