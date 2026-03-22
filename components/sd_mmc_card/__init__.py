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

# IDF sub-paths that must be on the include search path.
# IDF 5.x moved sdmmc_host.h into esp_driver_sdmmc/; listing both is safe
# because a -I for a non-existent directory is silently ignored by GCC.
_IDF_INCLUDE_SUBPATHS = [
    "components/fatfs/vfs/include",        # esp_vfs_fat.h
    "components/fatfs/src",                # ff.h, ffconf.h
    "components/sdmmc/include",            # sdmmc_cmd.h, driver/sdmmc_defs.h
    "components/esp_driver_sdmmc/include", # driver/sdmmc_host.h  (IDF 5+)
    "components/driver/include",           # driver/sdmmc_host.h  (IDF 4 compat)
]


def _is_idf_root(p: Path) -> bool:
    """Return True if p looks like an ESP-IDF root (has components/fatfs/src)."""
    return (p / "components" / "fatfs" / "src").is_dir()


def _find_idf_root() -> Path | None:
    """
    Locate the ESP-IDF root using multiple strategies, most reliable first.

    Strategy 1 – IDF_PATH env var (standalone IDF, CI, custom setups).
    Strategy 2 – Search the ESPHome build directory (.esphome/build/<device>)
                 for the libdeps / framework directory that PlatformIO already
                 unpacked for this exact build.  This is the most reliable path
                 for HA add-on / Docker because it is the framework that will
                 actually be used to compile.
    Strategy 3 – Walk every plausible PlatformIO home location and glob for
                 framework-espidf* packages.  Covers native Linux/macOS installs,
                 HA add-on (/root), custom PLATFORMIO_CORE_DIR, and common Docker
                 image layouts.
    Strategy 4 – Last resort: ask `find` to locate esp_vfs_fat.h on the
                 filesystem (capped to a few well-known root directories so it
                 doesn't traverse the whole disk).
    """

    # ── Strategy 1: IDF_PATH env var ─────────────────────────────────────────
    env_idf = os.environ.get("IDF_PATH")
    if env_idf:
        p = Path(env_idf)
        if _is_idf_root(p):
            _LOGGER.debug("sd_mmc_card: IDF root via IDF_PATH: %s", p)
            return p

    # ── Strategy 2: PlatformIO build tree inside CORE.build_path ─────────────
    # CORE.build_path is e.g. /config/esphome/.esphome/build/esp-smart-knob
    # PIO expands the framework under  <build_path>/.pioenvs/<name>/
    # We walk every directory inside that tree looking for the IDF root.
    try:
        build_path = Path(CORE.build_path)
        # Common locations PlatformIO unpacks the framework to:
        search_roots_in_build = [
            build_path / ".pioenvs",
            build_path / "managed_components",
        ]
        for sr in search_roots_in_build:
            if not sr.is_dir():
                continue
            # depth-limited walk: env/<name>/lib/framework-espidf or similar
            for candidate in sr.rglob("components/fatfs/src"):
                idf_root = candidate.parent.parent
                if _is_idf_root(idf_root):
                    _LOGGER.debug("sd_mmc_card: IDF root via build tree: %s", idf_root)
                    return idf_root
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("sd_mmc_card: build-tree search failed: %s", exc)

    # ── Strategy 3: PlatformIO package cache ─────────────────────────────────
    pio_home_candidates = [
        os.environ.get("PLATFORMIO_CORE_DIR"),
        os.path.expanduser("~/.platformio"),
        "/root/.platformio",            # HA add-on (runs as root)
        "/data/.platformio",            # some HA setups mount /data
        "/home/pi/.platformio",         # Raspberry Pi
        "/home/user/.platformio",       # generic Docker
        "/usr/local/.platformio",       # some CI images
    ]
    for pio_home in filter(None, pio_home_candidates):
        packages_dir = Path(pio_home) / "packages"
        if not packages_dir.is_dir():
            continue
        # The directory name may include a version suffix after "@" or "-"
        candidates = sorted(
            packages_dir.glob("framework-espidf*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate.is_dir() and _is_idf_root(candidate):
                _LOGGER.debug("sd_mmc_card: IDF root via PIO packages: %s", candidate)
                return candidate

    # ── Strategy 4: filesystem search for esp_vfs_fat.h ─────────────────────
    # Scoped to directories that realistically contain PIO/IDF artefacts.
    search_dirs = ["/root", "/data", "/home", "/usr/local", "/opt"]
    for search_dir in search_dirs:
        if not Path(search_dir).is_dir():
            continue
        try:
            result = subprocess.run(
                ["find", search_dir, "-name", "esp_vfs_fat.h",
                 "-maxdepth", "12", "-print", "-quit"],
                capture_output=True, text=True, timeout=15,
            )
            hit = result.stdout.strip()
            if hit:
                # hit is e.g. /root/.platformio/packages/framework-espidf/components/fatfs/vfs/include/esp_vfs_fat.h
                # IDF root is 5 levels up from the file
                idf_root = Path(hit).parents[4]
                if _is_idf_root(idf_root):
                    _LOGGER.debug("sd_mmc_card: IDF root via filesystem find: %s", idf_root)
                    return idf_root
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("sd_mmc_card: filesystem find failed in %s: %s", search_dir, exc)

    return None


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
        # ESPHome merges external component sources into its own generated IDF
        # component and does NOT honour a component-level CMakeLists.txt, so we
        # must inject the required IDF header directories as explicit -I flags.
        #
        # IMPORTANT: ${IDF_PATH} / $IDF_PATH are NOT expanded by GCC — they are
        # CMake/shell variables that GCC never sees.  We resolve the real
        # absolute path at code-generation time via _find_idf_root() and emit
        # literal -I/abs/path flags.
        idf_root = _find_idf_root()
        if idf_root is None:
            raise cv.Invalid(
                "sd_mmc_card: could not locate the ESP-IDF package.\n"
                "Searched: IDF_PATH env, CORE.build_path/.pioenvs, "
                "~/.platformio, /root/.platformio, /data/.platformio, "
                "and a filesystem search under /root /data /home /opt.\n"
                "Fix: set the IDF_PATH environment variable to your ESP-IDF "
                "root, or run a plain ESPHome build first so PlatformIO "
                "downloads the framework-espidf package."
            )

        _LOGGER.info("sd_mmc_card: using IDF root at %s", idf_root)

        added = []
        for subpath in _IDF_INCLUDE_SUBPATHS:
            full = idf_root / subpath
            if full.is_dir():
                cg.add_build_flag(f"-I{full}")
                added.append(str(full))
            # Silently skip paths that don't exist (IDF 4 vs 5 layout differences)
        _LOGGER.debug("sd_mmc_card: added %d include paths: %s", len(added), added)