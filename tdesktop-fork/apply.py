#!/usr/bin/env python3
"""Apply Hello Pix bubble-translation hooks to a cloned tdesktop tree.

Anchors target tdesktop 7.2.6 (pinned commit 80158983db) exact source shape
verified with cat -et. Fallback variants keep older layouts working where
feasible; every patch either lands on a verified anchor or fails loudly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Windows cmd 默认代码页(cp1252/cp437)打不出中文, 这里强制 UTF-8,
# 否则末尾中文 print 会抛 UnicodeEncodeError 把整个补丁流程带崩。
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise SystemExit(message)


def already(text: str, marker: str) -> bool:
    return marker in text


# --- CI 环境补齐 C++ ATL ------------------------------------------------------
# breakpad 的 common_windows_lib 要 <atlbase.h> / <atlcomcli.h>, 官方 Windows
# 编译文档要求 Visual Studio 勾 "C++ ATL for latest v143 build tools"。
# 开发机照 APPLY.txt 手动勾过就行; GitHub Actions 的 windows-2025 镜像里
# ATL 只在 per-toolset 目录下(且是别的工具集), breakpad 会 [28/34] C1083 挂掉。
# 所以只在 CI(GITHUB_ACTIONS=true)里补, 绝不动本机 VS 安装。
ATL_COMPONENTS = (
    'Microsoft.VisualStudio.Component.VC.ATL',
    'Microsoft.VisualStudio.Component.VC.ATLMFC',
)


# --- tdesktop 版本锚点 --------------------------------------------------------
# 之前 workflow 里写着 checkout 80158983dbdd338fe17fd4711afd02688c418fd47,
# 但那个 commit 在 telegramdesktop/tdesktop 里根本不存在(GitHub API 返回
# "No commit found for SHA"), 而 cmd 的退出码只取整段脚本最后一条命令, 所以
# `git checkout` 失败被 `git submodule update` 的成功退出码吞掉了 —— 实际一直
# 在编 dev 分支最新代码, 所谓"固定 commit"从来没生效过。
# 现在由 apply.py 自己把关: 版本不对就在 CI 里切回固定 tag, 否则直接报错退出。
PINNED_TAG = 'v7.2.8'
PINNED_VERSION = '7.2.8'


def run_git(tdesktop: Path, args: list[str]) -> None:
    if not try_git(tdesktop, args):
        fail(f'[pin] git {" ".join(args)} 失败')


def try_git(tdesktop: Path, args: list[str]) -> bool:
    shown = ' '.join(args)
    print(f'[pin] git {shown}')
    done = subprocess.run(
        ['git', *args],
        cwd=str(tdesktop),
        capture_output=True,
        text=True,
    )
    for line in (done.stdout or '').splitlines()[-5:]:
        print(f'[pin]   {line}')
    if done.returncode != 0:
        for line in (done.stderr or '').splitlines()[-10:]:
            print(f'[pin]   {line}')
        return False
    return True


def read_app_version(tdesktop: Path) -> str:
    version_file = tdesktop / 'Telegram' / 'SourceFiles' / 'core' / 'version.h'
    if not version_file.is_file():
        fail(f'[pin] 找不到 {version_file}')
    match = re.search(
        r'AppVersionStr\s*=\s*"([^"]+)"',
        version_file.read_text(encoding='utf-8'),
    )
    return match.group(1) if match else ''


def ensure_pinned_version(tdesktop: Path) -> None:
    version = read_app_version(tdesktop)
    if version == PINNED_VERSION:
        print(f'[pin] 版本已匹配: {PINNED_VERSION}')
        return

    if os.environ.get('GITHUB_ACTIONS') != 'true':
        fail(
            f'[pin] 当前 tdesktop 是 {version or "未知"}, 期望 {PINNED_VERSION} '
            f'({PINNED_TAG})。\n'
            f'      补丁锚点是针对该版本校准的, 请先: '
            f'git checkout {PINNED_TAG} && git submodule update --init --recursive'
        )

    print(f'[pin] 当前 {version or "未知"} != {PINNED_VERSION}, 切换到 {PINNED_TAG}')
    # CI 用的是完整 clone, tag 都在本地; 不带 --depth, 免得把仓库截成 shallow。
    if not try_git(tdesktop, ['checkout', '-f', f'refs/tags/{PINNED_TAG}']):
        run_git(
            tdesktop,
            ['fetch', 'origin', f'refs/tags/{PINNED_TAG}:refs/tags/{PINNED_TAG}'],
        )
        run_git(tdesktop, ['checkout', '-f', f'refs/tags/{PINNED_TAG}'])
    run_git(tdesktop, ['submodule', 'update', '--init', '--recursive'])

    version = read_app_version(tdesktop)
    if version != PINNED_VERSION:
        fail(f'[pin] 切换后版本仍是 {version or "未知"}, 期望 {PINNED_VERSION}')
    shown = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=str(tdesktop),
        capture_output=True,
        text=True,
    )
    print(f'[pin] 已固定在 {PINNED_TAG} @ {shown.stdout.strip()}')


def atl_headers_ok(atlmfc: Path) -> bool:
    include = atlmfc / 'include'
    return (
        (include / 'atlbase.h').is_file()
        and (include / 'atlcomcli.h').is_file()
    )


def find_atlmfc_dirs(vsdir: Path) -> list[Path]:
    """收集所有真正可用的 ATL 目录(含 include/atlbase.h 的那个 atlmfc)。"""
    found = []
    shared = vsdir / 'VC' / 'atlmfc'
    if atl_headers_ok(shared):
        found.append(shared)
    tools = vsdir / 'VC' / 'Tools' / 'MSVC'
    if tools.is_dir():
        for candidate in sorted(tools.glob('*/atlmfc')):
            if atl_headers_ok(candidate) and candidate not in found:
                found.append(candidate)
    return found


def make_junction(link: Path, target: Path) -> bool:
    """mklink /J 建目录联接 —— 不需要管理员权限, 不像符号链接。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(link), str(target)],
        capture_output=True,
        text=True,
    )
    print(f'[atl] mklink /J {link} -> {target} (退出码 {done.returncode})')
    for line in ((done.stdout or '') + (done.stderr or '')).splitlines()[-4:]:
        print(f'[atl]   {line}')
    return atl_headers_ok(link)


