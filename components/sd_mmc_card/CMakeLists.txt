# Declares the IDF component dependencies required by sd_mmc_card.
# Without this, esp_vfs_fat.h, ff.h, sdmmc_cmd.h etc. are not on the
# include path when building with ESPHome's esp-idf framework.
idf_component_register(
    SRCS
        "sd_mmc_card.cpp"
    INCLUDE_DIRS
        "."
    REQUIRES
        fatfs       # esp_vfs_fat.h, ff.h, ffconf.h
        sdmmc       # sdmmc_cmd.h, driver/sdmmc_host.h, driver/sdmmc_defs.h
        vfs         # esp_vfs.h (pulled in transitively by esp_vfs_fat.h)
        driver      # gpio_num_t and related types
        log         # ESP_LOGE / ESP_LOGI macros
        esp_common  # esp_err_to_name, ESP_IDF_VERSION macros
)