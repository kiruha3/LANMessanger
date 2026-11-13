"""Ordered Pi text/thinking/tool projection, independent of Qt widgets.

Adapted from bb's provider-pi/delta-translation.ts and assistant-stream-projection.ts.
Copyright (c) 2026 Michael Yong, MIT; see licenses/bb-MIT.txt.
Source: https://github.com/get-bb/bb/tree/7794941da1b6e2baf017e5f7ab65b5f223fcaf54
"""
import json
import uuid


def message_text(message):
    content = message.get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content if block.get("type") == "text")


class Timeline:
    def __init__(self, messages, redact):
        self.messages = messages
        self.redact = redact
        self._blocks = {}
        self._tools = {}
        for message in messages:
            message.setdefault("id", str(uuid.uuid4()))
            if message.get("tool_id"):
                self._tools[message["tool_id"]] = message

    def _append(self, role, **fields):
        message = {"id": str(uuid.uuid4()), "role": role, **fields}
        self.messages.append(message)
        return message

    def _text(self, role, index, text, snapshot=False):
        key = (index, role)
        message = self._blocks.get(key)
        if message is None:
            if not text:
                return
            message = self._append(role, text="", status="running")
            self._blocks[key] = message
        message["text"] = self.redact(text if snapshot else message["text"] + text)
        if snapshot:
            message["status"] = "completed"
        return message

    def _tool(self, key, name="", args=None):
        message = self._tools.get(key)
        if message is None:
            message = self._append("tool", tool_id=key, tool_name=name or "Инструмент",
                                   args={}, text="", result="", status="queued")
            self._tools[key] = message
        if name:
            message["tool_name"] = name
        if args is not None:
            # Redact serialized argument values as well as the human-readable details.
            safe_args = self.redact(json.dumps(args, ensure_ascii=False))
            try:
                message["args"] = json.loads(safe_args)
            except ValueError:
                message["args"] = {"details": safe_args}
        message["text"] = message["tool_name"] + "\n" + json.dumps(message["args"], ensure_ascii=False, indent=2)
        return message

    def event(self, event):
        kind = event.get("type")
        if kind == "message_start" and event.get("message", {}).get("role") == "assistant":
            self._blocks = {}
        elif kind == "message_update":
            delta = event.get("assistantMessageEvent", {})
            delta_type = delta.get("type", "")
            role = "thinking" if delta_type.startswith("thinking_") else "assistant"
            if delta_type in ("thinking_delta", "text_delta"):
                self._text(role, delta.get("contentIndex", 0), delta.get("delta", ""))
            elif delta_type in ("thinking_end", "text_end"):
                self._text(role, delta.get("contentIndex", 0), delta.get("content", ""), snapshot=True)
        elif kind == "message_end" and event.get("message", {}).get("role") == "assistant":
            message = event["message"]
            content = message.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            ordered = []
            for index, block in enumerate(content):
                block_type = block.get("type")
                row = None
                if block_type == "text":
                    row = self._text("assistant", index, block.get("text", ""), snapshot=True)
                elif block_type == "thinking":
                    row = self._text("thinking", index, block.get("thinking", ""), snapshot=True)
                elif block_type == "toolCall":
                    row = self._tool(block.get("id"), block.get("name"), block.get("arguments", {}))
                if row is not None:
                    ordered.append(row)
            if ordered:
                # The final snapshot also supplies blocks a provider may have omitted from deltas.
                owned = {row["id"] for row in self._blocks.values()} | {row["id"] for row in ordered}
                start = next(i for i, row in enumerate(self.messages) if row["id"] in owned)
                remainder = [row for row in self.messages if row["id"] not in owned]
                remainder[start:start] = ordered
                self.messages[:] = remainder
            if message.get("errorMessage"):
                self._append("system", text=self.redact(message["errorMessage"]))
        elif kind == "tool_execution_start":
            message = self._tool(event.get("toolCallId"), event.get("toolName"), event.get("args", {}))
            message["status"] = "running"
        elif kind in ("tool_execution_update", "tool_execution_end"):
            message = self._tool(event.get("toolCallId"), event.get("toolName"))
            result = event.get("result", event.get("partialResult", {}))
            message["result"] = self.redact(message_text(result))[-16000:]
            if kind == "tool_execution_end":
                message["status"] = "failed" if event.get("isError") else "completed"

    def finish(self, status="completed"):
        for message in self.messages:
            if message.get("status") in ("running", "queued"):
                # agent_end may follow a provider failure with unexecuted partial tool calls.
                # Only tool_execution_end is evidence that a tool actually completed.
                message["status"] = "interrupted" if message.get("role") == "tool" and status == "completed" else status
        self._blocks = {}


def restore_messages(source, redact):
    messages = []
    timeline = Timeline(messages, redact)
    for message in source:
        role = message.get("role")
        if role == "user":
            timeline._append("user", text=redact(message_text(message)))
        elif role == "assistant":
            timeline.event({"type": "message_start", "message": message})
            timeline.event({"type": "message_end", "message": message})
        elif role == "toolResult":
            timeline.event({"type": "tool_execution_end", "toolCallId": message.get("toolCallId"),
                            "toolName": message.get("toolName"), "result": message,
                            "isError": message.get("isError", False)})
    timeline.finish("interrupted")
    return messages
