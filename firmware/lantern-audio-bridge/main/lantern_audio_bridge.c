/*
 * SPDX-FileCopyrightText: 2026 SAGE Commons contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * USB Audio Class bridge for the Waveshare ESP32-S3-AUDIO-Board v1.1 used by
 * SAGE Lantern. The pin assignments are taken from the board schematic.
 */

#include <stdbool.h>
#include <inttypes.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_log.h"
#include "usb_device_uac.h"

#define LANTERN_SAMPLE_RATE 16000
#define LANTERN_CHANNELS 2
#define LANTERN_CODEC_BITS 32
#define LANTERN_I2C_PORT I2C_NUM_0
#define LANTERN_I2C_SCL GPIO_NUM_10
#define LANTERN_I2C_SDA GPIO_NUM_11

#define LANTERN_I2S_PORT I2S_NUM_1
#define LANTERN_I2S_MCLK GPIO_NUM_12
#define LANTERN_I2S_BCLK GPIO_NUM_13
#define LANTERN_I2S_WS GPIO_NUM_14
#define LANTERN_I2S_DIN GPIO_NUM_15
#define LANTERN_I2S_DOUT GPIO_NUM_16

/* Ten milliseconds is 640 UAC bytes. Leave headroom for scheduling jitter. */
#define LANTERN_MAX_UAC_BYTES 2048
#define LANTERN_MAX_CODEC_BYTES (LANTERN_MAX_UAC_BYTES * 2)

static const char *TAG = "lantern_audio";

typedef struct {
    i2c_master_bus_handle_t i2c_bus;
    i2s_chan_handle_t tx_channel;
    i2s_chan_handle_t rx_channel;
    esp_codec_dev_handle_t speaker;
    esp_codec_dev_handle_t microphone;
    volatile bool muted;
    int32_t speaker_samples[LANTERN_MAX_UAC_BYTES / sizeof(int16_t)];
    int16_t microphone_raw[LANTERN_MAX_CODEC_BYTES / sizeof(int16_t)];
} lantern_audio_t;

static lantern_audio_t s_audio;

static esp_err_t init_i2c(lantern_audio_t *audio)
{
    const i2c_master_bus_config_t config = {
        .i2c_port = LANTERN_I2C_PORT,
        .sda_io_num = LANTERN_I2C_SDA,
        .scl_io_num = LANTERN_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    return i2c_new_master_bus(&config, &audio->i2c_bus);
}

static esp_err_t init_i2s(lantern_audio_t *audio)
{
    i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(LANTERN_I2S_PORT, I2S_ROLE_MASTER);
    ESP_RETURN_ON_ERROR(
        i2s_new_channel(&channel_config, &audio->tx_channel, &audio->rx_channel),
        TAG, "create I2S channel");

    const i2s_std_config_t stream_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(LANTERN_SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = LANTERN_I2S_MCLK,
            .bclk = LANTERN_I2S_BCLK,
            .ws = LANTERN_I2S_WS,
            .dout = LANTERN_I2S_DOUT,
            .din = LANTERN_I2S_DIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(audio->tx_channel, &stream_config),
                        TAG, "configure I2S transmit");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(audio->rx_channel, &stream_config),
                        TAG, "configure I2S receive");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(audio->tx_channel), TAG, "enable I2S transmit");
    return i2s_channel_enable(audio->rx_channel);
}

static esp_err_t init_microphone(lantern_audio_t *audio)
{
    audio_codec_i2s_cfg_t data_config = {
        .port = LANTERN_I2S_PORT,
        .rx_handle = audio->rx_channel,
        .tx_handle = NULL,
    };
    const audio_codec_data_if_t *data = audio_codec_new_i2s_data(&data_config);
    ESP_RETURN_ON_FALSE(data != NULL, ESP_ERR_NO_MEM, TAG, "create microphone data interface");

    audio_codec_i2c_cfg_t control_config = {
        .addr = ES7210_CODEC_DEFAULT_ADDR,
        .bus_handle = audio->i2c_bus,
    };
    const audio_codec_ctrl_if_t *control = audio_codec_new_i2c_ctrl(&control_config);
    ESP_RETURN_ON_FALSE(control != NULL, ESP_ERR_NO_MEM, TAG, "create microphone control interface");

    es7210_codec_cfg_t codec_config = {
        .ctrl_if = control,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 |
                        ES7210_SEL_MIC3 | ES7210_SEL_MIC4,
    };
    const audio_codec_if_t *codec = es7210_codec_new(&codec_config);
    ESP_RETURN_ON_FALSE(codec != NULL, ESP_ERR_NO_MEM, TAG, "create ES7210 codec");

    esp_codec_dev_cfg_t device_config = {
        .codec_if = codec,
        .data_if = data,
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
    };
    audio->microphone = esp_codec_dev_new(&device_config);
    ESP_RETURN_ON_FALSE(audio->microphone != NULL, ESP_ERR_NO_MEM, TAG,
                        "create microphone device");

    esp_codec_dev_sample_info_t format = {
        .sample_rate = LANTERN_SAMPLE_RATE,
        .channel = LANTERN_CHANNELS,
        .bits_per_sample = LANTERN_CODEC_BITS,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(audio->microphone, &format), TAG,
                        "open microphone");
    return esp_codec_dev_set_in_gain(audio->microphone, 30.0f);
}

