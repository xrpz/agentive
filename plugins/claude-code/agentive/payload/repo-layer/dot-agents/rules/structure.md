# Module boundaries

- Import another module only through its entry point; never create an import cycle.
- The boundary check command and its config are on the `Module boundaries:` line of AGENTS.md. On a failure, fix the import as the message says; change the boundary config only when a human agrees.
