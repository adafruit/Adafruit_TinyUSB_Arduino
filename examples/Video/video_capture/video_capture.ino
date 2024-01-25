/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

#include "Adafruit_TinyUSB.h"
#include "usb_descriptors.h"

//--------------------------------------------------------------------+
//
//--------------------------------------------------------------------+
#define FRAME_WIDTH   128
#define FRAME_HEIGHT  96
#define FRAME_RATE    30

uint8_t const desc_video[] = {
  TUD_VIDEO_CAPTURE_DESCRIPTOR_UNCOMPR_BULK(0, 0x80, FRAME_WIDTH, FRAME_HEIGHT, FRAME_RATE, 64)
};

Adafruit_USBD_Video usb_video(desc_video, sizeof(desc_video));

// YUY2 frame buffer
static uint8_t frame_buffer[FRAME_WIDTH * FRAME_HEIGHT * 16 / 8];

static unsigned frame_num = 0;
static unsigned tx_busy = 0;
static unsigned interval_ms = 1000 / FRAME_RATE;
static unsigned start_ms = 0;
static unsigned already_sent = 0;

//--------------------------------------------------------------------+
//
//--------------------------------------------------------------------+
static void fill_color_bar(uint8_t* buffer, unsigned start_position);

void setup() {
  Serial.begin(115200);
  usb_video.begin();
}

void loop() {
  if (!tud_video_n_streaming(0, 0)) {
    already_sent = 0;
    frame_num = 0;
    return;
  }

  if (!already_sent) {
    already_sent = 1;
    start_ms = millis();
    fill_color_bar(frame_buffer, frame_num);
    tud_video_n_frame_xfer(0, 0, (void*) frame_buffer, FRAME_WIDTH * FRAME_HEIGHT * 16 / 8);
  }

  unsigned cur = millis();
  if (cur - start_ms < interval_ms) return; // not enough time
  if (tx_busy) return;
  start_ms += interval_ms;

  fill_color_bar(frame_buffer, frame_num);
  tud_video_n_frame_xfer(0, 0, (void*) frame_buffer, FRAME_WIDTH * FRAME_HEIGHT * 16 / 8);
}

void tud_video_frame_xfer_complete_cb(uint_fast8_t ctl_idx, uint_fast8_t stm_idx) {
  (void) ctl_idx;
  (void) stm_idx;
  tx_busy = 0;
  /* flip buffer */
  ++frame_num;
}

int tud_video_commit_cb(uint_fast8_t ctl_idx, uint_fast8_t stm_idx,
                        video_probe_and_commit_control_t const* parameters) {
  (void) ctl_idx;
  (void) stm_idx;
  /* convert unit to ms from 100 ns */
  interval_ms = parameters->dwFrameInterval / 10000;
  return VIDEO_ERROR_NONE;
}

//------------- Helper -------------//
static void fill_color_bar(uint8_t* buffer, unsigned start_position) {
  /* EBU color bars
   * See also https://stackoverflow.com/questions/6939422 */
  static uint8_t const bar_color[8][4] = {
      /*  Y,   U,   Y,   V */
      { 235, 128, 235, 128}, /* 100% White */
      { 219,  16, 219, 138}, /* Yellow */
      { 188, 154, 188,  16}, /* Cyan */
      { 173,  42, 173,  26}, /* Green */
      {  78, 214,  78, 230}, /* Magenta */
      {  63, 102,  63, 240}, /* Red */
      {  32, 240,  32, 118}, /* Blue */
      {  16, 128,  16, 128}, /* Black */
  };
  uint8_t* p;

  /* Generate the 1st line */
  uint8_t* end = &buffer[FRAME_WIDTH * 2];
  unsigned idx = (FRAME_WIDTH / 2 - 1) - (start_position % (FRAME_WIDTH / 2));
  p = &buffer[idx * 4];
  for (unsigned i = 0; i < 8; ++i) {
    for (int j = 0; j < FRAME_WIDTH / (2 * 8); ++j) {
      memcpy(p, &bar_color[i], 4);
      p += 4;
      if (end <= p) {
        p = buffer;
      }
    }
  }

  /* Duplicate the 1st line to the others */
  p = &buffer[FRAME_WIDTH * 2];
  for (unsigned i = 1; i < FRAME_HEIGHT; ++i) {
    memcpy(p, buffer, FRAME_WIDTH * 2);
    p += FRAME_WIDTH * 2;
  }
}
