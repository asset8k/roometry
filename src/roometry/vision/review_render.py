"""Private CPU diagnostic views from shared catalog parts, not photorealism.

The browser viewer stays unchanged. This renderer uses the same local XYZ,
parent scale/Y rotation, active placement parts and material-role colors. Curved
parts are tessellated coarsely, rounded/arched/pleated details are simplified.
Painter ordering is approximate: the reviewer must use JSON for occluded items.
Nothing is written to disk or stored in graph checkpoints.
"""

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from math import cos, pi, sin, sqrt

from PIL import Image, ImageDraw, ImageFont

from roometry.scenes.catalog import ASSET_CATALOG
from roometry.scenes.furniture import FurniturePart, Point
from roometry.scenes.schemas import RoomScene, SceneObject
from roometry.scenes.materials import DEFAULT_MATERIAL_COLORS as COLORS
from roometry.vision.review_schemas import ViewId

VIEW_IDS: tuple[ViewId, ...] = ("cutaway_nw", "cutaway_se", "top")
RENDER_LIMITATIONS = "Diagnostic orthographic catalog views, not camera-matched or photorealistic screenshots. Rounded/arched/pleated details, reflections and transparency are simplified; painter ordering can hide parts. Top view omits ceiling fixtures. Use JSON to check occluded or small items; do not infer a missing object solely from these renders."


@dataclass(frozen=True)
class ReviewView:
    view_id: ViewId
    image: bytes


def _rotate(point: Point, angles: Point) -> Point:
    x, y, z = angles
    cx, cy, cz, sx, sy, sz = cos(x), cos(y), cos(z), sin(x), sin(y), sin(z)
    rows = ((cy*cz, -cy*sz, sy), (cx*sz+sx*sy*cz, cx*cz-sx*sy*sz, -sx*cy), (sx*sz-cx*sy*cz, sx*cz+cx*sy*sz, cx*cy))
    return tuple(sum(a*b for a, b in zip(row, point)) for row in rows)


def world_point(obj: SceneObject, part: FurniturePart, point: Point) -> Point:
    # Torus geometry uses part.depth/(2*radius), matching FurnitureModel.tsx.
    rotated = _rotate(tuple(a*b for a, b in zip(point, part.size)), part.rotation)
    dims = obj.dimensions_m
    scaled = tuple((a+b)*d for a,b,d in zip(rotated, part.at, (dims.width, dims.height, dims.depth)))
    x, y, z = _rotate(scaled, (0, obj.rotation_y_rad, 0))
    return x+obj.position.x, y+obj.position.y, z+obj.position.z


@lru_cache(maxsize=8192)
def _mesh(part: FurniturePart) -> tuple[tuple[Point, ...], ...]:
    shape = part.shape
    if shape in {"box", "rounded_box", "pleated_panel", "arched_panel"}:
        vertices = [(x/2, y/2, z/2) for x,y,z in ((-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1))]
        return tuple(tuple(vertices[i] for i in face) for face in ((0,1,2,3),(4,7,6,5),(0,4,5,1),(3,2,6,7),(0,3,7,4),(1,5,6,2)))
    if shape == "ellipsoid":
        rings = [[(.5*sin(pi*j/6)*cos(2*pi*i/10), .5*cos(pi*j/6), .5*sin(pi*j/6)*sin(2*pi*i/10)) for i in range(10)] for j in range(7)]
    elif shape == "torus":
        rings = [[((.5-part.radius+part.radius*cos(2*pi*j/6))*cos(2*pi*i/12), (.5-part.radius+part.radius*cos(2*pi*j/6))*sin(2*pi*i/12), .5*sin(2*pi*j/6)) for i in range(12)] for j in range(7)]
    elif shape == "basin" and part.profile:
        rings = [[(r*cos(2*pi*i/12), y-.5, r*sin(2*pi*i/12)) for i in range(12)] for r,y in part.profile]
    else:
        n = 4 if shape == "frustum" else 12
        offset = pi/4 if shape == "frustum" else 0
        radius = sqrt(.5) if shape == "frustum" else .5
        rings = [[(radius*scale*cos(2*pi*i/n+offset), y, radius*scale*sin(2*pi*i/n+offset)) for i in range(n)] for scale,y in ((part.bottom_scale,-.5),(part.top_scale,.5))]
    n = len(rings[0])
    faces = [tuple((rings[j][i], rings[j][(i+1)%n], rings[j+1][(i+1)%n], rings[j+1][i])) for j in range(len(rings)-1) for i in range(n)]
    if shape in {"cylinder", "frustum"}:
        faces.extend((tuple(rings[0]), tuple(rings[-1])))
    return tuple(faces)


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i+2], 16) for i in (1,3,5))


