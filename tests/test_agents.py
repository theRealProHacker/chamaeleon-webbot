import pytest
from unittest.mock import patch, MagicMock
import sys
import os

# Add the current directory to Python path for imports
sys.path.insert(0, os.path.dirname(__file__))

# Import both implementations
import agent_lang
import agent_smol
import agent_base

class TestAgentImplementations:
    """Test suite for both agent implementations."""
    
    @pytest.fixture
    def sample_messages(self):
        """Sample message conversation for testing."""
        return [
            {"role": "user", "content": "Hallo, ich interessiere mich für eine Reise nach Namibia."},
            {"role": "assistant", "content": "Hallo! Gerne berate ich Sie zu unseren Namibia-Reisen."},
            {"role": "user", "content": "Welche Reisen bieten Sie denn an?"}
        ]
    
    @pytest.fixture
    def sample_endpoint(self):
        """Sample endpoint for testing."""
        return "/Afrika/Namibia"
    
    @pytest.fixture
    def mock_website_response(self):
        """Mock response for website tool."""
        return {
            'title': 'Namibia Reisen - Test',
            'main_content': '<div>Test content about Namibia trips</div>',
            'status': 'success'
        }

    def test_agent_base_functions(self):
        """Test that base functions work correctly."""
        # Test time info function
        time_info = agent_base.get_current_time_info()
        assert 'date' in time_info
        assert 'time' in time_info
        assert 'weekday' in time_info
        
        # Test system prompt formatting
        prompt = agent_base.format_system_prompt("/test-endpoint")
        assert "/test-endpoint" in prompt
        assert "Chamäleon" in prompt
        
        # Test link processing
        text_with_links = "Visit /Afrika/Namibia and https://example.com"
        processed = agent_base.process_links_in_reply(text_with_links)
        assert '<a href="/Afrika/Namibia"' in processed
        assert '<a href="https://example.com"' in processed

    @patch('agent_base.chamaeleon_website_tool_base')
    def test_langchain_agent_call(self, mock_website_tool, sample_messages, sample_endpoint, mock_website_response):
        """Test the LangChain agent implementation."""
        # Mock the website tool
        mock_website_tool.return_value = mock_website_response
        
        # Mock the LangChain components
        with patch('agent_lang.create_react_agent') as mock_create_agent, \
             patch('agent_lang.model') as mock_model:
            
            # Mock agent response
            mock_agent_instance = MagicMock()
            mock_agent_instance.invoke.return_value = {
                "messages": [
                    MagicMock(content="Test response from LangChain agent", pretty_print=MagicMock())
                ]
            }
            mock_create_agent.return_value = mock_agent_instance
            
            # Call the agent
            result = agent_lang.call(sample_messages, sample_endpoint)
            
            # Verify the result structure
            assert isinstance(result, dict)
            assert 'reply' in result
            assert 'recommendations' in result
            assert isinstance(result['recommendations'], list)
            
            # Verify the agent was called
            mock_create_agent.assert_called_once()
            mock_agent_instance.invoke.assert_called_once()

    @patch('agent_base.chamaeleon_website_tool_base')
    def test_smolagents_agent_call(self, mock_website_tool, sample_messages, sample_endpoint, mock_website_response):
        """Test the smolagents agent implementation."""
        # Mock the website tool
        mock_website_tool.return_value = mock_website_response
        
        # Mock the smolagents components
        with patch('agent_smol.CodeAgent') as mock_code_agent, \
             patch('agent_smol.model') as mock_model:
            
            # Mock agent response
            mock_agent_instance = MagicMock()
            mock_agent_instance.run.return_value = "Test response from smolagents agent"
            mock_code_agent.return_value = mock_agent_instance
            
            # Call the agent
            result = agent_smol.call(sample_messages, sample_endpoint)
            
            # Verify the result structure
            assert isinstance(result, dict)
            assert 'reply' in result
            assert 'recommendations' in result
            assert isinstance(result['recommendations'], list)
            
            # Verify the agent was called
            mock_code_agent.assert_called_once()
            mock_agent_instance.run.assert_called_once()

    def test_both_agents_have_same_interface(self, sample_messages, sample_endpoint):
        """Test that both implementations have the same interface."""
        # Both should have a call function with the same signature
        assert hasattr(agent_lang, 'call')
        assert hasattr(agent_smol, 'call')
        
        # Check function signatures are compatible
        import inspect
        lang_sig = inspect.signature(agent_lang.call)
        smol_sig = inspect.signature(agent_smol.call)
        
        assert len(lang_sig.parameters) == len(smol_sig.parameters)
        assert list(lang_sig.parameters.keys()) == list(smol_sig.parameters.keys())

    @patch('agent_base.chamaeleon_website_tool_base')
    def test_error_handling(self, mock_website_tool, sample_messages, sample_endpoint):
        """Test error handling in both implementations."""
        # Test LangChain error handling
        with patch('agent_lang.create_react_agent') as mock_create_agent:
            mock_create_agent.side_effect = Exception("Test error")
            
            # Should not raise an exception
            try:
                result = agent_lang.call(sample_messages, sample_endpoint)
                # If it doesn't raise, it should return a valid structure
                assert isinstance(result, dict)
                assert 'reply' in result
                assert 'recommendations' in result
            except Exception:
                pytest.fail("LangChain agent should handle errors gracefully")
        
        # Test smolagents error handling
        with patch('agent_smol.CodeAgent') as mock_code_agent:
            mock_code_agent.side_effect = Exception("Test error")
            
            # Should not raise an exception
            try:
                result = agent_smol.call(sample_messages, sample_endpoint)
                # Should return error message
                assert isinstance(result, dict)
                assert 'reply' in result
                assert 'recommendations' in result
                assert 'technischen Fehler' in result['reply']
            except Exception:
                pytest.fail("Smolagents agent should handle errors gracefully")

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
        langchain_messages = agent_lang.convert_messages_to_langchain(sample_messages)
        assert len(langchain_messages) == len(sample_messages)
        
        # Test smolagents message conversion
        smol_conversation = agent_smol.convert_messages_to_smolagents(sample_messages)
        assert isinstance(smol_conversation, str)
        assert "User:" in smol_conversation
        assert "Assistant:" in smol_conversation

    @pytest.mark.integration
    @patch('agent_base.chamaeleon_website_tool_base')
    def test_integration_both_agents(self, mock_website_tool, sample_messages, sample_endpoint, mock_website_response):
        """Integration test comparing both agents with mocked dependencies."""
        mock_website_tool.return_value = mock_website_response
        
        # Mock both agents
        with patch('agent_lang.create_react_agent') as mock_lang_agent, \
             patch('agent_smol.CodeAgent') as mock_smol_agent:
            
            # Setup LangChain mock
            mock_lang_instance = MagicMock()
            mock_lang_instance.invoke.return_value = {
                "messages": [MagicMock(content="LangChain response", pretty_print=MagicMock())]
            }
            mock_lang_agent.return_value = mock_lang_instance
            
            # Setup smolagents mock
            mock_smol_instance = MagicMock()
            mock_smol_instance.run.return_value = "Smolagents response"
            mock_smol_agent.return_value = mock_smol_instance
            
            # Test both implementations
            lang_result = agent_lang.call(sample_messages, sample_endpoint)
            smol_result = agent_smol.call(sample_messages, sample_endpoint)
            
            # Both should return the same structure
            assert set(lang_result.keys()) == set(smol_result.keys())
            assert isinstance(lang_result['reply'], str)
            assert isinstance(smol_result['reply'], str)
            assert isinstance(lang_result['recommendations'], list)
            assert isinstance(smol_result['recommendations'], list)

if __name__ == "__main__":
    # Run tests if executed directly
    pytest.main([__file__, "-v"])
