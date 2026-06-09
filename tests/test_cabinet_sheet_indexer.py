"""Tests for cabinet_sheet_indexer.py — marker-based and legacy heuristic parsing."""
from __future__ import annotations

import sys
import os
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ready_jobs_watcher.cabinet_sheet_indexer import (
    _extract_marker_cabinets,
    _extract_marker_wall,
    _extract_section_name,
    _parse_assembly_parts_for_page,
    _try_parse_with_markers,
    _parse_assembly_pdf,
    _parse_plans_pdf,
    _parse_plans_table,
    _DocumentParseResult,
    build_reference_index_for_job,
    REFERENCE_INDEX_FILENAME,
)


# ---------------------------------------------------------------------------
# Unit tests for individual helper functions
# ---------------------------------------------------------------------------

class TestExtractMarkerCabinets(unittest.TestCase):

    def test_single_cab_marker(self):
        text = "Some header text\n||CAB:42||\nMore text"
        self.assertEqual(_extract_marker_cabinets(text), {"42"})

    def test_multiple_cab_markers_same_page(self):
        text = "||CAB:5||  ||CAB:6||  ||CAB:100||"
        self.assertEqual(_extract_marker_cabinets(text), {"5", "6", "100"})

    def test_case_insensitive(self):
        text = "||cab:7||"
        self.assertEqual(_extract_marker_cabinets(text), {"7"})

    def test_no_markers_returns_empty(self):
        text = "Assembly # 42\nSome other text"
        self.assertEqual(_extract_marker_cabinets(text), set())

    def test_4_digit_cabinet(self):
        text = "||CAB:1234||"
        self.assertEqual(_extract_marker_cabinets(text), {"1234"})

    def test_5_digit_not_matched(self):
        # Pattern only allows 1-4 digits
        text = "||CAB:12345||"
        self.assertEqual(_extract_marker_cabinets(text), set())

    def test_partial_pipe_not_matched(self):
        text = "|CAB:42|"
        self.assertEqual(_extract_marker_cabinets(text), set())


class TestExtractMarkerWall(unittest.TestCase):

    def test_room_and_wall(self):
        text = "||WALL:Room 1 - Wall A||"
        room, wall = _extract_marker_wall(text)
        self.assertEqual(room, "Room 1")
        self.assertEqual(wall, "Wall A")

    def test_wall_only_no_separator(self):
        text = "||WALL:Wall B||"
        room, wall = _extract_marker_wall(text)
        self.assertIsNone(room)
        self.assertEqual(wall, "Wall B")

    def test_no_wall_marker(self):
        text = "||CAB:42||"
        room, wall = _extract_marker_wall(text)
        self.assertIsNone(room)
        self.assertIsNone(wall)

    def test_complex_room_name(self):
        text = "||WALL:Master Bath - Wall #3||"
        room, wall = _extract_marker_wall(text)
        self.assertEqual(room, "Master Bath")
        self.assertEqual(wall, "Wall #3")

    def test_extra_whitespace_stripped(self):
        text = "||WALL:  Room 2  -  Wall C  ||"
        room, wall = _extract_marker_wall(text)
        self.assertEqual(room, "Room 2")
        self.assertEqual(wall, "Wall C")


# ---------------------------------------------------------------------------
# Tests for _try_parse_with_markers using mock fitz.Document
# ---------------------------------------------------------------------------

def _make_mock_doc(pages_text: list[str]) -> MagicMock:
    """Build a mock fitz.Document whose pages return the given text strings."""
    doc = MagicMock()
    doc.page_count = len(pages_text)
    mock_pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text.return_value = text
        mock_pages.append(page)
    doc.__getitem__ = lambda self, idx: mock_pages[idx]
    return doc


