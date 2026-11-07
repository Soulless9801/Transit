import sys
from pathlib import Path


def test_databricks_exec_wrapper_without_dunder_file(monkeypatch):
    from aggiepulse.features import job
    path = Path(job.__file__)
    monkeypatch.setattr(sys,'argv',['job.py','--source-root',str(path.parents[2])])
    namespace = {'__name__':'bootstrap_test'}
    exec(compile(path.read_text(),str(path),'exec'),namespace)  # noqa: S102 - reproduce Databricks' wrapper
    assert callable(namespace['run_pipeline'])
