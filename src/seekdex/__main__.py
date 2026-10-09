from .runtime_output import prepare_windowed_output

prepare_windowed_output()

from .app import main

raise SystemExit(main())
