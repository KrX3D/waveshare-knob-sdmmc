import os
import re
import logging
import subprocess
import threading
import time
from pathlib import Path

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome import pins
from esphome.core import CORE

_LOGGER = logging.getLogger(__name__)

DEPENDENCIES = ["esp32"]
AUTO_LOAD = ["sensor", "text_sensor"]

sd_ns = cg.esphome_ns.namespace("sd_mmc_card")
SdMmcCard = sd_ns.class_("SdMmcCard", cg.Component)

CONF_CLK_PIN   = "clk_pin"
CONF_CMD_PIN   = "cmd_pin"
CONF_DATA0_PIN = "data0_pin"
CONF_DATA1_PIN = "data1_pin"
CONF_DATA2_PIN = "data2_pin"
CONF_DATA3_PIN = "data3_pin"
CONF_MODE_1BIT = "mode_1bit"

_IDF_DEPS = ["fatfs", "sdmmc", "esp_driver_sdmmc"]

_REQUIRED_HEADERS = [
    ("esp_vfs_fat.h", [
        "components/fatfs/vfs/include",
        "components/vfs/include",
        "components/esp_vfs/include",
    ]),
    ("wear_levelling.h", [
        "components/wear_levelling/include",
    ]),
    ("ff.h", [
        "components/fatfs/src",
        "components/fatfs/include",
    ]),
    ("sdmmc_cmd.h", [
        "components/sdmmc/include",
        "components/esp_driver_sdmmc/include",
    ]),
    ("sdmmc_host.h", [
        "components/esp_driver_sdmmc/include",
        "components/esp_driver_sdmmc/include/driver",
        "components/driver/include",
        "components/driver/include/driver",
    ]),
]


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _is_idf_root(p):
    return (p / "components" / "fatfs").is_dir()


