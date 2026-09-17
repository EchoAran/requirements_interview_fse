import argparse
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import AppConfig
from storage.project_store import ProjectStore
from services.state_view import StateView


def main():
    parser = argparse.ArgumentParser(description="Inspect current project state, slots, revisions, and evidence chain.")
    parser.add_argument("--project-id", "-p", required=True, help="Project ID.")
    parser.add_argument("--config", "-c", default="configs/default.yaml", help="Path to configuration YAML file.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show full revision details and evidence.")
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

    store = ProjectStore(base_runs_dir=config.runtime.runs_dir)
    if not store.project_exists(args.project_id):
        print(f"Error: Project state not found for ID: {args.project_id}", file=sys.stderr)
        sys.exit(1)

    state = store.load_state(args.project_id)
    turns = store.load_turns(args.project_id)
    evidences = store.load_evidences(args.project_id)
    events = store.load_state_events(args.project_id)
    view = StateView(state)
    summary = view.get_project_summary()

    print(f"\n=======================================================")
    print(f" Project : {state.project_name} ({state.project_id})")
    print(f" Status  : {state.project_status} | Turn Count: {len(turns)} (Turn Index: {state.turn_index})")
    print(f" Events  : {len(events)} state events | Evidences: {len(evidences)}")
    print(f" Coverage: {summary['filled_slots']}/{summary['total_slots']} slots ({summary['overall_slot_coverage']:.1%})")
    print(f" Current Topic ID: {state.current_topic_id}")
    print(f"=======================================================")

    print("\n--- Sections & Topics Structure ---")
    for sec in state.sections:
        print(f"\n[Section {sec.section_number}] {sec.section_content}")
        for top in sec.topics:
            marker = "--> (CURRENT ONGOING)" if top.topic_id == state.current_topic_id else ""
            comp = view.get_topic_completion(top.topic_id)
            print(f"  * Topic {top.topic_number}: {top.topic_content} [{top.topic_status}] (Comp: {comp:.0%}) {marker}")
            for slt in top.slots:
                val = f'"{slt.value}"' if slt.value is not None else "<empty>"
                nec = "REQUIRED" if slt.is_required else "dynamic"
                ev_count = f"({len(slt.evidence_refs)} ev_refs, {len(slt.revisions)} revs)"
                state_badge = f"[{slt.state.upper()}]"
                print(f"      - {slt.slot_number} [{slt.key}] {state_badge} ({nec}): {val} {ev_count}")

                if args.verbose and slt.revisions:
                    for r in slt.revisions:
                        print(f"          > Rev {r.revision_id[:8]} [{r.operation}] (Turn: {r.turn_id or 'init'}): old='{r.old_value}' -> new='{r.new_value}' ev={r.evidence_refs}")

    print("\n--- Initial Dependency Order ---")
    print(" -> ".join(state.initial_order) if state.initial_order else "<none>")

    print("\n--- Recent Turns (Last 4) ---")
    for t in turns[-4:]:
        print(f"[{t.role}] (Turn {t.turn_index}): {t.message_content}")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
