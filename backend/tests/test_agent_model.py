"""验证官方兼容消息协议的工具续轮，不联网、不使用真实模型密钥。"""

from copy import deepcopy
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from backend.agent_contracts import AgentConfiguration, AgentReport
from backend.agent_model import MessageModel
from backend.runtime import ServiceError


class AgentModelTests(unittest.TestCase):
    """从下一轮实际消息与公开结果验证协议，避免推理上下文丢失或外泄。"""

    def test_tool_round_preserves_reasoning_without_publishing_it(self):
        """工具调用历史须带回原始推理字段，公开报告与工具记录不包含该字段。"""
        configuration = AgentConfiguration()
        model = MessageModel(configuration)
        requests = []
        responses = [
            (dict(content=None, reasoning_content='测试专用续轮上下文', tool_calls=[dict(id='tool-1', type='function',
                function=dict(name='read_state', arguments='{}'))]), dict(total_tokens=5)),
            (dict(content=json.dumps(dict(summary='测试结构化报告', suggested_tasks=[]))), dict(total_tokens=7)),
        ]

        class ToolFixture:
            """只返回测试状态，不访问沙盘或外部服务。"""

            schemas = [dict(type='function', function=dict(name='read_state', parameters=dict(type='object', properties={})))]

            def call(self, name, arguments):
                """验证模型工具参数并返回稳定夹具。"""
                self_test.assertEqual((name, arguments), ('read_state', {}))
                return dict(source='test_fixture')

        def complete(messages, tools, remaining_tokens=None):
            """保留每轮发送时的消息快照，避免后续列表修改影响断言。"""
            requests.append(deepcopy(messages))
            return responses[len(requests) - 1]

        self_test = self
        with patch.object(model, 'complete', side_effect=complete):
            result = model.analyze('recon', dict(source='test_fixture'), ToolFixture(), AgentReport, lambda: None)
        assistant_message = next(message for message in requests[1] if message['role'] == 'assistant')
        self.assertEqual(assistant_message['reasoning_content'], '测试专用续轮上下文')
        self.assertEqual(result['usage']['total_tokens'], 12)
        self.assertNotIn('测试专用续轮上下文', json.dumps(result, ensure_ascii=False))

    def test_official_reasoning_parameters_are_sent_without_fixed_temperature(self):
        """Kimi 类接口可使用完成 token 参数并省略固定采样温度，既有接口仍保留默认参数。"""
        for official in (False, True):
            with self.subTest(official=official):
                configuration = AgentConfiguration()
                configuration.model.enabled = True
                configuration.model.base_url = 'http://127.0.0.1:1/v1'
                configuration.model.model = 'explicit-test-fixture'
                if official:
                    configuration.model.temperature = None
                    configuration.model.output_token_parameter = 'max_completion_tokens'
                    configuration.model.reasoning_effort = 'low'
                response = dict(choices=[dict(message=dict(content='{}'), finish_reason='stop')])
                with patch('backend.agent_model.build_opener') as opener:
                    opener.return_value.open.return_value = BytesIO(json.dumps(response).encode('utf-8'))
                    MessageModel(configuration).complete([dict(role='user', content='测试')], [])
                payload = json.loads(opener.return_value.open.call_args.args[0].data)
                if official:
                    self.assertNotIn('temperature', payload)
                    self.assertNotIn('max_tokens', payload)
                    self.assertEqual(payload['max_completion_tokens'], 2048)
                    self.assertEqual(payload['reasoning_effort'], 'low')
                else:
                    self.assertEqual(payload['temperature'], 0.2)
                    self.assertEqual(payload['max_tokens'], 2048)
                    self.assertNotIn('reasoning_effort', payload)

    def test_truncated_response_is_reported_as_budget_failure(self):
        """模型耗尽输出上限时明确报错，不把半个结构化对象当作可采用结果。"""
        configuration = AgentConfiguration()
        configuration.model.enabled = True
        configuration.model.base_url = 'http://127.0.0.1:1/v1'
        configuration.model.model = 'explicit-test-fixture'
        response = dict(choices=[dict(message=dict(content='{'), finish_reason='length')])
        with patch('backend.agent_model.build_opener') as opener:
            opener.return_value.open.return_value = BytesIO(json.dumps(response).encode('utf-8'))
            with self.assertRaises(ServiceError) as caught:
                MessageModel(configuration).complete([dict(role='user', content='测试')], [])
        self.assertEqual(caught.exception.code, 'MODEL_OUTPUT_TRUNCATED')


if __name__ == '__main__':
    unittest.main()
