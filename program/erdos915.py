#<header>
# Erdős Problem 915 in sixteen variants: measure connectivity, search
# for dense feasible graphs, and write a certificate that can be checked
# by hand.
#
# Run:  python erdos915.py > output_tabu.txt
#       python erdos915.py greedy > output_greedy.txt
# It prints one table per problem and writes every graph found, with its
# certificate, to witnesses_tabu.json or witnesses_greedy.json.
import itertools
import json
import math
import os
import platform
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from functools import partial
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
    TAIL_COUNTS: tuple = (1,)   # directed: the allowed numbers of tails


def ALL_MODELS(HYPEREDGE_SIZE=3):
    r = HYPEREDGE_SIZE
    splits = [("forward", (1,)), ("backward", (r - 1,)),
              ("general", tuple(range(1, r)))]
    return [
        Model("simple graph",             False, False, 2),
        Model("multigraph",               False, True,  2),
        Model("simple hypergraph",        False, False, r),
        Model("multihypergraph",          False, True,  r),
        Model("simple directed graph",    True,  False, 2),
        Model("directed multigraph",      True,  True,  2),
    ] + [
        Model(f"{name}, {split}", True, repeated, r, tail_counts)
        for name, repeated in [("directed hypergraph", False),
                               ("directed multihypergraph", True)]
        for split, tail_counts in splits
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
    TAILS: tuple                # empty for an undirected hyperedge
    MEMBERS: tuple              # all its vertices, sorted

    def entry_vertices(self):
        return self.TAILS or self.MEMBERS

    def exit_vertices(self):
        return tuple(v for v in self.MEMBERS if v not in self.TAILS)


def possible_hyperedges(problem):
    model, n = problem.MODEL, problem.VERTEX_COUNT
    hyperedges = []
    for members in itertools.combinations(range(n), model.EDGE_SIZE):
        if not model.IS_DIRECTED:
            hyperedges.append(Hyperedge((), members))
            continue
        for tail_count in model.TAIL_COUNTS:
            hyperedges += [Hyperedge(tails, members) for tails
                           in itertools.combinations(members, tail_count)]
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
    best = {"size": 0, "graph": {}}        # the empty graph
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
def nauty_commands(problem, size):
    """The nauty programs that list every support that can carry size
    edges, each once up to renaming the vertices. Each entry is one
    pipeline: a list of commands, the output of one feeding the next.
    None for directed hypergraphs, for which nauty has no program."""
    model, n = problem.MODEL, problem.VERTEX_COUNT
    r = model.EDGE_SIZE
    fewest_edges = math.ceil(size / largest_multiplicity(problem))
    most_edges = min(size, len(possible_hyperedges(problem)))
    if model.IS_DIRECTED and r > 2:
        return None
    if fewest_edges > most_edges:
        return []                   # no graph has this many edges
    edges = f"{fewest_edges}:{most_edges}"
    if not model.IS_DIRECTED and r == 2:
        return [[["geng", "-q", str(n), edges]]]
    if model.IS_DIRECTED:                   # one or two arcs per pair
        pairs = f"{math.ceil(fewest_edges / 2)}:{most_edges}"
        return [[["geng", "-q", str(n), pairs],
                 ["directg", "-q", "-T", f"-e{edges}"]]]
    return [[["genbg", "-q", "-z", f"-d0:{r}", f"-D{k}:{r}",
              str(n), str(k)]]
            for k in range(fewest_edges, most_edges + 1)]


def read_support(problem, line):
    """One line of nauty output as a list of hyperedges."""
    n = problem.VERTEX_COUNT
    if problem.MODEL.IS_DIRECTED:   # directg: n, arc count, arcs
        numbers = [int(word) for word in line.split()[2:]]
        return [Hyperedge((tail,), tuple(sorted((tail, head))))
                for tail, head in zip(numbers[::2], numbers[1::2])]
    graph = networkx.from_graph6_bytes(line.strip())
    if problem.MODEL.EDGE_SIZE == 2:        # geng: the graph itself
        return [Hyperedge((), tuple(sorted(edge)))
                for edge in graph.edges()]
    # genbg: first the n vertices, then one node per hyperedge
    return [Hyperedge((), tuple(sorted(graph[x])))
            for x in range(n, graph.number_of_nodes())]


def start_pipeline(pipeline):
    """Starts the programs of a pipeline. If one cannot start, those
    already started are stopped before the error is passed on."""
    processes = []
    try:
        for command in pipeline:
            previous = processes[-1].stdout if processes else None
            processes.append(subprocess.Popen(command, stdin=previous,
                                              stdout=subprocess.PIPE))
            if previous:
                previous.close()    # only the next program reads it
    except BaseException:
        stop_pipeline(processes)
        for process in processes:
            process.stdout.close()
            process.wait()
        raise
    return processes


def stop_pipeline(processes):
    for process in processes:
        if process.poll() is None:
            process.kill()


def supports(problem, size, deadline):
    """Streams the supports from nauty, one at a time. The list counts
    as complete only if every program of every pipeline exits normally,
    so a failed geng is never mistaken for an empty list. nauty can work a
    long time between two lines, so a timer stops it at the deadline."""
    for pipeline in nauty_commands(problem, size):
        processes = start_pipeline(pipeline)
        timer = threading.Timer(max(0, deadline - time.time()),
                                stop_pipeline, [processes])
        timer.start()
        try:
            for line in processes[-1].stdout:
                if time.time() > deadline:
                    raise OutOfTime
                yield read_support(problem, line)
            statuses = [process.wait() for process in processes]
            if any(statuses):
                if time.time() > deadline:
                    raise OutOfTime         # the timer stopped it
                raise RuntimeError(f"{pipeline} exited with {statuses}")
        finally:
            timer.cancel()
            stop_pipeline(processes)        # the search stopped early
            for process in processes:
                process.wait()
#</nauty>


#<larger>
def add_copies(problem, support, size, deadline):
    """Gives every edge of the support between 1 and the largest
    multiplicity copies, size in total. Returns a feasible choice, or
    None if there is none."""
    cap = largest_multiplicity(problem)
    multiplicity = dict.fromkeys(support, 1)

    def choose(index, missing):
        if time.time() > deadline:
            raise OutOfTime
        if missing == 0:
            return dict(multiplicity)
        if missing > (len(support) - index) * (cap - 1):
            return None                     # too few edges left
        edge = support[index]
        for copies in range(1, min(cap, missing + 1) + 1):
            multiplicity[edge] = copies
            if copies > 1 and not is_feasible(problem, multiplicity):
                break               # more copies stay infeasible
            found = choose(index + 1, missing - (copies - 1))
            if found:
                return found
        multiplicity[edge] = 1
        return None

    if not is_feasible(problem, multiplicity):
        return None
    return choose(0, size - len(support))


def feasible_graph_with(problem, size, deadline):
    listed = supports(problem, size, deadline)
    try:
        for support in listed:
            found = add_copies(problem, support, size, deadline)
            if found:
                return found
        return None
    finally:
        listed.close()                      # stops nauty at once


def prove_largest(problem, size, graph, deadline):
    """Raises size while a feasible graph with one edge more exists.
    When none exists, size is the maximum."""
    if nauty_commands(problem, size + 1) is None:
        return size, graph, False
    try:
        while larger := feasible_graph_with(problem, size + 1,
                                            deadline):
            size, graph = size + 1, larger
        return size, graph, True
    except OutOfTime:
        return size, graph, False
#</larger>


#<neighbours>
def stays_feasible(problem, multiplicity, hyperedge, connectivity):
    """One copy more raises a value by at most one, so only pairs
    already at m-1 need a check."""
    cap = problem.THRESHOLD_M - 1
    if max(connectivity.values(), default=0) < cap:
        return True
    network = build_flow_network(problem,
                                 apply(multiplicity, (hyperedge, +1)))
    return all(disjoint_routes(network, u, v, stop_at=cap + 1) <= cap
               for (u, v), value in connectivity.items() if value >= cap)


def next_addition(problem, multiplicity, connectivity, blocked, tabu,
                  best_size, generator):
    """A random hyperedge that can get one copy more, or None."""
    cap = largest_multiplicity(problem)
    candidates = [hyperedge for hyperedge in possible_hyperedges(problem)
                  if multiplicity.get(hyperedge, 0) < cap
                  and hyperedge not in blocked]
    generator.shuffle(candidates)
    for hyperedge in candidates:
        if hyperedge in tabu and edge_count(multiplicity) + 1 <= best_size:
            continue                        # tabu, and not a record
        if stays_feasible(problem, multiplicity, hyperedge, connectivity):
            return hyperedge
        blocked.add(hyperedge)              # infeasible until a removal
    return None
#</neighbours>


#<apply>
def apply(multiplicity, move):
    hyperedge, change = move
    changed = dict(multiplicity)
    changed[hyperedge] = changed.get(hyperedge, 0) + change
    return changed
#</apply>


#<tabu>
TABU_LIST_LENGTH = 10


def tabu_search(problem, deadline, seed):
    generator = random.Random(seed)
    multiplicity, blocked, tabu = {}, set(), []
    connectivity = all_connectivities(problem, multiplicity)
    best_size, best_graph = 0, {}
    while time.time() < deadline:
        hyperedge = next_addition(problem, multiplicity, connectivity,
                                  blocked, tabu, best_size, generator)
        change = +1
        if hyperedge is None:               # a local optimum
            present = [e for e, copies in multiplicity.items()
                       if copies and e not in tabu]
            if not present:
                break                       # no legal move left
            hyperedge, change = generator.choice(present), -1
            blocked = set()                 # a removal can unblock additions
        multiplicity = apply(multiplicity, (hyperedge, change))
        tabu = (tabu + [hyperedge])[-TABU_LIST_LENGTH:]
        connectivity = all_connectivities(problem, multiplicity)
        if edge_count(multiplicity) > best_size:
            best_size, best_graph = edge_count(multiplicity), multiplicity
    return best_size, best_graph
#</tabu>


#<greedy>
def random_greedy_growth(problem, deadline, seed):
    generator = random.Random(seed)
    best_size, best_graph = 0, {}
    while time.time() < deadline:
        multiplicity, blocked = {}, set()
        connectivity = all_connectivities(problem, multiplicity)
        while time.time() < deadline:
            hyperedge = next_addition(problem, multiplicity, connectivity,
                                      blocked, [], best_size, generator)
            if hyperedge is None:
                break                       # a local optimum: restart
            multiplicity = apply(multiplicity, (hyperedge, +1))
            connectivity = all_connectivities(problem, multiplicity)
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
def belongs_to_model(problem, claimed_size, multiplicity):
    """Every hyperedge is one the model allows, on vertices 0..n-1 with
    an allowed split into tails and heads, with a whole number of
    copies up to the largest multiplicity, and the copies add up to the
    size."""
    allowed = set(possible_hyperedges(problem))
    cap = largest_multiplicity(problem)
    for hyperedge, copies in multiplicity.items():
        if hyperedge not in allowed:
            return False
        if type(copies) is not int or not 0 <= copies <= cap:
            return False
    return edge_count(multiplicity) == claimed_size


def check_certificate(problem, claimed_size, multiplicity, certificate):
    """No flows and no search code, only following edges."""
    if not belongs_to_model(problem, claimed_size, multiplicity):
        return False
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
HEURISTIC_SEARCH = "tabu"                   # or "greedy", for every model
SEARCHES = {"tabu": tabu_search, "greedy": random_greedy_growth}


def solve(problem, search_name=HEURISTIC_SEARCH):
    has_nauty = nauty_commands(problem, 1) is not None
    part = SECONDS_PER_CASE / (3 if has_nauty else 2)
    size, graph, exact = exhaustive_search(problem, time.time() + part)
    if not exact:
        search = SEARCHES[search_name]
        found_size, found_graph = search(problem, time.time() + part,
                                         seed=1)
        if found_size > size:
            size, graph = found_size, found_graph
        size, graph, exact = prove_largest(problem, size, graph,
                                           time.time() + part)
    certificate = write_certificate(problem, graph)
    checked = check_certificate(problem, size, graph, certificate)
    return size, exact, checked, witness(problem, graph, certificate)


def witness(problem, graph, certificate):
    """The graph and its certificate, in a form a reader can check."""
    def as_list(hyperedge):
        return [list(hyperedge.TAILS), list(hyperedge.MEMBERS)]
    return {
        "hyperedges": [as_list(e) + [copies]
                       for e, copies in sorted(graph.items()) if copies],
        "certificate": [[u, v, removed_vertices,
                         [as_list(e) for e in removed_hyperedges]]
                        for (u, v), (removed_vertices, removed_hyperedges)
                        in certificate.items()]}


#</driver>


#<verifier>
def verify_witnesses(path="witnesses_tabu.json"):
    """Checks every saved graph and certificate. Uses only the standard
    library and nothing else from this file, so it can be read alone."""
    with open(path) as file:
        records = json.load(file)
    failed = [record for record in records if not verify_record(record)]
    passed = len(records) - len(failed)
    print(f"{passed} of {len(records)} witnesses pass")
    return not failed


def allowed_tail_counts(model, r):
    if "directed" not in model:
        return {0}
    if r == 2 or model.endswith("forward"):
        return {1}
    if model.endswith("backward"):
        return {r - 1}
    return set(range(1, r))                 # general: any split


def verify_record(record):
    n, m, r = record["n"], record["m"], record["edge_size"]
    model, separation = record["model"], record["separation"]
    directed = "directed" in model
    if ("hypergraph" in model) != (r >= 3) or r < 2:
        return False
    if separation not in ("edge", "vertex"):
        return False
    cap = m - 1 if "multi" in model else 1
    tail_counts = allowed_tail_counts(model, r)
    copies = {}
    for tails, members, count in record["hyperedges"]:
        if sorted(set(members)) != members or len(members) != r:
            return False                    # sorted, no repeats
        if not all(type(v) is int and 0 <= v < n for v in members):
            return False
        if sorted(set(tails)) != tails:
            return False                    # sorted, no repeats
        if not set(tails) <= set(members):
            return False
        if len(tails) not in tail_counts:
            return False
        if type(count) is not int or not 1 <= count <= cap:
            return False
        key = (tuple(tails), tuple(members))
        if key in copies:
            return False                    # listed twice
        copies[key] = count
    if sum(copies.values()) != record["size"]:
        return False
    certificate = {}
    for u, v, removed_vertices, removed_hyperedges in \
            record["certificate"]:
        certificate[(u, v)] = (set(removed_vertices), {
            (tuple(tails), tuple(members))
            for tails, members in removed_hyperedges})
    if directed:
        pairs = list(itertools.permutations(range(n), 2))
    else:
        pairs = list(itertools.combinations(range(n), 2))
    if sorted(certificate) != pairs \
            or len(record["certificate"]) != len(pairs):
        return False                        # one entry for every pair
    for u, v in pairs:
        removed_vertices, removed_hyperedges = certificate[(u, v)]
        if separation == "edge" and removed_vertices:
            return False
        if u in removed_vertices or v in removed_vertices:
            return False
        if not removed_hyperedges <= copies.keys():
            return False
        removed = len(removed_vertices) + sum(
            copies[e] for e in removed_hyperedges)
        if removed > m - 1:
            return False
        reached, frontier = {u}, [u]
        while frontier:
            vertex = frontier.pop()
            for tails, members in copies:
                if (tails, members) in removed_hyperedges:
                    continue
                if vertex not in (tails or members):
                    continue                # enter only at a tail
                for nxt in members:
                    if nxt in tails or nxt in reached \
                            or nxt in removed_vertices:
                        continue
                    reached.add(nxt)
                    frontier.append(nxt)
        if v in reached:
            return False
    return True
#</verifier>


#<main>
def print_table(model, separation, solved, largest_m, witnesses):
    print(f"\n{model.NAME}, {separation.value} separation")
    print("n \\ m" + "".join(f"{m:>6}" for m in range(2, largest_m + 1)))
    rows = {}
    for problem, (size, exact, checked, found) in solved:
        witnesses.append({
            "model": model.NAME, "separation": separation.value,
            "n": problem.VERTEX_COUNT, "m": problem.THRESHOLD_M,
            "edge_size": model.EDGE_SIZE, "size": size,
            "search_finished": exact,
            "certificate_checked": checked, **found})
        mark = ("*" if exact else " ") if checked else "!"
        rows.setdefault(problem.VERTEX_COUNT, []).append(f"{size:>5}{mark}")
    for n, cells in rows.items():
        print(f"{n:>5}" + "".join(cells), flush=True)


def main(search_name=HEURISTIC_SEARCH, largest_n=7, largest_m=7):
    problems = [Problem(model, separation, n, m)
                for model in ALL_MODELS() for separation in Separation
                for n in range(model.EDGE_SIZE, largest_n + 1)
                for m in range(2, largest_m + 1)]
    nauty = subprocess.run(["geng", "--version"], capture_output=True,
                           text=True).stdout.strip()
    print(f"Python {platform.python_version()}, "
          f"networkx {networkx.__version__},")
    print(f"{nauty},")
    print(f"{platform.platform()},")
    print(f"{os.cpu_count()} processor cores, {SECONDS_PER_CASE} "
          f"seconds per case, {search_name} search, "
          f"run on {time.strftime('%Y-%m-%d')}.")
    print("Edges of the densest feasible graph found, every one certified.")
    print("* the search finished, so the value is exact;",
          "! the certificate failed")
    witnesses = []
    with Pool() as pool:
        solved = zip(problems, pool.imap(
            partial(solve, search_name=search_name), problems))
        for (model, separation), table in itertools.groupby(
                solved, key=lambda pair: (pair[0].MODEL, pair[0].SEPARATION)):
            print_table(model, separation, table, largest_m, witnesses)
    with open(f"witnesses_{search_name}.json", "w") as file:
        file.write("[\n" + ",\n".join(json.dumps(w) for w in witnesses)
                   + "\n]\n")


if __name__ == "__main__":
    arguments = sys.argv[1:]                # "greedy", or "verify [file]"
    if arguments[:1] == ["verify"]:
        sys.exit(0 if verify_witnesses(*arguments[1:]) else 1)
    main(*arguments)
#</main>
