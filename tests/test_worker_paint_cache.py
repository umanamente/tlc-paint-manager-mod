"""The retired standalone paint disk cache cannot re-enter the runtime."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/"src/PaintManager/Scripts/paintmanager"

class PaintDiskCacheRetirementTests(unittest.TestCase):
    def test_disk_task_module_is_absent(self):
        self.assertFalse((SCRIPTS/"worker_paint_cache.lua").exists())

    def test_memory_controller_has_no_file_or_native_task_dependency(self):
        source=(SCRIPTS/"paint_cache.lua").read_text(encoding="utf-8")
        for forbidden in ("PaintManager.paint-cache.json", "paint_cache_read", "paint_cache_write",
                          'require("paintmanager.background")', "io.open", "os.rename", "os.remove"):
            self.assertNotIn(forbidden,source)

if __name__=="__main__":
    unittest.main()
