"""Resume one human decision at a time inside the draft's database transaction."""
from dataclasses import asdict
from itertools import groupby
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from matching import resolve_kit


class ReviewState(TypedDict):
    document: dict


class ReviewConflict(ValueError):
    pass


def human_review(state):
    document = state["document"].copy()
    decision = interrupt({"order_id": document["order_id"], "status": document["status"]})
    if decision["action"] == "clarify":
        document.setdefault("original_evidence", {
            "serial": document["serial"], "revision": document["revision"]})
        document.update(decision["updates"])
    match = resolve_kit(document["model"], document["serial"], document["revision"])
    if decision["action"] == "approve":
        if not match.part_code or asdict(match) != document["match"]:
            raise ReviewConflict("A current, supported match must be reviewed before approval.")
        document["status"] = "approved"
    else:
        document["match"] = asdict(match)
        document["status"] = "awaiting_review" if match.part_code else "needs_clarification"
    document["version"] += 1
    return {"document": document}


def next_step(state):
    return END if state["document"]["status"] == "approved" else "human_review"


builder = StateGraph(ReviewState)
builder.add_node("human_review", human_review)
builder.add_edge(START, "human_review")
builder.add_conditional_edges("human_review", next_step)


def advance(db, document, decision=None):
    # Keep just the latest paused checkpoint. Draft, history and checkpoint all
    # commit together. An interrupted HTTP request can retry the same version.
    order_id = document["order_id"]
    config = {"configurable": {"thread_id": order_id, "checkpoint_ns": ""}}
    saver = InMemorySaver()
    graph = builder.compile(checkpointer=saver)
    row = db.execute("SELECT encoding, payload FROM review_checkpoints WHERE order_id=?",
                     (order_id,)).fetchone()
    if row:
        checkpoint, metadata, writes = saver.serde.loads_typed(tuple(row))
        restored = saver.put(config, checkpoint, metadata, checkpoint["channel_versions"])
        for task, items in groupby(writes, key=lambda item: item[0]):
            saver.put_writes(restored, [(channel, value) for _, channel, value in items], task)
    else:
        # Includes orders that were waiting before graph checkpoints existed.
        graph.invoke({"document": document}, config)
    if decision:
        graph.invoke(Command(resume=decision), config)
    saved = saver.get_tuple(config)
    result = saved.checkpoint["channel_values"]["document"]
    if result["status"] == "approved":
        db.execute("DELETE FROM review_checkpoints WHERE order_id=?", (order_id,))
    else:
        encoding, payload = saver.serde.dumps_typed(
            [saved.checkpoint, saved.metadata, saved.pending_writes or []])
        db.execute("""INSERT INTO review_checkpoints VALUES (?, ?, ?)
                      ON CONFLICT (order_id) DO UPDATE SET encoding=excluded.encoding,
                      payload=excluded.payload""",
                   (order_id, encoding, payload))
    return result
