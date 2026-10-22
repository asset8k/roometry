"""Bounded floor-grid navigation with continuous clearance on every graph edge.

Nodes sample possible centers of an upright circular walking probe. Distances to
rotated catalog rectangles are exact; the grid can miss a route between samples,
but connecting nodes never jumps through a thin obstacle or cuts a corner.
"""

from array import array
from collections import deque
from dataclasses import dataclass
from math import ceil, floor, hypot, sqrt

from roometry.scenes.geometry import Point2, Rectangle, TOLERANCE_M

MAX_GRID_CELLS = 40_000
MAX_RASTER_TESTS = 1_000_000


class NavigationLimit(Exception):
    """No incomplete obstacle raster may be reported as a successful route."""


def point_rectangle_distance(point: Point2, rectangle: Rectangle) -> float:
    dx, dz = point[0] - rectangle.center[0], point[1] - rectangle.center[1]
    u, v = rectangle.axes
    return hypot(
        max(0., abs(dx * u[0] + dz * u[1]) - rectangle.half_extents[0]),
        max(0., abs(dx * v[0] + dz * v[1]) - rectangle.half_extents[1]),
    )


def segment_rectangle_distance(a: Point2, b: Point2, rectangle: Rectangle) -> float:
    # Slab intersection in the rectangle's own orthonormal frame.
    low, high = 0., 1.
    for axis, extent in zip(rectangle.axes, rectangle.half_extents):
        start = (a[0] - rectangle.center[0]) * axis[0] + (a[1] - rectangle.center[1]) * axis[1]
        delta = (b[0] - a[0]) * axis[0] + (b[1] - a[1]) * axis[1]
        if abs(delta) < 1e-12:
            if abs(start) > extent:
                high = -1.
                break
        else:
            t1, t2 = (-extent - start) / delta, (extent - start) / delta
            low, high = max(low, min(t1, t2)), min(high, max(t1, t2))
    if low <= high:
        return 0.
    # For two disjoint convex shapes their nearest points include an endpoint
    # or a rectangle corner; checking nodes alone would miss a thin barrier.
    result = min(point_rectangle_distance(a, rectangle), point_rectangle_distance(b, rectangle))
    dx, dz = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dz * dz
    for x, z in rectangle.corners:
        t = max(0., min(1., ((x - a[0]) * dx + (z - a[1]) * dz) / length_sq)) if length_sq else 0.
        result = min(result, hypot(x - a[0] - t * dx, z - a[1] - t * dz))
    return result


@dataclass
class NavigationGrid:
    nx: int
    nz: int
    dx: float
    dz: float
    nodes: array
    horizontal: array
    vertical: array

    def point(self, index: int) -> Point2:
        return ((index % self.nx + .5) * self.dx, (index // self.nx + .5) * self.dz)

    def regions(self, radius: float) -> tuple[array, list[int]]:
        labels = array("i", [-1]) * len(self.nodes)
        counts: list[int] = []
        for start, clearance in enumerate(self.nodes):
            if labels[start] >= 0 or clearance + TOLERANCE_M < radius:
                continue
            region = len(counts)
            labels[start] = region
            pending = deque([start])
            count = 0
            while pending:
                index = pending.popleft()
                count += 1
                i, j = index % self.nx, index // self.nx
                neighbors = []
                if i > 0:
                    neighbors.append((index - 1, self.horizontal[index - 1]))
                if i + 1 < self.nx:
                    neighbors.append((index + 1, self.horizontal[index]))
                if j > 0:
                    neighbors.append((index - self.nx, self.vertical[index - self.nx]))
                if j + 1 < self.nz:
                    neighbors.append((index + self.nx, self.vertical[index]))
                for other, edge_clearance in neighbors:
                    if labels[other] < 0 and self.nodes[other] + TOLERANCE_M >= radius and edge_clearance + TOLERANCE_M >= radius:
                        labels[other] = region
                        pending.append(other)
            counts.append(count)
        return labels, counts


def grid_shape(width: float, length: float, cell_size: float) -> tuple[int, int]:
    """Shared, bounded sampling dimensions for route and floor-area estimates."""
    nx, nz = max(1, ceil(width / cell_size)), max(1, ceil(length / cell_size))
    if nx * nz > MAX_GRID_CELLS:
        factor = sqrt(MAX_GRID_CELLS / (nx * nz))
        nx, nz = max(1, floor(nx * factor)), max(1, floor(nz * factor))
    return nx, nz


def build_navigation_grid(width: float, length: float, cell_size: float,
                          footprints: tuple[Rectangle, ...], radius: float) -> NavigationGrid:
    nx, nz = grid_shape(width, length, cell_size)
    dx, dz = width / nx, length / nz
    nodes, horizontal, vertical = array("d"), array("d"), array("d")
    for j in range(nz):
        z = (j + .5) * dz
        for i in range(nx):
            x = (i + .5) * dx
            nodes.append(min(radius, x, width - x, z, length - z))
            horizontal.append(min(radius, x, width - (x + dx), z, length - z) if i + 1 < nx else 0.)
            vertical.append(min(radius, x, width - x, z, length - (z + dz)) if j + 1 < nz else 0.)
    grid = NavigationGrid(nx, nz, dx, dz, nodes, horizontal, vertical)
    work = 0
    for rectangle in footprints:
        xs, zs = zip(*rectangle.corners)
        i0 = max(0, floor((min(xs) - radius) / dx) - 1)
        i1 = min(nx - 1, ceil((max(xs) + radius) / dx))
        j0 = max(0, floor((min(zs) - radius) / dz) - 1)
        j1 = min(nz - 1, ceil((max(zs) + radius) / dz))
        work += max(0, i1 - i0 + 1) * max(0, j1 - j0 + 1) * 3
        if work > MAX_RASTER_TESTS:
            raise NavigationLimit
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                index = j * nx + i
                p = grid.point(index)
                nodes[index] = min(nodes[index], point_rectangle_distance(p, rectangle))
                if i + 1 < nx:
                    horizontal[index] = min(horizontal[index], segment_rectangle_distance(p, (p[0] + dx, p[1]), rectangle))
                if j + 1 < nz:
                    vertical[index] = min(vertical[index], segment_rectangle_distance(p, (p[0], p[1] + dz), rectangle))
    return grid
