"""
Convert Cabinet Vision .dae exports to .glb for the Android mini viewer.

Uses Assimp CLI for geometry-preserving conversion:
    assimp export <dae> <glb> glb2 -tri -gn -jiv -et -emb

Minimal preprocessing only:
- wait for file-write stabilization,
- normalize <init_from> Windows paths to forward slashes.
- triangulate COLLADA <polygons>/<ph>/<h> into <triangles> while preserving
  hole cutouts so Assimp can ingest the file.

Output file: 3d_medium.glb  (alongside the original 3d.dae)
"""

import copy
import json
import logging
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List
from uuid import uuid4

logger = logging.getLogger('main')

_INIT_FROM = re.compile(r'<init_from>([\s\S]*?)</init_from>')
_TRANSPARENCY_FLOAT = re.compile(r'<transparency>\s*<float>([\d.]+)</float>\s*</transparency>', re.IGNORECASE)

def _find_assimp_exe(configured_path: str | None = None) -> str | None:
    candidates = (
        configured_path,
        os.environ.get("ASSIMP_PATH"),
        shutil.which("assimp"),
        r"C:\Scripts\Assimp\assimp\build_windows\bin\Release\assimp.exe",
    )
    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate)
        if p.exists() and p.is_file():
            return str(p)
    return None


def _find_room_dae(room_dir: Path) -> Path | None:
    """
    Find the room DAE in a case-insensitive way.
    Prefers a file named '3d.dae' (any case), otherwise falls back to first .dae.
    """
    preferred = []
    fallback = []
    try:
        for entry in room_dir.iterdir():
            if not entry.is_file() or entry.suffix.lower() != ".dae":
                continue
            if entry.stem.lower() == "3d":
                preferred.append(entry)
            else:
                fallback.append(entry)
    except OSError:
        return None
    if preferred:
        return sorted(preferred, key=lambda p: p.name.lower())[0]
    if fallback:
        return sorted(fallback, key=lambda p: p.name.lower())[0]
    return None


def _wait_for_stable_file(path: Path, timeout_seconds: float = 45.0, interval_seconds: float = 1.0, stable_checks: int = 3) -> bool:
    """
    Wait until file size/mtime stop changing. Helps avoid converting while
    Cabinet Vision is still writing the DAE to disk.
    """
    stable = 0
    last_sig = None
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            st = path.stat()
        except OSError:
            time.sleep(interval_seconds)
            continue

        sig = (st.st_size, st.st_mtime_ns)
        if sig == last_sig:
            stable += 1
            if stable >= stable_checks:
                return True
        else:
            stable = 0
            last_sig = sig
        time.sleep(interval_seconds)

    return False


def _fix_windows_paths(content: str) -> str:
    return _INIT_FROM.sub(
        lambda m: "<init_from>" + m.group(1).replace("\\\\", "/").replace("\\", "/") + "</init_from>",
        content
    )


def _resolve_glass_transparency() -> float:
    raw = os.environ.get("GLASS_TRANSPARENCY", "0.8")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.8
    # Keep the value in sensible alpha range.
    return max(0.0, min(1.0, value))


