import argparse
import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import AppConfig
from pipeline import ElicitationPipeline, ProjectNotReadyForFinalizationError


async def main():
    parser = argparse.ArgumentParser(description="Archive an already completed interview project and generate its report.")
    parser.add_argument("--project-id", "-p", required=True, help="Project ID.")
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
            base_runs_dir=config.runtime.runs_dir,
        )
        result = await pipeline.finish()
    except FileNotFoundError:
        print(f"Error: Project state not found for ID: {args.project_id}", file=sys.stderr)
        sys.exit(1)
    except ProjectNotReadyForFinalizationError as e:
        print(f"Error: Project not ready for finalization: {e}", file=sys.stderr)
        sys.exit(1)

    project_dir = pipeline.store.get_project_dir(args.project_id)
    print("\n--- Project Finalized Successfully ---")
    print(f"Project ID        : {result.project_id}")
    print(f"Final State       : {project_dir / 'final_state.json'}")
    print(f"Summary Report    : {project_dir / 'summary.md'}")
    print("--------------------------------------\n")


if __name__ == "__main__":
    asyncio.run(main())
