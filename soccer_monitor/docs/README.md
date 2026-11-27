# Football_Under_Monitor developer documentation

Verified against the working tree on 2026-11-27. Links point to source files so
that contracts can be checked against the implementation.

- [System overview](overview.md): modules, dependencies, shared state, tests, and deployment.
- [Live monitor internals](soccer-monitors.md): polling, alert delivery, rearms, the daily report, and the control panel.
- [Helper scripts](stock-and-tools.md): soccer analysis scripts. Stock tools are documented in [the SMA bot](../../sma_bot/README.md).
- [Operator setup](../README.md) and [VM deployment guide](../oracle_daemon_guide.md).

The checked-in service configuration is evidence of deployment configuration,
not proof of which services are currently running on any machine.
