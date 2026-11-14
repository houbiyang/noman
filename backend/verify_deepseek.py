"""从当前终端环境验证官方模型，只保存脱敏验收结果，不读取聊天中的密钥。"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from backend.agent_contracts import AgentConfiguration
from backend.agent_model import MessageModel
from backend.runtime import ServiceError


def configuration() -> AgentConfiguration:
    """生成用户指定的官方 V4.1 Flash 配置；模型别名依据官方接口文档。"""
    value = AgentConfiguration()
    value.model.enabled = True
    value.model.base_url = "https://api.deepseek.com"
    value.model.model = "deepseek-flash"
    value.model.credential_ref = "DEEPSEEK_API_KEY"
    value.model.temperature = None
    value.model.reasoning_effort = "low"
    value.model.timeout_seconds = 60
    return value


def verify() -> dict:
    """执行一次短结构化请求；失败不自动重试，原始响应不进入报告。"""
    value = configuration()
    result = dict(checked_at=datetime.now(timezone.utc).isoformat(), model=value.model.model,
                  endpoint=value.model.base_url, credential_ref=value.model.credential_ref,
                  connection_passed=False, game_acceptance_passed=False)
    try:
        message, usage = MessageModel(value).complete([
            dict(role="system", content='这是连接验证。只输出 JSON 对象 {"connection":"ok"}，不要调用工具。'),
            dict(role="user", content="请验证响应。"),
        ], [])
        content = message.get("content") or ""
        if content.startswith("```json") and content.rstrip().endswith("```"):
            content = content.strip()[7:-3].strip()
        result.update(connection_passed=json.loads(content) == {"connection": "ok"}, usage=usage)
        if not result["connection_passed"]:
            result["error_code"] = "MODEL_OUTPUT_INVALID"
    except ServiceError as error:
        result["error_code"] = error.code
        result["message"] = error.message
    except (ValueError, TypeError):
        result["error_code"] = "MODEL_OUTPUT_INVALID"
    return result


def main() -> None:
    """仅在显式运行时调用模型；输出路径固定在项目忽略目录。"""
    argparse.ArgumentParser(description="验证 DeepSeek 官方连接，产生一次正常 API 调用费用").parse_args()
    result = verify()
    target = Path(__file__).resolve().parents[1] / ".local/rules-implementation-20261113/deepseek-verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["connection_passed"] else 1)


if __name__ == "__main__":
    main()
