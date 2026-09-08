# searxng — metasearch settings

The settings of somebody else's service, which holds the result parsers for
dozens of search engines. They live in the repository and are mounted into the
container as a file: keeping them only on the machine's disk means losing them at
the first rebuild and leaving no history of why this particular set is enabled.

**This file does NOT decide which engines are asked.** An engine disabled here
still answers when named explicitly — measured. The set is decided by whoever
calls, and in this module that is the pool computed from observation
(`adapter/pool.py`). What remains here is the configuration of the engines
themselves.

There is no signing key here and there must not be: the file is version
controlled, and a value that once enters history cannot be taken out of it. The
key comes from the environment, and the requirement is enforced by the compose
file — the metasearch itself does not fail without it, it silently takes a
publicly known default.
