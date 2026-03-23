#include "sd_mmc_card.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"

#include "driver/sdmmc_host.h"
#include "driver/sdmmc_defs.h"
#include "sdmmc_cmd.h"
#include "esp_vfs_fat.h"
#include "esp_idf_version.h"
#include "ff.h"
#include <sys/stat.h>
#include <cerrno>
#include <cstring>

namespace esphome {
namespace sd_mmc_card {

static const char *TAG = "sd_mmc_card";
static const char *MOUNT_POINT = "/sdcard";
static const char *FATFS_ROOT = "0:";

static constexpr char kFatfsSeparator = '/';

using FatfsDir = FF_DIR;
using FatfsFileInfo = FILINFO;

static std::string fatfs_path_(const std::string &path) {
  if (path.empty()) {
    return std::string(FATFS_ROOT) + kFatfsSeparator;
  }
  if (path.front() == '/') {
    return std::string(FATFS_ROOT) + path;
  }
  return std::string(FATFS_ROOT) + kFatfsSeparator + path;
}

void SdMmcCard::setup() {
  if (!this->mount_card_()) {
    mark_failed();
    return;
  }
  update_sensors_();
}

bool SdMmcCard::mount_card_() {
  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();

  slot.clk = (gpio_num_t) clk_pin_;
  slot.cmd = (gpio_num_t) cmd_pin_;
  slot.d0  = (gpio_num_t) d0_pin_;
  slot.d1  = mode_1bit_ ? GPIO_NUM_NC : (gpio_num_t) d1_pin_;
  slot.d2  = mode_1bit_ ? GPIO_NUM_NC : (gpio_num_t) d2_pin_;
  slot.d3  = mode_1bit_ ? GPIO_NUM_NC : (gpio_num_t) d3_pin_;
  slot.width = mode_1bit_ ? 1 : 4;

  esp_vfs_fat_sdmmc_mount_config_t mount_cfg{};
  mount_cfg.format_if_mount_failed = false;
  mount_cfg.max_files = 5;

  esp_err_t err = esp_vfs_fat_sdmmc_mount(
      MOUNT_POINT, &host, &slot, &mount_cfg, &card_);

  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Mount failed: %s", esp_err_to_name(err));
    return false;
  }

  update_card_info_();

  ESP_LOGI(TAG, "Mounted at %s (%s-bit, %d kHz)",
           MOUNT_POINT, mode_1bit_ ? "1" : "4", card_freq_khz_);
  return true;
}

bool SdMmcCard::unmount_card_() {
  if (card_ == nullptr) {
    ESP_LOGW(TAG, "Unmount requested but card is not mounted");
    return false;
  }
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
  esp_err_t err = esp_vfs_fat_sdcard_unmount(MOUNT_POINT, card_);
#else
  esp_err_t err = esp_vfs_fat_sdmmc_unmount();
#endif
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Unmount failed: %s", esp_err_to_name(err));
    return false;
  }
  card_ = nullptr;
  ESP_LOGI(TAG, "Unmounted %s", MOUNT_POINT);
  return true;
}

void SdMmcCard::update_card_info_() {
  if (!card_) {
    card_type_ = CardType::UNKNOWN;
    card_freq_khz_ = 0;
    return;
  }

  // FIX: Properly detect MMC, SDSC, SDHC, and SDXC
  if (card_->is_mmc) {
    card_type_ = CardType::MMC;
  } else if (card_->ocr & SD_OCR_SDHC_CAP) {
    // SDXC threshold is > 32 GB; card_->csd.capacity is in 512-byte sectors
    uint64_t capacity_bytes = (uint64_t) card_->csd.capacity * 512ULL;
    card_type_ = (capacity_bytes > 32ULL * 1024 * 1024 * 1024) ? CardType::SDXC : CardType::SDHC;
  } else {
    card_type_ = CardType::SDSC;
  }

  card_freq_khz_ = card_->max_freq_khz;
}

