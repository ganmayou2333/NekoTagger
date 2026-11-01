"""种子数据：把 NekoTrak 分类页抓下来的显示名映射到类别。

种子是**用户亲手分过的**，权威性最高，但权威只建立在「配对是确定的」上：
只允许精确与词边界匹配，裸子串一律禁止（实测种子 ``Microsoft`` 会命中
39 个 exe）。同名出现在多个类别时视为歧义，不产生结论。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .categories import CategorySet

#: 这些词太常见，永远不参与匹配（路径与显示名都可能撞上）。
GENERIC_TOKENS = frozenset(
    {
        "microsoft",
        "windows",
        "x64",
        "x86",
        "app",
        "apps",
        "setup",
        "install",
        "installer",
        "application",
        "desktop",
        "download",
        "downloads",
        "program",
        "programs",
        "temp",
        "users",
        "system",
        "update",
    }
)

_BRACKET_RE = re.compile(r"\s*[\(\[（【][^\)\]）】]*[\)\]）】]\s*$")
_VERSION_RE = re.compile(r"[\s_-]*v?\d+(?:\.\d+){1,3}[\s_-]*$")
_TOKEN_RE = re.compile(r"[0-9a-z]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

#: 单 token 的种子至少要有这么长才参与词边界匹配。``inf`` / ``app`` 这种
#: 三字母词几乎必然会撞上路径里的片段，实测把 ``setup.exe`` 误判成「未分类」。
MIN_WORD_TOKEN_LENGTH = 4


class SeedDataError(ValueError):
    """种子文件缺失或非法。"""


def normalize_display_name(raw: str) -> str:
    """显示名规范化：NFKC → 小写 → 去尾部括号（最多两层）→ 去尾部版本号。"""

    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    for _ in range(2):
        text = _BRACKET_RE.sub("", text).strip()
    text = _VERSION_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def tokenize(text: str) -> tuple[str, ...]:
    """按非字母数字切词；中文没有空格，所以中文名切出的 token 为空。"""

    return tuple(_TOKEN_RE.findall(normalize_display_name(text)))


@dataclass(frozen=True)
class SeedName:
    name: str
    category_id: str
    normalized: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class SeedMatch:
    seed: SeedName
    kind: str  # "exact" | "word"
    candidate: str


def _contains_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    """needle 是否作为连续子序列出现在 haystack 里。"""

    width = len(needle)
    if width == 0 or width > len(haystack):
        return False
    return any(
        haystack[start : start + width] == needle
        for start in range(len(haystack) - width + 1)
    )


class SeedIndex:
    """已去重、已标歧义的种子索引。"""

    def __init__(
        self,
        entries: Iterable[SeedName],
        *,
        ambiguous: Iterable[str] = (),
        harvested_at: str | None = None,
    ) -> None:
        self._exact: dict[str, SeedName] = {}
        self._by_token: dict[tuple[str, ...], SeedName] = {}
        self.ambiguous = frozenset(ambiguous)
        self.harvested_at = harvested_at
        for entry in entries:
            if entry.normalized in self.ambiguous:
                continue
            if self._is_generic(entry):
                continue
            self._exact.setdefault(entry.normalized, entry)
            if not self._is_word_matchable(entry):
                continue
            self._by_token.setdefault(entry.tokens, entry)

    @staticmethod
    def _is_word_matchable(seed: SeedName) -> bool:
        """词边界只对「切词结果能代表原名」的种子成立。

        中文没有空格，``INF 默认安装`` 会被切成 ``("inf",)``——丢掉中文后就剩一个
        通用三字母词，能撞上任何含 ``inf`` 的路径。这类种子只允许精确匹配。
        """

        if not seed.tokens:
            return False
        if _CJK_RE.search(seed.name):
            return False
        if len(seed.tokens) == 1:
            return len(seed.tokens[0]) >= MIN_WORD_TOKEN_LENGTH
        return True

    @staticmethod
    def _is_generic(seed: SeedName) -> bool:
        if seed.normalized in GENERIC_TOKENS:
            return True
        if not seed.tokens:
            return False
        return all(token in GENERIC_TOKENS for token in seed.tokens)

    def __len__(self) -> int:
        return len(self._exact)

    @property
    def category_count(self) -> int:
        return len({entry.category_id for entry in self._exact.values()})

    def lookups(self) -> tuple[SeedName, ...]:
        return tuple(self._exact.values())

    def match(self, candidate: str) -> SeedMatch | None:
        """拿一个候选显示名（来自版本信息/文件名）去匹配种子。"""

        normalized = normalize_display_name(candidate)
        if not normalized or normalized in self.ambiguous:
            return None

        exact = self._exact.get(normalized)
        if exact is not None:
            return SeedMatch(exact, "exact", candidate)

        tokens = tokenize(candidate)
        if not tokens:
            return None
        word = self._by_token.get(tokens)
        if word is not None:
            return SeedMatch(word, "word", candidate)

        # 词边界也接受「种子 token 连续出现在候选 token 里」：种子 `OBS Studio`
        # 应该对上 `OBS Studio 64`。只看整串相等会让词边界形同虚设。
        for seed_tokens, entry in self._by_token.items():
            if len(seed_tokens) > len(tokens):
                continue
            if _contains_sequence(tokens, seed_tokens):
                return SeedMatch(entry, "word", candidate)
        return None

    def hints_for(self, text: str, limit: int = 20) -> list["SeedName"]:
        """为提示词挑相似种子：token 重叠 + 规范化相等。只作参考，不作结论。"""

        normalized = normalize_display_name(text)
        tokens = set(tokenize(text))
        scored: list[tuple[int, SeedName]] = []
        for entry in self._exact.values():
            score = 0
            if entry.normalized == normalized:
                score += 5
            score += len(tokens & set(entry.tokens))
            if normalized and (normalized in entry.normalized or entry.normalized in normalized):
                score += 1
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [entry for _score, entry in scored[:limit]]

    def representatives(self, category_id: str, limit: int = 2) -> list["SeedName"]:
        """某个类别下挑几个代表，供提示词在「没有相似项」时兜底。"""

        picked = [entry for entry in self._exact.values() if entry.category_id == category_id]
        picked.sort(key=lambda entry: entry.name)
        return picked[:limit]

    def best(self, candidates: Iterable[str]) -> SeedMatch | None:
        """按候选顺序取第一个精确命中；没有精确的再取第一个词边界命中。"""

        word_hit: SeedMatch | None = None
        for candidate in candidates:
            match = self.match(candidate)
            if match is None:
                continue
            if match.kind == "exact":
                return match
            if word_hit is None:
                word_hit = match
        return word_hit


def load_seeds(path: str | Path, categories: CategorySet) -> SeedIndex:
    """读取种子文件。类别键是**中文显示名**，需要经 ``categories`` 映射成 id。"""

    target = Path(path)
    try:
        document = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SeedDataError(f"找不到种子文件：{target}") from exc
    except json.JSONDecodeError as exc:
        raise SeedDataError(f"种子文件不是合法 JSON：{target}：{exc}") from exc
    except OSError as exc:
        raise SeedDataError(f"种子文件读取失败：{target}：{exc}") from exc

    meta: dict[str, Any] = document.get("_meta", {}) if isinstance(document, dict) else {}
    category_block = meta.get("categories")
    if not isinstance(category_block, dict) or not category_block:
        raise SeedDataError(f"种子文件缺少 _meta.categories：{target}")

    label_to_id = {category.label: category.id for category in categories}

    by_name: dict[str, set[str]] = {}
    for label, payload in category_block.items():
        category_id = label_to_id.get(label)
        if category_id is None:
            # 类别清单和种子文件不同步（用户改过 categories.json），跳过并继续。
            continue
        names = payload.get("names", []) if isinstance(payload, dict) else []
        if not isinstance(names, list):
            continue
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            by_name.setdefault(name.strip(), set()).add(category_id)

    entries: list[SeedName] = []
    ambiguous: set[str] = set()
    skipped_uncategorized = 0
    for name, category_ids in by_name.items():
        # 种子里的「未分类」不携带任何信息：用它只能证明「它没被分类」，
        # 拿它当结论是循环论证，还会挡住后面本该由规则/AI 给出的判断。
        category_ids = {cid for cid in category_ids if cid != "uncategorized"}
        if not category_ids:
            skipped_uncategorized += 1
            continue
        normalized = normalize_display_name(name)
        if not normalized:
            continue
        if len(category_ids) > 1:
            ambiguous.add(normalized)
            continue
        entries.append(
            SeedName(
                name=name,
                category_id=next(iter(category_ids)),
                normalized=normalized,
                tokens=tokenize(name),
            )
        )

    harvested_at = meta.get("harvestedAt")
    return SeedIndex(
        entries,
        ambiguous=ambiguous,
        harvested_at=harvested_at if isinstance(harvested_at, str) else None,
    )
