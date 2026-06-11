from pathlib import Path
import shutil

src = Path(r"C:\Users\abhishek\Downloads\autoagentpsy.abhisekh.png")
dst = Path(__file__).resolve().parent / "assets" / "logo.png"
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(src, dst)
src.unlink(missing_ok=True)
dst.write_bytes(dst.read_bytes())  # touch
print(f"OK {dst} {dst.stat().st_size}")
