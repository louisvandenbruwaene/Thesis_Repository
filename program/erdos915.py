#<header>
"""Erdős Problem 915 in sixteen variants: measure connectivity, search for
dense feasible graphs, and write a certificate that can be checked by hand.

Run:  python erdos915.py     (prints one line of numbers per case)
"""
import itertools
import math
import random
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from multiprocessing import Pool

import networkx
#</header>


#<models>
@dataclass(frozen=True)
class Model:
    NAME: str
    IS_DIRECTED: bool
    ALLOWS_REPEATED_EDGES: bool
    EDGE_SIZE: int              # r: 2 for graphs, at least 3 otherwise


def ALL_MODELS(HYPEREDGE_SIZE=3):
    r = HYPEREDGE_SIZE
    return [
        Model("simple graph",             False, False, 2),
        Model("multigraph",               False, True,  2),
        Model("simple hypergraph",        False, False, r),
        Model("multihypergraph",          False, True,  r),
        Model("simple digraph",           True,  False, 2),
        Model("directed multigraph",      True,  True,  2),
        Model("directed hypergraph",      True,  False, r),
        Model("directed multihypergraph", True,  True,  r),
    ]


class Separation(Enum):
    EDGE_DISJOINT = "edge"
    VERTEX_DISJOINT = "vertex"


@dataclass(frozen=True)
class Problem:
    MODEL: Model
    SEPARATION: Separation
    VERTEX_COUNT: int           # n
    THRESHOLD_M: int            # feasible: fewer than m disjoint routes
#</models>


#<hyperedges>
@dataclass(frozen=True, order=True)
class Hyperedge:
    TAIL: int | None            # None for an undirected hyperedge
    MEMBERS: tuple              # all its vertices, sorted

    def entry_vertices(self):
        return self.MEMBERS if self.TAIL is None else (self.TAIL,)

    def exit_vertices(self):
        return tuple(v for v in self.MEMBERS if v != self.TAIL)


def possible_hyperedges(problem):
    model, n = problem.MODEL, problem.VERTEX_COUNT
    hyperedges = []
    for members in itertools.combinations(range(n), model.EDGE_SIZE):
        tails = members if model.IS_DIRECTED else (None,)
        hyperedges += [Hyperedge(tail, members) for tail in tails]
    return hyperedges


def largest_multiplicity(problem):
    if problem.MODEL.ALLOWS_REPEATED_EDGES:
        return problem.THRESHOLD_M - 1
    return 1
#</hyperedges>


#<pairs>
def vertex_pairs(problem):
    vertices = range(problem.VERTEX_COUNT)
    if problem.MODEL.IS_DIRECTED:
        return list(itertools.permutations(vertices, 2))
    return list(itertools.combinations(vertices, 2))


def edge_count(multiplicity):
    return sum(multiplicity.values())
#</pairs>


#<network>
def build_flow_network(problem, multiplicity):
    network = networkx.DiGraph()
    if problem.SEPARATION is Separation.VERTEX_DISJOINT:
        through_vertex = 1
    else:
        through_vertex = math.inf
    for v in range(problem.VERTEX_COUNT):
        network.add_edge(("in", v), ("out", v), capacity=through_vertex)
    for hyperedge, copies in multiplicity.items():
        if copies == 0:
            continue
        enter, leave = ("enter", hyperedge), ("exit", hyperedge)
        network.add_edge(enter, leave, capacity=copies)
        for v in hyperedge.entry_vertices():
            network.add_edge(("out", v), enter, capacity=math.inf)
        for v in hyperedge.exit_vertices():
            network.add_edge(leave, ("in", v), capacity=math.inf)
    return network
#</network>


#<measure>
def disjoint_routes(network, source, target, stop_at=None):
    return networkx.maximum_flow_value(
        network, ("out", source), ("in", target),
        flow_func=networkx.algorithms.flow.edmonds_karp, cutoff=stop_at)


def is_feasible(problem, multiplicity):
    m = problem.THRESHOLD_M
    network = build_flow_network(problem, multiplicity)
    return all(disjoint_routes(network, u, v, stop_at=m) < m
               for u, v in vertex_pairs(problem))
#</measure>


