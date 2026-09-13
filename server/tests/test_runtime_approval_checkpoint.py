import asyncio

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from server.domain.run_approval import RunApprovalRequest
from server.infrastructure.deepagent_runtime import DeepAgentRuntime


@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_stream_approval_survives_checkpoint_reopen(tmp_path, nested, decision, parallel):
    calls = []

    async def review(state):
        result = interrupt({
            'action_requests': [{'name': 'write_file', 'args': {'content': 'original'}, 'description': 'write'}],
            'review_configs': [{'action_name': 'write_file', 'allowed_decisions': ['approve', 'reject']}],
        })
        calls.append(result['decisions'][0]['type'])
        return {'messages': [('assistant', 'finished')]}

    def graph(checkpointer):
        child = StateGraph(MessagesState)
        child.add_node('review', review)
        child.add_edge(START, 'review')
        child.add_edge('review', END)
        if not nested:
            if parallel:
                child.add_node('other_review', review)
                child.add_edge(START, 'other_review')
                child.add_edge('other_review', END)
            return child.compile(checkpointer=checkpointer)
        parent = StateGraph(MessagesState)
        parent.add_node('child', child.compile())
        parent.add_edge(START, 'child')
        parent.add_edge('child', END)
        if parallel:
            parent.add_node('other_child', child.compile())
            parent.add_edge(START, 'other_child')
            parent.add_edge('other_child', END)
        return parent.compile(checkpointer=checkpointer)

    async def scenario():
        runtime = object.__new__(DeepAgentRuntime)
        config = {'configurable': {'thread_id': 'test'}}
        path = str(tmp_path / 'checkpoint.sqlite')
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            approval = await runtime._run_agent(graph(saver), {'messages': [('user', 'write')]}, config, stream_callback=lambda e: None)
        assert isinstance(approval, RunApprovalRequest)
        assert len(approval.interrupts) == (2 if parallel else 1)
        assert calls == []
        async with AsyncSqliteSaver.from_conn_string(path) as saver:
            agent = graph(saver)
            state = await agent.aget_state(config)
            assert state.next
            result = await runtime._run_agent(agent, Command(resume={
                item.interrupt_id: {'decisions': [{'type': decision}]} for item in approval.interrupts
            }), config, stream_callback=lambda e: None)
            assert not isinstance(result, RunApprovalRequest)
            assert not (await agent.aget_state(config)).next
        assert calls == [decision] * (2 if parallel else 1)

    asyncio.run(scenario())
