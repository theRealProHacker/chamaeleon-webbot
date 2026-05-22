import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(__file__))

import agent
import agent_base


class TestAgentImplementations:
    """Test suite for agent implementation."""

    @pytest.fixture
    def sample_messages(self):
        """Sample message conversation for testing."""
        return [
            {
                "role": "user",
                "content": "Hallo, ich interessiere mich für eine Reise nach Namibia.",
            },
            {
                "role": "assistant",
                "content": "Hallo! Gerne berate ich Sie zu unseren Namibia-Reisen.",
            },
            {"role": "user", "content": "Welche Reisen bieten Sie denn an?"},
        ]

    @pytest.fixture
    def sample_endpoint(self):
        """Sample endpoint for testing."""
        return "/Afrika/Namibia"

    @pytest.fixture
    def mock_website_response(self):
        """Mock response for website tool."""
        return {
            "title": "Namibia Reisen - Test",
            "main_content": "<div>Test content about Namibia trips</div>",
            "status": "success",
        }

    def test_agent_base_functions(self):
        """Test that base functions work correctly."""
        # Test time info function
        time_info = agent_base.get_current_time_info()
        assert "date" in time_info
        assert "time" in time_info
        assert "weekday" in time_info

        # Test system prompt formatting
        prompt = agent_base.format_system_prompt("/test-endpoint")
        assert "/test-endpoint" in prompt
        assert "Chamäleon" in prompt

        # Test link processing
        text_with_links = "Visit /Afrika/Namibia and https://example.com"
        processed = agent_base.process_links_in_reply(text_with_links)
        assert '<a href="/Afrika/Namibia"' in processed
        assert '<a href="https://example.com"' in processed

    @patch("agent_base.chamaeleon_website_tool_base")
    def test_langchain_agent_call(
        self, mock_website_tool, sample_messages, sample_endpoint, mock_website_response
    ):
        """Test the LangChain agent implementation."""
        # Mock the website tool
        mock_website_tool.return_value = mock_website_response

        # Mock the LangChain components
        with (
            patch("agent_lang.create_react_agent") as mock_create_agent,
            patch("agent_lang.model") as mock_model,
        ):
            # Mock agent response
            mock_agent_instance = MagicMock()
            mock_agent_instance.invoke.return_value = {
                "messages": [
                    MagicMock(
                        content="Test response from LangChain agent",
                        pretty_print=MagicMock(),
                    )
                ]
            }
            mock_create_agent.return_value = mock_agent_instance

            # Call the agent
            result = agent.call(sample_messages, sample_endpoint)

            # Verify the result structure
            assert isinstance(result, dict)
            assert "reply" in result
            assert "recommendations" in result
            assert isinstance(result["recommendations"], list)

            # Verify the agent was called
            mock_create_agent.assert_called_once()
            mock_agent_instance.invoke.assert_called_once()

    def test_both_agents_have_same_interface(self, sample_messages, sample_endpoint):
        """Test that both implementations have the same interface."""
        # Check that agent has a call function
        assert hasattr(agent, "call")

    @patch("agent_base.chamaeleon_website_tool_base")
    def test_error_handling(self, mock_website_tool, sample_messages, sample_endpoint):
        """Test error handling in both implementations."""
        # Test LangChain error handling
        with patch("agent_lang.create_react_agent") as mock_create_agent:
            mock_create_agent.side_effect = Exception("Test error")

            # Should not raise an exception
            try:
                result = agent.call(sample_messages, sample_endpoint)
                # If it doesn't raise, it should return a valid structure
                assert isinstance(result, dict)
                assert "reply" in result
                assert "recommendations" in result
            except Exception:
                pytest.fail("LangChain agent should handle errors gracefully")

    def test_tool_factories(self):
        """Test that tool factory functions work correctly."""
        # Test trip recommendation container
        recommendations = set()
        trip_tool = agent_base.make_recommend_trip_base(recommendations)

        trip_tool("Test-Trip")
        assert "Test-Trip" in recommendations

        trip_tool(["Trip1", "Trip2"])
        assert "Trip1" in recommendations
        assert "Trip2" in recommendations

        # Test human support container
        support_requests = []
        support_tool = agent_base.make_recommend_human_support_base(support_requests)

        support_tool()
        assert len(support_requests) == 1

    def test_message_conversion(self, sample_messages):
        """Test message conversion functions."""
        # Test LangChain message conversion
        langchain_messages = agent.convert_messages_to_langchain(sample_messages)
        assert len(langchain_messages) == len(sample_messages)

    @pytest.mark.integration
    @patch("agent_base.chamaeleon_website_tool_base")
    def test_integration_agent(
        self, mock_website_tool, sample_messages, sample_endpoint, mock_website_response
    ):
        """Integration test for the agent with mocked dependencies."""
        mock_website_tool.return_value = mock_website_response

        # Mock agent
        with patch("agent_lang.create_react_agent") as mock_lang_agent:
            # Setup LangChain mock
            mock_lang_instance = MagicMock()
            mock_lang_instance.invoke.return_value = {
                "messages": [
                    MagicMock(content="LangChain response", pretty_print=MagicMock())
                ]
            }
            mock_lang_agent.return_value = mock_lang_instance

            # Test implementation
            lang_result = agent.call(sample_messages, sample_endpoint)

            # Verify result structure
            assert isinstance(lang_result, dict)
            assert "reply" in lang_result
            assert "recommendations" in lang_result
            assert isinstance(lang_result["reply"], str)
            assert isinstance(lang_result["recommendations"], list)


if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v"])