def ensure_cpp_atl() -> None:
    """保证 breakpad 真正要的那个 ATL 路径存在。

    踩过的坑: breakpad 的 gyp 把 ATL 头文件路径写死成
        $(VCToolsInstallDir)..\\..\\atlmfc\\include  ==  VC\\atlmfc\\include
    也就是 VS 根下的 *共享* 目录, 而且它编的是 v143(14.44) 工具集。
    运行器镜像里 ATL 只存在于 VC\\Tools\\MSVC\\14.51.xxx\\atlmfc\\include
    (per-toolset, 且是 v145 的), 共享目录根本不存在。
    第一版检查写成"任何工具集有 ATL 就算有", 于是误判成"已存在"直接跳过,
    breakpad 继续 C1083。所以这里必须校验编译器实际要的那个路径。
    """
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.name != 'nt':
        return

    installer = Path(
        os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    ) / 'Microsoft Visual Studio' / 'Installer'
    vswhere = installer / 'vswhere.exe'
    if not vswhere.is_file():
        fail(f'[atl] CI 环境找不到 vswhere.exe: {vswhere}')

    shown = subprocess.run(
        [str(vswhere), '-latest', '-property', 'installationPath'],
        capture_output=True,
        text=True,
    )
    vsdir = Path(shown.stdout.strip())
    if not vsdir.is_dir():
        fail(f'[atl] vswhere 没找到 Visual Studio 安装: {shown.stdout!r}')

    needed = vsdir / 'VC' / 'atlmfc'
    print(f'[atl] VS: {vsdir}')
    for candidate in sorted((vsdir / 'VC' / 'Tools' / 'MSVC').glob('*')):
        print(f'[atl]   工具集: {candidate.name}')

    if atl_headers_ok(needed):
        print(f'[atl] 共享 ATL 已就位: {needed / "include"}')
        return

    print(f'[atl] 缺少 breakpad 要的头文件: {needed / "include" / "atlbase.h"}')
    print('[atl] 先尝试补装 ATL 组件...')
    vs_installer = installer / 'vs_installer.exe'
    if vs_installer.is_file():
        command = [
            str(vs_installer), 'modify',
            '--installPath', str(vsdir),
            '--quiet', '--norestart', '--nocache',
        ]
        for component in ATL_COMPONENTS:
            command += ['--add', component]
        done = subprocess.run(command, capture_output=True, text=True)
        print(f'[atl] vs_installer 退出码 = {done.returncode}')
        for line in (done.stdout or '').splitlines()[-6:]:
            print(f'[atl]   {line}')
        deadline = time.monotonic() + 20 * 60
        while not atl_headers_ok(needed) and time.monotonic() < deadline:
            time.sleep(15)
    else:
        print(f'[atl] 没有 vs_installer.exe, 跳过组件补装')

    if atl_headers_ok(needed):
        print(f'[atl] 组件补装成功: {needed / "include"}')
        return

    # 组件补装没能生成共享目录 -> 直接把现成的 ATL 联接过去, 保证编译器找得到。
    candidates = find_atlmfc_dirs(vsdir)
    if not candidates:
        fail('[atl] 整个 VS 安装里都找不到 atlbase.h, breakpad 编不过去')
    source = candidates[0]
    print(f'[atl] 组件补装没生成共享路径, 改用目录联接兜底: {source}')
    if not make_junction(needed, source):
        fail(f'[atl] 目录联接失败, {needed / "include" / "atlbase.h"} 仍不存在')
    print(f'[atl] OK: {needed / "include"} -> {source}')



