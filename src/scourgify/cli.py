"""Single `scourgify` command.

Dispatch: bare -> wizard (via wrangle); setup/audit/apply -> wrangle;
classify -> classify; staleness -> staleness. Each tool keeps its own argparse,
so we just hand off argv. Imports are lazy so `scourgify --version` stays cheap.
"""
import sys

from scourgify import __version__


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("-V", "--version"):
        print(f"scourgify {__version__}")
        return
    if argv and argv[0] == "classify":
        from scourgify import classify
        sys.argv = ["scourgify classify", *argv[1:]]
        return classify.main()
    if argv and argv[0] == "staleness":
        from scourgify import staleness
        sys.argv = ["scourgify staleness", *argv[1:]]
        return staleness.main()
    # setup / audit / apply / (none -> wizard) all live in wrangle's main()
    from scourgify import wrangle
    return wrangle.main()


if __name__ == "__main__":
    main()
