#!/bin/bash
# Move code between the Pi's working copy and this git repository.
#
#   bash sync.sh            # or "pull": Pi mount -> repo, ready to commit
#   bash sync.sh push       # repo -> Pi mount, e.g. after a git pull
#   bash sync.sh status      # report differences, copy nothing
#
# The repo is kept OFF the SSHFS mount on purpose. git does constant
# read-after-write against its own object store, and reads back through SSHFS
# can return stale data when the Pi has written out of band. Handing git a
# filesystem that can lie to it is not worth the convenience.
#
#   ~/PiProjects/Willis/
#     |-- remote/                      the Pi, over SSHFS
#     |-- local/                       Mac-only notes; the unredacted CLAUDE.md
#     `-- WhatchuLookinAtWillis-Pi/    this repository
#
# THE TRAP, inherited knowingly from the project this pattern came from: only
# files named in the arrays below are copied. A new module that is not added to
# the right array is silently never synced - no error, it simply never appears.
# Adding a file to the project means editing this script in the same change.
# The guard below makes that non-silent for src/ and tools/; ROOT_FILES,
# TEST_FILES and DEPLOY_FILES are still on their honour.

set -u

REPO="$(cd "$(dirname "$0")" && pwd)"
BASE="$(cd "$REPO/.." && pwd)"
PI="$BASE/remote"
LOCAL="$BASE/local"

ROOT_FILES=(willis.py run_willis.sh sync.sh requirements.txt)

SRC_FILES=(
    version.py
    __init__.py
    panel/__init__.py  panel/ili9341.py  panel/caption.py
    capture/__init__.py capture/still.py
    eyes/__init__.py   eyes/client.py    eyes/describe.py
    control/__init__.py control/encoder.py control/power_led.py control/buzzer.py
)

TEST_FILES=(
    __init__.py
    panel/__init__.py  panel/caption_test.py  panel/panel_selftest.py
    eyes/__init__.py   eyes/describe_test.py
    control/__init__.py control/loop_test.py
)

DEPLOY_FILES=(setup.sh pwm_export.sh willis.service)

TOOL_FILES=(hardware/panel_blank.py)

# Listed rather than left out, because "absent from TOOL_FILES" and "meant to
# stay on the Mac" are different states and the guard cannot tell them apart.
REPO_ONLY_TOOLS=()

# --- the guard -------------------------------------------------------------
# Every file under src/ and tools/ must appear in exactly one array. This is
# the check that turns the trap above into an error message.
check_arrays() {
    local problem=0 entry found rel
    for rel in $(cd "$REPO/src" && find . -name '*.py' -not -path '*/__pycache__/*' | sed 's|^\./||' | sort); do
        found=0
        for entry in "${SRC_FILES[@]}"; do
            [ "$entry" = "$rel" ] && found=1 && break
        done
        [ "$found" = 0 ] && { echo "ERROR: src/$rel is in no array." >&2; problem=1; }
    done
    if [ -d "$REPO/tools" ]; then
        for rel in $(cd "$REPO/tools" && find . -type f -not -path '*/__pycache__/*' | sed 's|^\./||' | sort); do
            found=0
            for entry in "${TOOL_FILES[@]}" ${REPO_ONLY_TOOLS[@]+"${REPO_ONLY_TOOLS[@]}"}; do
                [ "$entry" = "$rel" ] && found=1 && break
            done
            [ "$found" = 0 ] && { echo "ERROR: tools/$rel is in no array." >&2; problem=1; }
        done
    fi
    if [ "$problem" = 1 ]; then
        echo "       Add it to the deploy array, or to REPO_ONLY_TOOLS to keep" >&2
        echo "       it on the Mac. Refusing to run with an unlisted file." >&2
        exit 1
    fi
}

# --- CLAUDE.md -------------------------------------------------------------
# The repo's copy has the Pi's address partly masked, and is regenerated from
# the unredacted original in local/ on every pull. Always edit local/CLAUDE.md;
# never edit the repo's copy, and never push it to the Pi.
sync_claude_md() {
    local src="$LOCAL/CLAUDE.md" dst="$REPO/CLAUDE.md" tmp
    if [ ! -f "$src" ]; then
        echo "  skip  CLAUDE.md (no $src)"
        return
    fi
    tmp="$(mktemp)"
    sed -E 's/([0-9]{1,3}\.[0-9]{1,3})\.[0-9]{1,3}\.[0-9]{1,3}/\1.x.x/g' \
        "$src" > "$tmp"
    # Refuse rather than publish. A redaction that silently failed would put
    # the address in git history, where deleting it later does not help.
    if grep -qE '([0-9]{1,3}\.){3}[0-9]{1,3}' "$tmp"; then
        echo "ERROR: an unmasked IP address survived redaction; refusing." >&2
        rm -f "$tmp"; exit 1
    fi
    if [ "$MODE" = status ]; then
        cmp -s "$tmp" "$dst" 2>/dev/null || echo "  DIFF  CLAUDE.md"
    else
        cp "$tmp" "$dst"
        echo "  copy  CLAUDE.md (IP redacted)"
    fi
    rm -f "$tmp"
}

copy_one() {               # copy_one <from> <to> <label>
    local from="$1" to="$2" label="$3"
    if [ ! -f "$from" ]; then
        echo "  MISS  $label"
        return
    fi
    case "$MODE" in
        status)
            if [ ! -f "$to" ]; then
                echo "  NEW   $label"
            elif ! cmp -s "$from" "$to"; then
                echo "  DIFF  $label"
            fi
            ;;
        *)
            mkdir -p "$(dirname "$to")"
            cmp -s "$from" "$to" && return
            cp "$from" "$to"
            echo "  copy  $label"
            ;;
    esac
}

walk() {                   # walk <src root> <dst root> <prefix> <files...>
    local from_root="$1" to_root="$2" prefix="$3"; shift 3
    local rel
    for rel in "$@"; do
        copy_one "$from_root/$rel" "$to_root/$rel" "$prefix$rel"
    done
}

MODE="${1:-pull}"
case "$MODE" in
    pull|push|status) ;;
    *) echo "usage: sync.sh [pull|push|status]"; exit 2 ;;
esac

check_arrays

if [ ! -d "$PI" ]; then
    echo "The Pi is not mounted at $PI - run ~/mountwillis.sh first." >&2
    exit 1
fi
if [ -z "$(ls -A "$PI" 2>/dev/null)" ]; then
    echo "$PI is empty. Either the mount dropped, or nothing is deployed yet." >&2
    [ "$MODE" = pull ] && exit 1
fi

if [ "$MODE" = push ]; then
    FROM="$REPO"; TO="$PI"
    echo "repo -> Pi"
else
    FROM="$PI"; TO="$REPO"
    echo "Pi -> repo${MODE:+ ($MODE)}"
fi

walk "$FROM" "$TO" ""        "${ROOT_FILES[@]}"
walk "$FROM/src" "$TO/src" "src/"       "${SRC_FILES[@]}"
walk "$FROM/tests" "$TO/tests" "tests/" "${TEST_FILES[@]}"
walk "$FROM/deploy" "$TO/deploy" "deploy/" "${DEPLOY_FILES[@]}"
walk "$FROM/tools" "$TO/tools" "tools/" "${TOOL_FILES[@]}"

# CLAUDE.md is never pushed: the repo's copy is redacted, and overwriting the
# Pi's with it would replace a working file with a censored one.
[ "$MODE" != push ] && sync_claude_md

echo "done."
