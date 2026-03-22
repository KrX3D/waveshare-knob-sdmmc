import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome import pins
from esphome.core import CORE

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

    if CORE.using_esp_idf:
        # ESPHome merges all external component sources into its own generated IDF
        # component and does NOT process a component-level CMakeLists.txt — so the
        # required IDF header paths must be injected here as explicit build flags.
        #
        # $IDF_PATH / ${IDF_PATH} is set by PlatformIO during the esp-idf build and
        # resolves to the unpacked framework-espidf package directory.
        #
        # IDF 5.x header locations relevant to this component:
        #   esp_vfs_fat.h  → fatfs/vfs/include/
        #   ff.h           → fatfs/src/
        #   sdmmc_cmd.h    → sdmmc/include/
        #   sdmmc_host.h   → esp_driver_sdmmc/include/ (IDF 5+)
        #                    driver/include/           (IDF 4 compat shim)
        for flag in [
            "-I${IDF_PATH}/components/fatfs/vfs/include",
            "-I${IDF_PATH}/components/fatfs/src",
            "-I${IDF_PATH}/components/sdmmc/include",
            # IDF 5.x moved sdmmc_host.h to the new esp_driver_sdmmc component.
            # Listing both is safe — a -I path that doesn't exist is silently ignored.
            "-I${IDF_PATH}/components/esp_driver_sdmmc/include",
            "-I${IDF_PATH}/components/driver/include",
        ]:
            cg.add_build_flag(flag)