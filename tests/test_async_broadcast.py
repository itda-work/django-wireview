"""Tests for async broadcast functions."""

from unittest.mock import AsyncMock, patch

import pytest


class TestAbroadcast:
    """Test the abroadcast async function."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_abroadcast_sends_notification(self):
        """abroadcast should send a notification to the specified channel."""
        from wireview import abroadcast

        mock_channel_layer = AsyncMock()

        with patch(
            "wireview.core.transport.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            await abroadcast("test-channel", action="joined", user="testuser")

        mock_channel_layer.group_send.assert_called_once_with(
            "test-channel",
            {
                "type": "notification",
                "channel": "test-channel",
                "kwargs": {"action": "joined", "user": "testuser"},
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_abroadcast_with_no_kwargs(self):
        """abroadcast should work with no additional kwargs."""
        from wireview import abroadcast

        mock_channel_layer = AsyncMock()

        with patch(
            "wireview.core.transport.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            await abroadcast("my-channel")

        mock_channel_layer.group_send.assert_called_once_with(
            "my-channel",
            {
                "type": "notification",
                "channel": "my-channel",
                "kwargs": {},
            },
        )


class TestAsendTo:
    """Test the asend_to async function."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_asend_to_sends_message(self):
        """asend_to should send a message to the specified channel."""
        from wireview.utils import asend_to

        mock_channel_layer = AsyncMock()

        with patch(
            "wireview.core.transport.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            await asend_to("test-channel", "model_mutation", action="created", instance="data")

        mock_channel_layer.group_send.assert_called_once_with(
            "test-channel",
            {
                "type": "model_mutation",
                "channel": "test-channel",
                "action": "created",
                "instance": "data",
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_asend_to_with_none_channel(self):
        """asend_to should not send anything if channel is None."""
        from wireview.utils import asend_to

        mock_channel_layer = AsyncMock()

        with patch(
            "wireview.core.transport.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            await asend_to(None, "notification", message="test")

        mock_channel_layer.group_send.assert_not_called()


class TestAsendNotification:
    """Test the asend_notification async function."""

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_asend_notification_sends_notification(self):
        """asend_notification should send a notification message."""
        from wireview.utils import asend_notification

        mock_channel_layer = AsyncMock()

        with patch(
            "wireview.core.transport.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            await asend_notification("test-channel", message="hello", count=5)

        mock_channel_layer.group_send.assert_called_once_with(
            "test-channel",
            {
                "type": "notification",
                "channel": "test-channel",
                "kwargs": {"message": "hello", "count": 5},
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_asend_notification_with_no_kwargs(self):
        """asend_notification should work with no additional kwargs."""
        from wireview.utils import asend_notification

        mock_channel_layer = AsyncMock()

        with patch(
            "wireview.core.transport.get_channel_layer",
            return_value=mock_channel_layer,
        ):
            await asend_notification("empty-channel")

        mock_channel_layer.group_send.assert_called_once_with(
            "empty-channel",
            {
                "type": "notification",
                "channel": "empty-channel",
                "kwargs": {},
            },
        )


class TestAsyncBroadcastImports:
    """Test that async broadcast functions are properly exported."""

    @pytest.mark.unit
    def test_abroadcast_exported_from_wireview(self):
        """abroadcast should be importable from wireview package."""
        from wireview import abroadcast

        assert callable(abroadcast)

    @pytest.mark.unit
    def test_async_utils_exported_from_utils(self):
        """Async utils should be importable from wireview.utils."""
        from wireview.utils import asend_notification, asend_to

        assert callable(asend_to)
        assert callable(asend_notification)

    @pytest.mark.unit
    def test_abroadcast_in_all(self):
        """abroadcast should be in wireview.__all__."""
        import wireview

        assert "abroadcast" in wireview.__all__

    @pytest.mark.unit
    def test_async_utils_in_all(self):
        """Async utils should be in wireview.utils.__all__."""
        from wireview import utils

        assert "asend_to" in utils.__all__
        assert "asend_notification" in utils.__all__
