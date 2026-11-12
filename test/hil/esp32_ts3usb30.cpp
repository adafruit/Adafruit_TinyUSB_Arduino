// HIL farm fixture, compiled into every sketch the build contract builds for
// the ESP32-S3/P4 rig boards (check_build.py BOARDS `fixture`): their OTG port
// reaches ci.lan through a TS3USB30 whose select is GPIO47. S high routes
// D = D2 (TS3USB30 datasheet SCDS237F, Table 1), the device side here, as
// tinyusb's hw/bsp/espressif/boards/family.c drives it for device builds.
#include <Arduino.h>

// the core's weak hook, run from initArduino() before setup()
void initVariant(void) {
  pinMode(47, OUTPUT);
  digitalWrite(47, HIGH);
}
