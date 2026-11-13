"""Pi timeline behavior, using synthetic model output only."""
import unittest

from app.core.ai_timeline import Timeline, restore_messages


class TimelineTests(unittest.TestCase):
    def test_final_snapshot_orders_blocks_missing_from_stream(self):
        messages = []
        timeline = Timeline(messages, lambda text: text)
        timeline.event({"type": "message_update", "assistantMessageEvent": {
            "type": "text_delta", "contentIndex": 1, "delta": "answer"}})
        answer_id = messages[0]["id"]
        timeline.event({"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "earlier thought"}, {"type": "text", "text": "answer"}]}})
        self.assertEqual([m["role"] for m in messages], ["thinking", "assistant"])
        self.assertEqual(messages[-1]["id"], answer_id)

    def test_separate_blocks_and_final_snapshot_do_not_duplicate(self):
        messages = []
        timeline = Timeline(messages, lambda text: text)
        timeline.event({"type": "message_start", "message": {"role": "assistant"}})
        self.assertEqual(messages, [])
        for kind, index, text in [("thinking_delta", 0, "checking"), ("text_delta", 1, "answer"),
                                  ("thinking_delta", 2, "again"), ("text_delta", 3, "final")]:
            timeline.event({"type": "message_update", "assistantMessageEvent": {
                "type": kind, "contentIndex": index, "delta": text}})
        ids = [message["id"] for message in messages]
        timeline.event({"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "checking done"}, {"type": "text", "text": "answer done"},
            {"type": "thinking", "thinking": "again done"}, {"type": "text", "text": "final done"}]}})
        self.assertEqual([m["role"] for m in messages], ["thinking", "assistant", "thinking", "assistant"])
        self.assertEqual([m["text"] for m in messages], ["checking done", "answer done", "again done", "final done"])
        self.assertEqual([m["id"] for m in messages], ids)

    def test_tool_call_and_execution_share_one_row_with_output_snapshot(self):
        messages = []
        timeline = Timeline(messages, lambda text: text.replace("private", "[redacted]"))
        timeline.event({"type": "message_end", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "one", "name": "bash", "arguments": {"command": "echo private"}}]}})
        timeline.event({"type": "tool_execution_start", "toolCallId": "one", "toolName": "bash",
                        "args": {"command": "echo private"}})
        row_id = messages[0]["id"]
        for text in ("first", "first\nsecond"):
            timeline.event({"type": "tool_execution_update", "toolCallId": "one",
                            "partialResult": {"content": [{"type": "text", "text": text}]}})
        timeline.event({"type": "tool_execution_end", "toolCallId": "one", "isError": True,
                        "result": {"content": [{"type": "text", "text": "private failed"}]}})
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["id"], row_id)
        self.assertEqual(messages[0]["status"], "failed")
        self.assertEqual(messages[0]["result"], "[redacted] failed")
        self.assertNotIn("private", str(messages))

    def test_two_tools_update_independently_and_stop_is_terminal(self):
        messages = []
        timeline = Timeline(messages, lambda text: text)
        for key in ("one", "two"):
            timeline.event({"type": "tool_execution_start", "toolCallId": key, "toolName": "read", "args": {"path": key}})
        timeline.event({"type": "tool_execution_end", "toolCallId": "two", "result": {
            "content": [{"type": "text", "text": "second result"}]}})
        timeline.finish("stopped")
        self.assertEqual([m["status"] for m in messages], ["stopped", "completed"])
        self.assertEqual(messages[1]["result"], "second result")

    def test_restore_keeps_content_order_and_tool_error(self):
        restored = restore_messages([
            {"role": "user", "content": "request"},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "reason"},
                {"type": "text", "text": "before"},
                {"type": "toolCall", "id": "call", "name": "write", "arguments": {"path": "sun.html", "content": "html"}},
                {"type": "text", "text": "after"}]},
            {"role": "toolResult", "toolCallId": "call", "isError": True,
             "content": [{"type": "text", "text": "denied"}]}], lambda text: text)
        self.assertEqual([m["role"] for m in restored], ["user", "thinking", "assistant", "tool", "assistant"])
        self.assertEqual(restored[3]["status"], "failed")
        self.assertEqual(restored[3]["result"], "denied")

    def test_existing_transcript_and_missing_start_remain_supported(self):
        messages = [{"role": "tool", "text": "write\n{}", "result": "old result"}]
        timeline = Timeline(messages, lambda text: text)
        timeline.event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hello"}})
        timeline.event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": " world"}})
        self.assertEqual(messages[-1]["text"], "hello world")
        self.assertTrue(messages[0]["id"])
        timeline.finish("interrupted")
        self.assertEqual(messages[-1]["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
