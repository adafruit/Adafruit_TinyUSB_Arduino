import os
import shutil
import urllib.request
import zipfile

TINYUSB_URL = 'https://github.com/hathach/tinyusb/archive/refs/heads/master.zip'
name = 'tinyusb-master.zip'

print("Downloading ", TINYUSB_URL)
urllib.request.urlretrieve(TINYUSB_URL, name)

# unzip
with zipfile.ZipFile(name, "r") as zip_ref:
    zip_ref.extractall()