class TestParsePlansTable(unittest.TestCase):

    HEADER = (
        "| # | | Unit Name |\n"
        "| Width |\n"
        "| Height |\n"
        "| Depth |\n"
        "| L.SCR | | R.SCR |\n"
    )

    def _lines(self, text: str) -> list:
        return [l.strip() for l in text.splitlines() if l.strip()]

    def test_basic_extraction(self):
        text = self.HEADER + "1\nStd Tall\n29\n95.5\n24\n0.5\n0.5\n"
        result = _parse_plans_table(self._lines(text))
        self.assertEqual(set(result.keys()), {"1"})
        self.assertEqual(result["1"], "Std Tall")

    def test_multiple_cabinets(self):
        text = (
            self.HEADER
            + "1\nStd Tall\n29\n95.5\n24\n0.5\n0.5\n"
            + "2\nRefrigerator\n40\n95.5\n26\n0\n0\n"
            + "3\nBase Cabinet\n74.5\n35.25\n24\n0.5\n2.375\n"
        )
        result = _parse_plans_table(self._lines(text))
        self.assertEqual(set(result.keys()), {"1", "2", "3"})
        self.assertEqual(result["2"], "Refrigerator")

    def test_zero_cabinet_skipped(self):
        text = self.HEADER + "0\nER-1\n30\n35.953\n28.2923\n0\n0\n5\nBase Cabinet\n52\n35.25\n24\n0.5\n0.5\n"
        result = _parse_plans_table(self._lines(text))
        self.assertIn("5", result)
        self.assertNotIn("0", result)

    def test_no_table_returns_empty(self):
        text = "Some random text\nNo table here\n42\nCabinet\n30\n36\n24\n0\n0\n"
        self.assertEqual(_parse_plans_table(self._lines(text)), {})

    def test_unit_name_on_same_line_as_hash(self):
        # Both | # | and | Unit Name | on the same line (as Cabinet Vision emits them)
        text = (
            "| # | | Unit Name |\n| Width |\n| Height |\n| Depth |\n| L.SCR | | R.SCR |\n"
            "10\nBase Cabinet\n60\n35.25\n24\n0\n0.5\n"
        )
        result = _parse_plans_table(self._lines(text))
        self.assertIn("10", result)
        self.assertEqual(result["10"], "Base Cabinet")

    def test_stops_at_pipe_line_after_data(self):
        # After data rows, a pipe line terminates extraction
        text = (
            self.HEADER
            + "7\nStd Tall\n30\n95.5\n26\n0\n0\n"
            + "| Room #1 - (KITCHEN) |\n"
            + "8\nStd Upper\n50\n41\n12\n0.5\n0\n"  # should not be parsed
        )
        result = _parse_plans_table(self._lines(text))
        self.assertIn("7", result)
        # Cabinet 8 appears after a pipe line — parser already consumed 7 lines for cab 7
        # and then hits "| Room #1 - |" which is not a digit, so stops

    def test_real_world_wall3_page(self):
        # Mimics actual Plans & Elevations Wall #3 page
        text = (
            "FULL OVERLAY\n| Wall #3 |\n| Date: 05/06/26 |\n571\n"
            "| # | | Unit Name |\n| Width |\n| Height |\n| Depth |\n| L.SCR | | R.SCR |\n"
            "0\nER-1\n30\n35.953\n28.2923\n0\n0\n"
            "0\nIH-2\n30\n37\n19.6656\n0\n0\n"
            "5\nBase Cabinet\n52\n35.25\n24\n0.5\n0.5\n"
            "6\nBase Cabinet\n37\n35.25\n24\n0.5\n0.5\n"
            "7\nStd Tall\n30\n95.5\n26\n0\n0\n"
            "8\nStd Upper\n50\n41\n12\n0.5\n0\n"
            "9\nStd Upper\n35\n41\n12\n0\n0.5\n"
        )
        result = _parse_plans_table(self._lines(text))
        self.assertEqual(set(result.keys()), {"5", "6", "7", "8", "9"})
        self.assertNotIn("0", result)

    def test_unit_names_captured(self):
        # Unit names must be preserved so rule scanner can match "hood", "wood top" etc.
        text = (
            self.HEADER
            + "5\nRange Hood\n36\n18\n12\n0\n0\n"
            + "6\nWood Top\n60\n3\n26\n0\n0\n"
            + "7\nFloating Shelf\n48\n1.5\n12\n0\n0\n"
        )
        result = _parse_plans_table(self._lines(text))
        self.assertEqual(result["5"], "Range Hood")
        self.assertEqual(result["6"], "Wood Top")
        self.assertEqual(result["7"], "Floating Shelf")


