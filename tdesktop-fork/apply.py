#!/usr/bin/env python3
"""Apply Hello Pix bubble-translation hooks to a cloned tdesktop tree."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    raise SystemExit(message)


def already(text: str, marker: str) -> bool:
    return marker in text


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

    replace_one_of(provider, [
        (
            '#include "lang/translate_url_provider.h"\n',
            '#include "lang/translate_url_provider.h"\n'
            '#include "lang/hello_pix_translate_provider.h"\n',
        ),
    ], 'hello_pix_translate_provider.h')

    replace_one_of(provider, [
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
    ], 'CreateHelloPixTranslateProvider')

    replace_one_of(tracker, [
        (
            '#include "lang/translate_provider.h"\n',
            '#include "lang/translate_provider.h"\n'
            '#include "lang/hello_pix_translate_provider.h"\n',
        ),
    ], 'hello_pix_translate_provider.h')

    replace_one_of(tracker, [
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
    ], 'HelloPixShouldTranslateIncoming')

    replace_one_of(tracker, [
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
    apply(Path(sys.argv[1]).resolve())
    print('完成。接下来按官方文档在 Windows 上编译 Telegram.exe。')


if __name__ == '__main__':
    main()
