# Example policy pack

House rules the crew reads at the start of every run. List the file under `policyPacks` in the crew config.

1. Never edit generated files under `gen/`. Change the generator or its input and regenerate.
2. New public functions need a test in the same commit.
3. Dependency changes (adding, removing or upgrading a package) are a deal breaker: ask the owner with `sf chat ask` before touching a lockfile.
