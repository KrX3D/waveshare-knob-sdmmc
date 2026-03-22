import os
import glob
import logging
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
# IDF 5.x moved sdmmc_host.h into esp_driver_sdmmc/; listing both paths is
# safe because a -I for a directory that doesn't exist is silently ignored.
_IDF_INCLUDE_SUBPATHS = [
    "components/fatfs/vfs/include",   # esp_vfs_fat.h
    "components/fatfs/src",           # ff.h, ffconf.h
    "components/sdmmc/include",       # sdmmc_cmd.h, driver/sdmmc_defs.h
    "components/esp_driver_sdmmc/include",  # driver/sdmmc_host.h  (IDF 5+)
    "components/driver/include",            # driver/sdmmc_host.h  (IDF 4 compat)
]


def _find_idf_components_root() -> Path | None:
    """
    Locate the ESP-IDF root (containing components/) from the PlatformIO
    framework-espidf package cache.  Returns the root Path or None.

    Search order:
      1. IDF_PATH environment variable  (standalone IDF install, CI, etc.)
      2. PLATFORMIO_CORE_DIR / packages / framework-espidf*
      3. ~/.platformio / packages / framework-espidf*   (default PIO location)
         This also covers the HA add-on where home = /root.
    """
    # 1. Explicit IDF_PATH
    env_idf = os.environ.get("IDF_PATH")
    if env_idf:
        p = Path(env_idf)
        if (p / "components").is_dir():
            return p

    # 2 + 3. PlatformIO package cache
    pio_homes = [
        os.environ.get("PLATFORMIO_CORE_DIR"),   # custom location
        os.path.expanduser("~/.platformio"),      # default (Linux/Mac/Docker/HA)
        "/root/.platformio",                      # explicit HA add-on path
        "/home/user/.platformio",                 # some Docker images
    ]
    for pio_home in filter(None, pio_homes):
        packages_dir = Path(pio_home) / "packages"
        if not packages_dir.is_dir():
            continue
        # The package may have a version suffix, e.g. "framework-espidf@3.50503.0".
        # Take the most recently modified matching directory.
        candidates = sorted(
            packages_dir.glob("framework-espidf*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate.is_dir() and (candidate / "components").is_dir():
                return candidate

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
        # ${IDF_PATH} / $IDF_PATH are NOT expanded by GCC — they are CMake/shell
        # variables that GCC never sees.  We must resolve the real absolute path
        # at code-generation time and emit a literal -I/abs/path flag.
        idf_root = _find_idf_components_root()
        if idf_root is None:
            raise cv.Invalid(
                "sd_mmc_card: could not locate the ESP-IDF package in the "
                "PlatformIO cache.  Set the IDF_PATH environment variable to "
                "your ESP-IDF installation directory, or ensure the "
                "framework-espidf package has been downloaded by running a "
                "build once with the standard ESPHome esp-idf framework."
            )

        _LOGGER.debug("sd_mmc_card: using IDF root at %s", idf_root)

        for subpath in _IDF_INCLUDE_SUBPATHS:
            full = idf_root / subpath
            if full.is_dir():
                cg.add_build_flag(f"-I{full}")
                _LOGGER.debug("sd_mmc_card: added include path %s", full)
            # Silently skip paths that don't exist (e.g. IDF 4 vs 5 differences)