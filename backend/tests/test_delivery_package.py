"""验证交付包的私有路径排除与文件摘要完整性，不操作真实交付目录。"""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from backend.build_agent_delivery import public_path, verify_archive


class DeliveryPackageTests(unittest.TestCase):
    """阻止私有配置混入交付包，并识别归档成员被修改。"""

    def test_private_and_ambiguous_paths_are_rejected(self):
        """Windows 路径别名、路径穿越及凭据路径不能作为公开归档成员。"""
        for path in ('backend/.local/admin-password', '.env', 'cert.pem', 'C:/secret.json',
                     '../secret.json', 'backend\\..\\secret.json', '.git/config', 'bootstrap-key'):
            with self.subTest(path=path):
                self.assertFalse(public_path(path))
        self.assertTrue(public_path('backend/agent_graph.py'))
        self.assertTrue(public_path('evidence/environment.json'))

    def test_changed_archive_member_fails_checksum_verification(self):
        """即使归档目录与文件长度一致，内容摘要变化也必须拒绝通过。"""
        with TemporaryDirectory(prefix='qiongtu-package-test-') as folder:
            path = Path(folder) / 'changed.zip'
            expected = dict(sha256=hashlib.sha256(b'original').hexdigest(), bytes=8)
            manifest = dict(files={'backend/source.py': expected}, real_model_verified=False)
            with ZipFile(path, 'w') as archive:
                archive.writestr('backend/source.py', b'modified')
                archive.writestr('delivery-manifest.json', json.dumps(manifest))
            with self.assertRaises(ValueError):
                verify_archive(path)


if __name__ == '__main__':
    unittest.main()
