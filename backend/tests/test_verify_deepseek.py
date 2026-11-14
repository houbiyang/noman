"""验证官方连接报告的失败语义和脱敏，不访问真实服务。"""

import unittest
from unittest.mock import patch

from backend import verify_deepseek
from backend.runtime import ServiceError


class VerifyDeepSeekTests(unittest.TestCase):
    """连接测试成功不升级为游戏验收成功。"""

    def test_success_only_proves_connection(self):
        with patch.object(verify_deepseek.MessageModel, "complete", return_value=({"content": '{"connection":"ok"}'}, {"total_tokens": 10})):
            report = verify_deepseek.verify()
        self.assertTrue(report["connection_passed"])
        self.assertFalse(report["game_acceptance_passed"])
        self.assertEqual(report["model"], "deepseek-flash")

    def test_invalid_response_never_echoes_content(self):
        with patch.object(verify_deepseek.MessageModel, "complete", return_value=({"content": "private-response"}, {})):
            report = verify_deepseek.verify()
        self.assertFalse(report["connection_passed"])
        self.assertNotIn("private-response", str(report))

    def test_http_failure_is_not_retried(self):
        with patch.object(verify_deepseek.MessageModel, "complete", side_effect=ServiceError(502, "MODEL_HTTP_ERROR", "接口返回 HTTP 401")) as completion:
            report = verify_deepseek.verify()
        self.assertEqual(completion.call_count, 1)
        self.assertEqual(report["error_code"], "MODEL_HTTP_ERROR")


if __name__ == "__main__":
    unittest.main()
