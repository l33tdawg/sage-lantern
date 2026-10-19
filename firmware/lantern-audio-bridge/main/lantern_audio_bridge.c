/*
 * SPDX-FileCopyrightText: 2026 SAGE Commons contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * USB Audio Class bridge for the Waveshare ESP32-S3-AUDIO-Board v1.1 used by
 * SAGE Lantern. The pin assignments are taken from the board schematic.
 */

#include <stdbool.h>
#include <stdlib.h>
#include <inttypes.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_log.h"
#include "usb_device_uac.h"
#include "led_strip.h"
#include "esp_io_expander_tca95xx_16bit.h"

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
    /* Diagnostics: how many times audio actually reached the codec. */
    uint32_t write_count;

    /* Visual diagnostics. The console is on UART0, which is not wired over USB,
       so a failure here is otherwise invisible: the board can enumerate, accept
       the whole stream and stay silent with nothing to inspect. These LEDs are on
       the ESP32-S3 itself (GPIO 38), independent of USB and of the codec:
         dark            = output_cb was never called, or the codec write failed
         lit, following  = samples are reaching the codec, brightness == peak level
    */
    led_strip_handle_t led_strip;
    esp_io_expander_handle_t expander;
    uint32_t led_error_count;
    int16_t microphone_raw[LANTERN_MAX_CODEC_BYTES / sizeof(int16_t)];
} lantern_audio_t;

static lantern_audio_t s_audio;


/* The TCA9555 IO expander on the shared I2C bus. Waveshare's reference firmware
 * initialises it and this firmware did not, which is very likely why the board
 * accepted audio and stayed silent.
 *
 * The expander powers up with every pin as an INPUT (its config register reads
 * 0xFF), so whatever it drives - the codec reset and the power-amplifier enable
 * among them - is left floating. A public register dump of this exact board shows
 * the difference: working state has the config register at 0x9C and the failing
 * cold-boot state at 0xFF. The symptom reported upstream is precisely ours - the
 * board comes up "dead" with the amplifier never enabled.
 *
 * Pins: 0,1,5,6,8 are outputs and 2,9,10,11 are inputs, matching Waveshare's own
 * driver. The outputs are then driven HIGH, which is the state the board is in
 * when it works.
 */
static esp_err_t init_expander(lantern_audio_t *audio)
{
    esp_err_t err = esp_io_expander_new_i2c_tca95xx_16bit(
        audio->i2c_bus, ESP_IO_EXPANDER_I2C_TCA9555_ADDRESS_000, &audio->expander);
    if (err != ESP_OK) {
        return err;
    }
    const uint32_t outputs = IO_EXPANDER_PIN_NUM_0 | IO_EXPANDER_PIN_NUM_1 |
                             IO_EXPANDER_PIN_NUM_5 | IO_EXPANDER_PIN_NUM_6 |
                             IO_EXPANDER_PIN_NUM_8;
    const uint32_t inputs = IO_EXPANDER_PIN_NUM_2 | IO_EXPANDER_PIN_NUM_9 |
                            IO_EXPANDER_PIN_NUM_10 | IO_EXPANDER_PIN_NUM_11;
    err = esp_io_expander_set_dir(audio->expander, outputs, IO_EXPANDER_OUTPUT);
    if (err != ESP_OK) {
        return err;
    }
    err = esp_io_expander_set_dir(audio->expander, inputs, IO_EXPANDER_INPUT);
    if (err != ESP_OK) {
        return err;
    }
    /* Drive the outputs high. Until this happens the amplifier enable is left
       floating, which is the state in which the board makes no sound. */
    return esp_io_expander_set_level(audio->expander, outputs, 1);
}

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
        /* The ESP32-S3 has no audio PLL, so the default clock source stands. */
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
        /* Leave mclk_div at its default. It was tempting to set it to
           bits*channels (64) on the theory that, with no MCLK pin, the codec's
           clock is BCLK. But Waveshare's own working 16 kHz / 32-bit-stereo
           example leaves this at the 256 default and runs identically, so 256 is
           demonstrably correct for this hardware and changing it would be a
           divergence from the only configuration known to produce sound.
           The silence is therefore still unexplained — see the note in the
           firmware README; do not re-apply the mclk_div theory without evidence. */
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
    /* Waveshare's working example sets the output volume *before* opening the
       device, so match that ordering rather than setting it afterwards. */
    ESP_RETURN_ON_ERROR(esp_codec_dev_set_out_vol(audio->speaker, 100), TAG,
                        "set speaker volume");
    return esp_codec_dev_open(audio->speaker, &format);
}

