import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome import pins, automation

DEPENDENCIES = ["esp32"]
AUTO_LOAD = ["sensor", "text_sensor"]

sd_ns = cg.esphome_ns.namespace("sd_mmc_card")
SdMmcCard = sd_ns.class_("SdMmcCard", cg.Component)

# Actions
WriteFileAction = sd_ns.class_("WriteFileAction", automation.Action)
AppendFileAction = sd_ns.class_("AppendFileAction", automation.Action)
DeleteFileAction = sd_ns.class_("DeleteFileAction", automation.Action)
CreateDirectoryAction = sd_ns.class_("CreateDirectoryAction", automation.Action)
RemoveDirectoryAction = sd_ns.class_("RemoveDirectoryAction", automation.Action)

CONF_CLK_PIN = "clk_pin"
CONF_CMD_PIN = "cmd_pin"
CONF_DATA0_PIN = "data0_pin"
CONF_DATA1_PIN = "data1_pin"
CONF_DATA2_PIN = "data2_pin"
CONF_DATA3_PIN = "data3_pin"
CONF_MODE_1BIT = "mode_1bit"

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(SdMmcCard),
    cv.Required(CONF_CLK_PIN): pins.internal_gpio_output_pin_number,
    cv.Required(CONF_CMD_PIN): pins.internal_gpio_output_pin_number,
    cv.Required(CONF_DATA0_PIN): pins.internal_gpio_pin_number,
    cv.Optional(CONF_DATA1_PIN): pins.internal_gpio_pin_number,
    cv.Optional(CONF_DATA2_PIN): pins.internal_gpio_pin_number,
    cv.Optional(CONF_DATA3_PIN): pins.internal_gpio_pin_number,
    cv.Optional(CONF_MODE_1BIT, default=False): cv.boolean,
}).extend(cv.COMPONENT_SCHEMA)

async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_clk_pin(config[CONF_CLK_PIN]))
    cg.add(var.set_cmd_pin(config[CONF_CMD_PIN]))
    cg.add(var.set_data0_pin(config[CONF_DATA0_PIN]))
    cg.add(var.set_mode_1bit(config[CONF_MODE_1BIT]))

    if not config[CONF_MODE_1BIT]:
        if CONF_DATA1_PIN in config:
            cg.add(var.set_data1_pin(config[CONF_DATA1_PIN]))
        if CONF_DATA2_PIN in config:
            cg.add(var.set_data2_pin(config[CONF_DATA2_PIN]))
        if CONF_DATA3_PIN in config:
            cg.add(var.set_data3_pin(config[CONF_DATA3_PIN]))