static esp_err_t init_speaker(lantern_audio_t *audio)
{
    audio_codec_i2s_cfg_t data_config = {
        .port = LANTERN_I2S_PORT,
        .rx_handle = NULL,
        .tx_handle = audio->tx_channel,
    };
    const audio_codec_data_if_t *data = audio_codec_new_i2s_data(&data_config);
    ESP_RETURN_ON_FALSE(data != NULL, ESP_ERR_NO_MEM, TAG, "create speaker data interface");

    audio_codec_i2c_cfg_t control_config = {
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = audio->i2c_bus,
    };
    const audio_codec_ctrl_if_t *control = audio_codec_new_i2c_ctrl(&control_config);
    ESP_RETURN_ON_FALSE(control != NULL, ESP_ERR_NO_MEM, TAG, "create speaker control interface");

    const audio_codec_gpio_if_t *gpio = audio_codec_new_gpio();
    ESP_RETURN_ON_FALSE(gpio != NULL, ESP_ERR_NO_MEM, TAG, "create speaker GPIO interface");

    es8311_codec_cfg_t codec_config = {
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .ctrl_if = control,
        .gpio_if = gpio,
        .pa_pin = GPIO_NUM_NC,
        .use_mclk = false,
    };
    const audio_codec_if_t *codec = es8311_codec_new(&codec_config);
    ESP_RETURN_ON_FALSE(codec != NULL, ESP_ERR_NO_MEM, TAG, "create ES8311 codec");

    esp_codec_dev_cfg_t device_config = {
        .codec_if = codec,
        .data_if = data,
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
    };
    audio->speaker = esp_codec_dev_new(&device_config);
    ESP_RETURN_ON_FALSE(audio->speaker != NULL, ESP_ERR_NO_MEM, TAG,
                        "create speaker device");

    esp_codec_dev_sample_info_t format = {
        .sample_rate = LANTERN_SAMPLE_RATE,
        .channel = LANTERN_CHANNELS,
        .bits_per_sample = LANTERN_CODEC_BITS,
    };
    ESP_RETURN_ON_ERROR(esp_codec_dev_open(audio->speaker, &format), TAG, "open speaker");
    return esp_codec_dev_set_out_vol(audio->speaker, 60);
}

static esp_err_t uac_speaker_write(uint8_t *buffer, size_t length, void *context)
{
    lantern_audio_t *audio = context;
    if (length > LANTERN_MAX_UAC_BYTES || (length % sizeof(int16_t)) != 0) {
        return ESP_ERR_INVALID_SIZE;
    }

    const int16_t *source = (const int16_t *)buffer;
    const size_t sample_count = length / sizeof(*source);
    for (size_t i = 0; i < sample_count; ++i) {
        audio->speaker_samples[i] =
            audio->muted ? 0 : (int32_t)source[i] * 65536;
    }
    return esp_codec_dev_write(audio->speaker, audio->speaker_samples,
                               sample_count * sizeof(audio->speaker_samples[0]));
}

static esp_err_t uac_microphone_read(uint8_t *buffer, size_t length,
                                     size_t *bytes_read, void *context)
{
    lantern_audio_t *audio = context;
    if (length > LANTERN_MAX_UAC_BYTES ||
        (length % (LANTERN_CHANNELS * sizeof(int16_t))) != 0) {
        *bytes_read = 0;
        return ESP_ERR_INVALID_SIZE;
    }

    const size_t codec_bytes = length * 2;
    esp_err_t result = esp_codec_dev_read(audio->microphone, audio->microphone_raw,
                                          codec_bytes);
    if (result != ESP_OK) {
        *bytes_read = 0;
        return result;
    }

    int16_t *destination = (int16_t *)buffer;
    const size_t frame_count = length / (LANTERN_CHANNELS * sizeof(int16_t));
    for (size_t frame = 0; frame < frame_count; ++frame) {
        destination[2 * frame] = audio->microphone_raw[4 * frame + 1];
        destination[2 * frame + 1] = audio->microphone_raw[4 * frame + 3];
    }
    *bytes_read = length;
    return ESP_OK;
}

static void uac_set_mute(uint32_t mute, void *context)
{
    lantern_audio_t *audio = context;
    audio->muted = mute != 0;
}

static void uac_set_volume(uint32_t volume, void *context)
{
    /* UAC volume is signed 16.16 dB; retain the safe board default for now. */
    (void)context;
    ESP_LOGI(TAG, "host volume request: 0x%08" PRIx32, volume);
}

void app_main(void)
{
    ESP_LOGI(TAG, "starting SAGE Lantern USB audio bridge");
    ESP_ERROR_CHECK(init_i2c(&s_audio));
    ESP_ERROR_CHECK(init_i2s(&s_audio));
    ESP_ERROR_CHECK(init_microphone(&s_audio));
    ESP_ERROR_CHECK(init_speaker(&s_audio));

    uac_device_config_t usb_config = {
        .skip_tinyusb_init = false,
        .output_cb = uac_speaker_write,
        .input_cb = uac_microphone_read,
        .set_mute_cb = uac_set_mute,
        .set_volume_cb = uac_set_volume,
        .cb_ctx = &s_audio,
    };
    ESP_ERROR_CHECK(uac_device_init(&usb_config));
    ESP_LOGI(TAG, "USB audio ready: 16 kHz, stereo, signed 16-bit PCM");
}
