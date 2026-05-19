"""Tests for sandbox replay_params and AuditTool replay action."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from opendesk.computer.sandbox import ActionType, clear_sandbox, get_sandbox
from opendesk.tools.audit import AuditTool
from opendesk.tools.base import ToolContext, ToolResult


def fresh(session_id: str):
    clear_sandbox(session_id)
    return get_sandbox(session_id)


# ---------------------------------------------------------------------------
# ComputerSandbox — replay_params storage
# ---------------------------------------------------------------------------


class TestSandboxReplayParams:
    @pytest.mark.asyncio
    async def test_record_action_stores_replay_params(self):
        sb = fresh("sb-rp-1")
        entry = await sb.record_action(
            ActionType.KEYBOARD_TYPE,
            {"action": "type"},
            result="ok",
            replay_params={"action": "type", "text": "hello"},
        )
        assert entry.replay_params == {"action": "type", "text": "hello"}

    @pytest.mark.asyncio
    async def test_record_action_replay_params_defaults_none(self):
        sb = fresh("sb-rp-2")
        entry = await sb.record_action(ActionType.SCREENSHOT, {}, result="ok")
        assert entry.replay_params is None

    @pytest.mark.asyncio
    async def test_to_dict_includes_replay_params(self):
        sb = fresh("sb-rp-3")
        entry = await sb.record_action(
            ActionType.MOUSE_CLICK,
            {"action": "click", "x": 10, "y": 20},
            replay_params={"action": "click", "x": 10, "y": 20},
        )
        d = entry.to_dict()
        assert "replay_params" in d
        assert d["replay_params"]["action"] == "click"

    @pytest.mark.asyncio
    async def test_to_dict_excludes_replay_params_when_none(self):
        sb = fresh("sb-rp-4")
        entry = await sb.record_action(ActionType.SCREENSHOT, {})
        d = entry.to_dict()
        assert "replay_params" not in d

    @pytest.mark.asyncio
    async def test_export_audit_log_preserves_replay_params(self):
        sb = fresh("sb-rp-5")
        await sb.record_action(
            ActionType.KEYBOARD_TYPE, {"action": "type"},
            replay_params={"action": "type", "text": "hi"},
        )
        await sb.record_action(ActionType.SCREENSHOT, {})
        log = sb.export_audit_log()
        assert log[0]["replay_params"] == {"action": "type", "text": "hi"}
        assert "replay_params" not in log[1]


# ---------------------------------------------------------------------------
# AuditTool — show
# ---------------------------------------------------------------------------


class TestAuditToolShow:
    @pytest.mark.asyncio
    async def test_show_empty_log(self):
        sid = "audit-show-empty"
        clear_sandbox(sid)
        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="show", format="full"))
        assert "No actions recorded" in result.output

    @pytest.mark.asyncio
    async def test_show_summary(self):
        sid = "audit-show-summary"
        sb = fresh(sid)
        await sb.record_action(ActionType.KEYBOARD_TYPE, {"action": "type"})
        await sb.record_action(ActionType.MOUSE_CLICK, {"action": "click"})

        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="show", format="summary"))
        assert "2 actions" in result.output

    @pytest.mark.asyncio
    async def test_show_full_marks_replayable_entries(self):
        sid = "audit-show-marks"
        sb = fresh(sid)
        await sb.record_action(
            ActionType.KEYBOARD_TYPE, {"action": "type"},
            replay_params={"action": "type", "text": "hello"},
        )
        await sb.record_action(ActionType.SCREENSHOT, {})  # not replayable

        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="show", format="full"))
        assert "✓" in result.output
        assert "keyboard_type" in result.output

    @pytest.mark.asyncio
    async def test_show_includes_replay_hint(self):
        sid = "audit-show-hint"
        sb = fresh(sid)
        await sb.record_action(ActionType.KEYBOARD_TYPE, {"action": "type"},
                                replay_params={"action": "type", "text": "x"})

        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="show", format="full"))
        assert "replay" in result.output.lower()


# ---------------------------------------------------------------------------
# AuditTool — replay
# ---------------------------------------------------------------------------


class TestAuditToolReplay:
    @pytest.mark.asyncio
    async def test_replay_empty_log(self):
        sid = "audit-rep-empty"
        clear_sandbox(sid)
        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="replay"))
        assert "empty" in result.output.lower()

    @pytest.mark.asyncio
    async def test_replay_no_replayable_entries(self):
        sid = "audit-rep-noop"
        sb = fresh(sid)
        await sb.record_action(ActionType.SCREENSHOT, {})

        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="replay"))
        assert "No replayable" in result.output

    @pytest.mark.asyncio
    async def test_replay_skips_errored_entries_by_default(self):
        sid = "audit-rep-skip-err"
        sb = fresh(sid)
        await sb.record_action(
            ActionType.KEYBOARD_TYPE, {"action": "type"},
            error="hardware failure",
            replay_params={"action": "type", "text": "hi"},
        )

        tool = AuditTool()
        ctx = ToolContext(session_id=sid)
        result = await tool.execute(ctx, AuditTool.Params(action="replay", skip_errors=True))
        assert "No replayable" in result.output

    @pytest.mark.asyncio
    async def test_replay_includes_errored_entries_when_flag_false(self):
        sid = "audit-rep-err-incl"
        sb = fresh(sid)
        await sb.record_action(
            ActionType.KEYBOARD_TYPE, {"action": "type"},
            error="some error",
            replay_params={"action": "type", "text": "hi", "key": None, "keys": None,
                           "interval": 0.02, "hold_duration": 1.0},
        )

        fake_result = ToolResult(title="Keyboard: type", output="Typed ok")
        with patch("opendesk.tools.audit._load_tool") as mock_load:
            mock_tool = AsyncMock()
            mock_tool.execute.return_value = fake_result
            mock_tool.parse_params.return_value = object()
            mock_load.return_value = mock_tool

            tool = AuditTool()
            ctx = ToolContext(session_id=sid)
            result = await tool.execute(ctx, AuditTool.Params(action="replay", skip_errors=False))

        assert "Replaying 1" in result.output
        mock_load.assert_called_once_with("keyboard")

    @pytest.mark.asyncio
    async def test_replay_dispatches_to_correct_tool(self):
        sid = "audit-rep-dispatch"
        sb = fresh(sid)
        await sb.record_action(
            ActionType.KEYBOARD_TYPE, {"action": "type"}, result="ok",
            replay_params={"action": "type", "text": "hello", "key": None, "keys": None,
                           "interval": 0.02, "hold_duration": 1.0},
        )

        fake_result = ToolResult(title="Keyboard: type", output="Typed 5 characters: 'hello'")
        with patch("opendesk.tools.audit._load_tool") as mock_load:
            mock_tool = AsyncMock()
            mock_tool.execute.return_value = fake_result
            mock_tool.parse_params.return_value = object()
            mock_load.return_value = mock_tool

            tool = AuditTool()
            ctx = ToolContext(session_id=sid)
            result = await tool.execute(ctx, AuditTool.Params(action="replay"))

        assert "1 succeeded" in result.output
        assert "keyboard_type" in result.output
        mock_load.assert_called_once_with("keyboard")

    @pytest.mark.asyncio
    async def test_replay_reports_failed_tool_execution(self):
        sid = "audit-rep-fail"
        sb = fresh(sid)
        await sb.record_action(
            ActionType.MOUSE_CLICK, {"action": "click"}, result="ok",
            replay_params={"action": "click", "x": 10, "y": 20, "direction": None,
                           "amount": 3, "duration": 0.25, "settle_ms": 500,
                           "image_width": None, "image_height": None},
        )

        error_result = ToolResult(title="Mouse error", output="hardware unavailable", error=True)
        with patch("opendesk.tools.audit._load_tool") as mock_load:
            mock_tool = AsyncMock()
            mock_tool.execute.return_value = error_result
            mock_tool.parse_params.return_value = object()
            mock_load.return_value = mock_tool

            tool = AuditTool()
            ctx = ToolContext(session_id=sid)
            result = await tool.execute(ctx, AuditTool.Params(action="replay"))

        assert "0 succeeded, 1 failed" in result.output
        assert "ERROR" in result.output

    @pytest.mark.asyncio
    async def test_replay_multiple_actions(self):
        sid = "audit-rep-multi"
        sb = fresh(sid)
        await sb.record_action(
            ActionType.KEYBOARD_TYPE, {"action": "type"}, result="ok",
            replay_params={"action": "type", "text": "a", "key": None, "keys": None,
                           "interval": 0.02, "hold_duration": 1.0},
        )
        await sb.record_action(
            ActionType.MOUSE_CLICK, {"action": "click"}, result="ok",
            replay_params={"action": "click", "x": 5, "y": 5, "direction": None,
                           "amount": 3, "duration": 0.25, "settle_ms": 500,
                           "image_width": None, "image_height": None},
        )
        await sb.record_action(ActionType.SCREENSHOT, {})  # skipped

        ok_result = ToolResult(title="ok", output="done")
        with patch("opendesk.tools.audit._load_tool") as mock_load:
            mock_tool = AsyncMock()
            mock_tool.execute.return_value = ok_result
            mock_tool.parse_params.return_value = object()
            mock_load.return_value = mock_tool

            tool = AuditTool()
            ctx = ToolContext(session_id=sid)
            result = await tool.execute(ctx, AuditTool.Params(action="replay"))

        assert "2 succeeded" in result.output
        assert mock_load.call_count == 2
