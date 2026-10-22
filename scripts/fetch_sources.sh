#!/usr/bin/env bash
# Download the upstream source archives into data/, verify their SHA-256
# and extract them. An archive already in data/ (for example one copied
# from this repository's Release) is verified instead of downloaded.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data"

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

# fetch <path> <url> <sha256>
fetch() {
    mkdir -p "$(dirname "$1")"
    if [[ ! -f "$1" ]]; then
        curl -fL --retry 3 -o "$1.part" "$2"
        mv "$1.part" "$1"
    fi
    local got
    got=$(sha256_of "$1")
    if [[ "$got" != "$3" ]]; then
        echo "SHA-256 mismatch for $1: expected $3, got $got" >&2
        exit 1
    fi
    echo "ok  $1"
}

FREEDICT_VERSION="1.9-fd1"
for pair in deu-eng eng-deu; do
    case "$pair" in
        deu-eng) want=8ea17f210e15f2f999792d9560c686a3d7df43c5b1506db2e0a1a6eac01ac6cf ;;
        eng-deu) want=83a02d8eace3fbb460fa96c32c2d964a9bc1e1a63ea754021ac33042ee2d1d5f ;;
    esac
    archive="$DATA/freedict/freedict-$pair.src.tar.xz"
    fetch "$archive" \
        "https://download.freedict.org/dictionaries/$pair/$FREEDICT_VERSION/freedict-$pair-$FREEDICT_VERSION.src.tar.xz" \
        "$want"
    [[ -f "$DATA/freedict/$pair/$pair.tei" ]] || tar -xJf "$archive" -C "$DATA/freedict/"
done

GCIDE_VERSION="0.53"
archive="$DATA/gcide/gcide-$GCIDE_VERSION.tar.xz"
fetch "$archive" "https://ftp.gnu.org/gnu/gcide/gcide-$GCIDE_VERSION.tar.xz" \
    d3acbd984de1ce022d5d139903887b36232e436e30eca2cb0fb1c058dd7f319f
[[ -d "$DATA/gcide/gcide-$GCIDE_VERSION" ]] || tar -xJf "$archive" -C "$DATA/gcide/"