void SdMmcCard::dump_config() {
  ESP_LOGCONFIG(TAG, "SD MMC Card:");
  ESP_LOGCONFIG(TAG, "  Mount: %s", MOUNT_POINT);
  ESP_LOGCONFIG(TAG, "  Mode: %s-bit", mode_1bit_ ? "1" : "4");
  ESP_LOGCONFIG(TAG, "  Card Type: %s", card_type_string_().c_str());
  ESP_LOGCONFIG(TAG, "  Frequency (max): %d kHz", card_freq_khz_);
  ESP_LOGCONFIG(TAG, "  Filesystem: %s", fs_type_string_().c_str());
  ESP_LOGCONFIG(TAG, "  Sensors: total=%s used=%s free=%s freq=%s file_size=%s",
                total_space_sensor_ ? "on" : "off",
                used_space_sensor_ ? "on" : "off",
                free_space_sensor_ ? "on" : "off",
                frequency_sensor_ ? "on" : "off",
                file_size_sensor_ ? "on" : "off");
  ESP_LOGCONFIG(TAG, "  Text Sensors: card_type=%s file_content=%s fs_type=%s",
                card_type_sensor_ ? "on" : "off",
                file_content_sensor_ ? "on" : "off",
                fs_type_sensor_ ? "on" : "off");
}

void SdMmcCard::loop() {
  // FIX: Use member variable instead of static — safe for multiple instances
  uint32_t now = millis();
  if (now - last_update_ > 60000) {
    update_sensors_();
    last_update_ = now;
  }
}

void SdMmcCard::update_sensors_() {
  // FIX: Guard against being called when card is not mounted (e.g. after failed setup)
  if (card_ == nullptr) return;

  if (total_space_sensor_)
    total_space_sensor_->publish_state(total_space());

  if (used_space_sensor_)
    used_space_sensor_->publish_state(used_space());

  if (free_space_sensor_)
    free_space_sensor_->publish_state(free_space());

  if (frequency_sensor_)
    frequency_sensor_->publish_state(card_freq_khz_);

  if (file_size_sensor_ && !file_size_path_.empty())
    file_size_sensor_->publish_state(file_size(file_size_path_));

  if (card_type_sensor_)
    card_type_sensor_->publish_state(card_type_string_());

  if (fs_type_sensor_)
    fs_type_sensor_->publish_state(fs_type_string_());
}

std::string SdMmcCard::card_type_string_() {
  switch (card_type_) {
    case CardType::MMC:  return "MMC";
    case CardType::SDSC: return "SDSC";
    case CardType::SDHC: return "SDHC";
    case CardType::SDXC: return "SDXC";
    default:             return "UNKNOWN";
  }
}

std::string SdMmcCard::fs_type_string_() {
  FATFS *fs = nullptr;
  DWORD free_clust = 0;
  FRESULT result = f_getfree(FATFS_ROOT, &free_clust, &fs);
  if (result != FR_OK || fs == nullptr)
    return "UNKNOWN";

  switch (fs->fs_type) {
    case FS_FAT12: return "FAT12";
    case FS_FAT16: return "FAT16";
    case FS_FAT32: return "FAT32";
#ifdef FS_EXFAT
    case FS_EXFAT: return "exFAT";
#endif
    default: return "UNKNOWN";
  }
}

/* ---------- file ops ---------- */

bool SdMmcCard::write_file(const std::string &path, const std::string &data) {
  // FIX: Use binary mode ("wb") to avoid text-mode line-ending translation
  FILE *f = fopen((std::string(MOUNT_POINT) + path).c_str(), "wb");
  if (!f) {
    ESP_LOGE(TAG, "Write failed to open %s (errno: %d)", path.c_str(), errno);
    return false;
  }
  fwrite(data.data(), 1, data.size(), f);
  fclose(f);
  ESP_LOGI(TAG, "Wrote %u bytes to %s", static_cast<unsigned>(data.size()), path.c_str());

  if (file_size_sensor_ && file_size_path_ == path)
    file_size_sensor_->publish_state(file_size(path));

  return true;
}

bool SdMmcCard::append_file(const std::string &path, const std::string &data) {
  // FIX: Use binary mode ("ab") to avoid text-mode line-ending translation
  FILE *f = fopen((std::string(MOUNT_POINT) + path).c_str(), "ab");
  if (!f) {
    ESP_LOGE(TAG, "Append failed to open %s (errno: %d)", path.c_str(), errno);
    return false;
  }
  fwrite(data.data(), 1, data.size(), f);
  fclose(f);
  ESP_LOGI(TAG, "Appended %u bytes to %s", static_cast<unsigned>(data.size()), path.c_str());

  if (file_size_sensor_ && file_size_path_ == path)
    file_size_sensor_->publish_state(file_size(path));

  return true;
}

