from pathlib import Path
import json
import os
import struct
import time

import pytest

import ready_jobs_watcher.dae_converter as dae_converter


def test_fix_windows_paths_normalizes_backslashes():
    src = "<init_from>images\\wood.jpg</init_from>"
    out = dae_converter._fix_windows_paths(src)
    assert out == "<init_from>images/wood.jpg</init_from>"


def test_convert_requires_assimp_when_not_found(tmp_path, monkeypatch):
    dae = tmp_path / "3d.dae"
    dae.write_text("<COLLADA/>", encoding="utf-8")

    monkeypatch.setattr(dae_converter, "_wait_for_stable_file", lambda _p: True)
    monkeypatch.setattr(dae_converter, "_find_assimp_exe", lambda _configured=None: None)

    with pytest.raises(RuntimeError, match="Assimp executable not found"):
        dae_converter.convert_dae_to_medium_glb(dae)


def test_convert_preserves_existing_glb_on_failure(tmp_path, monkeypatch):
    glb = tmp_path / "3d_medium.glb"
    glb.write_bytes(b"existing")
    dae = tmp_path / "3d.dae"
    dae.write_text("<COLLADA/>", encoding="utf-8")
    future = time.time() + 2
    os.utime(dae, (future, future))

    monkeypatch.setattr(dae_converter, "_wait_for_stable_file", lambda _p: True)
    monkeypatch.setattr(dae_converter, "_find_assimp_exe", lambda _configured=None: r"C:\fake\assimp.exe")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("assimp failed")

    monkeypatch.setattr(dae_converter, "_convert_with_assimp", _boom)

    with pytest.raises(RuntimeError, match="assimp failed"):
        dae_converter.convert_dae_to_medium_glb(dae)

    assert glb.exists()
    assert glb.read_bytes() == b"existing"


def test_convert_success_path_uses_assimp(tmp_path, monkeypatch):
    dae = tmp_path / "3d.dae"
    dae.write_text("<COLLADA/>", encoding="utf-8")
    glb = tmp_path / "3d_medium.glb"

    monkeypatch.setattr(dae_converter, "_wait_for_stable_file", lambda _p: True)
    monkeypatch.setattr(dae_converter, "_find_assimp_exe", lambda _configured=None: r"C:\fake\assimp.exe")

    called = {"value": False}

    def _ok(_dae: Path, out_glb: Path, _assimp: str):
        called["value"] = True
        out_glb.write_bytes(b"ok")

    monkeypatch.setattr(dae_converter, "_convert_with_assimp", _ok)

    out = dae_converter.convert_dae_to_medium_glb(dae)
    assert called["value"] is True
    assert out == glb
    assert glb.exists()


def test_find_room_dae_handles_uppercase_name(tmp_path):
    room = tmp_path / "KITCHEN"
    room.mkdir()
    dae_upper = room / "3D.dae"
    dae_upper.write_text("<COLLADA/>", encoding="utf-8")

    found = dae_converter._find_room_dae(room)
    assert found is not None
    assert found.name == "3D.dae"


def test_triangulate_collada_polygons_converts_ph_with_holes():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_geometries>
    <geometry id="g">
      <mesh>
        <source id="pos">
          <float_array id="pos-array" count="24">
            0 0 0  10 0 0  10 10 0  0 10 0
            3 3 0  7 3 0  7 7 0  3 7 0
          </float_array>
          <technique_common>
            <accessor source="#pos-array" count="8" stride="3"/>
          </technique_common>
        </source>
        <vertices id="vtx">
          <input semantic="POSITION" source="#pos"/>
        </vertices>
        <polygons material="m" count="1">
          <input semantic="VERTEX" source="#vtx" offset="0"/>
          <ph>
            <p>0 1 2 3</p>
            <h>4 5 6 7</h>
          </ph>
        </polygons>
      </mesh>
    </geometry>
  </library_geometries>
</COLLADA>
"""
    out = dae_converter._triangulate_collada_polygons(xml)
    assert "<polygons" not in out
    assert "triangles" in out
    assert "count=" in out


def test_triangulate_collada_polygons_keeps_plain_polygons_for_assimp():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_geometries>
    <geometry id="g">
      <mesh>
        <source id="pos">
          <float_array id="pos-array" count="12">0 0 0  1 0 0  1 1 0  0 1 0</float_array>
          <technique_common>
            <accessor source="#pos-array" count="4" stride="3"/>
          </technique_common>
        </source>
        <vertices id="vtx">
          <input semantic="POSITION" source="#pos"/>
        </vertices>
        <polygons material="m" count="1">
          <input semantic="VERTEX" source="#vtx" offset="0"/>
          <p>0 1 2 3</p>
        </polygons>
      </mesh>
    </geometry>
  </library_geometries>
</COLLADA>
"""
    out = dae_converter._triangulate_collada_polygons(xml)
    assert "<polygons" in out
    assert "<triangles" not in out


