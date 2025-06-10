from pathlib import Path
import argparse
import sys
import shutil

def main():
    """
    This script takes a mandatory 'dir' argument, which is a path to pivot example to update for all DualRole's examples
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('dpath', help='path to folder containing usbh_helper.h to copy from')
    args = parser.parse_args()

    dpath = args.dpath

    sample_dir = Path(dpath)
    if not sample_dir.is_dir():
        print(f"The specified dir '{dir}' does not exist or is not a valid dir.")
        sys.exit(1)

    sample_file = sample_dir / 'usbh_helper.h'
    f_list = sorted(Path('examples/DualRole').glob('**/usbh_helper.h'))
    for f in f_list:
        if f != sample_file:
            print(f"Updating {f}")
            shutil.copy(sample_file, f)


if __name__ == '__main__':
    main()