class TestTryParseWithMarkers(unittest.TestCase):

    def test_no_markers_returns_none(self):
        doc = _make_mock_doc(["Assembly # 42\nSome text", "More text"])
        self.assertIsNone(_try_parse_with_markers(doc))

    def test_single_page_single_cab(self):
        doc = _make_mock_doc(["||CAB:42||"])
        result = _try_parse_with_markers(doc)
        self.assertIsNotNone(result)
        self.assertEqual(result.cabinet_to_pages, {"42": [1]})
        self.assertEqual(result.page_details["1"]["cabinets"], ["42"])
        self.assertIsNone(result.page_details["1"]["room"])
        self.assertIsNone(result.page_details["1"]["wall"])

    def test_multiple_cabinets_same_page(self):
        doc = _make_mock_doc(["||CAB:5||  ||CAB:6||  ||WALL:Room 1 - Wall A||"])
        result = _try_parse_with_markers(doc)
        self.assertIsNotNone(result)
        self.assertIn(1, result.cabinet_to_pages["5"])
        self.assertIn(1, result.cabinet_to_pages["6"])
        detail = result.page_details["1"]
        self.assertEqual(sorted(detail["cabinets"]), ["5", "6"])
        self.assertEqual(detail["room"], "Room 1")
        self.assertEqual(detail["wall"], "Wall A")

    def test_cabinet_spans_multiple_pages(self):
        doc = _make_mock_doc([
            "||CAB:42||  ||WALL:Room 1 - Wall A||",
            "||CAB:42||  ||WALL:Room 1 - Wall B||",
            "||CAB:43||",
        ])
        result = _try_parse_with_markers(doc)
        self.assertEqual(result.cabinet_to_pages["42"], [1, 2])
        self.assertEqual(result.cabinet_to_pages["43"], [3])
        self.assertEqual(result.page_details["1"]["wall"], "Wall A")
        self.assertEqual(result.page_details["2"]["wall"], "Wall B")

    def test_pages_without_markers_not_in_page_details(self):
        # Page 2 has no markers — should not appear in page_details
        doc = _make_mock_doc([
            "||CAB:10||",
            "No markers here",
            "||CAB:11||",
        ])
        result = _try_parse_with_markers(doc)
        self.assertIn("1", result.page_details)
        self.assertNotIn("2", result.page_details)
        self.assertIn("3", result.page_details)

    def test_cabinet_to_pages_sorted(self):
        doc = _make_mock_doc([
            "||CAB:7||",
            "||CAB:7||",
            "||CAB:7||",
        ])
        result = _try_parse_with_markers(doc)
        self.assertEqual(result.cabinet_to_pages["7"], [1, 2, 3])

    def test_page_details_cabinets_sorted_numerically(self):
        doc = _make_mock_doc(["||CAB:100|| ||CAB:2|| ||CAB:20||"])
        result = _try_parse_with_markers(doc)
        # Should sort as integers: 2, 20, 100
        self.assertEqual(result.page_details["1"]["cabinets"], ["2", "20", "100"])

    def test_cabinetToPages_consistent_with_page_details(self):
        doc = _make_mock_doc([
            "||CAB:1||",
            "||CAB:1|| ||CAB:2||",
            "||CAB:2||",
        ])
        result = _try_parse_with_markers(doc)
        # Verify forward and inverse mappings are consistent
        for page_str, detail in result.page_details.items():
            page_num = int(page_str)
            for cab in detail["cabinets"]:
                self.assertIn(page_num, result.cabinet_to_pages[cab],
                              f"Page {page_num} missing from cabinetToPages[{cab}]")
        for cab, pages in result.cabinet_to_pages.items():
            for page in pages:
                self.assertIn(cab, result.page_details[str(page)]["cabinets"],
                              f"Cabinet {cab} missing from pageDetails[{page}]")


# ---------------------------------------------------------------------------
# Tests for the full parse functions using a temporary PDF-less approach
# (mock fitz.open so no real PDF needed)
# ---------------------------------------------------------------------------