def _fix_collada_transparency(content: str) -> str:
    """
    Cabinet Vision DAE transparency often imports as too-opaque in Assimp.
    Match KKC_Portal behavior by forcing COLLADA transparency floats to a
    configurable glass value (default 0.8).
    """
    glass_transparency = _resolve_glass_transparency()
    replacement = f"<transparency><float>{glass_transparency:g}</float></transparency>"
    return _TRANSPARENCY_FLOAT.sub(replacement, content)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _extract_ns(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[0][1:]
    return ""


def _qn(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}" if ns else local


def _parse_ints(text: str) -> List[int]:
    if not text:
        return []
    return [int(x) for x in text.split()]


def _find_child_by_local(parent: ET.Element, local: str) -> ET.Element | None:
    for child in list(parent):
        if _local_name(child.tag) == local:
            return child
    return None


def _build_source_float_map(mesh: ET.Element) -> dict[str, List[float]]:
    out: dict[str, List[float]] = {}
    for source in list(mesh):
        if _local_name(source.tag) != "source":
            continue
        source_id = source.get("id")
        if not source_id:
            continue
        float_array = _find_child_by_local(source, "float_array")
        if float_array is None or not float_array.text:
            continue
        values = [float(x) for x in float_array.text.split()]
        out[source_id] = values
        float_array_id = float_array.get("id")
        if float_array_id:
            out[float_array_id] = values
    return out


def _build_accessor_map(mesh: ET.Element) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for source in list(mesh):
        if _local_name(source.tag) != "source":
            continue
        source_id = source.get("id")
        if not source_id:
            continue
        tech = _find_child_by_local(source, "technique_common")
        if tech is None:
            continue
        accessor = _find_child_by_local(tech, "accessor")
        if accessor is None:
            continue
        src = accessor.get("source", "").lstrip("#")
        stride = int(accessor.get("stride", "1"))
        offset = int(accessor.get("offset", "0"))
        out[source_id] = {"float_array_id": src, "stride": stride, "offset": offset}
    return out


def _resolve_position_accessor(mesh: ET.Element) -> dict[str, str]:
    # Map vertices id -> POSITION source id
    vertices_to_position: dict[str, str] = {}
    for elem in list(mesh):
        if _local_name(elem.tag) != "vertices":
            continue
        vertices_id = elem.get("id")
        if not vertices_id:
            continue
        for child in list(elem):
            if _local_name(child.tag) != "input":
                continue
            if child.get("semantic") == "POSITION":
                vertices_to_position[vertices_id] = child.get("source", "").lstrip("#")
                break

    return vertices_to_position


def _project_to_2d(points3d: List[tuple[float, float, float]]) -> List[tuple[float, float]]:
    # Newell normal to choose drop axis.
    nx = ny = nz = 0.0
    n = len(points3d)
    for i in range(n):
        x1, y1, z1 = points3d[i]
        x2, y2, z2 = points3d[(i + 1) % n]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)

    ax, ay, az = abs(nx), abs(ny), abs(nz)
    if ax >= ay and ax >= az:
        # Drop X
        return [(y, z) for _, y, z in points3d]
    if ay >= ax and ay >= az:
        # Drop Y
        return [(x, z) for x, _, z in points3d]
    # Drop Z
    return [(x, y) for x, y, _ in points3d]


def _ring_set_has_valid_2d_geometry(
    rings: List[List[List[int]]],
    points2d_for_corner: List[tuple[float, float]]
) -> bool:
    if not points2d_for_corner:
        return False
    idx = 0
    for ring in rings:
        ring_len = len(ring)
        if ring_len < 3:
            return False
        ring_pts = points2d_for_corner[idx:idx + ring_len]
        idx += ring_len
        unique_pts = {(round(p[0], 10), round(p[1], 10)) for p in ring_pts}
        if len(unique_pts) < 3:
            return False
        area2 = 0.0
        for i in range(ring_len):
            x1, y1 = ring_pts[i]
            x2, y2 = ring_pts[(i + 1) % ring_len]
            area2 += (x1 * y2) - (x2 * y1)
        if abs(area2) < 1e-12:
            return False
    return True


def _triangulate_rings(
    rings: List[List[List[int]]],
    points_for_corner: List[tuple[float, float, float]],
    points2d_for_corner: List[tuple[float, float]] | None = None,
) -> List[int]:
    """
    Triangulate polygon rings (outer + holes) and return corner indices.
    """
    import numpy as np
    import mapbox_earcut as earcut

    # Flatten points/rings for earcut
    if (
        points2d_for_corner
        and len(points2d_for_corner) == len(points_for_corner)
        and _ring_set_has_valid_2d_geometry(rings, points2d_for_corner)
    ):
        pts_2d = points2d_for_corner
    else:
        pts_2d = _project_to_2d(points_for_corner)
    verts = np.array(pts_2d, dtype=np.float64)
    ends: List[int] = []
    total = 0
    for ring in rings:
        total += len(ring)
        ends.append(total)
    ring_ends = np.array(ends, dtype=np.uint32)
    idx = earcut.triangulate_float64(verts, ring_ends)
    return [int(x) for x in idx.tolist()]


