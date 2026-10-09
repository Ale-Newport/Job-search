import sys
from jobagent.main import run
from jobagent.worker import main as worker_main

if __name__ == "__main__":
    worker_main() if '--meridian-worker' in sys.argv else run()
