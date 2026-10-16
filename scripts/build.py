"""可复现的 Windows 便携发布构建，不读取用户设置。"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=54, fill="#0F766E")
    draw.line([(64, 132), (108, 174), (192, 84)], fill="#E6FFF4", width=18, joint="curve")
    image.save(assets / "app.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT / "dist-v1.1"), "--workpath", str(ROOT / "build-v1.1"), str(ROOT / "AutoAnswer.spec")]
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
