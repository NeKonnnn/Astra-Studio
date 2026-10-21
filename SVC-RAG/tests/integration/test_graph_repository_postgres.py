"""GraphRepository на реальном PostgreSQL: узлы, рёбра, обход соседей."""

import asyncio

import pytest

from app.database.graph_repository import GraphRepository

pytestmark = pytest.mark.integration


@pytest.fixture
async def graph(pg_connection):
    repo = GraphRepository(pg_connection)
    await repo.create_tables()
    return repo


async def _count(pg_connection, table, where="TRUE", *args):
    async with await pg_connection.acquire() as conn:
        return await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE {where}", *args)


UNRELATED = [(0, "alpha beta gamma"), (1, "delta epsilon zeta"), (2, "eta theta iota")]


async def test_rebuild_creates_nodes_and_adjacent_edges_both_ways(graph, pg_connection):
    await graph.rebuild_document_graph("kb", 1, UNRELATED)
    assert await _count(pg_connection, "rag_graph_nodes") == 3
    # цепочка 0-1-2 в обе стороны: 4 направленных ребра, семантических нет
    assert await _count(pg_connection, "rag_graph_edges", "edge_type = 'adjacent'") == 4
    assert await _count(pg_connection, "rag_graph_edges", "edge_type = 'semantic'") == 0


async def test_semantic_edge_only_above_jaccard_threshold(graph, pg_connection):
    await graph.rebuild_document_graph(
        "kb",
        1,
        [
            (0, "rag graph search context"),
            (1, "что-то совсем другое"),
            (2, "rag graph search retrieval"),  # jaccard с 0 = 3/5 = 0.6
        ],
    )
    async with await pg_connection.acquire() as conn:
        rows = await conn.fetch(
            "SELECT n1.chunk_index AS a, n2.chunk_index AS b, e.weight "
            "FROM rag_graph_edges e "
            "JOIN rag_graph_nodes n1 ON n1.id = e.from_node_id "
            "JOIN rag_graph_nodes n2 ON n2.id = e.to_node_id "
            "WHERE e.edge_type = 'semantic' ORDER BY a"
        )
    assert [(r["a"], r["b"]) for r in rows] == [(0, 2), (2, 0)]
    # вес = jaccard / (1 + ln(1 + расстояние)) = 0.6 / (1 + ln 3)
    assert rows[0]["weight"] == pytest.approx(0.6 / (1 + 1.0986122886681098))


async def test_rebuild_replaces_previous_graph(graph, pg_connection):
    await graph.rebuild_document_graph("kb", 1, UNRELATED)
    await graph.rebuild_document_graph("kb", 1, [(0, "one"), (1, "two")])
    assert await _count(pg_connection, "rag_graph_nodes") == 2
    assert await _count(pg_connection, "rag_graph_edges") == 2


async def test_rebuild_with_no_chunks_just_clears(graph, pg_connection):
    await graph.rebuild_document_graph("kb", 1, UNRELATED)
    await graph.rebuild_document_graph("kb", 1, [])
    assert await _count(pg_connection, "rag_graph_nodes") == 0


async def test_single_chunk_has_node_and_no_edges(graph, pg_connection):
    await graph.rebuild_document_graph("kb", 1, [(0, "alone")])
    assert await _count(pg_connection, "rag_graph_nodes") == 1
    assert await _count(pg_connection, "rag_graph_edges") == 0


async def test_store_types_and_documents_are_isolated(graph, pg_connection):
    await graph.rebuild_document_graph("kb", 1, UNRELATED)
    await graph.rebuild_document_graph("project", 1, UNRELATED)
    await graph.rebuild_document_graph("kb", 2, UNRELATED)
    await graph.delete_document_graph("kb", 1)
    assert await _count(pg_connection, "rag_graph_nodes", "store_type = 'kb' AND document_id = 1") == 0
    assert await _count(pg_connection, "rag_graph_nodes", "store_type = 'project'") == 3
    assert await _count(pg_connection, "rag_graph_nodes", "document_id = 2") == 3
    # рёбра удалённых узлов ушли каскадом
    assert await _count(pg_connection, "rag_graph_edges") == 8


async def test_null_bytes_stripped_from_node_content(graph, pg_connection):
    await graph.rebuild_document_graph("kb", 1, [(0, "a\x00b")])
    async with await pg_connection.acquire() as conn:
        assert await conn.fetchval("SELECT content FROM rag_graph_nodes") == "ab"


async def test_expand_neighbors_decays_along_chain(graph):
    await graph.rebuild_document_graph(
        "kb", 1, [(i, f"unique{i} token{i}") for i in range(5)]
    )
    got = await graph.expand_neighbors("kb", 1, [0], max_hops=2)
    assert got[(1, 0)] == 1.0
    assert got[(1, 1)] == pytest.approx(0.82)
    assert got[(1, 2)] == pytest.approx(0.82 * 0.82**2)
    assert (1, 3) not in got  # дальше max_hops