class TestParseAssemblyPdfWithMarkers(unittest.TestCase):

    def _run(self, pages_text: list[str]) -> _DocumentParseResult:
        doc = _make_mock_doc(pages_text)
        with patch("ready_jobs_watcher.cabinet_sheet_indexer.fitz.open", return_value=doc):
            doc.__enter__ = lambda s: s
            doc.__exit__ = MagicMock(return_value=False)
            doc.close = MagicMock()
            return _parse_assembly_pdf("fake_path.pdf")

    def test_marker_path_used_when_markers_present(self):
        result = self._run(["||CAB:42||\n||WALL:Room 1 - Wall A||"])
        self.assertEqual(result.cabinet_to_pages, {"42": [1]})
        self.assertEqual(result.page_details["1"]["room"], "Room 1")
        self.assertEqual(result.page_details["1"]["wall"], "Wall A")

    def test_fallback_to_legacy_when_no_markers(self):
        result = self._run(["Assembly # 5\nSome text"])
        self.assertEqual(result.cabinet_to_pages, {"5": [1]})
        # page_details populated even in fallback path; no room/wall in this text
        self.assertIn("1", result.page_details)
        self.assertEqual(result.page_details["1"]["cabinets"], ["5"])
        self.assertIsNone(result.page_details["1"]["room"])
        self.assertIsNone(result.page_details["1"]["wall"])

    def test_fallback_populates_page_details_with_room_wall(self):
        # Assembly format: cabinet number + combined room/wall on same line
        pages = [
            "Assembly # 3\nRoom #1 (KITCHEN) - Wall #2\nSome detail",
            "Assembly # 4\nRoom #2 (BATH) - Wall #1\n",
        ]
        result = self._run(pages)
        self.assertEqual(result.page_details["1"]["cabinets"], ["3"])
        self.assertEqual(result.page_details["1"]["room"], "Room #1 (KITCHEN)")
        self.assertEqual(result.page_details["1"]["wall"], "Wall #2")
        self.assertEqual(result.page_details["2"]["cabinets"], ["4"])
        self.assertEqual(result.page_details["2"]["room"], "Room #2 (BATH)")
        self.assertEqual(result.page_details["2"]["wall"], "Wall #1")

    def test_fallback_carry_over(self):
        pages = [
            "Assembly # 10\nRoom #1 (Bath) - Wall #1",
            "Room #1 (Bath) - Wall #1\nMore detail",
        ]
        result = self._run(pages)
        self.assertIn(1, result.cabinet_to_pages.get("10", []))
        self.assertIn(2, result.cabinet_to_pages.get("10", []))

    def test_mixed_doc_uses_markers_not_legacy(self):
        # If even one page has markers, use marker path for the whole doc
        pages = [
            "Assembly # 99",   # legacy text, but no ||CAB||
            "||CAB:1||",       # marker
        ]
        result = self._run(pages)
        # Only cab 1 should be found (from marker on page 2)
        # Legacy "Assembly # 99" on page 1 is not used
        self.assertEqual(result.cabinet_to_pages, {"1": [2]})
        self.assertNotIn("99", result.cabinet_to_pages)


class TestParsePlansPdfWithMarkers(unittest.TestCase):

    def _run(self, pages_text: list[str]) -> _DocumentParseResult:
        doc = _make_mock_doc(pages_text)
        with patch("ready_jobs_watcher.cabinet_sheet_indexer.fitz.open", return_value=doc):
            doc.__enter__ = lambda s: s
            doc.__exit__ = MagicMock(return_value=False)
            doc.close = MagicMock()
            return _parse_plans_pdf("fake_path.pdf")

    def test_marker_path(self):
        result = self._run(["||CAB:7||  ||WALL:Kitchen - Wall 2||"])
        self.assertEqual(result.cabinet_to_pages, {"7": [1]})
        self.assertEqual(result.page_details["1"]["room"], "Kitchen")
        self.assertEqual(result.page_details["1"]["wall"], "Wall 2")

    def test_fallback_pipe_table(self):
        # Simulate the Cabinet Vision pipe-delimited table format
        page_text = (
            "| # | | Unit Name |\n"
            "| Width |\n"
            "| Height |\n"
            "| Depth |\n"
            "| L.SCR | | R.SCR |\n"
            "5\nBase Cabinet\n52\n35.25\n24\n0.5\n0.5\n"
            "6\nBase Cabinet\n37\n35.25\n24\n0.5\n0.5\n"
            "0\nER-1\n30\n35.953\n28.2923\n0\n0\n"  # appliance placeholder — should be skipped
        )
        result = self._run([page_text])
        self.assertIn("5", result.cabinet_to_pages)
        self.assertIn("6", result.cabinet_to_pages)
        self.assertNotIn("0", result.cabinet_to_pages)
        # page_details populated with cabinetNames; no room/wall in this text
        self.assertIn("1", result.page_details)
        self.assertIsNone(result.page_details["1"]["room"])
        self.assertIsNone(result.page_details["1"]["wall"])
        self.assertEqual(result.page_details["1"]["cabinetNames"]["5"], "Base Cabinet")
        self.assertEqual(result.page_details["1"]["cabinetNames"]["6"], "Base Cabinet")

    def test_fallback_plans_populates_page_details_with_room_wall(self):
        # Plans format: wall and room as separate pipe-delimited fields
        page_text = (
            "| Wall #2 |\n"
            "| Room #1 -  (KITCHEN) |\n"
            "| # | | Unit Name |\n"
            "| Width |\n"
            "| Height |\n"
            "| Depth |\n"
            "| L.SCR | | R.SCR |\n"
            "5\nRange Hood\n52\n35.25\n24\n0.5\n0.5\n"
        )
        result = self._run([page_text])
        self.assertIn("5", result.cabinet_to_pages)
        self.assertIn("1", result.page_details)
        self.assertEqual(result.page_details["1"]["room"], "Room #1 (KITCHEN)")
        self.assertEqual(result.page_details["1"]["wall"], "Wall #2")
        self.assertEqual(result.page_details["1"]["cabinetNames"]["5"], "Range Hood")