#<connectivities>
def all_connectivities(problem, multiplicity):
    network = build_flow_network(problem, multiplicity)
    return {(u, v): disjoint_routes(network, u, v)
            for u, v in vertex_pairs(problem)}


class OutOfTime(Exception):
    pass
#</connectivities>


#<exhaustive>
def exhaustive_search(problem, deadline):
    hyperedges = possible_hyperedges(problem)
    cap = largest_multiplicity(problem)
    best = {"size": -1, "graph": {}}
    multiplicity = {}

    def decide(index, size):
        if time.time() > deadline:
            raise OutOfTime
        undecided = len(hyperedges) - index
        if size + undecided * cap <= best["size"]:
            return                          # cannot beat the best
        if index == len(hyperedges):
            best["size"], best["graph"] = size, dict(multiplicity)
            return
        hyperedge = hyperedges[index]
        for copies in range(1, cap + 1):
            multiplicity[hyperedge] = copies
            if not is_feasible(problem, multiplicity):
                break                       # more copies stay infeasible
            decide(index + 1, size + copies)
        multiplicity.pop(hyperedge, None)
        decide(index + 1, size)

    try:
        decide(0, 0)
        return best["size"], best["graph"], True
    except OutOfTime:
        return best["size"], best["graph"], False
#</exhaustive>


#<nauty>
def best_digraphs_on_support(problem, support_edges, deadline):
    m = problem.THRESHOLD_M
    choices = [(forward, backward) for forward in range(m)
               for backward in range(m) if forward + backward > 0]
    best = {"size": -1, "graphs": []}
    multiplicity = {}

    def choose(index, size):
        if time.time() > deadline:
            raise OutOfTime
        undecided = len(support_edges) - index
        if size + undecided * 2 * (m - 1) < best["size"]:
            return
        if index == len(support_edges):
            if size > best["size"]:
                best["size"], best["graphs"] = size, []
            best["graphs"].append(dict(multiplicity))
            return
        a, b = support_edges[index]
        a_to_b, b_to_a = Hyperedge(a, (a, b)), Hyperedge(b, (a, b))
        for forward, backward in choices:
            multiplicity[a_to_b], multiplicity[b_to_a] = forward, backward
            if is_feasible(problem, multiplicity):
                choose(index + 1, size + forward + backward)
        del multiplicity[a_to_b], multiplicity[b_to_a]

    try:
        choose(0, 0)
        return best["size"], best["graphs"], True
    except OutOfTime:
        return best["size"], best["graphs"], False
#</nauty>


#<supports>
def undirected_supports(vertex_count):
    """Streams the output of geng, one graph at a time."""
    geng = subprocess.Popen(["geng", "-q", str(vertex_count)],
                            stdout=subprocess.PIPE)
    try:
        for line in geng.stdout:
            yield networkx.from_graph6_bytes(line.strip())
    finally:
        geng.kill()
        geng.wait()


def canonical_form(vertex_count, multiplicity):
    arcs = [(e.TAIL, e.exit_vertices()[0], copies)
            for e, copies in multiplicity.items() if copies]
    return min(tuple(sorted((order[t], order[h], c) for t, h, c in arcs))
               for order in itertools.permutations(range(vertex_count)))


def generating_search(problem, deadline):
    best_size, extremal, finished = 0, [{}], True
    for support in undirected_supports(problem.VERTEX_COUNT):
        if time.time() > deadline:
            finished = False
            break
        size, graphs, done = best_digraphs_on_support(
            problem, sorted(support.edges()), deadline)
        finished = finished and done
        if size > best_size:
            best_size, extremal = size, []
        if size == best_size:
            extremal += graphs
    classes = None
    if finished:
        classes = len({canonical_form(problem.VERTEX_COUNT, graph)
                       for graph in extremal})
    return best_size, extremal[0], classes, finished
#</supports>


#<energy>
PENALTY_PER_EXCESS_ROUTE = 10


def energy(size, highest_connectivity, threshold_m):
    excess = max(0, highest_connectivity - (threshold_m - 1))
    return -size + PENALTY_PER_EXCESS_ROUTE * excess
#</energy>


#<neighbours>
def moves(problem, multiplicity):
    cap = largest_multiplicity(problem)
    for hyperedge in possible_hyperedges(problem):
        copies = multiplicity.get(hyperedge, 0)
        if copies < cap:
            yield hyperedge, +1
        if copies > 0:
            yield hyperedge, -1


