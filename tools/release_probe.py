"""Disposable-data runtime measurements, also available in the portable EXE."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from neuropet.release_probe import main
if __name__ == "__main__":
    main()