# ---------------------------------------------------------------------------
# Integration-style test: build_reference_index_for_job writes correct JSON
# ---------------------------------------------------------------------------

class TestBuildReferenceIndexForJob(unittest.TestCase):

    def test_writes_index_with_page_details(self):
        assembly_pages = [
            "||CAB:1||  ||WALL:Room A - Wall 1||",
            "||CAB:1||  ||WALL:Room A - Wall 2||",
            "||CAB:2||",
        ]
        plans_pages = [
            "||CAB:1|| ||CAB:2||",
        ]

        assembly_doc = _make_mock_doc(assembly_pages)
        plans_doc = _make_mock_doc(plans_pages)
        assembly_doc.close = MagicMock()
        plans_doc.close = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake PDF files so the file-finder works
            assembly_pdf = os.path.join(tmpdir, "12345 - Assembly Sheets.pdf")
            plans_pdf = os.path.join(tmpdir, "12345 - Plans & Elevations.pdf")
            open(assembly_pdf, "wb").close()
            open(plans_pdf, "wb").close()

            def fake_open(path):
                if "Assembly" in path:
                    return assembly_doc
                return plans_doc

            with patch("ready_jobs_watcher.cabinet_sheet_indexer.fitz.open", side_effect=fake_open):
                assembly_doc.__enter__ = lambda s: s
                assembly_doc.__exit__ = MagicMock(return_value=False)
                plans_doc.__enter__ = lambda s: s
                plans_doc.__exit__ = MagicMock(return_value=False)
                result = build_reference_index_for_job(tmpdir)

            self.assertTrue(result)
            index_path = os.path.join(tmpdir, ".metadata", REFERENCE_INDEX_FILENAME)
            self.assertTrue(os.path.isfile(index_path))

            with open(index_path, encoding="utf-8") as f:
                data = json.load(f)

            asm = data["documents"]["assembly"]
            self.assertEqual(asm["cabinetToPages"]["1"], [1, 2])
            self.assertEqual(asm["cabinetToPages"]["2"], [3])
            self.assertEqual(asm["pageDetails"]["1"]["room"], "Room A")
            self.assertEqual(asm["pageDetails"]["1"]["wall"], "Wall 1")
            self.assertEqual(asm["pageDetails"]["2"]["wall"], "Wall 2")
            # page 3 has a CAB marker but no WALL marker — still in pageDetails, room/wall are None
            self.assertIn("3", asm["pageDetails"])
            self.assertEqual(asm["pageDetails"]["3"]["cabinets"], ["2"])
            self.assertIsNone(asm["pageDetails"]["3"]["room"])
            self.assertIsNone(asm["pageDetails"]["3"]["wall"])

            pe = data["documents"]["plansElevations"]
            # Both cabinets appear on page 1 of plans PDF
            self.assertIn(1, pe["cabinetToPages"]["1"])
            self.assertIn(1, pe["cabinetToPages"]["2"])

    def test_returns_false_for_missing_dir(self):
        self.assertFalse(build_reference_index_for_job("/nonexistent/path"))

    def test_returns_false_when_no_reference_pdfs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # No PDFs with matching names
            open(os.path.join(tmpdir, "12345 - Some Other File.pdf"), "wb").close()
            self.assertFalse(build_reference_index_for_job(tmpdir))