def highest_after(problem, multiplicity, move, connectivity):
    """Measures only near the cap: one copy more raises a value by at
    most one, so only pairs already at m-1 need a check, and one copy
    less never raises a value."""
    cap = problem.THRESHOLD_M - 1
    current_highest = max(connectivity.values(), default=0)
    _, change = move
    if change < 0 and current_highest <= cap:
        return current_highest
    if change > 0 and current_highest < cap:
        return current_highest + 1
    network = build_flow_network(problem, apply(multiplicity, move))
    at_cap = [pair for pair, value in connectivity.items()
              if value >= cap]
    return max((disjoint_routes(network, u, v) for u, v in at_cap),
               default=0)
#</neighbours>


#<apply>
def apply(multiplicity, move):
    hyperedge, change = move
    changed = dict(multiplicity)
    changed[hyperedge] = changed.get(hyperedge, 0) + change
    return changed
#</apply>


#<tabu>
TABU_TENURE = 7
STEPS_WITHOUT_PROGRESS_BEFORE_KICK = 30


def tabu_search(problem, deadline, seed):
    generator = random.Random(seed)
    m = problem.THRESHOLD_M
    multiplicity, tabu_until, step = {}, {}, 0
    connectivity = all_connectivities(problem, multiplicity)
    best_size, best_graph, last_progress = 0, {}, 0
    while time.time() < deadline:
        step += 1
        scored = []
        for move in moves(problem, multiplicity):
            highest = highest_after(problem, multiplicity, move,
                                    connectivity)
            size = edge_count(multiplicity) + move[1]
            new_best = highest <= m - 1 and size > best_size
            if tabu_until.get(move[0], 0) > step and not new_best:
                continue                    # tabu, unless a new best
            scored.append((energy(size, highest, m),
                           generator.random(), move))
        if not scored:
            break
        _, _, move = min(scored)
        multiplicity = apply(multiplicity, move)
        tabu_until[move[0]] = step + TABU_TENURE
        connectivity = all_connectivities(problem, multiplicity)
        size = edge_count(multiplicity)
        if max(connectivity.values(), default=0) <= m - 1 \
                and size > best_size:
            best_size, best_graph = size, multiplicity
            last_progress = step
        if step - last_progress > STEPS_WITHOUT_PROGRESS_BEFORE_KICK:
            kicked = generator.sample(list(multiplicity),
                                      min(3, len(multiplicity)))
            multiplicity = dict(multiplicity)
            for hyperedge in kicked:
                multiplicity[hyperedge] = 0
            connectivity = all_connectivities(problem, multiplicity)
            last_progress = step
    return best_size, best_graph
#</tabu>


#<annealing>
START_TEMPERATURE, COOLING_FACTOR = 2.0, 0.995


def simulated_annealing(problem, deadline, seed):
    generator = random.Random(seed)
    m = problem.THRESHOLD_M
    multiplicity, temperature = {}, START_TEMPERATURE
    connectivity = all_connectivities(problem, multiplicity)
    current = energy(0, 0, m)
    best_size, best_graph = 0, {}
    while time.time() < deadline:
        move = generator.choice(list(moves(problem, multiplicity)))
        highest = highest_after(problem, multiplicity, move,
                                connectivity)
        size = edge_count(multiplicity) + move[1]
        proposed = energy(size, highest, m)
        worse_by = proposed - current
        if worse_by <= 0 or \
                generator.random() < math.exp(-worse_by / temperature):
            multiplicity, current = apply(multiplicity, move), proposed
            connectivity = all_connectivities(problem, multiplicity)
            if highest <= m - 1 and size > best_size:
                best_size, best_graph = size, multiplicity
        temperature = max(temperature * COOLING_FACTOR, 0.01)
    return best_size, best_graph
#</annealing>


#<greedy>
def random_greedy_growth(problem, deadline, seed):
    generator = random.Random(seed)
    cap = largest_multiplicity(problem)
    best_size, best_graph = 0, {}
    while time.time() < deadline:
        copies = [hyperedge for hyperedge in possible_hyperedges(problem)
                  for _ in range(cap)]
        generator.shuffle(copies)
        multiplicity = {}
        for hyperedge in copies:
            if time.time() > deadline:
                break                       # the partial graph is feasible
            grown = apply(multiplicity, (hyperedge, +1))
            if is_feasible(problem, grown):
                multiplicity = grown
        if edge_count(multiplicity) > best_size:
            best_size, best_graph = edge_count(multiplicity), multiplicity
    return best_size, best_graph
