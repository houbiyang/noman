"""验证本机交付检查的准入规则及脱敏，不连接真实数据库和模型。"""

from contextlib import ExitStack
import unittest
from unittest.mock import patch

from backend import delivery_check


class DeliveryCheckTests(unittest.TestCase):
    """软件可启动、配置齐备与真实推理验收必须分别报告。"""

    def report(self, configured=True, require_model=False, services=False):
        """替换外部环境检查，只测试明确的交付准入决策。"""
        with ExitStack() as stack:
            for name in ('dependency_versions', 'required_files', 'database_readiness', 'service_readiness'):
                stack.enter_context(patch.object(delivery_check, name, return_value={}))
            stack.enter_context(patch.object(delivery_check, 'model_readiness', return_value={
                'red': dict(configured=configured), 'blue': dict(configured=configured)}))
            return delivery_check.collect_checks(require_model, services)

    def test_missing_models_allow_configuration_but_block_strict_acceptance(self):
        """未配置模型时可以进入配置页面，严格交付检查必须失败。"""
        self.assertTrue(self.report(configured=False)['passed'])
        report = self.report(configured=False, require_model=True)
        self.assertTrue(report['software_ready'])
        self.assertFalse(report['models_configured'])
        self.assertFalse(report['passed'])

    def test_configured_models_do_not_imply_real_inference_verified(self):
        """配置检查成功时仍明确保留真实推理未验收状态。"""
        report = self.report(require_model=True, services=True)
        self.assertTrue(report['passed'])
        self.assertFalse(report['real_model_verified'])

    def test_failed_service_blocks_software_readiness_without_exposing_details(self):
        """运行旧代码或网络失败时必须拒绝就绪，不公开异常原文。"""
        with patch.object(delivery_check, 'dependency_versions', return_value={}), \
                patch.object(delivery_check, 'required_files', return_value={}), \
                patch.object(delivery_check, 'database_readiness', return_value={}), \
                patch.object(delivery_check, 'model_readiness', return_value={'red': dict(configured=True), 'blue': dict(configured=True)}), \
                patch.object(delivery_check, 'service_readiness', side_effect=ValueError('private-connection-value')):
            report = delivery_check.collect_checks(services=True)
        self.assertFalse(report['software_ready'])
        self.assertFalse(report['passed'])
        self.assertNotIn('private-connection-value', str(report))


if __name__ == '__main__':
    unittest.main()
