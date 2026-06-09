import threading
import time

from ready_jobs_watcher.pending_queue import PendingQueue


class _DummyPdfHandler:
    def __init__(self):
        self._cooldown_lock = threading.Lock()
        self._conversion_cooldown = {}
        self.calls = []

    def _schedule_pdf_conversion(self, pdf_path, invert_images, delay_seconds=None):
        self.calls.append((pdf_path, invert_images, delay_seconds))


class _DummyRenameHandler:
    def __init__(self):
        self.calls = []

    def _schedule_folder_processing(self, folder_path, delay_seconds=None, persist_in_queue=True):
        self.calls.append((folder_path, delay_seconds, persist_in_queue))


def test_resume_pending_operations_delegates_to_handlers(tmp_path):
    queue_file = tmp_path / "pending_queue.json"
    q = PendingQueue(str(queue_file))

    pdf_path = tmp_path / "job.pdf"
    pdf_path.write_text("stub", encoding="utf-8")
    folder_path = tmp_path / "123 - JOB"
    folder_path.mkdir()

    q.add_pending_pdf(str(pdf_path), time.time() - 1, invert_images=True)
    q.add_pending_folder(str(folder_path), time.time() - 1)

    pdf_handler = _DummyPdfHandler()
    rename_handler = _DummyRenameHandler()
    q.resume_pending_operations(pdf_handler, rename_handler)

    assert len(pdf_handler.calls) == 1
    assert pdf_handler.calls[0][0] == str(pdf_path)
    assert pdf_handler.calls[0][1] is True
    assert float(pdf_handler.calls[0][2]) == 0.0

    assert len(rename_handler.calls) == 1
    assert rename_handler.calls[0][0] == str(folder_path)
    assert float(rename_handler.calls[0][1]) == 0.0
    assert rename_handler.calls[0][2] is False


def test_resume_pending_operations_prunes_missing_paths(tmp_path):
    queue_file = tmp_path / "pending_queue.json"
    q = PendingQueue(str(queue_file))

    missing_pdf = tmp_path / "missing.pdf"
    missing_folder = tmp_path / "missing-folder"
    q.add_pending_pdf(str(missing_pdf), time.time(), invert_images=False)
    q.add_pending_folder(str(missing_folder), time.time())

    pdf_handler = _DummyPdfHandler()
    rename_handler = _DummyRenameHandler()
    q.resume_pending_operations(pdf_handler, rename_handler)

    assert pdf_handler.calls == []
    assert rename_handler.calls == []
    assert q.get_pending_pdf(str(missing_pdf)) is None
    assert q.get_pending_folder(str(missing_folder)) is None
