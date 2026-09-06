import unittest
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, Agent
import history_manager
from deep_research_openai import create_agents, classify_feedback_intent

class TestDeepResearchSystem(unittest.TestCase):
    def test_create_agents_3_pipeline(self):
        client = AsyncOpenAI(api_key="sk-test", base_url="https://api.deepseek.com")
        research_agent, elaboration_agent, verification_agent = create_agents(
            api_key="sk-test", 
            base_url_str="https://api.deepseek.com", 
            model_name_str="deepseek-chat"
        )
        
        # Verify all 3 agents are created
        self.assertEqual(research_agent.name, "research_agent")
        self.assertEqual(elaboration_agent.name, "elaboration_agent")
        self.assertEqual(verification_agent.name, "verification_agent")
        
        # Verify research_agent has deep_research tool
        self.assertTrue(len(research_agent.tools) > 0)
        # Verify verification_agent has deep_research tool for fact-checking
        self.assertTrue(len(verification_agent.tools) > 0)
        
    def test_classify_feedback_intent(self):
        # Verification routing tests
        self.assertEqual(classify_feedback_intent("请核对文中关于收入数据的准确性"), "verification_agent")
        self.assertEqual(classify_feedback_intent("I doubt the claim in section 2, please verify facts"), "verification_agent")
        self.assertEqual(classify_feedback_intent("请检查引用的来源是否真实"), "verification_agent")
        
        # Research routing tests
        self.assertEqual(classify_feedback_intent("请帮我联网检索一下最新的行业发展数据"), "research_agent")
        self.assertEqual(classify_feedback_intent("Search for more data about European market"), "research_agent")
        self.assertEqual(classify_feedback_intent("补充一些近期的最新进展"), "research_agent")
        
        # Elaboration routing tests
        self.assertEqual(classify_feedback_intent("请把结构调整得更清晰，增加两个企业落地案例"), "elaboration_agent")
        self.assertEqual(classify_feedback_intent("Elaborate on the technical architecture with more details"), "elaboration_agent")
        self.assertEqual(classify_feedback_intent("修改语气，更加学术严谨"), "elaboration_agent")

    def test_history_lifecycle(self):
        # Create a test session
        test_session = {
            "topic": "Autonomous Driving 2026",
            "provider": "DeepSeek",
            "model": "deepseek-chat",
            "initial_report": "Phase 1 report",
            "enhanced_report": "Phase 2 report",
            "verified_report": "Phase 3 report with Fact-Check Audit",
            "final_report": "Phase 3 report with Fact-Check Audit",
            "feedback_history": []
        }
        
        session_id = history_manager.save_session(test_session)
        self.assertIsNotNone(session_id)
        
        # Retrieve session
        retrieved = history_manager.get_session(session_id)
        self.assertEqual(retrieved["topic"], "Autonomous Driving 2026")
        self.assertEqual(retrieved["verified_report"], "Phase 3 report with Fact-Check Audit")
        
        # Append feedback
        updated = history_manager.append_feedback_to_session(
            session_id=session_id,
            user_request="Please add Waymo and Tesla comparisons",
            target_agent="Elaboration Agent",
            revised_report="Revised report with Waymo and Tesla"
        )
        self.assertEqual(updated["final_report"], "Revised report with Waymo and Tesla")
        self.assertEqual(len(updated["feedback_history"]), 1)
        
        # Clean up
        deleted = history_manager.delete_session(session_id)
        self.assertTrue(deleted)
        self.assertIsNone(history_manager.get_session(session_id))

if __name__ == "__main__":
    unittest.main()