bool SdMmcCard::read_file(const std::string &path, std::string &out) {
  // FIX: Use binary mode + fread for full file content (handles binary data,
  //      null bytes, and avoids fgets newline quirks)
  FILE *f = fopen((std::string(MOUNT_POINT) + path).c_str(), "rb");
  if (!f) {
    ESP_LOGE(TAG, "Read failed to open %s (errno: %d)", path.c_str(), errno);
    return false;
  }

  fseek(f, 0, SEEK_END);
  long sz = ftell(f);
  rewind(f);

  out.clear();
  if (sz > 0) {
    // Guard against unexpectedly large files consuming all heap
    if (sz > 65536) {
      ESP_LOGW(TAG, "File %s is %ld bytes — reading only first 65536", path.c_str(), sz);
      sz = 65536;
    }
    out.resize(sz);
    size_t read = fread(&out[0], 1, sz, f);
    out.resize(read);  // trim if fread read less than expected
    ESP_LOGI(TAG, "Read %u bytes from %s", static_cast<unsigned>(read), path.c_str());
  }

  fclose(f);
  return true;
}

bool SdMmcCard::delete_file(const std::string &path) {
  FRESULT result = f_unlink(fatfs_path_(path).c_str());
  bool removed = result == FR_OK;
  if (removed) {
    ESP_LOGI(TAG, "Deleted file %s", path.c_str());
  } else {
    ESP_LOGE(TAG, "Failed to delete file %s (fatfs err: %d)", path.c_str(), result);
  }

  if (removed && file_size_sensor_ && file_size_path_ == path)
    file_size_sensor_->publish_state(0);

  return removed;
}

bool SdMmcCard::format_card() {
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
  // FIX: On IDF 5.x, esp_vfs_fat_sdcard_unmount deinitialises the SDMMC host,
  //      so f_mkfs would have no physical access. Use the IDF 5 built-in
  //      format helper that keeps the host alive.
  if (card_ == nullptr) {
    ESP_LOGE(TAG, "Format failed: card is not mounted");
    return false;
  }
  esp_err_t err = esp_vfs_fat_sdcard_format(MOUNT_POINT, card_);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Format failed: %s", esp_err_to_name(err));
    return false;
  }
  ESP_LOGW(TAG, "Formatted card via esp_vfs_fat_sdcard_format");
  update_card_info_();
  update_sensors_();
  return true;
#else
  // IDF < 5: unmount first, then use f_mkfs directly (host stays alive)
  if (!this->unmount_card_()) {
    return false;
  }
  MKFS_PARM opt{};
  opt.fmt = FM_ANY;
  uint8_t work[4096];
  FRESULT result = f_mkfs(FATFS_ROOT, &opt, work, sizeof(work));
  if (result == FR_OK) {
    ESP_LOGW(TAG, "Formatted card at %s; remounting", FATFS_ROOT);
    if (!this->mount_card_()) {
      ESP_LOGE(TAG, "Remount failed after format");
      return false;
    }
    update_sensors_();
    return true;
  }
  ESP_LOGE(TAG, "Failed to format card at %s (fatfs err: %d)", FATFS_ROOT, result);
  return false;
#endif
}

bool SdMmcCard::remount_card() {
  if (!this->unmount_card_()) {
    return false;
  }
  if (!this->mount_card_()) {
    return false;
  }
  update_sensors_();
  return true;
}

/* ---------- dirs ---------- */

bool SdMmcCard::create_directory(const std::string &path) {
  FRESULT result = f_mkdir(fatfs_path_(path).c_str());
  bool created = result == FR_OK || result == FR_EXIST;
  if (created) {
    ESP_LOGI(TAG, "Directory ready: %s", path.c_str());
  } else {
    ESP_LOGE(TAG, "Failed to create directory %s (fatfs err: %d)", path.c_str(), result);
  }
  return created;
}

bool SdMmcCard::remove_directory(const std::string &path) {
  FRESULT result = f_unlink(fatfs_path_(path).c_str());
  bool removed = result == FR_OK;
  if (removed) {
    ESP_LOGI(TAG, "Removed directory %s", path.c_str());
  } else {
    ESP_LOGE(TAG, "Failed to remove directory %s (fatfs err: %d)", path.c_str(), result);
  }
  return removed;
}

bool SdMmcCard::is_directory(const std::string &path) {
  FatfsFileInfo info{};
  if (f_stat(fatfs_path_(path).c_str(), &info) != FR_OK)
    return false;
  return (info.fattrib & AM_DIR) != 0;
}

