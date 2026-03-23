import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor
from esphome.const import (
    CONF_ID,
    CONF_TYPE,
    STATE_CLASS_MEASUREMENT,
)
from . import SdMmcCard, sd_ns

CONF_SD_MMC_CARD_ID = "sd_mmc_card_id"
CONF_PATH = "path"

SENSOR_TYPES = {
    "total_space": "Total Space",
    "used_space": "Used Space", 
    "free_space": "Free Space",
    "frequency": "Card Frequency",
    "file_size": "File Size",
}

CONFIG_SCHEMA = sensor.sensor_schema(
    accuracy_decimals=0,
    state_class=STATE_CLASS_MEASUREMENT,
).extend({
    cv.GenerateID(CONF_SD_MMC_CARD_ID): cv.use_id(SdMmcCard),
    cv.Required(CONF_TYPE): cv.enum(SENSOR_TYPES, lower=True),
    cv.Optional(CONF_PATH): cv.string,
})

async def to_code(config):
    parent = await cg.get_variable(config[CONF_SD_MMC_CARD_ID])
    sens = await sensor.new_sensor(config)
    
    sensor_type = config[CONF_TYPE]
    
    if sensor_type == "total_space":
        cg.add(parent.register_total_space_sensor(sens))
    elif sensor_type == "used_space":
        cg.add(parent.register_used_space_sensor(sens))
    elif sensor_type == "free_space":
        cg.add(parent.register_free_space_sensor(sens))
    elif sensor_type == "frequency":
        cg.add(parent.register_frequency_sensor(sens))
    elif sensor_type == "file_size":
        if CONF_PATH not in config:
            raise cv.Invalid(f"'path' is required for file_size sensor")
        path = config[CONF_PATH]
        cg.add(parent.register_file_size_sensor(sens, path))