"""Bounded support descendants shared by assembly and protected transforms."""


def support_closure(objects, roots):
    result = set(roots)
    for _ in range(len(objects)):
        expanded = result | {obj.id for obj in objects if obj.support_object_id in result}
        if expanded == result:
            break
        result = expanded
    return result


def support_order(objects):
    remaining = {obj.id: obj for obj in objects}
    while remaining:
        ready = sorted((obj for obj in remaining.values() if obj.support_object_id not in remaining), key=lambda obj: obj.id)
        if not ready:
            return  # Structural validation rejects cycles; never guess a root.
        for obj in ready:
            del remaining[obj.id]
            yield obj
