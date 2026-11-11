import argparse
from pathlib import Path
from rtspdesk.gui import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RTSP Desk")
    parser.add_argument("--data-dir", type=Path, help="Override the settings, logs and playlist-cache folder")
    main(parser.parse_args().data_dir)
