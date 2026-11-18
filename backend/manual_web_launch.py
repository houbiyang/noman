"""本机手动新局的受控桥接：引导密钥不进入网页，创建意图先落盘再请求。"""
import hashlib
import json
from urllib.error import HTTPError, URLError
from uuid import UUID

from backend import database


class ManualWebLaunch:
    """由LocalWebLaunch提供固定上游、原子私有存储与进程内串行锁。"""

    @staticmethod
    def manual_uuid(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 36:
            raise ValueError('UUID must be a string')
        return str(UUID(value))

    def manual_active_path(self, map_hash, layout_hash):
        return self.state_path(f'web-manual-active-{map_hash}-{layout_hash}.json')

    def manual_context(self, token, body, request_key=None):
        """验证真实主持人会话和当前地图绑定；不相信网页传来的角色或地址。"""
        from backend.web_launch import WebLaunchError
        source = self.manual_uuid(body['game_id'])
        if not isinstance(token, str) or not token or len(token) > 256 or '\r' in token or '\n' in token:
            raise WebLaunchError('请先接入主持人席位')
        seat = self.request('GET', '/session', token=token)
        if seat.get('role') != 'host' or seat.get('game_id') != source:
            raise WebLaunchError('只有当前对局的主持人可以配置新局')
        path = self.project_root / 'unity-client/Assets/Sandbox/Resources/Maps/QinglanValley/map-definition.json'
        map_hash = json.loads(path.read_text(encoding='utf-8'))['contentHash']
        layout = path.with_name('capture-points.json')
        layout_hash = hashlib.sha256(layout.read_bytes()).hexdigest() if layout.exists() else 'legacy'
        if body['map_hash'] != map_hash or body['capture_layout_sha256'] != layout_hash:
            raise WebLaunchError('地图或据点版本已变化，请刷新页面')
        active = self.manual_active_path(map_hash, layout_hash)
        current_path = active if active.exists() else self.state_path(f'web-launch-qinglan-control-v2-{map_hash}-{layout_hash}.json')
        current = json.loads(current_path.read_text(encoding='utf-8'))
        # 同一次创建在切换成功但响应丢失后可恢复；旧局不能重新激活历史新局。
        same_creation = request_key and current.get('creation_request_key') == request_key and current.get('source_game_id') == source
        if current.get('game_id') != source and not same_creation:
            raise WebLaunchError('本机已切换其他对局，请重新接入主持人，不能覆盖当前对局')
        return source, map_hash, layout_hash, active, current

    def manual_setup(self, token, body):
        from backend.web_launch import WebLaunchError
        try:
            with self.lock:
                source, _, _, _, _ = self.manual_context(token, body)
                result = self.request('GET', '/battle/setup', bootstrap=True)
                pending = self.state_path(f'web-manual-pending-{source}.json')
                result['pending_creation'] = None
                if pending.exists():
                    record = json.loads(pending.read_text(encoding='utf-8'))
                    if record.get('status') == 'pending':
                        result['pending_creation'] = dict(request_key=record['creation_request_key'], payload=record['creation_payload'])
                return result
        except (OSError, URLError, ValueError, KeyError, TypeError) as error:
            raise WebLaunchError('无法读取手动配置，请确认主持人会话与本机服务') from error

    def manual_preview(self, token, body):
        """Host-owned local staging only; no game writes or client-supplied geometry."""
        from backend.manual_deployment_preview import CampPreviewRequest
        from backend.web_launch import WebLaunchError
        try:
            with self.lock:
                self.manual_context(token, body)
                request = CampPreviewRequest.model_validate(body['placement'])
                return self.request('POST', '/battle/deployment-preview', request.model_dump(mode='json'), bootstrap=True)
        except HTTPError as error:
            raise WebLaunchError('该位置未通过兵营及完整出兵区验收，请在本方已核验区域重新选址。') from error
        except (OSError, URLError, ValueError, KeyError, TypeError) as error:
            raise WebLaunchError('部署验收未完成，请确认连接后重试；未创建或修改对局。') from error

    def manual_validate(self, token, body):
        from backend.battle_setup import CreateManualBattle
        from backend.web_launch import WebLaunchError
        try:
            with self.lock:
                self.manual_context(token, body)
                payload = CreateManualBattle.model_validate(body['payload']).model_dump(mode='json')
                return self.request('POST', '/battle/deployment-validate', payload, bootstrap=True)
        except HTTPError as error:
            self.reject_known_failure(None, None, error)
            raise WebLaunchError('整体部署验收未完成，请检查连接后重试；未创建对局。') from error
        except (OSError, URLError, ValueError, KeyError, TypeError) as error:
            raise WebLaunchError('无法核对整体部署，请确认连接后重试；未创建对局。') from error

    def manual_create(self, token, body, *, recover=False):
        """不确定结果仅沿用原键/原正文；签发主持人成功后才切换所有角色的目标局。"""
        from backend.web_launch import WebLaunchError
        from backend.battle_setup import CreateManualBattle
        from backend.runtime import sha256
        try:
            key = self.manual_uuid(body['request_key'])
            with self.lock:
                source, map_hash, layout_hash, active, current = self.manual_context(token, body, key)
                path = self.state_path(f'web-manual-pending-{source}.json')
                record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
                if record and record['creation_request_key'] != key and record['status'] != 'rejected':
                    raise WebLaunchError('已有创建请求尚未核对，请恢复原请求，不要重复创建')
                if recover:
                    if not record or record['creation_request_key'] != key:
                        raise WebLaunchError('没有可恢复的本机创建记录')
                else:
                    # 严格模型仅做格式校验；不在本机代理伪造几何或发布状态。
                    payload = CreateManualBattle.model_validate(body['payload']).model_dump(mode='json')
                    if record and record['creation_request_key'] == key:
                        if record['payload_hash'] != sha256(payload):
                            raise WebLaunchError('同一创建请求不能修改配置，请先核对原结果')
                    else:
                        record = dict(source_game_id=source, map_hash=map_hash, capture_layout_sha256=layout_hash,
                            creation_request_key=key, creation_payload=payload, payload_hash=sha256(payload),
                            status='pending', sessions={})
                        self.save(path, record)  # 必须先落盘，网络超时/刷新后仍能恢复。
                if record['status'] == 'active':
                    return self.launch_seat(active, current, 'host')
                if not record.get('game_id'):
                    if recover:
                        try:
                            game = self.request('GET', '/game-creations/' + key, bootstrap=True)
                        except HTTPError as error:
                            if error.code == 404:
                                raise WebLaunchError('尚未查到创建结果；保留原配置，请使用原请求重试，不能另建新局') from error
                            raise
                    else:
                        record['status'] = 'pending'
                        self.save(path, record)
                        try:
                            game = self.request('POST', '/battle/games', record['creation_payload'], bootstrap=True, request_key=key)
                        except HTTPError as error:
                            # 只将后端明确的未创建错误标记为可编辑；未知5xx保持不确定。
                            self.reject_known_failure(path, record, error)
                            raise
                    expected = sha256(dict(protocol='qinglan-manual-creation-v1', payload=record['creation_payload']))
                    if (game.get('creation_request_key') != key or game.get('creation_payload_sha256') != expected
                            or game.get('scenario_version_id') != record['creation_payload']['scenario_version_id']
                            or game.get('rule_version_id') != record['creation_payload']['rule_version_id']):
                        raise WebLaunchError('创建摘要与原手动配置不一致，拒绝切换；请保留原请求核对。')
                    record['game_id'] = self.manual_uuid(game['game_id'])
                    self.save(path, record)
                # launch_seat写入的是意图文件，不能在签发成功前修改active pointer。
                launch = self.launch_seat(path, record, 'host')
                if current.get('game_id') != record['game_id']:
                    self.save(active, record)
                record['status'] = 'active'
                self.save(path, record)
                return launch
        except (OSError, URLError, ValueError, KeyError, TypeError) as error:
            raise WebLaunchError('创建未确认；请恢复或重试原请求，不要重新创建。规则与地图验收不会被绕过。') from error

    def reject_known_failure(self, path, record, error):
        """只展示白名单业务信息，不透传上游正文、地址、验证输入或任何凭据。"""
        from backend.web_launch import WebLaunchError
        messages = {
            'BATTLE_SCENARIO_NOT_READY': '总册场景尚未通过验收或发布；地图保持不变，本次未创建新局。',
            'BATTLE_VERSION_NOT_READY': '总册或容量版本尚未发布，本次未创建新局。',
            'BATTLE_VERSION_CHANGED': '配置来源已更新，请重新读取配置，本次未创建新局。',
            'BATTLE_COMMAND_SCHEMA_NOT_READY': '数据库命令约束尚未就绪，本次未创建新局。',
            'INPUT_INVALID': '配置字段或范围无效，本次未创建新局。',
            'BATTLE_CAMP_BLOCKS_ROUTE': '兵营阻挡了补给/停靠通道。请重新生成相邻兵营的单位或调整兵营位置，再确认部署；本次未创建新局。',
            'BATTLE_DEPLOYMENT_OVERLAP': '兵营或出兵区相互重叠。请重新生成相邻兵营的单位或调整位置；本次未创建新局。',
            'BATTLE_DOCK_INVALID': '停靠面被兵营占用，请重新生成单位或调整位置；本次未创建新局。',
            'BATTLE_DEPLOYMENT_OUTSIDE': '兵营或单位完整占地超出本方部署区；本次未创建新局。',
            'BATTLE_DEPLOYMENT_INVALID': '单位部署或兵营服务面未通过验收，本次未创建新局。',
            'BATTLE_DEPLOYMENT_NOT_READY': '总册部署验收尚未实现，本次未创建新局。',
            'BATTLE_ENGINE_NOT_READY': '总册地图执行器尚未就绪，本次未创建新局。',
        }
        try:
            value = json.loads(error.read(8192))
            code = value.get('error', {}).get('code')
        except (ValueError, AttributeError, OSError):
            return
        if code in messages and error.code in (400, 409, 422, 503):
            if record is not None:
                record['status'] = 'rejected'
                self.save(path, record)
            raise WebLaunchError(messages[code]) from error