async def test_expand_neighbors_respects_hops_and_max_nodes(graph):
    await graph.rebuild_document_graph(
        "kb", 1, [(i, f"unique{i} token{i}") for i in range(10)]
    )
    one_hop = await graph.expand_neighbors("kb", 1, [5], max_hops=1)
    assert set(one_hop) == {(1, 4), (1, 5), (1, 6)}
    assert await graph.expand_neighbors("kb", 1, [5], max_hops=0) == {(1, 5): 1.0}
    capped = await graph.expand_neighbors("kb", 1, [5], max_hops=5, max_nodes=2)
    assert len(capped) <= 3


async def test_expand_neighbors_empty_and_unknown_seeds(graph):
    await graph.rebuild_document_graph("kb", 1, UNRELATED)
    assert await graph.expand_neighbors("kb", 1, []) == {}
    assert await graph.expand_neighbors("kb", 1, [99]) == {}
    assert await graph.expand_neighbors("kb", 999, [0]) == {}
    assert await graph.expand_neighbors("project", 1, [0]) == {}


async def test_expand_neighbors_exact_pairs_avoid_cross_document_collisions(graph):
    await graph.rebuild_document_graph("kb", 1, [(i, f"a{i} x{i}") for i in range(3)])
    await graph.rebuild_document_graph("kb", 2, [(i, f"b{i} y{i}") for i in range(3)])
    got = await graph.expand_neighbors(
        "kb", None, [0], max_hops=1, seed_doc_chunk_pairs=[(2, 0)]
    )
    assert set(got) == {(2, 0), (2, 1)}


async def test_expand_neighbors_legacy_fallback_mixes_documents_known_issue(graph):
    """Известная проблема: без ``document_id`` и пар seed ищутся узлы с любым
    документом, у которых совпал только ``chunk_index`` — выдача смешивает документы."""
    await graph.rebuild_document_graph("kb", 1, [(i, f"a{i} x{i}") for i in range(3)])
    await graph.rebuild_document_graph("kb", 2, [(i, f"b{i} y{i}") for i in range(3)])
    got = await graph.expand_neighbors("kb", None, [0], max_hops=0)
    assert set(got) == {(1, 0), (2, 0)}


async def test_semantic_edges_do_not_cross_documents(graph):
    same = [(0, "rag graph search"), (1, "rag graph search")]
    await graph.rebuild_document_graph("kb", 1, same)
    await graph.rebuild_document_graph("kb", 2, same)
    got = await graph.expand_neighbors("kb", 1, [0], max_hops=3)
    assert {doc for doc, _ in got} == {1}


async def test_duplicate_chunk_index_fails_and_leaves_document_without_graph_known_issue(
    graph, pg_connection
):
    """Известная проблема: пересборка не транзакционна. Старый граф удаляется
    отдельным запросом, а затем вставка с повторным ``chunk_index`` падает на
    UNIQUE — документ остаётся вообще без графа (старый уже стёрт)."""
    await graph.rebuild_document_graph("kb", 1, UNRELATED)
    with pytest.raises(Exception):
        await graph.rebuild_document_graph("kb", 1, [(0, "a"), (0, "b")])
    assert await _count(pg_connection, "rag_graph_edges") == 0
    # вставленный до сбоя узел остался: частичный граф
    assert await _count(pg_connection, "rag_graph_nodes") == 1


async def test_concurrent_rebuilds_of_different_documents(graph, pg_connection):
    await asyncio.gather(
        *(
            graph.rebuild_document_graph("kb", d, [(i, f"d{d} w{i}") for i in range(4)])
            for d in range(1, 6)
        )
    )
    assert await _count(pg_connection, "rag_graph_nodes") == 20
    assert await _count(pg_connection, "rag_graph_edges", "edge_type = 'adjacent'") == 30


async def test_long_document_semantic_window_is_bounded(graph, pg_connection):
    """Семантические рёбра ищутся лишь в окне из 30 соседей."""
    chunks = [(i, "common words here") for i in range(40)]
    await graph.rebuild_document_graph("kb", 1, chunks)
    async with await pg_connection.acquire() as conn:
        far = await conn.fetchval(
            "SELECT COUNT(*) FROM rag_graph_edges e "
            "JOIN rag_graph_nodes a ON a.id = e.from_node_id "
            "JOIN rag_graph_nodes b ON b.id = e.to_node_id "
            "WHERE e.edge_type = 'semantic' AND ABS(a.chunk_index - b.chunk_index) > 30"
        )
    assert far == 0
    assert await _count(pg_connection, "rag_graph_edges", "edge_type = 'semantic'") > 0