static esp_err_t uac_speaker_write(uint8_t *buffer, size_t length, void *context)
{
    lantern_audio_t *audio = context;
    if (length > LANTERN_MAX_UAC_BYTES || (length % sizeof(int16_t)) != 0) {
        return ESP_ERR_INVALID_SIZE;
    }

    const int16_t *source = (const int16_t *)buffer;
    const size_t sample_count = length / sizeof(*source);
    int peak = 0;
    for (size_t i = 0; i < sample_count; ++i) {
        int v = source[i];
        if (v < 0) v = -v;
        if (v > peak) peak = v;
        audio->speaker_samples[i] =
            audio->muted ? 0 : (int32_t)source[i] * 65536;
    }
    esp_err_t result = esp_codec_dev_write(audio->speaker, audio->speaker_samples,
                                           sample_count * sizeof(audio->speaker_samples[0]));
    /* Report the first few writes and any failure, so "no sound" can be told apart
       from "the codec was never asked to play anything". */
    if (audio->write_count < 3 || result != ESP_OK) {
        ESP_LOGI(TAG, "speaker write #%u: %u bytes, peak=%d, muted=%d, rc=%s",
                 (unsigned)audio->write_count, (unsigned)length, peak,
                 (int)audio->muted, esp_err_to_name(result));
    }
    if (audio->led_strip) {
        if (result == ESP_OK && peak > 0) {
            /* Green, brightness scaled by the loudest sample in this chunk. */
            uint32_t level = (uint32_t)peak * 8 / 32768;
            if (level < 1) level = 1;
            if (level > 8) level = 8;
            led_strip_set_pixel(audio->led_strip, 0, 0, level, 0);
        } else {
            /* Red: the codec rejected the write. */
            audio->led_error_count++;
            led_strip_set_pixel(audio->led_strip, 0, 8, 0, 0);
        }
        led_strip_refresh(audio->led_strip);
    }
    audio->write_count++;
    return result;
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

/*
 * Every init below used to be ESP_ERROR_CHECK, which aborts on failure and
 * reboots the chip into its ROM USB-serial interface. The only externally visible
 * symptom of a failure was therefore "the board never appears as a USB audio
 * device" — with no indication of which stage broke, because the abort happens
 * before anything can be logged. The board presented exactly that symptom.
 *
 * So each stage is now run and reported individually. A failure is still fatal
 * (there is no useful bridge without the codec), but it now says *which* stage
 * failed and with what error code, over the same serial console.
 */
static void run_stage(const char *name, esp_err_t (*stage)(lantern_audio_t *))
{
    esp_err_t result = stage(&s_audio);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "FAILED at %s: %s (0x%x)", name, esp_err_to_name(result), result);
        ESP_LOGE(TAG, "bridge not started; see the message above for the failing stage");
        abort();
    }
    ESP_LOGI(TAG, "ok: %s", name);
}

void app_main(void)
{
    ESP_LOGI(TAG, "starting SAGE Lantern USB audio bridge");
    run_stage("i2c bus", init_i2c);
    run_stage("TCA9555 expander (amplifier enable)", init_expander);
    run_stage("i2s bus", init_i2s);
    run_stage("ES7210 microphone", init_microphone);
    run_stage("ES8311 speaker", init_speaker);

    /* Bring up the diagnostic LEDs before USB so a failure is visible even if
       the bridge never reaches its own callbacks. */
    led_strip_config_t strip_config = {
        .strip_gpio_num = 38,
        .max_leds = 7,
    };
    led_strip_rmt_config_t rmt_config = { .resolution_hz = 10 * 1000 * 1000 };
    if (led_strip_new_rmt_device(&strip_config, &rmt_config, &s_audio.led_strip) != ESP_OK) {
        s_audio.led_strip = NULL;
    } else {
        led_strip_set_pixel(s_audio.led_strip, 0, 0, 4, 4);  /* cyan = init started */
        led_strip_refresh(s_audio.led_strip);
    }

    uac_device_config_t usb_config = {
        .skip_tinyusb_init = false,
        .output_cb = uac_speaker_write,
        .input_cb = uac_microphone_read,
        .set_mute_cb = uac_set_mute,
        .set_volume_cb = uac_set_volume,
        .cb_ctx = &s_audio,
    };
    esp_err_t usb = uac_device_init(&usb_config);
    if (usb != ESP_OK) {
        ESP_LOGE(TAG, "FAILED at uac_device_init: %s (0x%x)", esp_err_to_name(usb), usb);
        abort();
    }
    ESP_LOGI(TAG, "ok: uac_device_init");
    ESP_LOGI(TAG, "USB audio ready: 16 kHz, stereo, signed 16-bit PCM");
    if (s_audio.led_strip) {
        led_strip_set_pixel(s_audio.led_strip, 0, 0, 0, 6);  /* blue = USB ready */
        led_strip_refresh(s_audio.led_strip);
    }
}
