"""Relative edit policy: retain observed defects, reject new clearance losses.

Hard component validity is separate. Compare reports made with the same server
policies; unknown swings are never upgraded to clear. Sampled area changes up to
four grid cells (at least .05 m²) are ignored to avoid boundary noise.
"""

from roometry.scenes.edit_schemas import EditIssue
from roometry.scenes.validation import GeometryReport


def clearance_regressions(before: GeometryReport, after: GeometryReport) -> tuple[EditIssue, ...]:
    issues: list[EditIssue] = []

    def reject(ids: tuple[str, ...], message: str) -> None:
        issues.append(EditIssue(code="clearance_regression", entity_ids=ids, message=message))

    if before.door_clearance is not None:
        old = {d.door_id: d for d in before.door_clearance.doors}
        new = {d.door_id: d for d in after.door_clearance.doors} if after.door_clearance else {}
        for key, previous in old.items():
            current = new.get(key)
            if current is None:
                reject((key,), "Door clearance could not be rechecked. Previous layout retained.")
                continue
            for field, label in (("access", "door access"), ("swing", "inward door swing")):
                was, now = getattr(previous, field + "_status"), getattr(current, field + "_status")
                added = set(getattr(current, field + "_object_ids")) - set(getattr(previous, field + "_object_ids"))
                if now == "blocked" and (was != "blocked" or added):
                    reject((key, *sorted(added)), f"This edit adds an obstruction to {label}. Previous layout retained.")
                elif was in {"clear", "blocked"} and now not in {"clear", "blocked"}:
                    reject((key,), f"This edit prevents rechecking {label}. Previous layout retained.")

    previous, current = before.walking_space, after.walking_space
    if previous is not None:
        if current is None or (previous.status != "unassessed" and current.status == "unassessed"):
            reject((), "Walking routes could not be rechecked. Previous layout retained.")
        elif current.status != "unassessed":
            severity = {"clear": 0, "narrow": 1, "blocked": 2, "unassessed": 3}
            if previous.status != "unassessed" and severity[current.status] > severity[previous.status]:
                reject((), "This edit blocks or narrows a previously better walking route. Previous layout retained.")
            entries = {e.door_id: e for e in current.entrances}
            for entry in previous.entrances:
                candidate = entries.get(entry.door_id)
                if entry.status != "unassessed" and (candidate is None or severity[candidate.status] > severity[entry.status]):
                    reject((entry.door_id,), "This edit reduces the entrance route clearance. Previous layout retained.")
                if candidate:
                    lost = set(entry.reachable_door_ids) - set(candidate.reachable_door_ids)
                    if lost:
                        reject((entry.door_id, *sorted(lost)), "This edit disconnects previously connected entrances. Previous layout retained.")
            if previous.status != "unassessed":
                tolerance = max(.05, 4 * (current.grid_step_x_m or 0) * (current.grid_step_z_m or 0))
                if (current.significant_region_count > max(1, previous.significant_region_count)
                    or (current.significant_region_count > 1 and
                        (current.disconnected_center_area_m2 or 0) > (previous.disconnected_center_area_m2 or 0) + tolerance)):
                    reject((), "This edit increases disconnected walking space. Previous layout retained.")
    return tuple(issues)
