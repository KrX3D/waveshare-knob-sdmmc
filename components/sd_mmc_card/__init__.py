import os
import logging
import subprocess
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

# Headers we need and the subpaths to search for them within the IDF root.
# Each entry: (header_filename, [candidate subpaths])
# The first existing subpath wins for each header.
_REQUIRED_HEADERS = [
    ("esp_vfs_fat.h", [
        "components/fatfs/vfs/include",
        "components/esp_driver_sdmmc/include",
        "components/vfs/include",
        "components/esp_vfs/include",
        "components/fat_fileio/include",
    ]),
    ("wear_levelling.h", [
        "components/wear_levelling/include",
        "components/wear_levelling",
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
    # 1. IDF_PATH env var
    env_idf = os.environ.get("IDF_PATH")
    if env_idf:
        p = Path(env_idf)
        if _is_idf_root(p):
            return p

    # 2. Known package directories (HA add-on uses /data/cache/platformio/packages)
    packages_dirs = list(filter(None, [
        "/data/cache/platformio/packages",
        "/data/cache/packages",
        "/data/platformio/packages",
        os.environ.get("PLATFORMIO_PACKAGES_DIR"),
        "/root/.platformio/packages",
        "/root/.pio/packages",
        "/data/.platformio/packages",
        "/config/.platformio/packages",
        "/usr/local/.platformio/packages",
    ]))
    for pkg_dir in packages_dirs:
        result = _glob_framework(pkg_dir)
        if result:
            return result

    # 3. Build tree
    try:
        for hit in Path(CORE.build_path).rglob("components/fatfs"):
            candidate = hit.parent
            if _is_idf_root(candidate):
                return candidate
    except Exception:
        pass

    # 4. Filesystem search
    hit = _run(
        ["find", "/",
         "-path", "/proc", "-prune", "-o",
         "-path", "/sys", "-prune", "-o",
         "-path", "/dev", "-prune", "-o",
         "-name", "esp_vfs_fat.h", "-print", "-quit"],
        timeout=60,
    )
    if hit:
        try:
            # walk up until we find the components/ parent
            p = Path(hit)
            for parent in p.parents:
                if (parent / "components").is_dir() and _is_idf_root(parent):
                    return parent
        except Exception:
            pass

    return None


def _collect_include_dirs(idf_root):
    """
    Walk the IDF root to find the include directories for each required header.
    Logs exactly which paths are found and which are missing.
    Returns a list of unique include directories to inject.
    """
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
            # Header not found in any candidate — search within the framework
            result = _run(
                ["find", str(idf_root), "-name", header, "-print", "-quit"],
                timeout=30,
            )
            if result:
                actual_dir = Path(result).parent
                if str(actual_dir) not in seen:
                    seen.add(str(actual_dir))
                    include_dirs.append(actual_dir)
                _LOGGER.info("sd_mmc_card: %-20s → %s (found by search)", header, actual_dir)
            else:
                _LOGGER.error(
                    "sd_mmc_card: %s NOT FOUND anywhere under %s — "
                    "this will cause a compile error. "
                    "IDF 5.5 may have moved this header. "
                    "Contents of %s/components: %s",
                    header, idf_root, idf_root,
                    [d.name for d in (idf_root / "components").iterdir()
                     if d.is_dir()][:40],
                )

    return include_dirs


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
        idf_root = _find_idf_root()
        if idf_root is None:
            _LOGGER.error("sd_mmc_card: could not locate ESP-IDF root at all")
            return

        _LOGGER.info("sd_mmc_card: IDF root = %s", idf_root)
        include_dirs = _collect_include_dirs(idf_root)
        for d in include_dirs:
            cg.add_build_flag(f"-I{d}")
        _LOGGER.info("sd_mmc_card: injected %d include paths", len(include_dirs))

        # ffconf.h (pulled in via ff.h → esp_vfs_fat.h) references CONFIG_WL_SECTOR_SIZE
        # from the IDF wear_levelling Kconfig. When headers are injected outside the
        # normal IDF component dependency chain, sdkconfig.h is not automatically
        # included for this TU and the macro is undefined.
        # 512 is the only valid value in IDF 5.x.
        cg.add_build_flag("-DCONFIG_WL_SECTOR_SIZE=512")

        # ── Linker flags ────────────────────────────────────────────────────
        # ESPHome merges our source into its own IDF component but does not add
        # fatfs/sdmmc to that component's REQUIRES, so the IDF-compiled .a files
        # are not included in the final firmware.elf link command.
        #
        # PlatformIO (pioarduino) controls the final link step and honours
        # build_flags for it.  We add -L paths so the linker can find the .a
        # files, then group them with --start-group/--end-group so circular
        # references between static libs are resolved correctly.
        #
        # The .a files are produced by IDF's CMake during the compile phase,
        # which completes before the link phase — so the paths exist when the
        # linker runs even if they don't yet exist during this code-gen step.
        #
        # Path: {build_path}/.pioenvs/{device_name}/esp-idf/{component}/lib{component}.a
        device_name = Path(CORE.build_path).name
        idf_libs_base = Path(CORE.build_path) / ".pioenvs" / device_name / "esp-idf"

        # fatfs            → f_* functions, esp_vfs_fat_sdmmc_mount, esp_vfs_fat_sdcard_format
        # esp_driver_sdmmc → SDMMC host driver (IDF 5+)
        # sdmmc            → SDMMC protocol layer (sdmmc_card_t etc.)
        link_components = ["fatfs", "esp_driver_sdmmc", "sdmmc"]

        for component in link_components:
            cg.add_build_flag(f"-L{idf_libs_base / component}")

        # Keep flags together and ordered with a single -Wl, string
        libs = ",".join(f"-l{c}" for c in link_components)
        cg.add_build_flag(f"-Wl,--start-group,{libs},--end-group")
        _LOGGER.info("sd_mmc_card: added linker flags for: %s", link_components)