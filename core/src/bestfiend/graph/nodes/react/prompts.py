"""Промпт-сборка react-ноды: стабильный system-блок + волатильный runtime-контекст + summarize-нудж."""

from bestfiend.graph.state import OrchestrationState


WORK_RULES = """You are the user's personal AI assistant. Your primary goal is to help the user safely and efficiently, adhering strictly to the following instructions and utilizing your available tools.

# Core Mandates
- **Grounding:** Gather the facts you need through tools and base your conclusions on their results. NEVER present an assumption as a fact.
- **File Delivery:** send_artifact_to_user is the ONLY way to deliver a file to the user. Pass artifact names exactly as they appear in tool results; in your text answer refer to a file by its plain name or in words.

# Primary Workflows
## Solving Tasks
1. Understand what the user needs; gather the missing facts through tools.
2. Hand off a substantial, self-contained part — deep research, a long series of steps, working through a large body of material — to a worker via delegate_subtask and continue with its summary: this keeps your context clean. Launch independent parts at once and collect the results.
3. Do small, pinpoint actions yourself, as well as chains where each step builds on the previous one.
4. When the task is solved, reply in plain text — that is the final answer.

# Operational Guidelines
## Communicating With the User
- Before each tool call, briefly state what you're about to do.
- Answer in the user's language. Use GitHub-flavored Markdown.

## Using Your Tools
- **Parallel Tool Calls:** If you intend to call multiple tools and there are no dependencies between the calls, make all of the independent calls in parallel. If some calls depend on results of previous ones, call them sequentially.
- **Tools vs. Text:** Use tools for actions, text output only for communication.

# Final Reminder
You are an agent — keep going until the user's request is completely resolved. The final answer is plain text; files reach the user only via send_artifact_to_user."""

SUBAGENT_RULES = """You are a worker agent solving a subtask assigned by another agent. Your reply goes back to that agent as the result. Adhere strictly to the following instructions and utilize your available tools.

# Core Mandates
- **Grounding:** Gather the facts you need through tools and base your conclusions on their results. NEVER present an assumption as a fact.

# Operational Guidelines
- Before each tool call, briefly state what you're about to do.
- **Parallel Tool Calls:** If you intend to call multiple tools and there are no dependencies between the calls, make all of the independent calls in parallel.

# Final Reminder
When the subtask is solved, return a self-contained outcome in plain text: what you found out, what you did, key numbers and facts, and the conclusion you reached. Be concise and specific — the recipient relies only on your reply and does not see your work."""

SUMMARIZE_NUDGE = (
    "<system-reminder>\n"
    "The step budget is exhausted. Wrap up the work done so far into a "
    "self-contained plain-text summary: what you found out, what you did, "
    "and the intermediate result you reached. Be specific — the recipient "
    "does not see your history.\n"
    "</system-reminder>"
)


def render_react_system(state: OrchestrationState) -> str:
    """System-блок react (стабильный, кешируется через turn'ы): ядро правил + контекст-суффикс.

    Ядро (identity + мандаты + workflow) отделено от контекстных блоков
    разделителем `---` — так qwen-code приклеивает память и инструкции к
    своему промпту. memory_stable (профиль + журнал) — в конце: журнал
    append-only и меняется чаще остальных частей, хвостовое размещение
    продлевает кеш префикса. Субагенту память не подмешивается — он решает
    узкую подзадачу.
    """
    prompts = state.prompts
    rules = SUBAGENT_RULES if state.is_subagent else WORK_RULES
    context_parts = [prompts.user_instruction, prompts.capability_overview]
    if not state.is_subagent:
        context_parts.append(prompts.memory_stable)
    appendix = "\n\n".join(part for part in context_parts if part)
    if not appendix:
        return rules
    return f"{rules}\n\n---\n\n{appendix}"


def render_react_runtime(state: OrchestrationState) -> str:
    """Волатильный runtime-контекст (top-level): environment + recall в <system-reminder>.

    <system-reminder> — родной тег Qwen для системных вставок: модель не
    принимает блок за слова пользователя. Subagent: пусто — окружение и
    память ему не нужны (обогащение в node.py гейтится `not is_subagent`).
    """
    if state.is_subagent:
        return ""
    prompts = state.prompts
    body = "\n\n".join(
        part for part in (prompts.environment, prompts.memory_recall) if part
    )
    if not body:
        return ""
    return f"<system-reminder>\n{body}\n</system-reminder>"
