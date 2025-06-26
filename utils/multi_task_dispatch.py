from __future__ import annotations

import sys
import uuid
from collections import deque, defaultdict
from concurrent.futures import as_completed
from concurrent.futures.thread import ThreadPoolExecutor
from typing import List, TypeVar, Any

import networkx as nx
from loguru import logger
from typing_extensions import Callable, Optional

P = TypeVar("P")


class Task:
    def __init__(self, f: Callable[[P]], args: P, dependencies: Optional[List[Task]] = None):
        self.f = f
        self.dependencies = dependencies
        self.args = args
        self.id = uuid.uuid4().hex

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        if not isinstance(other, Task):
            return False
        return self.id == other.id

    def __repr__(self):
        return f'Task({self.f.__name__}, {self.args})'


# 任务调度器，将一组任务按照依赖关系分组，并行执行
class TaskDispatcher:
    # 需要输入一个线程池
    def __init__(self, pool: ThreadPoolExecutor):
        self._pool = pool
        self._tasks = nx.DiGraph()

    def adds(self, tasks: List[Task]):
        for task in tasks:
            self.add(task)
        return self

    def add(self, f: Task):
        self._tasks.add_node(f)
        if f.dependencies is None:
            return
        for dep in f.dependencies:
            self._tasks.add_edge(f, dep)
        return self

    def map(self, dg: nx.DiGraph, f: Callable):
        tasks = {}
        for node in dg.nodes:
            tasks[node] = Task(f=f, args=(node,))
        for node in dg.nodes:
            tasks[node].dependencies = []
            for dep in dg.successors(node):
                tasks[node].dependencies.append(tasks[dep])
            self.add(tasks[node])
        return self

    def run(self):
        # 检查是否有环
        if not nx.is_directed_acyclic_graph(self._tasks):
            raise ValueError("Graph is not acyclic")
        groups = reverse_topo(self._tasks)
        logger.debug(f'[TaskDispatcher] split {len(self._tasks)} tasks into {len(groups)} groups')
        for i, g in enumerate(groups):
            futures = {self._pool.submit(task.f, *task.args): task for task in g}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.exception(f"Task {futures[future]} failed with exception!")
                    raise e
            logger.debug(f'[TaskDispatcher] finished group {i + 1}, size: {len(g)}')


# 获取有向图的逆拓扑排序
def reverse_topo(G: nx.DiGraph) -> List[List[Any]]:
    # 初始化所有节点的出度为分数
    out_degree = {node: G.out_degree(node) for node in G.nodes()}
    scores = {node: -1 for node in G.nodes()}  # -1表示未评分

    # 将所有出度为0且尚未评分的节点加入队列
    queue = deque([node for node, degree in out_degree.items() if degree == 0])

    current_score = 0
    while queue:
        # 处理当前层的所有节点
        for _ in range(len(queue)):
            node = queue.popleft()
            if scores[node] == -1:  # 确保只评一次分
                scores[node] = current_score

                # 对于每个邻居，减少其入度；如果入度变为0，则加入队列
                for neighbor in G.predecessors(node):
                    out_degree[neighbor] -= 1
                    if out_degree[neighbor] == 0:
                        queue.append(neighbor)

        # 进入下一层
        current_score += 1

    # 根据得分对节点进行分组
    groups = defaultdict(list)
    for node, score in scores.items():
        groups[score].append(node)

    return list(map(lambda x: x[1], sorted(groups.items(), key=lambda x: x[0])))


if __name__ == '__main__':
    import time


    def task(n):
        print(f"Task {n} started")
        time.sleep(1)
        print(f"Task {n} finished")


    pool = ThreadPoolExecutor(max_workers=4)
    dispatcher = TaskDispatcher(pool)

    task1 = Task(f=task, args=1)
    task2 = Task(f=task, args=2, dependencies=[task1])
    task3 = Task(f=task, args=3, dependencies=[task1])
    task4 = Task(f=task, args=4, dependencies=[task2, task3])

    # 添加任务
    dispatcher.add(task1)
    dispatcher.add(task2)
    dispatcher.add(task3)
    dispatcher.add(task4)

    # 执行任务
    dispatcher.run()

    dispatcher2 = TaskDispatcher(pool)

    graph = nx.DiGraph()
    graph.add_edges_from([(1, 2), (1, 3), (2, 4), (3, 4)])

    dispatcher2.map(graph, task)
    dispatcher2.run()
