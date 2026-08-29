from typing import Optional
from ..models.event import EvidenceRef
from ..models.state import ProjectState


class SummaryGenerator:
    """Generates human-readable Markdown summary report from finalized ProjectState and Evidence repository."""

    @classmethod
    def generate_markdown(
        cls,
        state: ProjectState,
        evidences: Optional[list[EvidenceRef]] = None,
    ) -> str:
        lines: list[str] = []
        ev_map = {e.evidence_id: e for e in (evidences or [])}

        lines.append(f"# 需求访谈最终归档报告: {state.project_name}")
        lines.append(f"- **项目 ID**: `{state.project_id}`")
        lines.append(f"- **项目状态**: `{state.project_status}`")
        lines.append(f"- **总访谈轮次**: `{state.turn_index}`")
        lines.append("")

        lines.append("## 一、项目初始描述与需求背景")
        lines.append(state.initial_requirements or "（无初始描述）")
        lines.append("")

        all_topics = state.get_all_topics()
        seed_topics = [t for t in all_topics if t.origin == "seed"]
        emergent_topics = [t for t in all_topics if t.origin == "emergent"]

        lines.append("## 二、访谈主题汇总与完成状态")
        lines.append("| 序号 | 主题编号 | 所属章节 | 主题名称与定位 | 来源 | 状态 | 槽位数 |")
        lines.append("|---|---|---|---|---|---|---|")
        for idx, top in enumerate(all_topics, start=1):
            sec_name = top.section_id
            for s in state.sections:
                if s.section_id == top.section_id:
                    sec_name = s.section_content
                    break
            origin_badge = "预设 (Seed)" if top.origin == "seed" else "涌现 (Emergent)"
            lines.append(
                f"| {idx} | `{top.topic_number}` | {sec_name} | {top.topic_content} | {origin_badge} | `{top.topic_status}` | {len(top.slots)} |"
            )
        lines.append("")

        lines.append("## 三、各主题详细槽位信息与证据链")
        for top in all_topics:
            lines.append(f"### 主题: {top.topic_content} (`{top.topic_number}`)")
            lines.append(f"- **状态**: `{top.topic_status}` | **来源**: `{top.origin}`")
            if top.evidence_refs:
                top_ev_strs = []
                for eid in top.evidence_refs:
                    if eid in ev_map:
                        top_ev_strs.append(f"`{eid}` (来源: {ev_map[eid].source_type})")
                    else:
                        top_ev_strs.append(f"`{eid}`")
                lines.append(f"- **主题级关联证据**: {', '.join(top_ev_strs)}")

            lines.append("")
            if not top.slots:
                lines.append("*(该主题下无槽位)*")
                lines.append("")
                continue

            lines.append("| 槽位编号 | 槽位名称 | 必填 | 状态 | 提取值 | 证据溯源 |")
            lines.append("|---|---|---|---|---|---|")
            for slot in top.slots:
                val_display = str(slot.value) if slot.value is not None else "*(空)*"
                req_display = "是" if slot.is_required else "否"
                ev_strs = []
                for eid in slot.evidence_refs:
                    if eid in ev_map:
                        ev_obj = ev_map[eid]
                        ev_strs.append(f"`{eid}`: \"{ev_obj.content}\"")
                    else:
                        ev_strs.append(f"`{eid}`")
                ev_display = "<br>".join(ev_strs) if ev_strs else "*(无)*"

                lines.append(
                    f"| `{slot.slot_number}` | {slot.key} | {req_display} | `{slot.state}` | {val_display} | {ev_display} |"
                )
            lines.append("")

        lines.append("## 四、动态结构演化清单")
        if emergent_topics:
            lines.append("### 动态新增主题 (Emergent Topics):")
            for t in emergent_topics:
                lines.append(f"- **`{t.topic_number}`**: {t.topic_content} (创建轮次: 第 {t.created_turn} 轮)")
        else:
            lines.append("- *(本次访谈未产生动态新增主题)*")

        emergent_slots = [
            (t, s) for t in all_topics for s in t.slots if s.origin == "emergent"
        ]
        if emergent_slots:
            lines.append("")
            lines.append("### 动态新增槽位 (Emergent Slots):")
            for t, s in emergent_slots:
                lines.append(f"- 主题 `{t.topic_number}` -> 槽位 `{s.slot_number}`: **{s.key}** (值: `{s.value}`)")
        else:
            lines.append("- *(本次访谈未产生动态新增槽位)*")
        lines.append("")

        lines.append("## 五、未决项与冲突提示")
        uncertain_slots = [
            (t, s) for t in all_topics for s in t.slots if s.state == "uncertain"
        ]
        conflict_slots = [
            (t, s) for t in all_topics for s in t.slots if s.state == "conflict"
        ]

        if uncertain_slots:
            lines.append("### 待进一步确认的不确定槽位 (Uncertain):")
            for t, s in uncertain_slots:
                lines.append(f"- 主题 `{t.topic_number}` -> 槽位 `{s.slot_number}` ({s.key}): `{s.value}`")
        else:
            lines.append("- **不确定槽位**: 无")

        if conflict_slots:
            lines.append("")
            lines.append("### 存在待协调规则冲突的槽位 (Conflict):")
            for t, s in conflict_slots:
                lines.append(f"- 主题 `{t.topic_number}` -> 槽位 `{s.slot_number}` ({s.key}): `{s.value}`")
        else:
            lines.append("- **冲突槽位**: 无")

        lines.append("")
        return "\n".join(lines)
