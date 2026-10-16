"""只加入程序运行库和公开文档的便携打包；不复制本机报告/截图/评测。"""
from pathlib import Path
from hashlib import sha256
from importlib import metadata
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from auto_answer import __version__


def main():
    distribution=ROOT/'dist-v1.1'/'AutoAnswer'
    executable=distribution/'AutoAnswer.exe'
    if not executable.is_file() or not (distribution/'_internal').is_dir():
        raise SystemExit('请先运行 scripts/build.py')
    release=ROOT/'release';release.mkdir(exist_ok=True)
    archive=release/f'AutoAnswer-{__version__}-win-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        bundle.write(executable,'AutoAnswer/AutoAnswer.exe')
        for file in sorted((distribution/'_internal').rglob('*')):
            if file.is_symlink():raise ValueError('运行库不能包含符号链接')
            if file.is_file():bundle.write(file,Path('AutoAnswer')/file.relative_to(distribution))
        for name in ['README.md','CHANGELOG.md','LICENSE','ACKNOWLEDGMENTS.md','THIRD_PARTY_NOTICES.md']:
            bundle.write(ROOT/name,Path('AutoAnswer')/name)
        for name in ['BUILD.md','PRIVACY.md','TESTING.md']:
            bundle.write(ROOT/'docs'/name,Path('AutoAnswer/docs')/name)
        for package in metadata.distributions():
            name=package.metadata.get('Name','unknown').replace('/','_')
            raw=package.read_text('METADATA') or ''
            # 保留包元信息以便追溯第三方许可，不收录个人pip配置。
            bundle.writestr(f'AutoAnswer/THIRD_PARTY_LICENSES/{name}/PACKAGE-METADATA.txt',raw)
            for relative in package.files or []:
                if any(word in str(relative).casefold() for word in ['license','copying','notice','copyright']):
                    file=Path(package.locate_file(relative))
                    if file.is_file() and file.stat().st_size<6000000:
                        destination=str(relative).replace('..','parent')
                        bundle.write(file,f'AutoAnswer/THIRD_PARTY_LICENSES/{name}/{destination}')
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip():raise ValueError('ZIP校验失败')
    digest=sha256(archive.read_bytes()).hexdigest()
    (release/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
    print(archive)
    print('SHA256:',digest)


if __name__=='__main__':main()
