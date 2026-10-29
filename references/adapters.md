# references/adapters.md — Provider / Adapter 接口

> 吸收自 Euraika-Labs/personal-assistant-memory-routing 的 Provider 抽象思想。
> 目标：**替换存储/检索/反馈实现时，Core 不重写。**
> MVP 全部用 Markdown 实现；未来可换 SQLite / Vector DB / Mem0 / 其他 Memory，Core 不变。

## 一、接口总览

| Provider | 职责 | 默认实现 |
|---|---|---|
| `MemoryProvider` | 读写用户记忆（style/preferences/taboos/people/events/feedback/candidates） | `MarkdownMemoryProvider` |
| `KnowledgeProvider` | 读写知识库（cases/strategies）+ 检索 | `MarkdownKnowledgeProvider` |
| `Retriever` | 根据 query 检索记忆 + 知识，按优先级返回 | `MarkdownRetriever` |
| `FeedbackProvider` | 记录用户反馈 → 学习信号 | `MarkdownFeedbackProvider` |
| `StyleProvider` | 获取用户当前推荐表达风格 | `MemoryStyleProvider`（未来可换成外部"语气学习 Skill"） |
| `ContextProvider` | 提供当前对话上下文（本轮+最近几轮） | 宿主注入（默认最小实现） |
| `RetrievalService` | **统一知识检索适配器**：一次打包 问题分析+答案+思路+风格+运行状态 | `MarkdownRetrievalService`（跨 Provider 用 `FakeRetrievalService`） |

## 二、接口定义（Python 参考，见 adapters/base.py）

```python
class MemoryProvider:      # get_user_style() / set_preference() / upsert_person() / upsert_event() / add_feedback() / add_candidate() / promote()
class KnowledgeProvider:   # list_cases() / get_case(id) / upsert_case() / list_strategies() / get_strategy(id) / upsert_strategy()
                           # / search_cases(text, top_k) / search_case_hits(text, top_k) / search_strategies(text, top_k)
class Retriever:           # retrieve_user(user_input) -> {style, people, events, feedback, cases, strategies}
class RetrievalService:    # available() / build_package(query) -> GenerationPackage
                           # / search(query) -> List[CaseHit] / get_patterns(query) -> List[Strategy]
class FeedbackProvider:    # record(user_input, ai_output, user_action)
class StyleProvider:       # current_style() -> dict(tone, length, strength, no_ai_tone)
class ContextProvider:     # recent_context()
```

`search_case_hits(text, top_k) -> List[CaseHit]` 是 v2 检索入口：先读 `knowledge/index/case_manifest.json`
在**索引层**打分召回 Top-K，再只加载命中的 case 文件；`CaseHit` 携带 `semantic / intent / scene_fit /
relation_fit / scope_penalty / total` 供上层做场景适配判定。替换为向量库/Embedding 时只需实现同一接口，
Core 不重写。

## 二·五、统一检索适配器 RetrievalService（v1.1.0 新增）

`build_package(query) -> GenerationPackage` 是 **Agent/核心唯一认识的检索入口**：
```
GenerationPackage = {
  analysis        # QueryAnalysis: relation / domains / is_accusation / wants_reply / strength
  style           # UserStyle
  people/events/feedback  # 记忆（按优先级）
  answer_candidates       # Answer Retrieval：现成答案候选（已打分）
  patterns                # Pattern Retrieval：回应思路（不接前提/反打回/装傻…）
  style_spec              # 全局风格规范（tone/length/forbid/sample），无匹配也用这个生成
  state           # RuntimeState: KB_AVAILABLE / KB_CALLED / KB_SEARCH_MS / CANDIDATES / ANSWER_MATCH / PATTERN_MATCH / ROUTE / TOTAL_MS
}
```
- **gatekeeper**：`KB_AVAILABLE=True` → `KB_CALLED=True` 强制检索；`False` → 明确 fallback，绝不"假装用了知识库"。
- **计时/状态仅进 `state`（调试层）**，绝不进用户回答；`scripts/retrieve.py --debug` 才展示。
- 换 SQLite/向量库/Mem0/外部语气 Skill：实现同一 `RetrievalService` 接口即可，Core 与 SKILL.md 不动。

## 三、核心生成只依赖接口，不依赖实现

嘴替核心 `scripts/mouthpiece.py` 只调用：
```
mode   = classify_mode(user_input)
pkg    = service.build_package(user_input, top_k)   # gatekeeper：KB 可用 → 强制检索（一次打包）
route  = 四级路由(answer_direct / answer_adapt / pattern_generate / style_generate)  # 见 references/knowledge.md
replies= generate(user_input, pkg, route)           # 只输出话术；内部字段留在 state/note（仅调试）
feedback_provider.record(...)                       # 可选：记录反馈
```

替换某 Provider（如换 Mem0 做记忆/检索）只需传入新实现（实现 `RetrievalService`），`mouthpiece.py` 与 SKILL.md 不动。

## 四、Host Adapter（跨宿主）
见 `references/hosting.md` 与 `adapters/host/`。Core 逻辑与宿主文件格式解耦：
- `SKILL.md` + `knowledge/` + `memory/` 是**可移植核心包**。
- 不同宿主（Claude Code/Trae/WorkBuddy）只提供一层薄 Adapter，不改 Core。
- 每个 host Adapter 都写明了**可执行的 KB 调用入口**（`scripts/retrieve.py "<问题>" --json`），
  避免"模型猜知识库可能在哪"或"装了 Skill 却从不调用知识库"。
