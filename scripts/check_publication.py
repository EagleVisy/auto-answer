"""审查Git暂存或已提交的公开文件，输出位置/类别而不打印疑似秘密。"""
from pathlib import Path
import argparse
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
TOP={'.gitignore','.gitattributes','README.md','ACKNOWLEDGMENTS.md','CHANGELOG.md','LICENSE','THIRD_PARTY_NOTICES.md','requirements.txt','requirements-dev.txt','requirements-lock.txt','run.py','AutoAnswer.spec'}
DOCS={'docs/BUILD.md','docs/PRIVACY.md','docs/TESTING.md'}
ALLOWED_EXT={'.py','.md','.txt','.spec'}
PATTERNS={
    '个人Windows用户路径':r'(?i)[a-z]:[\\/]+Users[\\/]+[^\\/\s]+',
    '本机附件目录':r'(?i)\.dsh[\\/]+attachments[\\/]',
    'GitHub凭据模式':r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b',
    'API凭据模式':r'\bsk-[A-Za-z0-9_-]{24,}\b',
    '私钥内容':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
}

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--head',action='store_true',help='检查HEAD而非暂存区')
    options=parser.parse_args()
    files=(git('ls-tree','-r','--name-only','-z','HEAD') if options.head else git('diff','--cached','--name-only','--diff-filter=ACMR','-z')).decode('utf-8').split('\0')
    files=[file for file in files if file]
    if not files:raise SystemExit('没有可检查的文件')
    errors=[]
    for name in files:
        path=Path(name);allowed=name in TOP or name in DOCS or name=='assets/app.ico' or (len(path.parts)==2 and path.parts[0] in ('auto_answer','scripts','tests') and path.suffix=='.py')
        if not allowed:errors.append((name,'不在公开白名单'));continue
        if name=='assets/app.ico':continue  # 程序生成的品牌图标，不是用户截图。
        blob=git('show',('HEAD:' if options.head else ':')+name)
        if len(blob)>1000000:errors.append((name,'异常大的文本文件'));continue
        try:text=blob.decode('utf-8')
        except UnicodeDecodeError:errors.append((name,'非UTF-8文本'));continue
        for label,pattern in PATTERNS.items():
            if re.search(pattern,text):errors.append((name,label))
        private_paths=['fixtures'+'/locator','attachments'+'/v1/objects']
        if any(marker in text for marker in private_paths):errors.append((name,'私人截图或OCR依赖'))
    if errors:
        for name,label in errors:print(f'REJECT {name}: {label}')
        raise SystemExit(1)
    print(f'PASS: {len(files)}个Git文件通过白名单和敏感模式检查。')
    print('无私人截图/OCR/环境文件/数据库/用户路径/已知凭据模式；仍需人工代码审核。')

if __name__=='__main__':main()
