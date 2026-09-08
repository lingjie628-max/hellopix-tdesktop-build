#!/usr/bin/env python3
"""Apply Hello Pix bubble-translation hooks to a cloned tdesktop tree.

Anchors target tdesktop 7.2.6 (pinned commit 80158983db) exact source shape
verified with cat -et. Fallback variants keep older layouts working where
feasible; every patch either lands on a verified anchor or fails loudly.
"""

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
    apply(Path(sys.argv[1]).resolve())
    print('完成。接下来按官方文档在 Windows 上编译 Telegram.exe。')


if __name__ == '__main__':
    main()
