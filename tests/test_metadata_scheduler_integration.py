from ready_jobs_watcher.scheduler import process_metadata_end_of_day_once


class FakeMetadataRefreshService:
    def __init__(self):
        self.calls = []

    def run_scheduled_sweep(self, *, consolidate_trackers=True):
        self.calls.append(consolidate_trackers)
        return {"processed": 2, "rebuilt": 1, "archived": 2, "errors": 0}


def test_process_metadata_end_of_day_once_runs_condensing_sweep():
    service = FakeMetadataRefreshService()

    result = process_metadata_end_of_day_once(service)

    assert service.calls == [True]
    assert result["rebuilt"] == 1
