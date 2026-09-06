import unittest
import os
import shutil
import history_manager

def classify_feedback_intent(feedback: str) -> str:
    feedback_lower = feedback.lower()
    
    # Verification keywords
    verify_kw = ["核实", "事实", "准确", "质疑", "数据对不对", "验算", "真伪", "打假", "有误", "假新闻", "真实性", "verify", "fact", "accuracy", "audit", "true", "false", "check claim", "doubt"]
    if any(k in feedback_lower for k in verify_kw):
        return "verification_agent"
        
    # Research keywords
    research_kw = ["搜索", "检索", "查一下", "补充数据", "最新进展", "额外信息", "最新消息", "联网查", "找资料", "search", "find", "latest", "more data", "web", "lookup", "sources"]
    if any(k in feedback_lower for k in research_kw):
        return "research_agent"
        
    # Default to elaboration for tone, style, explanation, structure, case studies
    return "elaboration_agent"

class TestHistoryManager(unittest.TestCase):
    def setUp(self):
        # Use a temp test dir
        self.test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_research_history')
        history_manager.HISTORY_DIR = self.test_dir
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        os.makedirs(self.test_dir, exist_ok=True)
        
    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
            
    def test_save_and_get_session(self):
        sample_data = {
            "topic": "Quantum Computing 2026",
            "provider": "DeepSeek",
            "model": "deepseek-chat",
            "initial_report": "Initial report content",
            "enhanced_report": "Enhanced content",
            "verified_report": "Verified content",
            "final_report": "Final verified content",
            "sources": [{"url": "https://example.com", "title": "Example"}]
        }
        session_id = history_manager.save_session(sample_data)
        self.assertIsNotNone(session_id)
        
        loaded = history_manager.get_session(session_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["topic"], "Quantum Computing 2026")
        self.assertEqual(loaded["final_report"], "Final verified content")
        
    def test_list_and_delete_sessions(self):
        data1 = {"topic": "Topic 1", "final_report": "Report 1"}
        data2 = {"topic": "Topic 2", "final_report": "Report 2"}
        
        id1 = history_manager.save_session(data1)
        id2 = history_manager.save_session(data2)
        
        sessions = history_manager.list_sessions()
        self.assertEqual(len(sessions), 2)
        
        # Delete id1
        deleted = history_manager.delete_session(id1)
        self.assertTrue(deleted)
        
        sessions_after = history_manager.list_sessions()
        self.assertEqual(len(sessions_after), 1)
        self.assertEqual(sessions_after[0]["session_id"], id2)
        
    def test_append_feedback(self):
        data = {"topic": "AI Ethics", "final_report": "Original Report"}
        session_id = history_manager.save_session(data)
        
        updated = history_manager.append_feedback_to_session(
            session_id=session_id,
            user_request="Please add case studies",
            target_agent="elaboration_agent",
            revised_report="Revised Report with Cases"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["final_report"], "Revised Report with Cases")
        self.assertEqual(len(updated["feedback_history"]), 1)
        self.assertEqual(updated["feedback_history"][0]["target_agent"], "elaboration_agent")

    def test_intent_classification(self):
        # Test verification intent
        self.assertEqual(classify_feedback_intent("请核实一下文中2025年的市场规模数据是否准确"), "verification_agent")
        self.assertEqual(classify_feedback_intent("Please fact-check this claim about GPU memory"), "verification_agent")
        
        # Test research intent
        self.assertEqual(classify_feedback_intent("帮我联网搜索一下最近一周的最新进展并补充进来"), "research_agent")
        self.assertEqual(classify_feedback_intent("Please search for more data on European markets"), "research_agent")
        
        # Test elaboration intent
        self.assertEqual(classify_feedback_intent("请把结论部分精炼成三个核心论点，语气更加正式"), "elaboration_agent")
        self.assertEqual(classify_feedback_intent("Can you elaborate on the second section with more examples?"), "elaboration_agent")

if __name__ == '__main__':
    unittest.main()
