"""The guard reads current residency, not the all-time working-set peak."""
import ctypes
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def test_current_working_set_matches_windows_counter():
    from neuropet.release_probe import memory
    from neuropet.core.windowing import working_set_mb
    kernel = ctypes.windll.kernel32
    kernel.VirtualAlloc.restype = ctypes.c_void_p
    kernel.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.c_ulong]
    kernel.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong]
    pointer = kernel.VirtualAlloc(None, 32*1024*1024, 0x3000, 4)
    assert pointer
    try:
        ctypes.memset(pointer, 1, 32*1024*1024)
        assert memory()["working_set_mib"] > 32
    finally:
        assert kernel.VirtualFree(pointer, 0, 0x8000)
    reference = memory()
    assert reference["peak_working_set_mib"] - reference["working_set_mib"] > 20
    assert abs(working_set_mb()-reference["working_set_mib"]) < 1, reference

if __name__ == "__main__":
    test_current_working_set_matches_windows_counter()
    print("PASS: current residency and peak counter are distinct")