def test_triangulate_rings_falls_back_when_uv_degenerate():
    # Outer square + inner square hole.
    rings = [
        [[0], [1], [2], [3]],
        [[4], [5], [6], [7]],
    ]
    points3d = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, 0.0),
        (10.0, 10.0, 0.0),
        (0.0, 10.0, 0.0),
        (3.0, 3.0, 0.0),
        (7.0, 3.0, 0.0),
        (7.0, 7.0, 0.0),
        (3.0, 7.0, 0.0),
    ]
    # Degenerate UVs (all identical) should be rejected and fallback to 3D projection.
    bad_uvs = [(0.5, 0.5)] * 8
    tri = dae_converter._triangulate_rings(rings, points3d, bad_uvs)
    assert len(tri) > 0
    assert len(tri) % 3 == 0


def test_hole_texcoord_repair_remaps_constant_hole_uv_to_vertex_index():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_geometries>
    <geometry id="g">
      <mesh>
        <source id="pos">
          <float_array id="pos-array" count="24">
            0 0 0 10 0 0 10 10 0 0 10 0
            3 3 0 7 3 0 7 7 0 3 7 0
          </float_array>
          <technique_common><accessor source="#pos-array" count="8" stride="3"/></technique_common>
        </source>
        <source id="uv">
          <float_array id="uv-array" count="16">
            0 0 1 0 1 1 0 1
            0.3 0.3 0.7 0.3 0.7 0.7 0.3 0.7
          </float_array>
          <technique_common><accessor source="#uv-array" count="8" stride="2"/></technique_common>
        </source>
        <vertices id="vtx">
          <input semantic="POSITION" source="#pos"/>
        </vertices>
        <polygons material="m" count="1">
          <input semantic="VERTEX" source="#vtx" offset="0"/>
          <input semantic="TEXCOORD" source="#uv" offset="1" set="0"/>
          <ph>
            <p>0 0 1 1 2 2 3 3</p>
            <h>4 0 5 0 6 0 7 0</h>
          </ph>
        </polygons>
      </mesh>
    </geometry>
  </library_geometries>
</COLLADA>
"""
    out = dae_converter._triangulate_collada_polygons(xml)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(out)
    ns = "{http://www.collada.org/2005/11/COLLADASchema}"
    uv_float = root.find(f".//{ns}source[@id='uv']/{ns}float_array")
    assert uv_float is not None
    # Original had 16 floats (8 uv pairs). Repair should append more.
    assert int(uv_float.get("count", "0")) > 16


def test_embed_external_images_in_glb_rewrites_uri_to_bufferview(tmp_path):
    tex = tmp_path / "images" / "wood.jpg"
    tex.parent.mkdir(parents=True, exist_ok=True)
    tex.write_bytes(b"\xff\xd8\xff\xd9")  # tiny jpeg marker bytes

    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 0}],
        "images": [{"uri": "images/wood.jpg"}],
        "textures": [{"source": 0}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
    }
    json_chunk = json.dumps(gltf).encode("utf-8")
    json_chunk += b" " * ((4 - (len(json_chunk) % 4)) % 4)
    glb_body = struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    total_len = 12 + len(glb_body)
    glb = struct.pack("<III", 0x46546C67, 2, total_len) + glb_body

    glb_path = tmp_path / "3d_medium.glb"
    glb_path.write_bytes(glb)

    changed = dae_converter._embed_external_images_in_glb(glb_path)
    assert changed is True

    out = glb_path.read_bytes()
    _, _, _ = struct.unpack_from("<III", out, 0)
    chunk_len, chunk_type = struct.unpack_from("<II", out, 12)
    assert chunk_type == 0x4E4F534A
    js = out[20:20 + chunk_len].decode("utf-8").rstrip(" \t\r\n\0")
    parsed = json.loads(js)

    assert "uri" not in parsed["images"][0]
    assert isinstance(parsed["images"][0].get("bufferView"), int)
    assert parsed["images"][0].get("mimeType") == "image/jpeg"
    assert parsed["buffers"][0]["byteLength"] >= 4
