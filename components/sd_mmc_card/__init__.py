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

_IDF_INCLUDE_SUBPATHS = [
    "components/fatfs/vfs/include",        # esp_vfs_fat.h
    "components/fatfs/src",                # ff.h, ffconf.h
    "components/sdmmc/include",            # sdmmc_cmd.h, driver/sdmmc_defs.h
    "components/esp_driver_sdmmc/include", # driver/sdmmc_host.h  (IDF 5+)
    "components/driver/include",           # driver/sdmmc_host.h  (IDF 4 compat)
]


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _is_idf_root(p):
    return (p / "components" / "fatfs" / "src").is_dir()


def _glob_framework(packages_dir):
    """Return the most recently modified framework-espidf* dir under packages_dir, or None."""
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
            _LOGGER.info("sd_mmc_card: IDF root from IDF_PATH: %s", p)
            return p

    # 2. Explicit package directories — /data/cache/platformio is the HA
    #    add-on's actual package storage location (revealed by the tool-esptoolpy
    #    error: '/data/cache/platformio/packages/tool-esptoolpy')
    packages_dirs = list(filter(None, [
        # ── HA add-on specific ──────────────────────────────────────────────
        "/data/cache/platformio/packages",       # confirmed HA add-on location
        "/data/cache/packages",
        "/data/platformio/packages",
        # ── Standard PlatformIO home locations ─────────────────────────────
        os.environ.get("PLATFORMIO_PACKAGES_DIR"),
        os.path.join(os.environ.get("PLATFORMIO_CORE_DIR", ""), "packages") or None,
        "/root/.platformio/packages",
        "/root/.pio/packages",
        "/data/.platformio/packages",
        "/data/.pio/packages",
        "/config/.platformio/packages",
        "/esphome/.platformio/packages",
        "/usr/local/.platformio/packages",
        "/home/pi/.platformio/packages",
        "/home/user/.platformio/packages",
        "/tmp/.platformio/packages",
    ]))

    for pkg_dir in packages_dirs:
        if not pkg_dir:
            continue
        result = _glob_framework(pkg_dir)
        if result:
            _LOGGER.info("sd_mmc_card: IDF root via packages dir %s: %s", pkg_dir, result)
            return result

    # 3. Search the ESPHome build tree (works after first PIO run)
    try:
        for hit in Path(CORE.build_path).rglob("components/fatfs/src"):
            candidate = hit.parent.parent
            if _is_idf_root(candidate):
                _LOGGER.info("sd_mmc_card: IDF root via build tree: %s", candidate)
                return candidate
    except Exception as exc:
        _LOGGER.debug("sd_mmc_card: build tree search failed: %s", exc)

    # 4. Full filesystem search — last resort
    _LOGGER.warning("sd_mmc_card: trying full filesystem search for esp_vfs_fat.h ...")
    hit = _run(
        ["find", "/",
         "-path", "/proc", "-prune", "-o",
         "-path", "/sys",  "-prune", "-o",
         "-path", "/dev",  "-prune", "-o",
         "-name", "esp_vfs_fat.h", "-print", "-quit"],
        timeout=60,
    )
    if hit:
        try:
            idf_root = Path(hit).parents[4]
            if _is_idf_root(idf_root):
                _LOGGER.info("sd_mmc_card: IDF root via filesystem find: %s", idf_root)
                return idf_root
        except Exception as exc:
            _LOGGER.debug("sd_mmc_card: could not derive root from %s: %s", hit, exc)

    # Diagnostics on total failure
    def _ls(p):
        try:
            return [str(x) for x in Path(p).iterdir()]
        except Exception:
            return f"<error listing {p}>"

    _LOGGER.error(
        "\n=== sd_mmc_card: COULD NOT LOCATE ESP-IDF ===\n"
        "  Diagnostic info:\n"
        "    IDF_PATH env              = %s\n"
        "    PLATFORMIO_CORE_DIR env   = %s\n"
        "    PLATFORMIO_PACKAGES_DIR   = %s\n"
        "    HOME env                  = %s\n"
        "    CORE.build_path           = %s\n"
        "    full find result          = %s\n"
        "    /data contents            = %s\n"
        "    /data/cache contents      = %s\n"
        "    /root contents            = %s\n"
        "    which pio                 = %s\n"
        "==============================================",
        os.environ.get("IDF_PATH", "not set"),
        os.environ.get("PLATFORMIO_CORE_DIR", "not set"),
        os.environ.get("PLATFORMIO_PACKAGES_DIR", "not set"),
        os.environ.get("HOME", "not set"),
        getattr(CORE, "build_path", "unknown"),
        hit if hit else "not found",
        _ls("/data"),
        _ls("/data/cache"),
        _ls("/root"),
        _run(["which", "pio"]),
    )
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
        idf_root = _find_idf_root()
        if idf_root is not None:
            added = []
            for subpath in _IDF_INCLUDE_SUBPATHS:
                full = idf_root / subpath
                if full.is_dir():
                    cg.add_build_flag(f"-I{full}")
                    added.append(str(full))
            _LOGGER.info("sd_mmc_card: injected %d IDF include paths from %s", len(added), idf_root)
        # If None: diagnostics already logged, compile will fail with the
        # missing-header error. Add /data/cache/platformio/packages to the
        # search list above if a new location appears in the diagnostic output.