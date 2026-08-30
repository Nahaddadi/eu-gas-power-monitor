"""Allow `python -m energydesk` to work from an editable install."""

from energydesk.cli import main

raise SystemExit(main())
