
"""Последовательная цепочка агентов (LibreChat Mixture-of-Agents).

Текущий агент — первый узел. `config.agent_ids` — упорядоченный список
следующих агентов (без текущего). На запуске каждый следующий получает
выводы предыдущих как auxiliary-контекст, LLM следующего не выбирает.

  settings.py  - лимиты (AGENT_CHAIN_MAX_AGENTS, AGENT_GRAPH_STEPS), agent_ids
  prompts.py   - сообщение следующему агенту, видимый пользователю текст
  profiles.py  - профили агентов цепочки, payload под шаг
  runner.py    - запуск цепочки в чате (был в realtime/handlers.py)
  step_search.py - свой поисковый запрос шага (AGENT_CHAIN_STEP_QUERY)
  answers.py   - метки [[ОТВЕТ N]] последнего шага -> ответы шагов как есть
  runner.py    - запуск цепочки в чате (был в realtime/handlers.py)

Общий RAG цепочки - backend/agents/shared_rag.py (он же у субагентов).
"""

from backend.agents.chain.profiles import prepare_step_socket_data, resolve_agent_chain
from backend.agents.chain.prompts import (
    DEFAULT_CHAIN_PROMPT_TEMPLATE,
    build_chain_user_message,
    chain_step_header,
    format_run_buffer,
    format_visible_chain_content,
    iter_chain_stream_prefixes,
)
from backend.agents.chain.settings import (
    DEFAULT_GRAPH_STEPS,
    GRAPH_STEPS_CAP,
    MAX_CHAIN_AGENTS,
    MAX_CHAIN_AGENTS_CAP,
    get_agent_graph_steps,
    get_max_chain_agents,
    parse_agent_ids,
    resolve_max_chain_agents,
)

__all__ = [
    "DEFAULT_CHAIN_PROMPT_TEMPLATE",
    "DEFAULT_GRAPH_STEPS",
    "GRAPH_STEPS_CAP",
    "MAX_CHAIN_AGENTS",
    "MAX_CHAIN_AGENTS_CAP",
    "build_chain_user_message",
    "chain_step_header",
    "format_run_buffer",
    "format_visible_chain_content",
    "get_agent_graph_steps",
    "get_max_chain_agents",
    "iter_chain_stream_prefixes",
    "parse_agent_ids",
    "prepare_step_socket_data",
    "resolve_agent_chain",
    "resolve_max_chain_agents",
]
