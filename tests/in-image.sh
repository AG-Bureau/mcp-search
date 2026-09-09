#!/usr/bin/env bash
# The Python test suites IN THE MODULE IMAGE, not on a developer machine.
#
#     bash tests/in-image.sh
#
# WHY A SEPARATE RUN. The module has a dependency — the PDF parser — which is in
# the image but need not be on the machine where the code is edited. Tests run
# outside skip everything that touches it.
#
# The skip is NOT SILENT: without the parser the PDF check goes red with a note
# saying "run this in the image". A silent skip would give a green run that
# checked nothing — exactly the defect these tests look for.
set -u
MOD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-ag-mod-search/adapter:0.3.0}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "no image $IMAGE — building it"
  docker build -q -t "$IMAGE" "$MOD/adapter" >/dev/null || { echo "build failed"; exit 1; }
fi

# THE CONTRACTS SIT IN TWO PLACES DEPENDING ON WHOSE TREE THIS IS: one level
# above the module in the repository we develop in, and INSIDE the root in the
# published one. Guessing one of them makes the suite pass here and die there —
# the difference the clean-tree trial exists to catch, and did.
CONTRACTS="$MOD/../contracts"
[ -d "$MOD/contracts" ] && CONTRACTS="$MOD/contracts"

fail=0
for t in test_adapter.py test_reader.py test_pool.py; do
  echo "=== $t"
  # The tests are mounted, not copied into the image: the image carries what runs
  # in production, and there is no reason to inflate it with checks.
  # PYTHONPATH=/app is mandatory: the tests look for the modules in a sibling
  # directory (`../adapter`), while in the image they live in /app. Without it the
  # run fails at import — and fails LOUDLY, which is right: silently it would
  # "pass".
  # THE PROMISE IS HELD BY THE RUN, NOT BY GOOD INTENTIONS. README says in bold
  # that not one check makes an outbound request; an outsider disproves that in
  # one command unless the suite is actually sealed. `--network none` seals it:
  # anything that reaches for the internet fails here rather than in somebody
  # else's log, and the loopback fixtures the tests raise keep working.
  docker run --rm --network none -e PYTHONPATH=/app \
    -v "$MOD/adapter":/app:ro -v "$MOD/tests":/tests:ro -v "$MOD/prober":/prober:ro \
    -v "$MOD/docker-compose.yml":/docker-compose.yml:ro \
    -v "$CONTRACTS":/contracts:ro \
    -v "$MOD/ALGORITHM.md":/docs/ALGORITHM.md:ro \
    -v "$MOD/README.md":/docs/README.md:ro \
    -v "$MOD/HOWTO-CALL.md":/docs/HOWTO-CALL.md:ro \
    -v "$MOD/adapter/Dockerfile":/dockerfiles/adapter:ro \
    -v "$MOD/browser/Dockerfile":/dockerfiles/browser:ro \
    -w /tests "$IMAGE" python "/tests/$t" || fail=1
done
[ "$fail" -eq 0 ] && echo "ALL SUITES GREEN IN THE IMAGE" || echo "THERE IS RED"
exit "$fail"
