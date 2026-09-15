"""Compatibility entry point for batches started before the module cleanup.

New code imports :mod:`pogo_iphone_renamer.batch_agent`.  Keep this tiny
launcher until every detached worker created by older releases has exited.
"""

from .batch_agent import main


if __name__ == "__main__":
    raise SystemExit(main())
