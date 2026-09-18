#!/bin/bash -e

# Setup environment for building inside Dockerized toolchain
[ $(id -u) = 0 ] && umask 0

version=$(grep '"version"' manifest.json | cut -d: -f2 | cut -d\" -f2)

if [ -z "${ADDON_ARCH}" ]; then
    TARFILE_SUFFIX=
else
    PYTHON_VERSION="$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d. -f 1-2)"
    TARFILE_SUFFIX="-${ADDON_ARCH}-v${PYTHON_VERSION}"
fi

# Clean up from previous releases
rm -rf *.tgz package SHA256SUMS lib

# Prep new package
mkdir lib package

# Pull down Python dependencies.
#
# For 3.7/3.8/3.9 we apply constraints-py-legacy.txt to keep the dep
# chain on releases that publish wheels for our older toolchain image
# (Raspbian Buster, glibc 2.28, no Rust). Without these pins, newer
# jsonschema pulls in Rust-backed rpds-py which has no usable wheels
# for the older arm/python combinations and source-builds via maturin.
if [ "$PYTHON_VERSION" = "3.7" ] || [ "$PYTHON_VERSION" = "3.8" ] || [ "$PYTHON_VERSION" = "3.9" ]; then
pip3 install -r requirements.txt -c constraints-py-legacy.txt -t lib --prefix ""
else
# Newer Python (3.10+) builds run in the official python image and install
# from prebuilt wheels. gateway_addon pins jsonschema==3.2.0, whose dependency
# chain is pyrsistent-backed (a pure-Python lineage, not the Rust-backed
# rpds-py), so no legacy constraint is needed; wheels are used wherever they
# are published (only armv7 pyrsistent has no wheel and source-builds via the
# official image's compiler).
pip3 install -r requirements.txt -t lib --prefix ""
fi

# Put package together
#cp -r lib pkg LICENSE README.md package.json manifest.json *.py package/
cp -r lib pkg LICENSE manifest.json package.json *.py README.md package/
find package -type f -name '*.pyc' -delete
find package -type d -empty -delete

# Generate checksums
cd package
sha256sum *.py pkg/*.py *.json README.md LICENSE > SHA256SUMS
find lib -type f -exec sha256sum {} \; >> SHA256SUMS
cd -

# Make the tarball
TARFILE="konnected-gdo-w-${version}${TARFILE_SUFFIX}.tgz"
tar czf ${TARFILE} package

shasum --algorithm 256 ${TARFILE} > ${TARFILE}.sha256sum

rm -rf SHA256SUMS package
