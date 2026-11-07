"""Bundle Python task launcher; dependencies come from the bundled wheel."""
from aggiepulse.platform.batch import main

if __name__ == '__main__':
    status = main()
    # Databricks executes scripts through IPython, where SystemExit(0) fails a task.
    if status:
        raise RuntimeError(f'Ingest failed with exit code {status}')
