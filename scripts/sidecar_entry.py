import sys
from jobagent.calibration import main as calibration_main
from jobagent.extension.parse_worker import main as parse_worker_main
from jobagent.main import run
from jobagent.worker import main as worker_main


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ['--meridian-calibration']:
        return calibration_main(arguments[1:])
    if arguments[:1] == ['--meridian-parse']:  # the killable page reader of the browser assistant (extension/markup.py)
        return parse_worker_main()
    if '--meridian-worker' in arguments:
        return worker_main()
    return run()


if __name__ == "__main__":
    main()
