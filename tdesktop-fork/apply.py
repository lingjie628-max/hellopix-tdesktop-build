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


def atl_include_ok(atlmfc: Path) -> bool:
    include = atlmfc / 'include'
    return all((include / n).is_file() for n in ('atlbase.h', 'atlcomcli.h'))


def atl_lib_ok(atlmfc: Path) -> bool:
    # 只要 Release 的 atls.lib: breakpad 只有 dump_syms 走 msbuild Release 要链接它,
    # Debug 那几个 ninja 目标(common/crash_generation_client/exception_handler)
    # 根本不编 ATL 源码, 所以 atlsd.lib 不是必需品 —— 早先把它列成必需,
    # 结果把镜像上唯一可用的那份 ATL 误判成"不完整"。
    return (atlmfc / 'lib' / 'x64' / 'atls.lib').is_file()


def atl_missing(atlmfc: Path) -> list[str]:
    """列出必需项里缺什么(头文件供编译, atls.lib 供链接)。"""
    missing = []
    include = atlmfc / 'include'
    for name in ('atlbase.h', 'atlcomcli.h'):
        if not (include / name).is_file():
            missing.append(f'include/{name}')
    if not atl_lib_ok(atlmfc):
        missing.append('lib/x64/atls.lib')
    return missing


def atl_missing_optional(atlmfc: Path) -> list[str]:
    return [
        f'lib/x64/{name}'
        for name in ('atlsd.lib',)
        if not (atlmfc / 'lib' / 'x64' / name).is_file()
    ]




def atl_complete(atlmfc: Path) -> bool:
    return not atl_missing(atlmfc)


def visual_studio_installs() -> list[Path]:
    """镜像上可能装了多套 VS(2022 / 18 等), 全部找出。"""
    found = []
    for key, fallback in (
        ('ProgramFiles', r'C:\Program Files'),
        ('ProgramFiles(x86)', r'C:\Program Files (x86)'),
    ):
        root = Path(os.environ.get(key, fallback)) / 'Microsoft Visual Studio'
        if not root.is_dir():
            continue
        for version in sorted(root.iterdir()):
            if not version.is_dir():
                continue
            for edition in sorted(version.iterdir()):
                if (edition / 'VC').is_dir():
                    found.append(edition)
    return found


def atlmfc_candidates(vsdir: Path) -> list[Path]:
    """一个 VS 安装里所有可能放 ATL 的目录。"""
    result = [vsdir / 'VC' / 'atlmfc']
    tools = vsdir / 'VC' / 'Tools' / 'MSVC'
    if tools.is_dir():
        result.extend(sorted(tools.glob('*/atlmfc')))
    return result


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
    return done.returncode == 0


def ci_windows() -> bool:
    return os.environ.get('GITHUB_ACTIONS') == 'true' and os.name == 'nt'


def ensure_cpp_atl() -> None:
    """保证 breakpad 真正要的那份 ATL 又全又在对的位置。

    踩过的坑(按时间顺序):
    1) breakpad 的 gyp 把路径写死成 VC\\atlmfc\\include (VS 根下的共享目录),
       而第一版检查是"任意工具集有 ATL 就算有", 命中别的工具集就误判跳过安装,
       结果 prepare 在 [28/34] 报 C1083 找不到 atlbase.h。
    2) 只查头文件不够: 链接 dump_syms 还要 atls.lib。镜像是 VS2026 单装,
       共享 VC\\atlmfc 根本不存在, 只有 VC\\Tools\\MSVC\\14.51.36231\\atlmfc,
       于是 C1083 变成 LNK1104: cannot open file 'atls.lib'。
    3) 把 atlsd.lib 也列成必需 —— 这是我自己加的过度约束。镜像上那份 ATL
       头文件 + atls.lib 都齐, 只差 Debug 版 atlsd.lib, 而 breakpad 只在
       Release 的 dump_syms 里链接 ATL, Debug 的 ninja 目标不碰 ATL。
       所以又一次误判成"没有可用 ATL"。现在 atlsd.lib 降级为可选。
    结论: 校验 头文件 + atls.lib; 缺就从镜像上任意一套 VS 找可用的那份联接过来。
    """
    if not ci_windows():
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
    print(f'[atl] 编译用的 VS(vswhere -latest): {vsdir}')


    # 先把镜像上所有 ATL 摆出来, 万一再出问题日志里能直接看出来。
    sources = []
    for vs in visual_studio_installs():
        for candidate in atlmfc_candidates(vs):
            missing = atl_missing(candidate)
            if not missing:
                sources.append(candidate)
                optional = atl_missing_optional(candidate)
                note = f' (可选件缺 {optional}, 不影响)' if optional else ''
                print(f'[atl]   可用 {candidate}{note}')
            else:
                print(f'[atl]   不完整 {candidate} 缺 {missing}')
    print(f'[atl] 镜像上可用的 ATL: {[str(p) for p in sources] or "无"}')

    missing = atl_missing(needed)
    if not missing:
        print(f'[atl] 目标 ATL 已完整: {needed}')
        return
    print(f'[atl] 目标 ATL 不完整 {needed} 缺 {missing}')

    # 镜像上已经有完整的一份(VS2022 的 v143 ATL) -> 直接联接, 不去等安装器。
    # 联接是确定性的、几秒完成; vs_installer 可能磨 20 分钟还什么都不装。
    if sources:
        source = sources[0]
        print(f'[atl] 联接镜像上完整的那份: {source}')
        _link_atl(needed, source)
        missing = atl_missing(needed)
        if not missing:
            print(f'[atl] OK: {needed} 已完整(include + lib/x64)')
            return
        print(f'[atl] 联接后仍缺 {missing}, 继续尝试组件补装')

    # 镜像上没有现成的 -> 只能指望 vs_installer。
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
        deadline = time.monotonic() + 15 * 60
        while atl_missing(needed) and time.monotonic() < deadline:
            time.sleep(15)
        if not atl_missing(needed):
            print(f'[atl] 组件补装成功: {needed}')
            return
    else:
        print('[atl] 没有 vs_installer.exe, 跳过组件补装')

    fail(
        f'[atl] {needed} 仍缺 {atl_missing(needed)}, breakpad 编不过去。\n'
        f'      镜像上找到的完整 ATL: {[str(p) for p in sources] or "无"}'
    )


def _link_atl(needed: Path, source: Path) -> None:
    """把完整的那份 ATL 接到编译器/链接器实际查找的位置。"""
    if not needed.exists():
        if not make_junction(needed, source):
            fail(f'[atl] 联接 {needed} -> {source} 失败')
        return
    # 目标已存在(比如 VS18 自带的部分 ATL), 只补缺的那几块。
    # 注意 include 和 lib 要分别判断, 不能用 atl_missing 看整体。
    if not atl_include_ok(needed):
        _replace_with_junction(needed / 'include', source / 'include')
    if not atl_lib_ok(needed):
        _replace_with_junction(needed / 'lib', source / 'lib')


def _replace_with_junction(part: Path, target: Path) -> None:
    """part 位置有残缺内容时先清掉再联接; 清掉目录联接不会动到目标本身。"""
    if part.exists():
        print(f'[atl] 先移除不完整的 {part}')
        subprocess.run(['cmd', '/c', 'rmdir', '/S', '/Q', str(part)])
    if not make_junction(part, target):
        fail(f'[atl] 联接 {part} -> {target} 失败')






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