def material_color(obj: SceneObject, role: str) -> str:
    if obj.material_colors is not None and role in {"wood", "metal", "linen", "inset", "rubber", "enamel"}:
        override = getattr(obj.material_colors, role)
        if override:
            return override
    if role == "primary_dark":
        # Three.js multiplyScalar(.7) operates in linear color space.
        def dark(value):
            value /= 255
            linear = value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4
            linear *= .7
            return round(255*(12.92*linear if linear <= .0031308 else 1.055*linear**(1/2.4)-.055))
        return "#" + "".join(f"{dark(v):02X}" for v in _rgb(obj.color))
    return obj.color if role in {"primary", "primary_metal", "primary_ceramic"} else COLORS[role]


def render_views(scene: RoomScene, *, annotate: bool = True, view_ids: tuple[ViewId, ...] = VIEW_IDS) -> list[ReviewView]:
    """Bounded 768px PNGs; public previews omit diagnostic labels and arrows."""
    width, length, height = scene.room.width_m, scene.room.length_m, scene.room.height_m
    faces = []
    for obj in scene.objects:
        for part in ASSET_CATALOG[obj.asset_id].parts:
            if part.placements and obj.placement not in part.placements:
                continue
            color = material_color(obj, part.material)
            for mesh_face in _mesh(part):
                if len(faces) >= 40_000:
                    raise ValueError("Diagnostic render face budget exceeded.")
                faces.append((tuple(world_point(obj, part, point) for point in mesh_face), color, obj.placement == "ceiling"))
    corners = [(x,y,z) for x in (0,width) for y in (0,height) for z in (0,length)]
    results = []
    for view_id in view_ids:
        # Orthographic camera basis: +north is -Z, functional model front +Z.
        if view_id == "top":
            right, up, toward = (1,0,0), (0,0,-1), (0,1,0)
        else:
            sign = -1 if view_id == "cutaway_nw" else 1
            right, up, toward = (sign/sqrt(2),0,-sign/sqrt(2)), (-sign/sqrt(6),2/sqrt(6),-sign/sqrt(6)), (sign/sqrt(3),1/sqrt(3),sign/sqrt(3))
        def dot(p, basis):
            return sum(a*b for a,b in zip(p,basis))
        projected = [(dot(p,right),dot(p,up)) for p in corners]
        min_x,max_x = min(p[0] for p in projected),max(p[0] for p in projected)
        min_y,max_y = min(p[1] for p in projected),max(p[1] for p in projected)
        scale = min(660/(max_x-min_x),610/(max_y-min_y))
        def project(p):
            return (384+(dot(p,right)-(min_x+max_x)/2)*scale, 392-(dot(p,up)-(min_y+max_y)/2)*scale)
        image = Image.new("RGB", (768,768), "#F5F3ED")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=14)
        if annotate:
            draw.text((16,12), f"{view_id} | {width:g}m X (W-E) x {length:g}m Z (N-S) x {height:g}m Y", fill="#26342D", font=font)
            draw.text((16,34), "Approximate catalog geometry; textures/curves simplified; not photo-matched", fill="#526154", font=ImageFont.load_default(size=12))
        floor = ((0,0,0),(width,0,0),(width,0,length),(0,0,length))
        draw.polygon([project(p) for p in floor], fill=scene.finishes.floor_color if scene.finishes and scene.finishes.floor_color else "#D6BEA0", outline="#697367")
        wall_faces = []
        walls = {"north": ((0,0,0),(width,0,0),(width,height,0),(0,height,0)), "south": ((0,0,length),(width,0,length),(width,height,length),(0,height,length)), "west": ((0,0,0),(0,0,length),(0,height,length),(0,height,0)), "east": ((width,0,0),(width,0,length),(width,height,length),(width,height,0))}
        visible_walls = set() if view_id == "top" else ({"south","east"} if view_id == "cutaway_nw" else {"north","west"})
        for wall in visible_walls:
            wall_faces.append((walls[wall], scene.finishes.wall_color if scene.finishes and scene.finishes.wall_color else "#ECE9DF", False))
        opening_faces = []
        for opening in scene.openings:
            a,b = opening.offset_m,opening.offset_m+opening.width_m
            low,high = opening.sill_height_m,opening.sill_height_m+opening.height_m
            if opening.wall in {"north","south"}:
                z = .002 if opening.wall == "north" else length-.002
                vertices = ((a,low,z),(b,low,z),(b,high,z),(a,high,z))
            else:
                x = .002 if opening.wall == "west" else width-.002
                vertices = ((x,low,a),(x,low,b),(x,high,b),(x,high,a))
            color = "#B2C9CB" if opening.type == "window" else "#F0F0ED"
            if view_id == "top":
                draw.line([project(vertices[0]),project(vertices[1])], fill=color, width=7)
            else:
                opening_faces.append((vertices,color,False))
        # Far walls are background planes. Sorting an entire large wall by its
        # centroid can wrongly paint it over nearby console parts or windows.
        # Near cutaway walls are omitted; opening markers stay visible.
        for points,color,_ in [*wall_faces, *opening_faces]:
            draw.polygon([project(p) for p in points], fill=color)
        combined = [(points,color,ceiling) for points,color,ceiling in faces if not (view_id == "top" and ceiling)]
        for points,color,_ in sorted(combined, key=lambda face: sum(dot(p,toward) for p in face[0])/len(face[0])):
            a,b,c = points[:3]
            u,v = tuple(b[i]-a[i] for i in range(3)),tuple(c[i]-a[i] for i in range(3))
            normal=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            norm=sqrt(sum(n*n for n in normal))
            shade=1 if not norm else .78+.22*abs(normal[1])/norm
            draw.polygon([project(p) for p in points], fill=tuple(round(channel*shade) for channel in _rgb(color)))
        if annotate:
            for wall,points in walls.items():
                middle = tuple(sum(p[i] for p in points)/4 for i in range(3))
                draw.text(project(middle), wall, fill="#425747", font=font, stroke_width=1, stroke_fill="#FFFFFF")
            for index,obj in enumerate(scene.objects):
                if index >= 40 or (view_id == "top" and obj.placement == "ceiling"):
                    continue
                point = (obj.position.x,obj.position.y+obj.dimensions_m.height,obj.position.z)
                draw.text(project(point), str(index+1), fill="#142D23", font=font, stroke_width=1, stroke_fill="#FFFFFF")
                # Functional front arrows make 180-degree errors assessable even
                # when stylized upholstery obscures the model's front.
                if obj.type in {"sofa","chair","armchair","tv","bed"}:
                    start=(obj.position.x,.03,obj.position.z)
                    end=(start[0]+.4*sin(obj.rotation_y_rad),.03,start[2]+.4*cos(obj.rotation_y_rad))
                    a,b=project(start),project(end)
                    draw.line([a,b], fill="#A32E24", width=3)
                    draw.ellipse((b[0]-3,b[1]-3,b[0]+3,b[1]+3),fill="#A32E24")
            draw.text((16,738), "Numbers = JSON object order (1-based); red arrows = functional front; north is -Z.", fill="#354A3C", font=ImageFont.load_default(size=12))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        image.close()
        results.append(ReviewView(view_id, buffer.getvalue()))
    return results