def _fit_affine_3d_to_uv(
    points3d: List[tuple[float, float, float]],
    uvs: List[tuple[float, float]]
) -> tuple[list[float], list[float]] | None:
    if len(points3d) != len(uvs) or len(points3d) < 3:
        return None
    try:
        import numpy as np
    except Exception:
        return None

    a = np.array([[x, y, z, 1.0] for x, y, z in points3d], dtype=np.float64)
    bu = np.array([u for u, _ in uvs], dtype=np.float64)
    bv = np.array([v for _, v in uvs], dtype=np.float64)
    try:
        cu, *_ = np.linalg.lstsq(a, bu, rcond=None)
        cv, *_ = np.linalg.lstsq(a, bv, rcond=None)
    except Exception:
        return None
    return (cu.tolist(), cv.tolist())


def _apply_affine_3d_to_uv(
    coeffs: tuple[list[float], list[float]],
    point3d: tuple[float, float, float]
) -> tuple[float, float]:
    cu, cv = coeffs
    x, y, z = point3d
    u = cu[0] * x + cu[1] * y + cu[2] * z + cu[3]
    v = cv[0] * x + cv[1] * y + cv[2] * z + cv[3]
    return (float(u), float(v))


def _convert_polygons_element_to_triangles(
    polygons: ET.Element,
    mesh: ET.Element,
    ns: str,
    source_float_map: dict[str, List[float]],
    accessor_map: dict[str, dict],
    vertices_to_position: dict[str, str],
) -> ET.Element:
    input_elems = [c for c in list(polygons) if _local_name(c.tag) == "input"]
    if not input_elems:
        raise RuntimeError("polygons element missing input channels")

    max_offset = max(int(inp.get("offset", "0")) for inp in input_elems)
    tuple_size = max_offset + 1

    vertex_input = next((inp for inp in input_elems if inp.get("semantic") == "VERTEX"), None)
    if vertex_input is None:
        raise RuntimeError("polygons element missing VERTEX input")
    vertex_offset = int(vertex_input.get("offset", "0"))
    vertices_id = vertex_input.get("source", "").lstrip("#")
    position_source_id = vertices_to_position.get(vertices_id)
    if not position_source_id:
        raise RuntimeError(f"Could not resolve POSITION source for vertices id '{vertices_id}'")

    pos_accessor = accessor_map.get(position_source_id)
    if not pos_accessor:
        raise RuntimeError(f"Missing accessor for POSITION source '{position_source_id}'")

    float_values = source_float_map.get(pos_accessor["float_array_id"])
    if float_values is None:
        raise RuntimeError(f"Missing float array for POSITION source '{position_source_id}'")

    stride = int(pos_accessor["stride"])
    offset = int(pos_accessor["offset"])

    def xyz_for_vertex_index(vertex_index: int) -> tuple[float, float, float]:
        base = offset + vertex_index * stride
        if base + 2 >= len(float_values):
            raise RuntimeError("Vertex index out of bounds while triangulating polygons")
        return (float_values[base], float_values[base + 1], float_values[base + 2])

    tex_offset: int | None = None
    tex_source_id: str | None = None
    tex_accessor: dict | None = None
    tex_float_values: List[float] | None = None
    tex_count = 0
    tex_stride = 0
    tex_source_elem: ET.Element | None = None
    tex_float_array_elem: ET.Element | None = None
    tex_accessor_elem: ET.Element | None = None
    tex_input_candidates = [inp for inp in input_elems if inp.get("semantic") == "TEXCOORD"]
    if tex_input_candidates:
        tex_input = next((inp for inp in tex_input_candidates if inp.get("set") == "0"), tex_input_candidates[0])
        tex_offset = int(tex_input.get("offset", "0"))
        tex_source_id = tex_input.get("source", "").lstrip("#")
        tex_accessor = accessor_map.get(tex_source_id)
        if tex_accessor:
            tex_float_values = source_float_map.get(tex_accessor["float_array_id"])
            tex_stride = max(1, int(tex_accessor["stride"]))
            tex_count = len(tex_float_values) // tex_stride if tex_float_values is not None else 0
            tex_source_elem = next(
                (s for s in list(mesh) if _local_name(s.tag) == "source" and s.get("id") == tex_source_id),
                None
            )
            if tex_source_elem is not None:
                tex_float_array_elem = _find_child_by_local(tex_source_elem, "float_array")
                tech = _find_child_by_local(tex_source_elem, "technique_common")
                if tech is not None:
                    tex_accessor_elem = _find_child_by_local(tech, "accessor")

    def uv_for_texcoord_index(tex_index: int) -> tuple[float, float]:
        if tex_accessor is None or tex_float_values is None:
            raise RuntimeError("Texture accessor unavailable")
        local_stride = int(tex_accessor["stride"])
        tex_base = int(tex_accessor["offset"]) + tex_index * local_stride
        if tex_base + 1 >= len(tex_float_values):
            raise RuntimeError("Texcoord index out of bounds while triangulating polygons")
        return (float(tex_float_values[tex_base]), float(tex_float_values[tex_base + 1]))

    def append_texcoord_uv(uv: tuple[float, float]) -> int:
        nonlocal tex_count
        if tex_float_values is None or tex_stride <= 0:
            raise RuntimeError("Texture source unavailable for UV append")
        base_index = tex_count
        tex_float_values.extend([0.0] * tex_stride)
        tex_float_values[base_index * tex_stride] = uv[0]
        if tex_stride > 1:
            tex_float_values[base_index * tex_stride + 1] = uv[1]
        tex_count += 1
        return base_index

    all_indices: List[int] = []
    total_triangles = 0

    primitive_nodes = [c for c in list(polygons) if _local_name(c.tag) in ("p", "ph")]
    for primitive in primitive_nodes:
        rings: List[List[List[int]]] = []

        if _local_name(primitive.tag) == "p":
            values = _parse_ints(primitive.text or "")
            if not values:
                continue
            if len(values) % tuple_size != 0:
                raise RuntimeError("Invalid index tuple width in <p> under <polygons>")
            ring = [values[i:i + tuple_size] for i in range(0, len(values), tuple_size)]
            rings.append(ring)
        else:
            outer = _find_child_by_local(primitive, "p")
            if outer is None:
                raise RuntimeError("<ph> missing outer <p>")
            outer_values = _parse_ints(outer.text or "")
            if len(outer_values) % tuple_size != 0:
                raise RuntimeError("Invalid index tuple width in <ph>/<p>")
            outer_ring = [outer_values[i:i + tuple_size] for i in range(0, len(outer_values), tuple_size)]
            rings.append(outer_ring)
            for hole in [c for c in list(primitive) if _local_name(c.tag) == "h"]:
                hole_values = _parse_ints(hole.text or "")
                if len(hole_values) % tuple_size != 0:
                    raise RuntimeError("Invalid index tuple width in <ph>/<h>")
                hole_ring = [hole_values[i:i + tuple_size] for i in range(0, len(hole_values), tuple_size)]
                rings.append(hole_ring)

            # CV-export quirk: hole rings can carry one repeated TEXCOORD index
            # while VERTEX indices vary, causing texture warping around openings.
            # Repair by fitting an affine mapping from outer 3D positions -> outer UV,
            # then synthesize UVs for hole corners and append them to the tex source.
            if tex_offset is not None and tex_count > 0 and rings and rings[0]:
                outer_ring = rings[0]
                outer_positions: List[tuple[float, float, float]] = []
                outer_uvs: List[tuple[float, float]] = []
                for c in outer_ring:
                    if len(c) <= max(vertex_offset, tex_offset):
                        continue
                    try:
                        outer_positions.append(xyz_for_vertex_index(c[vertex_offset]))
                        outer_uvs.append(uv_for_texcoord_index(c[tex_offset]))
                    except Exception:
                        outer_positions = []
                        outer_uvs = []
                        break
                affine = _fit_affine_3d_to_uv(outer_positions, outer_uvs)
                if affine is not None:
                    for hole_ring in rings[1:]:
                        if not hole_ring:
                            continue
                        tex_values = [c[tex_offset] for c in hole_ring if len(c) > tex_offset]
                        vertex_values = [c[vertex_offset] for c in hole_ring if len(c) > vertex_offset]
                        if (
                            tex_values
                            and vertex_values
                            and len(set(tex_values)) == 1
                            and len(set(vertex_values)) > 1
                            and tex_float_values is not None
                        ):
                            for corner in hole_ring:
                                if len(corner) <= max(vertex_offset, tex_offset):
                                    continue
                                pos = xyz_for_vertex_index(corner[vertex_offset])
                                new_uv = _apply_affine_3d_to_uv(affine, pos)
                                corner[tex_offset] = append_texcoord_uv(new_uv)

        if not rings or len(rings[0]) < 3:
            continue

        corner_tuples: List[List[int]] = []
        corner_points: List[tuple[float, float, float]] = []
        corner_uvs: List[tuple[float, float]] = []
        can_use_uv = tex_offset is not None
        for ring in rings:
            for corner in ring:
                corner_tuples.append(corner)
                corner_points.append(xyz_for_vertex_index(corner[vertex_offset]))
                if can_use_uv:
                    try:
                        corner_uvs.append(uv_for_texcoord_index(corner[tex_offset]))  # type: ignore[index]
                    except Exception:
                        can_use_uv = False
                        corner_uvs = []

        tri_corner_indices = _triangulate_rings(
            rings,
            corner_points,
            corner_uvs if can_use_uv else None,
        )
        if len(tri_corner_indices) % 3 != 0:
            raise RuntimeError("Triangulation produced invalid triangle index count")
        total_triangles += len(tri_corner_indices) // 3

        for ci in tri_corner_indices:
            all_indices.extend(corner_tuples[ci])

    if tex_float_values is not None and tex_float_array_elem is not None and tex_accessor_elem is not None and tex_count > 0:
        tex_float_array_elem.text = " ".join(f"{v:.9g}" for v in tex_float_values)
        tex_float_array_elem.set("count", str(len(tex_float_values)))
        tex_accessor_elem.set("count", str(tex_count))

    triangles = ET.Element(_qn(ns, "triangles"), attrib=dict(polygons.attrib))
    triangles.set("count", str(total_triangles))
    for inp in input_elems:
        triangles.append(copy.deepcopy(inp))
    p_node = ET.SubElement(triangles, _qn(ns, "p"))
    p_node.text = " ".join(str(v) for v in all_indices) if all_indices else ""
    return triangles