#</greedy>


#<certificate>
def write_certificate(problem, multiplicity):
    network = build_flow_network(problem, multiplicity)
    certificate = {}
    for u, v in vertex_pairs(problem):
        _, (source_side, _) = networkx.minimum_cut(
            network, ("out", u), ("in", v))
        removed_vertices, removed_hyperedges = [], []
        for node in source_side:
            for neighbour in network.successors(node):
                if neighbour in source_side:
                    continue
                if node[0] == "in":
                    removed_vertices.append(node[1])
                else:
                    removed_hyperedges.append(node[1])
        certificate[(u, v)] = (removed_vertices, removed_hyperedges)
    return certificate
#</certificate>


#<checker>
def check_certificate(problem, multiplicity, certificate):
    """Independent of the code above: no flows, only following edges."""
    for u, v in vertex_pairs(problem):
        removed_vertices, removed_hyperedges = certificate[(u, v)]
        if problem.SEPARATION is Separation.EDGE_DISJOINT \
                and removed_vertices:
            return False
        if u in removed_vertices or v in removed_vertices:
            return False
        size = len(set(removed_vertices)) + sum(
            multiplicity[e] for e in set(removed_hyperedges))
        if size > problem.THRESHOLD_M - 1:
            return False
        reached, frontier = {u}, [u]
        while frontier:
            vertex = frontier.pop()
            for hyperedge, copies in multiplicity.items():
                if copies == 0 or hyperedge in removed_hyperedges:
                    continue
                if vertex not in hyperedge.entry_vertices():
                    continue
                for nxt in hyperedge.exit_vertices():
                    if nxt not in reached and nxt not in removed_vertices:
                        reached.add(nxt)
                        frontier.append(nxt)
        if v in reached:
            return False
    return True
#</checker>


#<driver>
SECONDS_PER_CASE = 60


def solve(problem):
    model = problem.MODEL
    deadline = time.time() + SECONDS_PER_CASE / 2
    if model.IS_DIRECTED and model.ALLOWS_REPEATED_EDGES \
            and model.EDGE_SIZE == 2:
        size, graph, _, exact = generating_search(problem, deadline)
    else:
        size, graph, exact = exhaustive_search(problem, deadline)
    if not exact:
        deadline = time.time() + SECONDS_PER_CASE / 2
        if model.EDGE_SIZE == 2:
            search = tabu_search
        else:
            search = random_greedy_growth
        found_size, found_graph = search(problem, deadline, seed=1)
        if found_size > size:
            size, graph = found_size, found_graph
    certificate = write_certificate(problem, graph)
    return size, exact, check_certificate(problem, graph, certificate)


#</driver>


#<main>
def print_table(model, separation, solved, largest_m):
    print(f"\n{model.NAME}, {separation.value} separation")
    print("n \\ m" + "".join(f"{m:>6}" for m in range(2, largest_m + 1)))
    rows = {}
    for problem, (size, exact, checked) in solved:
        mark = ("*" if exact else " ") if checked else "!"
        rows.setdefault(problem.VERTEX_COUNT, []).append(f"{size:>5}{mark}")
    for n, cells in rows.items():
        print(f"{n:>5}" + "".join(cells), flush=True)


def main(largest_n=10, largest_m=10):
    problems = [Problem(model, separation, n, m)
                for model in ALL_MODELS() for separation in Separation
                for n in range(model.EDGE_SIZE, largest_n + 1)
                for m in range(2, largest_m + 1)]
    print("Edges of the densest feasible graph found, every one certified.")
    print("* the search finished, so the value is exact;",
          "! the certificate failed")
    with Pool() as pool:
        solved = zip(problems, pool.imap(solve, problems))
        for (model, separation), table in itertools.groupby(
                solved, key=lambda pair: (pair[0].MODEL, pair[0].SEPARATION)):
            print_table(model, separation, table, largest_m)


if __name__ == "__main__":
    main()
#</main>