# ---------------------------------------------------------------------------
# Tests for assembly sheet part extraction
# ---------------------------------------------------------------------------

class TestParseAssemblyPartsForPage(unittest.TestCase):

    def test_parse_parts_single_section(self):
        text = (
            "| Frame |\n"
            "1\n1.25\n25.5\nBottom Rail\n3/4 Paint Grade Wood\n"
            "1\n1.5\n92\nRight Stile\n3/4 Paint Grade Wood\n"
            "1\n3\n25.5\nTop Rail\n3/4 Paint Grade Wood\n"
        )
        parts = _parse_assembly_parts_for_page(text)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0]["description"], "Bottom Rail")
        self.assertEqual(parts[0]["qty"], 1)
        self.assertAlmostEqual(parts[0]["width"], 1.25)
        self.assertAlmostEqual(parts[0]["length"], 25.5)
        self.assertEqual(parts[0]["sectionType"], "Frame")
        self.assertFalse(parts[0]["isPurchased"])

    def test_parse_parts_multiple_sections(self):
        text = (
            "| Frame |\n"
            "1\n1.25\n25.5\nBottom Rail\n3/4 Paint Grade Wood\n"
            "| Panel Stock |\n"
            "1\n23.01\n91.011\nLeft End\n3/4 Prefinished 19mm\n"
            "| Hardware |\n"
            "4\n0.25\n4.8\nDoor Pull\nChrome 4in C-Pull\n"
        )
        parts = _parse_assembly_parts_for_page(text)
        self.assertEqual(len(parts), 3)
        sections = [p["sectionType"] for p in parts]
        self.assertIn("Frame", sections)
        self.assertIn("Panel Stock", sections)
        self.assertIn("Hardware", sections)

    def test_parse_parts_purchased_flag(self):
        text = (
            "| Hardware |\n"
            "3 P\n1\n22\nRoll Out Guide\n22\" SIDE MOUNT ROLLOUT\n"
            "8\n1.4567\n1.8504\nHinge\n110 Degree Blum 11/16\"\n"
        )
        parts = _parse_assembly_parts_for_page(text)
        self.assertEqual(len(parts), 2)
        roll_out = next(p for p in parts if p["description"] == "Roll Out Guide")
        hinge = next(p for p in parts if p["description"] == "Hinge")
        self.assertTrue(roll_out["isPurchased"])
        self.assertFalse(hinge["isPurchased"])
        self.assertEqual(roll_out["qty"], 3)

    def test_parse_parts_diagram_excluded(self):
        # Diagram data at page bottom: numbers-only description stops parsing
        text = (
            "| Hardware |\n"
            "4\n0.25\n4.8\nDoor Pull\nChrome 4in C-Pull\n"
            # diagram data: qty=29, width=92, length=3.5, but desc="24" (no letters)
            "29\n92\n3.5\n24\nU\n"
        )
        parts = _parse_assembly_parts_for_page(text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["description"], "Door Pull")

    def test_page_details_includes_parts(self):
        pages = [
            "| Assembly #5 - Base Cabinet |\n"
            "| Room #1 (KITCHEN) - Wall #2 |\n"
            "| Frame |\n"
            "1\n1.25\n25.5\nBottom Rail\n3/4 Paint Grade Wood\n"
            "| Panel Stock |\n"
            "1\n23.01\n91.011\nLeft End\n3/4 Prefinished 19mm\n"
        ]
        doc = _make_mock_doc(pages)
        with patch("ready_jobs_watcher.cabinet_sheet_indexer.fitz.open", return_value=doc):
            doc.__enter__ = lambda s: s
            doc.__exit__ = MagicMock(return_value=False)
            doc.close = MagicMock()
            result = _parse_assembly_pdf("fake.pdf")
        self.assertIn("1", result.page_details)
        parts = result.page_details["1"]["parts"]
        self.assertEqual(len(parts), 2)
        descriptions = [p["description"] for p in parts]
        self.assertIn("Bottom Rail", descriptions)
        self.assertIn("Left End", descriptions)
        self.assertEqual(parts[0]["sectionType"], "Frame")
        self.assertEqual(parts[1]["sectionType"], "Panel Stock")


if __name__ == "__main__":
    unittest.main()