def replace_one_of(path: Path, variants: list[tuple[str, str]], marker: str) -> None:
    text = path.read_text(encoding='utf-8')
    if already(text, marker):
        print(f'skip already patched: {path}')
        return
    for old, new in variants:
        if old in text:
            path.write_text(text.replace(old, new, 1), encoding='utf-8')
            print(f'patched {path}')
            return
    preview = '\n'.join(old[:160] for old, _ in variants)
    fail(f'找不到补丁锚点: {path}\n{preview}')


def apply(tdesktop: Path) -> None:
    lang = tdesktop / 'Telegram' / 'SourceFiles' / 'lang'
    cmake = tdesktop / 'Telegram' / 'CMakeLists.txt'
    provider = lang / 'translate_provider.cpp'
    tracker = (
        tdesktop / 'Telegram' / 'SourceFiles' / 'history' / 'view'
        / 'history_view_translate_tracker.cpp'
    )
    if not provider.is_file() or not tracker.is_file() or not cmake.is_file():
        fail(f'这不是完整的 tdesktop 目录: {tdesktop}')

    shutil.copy2(ROOT / 'hello_pix_translate_provider.h', lang / 'hello_pix_translate_provider.h')
    shutil.copy2(ROOT / 'hello_pix_translate_provider.cpp', lang / 'hello_pix_translate_provider.cpp')
    print('copied hello_pix_translate_provider.*')

    # --- Telegram/CMakeLists.txt: register the two new source files -----------
    replace_one_of(cmake, [
        (
            '    lang/translate_url_provider.cpp\n    lang/translate_url_provider.h\n',
            '    lang/translate_url_provider.cpp\n'
            '    lang/translate_url_provider.h\n'
            '    lang/hello_pix_translate_provider.cpp\n'
            '    lang/hello_pix_translate_provider.h\n',
        ),
        (
            'lang/translate_url_provider.cpp\n lang/translate_url_provider.h\n',
            'lang/translate_url_provider.cpp\n'
            ' lang/translate_url_provider.h\n'
            ' lang/hello_pix_translate_provider.cpp\n'
            ' lang/hello_pix_translate_provider.h\n',
        ),
        (
            'lang/translate_url_provider.cpp\n\tlang/translate_url_provider.h\n',
            'lang/translate_url_provider.cpp\n'
            '\tlang/translate_url_provider.h\n'
            '\tlang/hello_pix_translate_provider.cpp\n'
            '\tlang/hello_pix_translate_provider.h\n',
        ),
    ], 'hello_pix_translate_provider.cpp')

    # --- translate_provider.cpp: include header --------------------------------
    replace_one_of(provider, [
        (
            '#include "lang/translate_url_provider.h"\n',
            '#include "lang/translate_url_provider.h"\n'
            '#include "lang/hello_pix_translate_provider.h"\n',
        ),
    ], 'hello_pix_translate_provider.h')

    # --- translate_provider.cpp: inject pix provider first in factory ----------
    # 7.2.6 real shape (cat -et):
    #   \t\tnot_null<Main::Session*> session) {\n
    #   \tconst auto urlTemplate = ...
    provider_factory_old = (
        '\t\tnot_null<Main::Session*> session) {\n'
        '\tconst auto urlTemplate'
    )
    provider_factory_new = (
        '\t\tnot_null<Main::Session*> session) {\n'
        '\tif (auto pix = CreateHelloPixTranslateProvider()) {\n'
        '\t\treturn pix;\n'
        '\t}\n'
        '\tconst auto urlTemplate'
    )
    # Fallbacks for older source layouts (single-space/single-tab prefixes).
    replace_one_of(provider, [
        (
            provider_factory_old,
            provider_factory_new,
        ),
        (
            ' not_null<Main::Session*> session) {\n const auto urlTemplate',
            ' not_null<Main::Session*> session) {\n'
            '\tif (auto pix = CreateHelloPixTranslateProvider()) {\n'
            '\t\treturn pix;\n'
            '\t}\n'
            ' const auto urlTemplate',
        ),
        (
            ' not_null<Main::Session*> session) {\n\tconst auto urlTemplate',
            ' not_null<Main::Session*> session) {\n'
            '\tif (auto pix = CreateHelloPixTranslateProvider()) {\n'
            '\t\treturn pix;\n'
            '\t}\n'
            '\tconst auto urlTemplate',
        ),
        # Defensive: same-line signature, e.g. "CreateTranslateProvider(\n\t\t..." already
        # covered above; keep a bare-session fallback as last resort.
        (
            'session) {\n\tconst auto urlTemplate',
            'session) {\n'
            '\tif (auto pix = CreateHelloPixTranslateProvider()) {\n'
            '\t\treturn pix;\n'
            '\t}\n'
            '\tconst auto urlTemplate',
        ),
    ], 'CreateHelloPixTranslateProvider')

    # --- history_view_translate_tracker.cpp: include header --------------------
    replace_one_of(tracker, [
        (
            '#include "lang/translate_provider.h"\n',
            '#include "lang/translate_provider.h"\n'
            '#include "lang/hello_pix_translate_provider.h"\n',
        ),
    ], 'hello_pix_translate_provider.h')

    # --- history_view_translate_tracker.cpp: force tracking for incoming -------
    # 7.2.6 real shape:
    #   \t\tstd::move(autoTranslationValue),\n
    #   \t\t_1 && (_2 || _3));\n
    #   \t_trackingLanguage.value() | rpl::on_next([=](bool tracking) {
    tracker_setup_old = (
        '\t\t_1 && (_2 || _3));\n'
        '\t_trackingLanguage.value()'
    )
    tracker_setup_new = (
        '\t\t_1 && (_2 || _3));\n'
        '\tif (Ui::HelloPixShouldTranslateIncoming()) {\n'
        '\t\t_trackingLanguage = rpl::single(true);\n'
        '\t\t_history->translateTo(Ui::HelloPixTargetLanguage());\n'
        '\t}\n'
        '\t_trackingLanguage.value()'
    )
    replace_one_of(tracker, [
        (
            tracker_setup_old,
            tracker_setup_new,
        ),
        (
            ' _1 && (_2 || _3));\n _trackingLanguage.value()',
            ' _1 && (_2 || _3));\n'
            '\tif (Ui::HelloPixShouldTranslateIncoming()) {\n'
            '\t\t_trackingLanguage = rpl::single(true);\n'
            '\t\t_history->translateTo(Ui::HelloPixTargetLanguage());\n'
            '\t}\n'
            ' _trackingLanguage.value()',
        ),
        (
            ' _1 && (_2 || _3));\n\t_trackingLanguage.value()',
            ' _1 && (_2 || _3));\n'
            '\tif (Ui::HelloPixShouldTranslateIncoming()) {\n'
            '\t\t_trackingLanguage = rpl::single(true);\n'
            '\t\t_history->translateTo(Ui::HelloPixTargetLanguage());\n'
            '\t}\n'
            '\t_trackingLanguage.value()',
        ),
        (
            '_1 && (_2 || _3));\n\t_trackingLanguage.value()',
            '_1 && (_2 || _3));\n'
            '\tif (Ui::HelloPixShouldTranslateIncoming()) {\n'
            '\t\t_trackingLanguage = rpl::single(true);\n'
            '\t\t_history->translateTo(Ui::HelloPixTargetLanguage());\n'
            '\t}\n'
            '\t_trackingLanguage.value()',
        ),
    ], 'HelloPixShouldTranslateIncoming')

    # --- history_view_translate_tracker.cpp: gate group/channel translation ----
    # 7.2.6 real shape:
    #   \t\t|| item->isOnlyEmojiAndSpaces()) {\n
    #   \t\treturn false;\n
    #   \t}\n
    #   \tif (item->translationShowRequiresCheck(_bunchTranslatedTo)) {
    tracker_add_old = (
        '\t\t|| item->isOnlyEmojiAndSpaces()) {\n'
        '\t\treturn false;\n'
        '\t}\n'
        '\tif (item->translationShowRequiresCheck(_bunchTranslatedTo)) {'
    )
    tracker_add_new = (
        '\t\t|| item->isOnlyEmojiAndSpaces()) {\n'
        '\t\treturn false;\n'
        '\t}\n'
        '\tconst auto peer = item->history()->peer;\n'
        '\tif ((peer->isChat() || peer->isMegagroup() || peer->isBroadcast())\n'
        '\t\t&& !Ui::HelloPixShouldTranslateGroup()) {\n'
        '\t\treturn false;\n'
        '\t}\n'
        '\tif (item->translationShowRequiresCheck(_bunchTranslatedTo)) {'
    )
    replace_one_of(tracker, [
        (
            tracker_add_old,
            tracker_add_new,
        ),
        (
            ' || item->isOnlyEmojiAndSpaces()) {\n return false;\n }',
            ' || item->isOnlyEmojiAndSpaces()) {\n'
            ' return false;\n'
            ' }\n'
            '\tconst auto peer = item->history()->peer;\n'
            '\tif ((peer->isChat() || peer->isMegagroup() || peer->isBroadcast())\n'
            '\t\t&& !Ui::HelloPixShouldTranslateGroup()) {\n'
            '\t\treturn false;\n'
            '\t}',
        ),
        (
            ' || item->isOnlyEmojiAndSpaces()) {\n\t\treturn false;\n\t}',
            ' || item->isOnlyEmojiAndSpaces()) {\n'
            '\t\treturn false;\n'
            '\t}\n'
            '\tconst auto peer = item->history()->peer;\n'
            '\tif ((peer->isChat() || peer->isMegagroup() || peer->isBroadcast())\n'
            '\t\t&& !Ui::HelloPixShouldTranslateGroup()) {\n'
            '\t\treturn false;\n'
            '\t}',
        ),
    ], 'HelloPixShouldTranslateGroup')


def main() -> None:
    if len(sys.argv) != 2:
        fail('用法: python3 apply.py /path/to/tdesktop')
    tdesktop = Path(sys.argv[1]).resolve()
    ensure_pinned_version(tdesktop)
    ensure_cpp_atl()
    apply(tdesktop)
    print('完成。接下来按官方文档在 Windows 上编译 Telegram.exe。')


if __name__ == '__main__':
    main()
