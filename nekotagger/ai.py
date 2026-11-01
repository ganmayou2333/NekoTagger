"""AI 后端：OpenAI 兼容的 chat/completions，批量分类。

设计取舍（来自 grilling）：

- 批量：默认 20 条/批，**不重试**——失败整批进 pending，下一轮再说。
- 枚举校验：模型返回的类别必须在 ``categories.json`` 里，否则整条丢弃。
- 提示词只放路径与进程名，**不放窗口标题/用户名**；种子只作参考。
- 只用 stdlib ``urllib``，不引新依赖。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from .categories import CategorySet
from .seeds import SeedIndex

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BATCH_SIZE = 20
DEFAULT_TIMEOUT_SECONDS = 10.0

CONFIG_ENV_KEYS = {
    "api_key": "NEKOTAGGER_API_KEY",
    "base_url": "NEKOTAGGER_BASE_URL",
    "model": "NEKOTAGGER_MODEL",
}


class AiConfigError(ValueError):
    """AI 配置非法。"""


@dataclass(frozen=True)
class AiConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = None

    @property
    def usable(self) -> bool:
        return bool(self.api_key)


def default_config_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "NekoTagger" / "config.local.json"


def load_ai_config(path: str | os.PathLike[str] | None = None) -> AiConfig:
    """读配置；文件缺失不算错，环境变量优先覆盖。"""

    target = Path(path) if path is not None else default_config_path()
    values: dict[str, str] = {}
    if target.exists():
        try:
            document = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AiConfigError(f"AI 配置读取失败：{target}：{exc}") from exc
        if not isinstance(document, dict):
            raise AiConfigError(f"AI 配置根部必须是对象：{target}")
        for key in CONFIG_ENV_KEYS:
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                values[key] = value.strip()

    for key, env_name in CONFIG_ENV_KEYS.items():
        env_value = os.environ.get(env_name)
        if env_value and env_value.strip():
            values[key] = env_value.strip()

    return AiConfig(
        base_url=values.get("base_url", DEFAULT_BASE_URL),
        model=values.get("model", DEFAULT_MODEL),
        api_key=values.get("api_key"),
    )


@dataclass
class AiBatchItem:
    path: str  # 规范化路径
    display: str  # 给模型看的可读名（文件名或版本信息名）


class AiBackend(Protocol):
    def classify(self, items: list[AiBatchItem], categories: CategorySet, seeds: SeedIndex | None) -> Mapping[str, dict]:
        """返回 ``{path: {category, confidence, reason}}``；失败的条目可以缺席。"""


SYSTEM_PROMPT = (
    "You classify desktop applications into fixed categories. "
    "Answer with JSON only: an object with a key \"results\" holding an array of "
    "{\"path\": string, \"category\": string, \"confidence\": \"certain\"|\"likely\"|\"guess\", "
    "\"reason\": string}. Use only the category ids provided. If unsure, pick "
    "\"uncategorized\" rather than inventing a category."
)


def _category_menu(categories: CategorySet) -> str:
    return "\n".join(f"- {category.id} ({category.label})" for category in categories)


def build_messages(
    items: list[AiBatchItem],
    categories: CategorySet,
    seeds: SeedIndex | None,
) -> list[dict[str, str]]:
    """构造提示词。只用路径与显示名，绝不包含窗口标题/用户名。"""

    menu = _category_menu(categories)
    lines = [f"Categories:\n{menu}", "", "Applications to classify:"]
    for item in items:
        lines.append(f"- path: {item.path}")
        lines.append(f"  name: {item.display}")

    if seeds is not None:
        hints = []
        for item in items:
            similar = seeds.hints_for(item.display, limit=5)
            for seed in similar:
                hints.append(f"- {seed.name} => {seed.category_id}")
        if not hints:
            for category in categories:
                for representative in seeds.representatives(category.id, limit=2):
                    hints.append(f"- {representative.name} => {representative.category_id}")
        if hints:
            deduped = list(dict.fromkeys(hints))[:20]
            lines.extend(["", "The user previously classified these applications (reference only):", *deduped])

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def _extract_json(text: str) -> object | None:
    """从模型回复里取出 JSON；容忍 ```json 围栏与前后废话。"""

    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def validate_results(
    raw: object, categories: CategorySet, expected_paths: Iterable[str]
) -> tuple[dict[str, dict], list[str]]:
    """校验模型输出：类别必须命中枚举，否则丢弃。返回 (接受, 被拒路径)。"""

    accepted: dict[str, dict] = {}
    rejected: list[str] = []
    expected = set(expected_paths)
    rows = raw.get("results") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {}, list(expected)

    for row in rows:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        category = row.get("category")
        if not isinstance(path, str) or not isinstance(category, str):
            continue
        if path not in expected:
            continue
        if category not in categories:
            rejected.append(path)
            continue
        confidence = row.get("confidence")
        accepted[path] = {
            "category": category,
            "confidence": confidence if confidence in {"certain", "likely", "guess"} else "likely",
            "reason": str(row.get("reason") or "").strip()[:160],
        }
    missing = [path for path in expected if path not in accepted and path not in rejected]
    return accepted, rejected + missing


class HttpBackend:
    """真实的 OpenAI 兼容后端。一个 batch 一次请求，不重试。"""

    def __init__(self, config: AiConfig, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if not config.usable:
            raise AiConfigError("没有配置 api_key，无法调用 AI")
        self._config = config
        self._timeout = timeout

    def _post(self, messages: list[dict[str, str]]) -> str:
        payload = json.dumps(
            {
                "model": self._config.model,
                "messages": messages,
                "temperature": 0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self._config.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._config.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - 用户配置的地址
            body = json.loads(response.read(4 * 1024 * 1024).decode("utf-8", "replace"))
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("响应里没有 choices")
        return str(choices[0].get("message", {}).get("content") or "")

    def classify(
        self,
        items: list[AiBatchItem],
        categories: CategorySet,
        seeds: SeedIndex | None,
    ) -> Mapping[str, dict]:
        if not items:
            return {}
        messages = build_messages(items, categories, seeds)
        content = self._post(messages)
        parsed = _extract_json(content)
        if parsed is None:
            raise ValueError("模型回复不是可解析的 JSON")
        accepted, _rejected = validate_results(parsed, categories, [item.path for item in items])
        return accepted


class StubBackend:
    """测试用后端：记录调用、按配置返回结果。"""

    def __init__(self, results: Mapping[str, dict] | None = None, *, fail: bool = False) -> None:
        self._results = dict(results or {})
        self._fail = fail
        self.calls: list[list[str]] = []

    def classify(self, items, categories, seeds) -> Mapping[str, dict]:
        self.calls.append([item.path for item in items])
        if self._fail:
            raise TimeoutError("stub backend failure")
        return {
            item.path: self._results[item.path]
            for item in items
            if item.path in self._results
        }
