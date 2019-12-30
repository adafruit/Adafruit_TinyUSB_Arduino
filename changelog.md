# Adafruit TinyUSB Arduino Library Changelog

## 0.8.0 - 2019.12.30

- Correct USB BCD version to 2.1 for webUSB
- Migrate CI from travis to github actions

## 0.7.1 - 2019.10.12

- Fixed MIDI build failed since it is under development

## 0.7.0 - 2019.10.09

- Added MIDI virtual wires/plugs

## 0.6.0 - 2019.08.05

- Added webUSB support with 2 example: webusb-serial, webusb-rgb
- Alligned mouse examples, added newer hid in/out example from main repo, added new composite example for ramdisk and hid in/out. PR #19 thanks to @PTS93

## 0.5.0 - 2019.07.17

- Added travis build
- Fixed msc setID
- Added itfnum to internal API getDescriptor() 
- Added MIDI support
  - Added midi_test example
  - Added pizza box dj example for cplayground express
- Mass Storage
  - Added msc_sdfat, msc dual lun (external flash + sd card)  example  
  - Updated msc example to use new SPIFlash 3.x API
  - Update msc example to print root contents
- HID
  - Added hid_mouse, hid_keyboard
  - Added hid_composite_joy_featherwing
- Added Composite: mouse_ramdisk, mouse_external_flash example

## 0.0.1 Initial Release

