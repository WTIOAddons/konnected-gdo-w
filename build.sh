#!/bin/bash -e

ADDON_ARCH="$1"
LANGUAGE_NAME="$2"
LANGUAGE_VERSION="$3"

function map_posix_tools() {
  tar() {
    gtar "$@"
    return $!
  }
  export -f tar

  readlink() {
    greadlink "$@"
    return $!
  }
  export -f readlink

  find() {
    gfind "$@"
    return $!
  }
  export -f find
}

function install_osx_compiler() {
  brew install \
    boost \
    cmake \
    coreutils \
    eigen \
    findutils \
    gnu-tar \
    pkg-config
  map_posix_tools
}

function install_linux_cross_compiler() {
  sudo apt -qq update
  sudo apt install --no-install-recommends -y \
    binfmt-support \
    qemu-user-static
  docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
}

function build_native() {
  python -m pip install -U pip
  python -m pip install -U setuptools wheel
  ADDON_ARCH=${ADDON_ARCH} ./package.sh
}

function build_cross_compiled() {
  # WebThingsIO only publishes toolchain images up to Python 3.9. For newer
  # versions there is no webthingsio/toolchain-* image, so run the official
  # multi-arch python image under qemu emulation instead. Dependencies install
  # from prebuilt wheels (see package.sh), so no cross-compilation toolchain is
  # required.
  case "${LANGUAGE_VERSION}" in
    3.7 | 3.8 | 3.9)
      docker run --rm -t -v $PWD:/build \
        webthingsio/toolchain-${ADDON_ARCH}-${LANGUAGE_NAME}-${LANGUAGE_VERSION} \
        bash -c "cd /build; ADDON_ARCH=${ADDON_ARCH} ./package.sh"
      ;;
    *)
      case "${ADDON_ARCH}" in
        linux-x64)   DOCKER_PLATFORM="linux/amd64" ;;
        linux-arm64) DOCKER_PLATFORM="linux/arm64" ;;
        linux-arm)   DOCKER_PLATFORM="linux/arm/v7" ;;
        *)
          echo "Unsupported architecture for ${LANGUAGE_NAME} ${LANGUAGE_VERSION}: ${ADDON_ARCH}"
          exit 1
          ;;
      esac
      docker run --rm -t --platform "${DOCKER_PLATFORM}" -v $PWD:/build \
        "${LANGUAGE_NAME}:${LANGUAGE_VERSION}" \
        bash -c "cd /build; ADDON_ARCH=${ADDON_ARCH} ./package.sh"
      ;;
  esac
}

case "${ADDON_ARCH}" in
  darwin-x64)
    install_osx_compiler
    build_native
    ;;

  linux-arm)
    install_linux_cross_compiler
    build_cross_compiled
    ;;

  linux-arm64)
    install_linux_cross_compiler
    build_cross_compiled
    ;;

  linux-x64)
    install_linux_cross_compiler
    build_cross_compiled
    ;;

  *)
    echo "Unsupported architecture"
    exit 1
    ;;
esac
