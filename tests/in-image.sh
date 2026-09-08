#!/usr/bin/env bash
# The Python test suites IN THE MODULE IMAGE, not on a developer machine.
#
#     search/tests/in-image.sh
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
IMAGE="${IMAGE:-ag-mod-search/adapter:1}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "no image $IMAGE — building it"
  docker build -q -t "$IMAGE" "$MOD/adapter" >/dev/null || { echo "build failed"; exit 1; }
fi

fail=0
for t in test_adapter.py test_reader.py test_pool.py; do
  echo "=== $t"
  # The tests are mounted, not copied into the image: the image carries what runs
  # in production, and there is no reason to inflate it with checks.
  # PYTHONPATH=/app is mandatory: the tests look for the modules in a sibling
  # directory (`../adapter`), while in the image they live in /app. Without it the
  # run fails at import — and fails LOUDLY, which is right: silently it would
  # "pass".
  docker run --rm --network bridge -e PYTHONPATH=/app \
    -v "$MOD/adapter":/app:ro -v "$MOD/tests":/tests:ro -v "$MOD/prober":/prober:ro \
    -v "$MOD/docker-compose.yml":/docker-compose.yml:ro \
    -w /tests "$IMAGE" python "/tests/$t" || fail=1
done
[ "$fail" -eq 0 ] && echo "ALL SUITES GREEN IN THE IMAGE" || echo "THERE IS RED"
exit "$fail"
