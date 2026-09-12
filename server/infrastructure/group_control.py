"""Run-local structured host decision; scheduling stays in the application layer."""
import json
from threading import Lock
from pathlib import Path
from langchain_core.tools import StructuredTool
from server.domain.chat_group import GroupDecision


def group_control_tool(path: Path, members: list[str], finish_only: bool):
    decision_lock = Lock()
    def decide(action: str, tasks: list = None, summary: str = "") -> str:
        decision = GroupDecision(action=action, tasks=tasks or [], summary=summary)
        if finish_only and decision.action != "finish":
            raise ValueError("已达到三轮上限，必须总结并结束")
        if any(task.member_id not in members for task in decision.tasks):
            raise ValueError("只能调度本群成员")
        with decision_lock:
            if path.exists():
                previous = json.loads(path.read_text())
                if previous != decision.model_dump():
                    raise ValueError("本步骤已经提交决定，不能再次更改")
            else:
                temporary = path.with_suffix(".tmp")
                temporary.write_text(decision.model_dump_json(), encoding="utf-8")
                temporary.replace(path)
        return "决定已记录。本次回复结束后平台将执行，请勿再次调用。"

    return StructuredTool.from_function(
        decide, name="group_decision", args_schema=GroupDecision,
        description="主持专用：提交本轮分工（dispatch，最多4名成员）或结束（finish，必须填写summary）。每次执行只提交一个决定。",
    )
