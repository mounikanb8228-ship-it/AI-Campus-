"""Shortest walking route between campus buildings (Dijkstra's algorithm)."""
import heapq
import math


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_graph(buildings, paths):
    """buildings: rows with id, name, lat, lng. paths: rows with a_id, b_id."""
    by_id = {b["id"]: b for b in buildings}
    graph = {b["id"]: [] for b in buildings}
    for p in paths:
        a, b = by_id.get(p["a_id"]), by_id.get(p["b_id"])
        if not a or not b:
            continue
        d = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
        graph[a["id"]].append((b["id"], d))
        graph[b["id"]].append((a["id"], d))
    return graph


def shortest_path(graph, start, goal):
    """Return (list of building ids, total metres) or (None, None) when there is no route."""
    if start not in graph or goal not in graph:
        return None, None
    dist = {start: 0.0}
    prev = {}
    heap = [(0.0, start)]
    done = set()
    while heap:
        d, node = heapq.heappop(heap)
        if node in done:
            continue
        done.add(node)
        if node == goal:
            break
        for nxt, w in graph[node]:
            nd = d + w
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                prev[nxt] = node
                heapq.heappush(heap, (nd, nxt))
    if goal not in dist:
        return None, None
    path, node = [goal], goal
    while node != start:
        node = prev[node]
        path.append(node)
    path.reverse()
    return path, dist[goal]