def _triangulate_collada_polygons(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    ns = _extract_ns(root.tag)
    if ns:
        ET.register_namespace("", ns)

    # Process every mesh independently.
    for geom in root.findall(f".//{_qn(ns, 'geometry')}"):
        mesh = _find_child_by_local(geom, "mesh")
        if mesh is None:
            continue
        source_float_map = _build_source_float_map(mesh)
        accessor_map = _build_accessor_map(mesh)
        vertices_to_position = _resolve_position_accessor(mesh)

        mesh_children = list(mesh)
        replaced_children: List[ET.Element] = []
        for child in mesh_children:
            if _local_name(child.tag) != "polygons":
                replaced_children.append(child)
                continue

            # Only custom-triangulate polygons that include hole primitives.
            # For ordinary polygons, keep source data and let Assimp's own
            # triangulation run to preserve UV behavior.
            has_holes = any(_local_name(c.tag) == "ph" for c in list(child))
            if not has_holes:
                replaced_children.append(child)
                continue

            triangles = _convert_polygons_element_to_triangles(
                child,
                mesh,
                ns,
                source_float_map,
                accessor_map,
                vertices_to_position,
            )
            replaced_children.append(triangles)

        if replaced_children != mesh_children:
            mesh[:] = replaced_children

    return ET.tostring(root, encoding="unicode")


def _prepare_dae_for_assimp(source_dae: Path, cleaned_dae: Path) -> None:
    content = source_dae.read_text(encoding='utf-8', errors='ignore')
    content = _fix_windows_paths(content)
    content = _fix_collada_transparency(content)
    content = _triangulate_collada_polygons(content)
    cleaned_dae.write_text(content, encoding='utf-8')


def _pad4(data: bytes, pad_byte: bytes = b"\x00") -> bytes:
    padding = (-len(data)) % 4
    if padding == 0:
        return data
    return data + (pad_byte * padding)


def _embed_external_images_in_glb(glb_path: Path) -> bool:
    """
    Convert external image URIs in a GLB's JSON chunk into in-file bufferViews.
    Returns True when file was modified.
    """
    glb = glb_path.read_bytes()
    if len(glb) < 20:
        return False

    magic, version, _total_length = struct.unpack_from("<III", glb, 0)
    if magic != 0x46546C67 or version != 2:
        return False

    offset = 12
    json_chunk = None
    bin_chunk = b""
    while offset + 8 <= len(glb):
        chunk_len, chunk_type = struct.unpack_from("<II", glb, offset)
        offset += 8
        chunk_data = glb[offset:offset + chunk_len]
        offset += chunk_len
        if chunk_type == 0x4E4F534A:
            json_chunk = chunk_data
        elif chunk_type == 0x004E4942:
            bin_chunk = chunk_data

    if not json_chunk:
        return False

    gltf = json.loads(json_chunk.decode("utf-8").rstrip(" \t\r\n\0"))
    images = gltf.get("images") or []
    if not images:
        return False

    buffers = gltf.get("buffers")
    if not isinstance(buffers, list) or not buffers:
        buffers = [{"byteLength": len(bin_chunk)}]
        gltf["buffers"] = buffers

    # For GLB, the BIN-backed buffer must be index 0 and must not have "uri".
    if "uri" in buffers[0]:
        del buffers[0]["uri"]
    if len(buffers) > 1:
        buffers = [buffers[0]]
        gltf["buffers"] = buffers

    buffer_views = gltf.get("bufferViews")
    if not isinstance(buffer_views, list):
        buffer_views = []
        gltf["bufferViews"] = buffer_views

    bin_blob = bytes(bin_chunk)
    modified = False
    for image in images:
        uri = image.get("uri")
        if not uri or uri.startswith("data:"):
            continue

        image_path = (glb_path.parent / uri).resolve()
        if not image_path.exists() or not image_path.is_file():
            logger.warning("GLB embed skipped missing texture: %s", image_path)
            continue

        data = image_path.read_bytes()
        byte_offset = len(bin_blob)
        bin_blob += _pad4(data, b"\x00")

        buffer_view_idx = len(buffer_views)
        buffer_views.append({
            "buffer": 0,
            "byteOffset": byte_offset,
            "byteLength": len(data),
        })

        mime_type, _ = mimetypes.guess_type(str(image_path))
        image["bufferView"] = buffer_view_idx
        image["mimeType"] = mime_type or "application/octet-stream"
        image.pop("uri", None)
        modified = True

    if not modified:
        return False

    buffers[0]["byteLength"] = len(bin_blob)

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes = _pad4(json_bytes, b" ")
    bin_blob = _pad4(bin_blob, b"\x00")

    new_chunks = [
        struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes,
        struct.pack("<II", len(bin_blob), 0x004E4942) + bin_blob,
    ]
    total_length = 12 + sum(len(c) for c in new_chunks)
    rebuilt = struct.pack("<III", 0x46546C67, 2, total_length) + b"".join(new_chunks)

    tmp_out = glb_path.with_suffix(".tmp.glb")
    tmp_out.write_bytes(rebuilt)
    os.replace(tmp_out, glb_path)
    return True


def _run_assimp_export(
    assimp_exe: str,
    room_dir: Path,
    tmp_dae: Path,
    tmp_glb: Path,
    extra_flags: List[str],
) -> subprocess.CompletedProcess:
    command = [
        assimp_exe,
        'export',
        f'.\\{tmp_dae.name}',
        f'.\\{tmp_glb.name}',
        'glb2',
        *extra_flags,
    ]
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        # Prevent console pop-ups for each Assimp conversion on Windows.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    return subprocess.run(
        command,
        cwd=str(room_dir),
        capture_output=True,
        text=True,
        check=False,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def _convert_with_assimp(dae_path: Path, glb_path: Path, assimp_exe: str) -> None:
    room_dir = dae_path.parent
    run_id = uuid4().hex
    tmp_dae = room_dir / f'.tmp_assimp_input.{run_id}.dae'
    tmp_glb = room_dir / f'.tmp_assimp_output.{run_id}.glb'
    try:
        _prepare_dae_for_assimp(dae_path, tmp_dae)
        flags = ['-tri', '-gn', '-jiv', '-et', '-emb']
        result = _run_assimp_export(assimp_exe, room_dir, tmp_dae, tmp_glb, flags)

        if result.returncode != 0 or not tmp_glb.exists():
            stdout = (result.stdout or '').strip()
            stderr = (result.stderr or '').strip()
            error_text = f"{stdout}\n{stderr}".lower()
            if "failed to loa" in error_text and "-emb" in flags:
                logger.warning(
                    "Assimp export with -emb failed for %s; retrying without embedded texture export.",
                    dae_path
                )
                result = _run_assimp_export(
                    assimp_exe,
                    room_dir,
                    tmp_dae,
                    tmp_glb,
                    ['-tri', '-gn', '-jiv', '-et'],
                )

        if result.returncode != 0 or not tmp_glb.exists():
            stderr = (result.stderr or '').strip()
            stdout = (result.stdout or '').strip()
            raise RuntimeError(
                f"Assimp export failed rc={result.returncode} stderr={stderr[:1000]} stdout={stdout[:1200]}"
            )
        try:
            _embed_external_images_in_glb(tmp_glb)
        except Exception as embed_exc:
            logger.warning("Could not embed external textures in GLB: %s", embed_exc)
        os.replace(tmp_glb, glb_path)
    finally:
        if tmp_dae.exists():
            try:
                tmp_dae.unlink()
            except OSError:
                pass
        if tmp_glb.exists():
            try:
                tmp_glb.unlink()
            except OSError:
                pass


def convert_dae_to_medium_glb(dae_path: Path, assimp_path: str | None = None) -> Path:
    """
    Convert DAE to *_medium.glb using Assimp.
    Skips when the existing output is newer than the DAE.
    Raises RuntimeError on conversion failure.
    """
    glb_path = dae_path.with_name("3d_medium.glb")
    if glb_path.exists() and glb_path.stat().st_mtime >= dae_path.stat().st_mtime:
        logger.debug(f"Medium GLB up-to-date, skipping: {glb_path.name}")
        return glb_path

    if not _wait_for_stable_file(dae_path):
        raise RuntimeError(f"DAE did not stabilize before conversion timeout: {dae_path}")

    assimp_exe = _find_assimp_exe(assimp_path)
    if not assimp_exe:
        raise RuntimeError(
            "Assimp executable not found. Configure ASSIMP_PATH or install assimp.exe."
        )

    _convert_with_assimp(dae_path, glb_path, assimp_exe)
    logger.info(
        "Converted %s/3d.dae -> %s via Assimp (%s)",
        dae_path.parent.name,
        glb_path.name,
        assimp_exe
    )
    return glb_path


def scan_root_for_missing_glbs(root_dir: str) -> int:
    """
    Walk every job folder under root_dir and convert any 3D/<ROOM>/3d.dae
    that is missing its 3d_medium.glb.  Skips rooms where the GLB already
    exists (even if stale — stale reconversion is left to the per-event path).
    Returns total number of files converted.
    """
    root = Path(root_dir)
    total = 0
    try:
        job_dirs = [e for e in root.iterdir() if e.is_dir()]
    except OSError as e:
        logger.error(f"Cannot scan root dir for missing GLBs: {e}")
        return 0

    missing: List[Path] = []
    for job_dir in job_dirs:
        three_d = job_dir / '3D'
        if not three_d.is_dir():
            continue
        try:
            for room_dir in three_d.iterdir():
                if not room_dir.is_dir():
                    continue
                dae = _find_room_dae(room_dir)
                glb = room_dir / '3d_medium.glb'
                if dae is not None and not glb.exists():
                    missing.append(dae)
        except OSError as e:
            logger.warning(f"Cannot scan {three_d}: {e}")

    if not missing:
        logger.info("Startup GLB check: no missing GLB files found.")
        return 0

    logger.info(f"Startup GLB check: {len(missing)} missing GLB(s) — converting...")
    for dae in missing:
        try:
            convert_dae_to_medium_glb(dae)
            total += 1
        except RuntimeError as e:
            logger.error(str(e))

    logger.info(f"Startup GLB check complete: converted {total}/{len(missing)}.")
    return total


def convert_3d_models_for_job(job_folder_path: str) -> int:
    """
    Convert all 3D/<ROOM>/3d.dae files in a job folder to 3d_medium.glb.
    Skips rooms where the _medium.glb is already up-to-date.
    Returns the number of files newly converted.
    """
    three_d_dir = Path(job_folder_path) / '3D'
    if not three_d_dir.is_dir():
        return 0

    dae_files: List[Path] = []
    try:
        for room_dir in three_d_dir.iterdir():
            if not room_dir.is_dir():
                continue
            dae = _find_room_dae(room_dir)
            if dae is not None:
                dae_files.append(dae)
    except OSError as e:
        logger.warning(f"Could not scan 3D directory {three_d_dir}: {e}")
        return 0

    converted = 0
    for dae in dae_files:
        try:
            glb = dae.with_name('3d_medium.glb')
            if glb.exists() and glb.stat().st_mtime >= dae.stat().st_mtime:
                continue
            convert_dae_to_medium_glb(dae)
            converted += 1
        except RuntimeError as e:
            logger.error(str(e))

    return converted