size_t SdMmcCard::file_size(const std::string &path) {
  FatfsFileInfo info{};
  if (f_stat(fatfs_path_(path).c_str(), &info) != FR_OK)
    return 0;
  return info.fsize;
}

/* ---------- list directory ---------- */

std::vector<std::string> SdMmcCard::list_directory(const std::string &path, uint8_t depth) {
  std::vector<FileInfo> file_infos;
  scan_dir_(path, depth, file_infos);

  std::vector<std::string> result;
  result.reserve(file_infos.size());
  for (const auto &info : file_infos) {
    result.push_back(info.path);
  }
  ESP_LOGI(TAG, "Listed %u entries under %s (depth: %u)",
           static_cast<unsigned>(result.size()), path.c_str(), depth);
  return result;
}

std::vector<FileInfo> SdMmcCard::list_directory_file_info(const std::string &path, uint8_t depth) {
  std::vector<FileInfo> result;
  scan_dir_(path, depth, result);
  ESP_LOGI(TAG, "Listed %u entries with info under %s (depth: %u)",
           static_cast<unsigned>(result.size()), path.c_str(), depth);
  return result;
}

void SdMmcCard::scan_dir_(const std::string &path, uint8_t depth, std::vector<FileInfo> &out) {
  if (depth == 0) return;

  std::string full_path = fatfs_path_(path);
  ESP_LOGD(TAG, "Scanning directory: %s", full_path.c_str());

  FatfsDir dir{};
  FRESULT result = f_opendir(&dir, full_path.c_str());
  if (result != FR_OK) {
    ESP_LOGE(TAG, "Failed to open directory: %s (fatfs err: %d)", full_path.c_str(), result);
    return;
  }

  FatfsFileInfo file_info{};
  int count = 0;
  while (true) {
    result = f_readdir(&dir, &file_info);
    if (result != FR_OK || file_info.fname[0] == '\0')
      break;
    if (strcmp(file_info.fname, ".") == 0 || strcmp(file_info.fname, "..") == 0)
      continue;

    count++;
    std::string entry_path = path;
    if (!entry_path.empty() && entry_path.back() != kFatfsSeparator)
      entry_path += kFatfsSeparator;
    entry_path += file_info.fname;

    bool is_dir = (file_info.fattrib & AM_DIR) != 0;
    out.push_back(FileInfo{entry_path, static_cast<size_t>(file_info.fsize), is_dir});
    ESP_LOGD(TAG, "  Found: %s (%s, %d bytes)", file_info.fname, is_dir ? "DIR" : "FILE",
             static_cast<int>(file_info.fsize));

    if (is_dir && depth > 1) {
      scan_dir_(entry_path, depth - 1, out);
    }
  }
  ESP_LOGD(TAG, "Total entries found in %s: %d", path.c_str(), count);
  f_closedir(&dir);
}

/* ---------- space ---------- */

uint64_t SdMmcCard::total_space() {
  FATFS *fs = nullptr;
  DWORD free_clust = 0;
  if (f_getfree(FATFS_ROOT, &free_clust, &fs) != FR_OK || fs == nullptr) {
    ESP_LOGE(TAG, "total_space: f_getfree failed");
    return 0;
  }
  // fs->ssize only exists when FF_MAX_SS != FF_MIN_SS (variable sector size).
  // IDF 5.x FatFS uses fixed 512-byte sectors (FF_MIN_SS == FF_MAX_SS == 512),
  // so the ssize field is not present in the FATFS struct.
#if FF_MAX_SS != FF_MIN_SS
  return (uint64_t) fs->n_fatent * fs->csize * fs->ssize;
#else
  return (uint64_t) fs->n_fatent * fs->csize * FF_MIN_SS;
#endif
}

uint64_t SdMmcCard::free_space() {
  FATFS *fs = nullptr;
  DWORD free_clust = 0;
  if (f_getfree(FATFS_ROOT, &free_clust, &fs) != FR_OK || fs == nullptr) {
    ESP_LOGE(TAG, "free_space: f_getfree failed");
    return 0;
  }
#if FF_MAX_SS != FF_MIN_SS
  return (uint64_t) free_clust * fs->csize * fs->ssize;
#else
  return (uint64_t) free_clust * fs->csize * FF_MIN_SS;
#endif
}

uint64_t SdMmcCard::used_space() {
  return total_space() - free_space();
}

}  // namespace sd_mmc_card
}  // namespace esphome