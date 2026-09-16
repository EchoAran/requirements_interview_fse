import argparse
import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from elicitation_core.config import AppConfig
from elicitation_core.pipeline import ElicitationPipeline


async def main():
    parser = argparse.ArgumentParser(description="Advance interview by providing interviewee response.")
    parser.add_argument("--project-id", "-p", required=True, help="Project ID.")
    parser.add_argument("--answer", "-a", required=True, help="Interviewee's response text.")
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
    if not Path(config.runtime.prompts_dir).is_absolute():
        config.runtime.prompts_dir = str(root_dir / config.runtime.prompts_dir)

    try:
        pipeline = ElicitationPipeline.load(
            project_id=args.project_id,
            config=config,
        )
    except FileNotFoundError:
        print(f"Error: Project state not found for ID: {args.project_id}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[Interviewee Answer]: {args.answer}\n")
    print("Processing step...")

    result = await pipeline.step(answer=args.answer)

    print("\n--- Step Completed ---")
    print(f"Turn Index        : {result.turn_index}")
    if result.is_finished:
        print(f"Status            : FINISHED")
        print(f"Finish Message    : {result.finish_message}")
    elif result.termination_reason:
        print("Status            : TERMINATED (INCOMPLETE)")
        print(f"Termination Reason: {result.termination_reason}")
        print(f"Termination Message: {result.termination_message}")
    else:
        print(f"Current Topic     : [{result.current_topic_number}] {result.current_topic_content}")
        print(f"Selected Operation: {result.selected_operation}")
        print(f"Selected Strategy : {result.selected_strategy}")
        print("\n[Interviewer Next Question]:")
        print(result.next_question)
    print("----------------------\n")


if __name__ == "__main__":
    asyncio.run(main())
