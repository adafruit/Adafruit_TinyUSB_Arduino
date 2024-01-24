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

#define FRAME_WIDTH   128
#define FRAME_HEIGHT  96
#define FRAME_RATE    10

uint8_t const desc_video[] = {
  TUD_VIDEO_CAPTURE_DESCRIPTOR_UNCOMPR_BULK(4, 0x80, FRAME_WIDTH, FRAME_HEIGHT, FRAME_RATE, 64)
};

Adafruit_USBD_Video usb_video(desc_video, sizeof(desc_video));

void setup() {
  Serial.begin(115200);
  usb_video.begin();
}

void loop() {

}