def _glob_framework(packages_dir):
    packages_dir = Path(packages_dir)
    if not packages_dir.is_dir():
        return None
    candidates = sorted(
        packages_dir.glob("framework-espidf*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for c in candidates:
        if _is_idf_root(c):
            return c
    return None


def _find_idf_root():
    env_idf = os.environ.get("IDF_PATH")
    if env_idf:
        p = Path(env_idf)
        if _is_idf_root(p):
            return p

    for pkg_dir in filter(None, [
        "/data/cache/platformio/packages",
        "/data/cache/packages",
        os.environ.get("PLATFORMIO_PACKAGES_DIR"),
        "/root/.platformio/packages",
        "/root/.pio/packages",
        "/data/.platformio/packages",
        "/config/.platformio/packages",
        "/usr/local/.platformio/packages",
    ]):
        result = _glob_framework(pkg_dir)
        if result:
            return result

    try:
        for hit in Path(CORE.build_path).rglob("components/fatfs"):
            candidate = hit.parent
            if _is_idf_root(candidate):
                return candidate
    except Exception:
        pass

    hit = _run(["find", "/", "-path", "/proc", "-prune", "-o",
                 "-path", "/sys", "-prune", "-o", "-path", "/dev", "-prune", "-o",
                 "-name", "esp_vfs_fat.h", "-print", "-quit"], timeout=60)
    if hit:
        try:
            for parent in Path(hit).parents:
                if (parent / "components").is_dir() and _is_idf_root(parent):
                    return parent
        except Exception:
            pass
    return None


def _collect_include_dirs(idf_root):
    idf_root = Path(idf_root)
    include_dirs = []
    seen = set()

    for header, candidates in _REQUIRED_HEADERS:
        found = False
        for subpath in candidates:
            inc_dir = idf_root / subpath
            if (inc_dir / header).is_file():
                if str(inc_dir) not in seen:
                    seen.add(str(inc_dir))
                    include_dirs.append(inc_dir)
                _LOGGER.info("sd_mmc_card: %-20s → %s", header, inc_dir)
                found = True
                break
        if not found:
            result = _run(["find", str(idf_root), "-name", header, "-print", "-quit"], timeout=30)
            if result:
                actual_dir = Path(result).parent
                if str(actual_dir) not in seen:
                    seen.add(str(actual_dir))
                    include_dirs.append(actual_dir)
                _LOGGER.info("sd_mmc_card: %-20s → %s (found by search)", header, actual_dir)
            else:
                _LOGGER.error("sd_mmc_card: %s NOT FOUND under %s", header, idf_root)

    return include_dirs


# ── CMakeLists.txt patch (background thread) ─────────────────────────────────
#
# ESPHome 2026.3.0 does not expose cg.add_idf_component_dependency or any
# equivalent API that adds entries to the REQUIRES list of the generated
# src/CMakeLists.txt.  Without fatfs/sdmmc/esp_driver_sdmmc in that list the
# linker cannot find esp_vfs_fat_sdmmc_mount, f_getfree, f_opendir etc.
#
# The generated src/CMakeLists.txt is written by ESPHome AFTER all to_code
# coroutines complete, and is read by CMake during the PlatformIO/pioarduino
# build that starts afterwards.  We exploit this window by starting a thread
# that polls for the file, patches the REQUIRES list, and exits before CMake
# reads it.
#
# This does NOT affect the bootloader because the bootloader uses a completely
# separate CMakeLists.txt (under .pioenvs/<dev>/esp-idf/bootloader/) that is
# unrelated to the main src/CMakeLists.txt we patch.

def _patch_cmake_requires(cmake_path: Path, deps: list):
    """
    Wait for cmake_path to be written, then insert deps into the
    idf_component_register REQUIRES block.  Runs in a daemon thread.
    """
    deadline = time.monotonic() + 120  # wait up to 2 minutes

    while time.monotonic() < deadline:
        if not cmake_path.exists():
            time.sleep(0.15)
            continue

        try:
            content = cmake_path.read_text(encoding="utf-8")
        except OSError:
            continue

        # Wait until the file is fully written (must contain the register call)
        if "idf_component_register" not in content:
            continue

        # Check if we even need to patch
        missing = [d for d in deps if d not in content]
        if not missing:
            _LOGGER.debug("sd_mmc_card: CMakeLists.txt already contains all deps, no patch needed")
            return

        deps_str = " ".join(missing)  # space-separated; CMake is whitespace-agnostic

        if re.search(r'\bREQUIRES\b', content):
            # A REQUIRES block already exists — prepend our deps to it.
            patched = re.sub(
                r'\bREQUIRES\b',
                f'REQUIRES {deps_str}',
                content,
                count=1,
            )
        else:
            # No REQUIRES block — inject one before the closing ')' of
            # idf_component_register(…).  A literal newline before and after
            # the new keyword prevents it merging with adjacent tokens (e.g.
            # "esp_driver_sdmmcSRCS" when SRCS follows immediately).
            patched, n = re.subn(
                r'(idf_component_register\s*\([^)]*)\)',
                f'\\1\n    REQUIRES {deps_str}\n)',
                content,
                count=1,
                flags=re.DOTALL,
            )
            if n == 0:
                # Last-resort fallback
                patched = content.replace(
                    "idf_component_register(",
                    f"idf_component_register(\n    REQUIRES {deps_str}\n    ",
                    1,
                )

        try:
            cmake_path.write_text(patched, encoding="utf-8")
            _LOGGER.info(
                "sd_mmc_card: patched %s — added REQUIRES: %s", cmake_path, missing
            )
            return
        except OSError as exc:
            _LOGGER.warning("sd_mmc_card: could not write %s: %s", cmake_path, exc)
            return

    _LOGGER.error(
        "sd_mmc_card: timed out waiting for %s — linker will likely fail "
        "with undefined refs to f_getfree / esp_vfs_fat_sdmmc_mount etc.",
        cmake_path,
    )


def _validate_pins(config):
    if not config.get(CONF_MODE_1BIT, False):
        for pin_name in [CONF_DATA1_PIN, CONF_DATA2_PIN, CONF_DATA3_PIN]:
            if pin_name not in config:
                raise cv.Invalid(
                    f"'{pin_name}' is required when mode_1bit is false (4-bit mode). "
                    f"Set mode_1bit: true to use 1-bit mode with only data0_pin."
                )
    return config


CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(SdMmcCard),
            cv.Required(CONF_CLK_PIN):   pins.internal_gpio_output_pin_number,
            cv.Required(CONF_CMD_PIN):   pins.internal_gpio_output_pin_number,
            cv.Required(CONF_DATA0_PIN): pins.internal_gpio_pin_number,
            cv.Optional(CONF_DATA1_PIN): pins.internal_gpio_pin_number,
            cv.Optional(CONF_DATA2_PIN): pins.internal_gpio_pin_number,
            cv.Optional(CONF_DATA3_PIN): pins.internal_gpio_pin_number,
            cv.Optional(CONF_MODE_1BIT, default=False): cv.boolean,
        }
    ).extend(cv.COMPONENT_SCHEMA),
    _validate_pins,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_clk_pin(config[CONF_CLK_PIN]))
    cg.add(var.set_cmd_pin(config[CONF_CMD_PIN]))
    cg.add(var.set_data0_pin(config[CONF_DATA0_PIN]))
    cg.add(var.set_mode_1bit(config[CONF_MODE_1BIT]))

    if not config[CONF_MODE_1BIT]:
        cg.add(var.set_data1_pin(config[CONF_DATA1_PIN]))
        cg.add(var.set_data2_pin(config[CONF_DATA2_PIN]))
        cg.add(var.set_data3_pin(config[CONF_DATA3_PIN]))

    if CORE.is_esp32:
        # ── 1. Compiler include paths ────────────────────────────────────────
        idf_root = _find_idf_root()
        if idf_root is None:
            _LOGGER.error("sd_mmc_card: could not locate ESP-IDF root")
            return

        _LOGGER.info("sd_mmc_card: IDF root = %s", idf_root)
        for d in _collect_include_dirs(idf_root):
            cg.add_build_flag(f"-I{d}")

        # ffconf.h references CONFIG_WL_SECTOR_SIZE from wear_levelling Kconfig.
        # 512 is the only valid value in IDF 5.x.
        cg.add_build_flag("-DCONFIG_WL_SECTOR_SIZE=512")

        # ── 2. Linker: try ESPHome's built-in API first ──────────────────────
        # Print available IDF-related attrs for diagnostics on first run.
        idf_attrs = [a for a in dir(cg) if "idf" in a.lower() or "require" in a.lower()]
        _LOGGER.debug("sd_mmc_card: cg IDF-related attributes: %s", idf_attrs)

        linked = False
        for fn_name in [
            "add_idf_component_dependency",   # ESPHome 2022-2025
            "add_idf_sdk_component",
            "add_esp_idf_component",
        ]:
            fn = getattr(cg, fn_name, None)
            if fn is not None:
                for dep in _IDF_DEPS:
                    fn(dep)
                _LOGGER.info("sd_mmc_card: registered IDF deps via cg.%s: %s", fn_name, _IDF_DEPS)
                linked = True
                break

        if not linked:
            try:
                from esphome.components.esp32 import add_idf_component_dependency as _add
                for dep in _IDF_DEPS:
                    _add(dep)
                _LOGGER.info("sd_mmc_card: registered IDF deps via esp32 module: %s", _IDF_DEPS)
                linked = True
            except (ImportError, AttributeError):
                pass

        if not linked:
            # ── 3. Fallback: patch src/CMakeLists.txt ───────────────────────
            # ESPHome keeps the build directory between runs, so the file from
            # the previous build is usually already present when to_code runs.
            # We patch it IMMEDIATELY (synchronous) to cover that case, then
            # also start a thread to re-patch after ESPHome rewrites it for
            # this build — covering the first-ever build on a clean directory.
            cmake_path = Path(CORE.build_path) / "src" / "CMakeLists.txt"
            _LOGGER.warning(
                "sd_mmc_card: no ESPHome API available to add IDF component deps. "
                "Patching %s (sync now + thread for rewrite).",
                cmake_path,
            )
            # Synchronous patch — fixes the cached file CMake will read first
            if cmake_path.exists():
                _patch_cmake_requires(cmake_path, _IDF_DEPS)
            else:
                _LOGGER.info(
                    "sd_mmc_card: %s does not exist yet (first build) — "
                    "thread will patch it after ESPHome writes it.",
                    cmake_path,
                )
            # Thread patch — fixes the file after ESPHome rewrites it this run
            t = threading.Thread(
                target=_patch_cmake_requires,
                args=(cmake_path, _IDF_DEPS),
                daemon=True,
                name="sd_mmc_cmake_patch",
            )
            t.start()