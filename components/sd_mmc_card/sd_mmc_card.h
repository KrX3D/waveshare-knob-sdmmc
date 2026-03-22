#pragma once

#include "esphome/core/component.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/text_sensor/text_sensor.h"

#include "sdmmc_cmd.h"

#include <vector>
#include <string>

namespace esphome {
namespace sd_mmc_card {

enum class CardType {
  UNKNOWN,
  MMC,
  SDSC,
  SDHC,
  SDXC
};

struct FileInfo {
  std::string path;
  size_t size;
  bool is_directory;
};

class SdMmcCard : public Component {
 public:
  void setup() override;
  void dump_config() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  // config
  void set_clk_pin(uint8_t pin) { clk_pin_ = pin; }
  void set_cmd_pin(uint8_t pin) { cmd_pin_ = pin; }
  void set_data0_pin(uint8_t pin) { d0_pin_ = pin; }
  void set_data1_pin(uint8_t pin) { d1_pin_ = pin; }
  void set_data2_pin(uint8_t pin) { d2_pin_ = pin; }
  void set_data3_pin(uint8_t pin) { d3_pin_ = pin; }
  void set_mode_1bit(bool v) { mode_1bit_ = v; }

  // file operations
  bool write_file(const std::string &path, const std::string &data);
  bool append_file(const std::string &path, const std::string &data);
  bool read_file(const std::string &path, std::string &out);
  bool delete_file(const std::string &path);
  bool format_card();
  bool remount_card();

  bool create_directory(const std::string &path);
  bool remove_directory(const std::string &path);

  size_t file_size(const std::string &path);
  bool is_directory(const std::string &path);

  std::vector<std::string> list_directory(const std::string &path, uint8_t depth);
  std::vector<FileInfo> list_directory_file_info(const std::string &path, uint8_t depth);

  // space
  uint64_t total_space();
  uint64_t free_space();
  uint64_t used_space();

  CardType card_type() const { return card_type_; }

  // sensor registration
  void register_total_space_sensor(sensor::Sensor *s) { total_space_sensor_ = s; }
  void register_used_space_sensor(sensor::Sensor *s) { used_space_sensor_ = s; }
  void register_free_space_sensor(sensor::Sensor *s) { free_space_sensor_ = s; }
  void register_frequency_sensor(sensor::Sensor *s) { frequency_sensor_ = s; }
  void register_file_size_sensor(sensor::Sensor *s, const std::string &path) {
    file_size_sensor_ = s;
    file_size_path_ = path;
  }

  // text sensor registration
  void register_card_type_text_sensor(text_sensor::TextSensor *ts) { card_type_sensor_ = ts; }
  void register_file_content_text_sensor(text_sensor::TextSensor *ts) { file_content_sensor_ = ts; }
  void register_fs_type_text_sensor(text_sensor::TextSensor *ts) { fs_type_sensor_ = ts; }

 protected:
  bool mount_card_();
  bool unmount_card_();
  void update_card_info_();

  void scan_dir_(const std::string &path, uint8_t depth, std::vector<FileInfo> &out);
  void update_sensors_();
  std::string card_type_string_();
  std::string fs_type_string_();

  uint8_t clk_pin_{0}, cmd_pin_{0}, d0_pin_{0}, d1_pin_{0}, d2_pin_{0}, d3_pin_{0};
  bool mode_1bit_{false};

  CardType card_type_{CardType::UNKNOWN};
  sdmmc_card_t *card_{nullptr};
  uint32_t card_freq_khz_{0};

  // loop timer — per-instance (not static) to support multiple component instances
  uint32_t last_update_{0};

  // sensors
  sensor::Sensor *total_space_sensor_{nullptr};
  sensor::Sensor *used_space_sensor_{nullptr};
  sensor::Sensor *free_space_sensor_{nullptr};
  sensor::Sensor *frequency_sensor_{nullptr};
  sensor::Sensor *file_size_sensor_{nullptr};
  std::string file_size_path_;

  // text sensors
  text_sensor::TextSensor *card_type_sensor_{nullptr};
  text_sensor::TextSensor *file_content_sensor_{nullptr};
  text_sensor::TextSensor *fs_type_sensor_{nullptr};

};

}  // namespace sd_mmc_card
}  // namespace esphome