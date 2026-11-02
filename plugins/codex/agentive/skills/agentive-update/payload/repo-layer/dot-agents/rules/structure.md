# Module boundaries

- Import another module only through its entry point; never create an import cycle.
- The boundary check runs where this project wired it (the lint verb `.agents/bin/verb lint` or CI). On a failure, fix the import as the message says; change the boundary config only when a human agrees.
